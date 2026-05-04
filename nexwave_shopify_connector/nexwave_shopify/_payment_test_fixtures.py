# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Idempotent fixture factories for payment entry tests.

These helpers create (or update) the minimum set of records needed to exercise
the multi-gateway Payment Entry creation path without touching a real Shopify
store. They are safe to call multiple times.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import frappe
from frappe.utils import flt
from frappe.utils.password import set_encrypted_password

if TYPE_CHECKING:
	from frappe.model.document import Document

TEST_COMPANY = "_Test Company"
TEST_STORE_DOMAIN = "_test-payment-entry.myshopify.com"
TEST_ITEM_GROUP = "All Item Groups"

TEST_MODE_OF_PAYMENT_CARD = "_Test Shopify Card"
TEST_MODE_OF_PAYMENT_STORE_CREDIT = "_Test Shopify Store Credit"
TEST_MODE_OF_PAYMENT_GIFT_CARD = "_Test Shopify Gift Card"

TEST_GATEWAY_CARD = "shopify_payments"
TEST_GATEWAY_STORE_CREDIT = "shopify_store_credit"
TEST_GATEWAY_GIFT_CARD = "gift_card"


def _get_default_cash_account() -> str:
	"""Find a usable Cash/Bank account for `_Test Company`.

	Prefers an account named `Cash - _TC`; falls back to any non-group account
	with account_type Cash or Bank linked to the test company.
	"""
	candidate = frappe.db.get_value("Account", "Cash - _TC", "name")
	if candidate:
		return str(candidate)
	acct = frappe.db.get_value(
		"Account",
		{
			"company": TEST_COMPANY,
			"account_type": ["in", ["Cash", "Bank"]],
			"is_group": 0,
		},
		"name",
	)
	if acct:
		return str(acct)
	acct = frappe.db.get_value(
		"Account",
		{"company": TEST_COMPANY, "is_group": 0},
		"name",
	)
	if not acct:
		raise RuntimeError("No usable account available for payment entry tests")
	return str(acct)


def ensure_test_payment_methods() -> None:
	"""Create test Modes of Payment with company-level account setup.

	Creates two Modes of Payment specifically for these tests so the test
	suite does not depend on (or modify) ERPNext seed Modes of Payment that
	may already be wired into other tests.
	"""
	account = _get_default_cash_account()

	for mop_name in (
		TEST_MODE_OF_PAYMENT_CARD,
		TEST_MODE_OF_PAYMENT_STORE_CREDIT,
		TEST_MODE_OF_PAYMENT_GIFT_CARD,
	):
		if not frappe.db.exists("Mode of Payment", mop_name):
			doc = frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": mop_name,
					"enabled": 1,
					"type": "Cash",
					"accounts": [
						{
							"company": TEST_COMPANY,
							"default_account": account,
						}
					],
				}
			)
			doc.flags.ignore_validate = True
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
			continue

		# Ensure the company-level account row exists (idempotent update)
		mop = frappe.get_doc("Mode of Payment", mop_name)
		row_exists = any(r.company == TEST_COMPANY for r in mop.accounts or [])
		if not row_exists:
			mop.append("accounts", {"company": TEST_COMPANY, "default_account": account})
			mop.flags.ignore_validate = True
			mop.flags.ignore_mandatory = True
			mop.save(ignore_permissions=True)


def ensure_test_shopify_store_with_payment_mapping(**overrides) -> "Document":
	"""Create (or update) an idempotent Shopify Store with payment method mappings.

	The store is `enabled=1`, has `auto_create_payment_entry=1`, and has
	payment_method_mapping rows for both `shopify_payments` and
	`shopify_store_credit`. A dummy access token is set so .get_password
	returns a value.

	Args:
		overrides: Any fields to override on the store doc.

	Returns:
		The Shopify Store document.
	"""
	ensure_test_payment_methods()
	account = _get_default_cash_account()

	domain = overrides.get("shop_domain") or TEST_STORE_DOMAIN

	if frappe.db.exists("Shopify Store", domain):
		store = frappe.get_doc("Shopify Store", domain)
	else:
		store = frappe.get_doc(
			{
				"doctype": "Shopify Store",
				"shop_domain": domain,
				"auth_method": "Legacy (Access Token)",
				"company": TEST_COMPANY,
				"enabled": 1,
				"api_version": "2024-01",
				"auto_create_invoice": 1,
				"auto_create_payment_entry": 1,
			}
		)
		store.insert(ignore_permissions=True)

	store.enabled = 1
	store.company = TEST_COMPANY
	if not store.api_version:
		store.api_version = "2024-01"
	store.auth_method = "Legacy (Access Token)"
	store.auto_create_invoice = 1
	store.auto_create_payment_entry = 1

	# Apply caller overrides (after the defaults so callers can disable flags)
	for key, value in (overrides or {}).items():
		if key == "shop_domain":
			continue
		setattr(store, key, value)

	# Sync payment method mappings (idempotent)
	desired = {
		TEST_GATEWAY_CARD: {
			"shopify_gateway": TEST_GATEWAY_CARD,
			"mode_of_payment": TEST_MODE_OF_PAYMENT_CARD,
			"account": account,
		},
		TEST_GATEWAY_STORE_CREDIT: {
			"shopify_gateway": TEST_GATEWAY_STORE_CREDIT,
			"mode_of_payment": TEST_MODE_OF_PAYMENT_STORE_CREDIT,
			"account": account,
		},
		TEST_GATEWAY_GIFT_CARD: {
			"shopify_gateway": TEST_GATEWAY_GIFT_CARD,
			"mode_of_payment": TEST_MODE_OF_PAYMENT_GIFT_CARD,
			"account": account,
		},
	}
	existing_gateways = {row.shopify_gateway for row in store.payment_method_mapping or []}
	for gw, row_data in desired.items():
		if gw in existing_gateways:
			continue
		store.append("payment_method_mapping", row_data)

	store.flags.ignore_validate = True
	store.flags.ignore_mandatory = True
	store.save(ignore_permissions=True)

	# Set a dummy access token via the Password field so .get_password works.
	set_encrypted_password("Shopify Store", store.name, "test-token", fieldname="access_token")

	return store


def _ensure_leaf_customer_group() -> str:
	leaf_group = "_Test Customer Group"
	if not frappe.db.exists("Customer Group", leaf_group):
		frappe.get_doc(
			{
				"doctype": "Customer Group",
				"customer_group_name": leaf_group,
				"parent_customer_group": "All Customer Groups",
				"is_group": 0,
			}
		).insert(ignore_if_duplicate=True)
	return leaf_group


def _ensure_test_customer(customer_name: str) -> str:
	if frappe.db.exists("Customer", customer_name):
		return customer_name
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_group": _ensure_leaf_customer_group(),
			"territory": frappe.db.get_single_value("Selling Settings", "territory") or "All Territories",
		}
	)
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
	return str(doc.name)


def _ensure_test_item(item_code: str = "_Test Shopify Payment Item") -> str:
	if frappe.db.exists("Item", item_code):
		return item_code
	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": TEST_ITEM_GROUP,
			"stock_uom": "Nos",
			"is_stock_item": 0,
		}
	)
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
	return str(doc.name)


def create_test_sales_invoice_for_payment(
	store,
	grand_total: float,
	customer: str = "_Test Customer",
	shopify_order_id: str | None = None,
):
	"""Create a submitted Sales Invoice tied to a Shopify order.

	Builds a minimal SI with a single non-stock line so we don't need to
	manage stock balances. Sets `shopify_order_id`, `shopify_store`, and
	`shopify_order_number` so the connector code can find/track it.

	Args:
		store: Shopify Store document.
		grand_total: Target grand total (rate is set to this value, qty=1).
		customer: Customer name (must exist or be a default test customer).
		shopify_order_id: Shopify order id to set on the SI.

	Returns:
		The submitted Sales Invoice document.
	"""
	customer = _ensure_test_customer(customer)
	item_code = _ensure_test_item()
	company = store.company or TEST_COMPANY

	income_account = frappe.db.get_value("Company", company, "default_income_account") or frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Income Account", "is_group": 0},
		"name",
	)
	company_currency = frappe.db.get_value("Company", company, "default_currency") or "INR"

	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": customer,
			"company": company,
			"currency": company_currency,
			"shopify_store": store.name,
			"shopify_order_id": shopify_order_id,
			"shopify_order_number": "#TEST",
			"items": [
				{
					"item_code": item_code,
					"item_name": item_code,
					"description": item_code,
					"qty": 1,
					"rate": flt(grand_total),
					"income_account": income_account,
				}
			],
		}
	)
	si.flags.ignore_mandatory = True
	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)
	si.submit()
	return si


def load_shopify_order(filename: str) -> dict:
	"""Load a Shopify order JSON fixture from `test_data/`.

	Args:
		filename: Filename relative to `nexwave_shopify/test_data/`.

	Returns:
		Parsed JSON dict.
	"""
	path = Path(__file__).parent / "test_data" / filename
	return json.loads(path.read_text())

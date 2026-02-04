# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
Test fixtures for tax module tests.

Provides helper functions to setup test data for tax-related tests.

Usage:
    from nexwave_shopify_connector.nexwave_shopify.tax.fixtures import (
        setup_tax_test_data,
        get_test_store,
        load_shopify_order,
        TEST_COMPANY,
    )
"""

import json
from pathlib import Path

import frappe

# Test constants
TEST_COMPANY = "_Test Company"
TEST_COST_CENTER = "Main - _TC"
TEST_WAREHOUSE = "Stores - _TC"
TEST_GST_ACCOUNT = "GST - _TC"
TEST_SHIPPING_ACCOUNT = "Shipping Charges - _TC"
TEST_WRITE_OFF_ACCOUNT = "Write Off - _TC"
TEST_STORE_DOMAIN = "_test-shopify-store.myshopify.com"

# Path to test data files
DATA_DIR = Path(__file__).parent.parent.parent.parent.parent.parent.parent / "data" / "shopify"


def setup_tax_test_data(commit: bool = False):
	"""
	Setup all test data required for tax module tests.

	Creates:
	- Test Shopify Store with tax account mappings
	- Test accounts if they don't exist

	Args:
	    commit: If True, commit changes to database
	"""
	_ensure_test_accounts()
	_ensure_write_off_account_on_company()
	_create_test_shopify_store()

	if commit:
		frappe.db.commit()


def _ensure_test_accounts():
	"""Ensure test accounts exist."""
	# GST Account
	if not frappe.db.exists("Account", TEST_GST_ACCOUNT):
		# Try to find an existing GST account to use as reference
		existing_gst = frappe.db.get_value(
			"Account",
			{"account_type": "Tax", "company": TEST_COMPANY, "is_group": 0},
			"name"
		)
		if existing_gst:
			# Use existing GST account
			frappe.flags.test_gst_account = existing_gst
		else:
			# Skip account creation - tests will need to handle missing accounts
			pass

	# Shipping Charges Account
	if not frappe.db.exists("Account", TEST_SHIPPING_ACCOUNT):
		existing_shipping = frappe.db.get_value(
			"Account",
			{"account_type": "Chargeable", "company": TEST_COMPANY, "is_group": 0},
			"name"
		)
		if existing_shipping:
			frappe.flags.test_shipping_account = existing_shipping


def _ensure_write_off_account_on_company():
	"""Ensure the test company has a write_off_account configured."""
	if frappe.db.exists("Company", TEST_COMPANY):
		write_off_account = frappe.db.get_value("Company", TEST_COMPANY, "write_off_account")
		if not write_off_account:
			# Find a suitable write-off account
			write_off = frappe.db.get_value(
				"Account",
				{"company": TEST_COMPANY, "account_type": "Expense Account", "is_group": 0},
				"name"
			)
			if write_off:
				frappe.db.set_value("Company", TEST_COMPANY, "write_off_account", write_off)


def _create_test_shopify_store():
	"""Create a test Shopify Store document."""
	if frappe.db.exists("Shopify Store", TEST_STORE_DOMAIN):
		return

	# Get actual account names (may differ from constants)
	gst_account = getattr(frappe.flags, "test_gst_account", None) or _find_tax_account()
	shipping_account = getattr(frappe.flags, "test_shipping_account", None) or _find_expense_account()

	store = frappe.get_doc({
		"doctype": "Shopify Store",
		"shop_domain": TEST_STORE_DOMAIN,
		"enabled": 0,  # Disabled so it doesn't try to sync
		"auth_method": "Legacy (Access Token)",
		"access_token": "test-token",
		"company": TEST_COMPANY,
		"cost_center": _find_cost_center(),
		"warehouse": _find_warehouse(),
		"default_sales_tax_account": gst_account,
		"default_shipping_charges_account": shipping_account,
		"add_shipping_as_item": 0,
	})

	# Add tax account mapping
	if gst_account:
		store.append("tax_accounts", {
			"shopify_tax": "GST",
			"tax_account": gst_account,
		})

	store.insert(ignore_permissions=True)


def _find_tax_account() -> str | None:
	"""Find a suitable tax account for testing."""
	return frappe.db.get_value(
		"Account",
		{"company": TEST_COMPANY, "account_type": "Tax", "is_group": 0},
		"name"
	)


def _find_expense_account() -> str | None:
	"""Find a suitable expense account for testing."""
	return frappe.db.get_value(
		"Account",
		{"company": TEST_COMPANY, "account_type": "Expense Account", "is_group": 0},
		"name"
	)


def _find_cost_center() -> str | None:
	"""Find a cost center for the test company."""
	return frappe.db.get_value(
		"Cost Center",
		{"company": TEST_COMPANY, "is_group": 0},
		"name"
	)


def _find_warehouse() -> str | None:
	"""Find a warehouse for the test company."""
	return frappe.db.get_value(
		"Warehouse",
		{"company": TEST_COMPANY, "is_group": 0},
		"name"
	)


def get_test_store():
	"""
	Get the test Shopify Store document.

	Returns:
	    Shopify Store document
	"""
	return frappe.get_doc("Shopify Store", TEST_STORE_DOMAIN)


def load_shopify_order(filename: str) -> dict:
	"""
	Load a Shopify order JSON file from the test data directory.

	Args:
	    filename: Name of the JSON file (e.g., "order1.json")

	Returns:
	    Parsed JSON as a dict

	Raises:
	    FileNotFoundError: If the file doesn't exist
	"""
	filepath = DATA_DIR / filename
	if not filepath.exists():
		raise FileNotFoundError(f"Test data file not found: {filepath}")

	with open(filepath) as f:
		return json.load(f)


def create_test_shopify_order(
	line_items: list[dict] | None = None,
	shipping_lines: list[dict] | None = None,
	taxes_included: bool = True,
	total_price: str = "100.00",
) -> dict:
	"""
	Create a minimal Shopify order dict for testing.

	Args:
	    line_items: List of line item dicts
	    shipping_lines: List of shipping line dicts
	    taxes_included: Whether prices include tax
	    total_price: Order total

	Returns:
	    Shopify order dict
	"""
	if line_items is None:
		line_items = [
			{
				"sku": "TEST-001",
				"taxable": True,
				"price": "10.00",
				"quantity": 1,
				"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
			}
		]

	if shipping_lines is None:
		shipping_lines = []

	return {
		"id": 12345,
		"line_items": line_items,
		"shipping_lines": shipping_lines,
		"taxes_included": taxes_included,
		"total_price": total_price,
		"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
	}


def cleanup_test_data():
	"""Clean up test data created during tests."""
	if frappe.db.exists("Shopify Store", TEST_STORE_DOMAIN):
		frappe.delete_doc("Shopify Store", TEST_STORE_DOMAIN, force=True)


def create_test_items(item_codes: list[str], commit: bool = False) -> dict[str, str]:
	"""
	Create test Items in the database for testing.

	This is necessary because TaxBuilder._build_sku_lookup() queries the database
	to map Shopify SKUs to ERPNext item_codes.

	Args:
	    item_codes: List of item codes/SKUs to create
	    commit: If True, commit changes to database

	Returns:
	    Dict mapping item_code to created Item name
	"""
	created_items = {}

	# Get default item group
	item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"

	for item_code in item_codes:
		if frappe.db.exists("Item", item_code):
			created_items[item_code] = item_code
			continue

		item = frappe.get_doc({
			"doctype": "Item",
			"item_code": item_code,
			"item_name": f"Test Item {item_code}",
			"item_group": item_group,
			"stock_uom": "Nos",
			"is_stock_item": 0,  # Non-stock item for simplicity
		})
		item.insert(ignore_permissions=True)
		created_items[item_code] = item.name

	if commit:
		frappe.db.commit()

	return created_items


def delete_test_items(item_codes: list[str], commit: bool = False):
	"""
	Delete test Items created for testing.

	Args:
	    item_codes: List of item codes to delete
	    commit: If True, commit changes to database
	"""
	for item_code in item_codes:
		if frappe.db.exists("Item", item_code):
			frappe.delete_doc("Item", item_code, force=True)

	if commit:
		frappe.db.commit()

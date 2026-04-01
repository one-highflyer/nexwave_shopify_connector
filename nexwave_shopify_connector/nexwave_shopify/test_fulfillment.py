# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
Unit tests for fulfillment deduplication logic.

Tests the two-mode approach:
1. Default (safe): Skip DN creation if any DN exists
2. Optional (auto_fulfill_remaining_qty=1): Create DN for remaining qty only
"""

import frappe
from frappe.tests import IntegrationTestCase

from nexwave_shopify_connector.nexwave_shopify.fulfillment import (
	_get_delivered_qty_map,
	_has_existing_delivery_notes,
	create_delivery_notes_from_fulfillments,
)


def get_default_company():
	"""Get the default company for tests."""
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		company = frappe.db.get_value("Company", {}, "name")
	return company


def get_default_warehouse():
	"""Get the default warehouse for tests."""
	warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
	if not warehouse:
		company = get_default_company()
		warehouse = frappe.db.get_value("Warehouse", {"is_group": 0, "company": company}, "name")
	if not warehouse:
		warehouse = frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
	return warehouse


def create_stock_entry(item_code, qty, warehouse=None):
	"""Create a stock entry to add stock for testing."""
	warehouse = warehouse or get_default_warehouse()
	company = get_default_company()

	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": company,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"t_warehouse": warehouse,
					"basic_rate": 100,
				}
			],
		}
	)
	se.insert()
	se.submit()
	return se


def create_test_sales_order(customer, item_code, qty=10, warehouse=None, shopify_order_id=None):
	"""Create and submit a test Sales Order."""
	warehouse = warehouse or get_default_warehouse()
	company = get_default_company()

	so = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer,
			"company": company,
			"delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"rate": 100,
					"warehouse": warehouse,
				}
			],
		}
	)
	if shopify_order_id:
		so.shopify_order_id = shopify_order_id
	so.insert()
	so.submit()
	return so


def create_test_delivery_note(so, qty, submit=True):
	"""Create a Delivery Note from Sales Order with specified qty."""
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = make_delivery_note(so.name)
	dn.items[0].qty = qty
	dn.insert()
	if submit:
		dn.submit()
	return dn


def get_or_create_test_shopify_store(auto_fulfill_remaining_qty=0):
	"""Get or create test Shopify Store with setting."""
	store_name = "_Test Shopify Store"
	if frappe.db.exists("Shopify Store", store_name):
		store = frappe.get_doc("Shopify Store", store_name)
		store.auto_fulfill_remaining_qty = auto_fulfill_remaining_qty
		store.save()
		return store

	# Get required fields for creating a new store
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		company = frappe.db.get_value("Company", {}, "name")

	warehouse = get_default_warehouse()

	store = frappe.get_doc(
		{
			"doctype": "Shopify Store",
			"shop_domain": "_test-store.myshopify.com",
			"enabled": 0,
			"auth_method": "Legacy (Access Token)",
			"company": company,
			"warehouse": warehouse,
			"auto_fulfill_remaining_qty": auto_fulfill_remaining_qty,
		}
	)
	store.insert(ignore_permissions=True)
	return store


def create_test_fulfillment_payload(shopify_order_id, items):
	"""Create a mock Shopify fulfillment webhook payload."""
	return {
		"id": shopify_order_id,
		"fulfillments": [
			{
				"id": f"fulfillment_{shopify_order_id}",
				"status": "success",
				"line_items": items,
			}
		],
	}


class TestFulfillmentDeduplication(IntegrationTestCase):
	"""Test fulfillment deduplication logic."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.customer = "_Test Customer"
		cls.item_code = "_Test Item"

	def tearDown(self):
		# Clean up created documents after each test
		frappe.db.rollback()

	# --- Helper function tests ---

	def test_has_existing_delivery_notes_returns_true_for_submitted_dn(self):
		"""_has_existing_delivery_notes returns True when submitted DN exists."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		so = create_test_sales_order(self.customer, self.item_code, qty=10)
		create_test_delivery_note(so, qty=3, submit=True)

		self.assertTrue(_has_existing_delivery_notes(so.name))

	def test_has_existing_delivery_notes_returns_true_for_draft_dn(self):
		"""_has_existing_delivery_notes returns True when draft DN exists."""
		so = create_test_sales_order(self.customer, self.item_code, qty=10)
		create_test_delivery_note(so, qty=3, submit=False)  # Draft - no stock needed

		self.assertTrue(_has_existing_delivery_notes(so.name))

	def test_has_existing_delivery_notes_returns_false_when_none(self):
		"""_has_existing_delivery_notes returns False when no DN exists."""
		so = create_test_sales_order(self.customer, self.item_code, qty=10)

		self.assertFalse(_has_existing_delivery_notes(so.name))

	def test_get_delivered_qty_map_aggregates_correctly(self):
		"""_get_delivered_qty_map returns correct aggregated quantities."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		so = create_test_sales_order(self.customer, self.item_code, qty=10)
		create_test_delivery_note(so, qty=3, submit=True)
		create_test_delivery_note(so, qty=2, submit=True)

		qty_map = _get_delivered_qty_map(so.name)
		so_detail = so.items[0].name

		self.assertEqual(qty_map.get(so_detail), 5)  # 3 + 2

	def test_get_delivered_qty_map_ignores_draft_dns(self):
		"""_get_delivered_qty_map only counts submitted DNs."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		so = create_test_sales_order(self.customer, self.item_code, qty=10)
		create_test_delivery_note(so, qty=3, submit=True)
		create_test_delivery_note(so, qty=2, submit=False)  # Draft - should be ignored

		qty_map = _get_delivered_qty_map(so.name)
		so_detail = so.items[0].name

		self.assertEqual(qty_map.get(so_detail), 3)  # Only submitted

	# --- Integration tests for create_delivery_notes_from_fulfillments ---

	def test_skips_dn_creation_when_setting_off_and_dn_exists(self):
		"""With setting OFF, skips DN creation if any DN exists."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		store = get_or_create_test_shopify_store(auto_fulfill_remaining_qty=0)
		so = create_test_sales_order(
			self.customer, self.item_code, qty=10, shopify_order_id="12345"
		)
		create_test_delivery_note(so, qty=3, submit=True)

		payload = create_test_fulfillment_payload(
			"12345", [{"sku": self.item_code, "quantity": 10}]
		)

		result = create_delivery_notes_from_fulfillments(payload, store)

		self.assertEqual(result["created"], [])
		self.assertGreater(result["skipped"], 0)
		self.assertIn("Existing Delivery Notes found", result["message"])

	def test_creates_remaining_qty_when_setting_on(self):
		"""With setting ON, creates DN for remaining qty only."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		store = get_or_create_test_shopify_store(auto_fulfill_remaining_qty=1)
		so = create_test_sales_order(
			self.customer, self.item_code, qty=10, shopify_order_id="12346"
		)
		create_test_delivery_note(so, qty=3, submit=True)

		payload = create_test_fulfillment_payload(
			"12346", [{"sku": self.item_code, "quantity": 10}]
		)

		result = create_delivery_notes_from_fulfillments(payload, store)

		self.assertEqual(len(result["created"]), 1)
		dn = frappe.get_doc("Delivery Note", result["created"][0])
		self.assertEqual(dn.items[0].qty, 7)  # 10 - 3 already delivered

	def test_creates_dn_normally_when_no_existing_dn(self):
		"""Creates DN normally when no existing DN (regardless of setting)."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		store = get_or_create_test_shopify_store(auto_fulfill_remaining_qty=0)
		so = create_test_sales_order(
			self.customer, self.item_code, qty=10, shopify_order_id="12347"
		)

		payload = create_test_fulfillment_payload(
			"12347", [{"sku": self.item_code, "quantity": 10}]
		)

		result = create_delivery_notes_from_fulfillments(payload, store)

		self.assertEqual(len(result["created"]), 1)
		dn = frappe.get_doc("Delivery Note", result["created"][0])
		self.assertEqual(dn.items[0].qty, 10)

	def test_draft_dn_blocks_creation_when_setting_off(self):
		"""Draft DN also blocks creation when setting is OFF."""
		store = get_or_create_test_shopify_store(auto_fulfill_remaining_qty=0)
		so = create_test_sales_order(
			self.customer, self.item_code, qty=10, shopify_order_id="12348"
		)
		create_test_delivery_note(so, qty=3, submit=False)  # Draft - no stock needed

		payload = create_test_fulfillment_payload(
			"12348", [{"sku": self.item_code, "quantity": 10}]
		)

		result = create_delivery_notes_from_fulfillments(payload, store)

		self.assertEqual(result["created"], [])
		self.assertGreater(result["skipped"], 0)

	def test_skips_fully_delivered_items(self):
		"""With setting ON, skips items that are fully delivered."""
		# Create stock first
		create_stock_entry(self.item_code, qty=20)

		store = get_or_create_test_shopify_store(auto_fulfill_remaining_qty=1)
		so = create_test_sales_order(
			self.customer, self.item_code, qty=10, shopify_order_id="12349"
		)
		create_test_delivery_note(so, qty=10, submit=True)  # Fully delivered

		payload = create_test_fulfillment_payload(
			"12349", [{"sku": self.item_code, "quantity": 10}]
		)

		result = create_delivery_notes_from_fulfillments(payload, store)

		# No DN created because all items fully delivered (or empty DN not created)
		# The result could be empty created list or failed count
		self.assertEqual(result["created"], [])

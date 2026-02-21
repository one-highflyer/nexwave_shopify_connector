# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
Frappe unit tests for the tax module.

These tests verify tax calculation logic for Shopify order imports,
including:
- Zero-rated item detection
- Shipping tax handling
- Tax row building (On Net Total)
- Rounding adjustments

Run tests with:
    bench --site <site> run-tests --app nexwave_shopify_connector --module nexwave_shopify_connector.nexwave_shopify.tax.test_tax

Or run specific test class:
    bench --site <site> run-tests --app nexwave_shopify_connector --module nexwave_shopify_connector.nexwave_shopify.tax.test_tax --test TestTaxDetector
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from nexwave_shopify_connector.nexwave_shopify.tax.fixtures import (
	create_test_items,
	create_test_shopify_order,
	delete_test_items,
	get_test_store,
	load_shopify_order,
	setup_tax_test_data,
)


class TestTaxDetector(FrappeTestCase):
	"""Tests for TaxDetector class - zero-rated item detection."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data(commit=True)

	def test_all_items_taxable(self):
		"""Test detection when all items are taxable."""
		from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector

		order = load_shopify_order("order1.json")
		store = get_test_store()

		detector = TaxDetector(order, store)

		# All items in order1.json are taxable (taxable: true, has tax_lines)
		self.assertEqual(len(detector.get_zero_rated_skus()), 0)

	def test_zero_rated_by_taxable_false(self):
		"""Test detection of zero-rated item via taxable=false."""
		from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "TAXABLE-001",
					"taxable": True,
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				},
				{
					"sku": "ZERO-RATED-001",
					"taxable": False,
					"tax_lines": [],
				},
			]
		)
		store = get_test_store()

		detector = TaxDetector(order, store)

		zero_rated = detector.get_zero_rated_skus()
		self.assertIn("ZERO-RATED-001", zero_rated)
		self.assertNotIn("TAXABLE-001", zero_rated)
		self.assertTrue(detector.is_zero_rated("ZERO-RATED-001"))
		self.assertFalse(detector.is_zero_rated("TAXABLE-001"))

	def test_zero_rated_by_empty_tax_lines(self):
		"""Test detection of zero-rated item via empty tax_lines."""
		from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "NO-TAX-001",
					"taxable": True,  # Still marked taxable but no tax_lines
					"tax_lines": [],
				},
			]
		)
		store = get_test_store()

		detector = TaxDetector(order, store)

		self.assertTrue(detector.is_zero_rated("NO-TAX-001"))

	def test_zero_rated_by_zero_rate(self):
		"""Test detection of zero-rated item via rate=0."""
		from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "ZERO-RATE-001",
					"taxable": True,
					"tax_lines": [{"title": "GST", "rate": 0, "price": "0.00"}],
				},
			]
		)
		store = get_test_store()

		detector = TaxDetector(order, store)

		self.assertTrue(detector.is_zero_rated("ZERO-RATE-001"))

	def test_item_tax_rate_json(self):
		"""Test generation of item_tax_rate JSON for zero-rated items."""
		from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "ZERO-001",
					"taxable": False,
					"tax_lines": [],
				},
			]
		)
		store = get_test_store()

		detector = TaxDetector(order, store)

		json_str = detector.get_item_tax_rate_json("ZERO-001")
		self.assertIsNotNone(json_str)
		tax_rate = json.loads(json_str)
		# Should have the store's default tax account with 0 rate
		self.assertIn(store.default_sales_tax_account, tax_rate)
		self.assertEqual(tax_rate[store.default_sales_tax_account], 0)

	def test_item_tax_rate_json_none_for_taxable(self):
		"""Test that item_tax_rate JSON is None for taxable items."""
		from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "TAXABLE-001",
					"taxable": True,
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				},
			]
		)
		store = get_test_store()

		detector = TaxDetector(order, store)

		self.assertIsNone(detector.get_item_tax_rate_json("TAXABLE-001"))


class TestShippingTaxHandler(FrappeTestCase):
	"""Tests for ShippingTaxHandler class."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data(commit=True)

	def test_shipping_as_item(self):
		"""Test shipping added as line item when add_shipping_as_item=True."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = load_shopify_order("order1.json")

		# Create a modified store with add_shipping_as_item=True
		store = get_test_store()
		store.add_shipping_as_item = True
		store.shipping_item = "SHIPPING"

		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=1)
		tax_rows = handler.build()

		# When shipping is an item, it should be added to items list
		self.assertEqual(len(items), 2)  # Original item + shipping
		shipping_item = items[-1]
		self.assertEqual(shipping_item["item_code"], "SHIPPING")
		self.assertEqual(shipping_item["qty"], 1)

		# Tax rows should be empty (no separate shipping tax row)
		self.assertEqual(len(tax_rows), 0)

	def test_shipping_as_tax_row(self):
		"""Test shipping added as tax row with GST on previous row."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = load_shopify_order("order1.json")
		store = get_test_store()
		store.add_shipping_as_item = False

		items = [{"delivery_date": "2026-02-03"}]

		# Assume 1 tax row already exists (the GST On Net Total)
		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=1)
		tax_rows = handler.build()

		# Should have 2 rows: shipping charge + GST on shipping
		self.assertEqual(len(tax_rows), 2)

		# First row: Shipping as Actual
		shipping_row = tax_rows[0]
		self.assertEqual(shipping_row["charge_type"], "Actual")
		self.assertIn("Shipping", shipping_row["description"])

		# Second row: GST on shipping as On Previous Row Amount
		gst_row = tax_rows[1]
		self.assertEqual(gst_row["charge_type"], "On Previous Row Amount")
		self.assertEqual(gst_row["rate"], 15.0)  # 15% GST
		self.assertEqual(gst_row["row_id"], 2)  # References the shipping row

	def test_shipping_amount_calculation_tax_inclusive(self):
		"""Test shipping amount calculation for tax-inclusive orders."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		# Order1 has taxes_included: true, shipping price: 31.05, GST: 4.05
		order = load_shopify_order("order1.json")
		store = get_test_store()
		store.add_shipping_as_item = False

		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=1)
		tax_rows = handler.build()

		shipping_row = tax_rows[0]
		# Net shipping = 31.05 - 4.05 (GST) = 27.00
		self.assertEqual(shipping_row["tax_amount"], 27.0)

	def test_no_shipping(self):
		"""Test order with no shipping lines."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = create_test_shopify_order(shipping_lines=[])
		store = get_test_store()

		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=0)
		tax_rows = handler.build()

		self.assertEqual(len(tax_rows), 0)

	def test_free_shipping_skipped(self):
		"""Test that free shipping (price=0) is skipped and no tax row is added."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = create_test_shopify_order(
			shipping_lines=[
				{
					"price": "0.00",
					"title": "Free Pickup",
					"tax_lines": [],
				}
			]
		)
		store = get_test_store()
		store.add_shipping_as_item = False

		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=0)
		tax_rows = handler.build()

		# Free shipping should not add any tax rows
		self.assertEqual(len(tax_rows), 0)

	def test_multiple_shipping_lines(self):
		"""Test handling of orders with multiple shipping methods."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = create_test_shopify_order(
			shipping_lines=[
				{
					"price": "10.00",
					"title": "Standard Shipping",
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				},
				{
					"price": "5.00",
					"title": "Express Handling",
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "0.75"}],
				},
			]
		)
		store = get_test_store()
		store.add_shipping_as_item = False
		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=1)
		tax_rows = handler.build()

		# Should have 4 rows: 2 shipping charges + 2 GST on shipping
		self.assertEqual(len(tax_rows), 4)

		# Verify row structure:
		# Row 0: Standard Shipping (Actual)
		# Row 1: GST on Standard Shipping (On Previous Row Amount, row_id=2)
		# Row 2: Express Handling (Actual)
		# Row 3: GST on Express Handling (On Previous Row Amount, row_id=4)
		self.assertEqual(tax_rows[0]["charge_type"], "Actual")
		self.assertEqual(tax_rows[0]["description"], "Standard Shipping")

		self.assertEqual(tax_rows[1]["charge_type"], "On Previous Row Amount")
		self.assertEqual(tax_rows[1]["row_id"], 2)  # References row 2 (first shipping)

		self.assertEqual(tax_rows[2]["charge_type"], "Actual")
		self.assertEqual(tax_rows[2]["description"], "Express Handling")

		self.assertEqual(tax_rows[3]["charge_type"], "On Previous Row Amount")
		self.assertEqual(tax_rows[3]["row_id"], 4)  # References row 4 (second shipping)

		# Verify GST rates
		self.assertEqual(tax_rows[1]["rate"], 15.0)
		self.assertEqual(tax_rows[3]["rate"], 15.0)


class TestTaxBuilder(FrappeTestCase):
	"""Tests for TaxBuilder class - main orchestrator."""

	# Test item codes used in tests
	TEST_ITEM_CODES = ["TAXABLE-001", "ZERO-001"]

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data(commit=True)
		# Create test Items in database (needed for TaxBuilder._build_sku_lookup)
		create_test_items(cls.TEST_ITEM_CODES, commit=True)

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		# Clean up test items
		delete_test_items(cls.TEST_ITEM_CODES, commit=True)

	def test_build_with_real_order(self):
		"""Test TaxBuilder with real order data."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = load_shopify_order("order1.json")
		store = get_test_store()
		store.add_shipping_as_item = False

		items = [
			{"item_code": "6393", "delivery_date": "2026-02-03"},
			{"item_code": "10701", "delivery_date": "2026-02-03"},
			{"item_code": "7323", "delivery_date": "2026-02-03"},
		]

		builder = TaxBuilder(order, store, items)
		tax_rows = builder.build()

		# Should have at least 1 tax row (GST On Net Total)
		self.assertGreaterEqual(len(tax_rows), 1)

		# First row should be GST On Net Total
		gst_row = tax_rows[0]
		self.assertEqual(gst_row["charge_type"], "On Net Total")
		self.assertEqual(gst_row["rate"], 15.0)

	def test_build_large_order_reduces_tax_rows(self):
		"""
		Test TaxBuilder with large order (60 items).

		With old approach: 60+ tax rows (one per line item)
		With new approach: ~3 tax rows (GST + Shipping + GST on Shipping)
		"""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = load_shopify_order("order5.json")
		store = get_test_store()
		store.add_shipping_as_item = False

		# Create 60 mock items
		items = [{"item_code": f"ITEM-{i}", "delivery_date": "2026-02-03"} for i in range(60)]

		builder = TaxBuilder(order, store, items)
		tax_rows = builder.build()

		# Key assertion: should have MUCH fewer than 60 rows
		self.assertLess(len(tax_rows), 10)

		# Should have exactly 1 On Net Total row for GST
		on_net_total_rows = [r for r in tax_rows if r["charge_type"] == "On Net Total"]
		self.assertEqual(len(on_net_total_rows), 1)

	def test_build_australian_order(self):
		"""Test TaxBuilder with Australian order (10% GST)."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = load_shopify_order("order4.json")
		store = get_test_store()
		store.add_shipping_as_item = False

		items = [{"item_code": "GFD42P09", "delivery_date": "2026-02-03"}]

		builder = TaxBuilder(order, store, items)
		tax_rows = builder.build()

		# Find the GST row
		gst_rows = [r for r in tax_rows if r.get("charge_type") == "On Net Total"]
		self.assertGreaterEqual(len(gst_rows), 1)

		# Should have 10% rate (Australian GST)
		self.assertEqual(gst_rows[0]["rate"], 10.0)

	def test_zero_rated_items_fallback_to_item_tax_rate(self):
		"""
		Test that zero-rated items fall back to item_tax_rate when no template configured.

		When zero_rated_item_tax_template is not set, the builder should fall back
		to setting item_tax_rate JSON for zero-rated items.
		"""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "TAXABLE-001",
					"taxable": True,
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				},
				{
					"sku": "ZERO-001",
					"taxable": False,
					"tax_lines": [],
				},
			],
			shipping_lines=[],
		)
		store = get_test_store()
		# Ensure no item tax templates are configured (fallback scenario)
		for mapping in store.tax_accounts or []:
			mapping.taxable_item_tax_template = None
			mapping.zero_rated_item_tax_template = None

		items = [
			{"item_code": "TAXABLE-001", "delivery_date": "2026-02-03"},
			{"item_code": "ZERO-001", "delivery_date": "2026-02-03"},
		]

		builder = TaxBuilder(order, store, items)
		builder.build()

		# Check that the zero-rated item got item_tax_rate applied (fallback)
		zero_rated_item = items[1]
		self.assertIn("item_tax_rate", zero_rated_item)
		self.assertNotIn("item_tax_template", zero_rated_item)
		tax_rate = json.loads(zero_rated_item["item_tax_rate"])
		self.assertEqual(tax_rate[store.default_sales_tax_account], 0)

		# Check that the taxable item did NOT get item_tax_rate or item_tax_template
		taxable_item = items[0]
		self.assertNotIn("item_tax_rate", taxable_item)
		self.assertNotIn("item_tax_template", taxable_item)

	def test_order_tax_structure(self):
		"""
		Test tax structure for order1.json.

		Order1 details:
		- 3 items, all taxable at 15% GST
		- taxes_included: true
		- Shipping: $31.05 with $4.05 GST
		- Total: $555.75
		"""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = load_shopify_order("order1.json")
		store = get_test_store()
		store.add_shipping_as_item = False

		items = [
			{"item_code": "6393", "delivery_date": "2026-02-03"},
			{"item_code": "10701", "delivery_date": "2026-02-03"},
			{"item_code": "7323", "delivery_date": "2026-02-03"},
		]

		builder = TaxBuilder(order, store, items)
		tax_rows = builder.build()

		# Verify structure
		charge_types = [r["charge_type"] for r in tax_rows]

		# Should have On Net Total for GST
		self.assertIn("On Net Total", charge_types)

		# Should have Actual for shipping (when add_shipping_as_item=False)
		self.assertIn("Actual", charge_types)

		# Should have On Previous Row Amount for GST on shipping
		self.assertIn("On Previous Row Amount", charge_types)

	def test_zero_rated_items_with_taxable_shipping_as_item(self):
		"""
		Test that GST row is created when all items are zero-rated but shipping is taxable.

		This is an edge case where:
		- All line items are zero-rated (taxable=False, no tax_lines)
		- Shipping has tax_lines (taxable shipping)
		- add_shipping_as_item=True

		Expected: GST "On Net Total" row should still be created from shipping tax_lines.
		"""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		# Order: all zero-rated items, taxable shipping
		order = create_test_shopify_order(
			line_items=[
				{"sku": "ZERO-001", "taxable": False, "tax_lines": []},
			],
			shipping_lines=[
				{
					"price": "10.00",
					"title": "Standard Shipping",
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				}
			],
		)

		store = get_test_store()
		store.add_shipping_as_item = True
		store.shipping_item = "SHIPPING"

		items = [{"item_code": "ZERO-001", "delivery_date": "2026-02-05"}]

		builder = TaxBuilder(order, store, items)
		tax_rows = builder.build()

		# Should have GST "On Net Total" row created from shipping tax_lines
		self.assertGreaterEqual(len(tax_rows), 1, "Expected at least one tax row for shipping GST")

		# Verify GST row exists with correct type
		on_net_total_rows = [r for r in tax_rows if r["charge_type"] == "On Net Total"]
		self.assertEqual(len(on_net_total_rows), 1, "Expected one 'On Net Total' GST row")

		# Shipping should be added as item
		shipping_items = [i for i in items if i.get("item_code") == "SHIPPING"]
		self.assertEqual(len(shipping_items), 1, "Shipping item should be added")


class TestRoundingAdjuster(FrappeTestCase):
	"""Tests for rounding adjustment functionality."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data(commit=True)

	def _create_mock_sales_order(self, grand_total: float):
		"""Create a mock Sales Order-like object for testing."""
		from unittest.mock import MagicMock

		so = MagicMock()
		so.grand_total = grand_total
		so.company = "_Test Company"
		so.name = "SO-TEST-001"
		so.cost_center = frappe.db.get_value(
			"Cost Center", {"company": "_Test Company", "is_group": 0}, "name"
		)
		return so

	def test_no_adjustment_needed(self):
		"""Test no adjustment when totals match."""
		from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment

		order = {"total_price": "100.00"}
		so = self._create_mock_sales_order(100.00)

		adjustment = apply_rounding_adjustment(so, order)

		self.assertEqual(adjustment, 0.0)
		# Should not have appended any tax row
		so.append.assert_not_called()

	def test_positive_adjustment(self):
		"""Test positive adjustment (ERPNext total < Shopify total)."""
		from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment

		order = {"total_price": "100.50"}
		so = self._create_mock_sales_order(100.00)

		adjustment = apply_rounding_adjustment(so, order)

		self.assertEqual(adjustment, 0.50)
		so.append.assert_called_once()
		call_args = so.append.call_args
		self.assertEqual(call_args[0][0], "taxes")
		tax_row = call_args[0][1]
		self.assertEqual(tax_row["charge_type"], "Actual")
		self.assertEqual(tax_row["tax_amount"], 0.50)

	def test_negative_adjustment(self):
		"""Test negative adjustment (ERPNext total > Shopify total)."""
		from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment

		order = {"total_price": "99.50"}
		so = self._create_mock_sales_order(100.00)

		adjustment = apply_rounding_adjustment(so, order)

		self.assertEqual(adjustment, -0.50)
		so.append.assert_called_once()
		tax_row = so.append.call_args[0][1]
		self.assertEqual(tax_row["tax_amount"], -0.50)

	def test_tolerance_threshold(self):
		"""Test that 0.01 differences are adjusted (only sub-cent ignored)."""
		from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment

		order = {"total_price": "100.01"}
		so = self._create_mock_sales_order(100.00)

		adjustment = apply_rounding_adjustment(so, order)

		# 0.01 difference should be written off
		self.assertEqual(adjustment, 0.01)
		so.append.assert_called_once()

	def test_no_write_off_account_raises_error(self):
		"""Test that missing write_off_account raises a clear error."""
		from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment

		order = {"total_price": "100.50"}
		so = self._create_mock_sales_order(100.00)
		so.company = "NonExistent Company"  # Company without write_off_account

		with self.assertRaises(frappe.ValidationError) as context:
			apply_rounding_adjustment(so, order)

		# Should throw with actionable error message
		self.assertIn("write_off_account", str(context.exception).lower())
		self.assertIn("Shopify Store or Company", str(context.exception))

	def test_store_level_write_off_account(self):
		"""Test that store-level write_off_account is used when configured."""
		from unittest.mock import MagicMock

		from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment

		order = {"total_price": "100.50"}
		so = self._create_mock_sales_order(100.00)

		# Create mock store with write_off_account
		store = MagicMock()
		store.write_off_account = "Store Write Off Account - TC"

		adjustment = apply_rounding_adjustment(so, order, store=store)

		self.assertEqual(adjustment, 0.50)
		so.append.assert_called_once()
		tax_row = so.append.call_args[0][1]
		# Should use store's write_off_account, not company's
		self.assertEqual(tax_row["account_head"], "Store Write Off Account - TC")


class TestErrorHandling(FrappeTestCase):
	"""Tests for error handling in configuration validation."""

	# Test item codes used in tests
	TEST_ITEM_CODES = ["ITEM-001"]

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data(commit=True)
		# Create test Items in database (needed for TaxBuilder._build_sku_lookup)
		create_test_items(cls.TEST_ITEM_CODES, commit=True)

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		# Clean up test items
		delete_test_items(cls.TEST_ITEM_CODES, commit=True)

	def test_missing_tax_account_raises_error(self):
		"""Test that missing tax account configuration raises clear error."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "ITEM-001",
					"taxable": True,
					"tax_lines": [{"title": "UnmappedTax", "rate": 0.20, "price": "2.00"}],
				},
			],
			shipping_lines=[],
		)

		# Create a store without tax account mapping for "UnmappedTax"
		store = get_test_store()
		# Clear the default tax account to force the error
		original_default = store.default_sales_tax_account
		store.default_sales_tax_account = None
		store.tax_accounts = []

		items = [{"item_code": "ITEM-001", "delivery_date": "2026-02-03"}]

		try:
			builder = TaxBuilder(order, store, items)
			with self.assertRaises(frappe.ValidationError) as context:
				builder.build()

			self.assertIn("UnmappedTax", str(context.exception))
			self.assertIn("not configured", str(context.exception).lower())
		finally:
			# Restore
			store.default_sales_tax_account = original_default

	def test_missing_shipping_item_raises_error(self):
		"""Test that missing shipping_item when add_shipping_as_item=True raises error."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = create_test_shopify_order(
			shipping_lines=[
				{
					"price": "10.00",
					"title": "Standard Shipping",
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				}
			]
		)

		store = get_test_store()
		store.add_shipping_as_item = True
		store.shipping_item = None  # Missing shipping item

		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=0)

		with self.assertRaises(frappe.ValidationError) as context:
			handler.build()

		self.assertIn("Shipping Item not configured", str(context.exception))

	def test_missing_shipping_account_raises_error(self):
		"""Test that missing shipping account configuration raises error."""
		from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

		order = create_test_shopify_order(
			shipping_lines=[
				{
					"price": "10.00",
					"title": "Custom Shipping",
					"tax_lines": [],
				}
			]
		)

		store = get_test_store()
		store.add_shipping_as_item = False
		# Clear the default shipping account to force the error
		original_default = store.default_shipping_charges_account
		store.default_shipping_charges_account = None

		items = [{"delivery_date": "2026-02-03"}]

		handler = ShippingTaxHandler(store, items, order, current_tax_row_count=0)

		try:
			with self.assertRaises(frappe.ValidationError) as context:
				handler.build()

			self.assertIn("Shipping charges account not configured", str(context.exception))
		finally:
			store.default_shipping_charges_account = original_default


class TestItemTaxTemplateSupport(FrappeTestCase):
	"""Tests for Item Tax Template support in tax handling."""

	# Test item codes used in tests
	TEST_ITEM_CODES = ["TAXABLE-001", "ZERO-001", "DUMMY"]

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data(commit=True)
		cls._create_test_item_tax_templates()
		# Create test Items in database (needed for TaxBuilder._build_sku_lookup)
		create_test_items(cls.TEST_ITEM_CODES, commit=True)

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		# Clean up test items
		delete_test_items(cls.TEST_ITEM_CODES, commit=True)
		# Clean up test Item Tax Templates
		for template_name in [
			getattr(cls, "gst15_template", None),
			getattr(cls, "zr_template", None),
		]:
			if template_name and frappe.db.exists("Item Tax Template", template_name):
				frappe.delete_doc("Item Tax Template", template_name, force=True)

	@classmethod
	def _create_test_item_tax_templates(cls):
		"""Create GST15 and ZR Item Tax Templates for testing."""
		company = frappe.db.get_single_value("Global Defaults", "default_company")
		if not company:
			company = frappe.db.get_value("Company", {}, "name")

		# Get company abbreviation for template name
		abbr = frappe.db.get_value("Company", company, "abbr") or ""

		# Get tax account
		tax_account = frappe.db.get_value(
			"Account",
			{"account_type": "Tax", "company": company, "is_group": 0},
			"name",
		)

		# Create GST15 template if not exists
		# Item Tax Template names include company abbreviation: "Title - ABBR"
		gst15_title = "_Test GST15 Template"
		cls.gst15_template = f"{gst15_title} - {abbr}" if abbr else gst15_title
		if not frappe.db.exists("Item Tax Template", cls.gst15_template):
			template = frappe.get_doc(
				{
					"doctype": "Item Tax Template",
					"title": gst15_title,
					"company": company,
					"taxes": [
						{
							"tax_type": tax_account,
							"tax_rate": 15,
						}
					],
				}
			)
			template.insert(ignore_permissions=True)
			cls.gst15_template = template.name  # Use actual name

		# Create ZR (Zero Rated) template if not exists
		zr_title = "_Test ZR Template"
		cls.zr_template = f"{zr_title} - {abbr}" if abbr else zr_title
		if not frappe.db.exists("Item Tax Template", cls.zr_template):
			template = frappe.get_doc(
				{
					"doctype": "Item Tax Template",
					"title": zr_title,
					"company": company,
					"taxes": [
						{
							"tax_type": tax_account,
							"tax_rate": 0,
						}
					],
				}
			)
			template.insert(ignore_permissions=True)
			cls.zr_template = template.name  # Use actual name

		frappe.db.commit()

	def test_zero_rated_item_gets_template(self):
		"""Zero-rated items get zero_rated_item_tax_template when configured."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "ZERO-001",
					"taxable": False,
					"tax_lines": [],
				},
			],
			shipping_lines=[],
		)
		store = get_test_store()

		# Configure the store with zero_rated_item_tax_template
		# Need to add a GST tax line to have a primary tax title
		order["line_items"].append(
			{
				"sku": "DUMMY",
				"taxable": True,
				"tax_lines": [{"title": "GST", "rate": 0.15, "price": "0.01"}],
			}
		)

		if store.tax_accounts:
			store.tax_accounts[0].zero_rated_item_tax_template = self.zr_template

		items = [
			{"item_code": "ZERO-001", "delivery_date": "2026-02-03"},
			{"item_code": "DUMMY", "delivery_date": "2026-02-03"},
		]

		builder = TaxBuilder(order, store, items)
		builder.build()

		# Verify item_tax_template is set for zero-rated item
		self.assertEqual(items[0].get("item_tax_template"), self.zr_template)
		self.assertNotIn("item_tax_rate", items[0])

	def test_zero_rated_fallback_when_no_template(self):
		"""Zero-rated items fall back to item_tax_rate when no template configured."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "ZERO-001",
					"taxable": False,
					"tax_lines": [],
				},
			],
			shipping_lines=[],
		)
		store = get_test_store()

		# Ensure NO zero_rated template is configured (fallback scenario)
		for mapping in store.tax_accounts or []:
			mapping.zero_rated_item_tax_template = None

		items = [{"item_code": "ZERO-001", "delivery_date": "2026-02-03"}]

		builder = TaxBuilder(order, store, items)
		builder.build()

		# Verify item_tax_rate is set (fallback), not item_tax_template
		self.assertNotIn("item_tax_template", items[0])
		self.assertIn("item_tax_rate", items[0])
		tax_rate = json.loads(items[0]["item_tax_rate"])
		self.assertEqual(tax_rate[store.default_sales_tax_account], 0)

	def test_taxable_item_no_override(self):
		"""Taxable items don't get any item-level tax override (use SO-level tax)."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "TAXABLE-001",
					"taxable": True,
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				},
			],
			shipping_lines=[],
		)
		store = get_test_store()

		# Configure zero_rated template (should not affect taxable items)
		if store.tax_accounts:
			store.tax_accounts[0].zero_rated_item_tax_template = self.zr_template

		items = [{"item_code": "TAXABLE-001", "delivery_date": "2026-02-03"}]

		builder = TaxBuilder(order, store, items)
		builder.build()

		# Verify taxable item gets NO item-level override (uses SO-level tax rate)
		self.assertNotIn("item_tax_template", items[0])
		self.assertNotIn("item_tax_rate", items[0])

	def test_mixed_order_only_zero_rated_gets_template(self):
		"""In mixed orders, only zero-rated items get item_tax_template."""
		from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder

		order = create_test_shopify_order(
			line_items=[
				{
					"sku": "TAXABLE-001",
					"taxable": True,
					"tax_lines": [{"title": "GST", "rate": 0.15, "price": "1.50"}],
				},
				{
					"sku": "ZERO-001",
					"taxable": False,
					"tax_lines": [],
				},
			],
			shipping_lines=[],
		)
		store = get_test_store()

		# Configure zero_rated_item_tax_template
		if store.tax_accounts:
			store.tax_accounts[0].zero_rated_item_tax_template = self.zr_template

		items = [
			{"item_code": "TAXABLE-001", "delivery_date": "2026-02-03"},
			{"item_code": "ZERO-001", "delivery_date": "2026-02-03"},
		]

		builder = TaxBuilder(order, store, items)
		builder.build()

		# Verify taxable item gets NO item-level override (uses SO-level tax rate)
		self.assertNotIn("item_tax_template", items[0])
		self.assertNotIn("item_tax_rate", items[0])

		# Verify zero-rated item gets ZR template
		self.assertEqual(items[1].get("item_tax_template"), self.zr_template)
		self.assertNotIn("item_tax_rate", items[1])

# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings

from nexwave_shopify_connector.nexwave_shopify._fulfillment_test_fixtures import (
	TEST_ITEM,
	TEST_WAREHOUSE,
	create_fulfillment_stock_item,
	create_fulfillment_test_delivery,
	create_fulfillment_test_order,
	create_fulfillment_test_store,
	fulfillment_payload,
)
from nexwave_shopify_connector.nexwave_shopify.fulfillment import (
	_get_fulfillment_items,
	create_delivery_notes_from_fulfillments,
)
from nexwave_shopify_connector.nexwave_shopify.order import _create_sales_order

test_dependencies = ["Company", "Customer", "Warehouse", "Item"]


class TestFulfillmentAllocation(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.store = create_fulfillment_test_store()

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def get_items(self, so, lines):
		return _get_fulfillment_items(so.items, lines, None, self.store, so.name)

	def process(self, so, *lines):
		result = create_delivery_notes_from_fulfillments(fulfillment_payload(so, *lines), self.store)
		return [frappe.get_doc("Delivery Note", name) for name in result["created"]]

	def assert_allocations(self, dn, so, quantities):
		for row, qty in zip(so.items, quantities, strict=True):
			matches = [item for item in dn.items if item.so_detail == row.name]
			self.assertEqual(sum(item.qty for item in matches), qty)
			for item in matches:
				self.assertEqual(item.rate, row.rate)
				self.assertEqual(item.against_sales_order, so.name)
				self.assertEqual(item.warehouse, TEST_WAREHOUSE)

	def test_duplicate_sku_is_delivered_and_billed_on_each_order_row(self):
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		for mode in (0, 1):
			with self.subTest(mode=mode):
				self.store.auto_fulfill_remaining_qty = mode
				so = create_fulfillment_test_order()
				si = make_sales_invoice(so.name)
				si.insert()
				si.submit()
				dn = self.process(so, [{"sku": TEST_ITEM, "quantity": 5}])[0]
				self.assert_allocations(dn, so, [2, 3])
				self.assertEqual(dn.total, 425)
				self.assertEqual(dn.per_billed, 100)
				self.assertEqual(dn.status, "Completed")
				self.assertEqual([row.billed_amt for row in dn.items], [200, 225])
				so.reload()
				self.assertEqual([row.delivered_qty for row in so.items], [2, 3])
				self.assertEqual(so.status, "Completed")

	def test_repeated_payload_lines_share_remaining_capacity(self):
		for mode in (0, 1):
			with self.subTest(mode=mode):
				self.store.auto_fulfill_remaining_qty = mode
				so = create_fulfillment_test_order()
				dn = self.process(so, [{"sku": TEST_ITEM, "quantity": 1}, {"sku": TEST_ITEM, "quantity": 4}])[
					0
				]
				self.assert_allocations(dn, so, [2, 3])

	def test_multiple_fulfillments_use_fresh_delivered_quantities_in_both_modes(self):
		for mode in (0, 1):
			with self.subTest(mode=mode):
				self.store.auto_fulfill_remaining_qty = mode
				so = create_fulfillment_test_order([{"qty": 1, "rate": 100}, {"qty": 1, "rate": 75}])
				dns = self.process(
					so, [{"sku": TEST_ITEM, "quantity": 1}], [{"sku": TEST_ITEM, "quantity": 1}]
				)
				self.assertEqual(len(dns), 2)
				self.assertEqual([dn.items[0].so_detail for dn in dns], [row.name for row in so.items])
				so.reload()
				self.assertEqual(so.per_delivered, 100)

	def test_remaining_mode_allocates_after_prior_partial_delivery(self):
		self.store.auto_fulfill_remaining_qty = 1
		so = create_fulfillment_test_order()
		create_fulfillment_test_delivery(so, {so.items[0].name: 1})
		dn = self.process(so, [{"sku": TEST_ITEM, "quantity": 5}])[0]
		self.assert_allocations(dn, so, [1, 3])
		so.reload()
		self.assertEqual(so.per_delivered, 100)

	def test_remaining_mode_skips_fully_delivered_first_row(self):
		self.store.auto_fulfill_remaining_qty = 1
		so = create_fulfillment_test_order()
		create_fulfillment_test_delivery(so, {so.items[0].name: 2})
		dn = self.process(so, [{"sku": TEST_ITEM, "quantity": 3}])[0]
		self.assert_allocations(dn, so, [0, 3])

	def test_exact_line_id_preserves_out_of_order_partial_price(self):
		so = create_fulfillment_test_order(
			[
				{"qty": 2, "rate": 100, "shopify_line_item_id": "101"},
				{"qty": 3, "rate": 75, "shopify_line_item_id": "102"},
			]
		)
		so.reload()
		dn = self.process(so, [{"id": 102, "sku": TEST_ITEM, "quantity": 1}])[0]
		self.assert_allocations(dn, so, [0, 1])
		self.assertEqual(dn.total, 75)

	def test_exact_line_id_handles_changed_or_missing_sku(self):
		so = create_fulfillment_test_order([{"qty": 2, "rate": 75, "shopify_line_item_id": "102"}])
		for sku in ("old-sku", None):
			with self.subTest(sku=sku):
				items = self.get_items(so, [{"id": 102, "sku": sku, "quantity": 1}])
				self.assertEqual(items[0]["so_detail"], so.items[0].name)
				self.assertEqual(items[0]["rate"], 75)

	def test_unknown_or_missing_id_cannot_use_known_id_rows(self):
		so = create_fulfillment_test_order([{"qty": 2, "rate": 75, "shopify_line_item_id": "102"}])
		for line_id in (999, None):
			with self.subTest(line_id=line_id):
				with self.assertRaisesRegex(ValueError, "line.*match"):
					self.get_items(so, [{"id": line_id, "sku": TEST_ITEM, "quantity": 1}])

	def test_legacy_fallback_never_consumes_known_id_row(self):
		so = create_fulfillment_test_order(
			[
				{"qty": 2, "rate": 100, "shopify_line_item_id": "101"},
				{"qty": 3, "rate": 75},
			]
		)
		for line_id in (999, None):
			with self.subTest(line_id=line_id):
				items = self.get_items(so, [{"id": line_id, "sku": TEST_ITEM, "quantity": 1}])
				self.assertEqual(items[0]["so_detail"], so.items[1].name)

	def test_exact_exhausted_row_does_not_spill_into_other_row(self):
		self.store.auto_fulfill_remaining_qty = 1
		so = create_fulfillment_test_order(
			[
				{"qty": 1, "rate": 100, "shopify_line_item_id": "101"},
				{"qty": 1, "rate": 75, "shopify_line_item_id": "102"},
			]
		)
		create_fulfillment_test_delivery(so, {so.items[1].name: 1})
		self.assertEqual(self.get_items(so, [{"id": 102, "sku": TEST_ITEM, "quantity": 1}]), [])

	def test_identity_conflict_prevents_partial_dn_insertion(self):
		so = create_fulfillment_test_order([{"qty": 2, "rate": 75, "shopify_line_item_id": "102"}])
		with self.assertRaisesRegex(ValueError, "line.*match"):
			self.process(
				so,
				[{"id": 102, "sku": TEST_ITEM, "quantity": 1}, {"id": 999, "sku": TEST_ITEM, "quantity": 1}],
			)
		self.assertFalse(frappe.db.exists("Delivery Note", {"shopify_order_id": so.shopify_order_id}))

	def test_duplicate_stored_line_ids_are_rejected(self):
		so = create_fulfillment_test_order(
			[
				{"qty": 1, "rate": 100, "shopify_line_item_id": "101"},
				{"qty": 1, "rate": 75, "shopify_line_item_id": "101"},
			]
		)
		with self.assertRaisesRegex(ValueError, "Duplicate Shopify line"):
			self.get_items(so, [{"id": 101, "sku": TEST_ITEM, "quantity": 1}])

	def test_order_import_persists_line_ids_as_strings(self):
		so = _create_sales_order(
			{
				"id": frappe.generate_hash(length=12),
				"created_at": frappe.utils.nowdate(),
				"currency": frappe.get_cached_value("Company", self.store.company, "default_currency"),
				"total_price": "175.00",
				"line_items": [
					{"id": 101, "sku": TEST_ITEM, "price": "100", "quantity": 1},
					{"id": 102, "sku": TEST_ITEM, "price": "75", "quantity": 1},
				],
			},
			self.store,
			"_Test Customer",
		)
		so.submit()
		so.reload()
		self.assertEqual([row.get("shopify_line_item_id") for row in so.items], ["101", "102"])
		dns = self.process(
			so,
			[{"id": 102, "sku": TEST_ITEM, "quantity": 1}],
			[{"id": 101, "sku": TEST_ITEM, "quantity": 1}],
		)
		self.assertEqual([dn.items[0].so_detail for dn in dns], [so.items[1].name, so.items[0].name])
		self.assertEqual([dn.items[0].rate for dn in dns], [75, 100])

	def test_remaining_mode_caps_repeated_lines_at_row_capacity(self):
		self.store.auto_fulfill_remaining_qty = 1
		so = create_fulfillment_test_order([{"qty": 2, "rate": 100, "shopify_line_item_id": "101"}])
		dn = self.process(
			so,
			[{"id": 101, "sku": TEST_ITEM, "quantity": 1}, {"id": 101, "sku": TEST_ITEM, "quantity": 5}],
		)[0]
		self.assert_allocations(dn, so, [2])

	@change_settings("Stock Settings", {"over_delivery_receipt_allowance": 0})
	def test_default_mode_excess_reaches_native_validation(self):
		from erpnext.controllers.status_updater import OverAllowanceError

		item = create_fulfillment_stock_item()
		so = create_fulfillment_test_order([{"item_code": item.name, "qty": 2, "rate": 100}])
		with self.assertRaises(OverAllowanceError):
			self.process(so, [{"sku": item.name, "quantity": 3}])

	@change_settings("Stock Settings", {"over_delivery_receipt_allowance": 50})
	def test_default_mode_preserves_allowed_overdelivery_quantity(self):
		item = create_fulfillment_stock_item()
		so = create_fulfillment_test_order([{"item_code": item.name, "qty": 2, "rate": 100}])
		dn = self.process(so, [{"sku": item.name, "quantity": 3}])[0]
		self.assertEqual(len(dn.items), 1)
		self.assertEqual(dn.items[0].qty, 3)

	def test_replaying_fulfillment_creates_no_second_dn(self):
		self.store.auto_fulfill_remaining_qty = 1
		so = create_fulfillment_test_order()
		payload = fulfillment_payload(so, [{"sku": TEST_ITEM, "quantity": 5}])
		first = create_delivery_notes_from_fulfillments(payload, self.store)
		second = create_delivery_notes_from_fulfillments(payload, self.store)
		self.assertEqual(len(first["created"]), 1)
		self.assertEqual(second["created"], [])
		self.assertEqual(second["skipped"], 1)

	def test_default_mode_still_blocks_existing_draft_and_submitted_dns(self):
		for submit in (False, True):
			with self.subTest(submit=submit):
				so = create_fulfillment_test_order()
				create_fulfillment_test_delivery(so, {so.items[0].name: 1}, submit=submit)
				self.assertEqual(self.process(so, [{"sku": TEST_ITEM, "quantity": 5}]), [])

	def test_nonpositive_and_unknown_lines_do_not_create_dn_rows(self):
		so = create_fulfillment_test_order()
		self.assertEqual(
			self.get_items(
				so,
				[
					{"sku": TEST_ITEM, "quantity": 0},
					{"sku": TEST_ITEM, "quantity": -1},
					{"sku": "missing-test-sku", "quantity": 1},
				],
			),
			[],
		)

	def test_variant_id_fallback_remains_available_for_legacy_rows(self):
		so = create_fulfillment_test_order()
		items = self.get_items(so, [{"variant_id": TEST_ITEM, "quantity": 1}])
		self.assertEqual(items[0]["so_detail"], so.items[0].name)

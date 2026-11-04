# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Bundle inventory tests use real records and fake only the Shopify boundary."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings
from frappe.utils import add_to_date, now_datetime

from nexwave_shopify_connector.nexwave_shopify._bundle_inventory_test_fixtures import (
	cleanup_bundle_inventory_data,
	make_bundle_definition,
	make_bundle_inventory_data,
	make_component_draft_reservation,
	make_second_warehouse,
)
from nexwave_shopify_connector.nexwave_shopify._inventory_test_fixtures import set_bin_qty
from nexwave_shopify_connector.nexwave_shopify.inventory import (
	_bulk_get_stock_qty,
	_get_changed_inventory_item_codes,
	get_stock_qty,
	sync_changed_bin_inventory,
	sync_items_inventory,
	sync_single_item_inventory,
	sync_store_inventory,
)
from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import BatchResult


@contextmanager
def _noop_shopify_session(*args, **kwargs):
	yield


class TestBundleInventory(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.data = make_bundle_inventory_data()
		self.warehouse = self.data.warehouse
		self.store = self.data.store
		for target, replacement in (
			("Session.temp", _noop_shopify_session),
			("fetch_inventory_item_ids", self._fetch_inventory_ids),
			("set_inventory_batch", self._successful_batch),
		):
			boundary_patch = patch(
				f"nexwave_shopify_connector.nexwave_shopify.inventory.{target}", side_effect=replacement
			)
			mock = boundary_patch.start()
			self.addCleanup(boundary_patch.stop)
			if target == "set_inventory_batch":
				self.shopify_set = mock

	def tearDown(self):
		cleanup_bundle_inventory_data()
		super().tearDown()

	def _fetch_inventory_ids(self, variant_ids, **kwargs):
		return {
			variant_id: {"tracked": True, "inventory_item_id": f"inventory-{variant_id}"}
			for variant_id in variant_ids
		}

	def _successful_batch(self, quantities, *args, **kwargs):
		return BatchResult(succeeded=[quantity["item_code"] for quantity in quantities])

	def _assert_quantity(self, item_code, expected, warehouse=None):
		warehouse = warehouse or self.warehouse
		self.assertEqual(get_stock_qty(item_code, warehouse), expected)
		self.assertEqual(_bulk_get_stock_qty([(item_code, warehouse)])[(item_code, warehouse)], expected)

	def _published_quantities(self):
		return [quantity for call in self.shopify_set.call_args_list for quantity in call.args[0]]

	def _pending(self, item_code):
		return frappe.db.get_value(
			"Item Shopify Store",
			{"parent": item_code, "shopify_store": self.store.name},
			"inventory_sync_pending",
		)

	def _assert_skip_logged(self, item_code):
		self.assertTrue(
			frappe.db.exists(
				"NexWave Shopify Log",
				{"shopify_store": self.store.name, "status": "Warning", "reference_name": item_code},
			)
		)

	def test_component_with_lowest_availability_limits_complete_kits(self):
		self._assert_quantity(self.data.kit.name, 3)
		self._assert_quantity(self.data.stock_item.name, 11)

	def test_duplicate_component_rows_are_added_before_division(self):
		make_bundle_definition(
			self.data.kit.name,
			[
				(self.data.component_a.name, 1),
				(self.data.component_a.name, 2),
				(self.data.component_b.name, 1),
			],
		)
		set_bin_qty(self.data.component_a.name, self.warehouse, 8)
		set_bin_qty(self.data.component_b.name, self.warehouse, 30)
		self._assert_quantity(self.data.kit.name, 2)

	def test_fractional_component_quantity_is_not_truncated(self):
		frappe.db.set_value("Item", self.data.component_a.name, "stock_uom", "Kg")
		make_bundle_definition(
			self.data.kit.name,
			[(self.data.component_a.name, 1.5), (self.data.component_b.name, 2)],
		)
		set_bin_qty(self.data.component_a.name, self.warehouse, 5)
		set_bin_qty(self.data.component_b.name, self.warehouse, 20)
		self._assert_quantity(self.data.kit.name, 3)

	def test_fractional_reservation_does_not_lose_a_complete_kit(self):
		frappe.db.set_value("Item", self.data.component_a.name, "stock_uom", "Kg")
		make_bundle_definition(
			self.data.kit.name,
			[(self.data.component_a.name, 0.1), (self.data.component_b.name, 1)],
		)
		set_bin_qty(self.data.component_a.name, self.warehouse, 0.3)
		frappe.db.set_value(
			"Bin",
			{"item_code": self.data.component_a.name, "warehouse": self.warehouse},
			"reserved_qty",
			0.1,
		)
		self._assert_quantity(self.data.kit.name, 2)

	def test_component_reservations_reduce_kit_availability(self):
		frappe.db.set_value(
			"Bin",
			{"item_code": self.data.component_a.name, "warehouse": self.warehouse},
			"reserved_qty",
			7,
		)
		self._assert_quantity(self.data.kit.name, 2)

	@change_settings("Stock Settings", {"enable_stock_reservation": 1, "allow_partial_reservation": 1})
	def test_component_draft_reservation_is_applied_when_store_setting_enabled(self):
		order = make_component_draft_reservation(self.data)
		self.assertEqual(order.docstatus, 0)
		reservations = frappe.get_all(
			"Stock Reservation Entry",
			filters={"voucher_type": "Sales Order", "voucher_no": order.name, "docstatus": 1},
			fields=["item_code", "reserved_qty"],
		)
		self.assertEqual(len(reservations), 1)
		self.assertEqual(reservations[0].item_code, self.data.component_a.name)
		self.assertEqual(reservations[0].reserved_qty, 7)
		self._assert_quantity(self.data.kit.name, 3)
		self.assertEqual(
			get_stock_qty(self.data.kit.name, self.warehouse, include_draft_order_reservations=True), 2
		)
		self.assertEqual(
			_bulk_get_stock_qty(
				[(self.data.kit.name, self.warehouse)], include_draft_order_reservations=True
			)[(self.data.kit.name, self.warehouse)],
			2,
		)
		sync_store_inventory(self.store.name, force=True)
		published = {q["item_code"]: q["qty"] for q in self._published_quantities()}
		self.assertEqual(published[self.data.kit.name], 2)

	def test_fully_reserved_component_publishes_zero_kits(self):
		frappe.db.set_value(
			"Bin",
			{"item_code": self.data.component_a.name, "warehouse": self.warehouse},
			"reserved_qty",
			12,
		)
		self._assert_quantity(self.data.kit.name, 0)

	def test_missing_component_bin_publishes_zero_kits(self):
		frappe.db.delete("Bin", {"item_code": self.data.component_b.name})
		self._assert_quantity(self.data.kit.name, 0)

	def test_missing_stock_item_bin_still_publishes_zero(self):
		frappe.db.delete("Bin", {"item_code": self.data.stock_item.name})
		self._assert_quantity(self.data.stock_item.name, 0)

	def test_component_availability_is_calculated_per_warehouse(self):
		other_warehouse = make_second_warehouse()
		set_bin_qty(self.data.component_a.name, other_warehouse, 12)
		set_bin_qty(self.data.component_b.name, other_warehouse, 10)
		result = _bulk_get_stock_qty(
			[(self.data.kit.name, self.warehouse), (self.data.kit.name, other_warehouse)]
		)
		self.assertEqual(result[(self.data.kit.name, self.warehouse)], 3)
		self.assertEqual(result[(self.data.kit.name, other_warehouse)], 5)

	def test_undefined_non_stock_kit_is_skipped(self):
		self._assert_quantity(self.data.undefined_kit.name, None)

	def test_disabled_bundle_is_skipped(self):
		frappe.db.set_value("Product Bundle", self.data.kit.name, "disabled", 1)
		self._assert_quantity(self.data.kit.name, None)

	def test_bundle_without_components_is_skipped(self):
		frappe.db.delete("Product Bundle Item", {"parent": self.data.kit.name})
		self._assert_quantity(self.data.kit.name, None)

	def test_disabled_component_invalidates_bundle(self):
		frappe.db.set_value("Item", self.data.component_a.name, "disabled", 1)
		self._assert_quantity(self.data.kit.name, None)

	def test_non_stock_component_invalidates_bundle(self):
		frappe.db.set_value("Item", self.data.component_a.name, "is_stock_item", 0)
		self._assert_quantity(self.data.kit.name, None)

	def test_missing_component_invalidates_bundle(self):
		frappe.db.set_value(
			"Product Bundle Item",
			{"parent": self.data.kit.name, "item_code": self.data.component_a.name},
			"item_code",
			"_Test Missing Bundle Component",
		)
		self._assert_quantity(self.data.kit.name, None)

	def test_non_positive_component_quantity_invalidates_bundle(self):
		for qty in (0, -1):
			with self.subTest(qty=qty):
				frappe.db.set_value(
					"Product Bundle Item",
					{"parent": self.data.kit.name, "item_code": self.data.component_a.name},
					"qty",
					qty,
				)
				self._assert_quantity(self.data.kit.name, None)

	def test_component_uom_mismatch_invalidates_bundle(self):
		frappe.db.set_value(
			"Product Bundle Item",
			{"parent": self.data.kit.name, "item_code": self.data.component_a.name},
			"uom",
			"Kg",
		)
		self._assert_quantity(self.data.kit.name, None)

	def test_empty_component_uom_uses_stock_units(self):
		frappe.db.set_value(
			"Product Bundle Item",
			{"parent": self.data.kit.name, "item_code": self.data.component_a.name},
			"uom",
			"",
		)
		self._assert_quantity(self.data.kit.name, 3)

	def test_full_sync_publishes_kit_and_stock_but_omits_undefined_kit(self):
		sync_store_inventory(self.store.name, force=True)
		published = {q["item_code"]: q["qty"] for q in self._published_quantities()}
		self.assertEqual(published, {self.data.kit.name: 3, self.data.stock_item.name: 11})
		self.assertEqual(self._pending(self.data.kit.name), 0)
		self.assertEqual(self._pending(self.data.undefined_kit.name), 1)
		self._assert_skip_logged(self.data.undefined_kit.name)

	def test_targeted_sync_counts_skip_and_preserves_pending_undefined_kit(self):
		stats = sync_items_inventory(
			self.store.name, [self.data.kit.name, self.data.undefined_kit.name], source="manual"
		)
		self.assertEqual(stats["synced"], 1)
		self.assertEqual(stats["skipped"], 1)
		self.assertEqual(stats["errors"], 0)
		self.assertEqual(
			{q["item_code"]: q["qty"] for q in self._published_quantities()}, {self.data.kit.name: 3}
		)
		self.assertEqual(self._pending(self.data.undefined_kit.name), 1)
		self._assert_skip_logged(self.data.undefined_kit.name)

	def test_single_item_sync_calculates_kit_quantity(self):
		sync_single_item_inventory(self.data.kit.name, self.store.name)
		self.assertEqual(
			{q["item_code"]: q["qty"] for q in self._published_quantities()}, {self.data.kit.name: 3}
		)
		self.assertEqual(self._pending(self.data.kit.name), 0)

	def test_single_item_sync_omits_undefined_kit(self):
		sync_single_item_inventory(self.data.undefined_kit.name, self.store.name)
		self.shopify_set.assert_not_called()
		self.assertEqual(self._pending(self.data.undefined_kit.name), 1)
		self._assert_skip_logged(self.data.undefined_kit.name)

	def test_skipped_kit_cannot_hide_conflicting_inventory_mapping(self):
		inventory_id = frappe.db.get_value(
			"Item Shopify Store",
			{"parent": self.data.stock_item.name, "shopify_store": self.store.name},
			"shopify_inventory_item_id",
		)
		frappe.db.set_value(
			"Item Shopify Store",
			{"parent": self.data.undefined_kit.name, "shopify_store": self.store.name},
			"shopify_inventory_item_id",
			inventory_id,
		)
		for source, run in (
			("full", lambda: sync_store_inventory(self.store.name, force=True)),
			("targeted", lambda: sync_items_inventory(self.store.name, [self.data.stock_item.name])),
			("single", lambda: sync_single_item_inventory(self.data.stock_item.name, self.store.name)),
		):
			with self.subTest(source=source):
				self.shopify_set.reset_mock()
				run()
				published = {q["item_code"] for q in self._published_quantities()}
				self.assertNotIn(self.data.stock_item.name, published)
				self.assertNotIn(self.data.undefined_kit.name, published)
				self.assertEqual(self._pending(self.data.stock_item.name), 1)

	def test_changed_scan_includes_kit_when_components_have_no_shopify_mapping(self):
		cursor = add_to_date(now_datetime(), minutes=-60)
		for item_code in (self.data.kit.name, self.data.undefined_kit.name, self.data.stock_item.name):
			frappe.db.set_value(
				"Item Shopify Store",
				{"parent": item_code, "shopify_store": self.store.name},
				{"inventory_sync_pending": 0, "last_sync_at": None},
				update_modified=False,
			)
		frappe.db.set_value(
			"Bin",
			{"item_code": self.data.stock_item.name, "warehouse": self.warehouse},
			"modified",
			add_to_date(cursor, minutes=-60),
			update_modified=False,
		)
		changed = _get_changed_inventory_item_codes(self.store, since=cursor, until=now_datetime())
		self.assertEqual(changed, [self.data.kit.name])

	def test_changed_bin_sync_refreshes_kit_after_component_quantity_change(self):
		cursor = add_to_date(now_datetime(), minutes=-60)
		frappe.db.set_value(
			"Shopify Store",
			self.store.name,
			{"last_inventory_sync": cursor, "inventory_sync_mode": "Changed Bins"},
		)
		set_bin_qty(self.data.component_b.name, self.warehouse, 4)
		sync_changed_bin_inventory(self.store.name, force=True)
		published = {q["item_code"]: q["qty"] for q in self._published_quantities()}
		self.assertEqual(published[self.data.kit.name], 2)
		self.assertNotIn(self.data.undefined_kit.name, published)

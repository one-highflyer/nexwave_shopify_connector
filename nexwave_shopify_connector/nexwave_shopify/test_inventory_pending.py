# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Pending inventory retries use real Frappe data and a fake Shopify boundary."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from nexwave_shopify_connector.nexwave_shopify._inventory_test_fixtures import (
	_get_default_warehouse,
	ensure_test_item,
	ensure_test_shopify_store,
	set_bin_qty,
)
from nexwave_shopify_connector.nexwave_shopify.inventory import (
	_get_changed_inventory_item_codes,
	sync_changed_bin_inventory,
	sync_items_inventory,
	sync_single_item_inventory,
	sync_store_inventory,
)
from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
	BatchResult,
	ShopifyGraphQLError,
	set_inventory_batch,
)


@contextmanager
def _noop_shopify_session(*args, **kwargs):
	yield


class TestPendingInventorySync(FrappeTestCase):
	STORE_DOMAIN = "_test-pending-inventory.myshopify.com"
	ITEM_CODE = "_Test Shopify Pending Inventory Item"
	OTHER_ITEM_CODE = "_Test Shopify Pending Other Item"

	def setUp(self):
		super().setUp()
		self.warehouse = _get_default_warehouse()
		self.store = ensure_test_shopify_store(
			shop_domain=self.STORE_DOMAIN,
			warehouse=self.warehouse,
			last_inventory_sync=None,
			inventory_sync_mode="Full Inventory",
		)
		self.item = ensure_test_item(self.ITEM_CODE)
		frappe.db.delete("Item Shopify Store", {"parent": self.item.name})
		self.row_name = self._add_mapping(self.item.name, "pending-variant-1")
		set_bin_qty(self.item.name, self.warehouse, 7)

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
			elif target == "fetch_inventory_item_ids":
				self.shopify_fetch = mock

	def tearDown(self):
		frappe.db.delete("NexWave Shopify Log", {"shopify_store": self.store.name})
		for item_code in (self.ITEM_CODE, self.OTHER_ITEM_CODE):
			frappe.db.delete("Bin", {"item_code": item_code})
			if frappe.db.exists("Item", item_code):
				frappe.delete_doc("Item", item_code, force=True, ignore_missing=True)
		frappe.delete_doc("Shopify Store", self.store.name, force=True, ignore_missing=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- remove fixtures persisted by inventory job commits
		super().tearDown()

	def _add_mapping(self, item_code, variant_id, **overrides):
		item = frappe.get_doc("Item", item_code)
		row = item.append(
			"shopify_stores",
			{
				"shopify_store": self.store.name,
				"enabled": 1,
				"shopify_product_id": f"product-{variant_id}",
				"shopify_variant_id": variant_id,
				"shopify_sku": item_code,
				"inventory_sync_pending": 1,
				**overrides,
			},
		)
		row.db_insert()
		return row.name

	def _fetch_inventory_ids(self, variant_ids, **kwargs):
		return {
			variant_id: {"tracked": True, "inventory_item_id": f"inventory-{variant_id}"}
			for variant_id in variant_ids
		}

	def _successful_batch(self, quantities, *args, **kwargs):
		return BatchResult(succeeded=[quantity["item_code"] for quantity in quantities])

	def _pending(self, row_name=None):
		return frappe.db.get_value("Item Shopify Store", row_name or self.row_name, "inventory_sync_pending")

	def _set_store_values(self, **values):
		frappe.db.set_value("Shopify Store", self.store.name, values)
		self.store.reload()

	def _add_second_location(self):
		# The fixture token lives in __Auth; repopulate the Password field so
		# saving warehouse mappings does not remove that synthetic token.
		self.store.reload()
		self.store.access_token = self.store.get_password("access_token")
		self.store.append(
			"warehouse_mapping",
			{"shopify_location_id": "pending-location-2", "erpnext_warehouse": self.warehouse},
		)
		self.store.save(ignore_permissions=True)

	@contextmanager
	def _real_batch_with_graphql_response(self, response):
		"""Run the actual acknowledgment parser while faking only the Shopify response."""
		previous_side_effect = self.shopify_set.side_effect
		self.shopify_set.side_effect = set_inventory_batch
		try:
			with patch(
				"nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql",
				return_value=response,
			) as graphql:
				yield graphql
		finally:
			self.shopify_set.side_effect = previous_side_effect

	def _noop_response(self):
		return {"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}}}

	def test_full_sync_clears_pending_on_explicit_noop_at_all_locations(self):
		second_row = self._add_mapping(self.item.name, "pending-variant-2")
		self._add_second_location()
		with self._real_batch_with_graphql_response(self._noop_response()) as graphql:
			sync_store_inventory(self.store.name, force=True)
			self.assertEqual(graphql.call_count, 2)
			for graphql_call in graphql.call_args_list:
				self.assertEqual(len(graphql_call.args[1]["input"]["quantities"]), 2)
		self.assertEqual(self.shopify_set.call_count, 2)
		for batch_call in self.shopify_set.call_args_list:
			self.assertEqual([quantity["item_code"] for quantity in batch_call.args[0]], [self.item.name] * 2)
		self.assertEqual(self._pending(), 0)
		self.assertEqual(self._pending(second_row), 0)
		self.store.reload()
		self.assertIsNotNone(self.store.last_inventory_sync)

	def test_targeted_sync_clears_only_selected_pending_item_on_explicit_noop(self):
		other_item = ensure_test_item(self.OTHER_ITEM_CODE)
		other_row = self._add_mapping(other_item.name, "pending-other-variant")
		with self._real_batch_with_graphql_response(self._noop_response()) as graphql:
			stats = sync_items_inventory(self.store.name, [self.item.name], source="order_auto_link")
			graphql.assert_called_once()
		self.assertEqual(stats["synced"], 1)
		self.assertEqual(stats["errors"], 0)
		self.assertEqual(self._pending(), 0)
		self.assertEqual(self._pending(other_row), 1)

	def test_single_item_sync_clears_pending_on_explicit_noop(self):
		with self._real_batch_with_graphql_response(self._noop_response()) as graphql:
			sync_single_item_inventory(self.item.name, self.store.name)
			graphql.assert_called_once()
		self.assertEqual(self._pending(), 0)

	def test_changed_bins_acknowledges_explicit_noop_without_stock_changes(self):
		cursor = add_to_date(now_datetime(), minutes=-60)
		self._set_store_values(inventory_sync_mode="Changed Bins", last_inventory_sync=cursor)
		frappe.db.set_value(
			"Bin",
			{"item_code": self.item.name, "warehouse": self.warehouse},
			"modified",
			add_to_date(cursor, minutes=-60),
			update_modified=False,
		)
		with self._real_batch_with_graphql_response(self._noop_response()) as graphql:
			sync_changed_bin_inventory(self.store.name, force=True)
			graphql.assert_called_once()
		self.assertEqual(self._pending(), 0)
		self.store.reload()
		self.assertGreater(self.store.last_inventory_sync, cursor)
		self.assertNotIn(
			self.item.name,
			_get_changed_inventory_item_codes(self.store, self.store.last_inventory_sync, now_datetime()),
		)

	def test_null_group_with_user_error_keeps_all_sibling_variants_pending(self):
		second_row = self._add_mapping(self.item.name, "pending-variant-2")
		response = {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": None,
					"userErrors": [
						{
							"field": ["input", "quantities", "0", "quantity"],
							"message": "Inventory is unavailable at the requested location",
							"code": "INVENTORY_ITEM_NOT_FOUND",
						}
					],
				}
			}
		}
		with self._real_batch_with_graphql_response(response) as graphql:
			stats = sync_items_inventory(self.store.name, [self.item.name])
			graphql.assert_called_once()
		self.assertEqual(stats["synced"], 0)
		self.assertEqual(stats["errors"], 2)
		self.assertEqual(self._pending(), 1)
		self.assertEqual(self._pending(second_row), 1)

	def test_missing_group_or_malformed_response_never_clears_pending(self):
		for response in (
			{"data": {"inventorySetQuantities": {"userErrors": []}}},
			{"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None}}},
			{"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": [], "userErrors": []}}},
			{"data": {"inventorySetQuantities": None}},
		):
			with self.subTest(response=response):
				with self._real_batch_with_graphql_response(response) as graphql:
					stats = sync_items_inventory(self.store.name, [self.item.name])
					graphql.assert_called_once()
				self.assertEqual(stats["synced"], 0)
				self.assertEqual(stats["errors"], 1)
				self.assertEqual(self._pending(), 1)

	def test_changed_bins_retains_pending_and_cursor_when_group_is_missing(self):
		cursor = add_to_date(now_datetime(), minutes=-60)
		self._set_store_values(inventory_sync_mode="Changed Bins", last_inventory_sync=cursor)
		response = {"data": {"inventorySetQuantities": {"userErrors": []}}}
		with self._real_batch_with_graphql_response(response) as graphql:
			sync_changed_bin_inventory(self.store.name, force=True)
			graphql.assert_called_once()
		self.assertEqual(self._pending(), 1)
		self.store.reload()
		self.assertEqual(self.store.last_inventory_sync, cursor)

	def test_pending_mapping_is_selected_without_stock_or_timestamp_changes(self):
		frappe.db.delete("Bin", {"item_code": self.item.name})
		codes = _get_changed_inventory_item_codes(
			self.store,
			since=add_to_date(now_datetime(), minutes=-30),
			until=now_datetime(),
		)
		self.assertIn(self.item.name, codes)

	def test_disabled_mapping_or_item_is_not_selected(self):
		for doctype, name in (("Item Shopify Store", self.row_name), ("Item", self.item.name)):
			with self.subTest(doctype=doctype):
				fieldname = "enabled" if doctype == "Item Shopify Store" else "disabled"
				frappe.db.set_value(doctype, name, fieldname, 0 if fieldname == "enabled" else 1)
				codes = _get_changed_inventory_item_codes(
					self.store,
					since=add_to_date(now_datetime(), minutes=-30),
					until=now_datetime(),
				)
				self.assertNotIn(self.item.name, codes)
				frappe.db.set_value(doctype, name, fieldname, 1 if fieldname == "enabled" else 0)

	def test_full_sync_clears_pending_after_cache_backfill_and_all_locations(self):
		self._add_second_location()
		sync_store_inventory(self.store.name, force=True)
		self.assertEqual(self.shopify_set.call_count, 2)
		self.assertEqual(self._pending(), 0)
		self.assertEqual(
			frappe.db.get_value("Item Shopify Store", self.row_name, "shopify_inventory_item_id"),
			"inventory-pending-variant-1",
		)

	def test_targeted_sync_clears_only_the_selected_item(self):
		other = ensure_test_item(self.OTHER_ITEM_CODE)
		other_row = self._add_mapping(other.name, "pending-other-variant")
		stats = sync_items_inventory(self.store.name, [self.item.name], source="order_auto_link")
		self.assertEqual(stats["synced"], 1)
		self.assertEqual(self._pending(), 0)
		self.assertEqual(self._pending(other_row), 1)

	def test_single_item_sync_clears_pending(self):
		sync_single_item_inventory(self.item.name, self.store.name)
		self.assertEqual(self._pending(), 0)

	def test_all_sibling_variants_and_locations_must_be_acknowledged(self):
		second_row = self._add_mapping(self.item.name, "pending-variant-2")
		self._add_second_location()
		stats = sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(stats["synced"], 4)
		self.assertEqual(self._pending(), 0)
		self.assertEqual(self._pending(second_row), 0)

	def test_partial_location_failure_keeps_pending(self):
		self._add_second_location()

		def fail_second_location(quantities, *args, **kwargs):
			if quantities[0]["location_id"] == "pending-location-2":
				return BatchResult(failed=[(self.item.name, "Location unavailable")])
			return self._successful_batch(quantities)

		self.shopify_set.side_effect = fail_second_location
		stats = sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(stats["errors"], 1)
		self.assertEqual(self._pending(), 1)

	def test_full_and_single_sync_keep_pending_on_partial_location_failure(self):
		self._add_second_location()

		def fail_second_location(quantities, *args, **kwargs):
			if quantities[0]["location_id"] == "pending-location-2":
				return BatchResult(failed=[(self.item.name, "Location unavailable")])
			return self._successful_batch(quantities)

		self.shopify_set.side_effect = fail_second_location
		sync_store_inventory(self.store.name, force=True)
		self.assertEqual(self._pending(), 1)
		sync_single_item_inventory(self.item.name, self.store.name)
		self.assertEqual(self._pending(), 1)

	def test_one_skipped_variant_keeps_sibling_pending(self):
		second_row = self._add_mapping(self.item.name, "pending-variant-2")

		def skip_second_variant(variant_ids, **kwargs):
			result = self._fetch_inventory_ids(variant_ids)
			result["pending-variant-2"]["tracked"] = False
			return result

		self.shopify_fetch.side_effect = skip_second_variant
		stats = sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(stats["synced"], 1)
		self.assertEqual(stats["skipped"], 1)
		self.assertEqual(self._pending(), 1)
		self.assertEqual(self._pending(second_row), 1)

	def test_one_failed_variant_keeps_all_sibling_pending_rows(self):
		second_row = self._add_mapping(self.item.name, "pending-variant-2")
		self.shopify_set.side_effect = lambda quantities, *args, **kwargs: BatchResult(
			succeeded=[self.item.name], failed=[(self.item.name, "Variant unavailable")]
		)
		sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(self._pending(), 1)
		self.assertEqual(self._pending(second_row), 1)

	def test_missing_success_acknowledgement_keeps_pending(self):
		self.shopify_set.side_effect = lambda quantities, *args, **kwargs: BatchResult()
		sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(self._pending(), 1)

	def test_transport_error_keeps_pending(self):
		self.shopify_set.side_effect = ShopifyGraphQLError("Unauthorized", http_status=401)
		stats = sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(stats["errors"], 1)
		self.assertEqual(self._pending(), 1)

	def test_deleted_variant_with_empty_payload_keeps_pending(self):
		self.shopify_fetch.side_effect = lambda variant_ids, **kwargs: {}
		stats = sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(stats["synced"], 0)
		self.shopify_set.assert_not_called()
		self.assertEqual(self._pending(), 1)

	def test_changed_mapping_is_not_cleared_by_an_old_request(self):
		def change_mapping_during_request(quantities, *args, **kwargs):
			frappe.db.set_value("Item Shopify Store", self.row_name, "shopify_variant_id", "changed-variant")
			return self._successful_batch(quantities)

		self.shopify_set.side_effect = change_mapping_during_request
		sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(self._pending(), 1)

	def test_pending_remarked_during_request_is_not_cleared(self):
		def remark_pending_during_request(quantities, *args, **kwargs):
			frappe.db.set_value(
				"Item Shopify Store",
				self.row_name,
				{"modified": add_to_date(now_datetime(), seconds=1), "inventory_sync_pending": 1},
				update_modified=False,
			)
			return self._successful_batch(quantities)

		self.shopify_set.side_effect = remark_pending_during_request
		sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(self._pending(), 1)

	def test_new_pending_row_is_not_cleared_by_an_old_request(self):
		new_rows = []

		def add_mapping_during_request(quantities, *args, **kwargs):
			new_rows.append(self._add_mapping(self.item.name, "pending-variant-added-later"))
			return self._successful_batch(quantities)

		self.shopify_set.side_effect = add_mapping_during_request
		sync_items_inventory(self.store.name, [self.item.name])
		self.assertEqual(self._pending(), 0)
		self.assertEqual(self._pending(new_rows[0]), 1)

	def test_store_configuration_guards_keep_pending(self):
		for fieldname in ("enabled", "enable_inventory_sync"):
			with self.subTest(fieldname=fieldname):
				self._set_store_values(**{fieldname: 0})
				sync_items_inventory(self.store.name, [self.item.name])
				self.assertEqual(self._pending(), 1)
				self._set_store_values(**{fieldname: 1})
		self.shopify_set.assert_not_called()

	def test_store_config_kill_switch_keeps_pending(self):
		with patch.dict(frappe.conf, {"nexwave_shopify_disable_graphql_inventory_sync": [self.store.name]}):
			sync_items_inventory(self.store.name, [self.item.name])
		self.shopify_set.assert_not_called()
		self.assertEqual(self._pending(), 1)

	def test_missing_location_keeps_pending(self):
		frappe.db.delete("Shopify Store Warehouse Mapping", {"parent": self.store.name})
		sync_items_inventory(self.store.name, [self.item.name])
		self.shopify_set.assert_not_called()
		self.assertEqual(self._pending(), 1)

	def test_conflicting_inventory_owners_keep_pending(self):
		other = ensure_test_item(self.OTHER_ITEM_CODE)
		self._add_mapping(other.name, "pending-other-variant", shopify_inventory_item_id="shared-inventory")
		frappe.db.set_value(
			"Item Shopify Store", self.row_name, "shopify_inventory_item_id", "shared-inventory"
		)
		sync_items_inventory(self.store.name, [self.item.name])
		self.shopify_set.assert_not_called()
		self.assertEqual(self._pending(), 1)

	def test_changed_bins_retries_pending_even_after_cursor_advances(self):
		self._set_store_values(
			inventory_sync_mode="Changed Bins",
			last_inventory_sync=add_to_date(now_datetime(), minutes=-60),
		)
		frappe.db.delete("Bin", {"item_code": self.item.name})
		self.shopify_fetch.side_effect = lambda variant_ids, **kwargs: {}
		sync_changed_bin_inventory(self.store.name, force=True)
		self.assertEqual(self._pending(), 1)
		self.store.reload()
		self.assertIn(
			self.item.name,
			_get_changed_inventory_item_codes(self.store, self.store.last_inventory_sync, now_datetime()),
		)

	def _set_cached_id(self, inventory_id="stale-inventory"):
		frappe.db.set_value("Item Shopify Store", self.row_name, "shopify_inventory_item_id", inventory_id)

	def _invalid_response(self, index=0):
		return {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": None,
					"userErrors": [
						{
							"field": ["input", "quantities", str(index), "inventoryItemId"],
							"code": "INVALID_INVENTORY_ITEM",
							"message": "The specified inventory item could not be found",
						}
					],
				}
			}
		}

	def test_invalid_cache_is_repaired_once_across_locations(self):
		self._set_cached_id()
		self._add_second_location()
		with self._real_batch_with_graphql_response(self._noop_response()) as graphql:
			graphql.side_effect = [self._invalid_response(), self._noop_response(), self._noop_response()]
			sync_store_inventory(self.store.name, force=True)
			self.assertEqual(graphql.call_count, 3)
			requests = [call.args[1] for call in graphql.call_args_list]
			self.assertNotEqual(requests[0]["idempotencyKey"], requests[1]["idempotencyKey"])
			for request in requests[1:]:
				self.assertEqual(
					request["input"]["quantities"][0]["inventoryItemId"],
					"gid://shopify/InventoryItem/inventory-pending-variant-1",
				)
		self.shopify_fetch.assert_called_once()
		self.assertEqual(self._pending(), 0)
		self.assertEqual(
			frappe.db.get_value("Item Shopify Store", self.row_name, "shopify_inventory_item_id"),
			"inventory-pending-variant-1",
		)

	def test_missing_variant_does_not_block_healthy_unacknowledged_rows(self):
		self._set_cached_id()
		other = ensure_test_item(self.OTHER_ITEM_CODE)
		other_row = self._add_mapping(
			other.name, "healthy-variant", shopify_inventory_item_id="healthy-inventory"
		)
		self.shopify_fetch.side_effect = lambda *args, **kwargs: {
			"healthy-variant": {"tracked": True, "inventory_item_id": "healthy-inventory"}
		}
		requests = []

		def respond(query, variables):
			quantities = variables["input"]["quantities"]
			requests.append(quantities)
			for index, quantity in enumerate(quantities):
				if quantity["inventoryItemId"].endswith("/stale-inventory"):
					return self._invalid_response(index)
			return self._noop_response()

		with self._real_batch_with_graphql_response(None) as graphql:
			graphql.side_effect = respond
			sync_store_inventory(self.store.name, force=True)
		self.assertEqual([len(rows) for rows in requests], [2, 1])
		self.assertEqual(self._pending(), 1)
		self.assertEqual(self._pending(other_row), 0)
		self.assertIsNone(
			frappe.db.get_value("Item Shopify Store", self.row_name, "shopify_inventory_item_id")
		)
		self.store.reload()
		self.assertIsNone(self.store.last_inventory_sync)
		self.assertIsNotNone(self.store.last_inventory_sync_attempt)

	def test_second_invalid_result_stops_repair_and_keeps_pending(self):
		self._set_cached_id()
		self._add_second_location()
		with self._real_batch_with_graphql_response(self._invalid_response()) as graphql:
			sync_store_inventory(self.store.name, force=True)
			self.assertEqual(graphql.call_count, 2)
		self.shopify_fetch.assert_called_once()
		self.assertEqual(self._pending(), 1)

	def test_refresh_failure_preserves_cache_and_pending(self):
		self._set_cached_id()
		self.shopify_fetch.side_effect = ShopifyGraphQLError("Invalid lookup response", http_status=-1)
		with self._real_batch_with_graphql_response(self._invalid_response()) as graphql:
			sync_store_inventory(self.store.name, force=True)
			graphql.assert_called_once()
		self.assertEqual(
			frappe.db.get_value("Item Shopify Store", self.row_name, "shopify_inventory_item_id"),
			"stale-inventory",
		)
		self.assertEqual(self._pending(), 1)

	def test_mapping_changed_during_refresh_is_not_overwritten(self):
		self._set_cached_id()

		def change_mapping(variant_ids, **kwargs):
			frappe.db.set_value(
				"Item Shopify Store",
				self.row_name,
				{
					"shopify_variant_id": "replacement-variant",
					"shopify_inventory_item_id": "replacement-inventory",
				},
			)
			return self._fetch_inventory_ids(variant_ids)

		self.shopify_fetch.side_effect = change_mapping
		with self._real_batch_with_graphql_response(self._invalid_response()) as graphql:
			sync_store_inventory(self.store.name, force=True)
			graphql.assert_called_once()
		self.assertEqual(
			frappe.db.get_value("Item Shopify Store", self.row_name, "shopify_inventory_item_id"),
			"replacement-inventory",
		)
		self.assertEqual(self._pending(), 1)

	def test_refreshed_id_collision_with_unselected_item_is_not_sent(self):
		self._set_cached_id()
		other = ensure_test_item(self.OTHER_ITEM_CODE)
		self._add_mapping(
			other.name, "healthy-variant", shopify_inventory_item_id="inventory-pending-variant-1"
		)
		with self._real_batch_with_graphql_response(self._invalid_response()) as graphql:
			stats = sync_items_inventory(self.store.name, [self.item.name], source="order_auto_link")
			graphql.assert_called_once()
		self.assertEqual(stats["synced"], 0)
		self.assertEqual(stats["errors"], 1)
		self.assertEqual(self._pending(), 1)

	def test_failed_attempt_waits_for_interval_without_advancing_cursor(self):
		from nexwave_shopify_connector.nexwave_shopify.inventory import _should_sync_inventory

		self._set_store_values(inventory_sync_frequency=30)
		self._set_cached_id()
		with self._real_batch_with_graphql_response({"data": {}}):
			sync_store_inventory(self.store.name)
		self.store.reload()
		attempt = self.store.last_inventory_sync_attempt
		self.assertIsNotNone(attempt)
		self.assertIsNone(self.store.last_inventory_sync)
		for minutes, expected in ((10, False), (20, False), (30, True)):
			with patch(
				"nexwave_shopify_connector.nexwave_shopify.inventory.now_datetime",
				return_value=add_to_date(attempt, minutes=minutes),
			):
				self.assertEqual(_should_sync_inventory(self.store), expected)

	def test_full_sync_cursor_uses_start_time(self):
		started = now_datetime()
		later = add_to_date(started, minutes=5)
		with patch(
			"nexwave_shopify_connector.nexwave_shopify.inventory.now_datetime", side_effect=[started, later]
		):
			sync_store_inventory(self.store.name, force=True)
		self.store.reload()
		self.assertEqual(self.store.last_inventory_sync, started)
		self.assertEqual(self.store.last_inventory_sync_attempt, started)

	def test_targeted_sync_does_not_postpone_scheduled_run(self):
		attempt = add_to_date(now_datetime(), minutes=-60)
		self._set_store_values(last_inventory_sync_attempt=attempt)
		sync_items_inventory(self.store.name, [self.item.name], source="order_auto_link")
		self.store.reload()
		self.assertEqual(self.store.last_inventory_sync_attempt, attempt)

	def test_one_reported_invalid_target_refreshes_all_unacknowledged_targets(self):
		self._set_cached_id()
		other = ensure_test_item(self.OTHER_ITEM_CODE)
		other_row = self._add_mapping(
			other.name, "second-variant", shopify_inventory_item_id="second-stale-inventory"
		)
		self._add_second_location()
		requests = []

		def report_first_invalid(query, variables):
			quantities = variables["input"]["quantities"]
			requests.append(variables)
			for index, quantity in enumerate(quantities):
				if quantity["inventoryItemId"].endswith("stale-inventory"):
					return self._invalid_response(index)
			return self._noop_response()

		with self._real_batch_with_graphql_response(None) as graphql:
			graphql.side_effect = report_first_invalid
			sync_store_inventory(self.store.name, force=True)
			self.assertEqual(graphql.call_count, 3)
		self.shopify_fetch.assert_called_once()
		self.assertEqual(set(self.shopify_fetch.call_args.args[0]), {"pending-variant-1", "second-variant"})
		for request in requests[1:]:
			self.assertEqual(
				{row["inventoryItemId"] for row in request["input"]["quantities"]},
				{
					"gid://shopify/InventoryItem/inventory-pending-variant-1",
					"gid://shopify/InventoryItem/inventory-second-variant",
				},
			)
		self.assertNotEqual(requests[0]["idempotencyKey"], requests[1]["idempotencyKey"])
		self.assertEqual(self._pending(), 0)
		self.assertEqual(self._pending(other_row), 0)

	def test_concurrently_disabled_mapping_with_unchanged_cache_is_not_retried(self):
		from nexwave_shopify_connector.nexwave_shopify import inventory

		self._set_cached_id("healthy-inventory")
		other = ensure_test_item(self.OTHER_ITEM_CODE)
		self._add_mapping(other.name, "second-variant", shopify_inventory_item_id="stale-inventory")
		original_isolation = frappe.db.sql("SELECT @@tx_isolation")[0][0]
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- publish synthetic mappings for the second connection
		frappe.db.sql("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
		cache_results = []
		requests = []
		original_cache = inventory._cache_inventory_item_id

		def disable_during_lookup(variant_ids, **kwargs):
			self.assertEqual(set(variant_ids), {"pending-variant-1", "second-variant"})
			self.assertEqual(frappe.db.get_value("Item Shopify Store", self.row_name, "enabled"), 1)
			connection = frappe.db.get_connection()
			try:
				with connection.cursor() as cursor:
					cursor.execute(
						"UPDATE `tabItem Shopify Store` SET enabled = 0 WHERE name = %s", (self.row_name,)
					)
				connection.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- simulate a committed mapping change in a separate connection
			finally:
				connection.close()
			return {
				"pending-variant-1": {"tracked": True, "inventory_item_id": "healthy-inventory"},
				"second-variant": {"tracked": True, "inventory_item_id": "repaired-inventory"},
			}

		def check_cache_result(row, store_name, inventory_item_id, **kwargs):
			if row["item_shopify_store_name"] != self.row_name:
				return original_cache(row, store_name, inventory_item_id, **kwargs)
			# These are the post-update filters used by the old helper. The
			# unchanged cache ID still matches its earlier transaction snapshot.
			self.assertEqual(inventory_item_id, row["shopify_inventory_item_id"])
			self.assertTrue(
				frappe.db.exists(
					"Item Shopify Store",
					{
						"name": self.row_name,
						"parent": row["item_code"],
						"parenttype": "Item",
						"parentfield": "shopify_stores",
						"shopify_store": store_name,
						"enabled": 1,
						"shopify_variant_id": row["shopify_variant_id"],
						"shopify_inventory_item_id": inventory_item_id,
						"modified": row["mapping_modified"],
					},
				)
			)
			try:
				result = original_cache(row, store_name, inventory_item_id, **kwargs)
			except frappe.QueryDeadlockError:
				# MariaDB 11.8 can reject the current read with error 1020.
				# This must stop the run before any target is retried.
				cache_results.append("QueryDeadlockError")
				raise
			cache_results.append(result)
			return result

		def respond(query, variables):
			quantities = variables["input"]["quantities"]
			requests.append(quantities)
			for index, quantity in enumerate(quantities):
				if quantity["inventoryItemId"].endswith("/stale-inventory"):
					return self._invalid_response(index)
			return self._noop_response()

		self.shopify_fetch.side_effect = disable_during_lookup
		try:
			with self._real_batch_with_graphql_response(None) as graphql:
				graphql.side_effect = respond
				with patch.object(inventory, "_cache_inventory_item_id", side_effect=check_cache_result):
					sync_store_inventory(self.store.name, force=True)
			self.shopify_fetch.assert_called_once()
			self.assertIn(cache_results, ([False], ["QueryDeadlockError"]))
			for quantities in requests[1:]:
				self.assertNotIn(
					"gid://shopify/InventoryItem/healthy-inventory",
					{q["inventoryItemId"] for q in quantities},
				)
			if cache_results == ["QueryDeadlockError"]:
				self.assertEqual(len(requests), 1)
			self.assertEqual(self._pending(), 1)
		finally:
			frappe.db.rollback()
			commands = {
				"READ-UNCOMMITTED": "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
				"READ-COMMITTED": "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
				"REPEATABLE-READ": "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
				"SERIALIZABLE": "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE",
			}
			frappe.db.sql(commands[original_isolation])
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- finish the synthetic isolation change before fixture cleanup
		self.assertEqual(frappe.db.get_value("Item Shopify Store", self.row_name, "enabled"), 0)

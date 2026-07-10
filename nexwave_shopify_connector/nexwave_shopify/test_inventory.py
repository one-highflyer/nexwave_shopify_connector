# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""Integration tests for sync_store_inventory and sync_single_item_inventory.

These tests patch ``set_inventory_batch``, ``fetch_inventory_item_ids`` and
``Session`` inside inventory.py so no real Shopify HTTP traffic is made. The
goal is to verify:
  - batch assembly (one call per location per chunk)
  - lazy backfill (cache write, skipped variants)
  - disabled store / bench config early returns
  - zero-qty items are included; negative qty is clamped
  - last_inventory_sync is updated on completion and partial success only
  - partial batch failures produce a Warning log
  - force=True bypasses the sync frequency check
  - store-level exceptions are logged without re-raising
  - _execute_batch_with_retry handles 429, 5xx, network, and non-retryable
  - sync_single_item_inventory happy path and no-store-row skip
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from nexwave_shopify_connector.nexwave_shopify._inventory_test_fixtures import (
	TEST_COMPANY,
	TEST_WAREHOUSE,
	ensure_item_shopify_store_row,
	ensure_test_item,
	ensure_test_shopify_store,
	set_bin_qty,
)
from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
	BatchResult,
	ShopifyGraphQLError,
	ThrottleStatus,
)


@contextmanager
def _noop_session(*args, **kwargs):
	"""Replacement for Session.temp used in patches."""
	yield


def _append_item_shopify_store_row(
	item_code,
	store_name,
	shopify_product_id,
	shopify_variant_id,
	shopify_inventory_item_id,
):
	item = frappe.get_doc("Item", item_code)
	row = item.append(
		"shopify_stores",
		{
			"shopify_store": store_name,
			"shopify_product_id": shopify_product_id,
			"shopify_variant_id": shopify_variant_id,
			"shopify_sku": item_code,
			"shopify_inventory_item_id": shopify_inventory_item_id,
			"enabled": 1,
		},
	)
	row.db_insert()
	return row.name


class TestSyncStoreInventory(FrappeTestCase):
	@classmethod
	def _ensure_unmapped_warehouse(cls):
		warehouse_name = "_Test Unmapped Shopify Warehouse"
		warehouse_id = f"{warehouse_name} - _TC"
		if frappe.db.exists("Warehouse", warehouse_id):
			return warehouse_id

		warehouse = frappe.new_doc("Warehouse")
		warehouse.warehouse_name = warehouse_name
		warehouse.company = TEST_COMPANY
		if frappe.db.exists("Warehouse", "_Test Warehouse Group - _TC"):
			warehouse.parent_warehouse = "_Test Warehouse Group - _TC"
		warehouse.flags.ignore_mandatory = True
		warehouse.insert(ignore_permissions=True)
		return warehouse.name

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.store = ensure_test_shopify_store()
		cls.item_a = ensure_test_item("_Test Shop Item A")
		cls.item_b = ensure_test_item("_Test Shop Item B")
		cls.item_c = ensure_test_item("_Test Shop Item C")
		cls.unmapped_warehouse = cls._ensure_unmapped_warehouse()
		ensure_item_shopify_store_row(
			cls.item_a.name,
			cls.store.name,
			shopify_product_id="100",
			shopify_variant_id="200",
			shopify_inventory_item_id="1001",
		)
		ensure_item_shopify_store_row(
			cls.item_b.name,
			cls.store.name,
			shopify_product_id="101",
			shopify_variant_id="201",
			shopify_inventory_item_id="1002",
		)
		set_bin_qty(cls.item_a.name, TEST_WAREHOUSE, 10)
		set_bin_qty(cls.item_b.name, TEST_WAREHOUSE, 0)
		frappe.db.delete("Bin", {"item_code": cls.item_c.name})
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture persistence

	@classmethod
	def tearDownClass(cls):
		# setUpClass commits fixtures so the framework's auto-rollback does
		# not clean them up. Delete them explicitly so reruns are idempotent
		# and the test site is not polluted. Order matters: Bin and Stock
		# Ledger Entry before Item; Item before Shopify Store.
		for item_code in (cls.item_a.name, cls.item_b.name, cls.item_c.name):
			frappe.db.delete("Bin", {"item_code": item_code})
			frappe.db.delete("Stock Ledger Entry", {"item_code": item_code})
			if frappe.db.exists("Item", item_code):
				frappe.delete_doc("Item", item_code, force=True, ignore_missing=True)
		if frappe.db.exists("Shopify Store", cls.store.name):
			frappe.delete_doc("Shopify Store", cls.store.name, force=True, ignore_missing=True)
		if frappe.db.exists("Warehouse", cls.unmapped_warehouse):
			frappe.delete_doc("Warehouse", cls.unmapped_warehouse, force=True, ignore_missing=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture cleanup
		super().tearDownClass()

	def setUp(self):
		# Clear NexWave Shopify Log rows from prior tests so status
		# assertions in this run can't be satisfied by stale logs.
		frappe.db.delete("NexWave Shopify Log", {"shopify_store": self.store.name})
		# Reset last_inventory_sync, sync mode, and cached ids so each test starts fresh
		frappe.db.set_value(
			"Shopify Store",
			self.store.name,
			{
				"last_inventory_sync": None,
				"inventory_sync_mode": "Full Inventory",
			},
			update_modified=False,
		)
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = %s, last_sync_at = NULL
			WHERE parent = %s AND shopify_store = %s
			""",
			("1001", self.item_a.name, self.store.name),
		)
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = %s, last_sync_at = NULL
			WHERE parent = %s AND shopify_store = %s
			""",
			("1002", self.item_b.name, self.store.name),
		)
		frappe.db.delete(
			"Item Shopify Store",
			{"parent": self.item_c.name, "shopify_store": self.store.name},
		)
		set_bin_qty(self.item_a.name, TEST_WAREHOUSE, 10)
		set_bin_qty(self.item_b.name, TEST_WAREHOUSE, 0)
		frappe.db.delete("Bin", {"item_code": self.item_c.name})
		frappe.db.delete("Bin", {"item_code": self.item_b.name, "warehouse": self.unmapped_warehouse})
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- ensure test isolation

	def _set_last_inventory_sync(self, sync_time):
		frappe.db.set_value(
			"Shopify Store",
			self.store.name,
			"last_inventory_sync",
			sync_time,
			update_modified=False,
		)

	def _set_bin_modified(self, item_code, warehouse, modified_at):
		frappe.db.sql(
			"""
			UPDATE `tabBin`
			SET modified = %s
			WHERE item_code = %s AND warehouse = %s
			""",
			(modified_at, item_code, warehouse),
		)

	def _set_mapping_last_sync(self, item_code, sync_time):
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET last_sync_at = %s
			WHERE parent = %s AND shopify_store = %s
			""",
			(sync_time, item_code, self.store.name),
		)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_happy_path_sends_both_items(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name, self.item_b.name],
			failed=[],
			throttle=ThrottleStatus(),
		)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)

		mock_set.assert_called_once()
		quantities = mock_set.call_args[0][0]
		self.assertEqual(len(quantities), 2)
		self.assertEqual(mock_set.call_args.kwargs["api_version"], "2026-01")

		# Assert last_inventory_sync was updated
		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertIsNotNone(last_sync)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_zero_progress_skip_reports_warning_not_success(self, mock_set, mock_fetch, mock_session):
		"""If every item is skipped (e.g. all untracked), status must be Warning.

		Regression: previously total_sync=0 + total_error=0 reported as
		Success and bumped last_inventory_sync, masking config drift where
		every item had tracking disabled or variants were mass-deleted.
		"""
		mock_session.temp = _noop_session
		# Force both items to go through backfill with tracked=False
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = ''
			WHERE parent IN (%s, %s) AND shopify_store = %s
			""",
			(self.item_a.name, self.item_b.name, self.store.name),
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {
				"200": {"inventory_item_id": "9999", "tracked": False},
				"201": {"inventory_item_id": "8888", "tracked": False},
			}
			mock_set.return_value = BatchResult(succeeded=[], failed=[], throttle=ThrottleStatus())

			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			# set_inventory_batch should NOT be called (nothing to sync)
			mock_set.assert_not_called()

			# Summary log must be Warning, not Success
			warning_logs = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"method": "sync_store_inventory",
					"reference_name": self.store.name,
					"status": "Warning",
				},
				order_by="creation desc",
				limit=1,
			)
			self.assertTrue(warning_logs, "Zero-progress run should log as Warning")

			# last_inventory_sync must NOT be updated on a zero-progress run
			last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
			self.assertIsNone(last_sync, "Zero-progress run must not bump last_inventory_sync")
		finally:
			# Restore cache for other tests
			frappe.db.sql(
				"""
				UPDATE `tabItem Shopify Store`
				SET shopify_inventory_item_id = CASE parent
					WHEN %s THEN '1001'
					WHEN %s THEN '1002'
				END
				WHERE parent IN (%s, %s) AND shopify_store = %s
				""",
				(
					self.item_a.name,
					self.item_b.name,
					self.item_a.name,
					self.item_b.name,
					self.store.name,
				),
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_partial_batch_failure_logs_errors_warning_status(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name],
			failed=[(self.item_b.name, "Inventory item not found")],
			throttle=ThrottleStatus(),
		)
		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)
		# Summary log should exist with "Warning" status
		logs = frappe.get_all(
			"NexWave Shopify Log",
			filters={
				"shopify_store": self.store.name,
				"method": "sync_store_inventory",
				"status": "Warning",
			},
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(logs)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_lazy_backfill_populates_cache(self, mock_set, mock_fetch, mock_session):
		mock_session.temp = _noop_session
		# Clear cached id on item_a so backfill must happen
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = ''
			WHERE parent = %s AND shopify_store = %s
			""",
			(self.item_a.name, self.store.name),
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		mock_fetch.return_value = {
			"200": {"inventory_item_id": "9999", "tracked": True},
		}
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name, self.item_b.name],
			failed=[],
			throttle=ThrottleStatus(),
		)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)

		mock_fetch.assert_called_once()
		# Verify cached back to DB
		cached = frappe.db.get_value(
			"Item Shopify Store",
			{"parent": self.item_a.name, "shopify_store": self.store.name},
			"shopify_inventory_item_id",
		)
		self.assertEqual(cached, "9999")

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_conflicted_duplicate_mapping_cache_rechecked_by_variant(
		self, mock_set, mock_fetch, mock_session
	):
		mock_session.temp = _noop_session
		original_row_name = frappe.db.get_value(
			"Item Shopify Store",
			{
				"parent": self.item_a.name,
				"shopify_store": self.store.name,
				"shopify_variant_id": "200",
			},
			"name",
		)
		duplicate_row_name = _append_item_shopify_store_row(
			self.item_a.name,
			self.store.name,
			shopify_product_id="102",
			shopify_variant_id="202",
			shopify_inventory_item_id="1001",
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {
				"200": {"inventory_item_id": "1001", "tracked": True},
				"202": {"inventory_item_id": "1003", "tracked": True},
			}
			mock_set.return_value = BatchResult(
				succeeded=[self.item_a.name, self.item_a.name, self.item_b.name],
				failed=[],
				throttle=ThrottleStatus(),
			)

			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			mock_fetch.assert_called_once()
			self.assertEqual(set(mock_fetch.call_args[0][0]), {"200", "202"})
			quantities = mock_set.call_args[0][0]
			item_a_quantities = [q for q in quantities if q["item_code"] == self.item_a.name]
			self.assertEqual(len(item_a_quantities), 2)
			item_a_inventory_ids = {
				q["inventory_item_id"] for q in quantities if q["item_code"] == self.item_a.name
			}
			self.assertEqual(item_a_inventory_ids, {"1001", "1003"})
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					original_row_name,
					"shopify_inventory_item_id",
				),
				"1001",
			)
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					duplicate_row_name,
					"shopify_inventory_item_id",
				),
				"1003",
			)
		finally:
			frappe.db.delete("Item Shopify Store", {"name": duplicate_row_name})
			frappe.db.set_value(
				"Item Shopify Store",
				original_row_name,
				"shopify_inventory_item_id",
				"1001",
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_duplicate_mapping_rows_same_inventory_item_sent_once(self, mock_set, mock_fetch, mock_session):
		mock_session.temp = _noop_session
		original_row_name = frappe.db.get_value(
			"Item Shopify Store",
			{
				"parent": self.item_a.name,
				"shopify_store": self.store.name,
				"shopify_variant_id": "200",
			},
			"name",
		)
		frappe.db.set_value(
			"Item Shopify Store",
			original_row_name,
			"shopify_inventory_item_id",
			"1999",
			update_modified=False,
		)
		duplicate_row_name = _append_item_shopify_store_row(
			self.item_a.name,
			self.store.name,
			shopify_product_id="102",
			shopify_variant_id="202",
			shopify_inventory_item_id="",
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {
				"200": {"inventory_item_id": "1001", "tracked": True},
				"202": {"inventory_item_id": "1001", "tracked": True},
			}
			mock_set.return_value = BatchResult(
				succeeded=[self.item_a.name, self.item_b.name],
				failed=[],
				throttle=ThrottleStatus(),
			)

			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			mock_fetch.assert_called_once()
			self.assertEqual(set(mock_fetch.call_args[0][0]), {"200", "202"})
			quantities = mock_set.call_args[0][0]
			item_a_quantities = [q for q in quantities if q["item_code"] == self.item_a.name]
			self.assertEqual(len(item_a_quantities), 1)
			self.assertEqual(item_a_quantities[0]["inventory_item_id"], "1001")
			self.assertEqual(len(quantities), 2)
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					original_row_name,
					"shopify_inventory_item_id",
				),
				"1001",
			)
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					duplicate_row_name,
					"shopify_inventory_item_id",
				),
				"1001",
			)
		finally:
			frappe.db.delete("Item Shopify Store", {"name": duplicate_row_name})
			frappe.db.set_value(
				"Item Shopify Store",
				original_row_name,
				"shopify_inventory_item_id",
				"1001",
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_different_items_same_inventory_target_are_omitted_and_reported(
		self, mock_set, mock_fetch, mock_session
	):
		mock_session.temp = _noop_session
		set_bin_qty(self.item_a.name, TEST_WAREHOUSE, 10)
		set_bin_qty(self.item_b.name, TEST_WAREHOUSE, 0)
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = ''
			WHERE parent IN (%s, %s) AND shopify_store = %s
			""",
			(self.item_a.name, self.item_b.name, self.store.name),
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {
				"200": {"inventory_item_id": "1001", "tracked": True},
				"201": {"inventory_item_id": "1001", "tracked": True},
			}
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			mock_fetch.assert_called_once()
			self.assertEqual(set(mock_fetch.call_args[0][0]), {"200", "201"})
			mock_set.assert_not_called()
			conflict_logs = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"status": "Error",
					"method": "sync_store_inventory",
					"reference_doctype": "Item",
				},
				fields=["message", "traceback", "reference_name"],
			)
			conflict_logs = [
				log
				for log in conflict_logs
				if log.get("message", "").startswith("Inventory preparation conflict")
			]
			self.assertEqual(
				{log.reference_name for log in conflict_logs},
				{self.item_a.name, self.item_b.name},
			)
			for log in conflict_logs:
				self.assertIn("Multiple source items target Shopify inventory item 1001", log.traceback)

			summary = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"status": "Error",
					"method": "sync_store_inventory",
					"reference_name": self.store.name,
				},
				fields=["message"],
				order_by="creation desc",
				limit=1,
			)
			self.assertTrue(summary)
			self.assertIn("0 synced, 0 skipped, 2 errors", summary[0].message)
			self.assertIsNone(frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync"))
		finally:
			frappe.db.sql(
				"""
				UPDATE `tabItem Shopify Store`
				SET shopify_inventory_item_id = CASE parent
					WHEN %s THEN '1001'
					WHEN %s THEN '1002'
				END
				WHERE parent IN (%s, %s) AND shopify_store = %s
				""",
				(
					self.item_a.name,
					self.item_b.name,
					self.item_a.name,
					self.item_b.name,
					self.store.name,
				),
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_lazy_backfill_skips_deleted_variants(self, mock_set, mock_fetch, mock_session):
		mock_session.temp = _noop_session
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = ''
			WHERE parent = %s AND shopify_store = %s
			""",
			(self.item_a.name, self.store.name),
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		mock_fetch.return_value = {}  # variant missing from response
		mock_set.return_value = BatchResult(
			succeeded=[self.item_b.name], failed=[], throttle=ThrottleStatus()
		)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)

		# item_a should NOT be in the quantities list
		quantities = mock_set.call_args[0][0]
		item_codes = [q["item_code"] for q in quantities]
		self.assertNotIn(self.item_a.name, item_codes)
		self.assertIn(self.item_b.name, item_codes)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_lazy_backfill_failure_reported_as_error(self, mock_set, mock_fetch, mock_session):
		"""Backfill API failures must be counted as errors, not silent skips.

		Regression for the bug where a GraphQL schema error in the nodes query
		caused all items to be marked "skipped" and the sync summary reported
		status=Success with 0 errors, hiding the real problem.
		"""
		mock_session.temp = _noop_session
		# Force both items to need backfill
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = ''
			WHERE parent IN (%s, %s) AND shopify_store = %s
			""",
			(self.item_a.name, self.item_b.name, self.store.name),
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.side_effect = ShopifyGraphQLError("schema error", http_status=None)
			mock_set.return_value = BatchResult(succeeded=[], failed=[], throttle=ThrottleStatus())

			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			# set_inventory_batch should not be called at all; no items resolved
			mock_set.assert_not_called()

			# An Error log must exist for the backfill failure (not a Success summary)
			error_logs = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"status": "Error",
					"method": "sync_store_inventory",
				},
				fields=["message"],
				order_by="creation desc",
			)
			backfill_errors = [
				log for log in error_logs if log.get("message", "").startswith("Backfill failed")
			]
			self.assertTrue(
				backfill_errors,
				"Backfill API failure should create Error logs, not silent skips",
			)

			# last_inventory_sync must NOT be updated (H2 regression check)
			last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
			self.assertIsNone(last_sync)
		finally:
			# Restore cache for other tests
			frappe.db.sql(
				"""
				UPDATE `tabItem Shopify Store`
				SET shopify_inventory_item_id = CASE parent
					WHEN %s THEN '1001'
					WHEN %s THEN '1002'
				END
				WHERE parent IN (%s, %s) AND shopify_store = %s
				""",
				(self.item_a.name, self.item_b.name, self.item_a.name, self.item_b.name, self.store.name),
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_disabled_store_early_return(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		frappe.db.set_value("Shopify Store", self.store.name, "enabled", 0)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			mock_set.assert_not_called()
		finally:
			# Restore
			frappe.db.set_value("Shopify Store", self.store.name, "enabled", 1)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_bench_config_bailout_skips_store(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		# Preserve existing conf keys while adding the bailout for this store
		conf_override = frappe._dict(frappe.conf)
		conf_override["nexwave_shopify_disable_graphql_inventory_sync"] = [self.store.name]
		with patch.object(frappe, "conf", conf_override):
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)
		mock_set.assert_not_called()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_zero_qty_included(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name, self.item_b.name],
			failed=[],
			throttle=ThrottleStatus(),
		)
		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)
		quantities = mock_set.call_args[0][0]
		# Find item_b (qty=0)
		item_b_q = next(q for q in quantities if q["item_code"] == self.item_b.name)
		self.assertEqual(item_b_q["qty"], 0)

	# --- C2: last_inventory_sync update semantics ---

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_all_batches_fail_does_not_update_last_sync(self, mock_set, mock_session, mock_sleep):
		"""H2 regression: if every batch fails, last_inventory_sync stays unchanged."""
		mock_session.temp = _noop_session
		mock_set.side_effect = ShopifyGraphQLError("server dead", http_status=503)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)

		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertIsNone(last_sync, "last_inventory_sync should NOT be updated when all batches fail")

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_partial_success_updates_last_sync(self, mock_set, mock_session):
		"""Partial success (some items synced) should still update last_inventory_sync."""
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name],
			failed=[(self.item_b.name, "Inventory item not found")],
			throttle=ThrottleStatus(),
		)
		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name)

		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertIsNotNone(last_sync, "last_inventory_sync should be updated on partial success")

	# --- C3: force=True / force=False behaviour ---

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_force_false_skips_when_recently_synced(self, mock_set, mock_session):
		"""With force=False and a recent last_inventory_sync, sync should no-op."""
		mock_session.temp = _noop_session
		frappe.db.set_value("Shopify Store", self.store.name, "last_inventory_sync", now_datetime())
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name, force=False)

		mock_set.assert_not_called()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_force_true_bypasses_frequency_check(self, mock_set, mock_session):
		"""With force=True, the frequency guard must be bypassed even if recently synced."""
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name, self.item_b.name],
			failed=[],
			throttle=ThrottleStatus(),
		)
		frappe.db.set_value("Shopify Store", self.store.name, "last_inventory_sync", now_datetime())
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		sync_store_inventory(self.store.name, force=True)

		mock_set.assert_called_once()

	# --- C3b: scheduled inventory mode routing ---

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.frappe.enqueue")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.frappe.get_all")
	def test_scheduler_keeps_full_inventory_mode_on_full_sync_method(self, mock_get_all, mock_enqueue):
		"""Full Inventory mode should keep queuing the existing full sync method."""
		mock_get_all.return_value = [self.store.name]

		from nexwave_shopify_connector.nexwave_shopify.inventory import (
			INVENTORY_SYNC_METHOD_FULL,
			update_inventory_on_shopify,
		)

		update_inventory_on_shopify()

		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args[0][0], INVENTORY_SYNC_METHOD_FULL)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.frappe.enqueue")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.frappe.get_all")
	def test_scheduler_routes_changed_bins_mode(self, mock_get_all, mock_enqueue):
		"""Changed Bins mode should queue the incremental sync method."""
		mock_get_all.return_value = [self.store.name]
		frappe.db.set_value(
			"Shopify Store",
			self.store.name,
			"inventory_sync_mode",
			"Changed Bins",
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		from nexwave_shopify_connector.nexwave_shopify.inventory import (
			INVENTORY_SYNC_METHOD_CHANGED_BINS,
			update_inventory_on_shopify,
		)

		update_inventory_on_shopify()

		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args[0][0], INVENTORY_SYNC_METHOD_CHANGED_BINS)

	# --- C3c: changed Bin incremental sync ---

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_changed_bin_sync_sends_only_mapped_bin_changes(self, mock_set, mock_session):
		"""Incremental sync should include changed Bins in mapped warehouses only."""
		mock_session.temp = _noop_session
		old_sync = add_to_date(now_datetime(), minutes=-30).replace(microsecond=0)
		before_cursor = add_to_date(old_sync, minutes=-1)
		changed_at = add_to_date(now_datetime(), minutes=-1)
		self._set_last_inventory_sync(old_sync)
		self._set_bin_modified(self.item_a.name, TEST_WAREHOUSE, changed_at)
		self._set_bin_modified(self.item_b.name, TEST_WAREHOUSE, before_cursor)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name], failed=[], throttle=ThrottleStatus()
		)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

		sync_changed_bin_inventory(self.store.name, force=True)

		mock_set.assert_called_once()
		quantities = mock_set.call_args[0][0]
		self.assertEqual({q["item_code"] for q in quantities}, {self.item_a.name})
		self.assertEqual(mock_set.call_args.kwargs["api_version"], "2026-01")

		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertGreater(last_sync, old_sync)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_changed_item_summary_ignores_unselected_backfill_skip(self, mock_set, mock_fetch, mock_session):
		mock_session.temp = _noop_session
		frappe.db.set_value(
			"Item Shopify Store",
			{"parent": self.item_b.name, "shopify_store": self.store.name},
			"shopify_inventory_item_id",
			"",
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {}
			mock_set.return_value = BatchResult(
				succeeded=[self.item_a.name],
				failed=[],
				throttle=ThrottleStatus(),
			)
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_items_inventory

			stats = sync_items_inventory(self.store.name, [self.item_a.name])

			self.assertEqual(stats["synced"], 1)
			self.assertEqual(stats["skipped"], 0)
			self.assertEqual(stats["errors"], 0)
			quantities = mock_set.call_args[0][0]
			self.assertEqual({q["item_code"] for q in quantities}, {self.item_a.name})
		finally:
			frappe.db.set_value(
				"Item Shopify Store",
				{"parent": self.item_b.name, "shopify_store": self.store.name},
				"shopify_inventory_item_id",
				"1002",
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_changed_bin_sync_ignores_unmapped_warehouse_changes(self, mock_set, mock_session):
		"""Bin updates outside the store warehouse mapping should not be selected."""
		mock_session.temp = _noop_session
		old_sync = add_to_date(now_datetime(), minutes=-30).replace(microsecond=0)
		before_cursor = add_to_date(old_sync, minutes=-1)
		changed_at = add_to_date(now_datetime(), minutes=-1)
		self._set_last_inventory_sync(old_sync)
		self._set_bin_modified(self.item_a.name, TEST_WAREHOUSE, before_cursor)
		self._set_bin_modified(self.item_b.name, TEST_WAREHOUSE, before_cursor)
		set_bin_qty(self.item_b.name, self.unmapped_warehouse, 7)
		self._set_bin_modified(self.item_b.name, self.unmapped_warehouse, changed_at)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

		sync_changed_bin_inventory(self.store.name, force=True)

		mock_set.assert_not_called()
		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertGreater(last_sync, old_sync)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_recent_mapping_without_bin_syncs_zero_quantity(self, mock_set, mock_session):
		"""Recently synced mappings with no Bin row should still push quantity 0."""
		mock_session.temp = _noop_session
		old_sync = add_to_date(now_datetime(), minutes=-30).replace(microsecond=0)
		before_cursor = add_to_date(old_sync, minutes=-1)
		changed_at = add_to_date(now_datetime(), minutes=-1)
		self._set_last_inventory_sync(old_sync)
		self._set_bin_modified(self.item_a.name, TEST_WAREHOUSE, before_cursor)
		self._set_bin_modified(self.item_b.name, TEST_WAREHOUSE, before_cursor)
		ensure_item_shopify_store_row(
			self.item_c.name,
			self.store.name,
			shopify_product_id="102",
			shopify_variant_id="202",
			shopify_inventory_item_id="1003",
		)
		frappe.db.delete("Bin", {"item_code": self.item_c.name})
		self._set_mapping_last_sync(self.item_c.name, changed_at)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup
		mock_set.return_value = BatchResult(
			succeeded=[self.item_c.name], failed=[], throttle=ThrottleStatus()
		)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

		sync_changed_bin_inventory(self.store.name, force=True)

		mock_set.assert_called_once()
		quantities = mock_set.call_args[0][0]
		self.assertEqual(len(quantities), 1)
		self.assertEqual(quantities[0]["item_code"], self.item_c.name)
		self.assertEqual(quantities[0]["qty"], 0)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_changed_bin_noop_advances_cursor(self, mock_set, mock_session):
		"""A no-change incremental scan should advance last_inventory_sync."""
		mock_session.temp = _noop_session
		old_sync = add_to_date(now_datetime(), minutes=-30).replace(microsecond=0)
		cursor = add_to_date(now_datetime(), minutes=-1).replace(microsecond=0)
		self._set_last_inventory_sync(cursor)
		self._set_bin_modified(self.item_a.name, TEST_WAREHOUSE, old_sync)
		self._set_bin_modified(self.item_b.name, TEST_WAREHOUSE, old_sync)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

		sync_changed_bin_inventory(self.store.name, force=True)

		mock_set.assert_not_called()
		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertGreater(last_sync, cursor)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_changed_bin_conflicts_are_counted_without_advancing_cursor(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		old_sync = add_to_date(now_datetime(), minutes=-30).replace(microsecond=0)
		before_cursor = add_to_date(old_sync, minutes=-1)
		changed_at = add_to_date(now_datetime(), minutes=-1)
		self._set_last_inventory_sync(old_sync)
		self._set_bin_modified(self.item_a.name, TEST_WAREHOUSE, changed_at)
		self._set_bin_modified(self.item_b.name, TEST_WAREHOUSE, before_cursor)
		frappe.db.set_value(
			"Item Shopify Store",
			{"parent": self.item_b.name, "shopify_store": self.store.name},
			"shopify_inventory_item_id",
			"1001",
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

			sync_changed_bin_inventory(self.store.name, force=True)

			mock_set.assert_not_called()
			last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
			self.assertEqual(last_sync, old_sync)
			conflict_logs = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"status": "Error",
					"method": "sync_changed_bin_inventory",
					"reference_doctype": "Item",
				},
				fields=["message", "reference_name"],
			)
			conflict_logs = [
				log
				for log in conflict_logs
				if log.get("message", "").startswith("Inventory preparation conflict")
			]
			self.assertEqual(
				{log.reference_name for log in conflict_logs},
				{self.item_a.name, self.item_b.name},
			)
			summary = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"status": "Error",
					"method": "sync_changed_bin_inventory",
					"reference_name": self.store.name,
				},
				fields=["message"],
				order_by="creation desc",
				limit=1,
			)
			self.assertTrue(summary)
			self.assertIn("0 synced, 0 skipped, 2 errors", summary[0].message)
		finally:
			frappe.db.set_value(
				"Item Shopify Store",
				{"parent": self.item_b.name, "shopify_store": self.store.name},
				"shopify_inventory_item_id",
				"1002",
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_changed_bin_errors_do_not_advance_cursor(self, mock_set, mock_session):
		"""Incremental item errors should leave the cursor unchanged for retry."""
		mock_session.temp = _noop_session
		old_sync = add_to_date(now_datetime(), minutes=-30).replace(microsecond=0)
		changed_at = add_to_date(now_datetime(), minutes=-1)
		self._set_last_inventory_sync(old_sync)
		self._set_bin_modified(self.item_a.name, TEST_WAREHOUSE, changed_at)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup
		mock_set.return_value = BatchResult(
			succeeded=[],
			failed=[(self.item_a.name, "Inventory item not found")],
			throttle=ThrottleStatus(),
		)

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

		sync_changed_bin_inventory(self.store.name, force=True)

		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertEqual(last_sync, old_sync)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.sync_store_inventory")
	def test_changed_bin_empty_cursor_runs_full_baseline(self, mock_full_sync):
		"""An empty incremental cursor should run a forced full sync baseline."""
		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_changed_bin_inventory

		sync_changed_bin_inventory(self.store.name, force=True)

		mock_full_sync.assert_called_once_with(self.store.name, force=True)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.frappe.msgprint")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.frappe.enqueue")
	def test_manual_inventory_sync_always_queues_full_sync(self, mock_enqueue, mock_msgprint):
		"""Manual inventory sync should stay a forced full sync in every mode."""
		frappe.db.set_value(
			"Shopify Store",
			self.store.name,
			"inventory_sync_mode",
			"Changed Bins",
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		from nexwave_shopify_connector.nexwave_shopify.inventory import (
			INVENTORY_SYNC_METHOD_FULL,
			manual_inventory_sync,
		)

		manual_inventory_sync(self.store.name)

		mock_enqueue.assert_called_once()
		self.assertEqual(mock_enqueue.call_args[0][0], INVENTORY_SYNC_METHOD_FULL)
		self.assertTrue(mock_enqueue.call_args.kwargs["force"])

	# --- C4: store-level exception path ---

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	def test_session_exception_logs_store_level_error(self, mock_session):
		"""If Session.temp raises, the outer handler logs an error without re-raising."""

		@contextmanager
		def _raising_session_cm(*args, **kwargs):
			raise Exception("Token expired")
			yield  # unreachable

		mock_session.temp = _raising_session_cm

		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

		# Must not raise
		sync_store_inventory(self.store.name)

		logs = frappe.get_all(
			"NexWave Shopify Log",
			filters={
				"shopify_store": self.store.name,
				"status": "Error",
				"method": "sync_store_inventory",
				"reference_name": self.store.name,
			},
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(logs, "Store-level exception should create an Error log")

	# --- I6: negative qty clamping ---

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_negative_qty_clamped_to_zero(self, mock_set, mock_session):
		"""Bin.actual_qty < 0 (e.g. if Allow Negative Stock) should be sent as 0."""
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(
			succeeded=[self.item_a.name, self.item_b.name],
			failed=[],
			throttle=ThrottleStatus(),
		)
		# Set item_a's qty to -5
		set_bin_qty(self.item_a.name, TEST_WAREHOUSE, -5)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_store_inventory

			sync_store_inventory(self.store.name)

			quantities = mock_set.call_args[0][0]
			item_a_q = next(q for q in quantities if q["item_code"] == self.item_a.name)
			self.assertEqual(item_a_q["qty"], 0, "Negative qty should be clamped to 0")
		finally:
			# Restore original fixture state so later tests see qty=10
			set_bin_qty(self.item_a.name, TEST_WAREHOUSE, 10)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown


class TestInventoryApiVersion(FrappeTestCase):
	def test_old_missing_and_invalid_versions_are_clamped(self):
		from nexwave_shopify_connector.nexwave_shopify.inventory import _get_inventory_api_version

		for configured_version in (
			None,
			"",
			"2024-01",
			"2025-10",
			"invalid",
			"2026-00",
			"2026-1",
			"2026-02",
		):
			with self.subTest(configured_version=configured_version):
				self.assertEqual(_get_inventory_api_version(configured_version), "2026-01")

	def test_modern_and_unstable_versions_are_preserved(self):
		from nexwave_shopify_connector.nexwave_shopify.inventory import _get_inventory_api_version

		for configured_version in ("2026-01", "2026-04", "2026-07", "unstable"):
			with self.subTest(configured_version=configured_version):
				self.assertEqual(_get_inventory_api_version(configured_version), configured_version)

	def test_selected_release_is_registered_with_sdk(self):
		from shopify.api_version import ApiVersion

		from nexwave_shopify_connector.nexwave_shopify.inventory import _init_shopify_api_versions

		with patch.object(ApiVersion, "versions", {}):
			_init_shopify_api_versions("2026-07")

			self.assertIn("unstable", ApiVersion.versions)
			self.assertIn("2024-01", ApiVersion.versions)
			self.assertEqual(ApiVersion.versions["2026-07"].name, "2026-07")


class TestInventoryQuantityPreparation(FrappeTestCase):
	def test_duplicate_location_with_different_quantities_is_rejected(self):
		from nexwave_shopify_connector.nexwave_shopify.inventory import (
			_prepare_inventory_quantities,
		)

		location_mapping = [("loc1", "Warehouse A"), ("loc1", "Warehouse B")]
		items = [{"item_code": "ITEM-A", "shopify_inventory_item_id": "1001"}]
		target_owners = {"1001": {"ITEM-A"}}

		quantities_by_location, failures = _prepare_inventory_quantities(
			location_mapping,
			items,
			{("ITEM-A", "Warehouse A"): 4, ("ITEM-A", "Warehouse B"): 7},
			target_owners,
		)

		self.assertEqual(quantities_by_location, {})
		self.assertEqual(len(failures), 1)
		self.assertEqual(failures[0][0], "ITEM-A")
		self.assertIn("conflicting desired quantities", failures[0][1])

		quantities_by_location, failures = _prepare_inventory_quantities(
			location_mapping,
			items,
			{("ITEM-A", "Warehouse A"): 4, ("ITEM-A", "Warehouse B"): 4},
			target_owners,
		)

		self.assertEqual(failures, [])
		self.assertEqual(len(quantities_by_location["loc1"]), 1)
		self.assertEqual(quantities_by_location["loc1"][0]["qty"], 4)


class TestExecuteBatchWithRetry(FrappeTestCase):
	"""Unit tests for the retry wrapper around set_inventory_batch.

	These tests mock both ``set_inventory_batch`` and ``time.sleep`` so the
	retry schedule is exercised without any real delay or HTTP traffic.
	"""

	def _make_chunk(self):
		return [
			{
				"item_code": "ITEM-A",
				"inventory_item_id": "1001",
				"location_id": "loc1",
				"qty": 5,
			}
		]

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_429_retries_with_retry_after_sleep(self, mock_set, mock_sleep):
		"""On 429, the wrapper sleeps for retry_after and retries up to 2 times."""
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		rate_limit = ShopifyGraphQLError("rate limited", http_status=429, retry_after=2.5)
		success = BatchResult(succeeded=["ITEM-A"], failed=[], throttle=ThrottleStatus())
		mock_set.side_effect = [rate_limit, rate_limit, success]

		result = _execute_batch_with_retry(
			chunk=self._make_chunk(),
			store_name="test.myshopify.com",
			timestamp_iso="2026-04-10T10:00:00",
			logger=frappe.logger("test"),
		)

		self.assertEqual(result.succeeded, ["ITEM-A"])
		self.assertEqual(mock_set.call_count, 3)
		# Both retry sleeps should use retry_after value from the 429 response
		self.assertEqual(mock_sleep.call_count, 2)
		self.assertEqual(mock_sleep.call_args_list[0][0][0], 2.5)
		self.assertEqual(mock_sleep.call_args_list[1][0][0], 2.5)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.uuid.uuid4")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_idempotency_key_is_reused_for_retries_and_unique_per_batch(
		self, mock_set, mock_uuid, mock_sleep
	):
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		transient_error = ShopifyGraphQLError("server error", http_status=503)
		success = BatchResult(succeeded=["ITEM-A"], failed=[], throttle=ThrottleStatus())
		mock_uuid.side_effect = ["batch-key-one", "batch-key-two"]
		mock_set.side_effect = [transient_error, success, success]

		_execute_batch_with_retry(
			chunk=self._make_chunk(),
			store_name="test.myshopify.com",
			timestamp_iso="2026-04-10T10:00:00",
			logger=frappe.logger("test"),
			api_version="2026-04",
		)
		_execute_batch_with_retry(
			chunk=self._make_chunk(),
			store_name="test.myshopify.com",
			timestamp_iso="2026-04-10T10:00:01",
			logger=frappe.logger("test"),
			api_version="2026-04",
		)

		self.assertEqual(mock_uuid.call_count, 2)
		self.assertEqual(
			[call.kwargs["idempotency_key"] for call in mock_set.call_args_list],
			["batch-key-one", "batch-key-one", "batch-key-two"],
		)
		self.assertTrue(all(call.kwargs["api_version"] == "2026-04" for call in mock_set.call_args_list))
		mock_sleep.assert_called_once_with(2.0)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_5xx_exponential_backoff_then_raises(self, mock_set, mock_sleep):
		"""On repeated 5xx, the wrapper backs off with 2.0/4.0 then re-raises."""
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		mock_set.side_effect = ShopifyGraphQLError("server error", http_status=503)

		with self.assertRaises(ShopifyGraphQLError):
			_execute_batch_with_retry(
				chunk=self._make_chunk(),
				store_name="test.myshopify.com",
				timestamp_iso="2026-04-10T10:00:00",
				logger=frappe.logger("test"),
			)

		self.assertEqual(mock_set.call_count, 3)  # 1 initial + 2 retries
		self.assertEqual(mock_sleep.call_count, 2)
		self.assertEqual(
			[call.args[0] for call in mock_sleep.call_args_list],
			[2.0, 4.0],
		)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_non_retryable_4xx_raises_immediately(self, mock_set, mock_sleep):
		"""A non-retryable 4xx (e.g. 403 Forbidden) is re-raised without sleeping."""
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		mock_set.side_effect = ShopifyGraphQLError("forbidden", http_status=403)

		with self.assertRaises(ShopifyGraphQLError):
			_execute_batch_with_retry(
				chunk=self._make_chunk(),
				store_name="test.myshopify.com",
				timestamp_iso="2026-04-10T10:00:00",
				logger=frappe.logger("test"),
			)

		self.assertEqual(mock_set.call_count, 1)
		mock_sleep.assert_not_called()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_network_error_retries_then_succeeds(self, mock_set, mock_sleep):
		"""An HTTP-less error (http_status=None) is treated like 5xx: backoff + retry."""
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		network_error = ShopifyGraphQLError("connection reset", http_status=None)
		success = BatchResult(succeeded=["ITEM-A"], failed=[], throttle=ThrottleStatus())
		mock_set.side_effect = [network_error, network_error, success]

		result = _execute_batch_with_retry(
			chunk=self._make_chunk(),
			store_name="test.myshopify.com",
			timestamp_iso="2026-04-10T10:00:00",
			logger=frappe.logger("test"),
		)

		self.assertEqual(result.succeeded, ["ITEM-A"])
		self.assertEqual(mock_set.call_count, 3)
		self.assertEqual(mock_sleep.call_count, 2)
		self.assertEqual(
			[call.args[0] for call in mock_sleep.call_args_list],
			[2.0, 4.0],
		)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_429_without_retry_after_falls_back_to_backoff(self, mock_set, mock_sleep):
		"""A 429 with no Retry-After header must still retry (using backoff schedule).

		Regression: previously the wrapper required `e.retry_after` to be
		truthy before sleeping, so a 429 without the optional header would
		raise on the first attempt and kill 250 items per batch.
		"""
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		rate_limit_no_header = ShopifyGraphQLError(
			"rate limited, no Retry-After header", http_status=429, retry_after=None
		)
		success = BatchResult(succeeded=["ITEM-A"], failed=[], throttle=ThrottleStatus())
		mock_set.side_effect = [rate_limit_no_header, rate_limit_no_header, success]

		result = _execute_batch_with_retry(
			chunk=self._make_chunk(),
			store_name="test.myshopify.com",
			timestamp_iso="2026-04-10T10:00:00",
			logger=frappe.logger("test"),
		)

		self.assertEqual(result.succeeded, ["ITEM-A"])
		self.assertEqual(mock_set.call_count, 3)
		# Fall back to exponential backoff when Retry-After is absent
		self.assertEqual(
			[call.args[0] for call in mock_sleep.call_args_list],
			[2.0, 4.0],
		)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.time.sleep")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_schema_error_is_non_retryable(self, mock_set, mock_sleep):
		"""Top-level GraphQL errors (http_status=-1) must not be retried.

		Regression: previously top-level errors were raised with
		http_status=None, which the wrapper treated as a network error and
		retried with 2s + 4s backoff. Schema regressions should fail fast,
		not waste 6 seconds per batch and log as 'network error'.
		"""
		from nexwave_shopify_connector.nexwave_shopify.inventory import _execute_batch_with_retry

		schema_err = ShopifyGraphQLError("GraphQL errors: [{'message': 'Field X not found'}]", http_status=-1)
		mock_set.side_effect = schema_err

		with self.assertRaises(ShopifyGraphQLError):
			_execute_batch_with_retry(
				chunk=self._make_chunk(),
				store_name="test.myshopify.com",
				timestamp_iso="2026-04-10T10:00:00",
				logger=frappe.logger("test"),
			)

		# Should raise immediately on first attempt, no retries, no sleeps
		self.assertEqual(mock_set.call_count, 1)
		mock_sleep.assert_not_called()


class TestSyncSingleItemInventory(FrappeTestCase):
	"""Integration tests for sync_single_item_inventory."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.store = ensure_test_shopify_store()
		cls.item = ensure_test_item("_Test Shop Single Item")
		ensure_item_shopify_store_row(
			cls.item.name,
			cls.store.name,
			shopify_product_id="500",
			shopify_variant_id="600",
			shopify_inventory_item_id="7001",
		)
		set_bin_qty(cls.item.name, TEST_WAREHOUSE, 25)
		# Item without any Item Shopify Store row, to test the skip path.
		cls.item_no_store_row = ensure_test_item("_Test Shop Single Item No Store")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture persistence

	@classmethod
	def tearDownClass(cls):
		# Committed fixtures need explicit cleanup so reruns stay idempotent.
		for item_code in (cls.item.name, cls.item_no_store_row.name):
			frappe.db.delete("Bin", {"item_code": item_code})
			frappe.db.delete("Stock Ledger Entry", {"item_code": item_code})
			if frappe.db.exists("Item", item_code):
				frappe.delete_doc("Item", item_code, force=True, ignore_missing=True)
		if frappe.db.exists("Shopify Store", cls.store.name):
			frappe.delete_doc("Shopify Store", cls.store.name, force=True, ignore_missing=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture cleanup
		super().tearDownClass()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_single_item_happy_path(self, mock_set, mock_session):
		"""sync_single_item_inventory posts exactly one quantity entry for the item."""
		mock_session.temp = _noop_session
		mock_set.return_value = BatchResult(succeeded=[self.item.name], failed=[], throttle=ThrottleStatus())
		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_single_item_inventory

		sync_single_item_inventory(self.item.name, self.store.name)

		mock_set.assert_called_once()
		call_args = mock_set.call_args
		quantities = call_args[0][0] if call_args[0] else call_args.kwargs["quantities"]
		self.assertEqual(len(quantities), 1)
		self.assertEqual(quantities[0]["item_code"], self.item.name)
		self.assertEqual(call_args.kwargs["api_version"], "2026-01")

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_single_item_sync_handles_multiple_mapping_rows(self, mock_set, mock_fetch, mock_session):
		mock_session.temp = _noop_session
		original_row_name = frappe.db.get_value(
			"Item Shopify Store",
			{
				"parent": self.item.name,
				"shopify_store": self.store.name,
				"shopify_variant_id": "600",
			},
			"name",
		)
		duplicate_row_name = _append_item_shopify_store_row(
			self.item.name,
			self.store.name,
			shopify_product_id="501",
			shopify_variant_id="601",
			shopify_inventory_item_id="7001",
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {
				"600": {"inventory_item_id": "7001", "tracked": True},
				"601": {"inventory_item_id": "7002", "tracked": True},
			}
			mock_set.return_value = BatchResult(
				succeeded=[self.item.name, self.item.name],
				failed=[],
				throttle=ThrottleStatus(),
			)

			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_single_item_inventory

			sync_single_item_inventory(self.item.name, self.store.name)

			mock_fetch.assert_called_once()
			self.assertEqual(set(mock_fetch.call_args[0][0]), {"600", "601"})
			quantities = mock_set.call_args[0][0]
			item_quantities = [q for q in quantities if q["item_code"] == self.item.name]
			self.assertEqual(len(item_quantities), 2)
			item_inventory_ids = {
				q["inventory_item_id"] for q in quantities if q["item_code"] == self.item.name
			}
			self.assertEqual(item_inventory_ids, {"7001", "7002"})
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					original_row_name,
					"shopify_inventory_item_id",
				),
				"7001",
			)
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					duplicate_row_name,
					"shopify_inventory_item_id",
				),
				"7002",
			)
		finally:
			frappe.db.delete("Item Shopify Store", {"name": duplicate_row_name})
			frappe.db.set_value(
				"Item Shopify Store",
				original_row_name,
				"shopify_inventory_item_id",
				"7001",
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.fetch_inventory_item_ids")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_single_item_duplicate_variant_mapping_sent_once(self, mock_set, mock_fetch, mock_session):
		mock_session.temp = _noop_session
		original_row_name = frappe.db.get_value(
			"Item Shopify Store",
			{
				"parent": self.item.name,
				"shopify_store": self.store.name,
				"shopify_variant_id": "600",
			},
			"name",
		)
		frappe.db.set_value(
			"Item Shopify Store",
			original_row_name,
			"shopify_inventory_item_id",
			"7998",
			update_modified=False,
		)
		duplicate_row_name = _append_item_shopify_store_row(
			self.item.name,
			self.store.name,
			shopify_product_id="500",
			shopify_variant_id="600",
			shopify_inventory_item_id="7999",
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			mock_fetch.return_value = {
				"600": {"inventory_item_id": "7001", "tracked": True},
			}
			mock_set.return_value = BatchResult(
				succeeded=[self.item.name],
				failed=[],
				throttle=ThrottleStatus(),
			)

			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_single_item_inventory

			sync_single_item_inventory(self.item.name, self.store.name)

			mock_fetch.assert_called_once()
			self.assertEqual(set(mock_fetch.call_args[0][0]), {"600"})
			quantities = mock_set.call_args[0][0]
			self.assertEqual(len(quantities), 1)
			self.assertEqual(quantities[0]["inventory_item_id"], "7001")
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					original_row_name,
					"shopify_inventory_item_id",
				),
				"7001",
			)
			self.assertEqual(
				frappe.db.get_value(
					"Item Shopify Store",
					duplicate_row_name,
					"shopify_inventory_item_id",
				),
				"7001",
			)
		finally:
			frappe.db.delete("Item Shopify Store", {"name": duplicate_row_name})
			frappe.db.set_value(
				"Item Shopify Store",
				original_row_name,
				"shopify_inventory_item_id",
				"7001",
				update_modified=False,
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_single_item_conflict_includes_unselected_target_owner(self, mock_set, mock_session):
		mock_session.temp = _noop_session
		frappe.db.delete(
			"NexWave Shopify Log",
			{"shopify_store": self.store.name, "method": "sync_single_item_inventory"},
		)
		other_row_name = _append_item_shopify_store_row(
			self.item_no_store_row.name,
			self.store.name,
			shopify_product_id="502",
			shopify_variant_id="602",
			shopify_inventory_item_id="7001",
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test setup

		try:
			from nexwave_shopify_connector.nexwave_shopify.inventory import sync_single_item_inventory

			sync_single_item_inventory(self.item.name, self.store.name)

			mock_set.assert_not_called()
			conflict_logs = frappe.get_all(
				"NexWave Shopify Log",
				filters={
					"shopify_store": self.store.name,
					"status": "Error",
					"method": "sync_single_item_inventory",
					"reference_doctype": "Item",
				},
				fields=["message", "reference_name"],
			)
			conflict_logs = [
				log
				for log in conflict_logs
				if log.get("message", "").startswith("Inventory preparation conflict")
			]
			self.assertEqual(
				{log.reference_name for log in conflict_logs},
				{self.item.name, self.item_no_store_row.name},
			)
		finally:
			frappe.db.delete("Item Shopify Store", {"name": other_row_name})
			frappe.db.delete(
				"NexWave Shopify Log",
				{"shopify_store": self.store.name, "method": "sync_single_item_inventory"},
			)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test teardown

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.Session")
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory.set_inventory_batch")
	def test_single_item_skipped_when_no_store_row(self, mock_set, mock_session):
		"""An item without an Item Shopify Store row should be skipped silently."""
		mock_session.temp = _noop_session
		from nexwave_shopify_connector.nexwave_shopify.inventory import sync_single_item_inventory

		sync_single_item_inventory(self.item_no_store_row.name, self.store.name)

		mock_set.assert_not_called()

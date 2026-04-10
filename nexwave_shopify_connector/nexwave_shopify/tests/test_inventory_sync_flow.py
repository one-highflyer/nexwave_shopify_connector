# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""Integration tests for sync_store_inventory.

These tests patch ``set_inventory_batch``, ``fetch_inventory_item_ids`` and
``Session`` inside inventory.py so no real Shopify HTTP traffic is made. The
goal is to verify:
  - batch assembly (one call per location per chunk)
  - lazy backfill (cache write, skipped variants)
  - disabled store / bench config early returns
  - zero-qty items are included
  - last_inventory_sync is updated on completion
  - partial batch failures produce a Warning log
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
	BatchResult,
	ThrottleStatus,
)

from .fixtures import (
	TEST_WAREHOUSE,
	ensure_item_shopify_store_row,
	ensure_test_item,
	ensure_test_shopify_store,
	set_bin_qty,
)


@contextmanager
def _noop_session(*args, **kwargs):
	"""Replacement for Session.temp used in patches."""
	yield


class TestSyncStoreInventory(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.store = ensure_test_shopify_store()
		cls.item_a = ensure_test_item("_Test Shop Item A")
		cls.item_b = ensure_test_item("_Test Shop Item B")
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
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture persistence

	def setUp(self):
		# Reset last_inventory_sync and cached ids so each test starts fresh
		frappe.db.set_value("Shopify Store", self.store.name, "last_inventory_sync", None)
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = %s
			WHERE parent = %s AND shopify_store = %s
			""",
			("1001", self.item_a.name, self.store.name),
		)
		frappe.db.sql(
			"""
			UPDATE `tabItem Shopify Store`
			SET shopify_inventory_item_id = %s
			WHERE parent = %s AND shopify_store = %s
			""",
			("1002", self.item_b.name, self.store.name),
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- ensure test isolation

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

		# Assert last_inventory_sync was updated
		last_sync = frappe.db.get_value("Shopify Store", self.store.name, "last_inventory_sync")
		self.assertIsNotNone(last_sync)

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
			"200": {"inventory_item_id": "9999", "inventory_management": "SHOPIFY"},
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
		original_conf = frappe.conf
		patched = frappe._dict(dict(original_conf or {}))
		patched["nexwave_shopify_disable_graphql_inventory_sync"] = [self.store.name]
		with patch.object(frappe, "conf", patched):
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

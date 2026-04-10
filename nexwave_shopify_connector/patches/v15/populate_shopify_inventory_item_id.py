# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe

from nexwave_shopify_connector.utils.logger import get_logger


def execute():
	"""
	One-time: the new shopify_inventory_item_id field arrives via doctype JSON.

	We DO NOT pre-populate via GraphQL here because:
	  - Not every bench has valid tokens at migration time
	  - Migration must be fast and offline-safe

	Lazy backfill handles it on first sync (250 items at a time via GraphQL
	nodes query) in sync_store_inventory._resolve_inventory_item_ids.
	"""
	logger = get_logger()
	stores_with_items = frappe.db.sql(
		"""
		SELECT shopify_store, COUNT(*) AS item_count
		FROM `tabItem Shopify Store`
		WHERE IFNULL(shopify_variant_id, '') != ''
		  AND IFNULL(shopify_inventory_item_id, '') = ''
		GROUP BY shopify_store
		""",
		as_dict=True,
	)
	for row in stores_with_items:
		logger.info(
			"post-upgrade: store %s has %s items pending inventory_item_id backfill",
			row.shopify_store,
			row.item_count,
		)

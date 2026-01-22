# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Add composite unique index on (shopify_store, shopify_fulfillment_id) for Delivery Note.

	This replaces the single-field unique constraint on shopify_fulfillment_id
	to allow the same fulfillment ID across different stores while preventing
	duplicates within a single store.
	"""
	# First, drop the old unique index if it exists
	old_index_name = "shopify_fulfillment_id"
	if frappe.db.has_index("tabDelivery Note", old_index_name):
		frappe.db.sql_ddl(f"ALTER TABLE `tabDelivery Note` DROP INDEX `{old_index_name}`")

	# Create composite unique index
	index_name = "unique_shopify_store_fulfillment"
	if not frappe.db.has_index("tabDelivery Note", index_name):
		frappe.db.sql_ddl(
			f"""
			CREATE UNIQUE INDEX `{index_name}`
			ON `tabDelivery Note` (`shopify_store`, `shopify_fulfillment_id`)
			"""
		)

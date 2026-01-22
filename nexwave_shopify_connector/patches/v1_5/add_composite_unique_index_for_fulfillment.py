# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Remove the unique constraint on shopify_fulfillment_id for Delivery Note.

	Shopify fulfillment IDs are only unique within a store, not globally.
	The deduplication is handled in code by checking both shopify_store
	and shopify_fulfillment_id fields.
	"""
	# Drop the old unique index if it exists
	if frappe.db.has_index("tabDelivery Note", "shopify_fulfillment_id"):
		frappe.db.sql_ddl("ALTER TABLE `tabDelivery Note` DROP INDEX `shopify_fulfillment_id`")

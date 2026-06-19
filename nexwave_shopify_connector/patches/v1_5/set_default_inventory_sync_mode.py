import frappe


def execute():
	"""Set the inventory sync mode for existing stores added before this field."""
	if not frappe.db.has_column("Shopify Store", "inventory_sync_mode"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabShopify Store`
		SET inventory_sync_mode = 'Full Inventory'
		WHERE inventory_sync_mode IS NULL OR inventory_sync_mode = ''
		"""
	)

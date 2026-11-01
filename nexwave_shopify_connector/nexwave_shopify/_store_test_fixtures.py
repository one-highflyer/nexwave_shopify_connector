# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe

TEST_STORE = "_test-oauth-connection.myshopify.com"
TEST_CLIENT_SECRET = "synthetic-client-secret"
TEST_COMPANY = "_Test Shopify Connection Company"


def make_oauth_store():
	"""Create a disabled store and its synthetic company when needed."""
	if not frappe.db.exists("Company", TEST_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": TEST_COMPANY,
				"abbr": "_TSC",
				"country": "United States",
				"default_currency": "USD",
				"chart_of_accounts": "Standard",
				"enable_perpetual_inventory": 0,
			}
		).insert(ignore_permissions=True)
	store = (
		frappe.get_doc("Shopify Store", TEST_STORE)
		if frappe.db.exists("Shopify Store", TEST_STORE)
		else frappe.new_doc("Shopify Store")
	)
	store.update(
		{
			"shop_domain": TEST_STORE,
			"company": TEST_COMPANY,
			"auth_method": "OAuth",
			"api_version": "2026-01",
			"client_id": "synthetic-client-id",
			"client_secret": TEST_CLIENT_SECRET,
			"access_token": None,
			"oauth_status": "Not Connected",
			"connected_user": None,
			"enabled": 0,
			"sync_orders": 0,
			"enable_inventory_sync": 0,
			"enable_item_sync": 0,
			"enable_webhook_orders_create": 0,
			"enable_webhook_orders_paid": 0,
			"enable_webhook_orders_cancelled": 0,
			"enable_webhook_fulfillment": 0,
		}
	)
	store.save(ignore_permissions=True)
	return store


def delete_oauth_store():
	if frappe.db.exists("Shopify Store", TEST_STORE):
		frappe.delete_doc("Shopify Store", TEST_STORE, force=True, ignore_permissions=True)
	if frappe.db.exists("Company", TEST_COMPANY):
		frappe.delete_doc("Company", TEST_COMPANY, force=True, ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- remove committed callback fixtures

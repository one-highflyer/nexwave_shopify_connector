# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Real documents for fulfillment row-allocation regressions."""

import frappe
from frappe.utils import add_days, nowdate

TEST_COMPANY = "_Test Company"
TEST_WAREHOUSE = "_Test Warehouse - _TC"
TEST_ITEM = "_Test Shopify Fulfillment Service"
TEST_STORE = "fulfillment-test.myshopify.com"


def create_fulfillment_test_store(auto_fulfill_remaining_qty=0):
	if not frappe.db.exists("Item", TEST_ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": TEST_ITEM,
				"item_group": "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		).insert()

	store = frappe.get_doc(
		{
			"doctype": "Shopify Store",
			"shop_domain": TEST_STORE,
			"enabled": 0,
			"auth_method": "Legacy (Access Token)",
			"company": TEST_COMPANY,
			"warehouse": TEST_WAREHOUSE,
			"cost_center": "Main - _TC",
			"auto_fulfill_remaining_qty": auto_fulfill_remaining_qty,
		}
	)
	if frappe.db.exists("Shopify Store", TEST_STORE):
		store = frappe.get_doc("Shopify Store", TEST_STORE)
		store.auto_fulfill_remaining_qty = auto_fulfill_remaining_qty
		store.save()
	else:
		store.insert()
	return store


def create_fulfillment_test_order(rows=None):
	rows = rows if rows is not None else [{"qty": 2, "rate": 100}, {"qty": 3, "rate": 75}]
	so = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": TEST_COMPANY,
			"customer": "_Test Customer",
			"currency": frappe.get_cached_value("Company", TEST_COMPANY, "default_currency"),
			"conversion_rate": 1,
			"delivery_date": add_days(nowdate(), 7),
			"shopify_order_id": frappe.generate_hash(length=12),
			"shopify_store": TEST_STORE,
			"ignore_pricing_rule": 1,
			"items": [{"item_code": TEST_ITEM, "warehouse": TEST_WAREHOUSE, **row} for row in rows],
		}
	)
	so.insert()
	so.submit()
	return so


def create_fulfillment_stock_item():
	from erpnext.stock.doctype.item.test_item import make_item
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	item = make_item("_Test Shopify Allocation Stock", {"is_stock_item": 1})
	make_stock_entry(item_code=item.name, qty=10, to_warehouse=TEST_WAREHOUSE, company=TEST_COMPANY, rate=100)
	return item


def create_fulfillment_test_delivery(so, row_quantities, submit=True):
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = make_delivery_note(so.name)
	dn.items = [row for row in dn.items if row.so_detail in row_quantities]
	for row in dn.items:
		row.qty = row_quantities[row.so_detail]
	dn.insert()
	if submit:
		dn.submit()
	return dn


def fulfillment_payload(so, *fulfillment_lines):
	return {
		"id": so.shopify_order_id,
		"fulfillment_status": "fulfilled",
		"fulfillments": [
			{"id": frappe.generate_hash(length=12), "status": "success", "line_items": lines}
			for lines in fulfillment_lines
		],
	}

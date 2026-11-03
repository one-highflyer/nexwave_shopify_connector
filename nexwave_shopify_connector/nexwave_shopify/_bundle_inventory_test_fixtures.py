# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Real document fixtures for component-based inventory publishing."""

import frappe
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.utils import nowdate

from nexwave_shopify_connector.nexwave_shopify._inventory_test_fixtures import (
	TEST_COMPANY,
	_get_default_warehouse,
	ensure_item_shopify_store_row,
	ensure_test_item,
	ensure_test_shopify_store,
	set_bin_qty,
)

STORE_DOMAIN = "_test-bundle-inventory.myshopify.com"
ITEM_CODES = {
	"component_a": "_Test Bundle Inventory Component A",
	"component_b": "_Test Bundle Inventory Component B",
	"kit": "_Test Bundle Inventory Kit",
	"undefined_kit": "_Test Bundle Inventory Undefined Kit",
	"stock_item": "_Test Bundle Inventory Stock Item",
}
SECOND_WAREHOUSE_NAME = "_Test Bundle Inventory Other Warehouse"
CUSTOMER_NAME = "_Test Bundle Inventory Customer"
PRICE_LIST_NAME = "_Test Bundle Inventory Price List"


def ensure_bundle_test_company():
	"""Provide the standard test company without unrelated fixture dependencies."""
	if not frappe.db.exists("Company", TEST_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": TEST_COMPANY,
				"abbr": "_TC",
				"country": "India",
				"default_currency": "INR",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
				"enable_perpetual_inventory": 0,
			}
		).insert(ignore_permissions=True)


def make_bundle_definition(item_code, components, *, disabled=0):
	"""Create a bundle using component quantities in their stock units."""
	if frappe.db.exists("Product Bundle", item_code):
		frappe.delete_doc("Product Bundle", item_code, force=True)
	return frappe.get_doc(
		{
			"doctype": "Product Bundle",
			"new_item_code": item_code,
			"disabled": disabled,
			"items": [
				{
					"item_code": component,
					"qty": qty,
					"uom": frappe.db.get_value("Item", component, "stock_uom"),
				}
				for component, qty in components
			],
		}
	).insert(ignore_permissions=True)


def make_bundle_inventory_data():
	ensure_bundle_test_company()
	warehouse = _get_default_warehouse()
	store = ensure_test_shopify_store(
		shop_domain=STORE_DOMAIN,
		warehouse=warehouse,
		last_inventory_sync=None,
		inventory_sync_mode="Full Inventory",
		reserve_stock_for_draft_orders=0,
	)
	data = frappe._dict(store=store, warehouse=warehouse)
	for key, item_code in ITEM_CODES.items():
		data[key] = ensure_test_item(
			item_code,
			is_stock_item=int(key not in {"kit", "undefined_kit"}),
			disabled=0,
		)
	for index, key in enumerate(("kit", "undefined_kit", "stock_item"), start=1):
		ensure_item_shopify_store_row(
			data[key].name,
			store.name,
			shopify_product_id=f"bundle-product-{index}",
			shopify_variant_id=f"bundle-variant-{index}",
			shopify_inventory_item_id=f"bundle-inventory-{index}",
			inventory_sync_pending=1,
		)
	make_bundle_definition(
		data.kit.name,
		[(data.component_a.name, 1), (data.component_b.name, 2)],
	)
	for key, qty in (("component_a", 9), ("component_b", 7), ("stock_item", 11)):
		set_bin_qty(data[key].name, warehouse, qty)
		frappe.db.set_value(
			"Bin",
			{"item_code": data[key].name, "warehouse": warehouse},
			"reserved_qty",
			0,
			update_modified=False,
		)
	return data


def make_component_draft_reservation(data, qty=7):
	"""Reserve received component stock through an actual draft Shopify order."""
	from nexwave_shopify_connector.nexwave_shopify._payment_test_fixtures import (
		_ensure_leaf_customer_group,
	)
	from nexwave_shopify_connector.nexwave_shopify.order import _create_sales_order

	if not frappe.db.exists("Price List", PRICE_LIST_NAME):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": PRICE_LIST_NAME,
				"currency": "INR",
				"selling": 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Customer", CUSTOMER_NAME):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER_NAME,
				"customer_group": _ensure_leaf_customer_group(),
				"territory": frappe.db.get_single_value("Selling Settings", "territory") or "All Territories",
			}
		).insert(ignore_permissions=True)
	make_stock_entry(
		item_code=data.component_a.name,
		target=data.warehouse,
		company=TEST_COMPANY,
		qty=9,
		basic_rate=1,
	)
	frappe.db.set_value(
		"Shopify Store",
		data.store.name,
		{
			"reserve_stock_for_draft_orders": 1,
			"auto_submit_sales_order": 0,
			"auto_create_invoice": 0,
			"price_list": PRICE_LIST_NAME,
			"cost_center": frappe.db.get_value("Company", TEST_COMPANY, "cost_center"),
		},
	)
	data.store.reload()
	return _create_sales_order(
		{
			"id": "bundle-component-reservation",
			"name": "#bundle-component-reservation",
			"financial_status": "pending",
			"fulfillment_status": None,
			"currency": "INR",
			"created_at": nowdate(),
			"taxes_included": False,
			"total_price": qty * 10,
			"line_items": [
				{
					"sku": data.component_a.name,
					"title": data.component_a.name,
					"quantity": qty,
					"price": 10,
					"discount_allocations": [],
					"tax_lines": [],
				}
			],
			"shipping_lines": [],
		},
		data.store,
		CUSTOMER_NAME,
	)


def make_second_warehouse():
	name = f"{SECOND_WAREHOUSE_NAME} - _TC"
	if not frappe.db.exists("Warehouse", name):
		warehouse = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": SECOND_WAREHOUSE_NAME,
				"company": TEST_COMPANY,
			}
		).insert(ignore_permissions=True)
		return warehouse.name
	return name


def cleanup_bundle_inventory_data():
	"""Remove fixtures even when an inventory job committed its transaction."""
	for order_name in frappe.get_all("Sales Order", filters={"shopify_store": STORE_DOMAIN}, pluck="name"):
		order = frappe.get_doc("Sales Order", order_name)
		reservations = frappe.get_all(
			"Stock Reservation Entry",
			filters={"voucher_type": "Sales Order", "voucher_no": order_name, "docstatus": 1},
			pluck="name",
		)
		if reservations:
			order.cancel_stock_reservation_entries(sre_list=reservations, notify=False)
		for reservation in frappe.get_all(
			"Stock Reservation Entry", filters={"voucher_no": order_name}, pluck="name"
		):
			frappe.delete_doc("Stock Reservation Entry", reservation, force=True)
		if order.docstatus == 1:
			order.cancel()
		frappe.delete_doc("Sales Order", order_name, force=True)
	stock_entries = frappe.get_all(
		"Stock Entry",
		filters=[["Stock Entry Detail", "item_code", "in", list(ITEM_CODES.values())]],
		pluck="name",
	)
	for entry_name in set(stock_entries):
		entry = frappe.get_doc("Stock Entry", entry_name)
		if entry.docstatus == 1:
			entry.cancel()
		frappe.delete_doc("Stock Entry", entry_name, force=True)
	frappe.db.delete("NexWave Shopify Log", {"shopify_store": STORE_DOMAIN})
	for key in ("kit", "undefined_kit"):
		if frappe.db.exists("Product Bundle", ITEM_CODES[key]):
			frappe.delete_doc("Product Bundle", ITEM_CODES[key], force=True)
	for item_code in ITEM_CODES.values():
		frappe.db.delete("Bin", {"item_code": item_code})
		frappe.db.delete("Stock Ledger Entry", {"item_code": item_code})
		if frappe.db.exists("Item", item_code):
			frappe.delete_doc("Item", item_code, force=True)
	if frappe.db.exists("Shopify Store", STORE_DOMAIN):
		frappe.delete_doc("Shopify Store", STORE_DOMAIN, force=True)
	for doctype, name in (("Customer", CUSTOMER_NAME), ("Price List", PRICE_LIST_NAME)):
		if frappe.db.exists(doctype, name):
			frappe.delete_doc(doctype, name, force=True)
	warehouse_name = f"{SECOND_WAREHOUSE_NAME} - _TC"
	if frappe.db.exists("Warehouse", warehouse_name):
		frappe.delete_doc("Warehouse", warehouse_name, force=True)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- remove fixtures persisted by inventory job commits

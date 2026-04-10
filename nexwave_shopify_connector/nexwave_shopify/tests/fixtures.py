# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""Idempotent fixture factories for inventory GraphQL tests.

These helpers create (or update) the minimum set of records needed to exercise
the batched inventory sync path without touching a real Shopify store. They are
safe to call multiple times.
"""

import frappe
from frappe.utils.password import set_encrypted_password

TEST_COMPANY = "_Test Company"
# `Stores - _TC` is auto-created when `_Test Company` is set up.
TEST_WAREHOUSE = "Stores - _TC"
TEST_SHOPIFY_LOCATION_ID = "loc_123"
TEST_STORE_DOMAIN = "_test-graphql-inventory.myshopify.com"
TEST_ITEM_GROUP = "All Item Groups"


def _get_default_warehouse() -> str:
	"""Return a test warehouse that definitely exists.

	Prefers `Stores - _TC`; falls back to any non-group warehouse linked to
	_Test Company when run on a site where that name is not present.
	"""
	if frappe.db.exists("Warehouse", TEST_WAREHOUSE):
		return TEST_WAREHOUSE
	wh = frappe.db.get_value(
		"Warehouse",
		{"company": TEST_COMPANY, "is_group": 0},
		"name",
	)
	if wh:
		return wh
	# Last resort: pick any non-group warehouse
	wh = frappe.db.get_value("Warehouse", {"is_group": 0}, "name")
	if not wh:
		raise RuntimeError("No warehouse available for tests")
	return wh


def ensure_test_shopify_store(**overrides) -> "frappe.model.document.Document":
	"""Create (or update) an idempotent Shopify Store for inventory tests.

	The store is `enabled=1`, `enable_inventory_sync=1`, and has one
	warehouse_mapping row binding `Stores - _TC` to a fake location id.
	A dummy access token is set so .get_password returns a value.

	Args:
		overrides: Any fields to override on the store doc.

	Returns:
		The Shopify Store document.
	"""
	domain = overrides.get("shop_domain") or TEST_STORE_DOMAIN
	warehouse = _get_default_warehouse()

	if frappe.db.exists("Shopify Store", domain):
		store = frappe.get_doc("Shopify Store", domain)
	else:
		store = frappe.get_doc(
			{
				"doctype": "Shopify Store",
				"shop_domain": domain,
				"auth_method": "Legacy (Access Token)",
				"company": TEST_COMPANY,
				"enabled": 1,
				"enable_inventory_sync": 1,
				"api_version": "2024-01",
			}
		)
		store.insert(ignore_permissions=True)

	# Ensure required flags
	store.enabled = 1
	store.enable_inventory_sync = 1
	store.company = TEST_COMPANY
	if not store.api_version:
		store.api_version = "2024-01"
	store.auth_method = "Legacy (Access Token)"

	# Apply caller overrides
	for key, value in (overrides or {}).items():
		if key == "shop_domain":
			continue
		setattr(store, key, value)

	# Ensure a warehouse mapping row
	mapping_exists = False
	for row in store.warehouse_mapping or []:
		if row.erpnext_warehouse == warehouse and row.shopify_location_id == TEST_SHOPIFY_LOCATION_ID:
			mapping_exists = True
			break
	if not mapping_exists:
		store.append(
			"warehouse_mapping",
			{
				"erpnext_warehouse": warehouse,
				"shopify_location_id": TEST_SHOPIFY_LOCATION_ID,
			},
		)

	store.flags.ignore_validate = True
	store.flags.ignore_mandatory = True
	store.save(ignore_permissions=True)

	# Set a dummy access token via the Password field. Use the low-level
	# helper so we don't need to go through .save() again.
	set_encrypted_password("Shopify Store", store.name, "test-token", fieldname="access_token")

	return store


def ensure_test_item(item_code: str, **overrides) -> "frappe.model.document.Document":
	"""Create an Item with sensible defaults. Idempotent.

	Uses ``flags.ignore_validate`` + ``ignore_mandatory`` so third-party apps
	hooking ``Item.validate`` (e.g., a site that has webshop + an unrelated
	barcode hook) can't break basic test setup.

	Args:
		item_code: Item code to create (or reuse).
		overrides: Any additional fields to set.

	Returns:
		The Item document.
	"""
	if frappe.db.exists("Item", item_code):
		return frappe.get_doc("Item", item_code)

	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": overrides.get("item_name") or item_code,
			"item_group": overrides.get("item_group") or TEST_ITEM_GROUP,
			"stock_uom": overrides.get("stock_uom") or "Nos",
			"is_stock_item": 1,
		}
	)
	for key, value in (overrides or {}).items():
		if key in ("item_name", "item_group", "stock_uom"):
			continue
		setattr(doc, key, value)
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
	return doc


def ensure_item_shopify_store_row(
	item_code: str,
	store_name: str,
	shopify_product_id: str,
	shopify_variant_id: str,
	shopify_inventory_item_id: str | None = None,
	**overrides,
) -> None:
	"""Append or update an Item Shopify Store row on Item.

	Idempotent: if a row already exists for (item, store), it is updated in
	place.
	"""
	item = frappe.get_doc("Item", item_code)

	existing = None
	for row in item.shopify_stores or []:
		if row.shopify_store == store_name:
			existing = row
			break

	data = {
		"shopify_product_id": shopify_product_id,
		"shopify_variant_id": shopify_variant_id,
		"shopify_sku": item_code,
		"enabled": 1,
	}
	if shopify_inventory_item_id is not None:
		data["shopify_inventory_item_id"] = shopify_inventory_item_id
	data.update(overrides or {})

	if existing:
		frappe.db.set_value("Item Shopify Store", existing.name, data, update_modified=False)
		return

	row = item.append(
		"shopify_stores",
		{
			"shopify_store": store_name,
			**data,
		},
	)
	row.db_insert()


def set_bin_qty(item_code: str, warehouse: str, qty: float) -> None:
	"""Create or update a Bin row with the given actual_qty.

	Writes directly to `tabBin` to avoid triggering stock ledger side effects.
	"""
	bin_name = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "name")
	if bin_name:
		frappe.db.set_value("Bin", bin_name, "actual_qty", qty, update_modified=False)
		return

	bin_doc = frappe.get_doc(
		{
			"doctype": "Bin",
			"item_code": item_code,
			"warehouse": warehouse,
			"actual_qty": qty,
		}
	)
	bin_doc.insert(ignore_permissions=True, ignore_if_duplicate=True)

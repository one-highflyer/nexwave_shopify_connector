# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

from typing import TYPE_CHECKING, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime
from shopify.api_version import ApiVersion
from shopify.resources import InventoryLevel, Variant
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.utils import create_shopify_log
from nexwave_shopify_connector.utils.logger import get_logger

if TYPE_CHECKING:
	from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store.shopify_store import ShopifyStore


def update_inventory_on_shopify():
	"""
	Scheduler job - sync inventory for all enabled stores.

	Runs every 5 minutes (configured in hooks.py) but checks each store's
	inventory_sync_frequency to determine if it's time to sync.
	"""
	# Get all stores with inventory sync enabled
	stores = frappe.get_all("Shopify Store", filters={"enabled": 1, "enable_inventory_sync": 1}, pluck="name")

	for store_name in stores:
		store = frappe.get_doc("Shopify Store", store_name)

		# Check if it's time to sync based on frequency
		if not _should_sync_inventory(store):
			continue

		# Enqueue inventory sync for this store
		frappe.enqueue(
			"nexwave_shopify_connector.nexwave_shopify.inventory.sync_store_inventory",
			queue="long",
			timeout=1800,  # 30 minutes timeout for large inventories
			store_name=store_name,
		)


def _should_sync_inventory(store) -> bool:
	"""
	Check if inventory sync should run based on store's sync frequency.

	Args:
		store: Shopify Store document

	Returns:
		True if sync should run
	"""
	if not store.last_inventory_sync:
		return True

	frequency_minutes = store.inventory_sync_frequency or 30
	next_sync_time = add_to_date(store.last_inventory_sync, minutes=frequency_minutes)

	return now_datetime() >= next_sync_time


def sync_store_inventory(store_name: str):
	"""
	Sync inventory for all items linked to a specific store.

	Args:
		store_name: Shopify Store name
	"""
	logger = get_logger()
	logger.info("Syncing inventory for Shopify store: %s", store_name)
	store = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled or not store.enable_inventory_sync:
		return

	# Check warehouse mappings
	if not store.warehouse_mapping:
		logger.error("No warehouse mappings configured for inventory sync for Shopify store: %s", store_name)
		frappe.log_error(
			title=f"Shopify Inventory Sync - {store_name}",
			message="No warehouse mappings configured for inventory sync",
		)
		return

	# Initialize API versions
	_init_shopify_api_versions()

	# Get auth details
	api_version = store.api_version or DEFAULT_API_VERSION
	access_token = store.get_password("access_token")

	if not access_token:
		frappe.log_error(
			title=f"Shopify Inventory Sync Error - {store_name}",
			message=f"Access token not configured for store {store_name}",
		)
		return

	# Get all items with Shopify product/variant IDs for this store
	items_to_sync = get_items_with_shopify_ids(store_name)

	if not items_to_sync:
		logger.warning("No items to sync for Shopify store: %s", store_name)
		frappe.db.set_value("Shopify Store", store_name, "last_inventory_sync", now_datetime())
		frappe.db.commit()
		return

	sync_count = 0
	error_count = 0

	try:
		with Session.temp(store.shop_domain, api_version, access_token):
			logger.info(
				"Syncing inventory for %s items for Shopify store: %s", len(items_to_sync), store_name
			)
			for item_data in items_to_sync:
				try:
					_sync_item_inventory(item_data, store)
					sync_count += 1
				except Exception as e:
					logger.error(
						"Failed to sync inventory for %s items for Shopify store: %s, error: %s",
						item_data["item_code"],
						store_name,
						str(e),
						exc_info=True,
					)
					error_count += 1
					# Log individual item failure to NexWave Shopify Log
					create_shopify_log(
						status="Error",
						method="sync_item_inventory",
						shopify_store=store_name,
						message=f"Failed to sync inventory for {item_data['item_code']}",
						exception=str(e),
						reference_doctype="Item",
						reference_name=item_data["item_code"],
						request_data={
							"item_code": item_data["item_code"],
							"shopify_variant_id": item_data.get("shopify_variant_id"),
						},
					)

		# Update last sync time
		frappe.db.set_value("Shopify Store", store_name, "last_inventory_sync", now_datetime())
		frappe.db.commit()

		# Determine overall status
		if error_count > 0 and sync_count == 0:
			status = "Error"  # Complete failure
		elif error_count > 0:
			status = "Warning"  # Partial success
		else:
			status = "Success"  # All items synced

		logger.info(
			"Inventory sync completed for Shopify store: %s, status: %s, errors: %s",
			store_name,
			status,
			error_count,
		)

		create_shopify_log(
			status=status,
			method="sync_store_inventory",
			shopify_store=store_name,
			message=f"Synced inventory for {sync_count} items"
			+ (f" ({error_count} errors)" if error_count else ""),
			reference_doctype="Shopify Store",
			reference_name=store_name,
		)

	except Exception as e:
		logger.error(
			"Store-level sync error for Shopify store: %s, error: %s", store_name, str(e), exc_info=True
		)
		create_shopify_log(
			status="Error",
			method="sync_store_inventory",
			shopify_store=store_name,
			message=f"Store-level sync error: {str(e)}",
			exception=frappe.get_traceback(),
			reference_doctype="Shopify Store",
			reference_name=store_name,
		)


def _init_shopify_api_versions():
	"""Initialize Shopify API versions if not already loaded."""
	if not ApiVersion.versions:
		ApiVersion.fetch_known_versions()


def get_items_with_shopify_ids(store_name: str) -> List[Dict]:
	"""
	Get all items that have Shopify product/variant IDs for this store.

	Args:
		store_name: Shopify Store name

	Returns:
		List of dicts with item_code, shopify_variant_id
	"""
	return frappe.db.sql(
		"""
		SELECT
			iss.parent as item_code,
			iss.shopify_product_id,
			iss.shopify_variant_id
		FROM `tabItem Shopify Store` iss
		JOIN `tabItem` item ON item.name = iss.parent
		WHERE
			iss.shopify_store = %s
			AND iss.enabled = 1
			AND iss.shopify_variant_id IS NOT NULL
			AND iss.shopify_variant_id != ''
			AND item.disabled = 0
		""",
		(store_name,),
		as_dict=True,
	)


def _sync_item_inventory(item_data: Dict, store):
	"""
	Sync inventory for a single item to all mapped Shopify locations.

	Args:
		item_data: Dict with item_code, shopify_variant_id
		store: Shopify Store document
	"""
	item_code = item_data["item_code"]
	variant_id = item_data["shopify_variant_id"]
	product_id = item_data["shopify_product_id"]

	# Get inventory_item_id from variant
	variant = Variant.find(variant_id, product_id=product_id)
	if not variant or not variant.inventory_item_id:
		raise Exception(f"Could not get inventory_item_id for variant {variant_id}")

	inventory_item_id = variant.inventory_item_id

	# Sync to each mapped location
	for mapping in store.warehouse_mapping:
		location_id = mapping.shopify_location_id
		warehouse = mapping.erpnext_warehouse

		if not location_id or not warehouse:
			continue

		# Get stock qty from ERPNext
		qty = get_stock_qty(item_code, warehouse)

		# Update Shopify inventory level
		_set_inventory_level(location_id, inventory_item_id, qty)


def get_stock_qty(item_code: str, warehouse: str) -> float:
	"""
	Get actual stock quantity from ERPNext Bin.

	Args:
		item_code: Item code
		warehouse: Warehouse name

	Returns:
		Actual quantity (0 if no bin exists)
	"""
	qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
	return qty or 0


def _set_inventory_level(location_id: str, inventory_item_id: str, qty: float):
	"""
	Set inventory level in Shopify for a specific location.

	Args:
		location_id: Shopify location ID
		inventory_item_id: Shopify inventory item ID
		qty: Quantity to set
	"""
	# Shopify requires integer quantities
	qty = int(qty)

	# Use the set endpoint to set absolute quantity
	InventoryLevel.set(location_id=location_id, inventory_item_id=inventory_item_id, available=qty)


def sync_single_item_inventory(item_code: str, store_name: Optional[str] = None):
	"""
	Sync inventory for a single item to one or all stores.

	Can be called manually or from stock entry hooks.

	Args:
		item_code: ERPNext Item code
		store_name: Optional specific store (syncs to all eligible stores if not provided)
	"""
	item = frappe.get_doc("Item", item_code)

	if item.disabled:
		return

	# Get stores to sync to
	if store_name:
		stores = [frappe.get_doc("Shopify Store", store_name)]
	else:
		# Get all stores this item is linked to
		store_names = frappe.get_all(
			"Item Shopify Store",
			filters={"parent": item_code, "enabled": 1, "shopify_variant_id": ["is", "set"]},
			pluck="shopify_store",
		)
		stores = [frappe.get_doc("Shopify Store", name) for name in store_names]

	for store in stores:
		if not store.enabled or not store.enable_inventory_sync:
			continue

		# Initialize API versions
		_init_shopify_api_versions()

		api_version = store.api_version or DEFAULT_API_VERSION
		access_token = store.get_password("access_token")

		if not access_token:
			continue

		# Get Item Shopify Store row
		store_row = None
		for row in item.shopify_stores:
			if row.shopify_store == store.name:
				store_row = row
				break

		if not store_row or not store_row.shopify_variant_id:
			continue

		try:
			with Session.temp(store.shop_domain, api_version, access_token):
				item_data = {
					"item_code": item_code,
					"shopify_variant_id": store_row.shopify_variant_id,
					"shopify_product_id": store_row.shopify_product_id,
				}
				_sync_item_inventory(item_data, store)

		except Exception as e:
			frappe.log_error(
				title=f"Shopify Inventory Sync Error - {store.name}",
				message=f"Failed to sync inventory for {item_code}: {str(e)}",
			)


def manual_inventory_sync(store_name: str):
	"""
	Manual trigger to sync all inventory for a store.

	Called from "Sync Inventory" button on Shopify Store.

	Args:
		store_name: Shopify Store name
	"""
	logger = get_logger()
	logger.info("Manual inventory sync for Shopify store: %s", store_name)
	store: ShopifyStore = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled:
		logger.error("Shopify store: %s is not enabled", store_name)
		frappe.throw(_("Store is not enabled"))

	if not store.enable_inventory_sync:
		logger.error("Inventory sync is not enabled for Shopify store: %s", store_name)
		frappe.throw(_("Inventory sync is not enabled for this store"))

	if not store.warehouse_mapping:
		logger.error("No warehouse mappings configured for Shopify store: %s", store_name)
		frappe.throw(_("No warehouse mappings configured"))

	# Enqueue sync job
	frappe.enqueue(
		"nexwave_shopify_connector.nexwave_shopify.inventory.sync_store_inventory",
		queue="long",
		timeout=1800,
		store_name=store_name,
	)

	frappe.msgprint(_("Inventory sync has been queued for {0}").format(store_name), indicator="green")
	logger.info("Successfully queued inventory sync for Shopify store: %s", store_name)

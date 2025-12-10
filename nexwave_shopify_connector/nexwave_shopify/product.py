# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import hashlib
import json
from typing import Any, Dict, List, Optional

import frappe
from frappe import _
from frappe.utils import now_datetime
from shopify.api_version import ApiVersion
from shopify.resources import Metafield, Product, Variant
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.utils import (
	create_shopify_log,
	get_eligible_stores_for_item,
	get_item_shopify_store_row,
)

# Fields that go on the product level
PRODUCT_STANDARD_FIELDS = ["body_html", "vendor", "product_type", "tags", "handle"]

# Fields that go on the variant level
VARIANT_STANDARD_FIELDS = ["price", "compare_at_price", "sku", "barcode", "weight", "weight_unit"]


def sync_item_to_shopify(doc, method=None):
	"""
	Doc event handler - sync item to all eligible stores.

	Called on Item on_update and after_insert events.

	Args:
		doc: Item document
		method: Event method name (on_update, after_insert)
	"""
	# Skip if in test mode or import
	if frappe.flags.in_test or frappe.flags.in_import:
		return

	# Skip if item is disabled
	if doc.disabled:
		return

	# Get eligible stores for this item
	eligible_stores = get_eligible_stores_for_item(doc)

	if not eligible_stores:
		return

	for store in eligible_stores:
		# Check if store has update_shopify_on_item_update enabled
		if method == "on_update" and not store.update_shopify_on_item_update:
			continue

		# Enqueue sync job for each store
		frappe.enqueue(
			"nexwave_shopify_connector.nexwave_shopify.product.sync_item_to_store",
			queue="short",
			timeout=300,
			item_code=doc.name,
			store_name=store.name,
		)


def sync_item_to_store(item_code: str, store_name: str):
	"""
	Sync single item to a single Shopify store.

	Args:
		item_code: ERPNext Item code
		store_name: Shopify Store name
	"""
	item = frappe.get_doc("Item", item_code)
	store = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled or not store.enable_item_sync:
		return

	# Initialize API versions
	_init_shopify_api_versions()

	# Get auth details
	api_version = store.api_version or DEFAULT_API_VERSION
	access_token = store.get_password("access_token")

	if not access_token:
		frappe.log_error(
			title=f"Shopify Sync Error - {store_name}",
			message=f"Access token not configured for store {store_name}",
		)
		return

	# Get or create Item Shopify Store row
	store_row = get_item_shopify_store_row(item, store)

	# Check for changes using hash
	current_hash = compute_sync_hash(item, store)
	if store_row and store_row.last_sync_hash == current_hash:
		# No changes, skip sync
		return

	try:
		with Session.temp(store.shop_domain, api_version, access_token):
			# Build product payload
			product_data, variant_data, metafields_data = build_product_payload(item, store)

			if store_row and store_row.shopify_product_id:
				# Update existing product
				product = _update_shopify_product(
					store_row.shopify_product_id,
					store_row.shopify_variant_id,
					product_data,
					variant_data,
					metafields_data,
				)
			else:
				# Create new product
				product = _create_shopify_product(product_data, variant_data, metafields_data)

			# Update Item Shopify Store row
			_update_item_shopify_store_row(item, store, product, current_hash)

			create_shopify_log(
				status="Success",
				method="sync_item_to_store",
				shopify_store=store_name,
				message=f"Synced item {item_code} to Shopify product {product.id}",
				reference_doctype="Item",
				reference_name=item_code,
			)

	except Exception:
		create_shopify_log(
			status="Error",
			method="sync_item_to_store",
			shopify_store=store_name,
			exception=frappe.get_traceback(),
			message=f"Failed to sync item {item_code}",
			reference_doctype="Item",
			reference_name=item_code,
		)


def _init_shopify_api_versions():
	"""Initialize Shopify API versions if not already loaded."""
	if not ApiVersion.versions:
		ApiVersion.fetch_known_versions()


def _create_shopify_product(
	product_data: Dict[str, Any], variant_data: Dict[str, Any], metafields_data: List[Dict[str, Any]]
) -> Product:
	"""
	Create a new product in Shopify.

	Args:
		product_data: Product level fields
		variant_data: Variant level fields
		metafields_data: List of metafield definitions

	Returns:
		Created Product resource
	"""
	# Create product with product-level data only (no variants)
	# Shopify auto-creates a default variant when product is saved
	product = Product()
	for key, value in product_data.items():
		setattr(product, key, value)

	if not product.save():
		raise Exception(f"Failed to create product: {product.errors.full_messages()}")

	# Update default variant with variant-level data (sku, price, inventory_management, etc.)
	if variant_data and product.variants:
		default_variant = product.variants[0]
		for key, value in variant_data.items():
			setattr(default_variant, key, value)
		if not default_variant.save():
			raise Exception(f"Failed to update variant: {default_variant.errors.full_messages()}")

	# Create metafields if any
	if metafields_data and product.id:
		for mf_data in metafields_data:
			metafield = Metafield(
				{
					"namespace": mf_data["namespace"],
					"key": mf_data["key"],
					"value": mf_data["value"],
					"type": mf_data["type"],
					"owner_resource": "product",
					"owner_id": product.id,
				}
			)
			metafield.save()

	return product


def _update_shopify_product(
	product_id: str,
	variant_id: Optional[str],
	product_data: Dict[str, Any],
	variant_data: Dict[str, Any],
	metafields_data: List[Dict[str, Any]],
) -> Product:
	"""
	Update an existing product in Shopify.

	Args:
		product_id: Shopify product ID
		variant_id: Shopify variant ID (optional)
		product_data: Product level fields
		variant_data: Variant level fields
		metafields_data: List of metafield definitions

	Returns:
		Updated Product resource
	"""
	product = Product.find(product_id)

	# Update product fields
	for key, value in product_data.items():
		setattr(product, key, value)

	if not product.save():
		raise Exception(f"Failed to update product: {product.errors.full_messages()}")

	# Update variant if we have variant data
	if variant_data and variant_id:
		variant = Variant.find(variant_id, product_id=product_id)
		for key, value in variant_data.items():
			setattr(variant, key, value)
		if not variant.save():
			raise Exception(f"Failed to update variant: {variant.errors.full_messages()}")
	elif variant_data and product.variants:
		# Update first variant if no specific variant ID
		variant = product.variants[0]
		for key, value in variant_data.items():
			setattr(variant, key, value)
		variant.save()

	# Update metafields
	if metafields_data:
		existing_metafields = Metafield.find(resource="products", resource_id=product_id)
		existing_map = {(mf.namespace, mf.key): mf for mf in existing_metafields}

		for mf_data in metafields_data:
			key_tuple = (mf_data["namespace"], mf_data["key"])
			if key_tuple in existing_map:
				# Update existing metafield
				mf = existing_map[key_tuple]
				mf.value = mf_data["value"]
				mf.save()
			else:
				# Create new metafield
				metafield = Metafield(
					{
						"namespace": mf_data["namespace"],
						"key": mf_data["key"],
						"value": mf_data["value"],
						"type": mf_data["type"],
						"owner_resource": "product",
						"owner_id": product_id,
					}
				)
				metafield.save()

	return product


def _update_item_shopify_store_row(item, store, product, sync_hash: str):
	"""
	Update or create Item Shopify Store child row with sync details.

	Args:
		item: Item document
		store: Shopify Store document
		product: Shopify Product resource
		sync_hash: Current sync hash
	"""
	store_row = get_item_shopify_store_row(item, store)

	# Get first variant ID
	variant_id = None
	if product.variants:
		variant_id = str(product.variants[0].id)

	if store_row:
		# Update existing row
		frappe.db.set_value(
			"Item Shopify Store",
			store_row.name,
			{
				"shopify_product_id": str(product.id),
				"shopify_variant_id": variant_id,
				"last_sync_at": now_datetime(),
				"last_sync_hash": sync_hash,
			},
			update_modified=False,
		)
	else:
		# Create new row
		item.reload()
		item.append(
			"shopify_stores",
			{
				"shopify_store": store.name,
				"enabled": 1,
				"shopify_product_id": str(product.id),
				"shopify_variant_id": variant_id,
				"last_sync_at": now_datetime(),
				"last_sync_hash": sync_hash,
			},
		)
		item.flags.ignore_validate = True
		item.flags.ignore_mandatory = True
		item.save(ignore_permissions=True)

	frappe.db.commit()


def build_product_payload(item, store) -> tuple:
	"""
	Build Shopify product JSON from Item using store's field mapping.

	Args:
		item: Item document
		store: Shopify Store document

	Returns:
		Tuple of (product_data, variant_data, metafields_data)
	"""
	product_data = {"title": item.item_name or item.name}
	variant_data = {
		"sku": item.name,  # Use item_code as SKU (matches ecommerce_integrations pattern)
	}
	metafields_data = []

	# Enable inventory tracking for stock items
	if item.is_stock_item:
		variant_data["inventory_management"] = "shopify"

	# Process field mappings
	for field_map in store.item_field_map:
		erpnext_field = field_map.erpnext_field
		field_type = field_map.shopify_field_type

		# Get value from item
		value = _get_field_value(item, store, erpnext_field, field_map.default_value)

		if value is None:
			continue

		if field_type == "Standard Field":
			shopify_field = field_map.shopify_standard_field
			if shopify_field in PRODUCT_STANDARD_FIELDS:
				product_data[shopify_field] = value
			elif shopify_field in VARIANT_STANDARD_FIELDS:
				variant_data[shopify_field] = value

		elif field_type == "Metafield":
			metafields_data.append(
				{
					"namespace": field_map.metafield_namespace,
					"key": field_map.metafield_key,
					"value": str(value),
					"type": field_map.metafield_type or "single_line_text_field",
				}
			)

	return product_data, variant_data, metafields_data


def _get_field_value(item, store, field_name: str, default_value: Optional[str] = None):
	"""
	Get field value from item, with special handling for certain fields.

	Args:
		item: Item document
		store: Shopify Store document
		field_name: Field name to get
		default_value: Default value if field is empty

	Returns:
		Field value or default
	"""
	# Special handling for price - get from Item Price
	if field_name in ("standard_rate", "price", "valuation_rate"):
		return get_item_price(item, store) or default_value

	# Get from item directly
	value = getattr(item, field_name, None)

	if value is None or value == "":
		return default_value

	return value


def get_item_price(item, store) -> Optional[float]:
	"""
	Get item price from store's configured price list.

	Args:
		item: Item document
		store: Shopify Store document

	Returns:
		Price rate or None
	"""
	if not store.price_list:
		# Fallback to item's standard_rate
		return item.standard_rate if hasattr(item, "standard_rate") else None

	price = frappe.db.get_value(
		"Item Price",
		{
			"item_code": item.name,
			"price_list": store.price_list,
			"selling": 1,
		},
		"price_list_rate",
	)

	return price


def compute_sync_hash(item, store) -> str:
	"""
	Compute hash of item fields for change detection.

	Args:
		item: Item document
		store: Shopify Store document

	Returns:
		MD5 hash string
	"""
	# Collect all mapped field values
	hash_data = {"item_name": item.item_name}

	for field_map in store.item_field_map:
		field_name = field_map.erpnext_field
		value = _get_field_value(item, store, field_name, field_map.default_value)
		hash_data[field_name] = str(value) if value is not None else ""

	# Create deterministic hash
	hash_str = json.dumps(hash_data, sort_keys=True)
	return hashlib.md5(hash_str.encode()).hexdigest()


def sync_items_to_store(store_name: str):
	"""
	Sync all eligible items to a specific store.

	Called from manual "Sync All Items" button.

	Args:
		store_name: Shopify Store name
	"""
	store = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled or not store.enable_item_sync:
		frappe.throw(_("Item sync is not enabled for this store"))

	# Get all items that have this store in shopify_stores
	items_with_store = frappe.get_all(
		"Item Shopify Store", filters={"shopify_store": store_name, "enabled": 1}, pluck="parent"
	)

	# Also get items that match store filters but don't have explicit row
	if store.item_filters:
		# Build filter query based on store filters
		all_items = frappe.get_all("Item", filters={"disabled": 0}, pluck="name")
		for item_code in all_items:
			if item_code not in items_with_store:
				item = frappe.get_doc("Item", item_code)
				# Check if item matches filters (using existing utility)
				from nexwave_shopify_connector.nexwave_shopify.utils import is_item_eligible_for_store

				if is_item_eligible_for_store(item, store):
					items_with_store.append(item_code)

	# Remove duplicates
	items_to_sync = list(set(items_with_store))

	if not items_to_sync:
		frappe.msgprint(_("No items to sync for this store"))
		return

	# Enqueue sync jobs
	for item_code in items_to_sync:
		frappe.enqueue(
			"nexwave_shopify_connector.nexwave_shopify.product.sync_item_to_store",
			queue="short",
			timeout=300,
			item_code=item_code,
			store_name=store_name,
		)

	frappe.msgprint(
		_("Queued {0} items for sync to {1}").format(len(items_to_sync), store_name), indicator="green"
	)

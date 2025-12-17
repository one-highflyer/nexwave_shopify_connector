# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import base64
import hashlib
import json
import os
from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime
from shopify.api_version import ApiVersion
from shopify.resources import Collect, CustomCollection, Image, Metafield, Product, Variant
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.utils import (
	create_shopify_log,
	get_eligible_stores_for_item,
	get_item_shopify_store_row,
)
from nexwave_shopify_connector.utils.logger import get_logger

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


def sync_item_price_to_shopify(doc, method=None):
	"""
	Doc event handler - sync item when its price changes.

	Called on Item Price on_update and after_insert events.

	Args:
		doc: Item Price document
		method: Event method name
	"""
	# Skip if in test mode or import
	if frappe.flags.in_test or frappe.flags.in_import:
		return

	# Only sync selling prices
	if not doc.selling:
		return

	item_code = doc.item_code
	price_list = doc.price_list

	# Find stores that use this price list
	stores = frappe.get_all(
		"Shopify Store",
		filters={
			"enabled": 1,
			"enable_item_sync": 1,
			"update_shopify_on_item_update": 1,
			"price_list": price_list,
		},
		pluck="name",
	)

	if not stores:
		return

	# Check if item is linked to any of these stores
	for store_name in stores:
		# Check if item has this store enabled
		has_store = frappe.db.exists(
			"Item Shopify Store", {"parent": item_code, "shopify_store": store_name, "enabled": 1}
		)

		if has_store:
			frappe.enqueue(
				"nexwave_shopify_connector.nexwave_shopify.product.sync_item_to_store",
				queue="short",
				timeout=300,
				item_code=item_code,
				store_name=store_name,
			)


@frappe.whitelist()
def manual_sync_item_to_shopify(item_code: str):
	"""
	Manually trigger sync of item to all configured Shopify stores.

	Called from Item form button. Forces sync regardless of change detection.

	Args:
		item_code: ERPNext Item code
	"""
	item = frappe.get_doc("Item", item_code)

	if not item.shopify_stores:
		frappe.throw(_("No Shopify stores configured for this item"))

	queued_count = 0
	for store_row in item.shopify_stores:
		if store_row.enabled:
			frappe.enqueue(
				"nexwave_shopify_connector.nexwave_shopify.product.sync_item_to_store",
				queue="short",
				timeout=300,
				item_code=item_code,
				store_name=store_row.shopify_store,
				force=True,  # Skip change detection for manual sync
			)
			queued_count += 1

	if queued_count == 0:
		frappe.throw(_("No enabled Shopify stores found for this item"))

	return {"success": True, "queued_count": queued_count}


def sync_item_to_store(item_code: str, store_name: str, force: bool = False):
	"""
	Sync single item to a single Shopify store.

	Args:
		item_code: ERPNext Item code
		store_name: Shopify Store name
		force: If True, skip change detection and force sync
	"""
	logger = get_logger()
	logger.info("Syncing item %s to store %s", item_code, store_name)
	item = frappe.get_doc("Item", item_code)
	store = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled or not store.enable_item_sync:
		logger.error("Store %s is not enabled or item sync is not enabled", store_name)
		return

	# Initialize API versions
	_init_shopify_api_versions()

	# Get auth details
	api_version = store.api_version or DEFAULT_API_VERSION
	access_token = store.get_password("access_token")

	if not access_token:
		logger.error("Access token not configured for store %s", store_name)
		frappe.log_error(
			title=f"Shopify Sync Error - {store_name}",
			message=f"Access token not configured for store {store_name}",
		)
		return

	# Get or create Item Shopify Store row
	store_row = get_item_shopify_store_row(item, store)

	# Check for changes using hash (skip if force=True)
	current_hash = compute_sync_hash(item, store)
	if not force and store_row and store_row.last_sync_hash == current_hash:
		# No changes, skip sync
		logger.info("No changes, skipping sync for item %s", item_code)
		return

	try:
		with Session.temp(store.shop_domain, api_version, access_token):
			# Build product payload
			product_data, variant_data, metafields_data, category_value, collections_field = (
				build_product_payload(item, store)
			)

			# Add category to product data if specified
			if category_value:
				product_data["product_category"] = {"product_taxonomy_node_id": str(category_value)}

			if store_row and store_row.shopify_product_id:
				# Update existing product
				logger.info(
					"Updating existing product %s, item %s, store %s",
					store_row.shopify_product_id,
					item_code,
					store_name,
				)
				product = _update_shopify_product(
					store_row.shopify_product_id,
					store_row.shopify_variant_id,
					product_data,
					variant_data,
					metafields_data,
				)
			else:
				# Create new product
				logger.info("Creating new product %s, item %s, store %s", item_code, store_name)
				product = _create_shopify_product(product_data, variant_data, metafields_data)

			# Sync collections if mapping configured
			if collections_field:
				logger.info(
					"Syncing collections for product %s, item %s, store %s", product.id, item_code, store_name
				)
				_sync_product_collections(str(product.id), item, store, collections_field)

			# Sync image if enabled
			image_hash = None
			if store.enable_image_sync:
				logger.info(
					"Syncing image for product %s, item %s, store %s", product.id, item_code, store_name
				)
				image_hash = _sync_item_image_to_shopify(item, store, product.id, store_row, force)

			# Update Item Shopify Store row
			_update_item_shopify_store_row(item, store, product, current_hash, image_hash)

			create_shopify_log(
				status="Success",
				method="sync_item_to_store",
				shopify_store=store_name,
				message=f"Synced item {item_code} to Shopify product {product.id}",
				reference_doctype="Item",
				reference_name=item_code,
			)

	except Exception as e:
		logger.error("Failed to sync item %s to store %s: %s", item_code, store_name, str(e), exc_info=True)
		create_shopify_log(
			status="Error",
			method="sync_item_to_store",
			shopify_store=store_name,
			exception=frappe.get_traceback(),
			message=f"Failed to sync item {item_code}",
			reference_doctype="Item",
			reference_name=item_code,
		)
		frappe.db.commit()
		raise


def _init_shopify_api_versions():
	"""Initialize Shopify API versions if not already loaded."""
	if not ApiVersion.versions:
		ApiVersion.fetch_known_versions()


def _create_shopify_product(
	product_data: dict[str, Any], variant_data: dict[str, Any], metafields_data: list[dict[str, Any]]
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
	logger = get_logger()
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
			logger.error("Failed to update default variant: %s", default_variant.errors.full_messages())
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
			if not metafield.save():
				logger.warning(
					"Failed to create metafield %s.%s for product %s: %s",
					mf_data["namespace"],
					mf_data["key"],
					product.id,
					metafield.errors.full_messages(),
				)

	return product


def _update_shopify_product(
	product_id: str,
	variant_id: str | None,
	product_data: dict[str, Any],
	variant_data: dict[str, Any],
	metafields_data: list[dict[str, Any]],
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
	logger = get_logger()
	product = Product.find(product_id)

	# Update product fields
	for key, value in product_data.items():
		setattr(product, key, value)

	if not product.save():
		logger.error("Failed to update product: %s", product.errors.full_messages())
		raise Exception(f"Failed to update product: {product.errors.full_messages()}")

	# Update variant if we have variant data
	if variant_data and variant_id:
		variant = Variant.find(variant_id, product_id=product_id)
		for key, value in variant_data.items():
			setattr(variant, key, value)
		if not variant.save():
			logger.error("Failed to update variant: %s", variant.errors.full_messages())
			raise Exception(f"Failed to update variant: {variant.errors.full_messages()}")
	elif variant_data and product.variants:
		# Update first variant if no specific variant ID
		variant = product.variants[0]
		for key, value in variant_data.items():
			setattr(variant, key, value)
		if not variant.save():
			logger.error("Failed to update variant: %s", variant.errors.full_messages())
			raise Exception(f"Failed to update variant: {variant.errors.full_messages()}")

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


def _update_item_shopify_store_row(item, store, product, sync_hash: str, image_hash: str | None = None):
	"""
	Update or create Item Shopify Store child row with sync details.

	Args:
		item: Item document
		store: Shopify Store document
		product: Shopify Product resource
		sync_hash: Current sync hash
		image_hash: Current image hash (optional)
	"""
	logger = get_logger()
	store_row = get_item_shopify_store_row(item, store)

	# Get first variant ID
	variant_id = None
	if product.variants:
		variant_id = str(product.variants[0].id)

	update_data = {
		"shopify_product_id": str(product.id),
		"shopify_variant_id": variant_id,
		"last_sync_at": now_datetime(),
		"last_sync_hash": sync_hash,
	}

	# Only update image hash if provided
	if image_hash is not None:
		update_data["last_image_hash"] = image_hash

	if store_row:
		# Update existing row
		logger.info(
			"Updating existing Item Shopify Store row %s, item %s, store %s",
			store_row.name,
			item.name,
			store.name,
		)
		frappe.db.set_value(
			"Item Shopify Store",
			store_row.name,
			update_data,
			update_modified=False,
		)
	else:
		# Create new row
		logger.info("Creating new row on Item Shopify Store for item %s, store %s", item.name, store.name)
		row_data = {
			"shopify_store": store.name,
			"enabled": 1,
			**update_data,
		}
		item.reload()
		item.append("shopify_stores", row_data)
		item.flags.ignore_validate = True
		item.flags.ignore_mandatory = True
		item.save(ignore_permissions=True)


def build_product_payload(item, store) -> tuple:
	"""
	Build Shopify product JSON from Item using store's field mapping.

	Args:
		item: Item document
		store: Shopify Store document

	Returns:
		Tuple of (product_data, variant_data, metafields_data, category_value, collections_field)
	"""
	product_data = {"title": item.item_name or item.name}
	variant_data = {
		"sku": item.name,  # Use item_code as SKU (matches ecommerce_integrations pattern)
	}
	metafields_data = []
	category_value = None
	collections_field = None

	# Enable inventory tracking for stock items
	if item.is_stock_item:
		variant_data["inventory_management"] = "shopify"

	# Process field mappings
	for field_map in store.item_field_map:
		erpnext_field = field_map.erpnext_field
		field_type = field_map.shopify_field_type

		# Get value from item
		value = _get_field_value(item, store, erpnext_field, field_map.default_value)

		if field_type == "Standard Field":
			shopify_field = field_map.shopify_standard_field

			# Handle special fields that require separate processing
			if shopify_field == "category":
				if value:
					category_value = value
			elif shopify_field == "collections":
				# Store field name for collection sync (processed separately)
				collections_field = erpnext_field
			elif value is not None:
				if shopify_field in PRODUCT_STANDARD_FIELDS:
					product_data[shopify_field] = value
				elif shopify_field in VARIANT_STANDARD_FIELDS:
					variant_data[shopify_field] = value

		elif field_type == "Metafield" and value is not None:
			metafields_data.append(
				{
					"namespace": field_map.metafield_namespace,
					"key": field_map.metafield_key,
					"value": str(value),
					"type": field_map.metafield_type or "single_line_text_field",
				}
			)

	return product_data, variant_data, metafields_data, category_value, collections_field


def _get_field_value(item, store, field_name: str, default_value: str | None = None):
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


def get_item_price(item, store) -> float | None:
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


def _get_collection_values(item, field_name: str) -> list[str]:
	"""
	Get collection values from item field, handling different field types.

	Supports:
	- Table MultiSelect: Reads from child table
	- Link/Data/Select: Single value
	- Small Text: Comma-separated values

	Args:
		item: Item document
		field_name: Field name on Item to read

	Returns:
		List of collection names/values
	"""
	meta = frappe.get_meta("Item")
	field = meta.get_field(field_name)

	if not field:
		return []

	if field.fieldtype == "Table MultiSelect":
		# Get the link field from child table
		child_meta = frappe.get_meta(field.options)
		link_field = None
		for f in child_meta.fields:
			if f.fieldtype == "Link":
				link_field = f.fieldname
				break

		if not link_field:
			return []

		values = []
		for row in getattr(item, field_name, []) or []:
			val = getattr(row, link_field, None)
			if val:
				values.append(val)
		return values

	elif field.fieldtype in ("Link", "Data", "Select"):
		value = getattr(item, field_name, None)
		return [value] if value else []

	elif field.fieldtype in ("Small Text", "Text"):
		value = getattr(item, field_name, None)
		if value:
			return [v.strip() for v in value.split(",") if v.strip()]
		return []

	return []


def _create_shopify_collection_and_mapping(store, collection_name: str) -> str | None:
	"""
	Create a new collection on Shopify and add the mapping entry to the store.

	Args:
		store: Shopify Store document
		collection_name: Name for the new collection

	Returns:
		Shopify collection ID if successful, None otherwise
	"""
	try:
		# Create collection on Shopify
		collection = CustomCollection()
		collection.title = collection_name
		if not collection.save():
			frappe.log_error(
				title=f"Collection Creation Error - {store.name}",
				message=f"Failed to create collection '{collection_name}': {collection.errors.full_messages()}",
			)
			return None

		collection_id = str(collection.id)

		# Add mapping entry to store's collection_mapping table
		store.reload()
		store.append(
			"collection_mapping",
			{
				"field_value": collection_name,
				"shopify_collection_id": collection_id,
				"shopify_collection_title": collection_name,
			},
		)
		store.flags.ignore_validate = True
		store.flags.ignore_mandatory = True
		store.save(ignore_permissions=True)
		frappe.db.commit()

		create_shopify_log(
			status="Success",
			method="_create_shopify_collection_and_mapping",
			shopify_store=store.name,
			message=f"Auto-created Shopify collection '{collection_name}' (ID: {collection_id})",
			reference_doctype="Shopify Store",
			reference_name=store.name,
		)

		return collection_id

	except Exception as e:
		frappe.log_error(
			title=f"Collection Creation Error - {store.name}",
			message=f"Failed to create collection '{collection_name}': {e}\n{frappe.get_traceback()}",
		)
		return None


def _sync_product_collections(product_id: str, item, store, collections_field: str):
	"""
	Sync item to Shopify collections based on field values and collection mapping.

	Uses Shopify SDK Collect resource for all API operations.
	Adds product to new collections and removes from collections no longer assigned.
	Auto-creates missing collections on Shopify and adds mapping entries.

	Args:
		product_id: Shopify product ID
		item: Item document
		store: Shopify Store document
		collections_field: Name of the field on Item containing collection values
	"""
	if not collections_field:
		return

	# Get collection values from item (handles Table MultiSelect, Link, Data, etc.)
	collection_values = _get_collection_values(item, collections_field)

	if not collection_values:
		return

	# Build lookup from collection mapping table
	# Key: field_value, Value: shopify_collection_id
	collection_lookup = {}
	for mapping in store.collection_mapping or []:
		collection_id = mapping.shopify_collection_id
		# Extract numeric ID from GID format if needed
		if collection_id and collection_id.startswith("gid://"):
			collection_id = collection_id.split("/")[-1]
		if collection_id:
			collection_lookup[mapping.field_value] = collection_id

	# Find target Shopify collection IDs based on item's collection values
	target_collection_ids = set()
	for value in collection_values:
		if value in collection_lookup:
			target_collection_ids.add(collection_lookup[value])
		elif store.auto_create_collections:
			# Auto-create missing collections only if enabled on store
			new_collection_id = _create_shopify_collection_and_mapping(store, value)
			if new_collection_id:
				target_collection_ids.add(new_collection_id)
				# Update local lookup for this sync cycle
				collection_lookup[value] = new_collection_id

	# Get current product-collection relationships using SDK
	try:
		current_collects = Collect.find(product_id=product_id)
	except Exception:
		current_collects = []

	current_collection_ids = {str(c.collection_id) for c in current_collects}

	# ADD to new collections
	for collection_id in target_collection_ids - current_collection_ids:
		try:
			collect = Collect()
			collect.product_id = int(product_id)
			collect.collection_id = int(collection_id)
			if not collect.save():
				frappe.log_error(
					title=f"Collection Sync Error - {store.name}",
					message=f"Failed to add product {product_id} to collection {collection_id}: {collect.errors.full_messages()}",
				)
		except Exception as e:
			# Log but don't fail - collection might be a smart collection (403 error)
			frappe.log_error(
				title=f"Collection Sync Warning - {store.name}",
				message=f"Could not add product {product_id} to collection {collection_id}: {e}",
			)

	# REMOVE from collections no longer in item
	for collection_id in current_collection_ids - target_collection_ids:
		for collect in current_collects:
			if str(collect.collection_id) == collection_id:
				try:
					collect.destroy()
				except Exception as e:
					frappe.log_error(
						title=f"Collection Remove Warning - {store.name}",
						message=f"Could not remove product {product_id} from collection {collection_id}: {e}",
					)


def compute_sync_hash(item, store) -> str:
	"""
	Compute hash of item fields for change detection.

	Includes mapped fields and image (if image sync is enabled).

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

	# Include image in hash if image sync is enabled
	if store.enable_image_sync and item.image:
		file_path = _get_item_image_path(item)
		if file_path:
			hash_data["_image_hash"] = _compute_image_hash(file_path)
		else:
			hash_data["_image_hash"] = ""
	elif store.enable_image_sync:
		# No image set
		hash_data["_image_hash"] = ""

	# Include collection values in hash if collections mapping exists
	for field_map in store.item_field_map:
		if (
			field_map.shopify_field_type == "Standard Field"
			and field_map.shopify_standard_field == "collections"
		):
			collection_values = _get_collection_values(item, field_map.erpnext_field)
			hash_data["_collections"] = ",".join(sorted(collection_values))
			break

	# Create deterministic hash
	hash_str = json.dumps(hash_data, sort_keys=True)
	return hashlib.md5(hash_str.encode()).hexdigest()


def _get_item_image_path(item) -> str | None:
	"""
	Get the file path for item's primary image.

	Args:
		item: Item document

	Returns:
		Absolute file path or None
	"""
	if not item.image:
		return None

	# Item.image contains URL like /files/item-image.jpg or /private/files/item-image.jpg
	image_url = item.image

	# Determine if public or private file
	if image_url.startswith("/private/files/"):
		file_path = frappe.get_site_path("private", "files", image_url.replace("/private/files/", ""))
	elif image_url.startswith("/files/"):
		file_path = frappe.get_site_path("public", "files", image_url.replace("/files/", ""))
	else:
		# Could be full URL or other format
		return None

	if os.path.exists(file_path):
		return file_path

	return None


def _compute_image_hash(file_path: str) -> str:
	"""
	Compute MD5 hash of image file content.

	Args:
		file_path: Absolute path to image file

	Returns:
		MD5 hash string
	"""
	hash_md5 = hashlib.md5()
	with open(file_path, "rb") as f:
		for chunk in iter(lambda: f.read(4096), b""):
			hash_md5.update(chunk)
	return hash_md5.hexdigest()


def _get_image_data_and_hash(item) -> tuple[str | None, str | None, str | None]:
	"""
	Get base64 encoded image data and hash for an item.

	Args:
		item: Item document

	Returns:
		Tuple of (base64_data, image_hash, filename) or (None, None, None)
	"""
	file_path = _get_item_image_path(item)
	if not file_path:
		return None, None, None

	try:
		image_hash = _compute_image_hash(file_path)

		with open(file_path, "rb") as f:
			image_data = base64.b64encode(f.read()).decode("utf-8")

		filename = os.path.basename(file_path)
		return image_data, image_hash, filename
	except Exception:
		frappe.log_error(
			title="Image Read Error",
			message=f"Failed to read image file: {file_path}\n{frappe.get_traceback()}",
		)
		return None, None, None


def _sync_product_image(product_id: str, image_data: str, filename: str) -> bool:
	"""
	Upload image to Shopify product using base64 attachment.

	Args:
		product_id: Shopify product ID
		image_data: Base64 encoded image data
		filename: Image filename

	Returns:
		True if successful, False otherwise
	"""
	logger = get_logger()

	# Create new image
	image = Image()
	image.product_id = product_id
	image.attachment = image_data
	image.filename = filename

	if not image.save():
		logger.error("Failed to upload image for product %s: %s", product_id, image.errors.full_messages())
		raise Exception(f"Failed to upload image for product {product_id}: {image.errors.full_messages()}")

	# Delete existing images to avoid duplicates, but skip the newly uploaded one
	existing_images = Image.find(product_id=product_id)
	for img in existing_images:
		if img.id != image.id:
			img.destroy()

	return True


def _sync_item_image_to_shopify(item, store, product_id: str, store_row, force: bool = False) -> str | None:
	"""
	Sync item image to Shopify product if image has changed.

	Args:
		item: Item document
		store: Shopify Store document
		product_id: Shopify product ID
		store_row: Item Shopify Store row (or None)
		force: If True, skip change detection and force sync

	Returns:
		New image hash if synced, None otherwise
	"""
	image_data, image_hash, filename = _get_image_data_and_hash(item)

	if not image_data:
		return None

	# Check if image has changed (skip if force=True)
	last_image_hash = store_row.last_image_hash if store_row else None
	if not force and last_image_hash == image_hash:
		# Image unchanged, return existing hash
		return image_hash

	try:
		_sync_product_image(str(product_id), image_data, filename)
		return image_hash
	except Exception:
		frappe.log_error(
			title=f"Shopify Image Sync Error - {store.name}",
			message=f"Failed to sync image for item {item.name}\n{frappe.get_traceback()}",
		)
		return None


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
		# TODO: Optimize performance of this query and the logic
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

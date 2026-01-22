# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cstr

from nexwave_shopify_connector.nexwave_shopify.utils import create_shopify_log
from nexwave_shopify_connector.utils.logger import get_logger


def sync_fulfillment(payload: dict, request_id: str | None = None, shopify_store: str | None = None):
	"""
	Webhook handler for orders/fulfilled and orders/partially_fulfilled events.

	Creates Delivery Notes for each fulfillment in the order.

	Args:
		payload: Shopify order data with fulfillments
		request_id: Log entry name for tracking
		shopify_store: Shopify Store name
	"""
	logger = get_logger()
	logger.info(
		"Processing fulfillment webhook for order %s, store: %s",
		payload.get("id"),
		shopify_store,
	)
	frappe.flags.request_id = request_id

	# Set user context for permission checks (webhook runs as Guest)
	frappe.set_user("Administrator")

	store = frappe.get_doc("Shopify Store", shopify_store)

	try:
		result = create_delivery_notes_from_fulfillments(payload, store)

		if result["created"]:
			logger.info("Created Delivery Notes for fulfillment: %s", result["created"])
			message = f"Created {len(result['created'])} Delivery Note(s): {', '.join(result['created'])}"
			if result.get("failed"):
				message += f". Failed to create {result['failed']} Delivery Note(s) - check item mapping or warehouse configuration."
			create_shopify_log(
				status="Success" if not result.get("failed") else "Warning",
				message=message,
				shopify_store=shopify_store,
				reference_doctype="Sales Order",
				reference_name=result.get("sales_order"),
			)
		elif result.get("failed"):
			logger.warning("Failed to create Delivery Notes for fulfillment: %s", result["failed"])
			create_shopify_log(
				status="Error",
				message=f"Failed to create {result['failed']} Delivery Note(s) - check item mapping or warehouse configuration",
				shopify_store=shopify_store,
				reference_doctype="Sales Order",
				reference_name=result.get("sales_order"),
			)
		elif result["skipped"]:
			logger.info("Skipped fulfillment(s): %s", result["skipped"])
			create_shopify_log(
				status="Warning",
				message=f"Skipped {result['skipped']} fulfillment(s) - already processed",
				shopify_store=shopify_store,
			)
		else:
			logger.warning("No fulfillments to process: %s", result.get("message"))
			create_shopify_log(
				status="Warning",
				message=result.get("message", "No fulfillments to process"),
				shopify_store=shopify_store,
			)

	except Exception as e:
		frappe.db.rollback()
		logger.error(
			"Error processing fulfillment for order %s: %s",
			payload.get("id"),
			str(e),
			exc_info=True,
		)
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.commit()
		raise


def create_delivery_notes_from_fulfillments(order: dict, store) -> dict:
	"""
	Create Delivery Notes from Shopify order fulfillments.

	Args:
		order: Shopify order data containing fulfillments
		store: Shopify Store document

	Returns:
		Dict with results: {"created": [dn_names], "skipped": count, "failed": count, "sales_order": so_name, "message": str}
	"""
	logger = get_logger()
	shopify_order_id = cstr(order.get("id"))

	# Find the Sales Order
	so_name = frappe.db.get_value(
		"Sales Order",
		{"shopify_order_id": shopify_order_id, "docstatus": 1},
		"name",
	)

	if not so_name:
		# Check if SO exists but is not submitted
		draft_so = frappe.db.get_value(
			"Sales Order",
			{"shopify_order_id": shopify_order_id, "docstatus": 0},
			"name",
		)
		if draft_so:
			logger.warning(
				"Sales Order %s exists but is not submitted, cannot create Delivery Note",
				draft_so,
			)
			return {
				"created": [],
				"skipped": 0,
				"failed": 0,
				"sales_order": None,
				"message": f"Sales Order {draft_so} is not submitted",
			}

		logger.warning(
			"No Sales Order found for Shopify Order ID %s",
			shopify_order_id,
		)
		return {
			"created": [],
			"skipped": 0,
			"failed": 0,
			"sales_order": None,
			"message": f"Sales Order not found for Shopify Order {shopify_order_id}",
		}

	so = frappe.get_doc("Sales Order", so_name)

	fulfillments = order.get("fulfillments", [])
	if not fulfillments:
		logger.info("No fulfillments in order %s", shopify_order_id)
		return {
			"created": [],
			"skipped": 0,
			"failed": 0,
			"sales_order": so_name,
			"message": "No fulfillments in order",
		}

	created_dns = []
	skipped_count = 0
	failed_count = 0

	for fulfillment in fulfillments:
		fulfillment_id = cstr(fulfillment.get("id"))

		# Check if DN already exists for this fulfillment (deduplication)
		existing_dn = frappe.db.get_value(
			"Delivery Note",
			{"shopify_fulfillment_id": fulfillment_id},
			"name",
		)

		if existing_dn:
			logger.info(
				"Delivery Note %s already exists for fulfillment %s, skipping",
				existing_dn,
				fulfillment_id,
			)
			skipped_count += 1
			continue

		# Only process successful fulfillments
		status = fulfillment.get("status", "").lower()
		if status not in ("success", ""):
			logger.info(
				"Skipping fulfillment %s with status %s",
				fulfillment_id,
				status,
			)
			continue

		dn_name = _create_delivery_note_from_fulfillment(
			fulfillment=fulfillment,
			so=so,
			store=store,
			shopify_order_id=shopify_order_id,
			order=order,
		)

		if dn_name:
			created_dns.append(dn_name)
			logger.info(
				"Created Delivery Note %s for fulfillment %s",
				dn_name,
				fulfillment_id,
			)
		else:
			failed_count += 1
			logger.warning(
				"Failed to create Delivery Note for fulfillment %s - check item mapping or warehouse configuration",
				fulfillment_id,
			)

	# Update fulfillment status on Sales Order
	fulfillment_status = order.get("fulfillment_status") or "unfulfilled"
	frappe.db.set_value("Sales Order", so_name, "shopify_fulfillment_status", fulfillment_status)

	return {
		"created": created_dns,
		"skipped": skipped_count,
		"failed": failed_count,
		"sales_order": so_name,
		"message": None,
	}


def _create_delivery_note_from_fulfillment(
	fulfillment: dict,
	so,
	store,
	shopify_order_id: str,
	order: dict,
) -> str | None:
	"""
	Create a single Delivery Note from a Shopify fulfillment.

	Args:
		fulfillment: Shopify fulfillment data
		so: Sales Order document
		store: Shopify Store document
		shopify_order_id: Shopify order ID
		order: Full Shopify order data

	Returns:
		Delivery Note name if created, None otherwise
	"""
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	logger = get_logger()
	fulfillment_id = cstr(fulfillment.get("id"))
	location_id = cstr(fulfillment.get("location_id") or "")
	line_items = fulfillment.get("line_items", [])

	logger.info(
		"Creating DN from fulfillment %s: location_id=%s, line_items_count=%s, SO=%s",
		fulfillment_id,
		location_id,
		len(line_items),
		so.name,
	)

	if not line_items:
		logger.warning("No line items in fulfillment %s", fulfillment_id)
		return None

	# Create DN from SO (skip item mapping so we can manually add fulfillment items)
	logger.info("Creating DN from SO %s (skip_item_mapping=True)", so.name)
	dn = make_delivery_note(so.name, kwargs={"skip_item_mapping": True})
	logger.info("DN created from SO, customer=%s", dn.customer)

	# Set Shopify fields
	dn.shopify_store = store.name
	dn.shopify_order_id = shopify_order_id
	dn.shopify_order_number = order.get("name")
	dn.shopify_fulfillment_id = fulfillment_id

	# Set naming series if configured
	if store.delivery_note_series:
		dn.naming_series = store.delivery_note_series

	# Set cost center if configured
	if store.cost_center:
		dn.cost_center = store.cost_center

	# Get warehouse for the location
	warehouse = _get_warehouse_for_location(store, location_id)
	logger.info("Resolved warehouse for location %s: %s", location_id, warehouse)

	# Build items from fulfillment line items
	logger.info(
		"Building DN items from %s fulfillment line items, SO has %s items",
		len(line_items),
		len(so.items),
	)
	dn_items = _get_fulfillment_items(
		so_items=so.items,
		fulfillment_items=line_items,
		warehouse=warehouse,
		store=store,
	)

	# Add shipping items if configured
	if store.add_shipping_as_item and store.shipping_item:
		for so_item in so.items:
			if so_item.item_code == store.shipping_item:
				dn_items.append({
					"item_code": so_item.item_code,
					"item_name": so_item.item_name,
					"description": so_item.description,
					"qty": so_item.qty,
					"rate": so_item.rate,
					"warehouse": warehouse or store.warehouse,
					"against_sales_order": so_item.parent,
					"so_detail": so_item.name,
					"cost_center": store.cost_center,
				})
				logger.info("Added shipping item %s to DN", so_item.item_code)

	if not dn_items:
		logger.warning(
			"No matching items found for fulfillment %s",
			fulfillment_id,
		)
		return None

	logger.info("Built %s DN items from fulfillment", len(dn_items))
	for idx, item in enumerate(dn_items):
		logger.info(
			"  DN item %s: item_code=%s, qty=%s, warehouse=%s, so_detail=%s",
			idx + 1,
			item.get("item_code"),
			item.get("qty"),
			item.get("warehouse"),
			item.get("so_detail"),
		)

	dn.items = []
	for item in dn_items:
		dn.append("items", item)

	logger.info("Inserting and submitting DN for fulfillment %s", fulfillment_id)
	dn.flags.ignore_mandatory = True
	dn.insert(ignore_permissions=True)
	dn.submit()
	logger.info("DN %s created and submitted successfully", dn.name)

	# Add tracking info as comment (must be after insert/submit)
	tracking_info = _get_tracking_info(fulfillment)
	if tracking_info:
		logger.info("Adding tracking info to DN %s: %s", dn.name, tracking_info[:100])
		try:
			dn.add_comment(text=tracking_info, comment_type="Comment")
		except Exception as e:
			logger.warning("Failed to add tracking comment to DN %s: %s", dn.name, str(e))

	return dn.name


def _get_fulfillment_items(
	so_items: list,
	fulfillment_items: list,
	warehouse: str,
	store,
) -> list:
	"""
	Match fulfillment line items to Sales Order items and build DN item list.

	Args:
		so_items: Sales Order items
		fulfillment_items: Shopify fulfillment line items
		warehouse: Warehouse to set on items
		store: Shopify Store document

	Returns:
		List of item dicts for Delivery Note
	"""
	logger = get_logger()
	dn_items = []

	# Build SKU to SO item mapping
	so_item_by_sku = {}
	for so_item in so_items:
		item_code = so_item.item_code
		# Get the SKU (item_code is used as SKU in the order sync)
		so_item_by_sku[item_code] = so_item

	for f_item in fulfillment_items:
		sku = f_item.get("sku")
		if not sku:
			# Try variant ID or product ID as fallback (matching order.py logic)
			sku = cstr(f_item.get("variant_id") or f_item.get("product_id"))

		qty = f_item.get("quantity", 1)

		# Find matching SO item
		if sku not in so_item_by_sku:
			# Try to find by item_code lookup
			item_code = frappe.db.get_value("Item", {"name": sku}) or frappe.db.get_value(
				"Item", {"item_code": sku}
			)
			if item_code and item_code in so_item_by_sku:
				sku = item_code
			else:
				logger.warning(
					"SKU %s from fulfillment not found in Sales Order items",
					sku,
				)
				continue

		so_item = so_item_by_sku[sku]

		dn_items.append(
			{
				"item_code": so_item.item_code,
				"item_name": so_item.item_name,
				"description": so_item.description,
				"qty": qty,
				"rate": so_item.rate,
				"warehouse": warehouse or store.warehouse,
				"against_sales_order": so_item.parent,
				"so_detail": so_item.name,
				"cost_center": store.cost_center,
			}
		)

	return dn_items


def _get_warehouse_for_location(store, location_id: str) -> str:
	"""
	Get ERPNext warehouse for a Shopify location ID.

	Args:
		store: Shopify Store document
		location_id: Shopify location ID

	Returns:
		Warehouse name, falls back to store default warehouse
	"""
	if not location_id:
		return store.warehouse

	# Look up in warehouse mapping
	for mapping in store.warehouse_mapping or []:
		if mapping.shopify_location_id == location_id:
			return mapping.erpnext_warehouse

	# Fallback to default warehouse
	return store.warehouse


def _get_tracking_info(fulfillment: dict) -> str | None:
	"""
	Build tracking information string from fulfillment data.

	Args:
		fulfillment: Shopify fulfillment data

	Returns:
		Formatted tracking info string or None
	"""
	tracking_company = fulfillment.get("tracking_company")
	tracking_numbers = fulfillment.get("tracking_numbers") or []
	tracking_urls = fulfillment.get("tracking_urls") or []

	if not tracking_company and not tracking_numbers:
		return None

	parts = []
	if tracking_company:
		parts.append(f"Carrier: {tracking_company}")

	if tracking_numbers:
		parts.append(f"Tracking Number(s): {', '.join(tracking_numbers)}")

	if tracking_urls:
		parts.append(f"Tracking URL(s): {', '.join(tracking_urls)}")

	return "\n".join(parts)

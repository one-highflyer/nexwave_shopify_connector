# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

from typing import Optional

import frappe
import pytz
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, cstr, flt, get_datetime, get_system_timezone, getdate, now, nowdate
from shopify.collection import PaginatedIterator
from shopify.resources import Order
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.utils import create_shopify_log
from nexwave_shopify_connector.utils.logger import get_logger

# =============================================================================
# Core Shared Function
# =============================================================================


def _process_order(order: dict, store, request_id: str | None = None) -> str | None:
	"""
	Core order sync logic - creates Sales Order from Shopify order data.

	This is the shared function used by both webhook handlers and manual sync.

	Args:
		order: Shopify order data (dict)
		store: Shopify Store document
		request_id: Optional log entry name for tracking

	Returns:
		Sales Order name if created, None if skipped (duplicate)
	"""
	logger = get_logger()
	logger.info(
		"Processing order: %s for Shopify Store: %s, request ID: %s", order.get("id"), store.name, request_id
	)
	# Check for duplicate
	if frappe.db.get_value("Sales Order", filters={"shopify_order_id": cstr(order.get("id"))}):
		logger.info("Order already exists, skipping: %s", order.get("id"))
		return None

	# Sync customer and addresses
	customer_name, contact_name, billing_addr, shipping_addr = _sync_customer(order, store)

	# Create Sales Order
	so = _create_sales_order(order, store, customer_name, contact_name, billing_addr, shipping_addr)

	logger.info(
		"Sales Order created: %s for Shopify Order ID: %s, financial status: %s",
		so.name,
		order.get("id"),
		order.get("financial_status"),
	)

	# Handle prepaid orders
	if order.get("financial_status") == "paid" and store.auto_submit_sales_order:
		so.submit()
		logger.info("Sales Order submitted: %s for Shopify Order ID: %s", so.name, order.get("id"))

		if store.auto_create_invoice:
			si = _create_sales_invoice(so, order, store)
			logger.info("Sales Invoice created: %s for Shopify Order ID: %s", si.name, order.get("id"))

			if store.auto_create_payment_entry and si and si.grand_total > 0:
				_create_payment_entry(si, store, getdate(order.get("created_at")))
				logger.info("Payment Entry created for Shopify Order ID: %s", order.get("id"))

	logger.info("Sales Order created: %s for Shopify Order ID: %s", so.name, order.get("id"))
	return so.name


# =============================================================================
# Webhook Handlers
# =============================================================================


def sync_sales_order(payload: dict, request_id: str | None = None, shopify_store: str | None = None):
	"""
	Webhook handler for orders/create event.

	Creates a Sales Order from Shopify order data.
	Optionally creates Sales Invoice and Payment Entry for prepaid orders.

	Args:
		payload: Shopify order data
		request_id: Log entry name for tracking
		shopify_store: Shopify Store name
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	store = frappe.get_doc("Shopify Store", shopify_store)

	# Check for duplicate first
	if frappe.db.get_value("Sales Order", filters={"shopify_order_id": cstr(payload["id"])}):
		create_shopify_log(
			status="Warning",
			message="Sales order already exists, not synced",
			shopify_store=shopify_store,
		)
		return

	try:
		so_name = _process_order(payload, store, request_id)

		if so_name:
			create_shopify_log(
				status="Success",
				message=f"Created Sales Order {so_name}",
				shopify_store=shopify_store,
				reference_doctype="Sales Order",
				reference_name=so_name,
			)
		else:
			create_shopify_log(
				status="Warning",
				message="Sales order already exists, not synced",
				shopify_store=shopify_store,
			)

	except Exception as e:
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.rollback()
		raise


def process_paid_order(payload: dict, request_id: str | None = None, shopify_store: str | None = None):
	"""
	Webhook handler for orders/paid event.

	For COD orders that are now paid - submits SO and creates SI/PE.

	Args:
		payload: Shopify order data
		request_id: Log entry name for tracking
		shopify_store: Shopify Store name
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	order = payload
	store = frappe.get_doc("Shopify Store", shopify_store)

	try:
		# Find existing Sales Order
		so_name = frappe.db.get_value("Sales Order", {"shopify_order_id": cstr(order["id"])})

		if not so_name:
			create_shopify_log(
				status="Warning",
				message="Sales Order not found for paid order",
				shopify_store=shopify_store,
			)
			return

		so = frappe.get_doc("Sales Order", so_name)

		# Update financial status
		frappe.db.set_value("Sales Order", so_name, "shopify_financial_status", "paid")

		# Submit if draft and auto-submit enabled
		if so.docstatus == 0 and store.auto_submit_sales_order:
			so.submit()

		# Create invoice if enabled and SO is submitted
		if store.auto_create_invoice and so.docstatus == 1 and not so.per_billed:
			si = _create_sales_invoice(so, order, store)

			if store.auto_create_payment_entry and si and si.grand_total > 0:
				_create_payment_entry(si, store, getdate(order.get("created_at")))

		create_shopify_log(
			status="Success",
			message=f"Processed paid order for {so.name}",
			shopify_store=shopify_store,
			reference_doctype="Sales Order",
			reference_name=so.name,
		)

	except Exception as e:
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.rollback()
		raise


def cancel_order(payload: dict, request_id: str | None = None, shopify_store: str | None = None):
	"""
	Webhook handler for orders/cancelled event.

	Cancels the Sales Order if no invoice or delivery note exists.

	Args:
		payload: Shopify order data
		request_id: Log entry name for tracking
		shopify_store: Shopify Store name
	"""
	frappe.set_user("Administrator")
	frappe.flags.request_id = request_id

	order = payload

	try:
		order_id = order["id"]
		order_status = order.get("financial_status", "cancelled")

		so_name = frappe.db.get_value("Sales Order", {"shopify_order_id": cstr(order_id)})

		if not so_name:
			create_shopify_log(
				status="Warning",
				message="Sales Order does not exist for cancellation",
				shopify_store=shopify_store,
			)
			return

		so = frappe.get_doc("Sales Order", so_name)

		# Check for linked documents
		sales_invoice = frappe.db.get_value("Sales Invoice", {"shopify_order_id": cstr(order_id)})
		delivery_notes = frappe.db.get_list("Delivery Note", filters={"shopify_order_id": cstr(order_id)})

		# Update status on linked docs
		if sales_invoice:
			frappe.db.set_value("Sales Invoice", sales_invoice, "shopify_financial_status", order_status)

		for dn in delivery_notes:
			frappe.db.set_value("Delivery Note", dn.name, "shopify_financial_status", order_status)

		# Cancel SO only if no linked docs and it's submitted
		if not sales_invoice and not delivery_notes and so.docstatus == 1:
			so.cancel()
		elif so.docstatus == 0:
			# Draft - just delete or update status
			frappe.db.set_value("Sales Order", so.name, "shopify_financial_status", "voided")
		else:
			# Has linked docs - just update status
			frappe.db.set_value("Sales Order", so.name, "shopify_financial_status", order_status)

		create_shopify_log(
			status="Success",
			message=f"Processed cancellation for {so.name}",
			shopify_store=shopify_store,
			reference_doctype="Sales Order",
			reference_name=so.name,
		)

	except Exception as e:
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.rollback()
		raise


# =============================================================================
# Manual Sync Functions
# =============================================================================


def sync_new_orders(shopify_store: str, from_date=None, to_date=None) -> dict:
	"""
	Fetch and sync all new orders from Shopify since last sync.

	Args:
		shopify_store: Shopify Store name
		from_date: Optional start date (defaults to last_order_sync or 30 days ago)
		to_date: Optional end date (defaults to now)

	Returns:
		Dict with counts: {"synced": n, "skipped": n, "errors": n}
	"""
	logger = get_logger()
	logger.info(
		"Syncing new orders for Shopify Store: %s, from: %s, to: %s", shopify_store, from_date, to_date
	)
	frappe.set_user("Administrator")
	store = frappe.get_doc("Shopify Store", shopify_store)

	# Determine sync window
	if not from_date:
		from_date = store.last_order_sync or add_days(nowdate(), -30)

	if not to_date:
		to_date = now()

	# Convert to ISO format for Shopify API using site's timezone
	site_timezone = pytz.timezone(get_system_timezone())
	from_time_iso = site_timezone.localize(get_datetime(from_date)).isoformat()
	to_time_iso = site_timezone.localize(get_datetime(to_date)).isoformat()

	synced, skipped, errors = 0, 0, 0

	api_version = store.api_version or DEFAULT_API_VERSION
	auth_details = (store.shop_domain, api_version, store.get_password("access_token"))

	with Session.temp(*auth_details):
		orders_iter = PaginatedIterator(
			Order.find(created_at_min=from_time_iso, created_at_max=to_time_iso, limit=250)
		)

		for orders in orders_iter:
			for order in orders:
				order_data = order.to_dict()
				try:
					logger.info("Processing order: %s for Shopify Store: %s", order.id, shopify_store)
					result = _process_order(order_data, store)
					if result:
						synced += 1
						create_shopify_log(
							status="Success",
							message=f"Created Sales Order {result}",
							shopify_store=shopify_store,
							request_data=order_data,
							reference_doctype="Shopify Store",
							reference_name=shopify_store,
						)
						logger.info("Order synced: %s for Shopify Store: %s", order.id, shopify_store)
					else:
						skipped += 1
						create_shopify_log(
							status="Warning",
							message=f"Order {order.name} already exists, skipped",
							shopify_store=shopify_store,
							request_data=order_data,
							reference_doctype="Shopify Store",
							reference_name=shopify_store,
						)
						logger.info("Order skipped: %s for Shopify Store: %s", order.id, shopify_store)
				except Exception as e:
					errors += 1
					create_shopify_log(
						status="Error",
						message=str(e),
						exception=frappe.get_traceback(),
						shopify_store=shopify_store,
						request_data=order_data,
						reference_doctype="Shopify Store",
						reference_name=shopify_store,
					)
					frappe.log_error(
						title=f"Shopify Order Sync Error - {store.name}",
						message=f"Order ID: {order.id}\n\n{frappe.get_traceback()}",
						reference_doctype="Shopify Store",
						reference_name=shopify_store,
					)
					logger.error(
						"Error processing order: %s for Shopify Store: %s, Error: %s",
						order.id,
						shopify_store,
						str(e),
						exc_info=True,
					)

	# Only update last sync timestamp if no errors occurred
	if errors == 0:
		frappe.db.set_value("Shopify Store", store.name, "last_order_sync", to_date)
		logger.info("Last order sync timestamp updated: %s for Shopify Store: %s", to_date, shopify_store)

	return {"synced": synced, "skipped": skipped, "errors": errors}


# =============================================================================
# Helper Functions
# =============================================================================


def _sync_customer(order: dict, store) -> tuple[str, str | None, str | None, str | None]:
	"""
	Sync customer and addresses from Shopify order.

	Args:
		order: Shopify order data
		store: Shopify Store document

	Returns:
		Tuple of (customer_name, contact_name, billing_address_name, shipping_address_name)
	"""
	logger = get_logger()
	logger.info("Syncing customer for order: %s", order.get("id"))

	# Log incoming order data for debugging
	logger.info(
		"Order data - email: %s, customer_id: %s",
		order.get("email"),
		order.get("customer", {}).get("id") if order.get("customer") else None,
	)
	logger.info("Order billing_address: %s", order.get("billing_address"))
	logger.info("Order shipping_address: %s", order.get("shipping_address"))

	shopify_customer = order.get("customer") or {}
	customer_id = shopify_customer.get("id")

	# Enrich customer with order-level data (addresses and email)
	# This is critical because the order.customer object often has incomplete data
	if not shopify_customer.get("email") and order.get("email"):
		shopify_customer["email"] = order.get("email")
	shopify_customer["billing_address"] = order.get("billing_address") or {}
	shopify_customer["shipping_address"] = order.get("shipping_address") or {}

	logger.info(
		"Customer data after enrichment - first: %s, last: %s, email: %s",
		shopify_customer.get("first_name"),
		shopify_customer.get("last_name"),
		shopify_customer.get("email"),
	)

	if not customer_id:
		# No customer in order - use default customer
		logger.warning("No customer in order, using default customer: %s", store.default_customer)
		customer_name = store.default_customer
		# Still create contact from billing address if available
		billing_address = order.get("billing_address") or {}
		contact_data = {
			"email": order.get("email") or billing_address.get("email"),
			"phone": billing_address.get("phone"),
			"first_name": billing_address.get("first_name"),
			"last_name": billing_address.get("last_name"),
		}
		contact_name = _create_contact(contact_data, customer_name)
		billing_addr, shipping_addr = _sync_addresses(order, customer_name)
		return customer_name, contact_name, billing_addr, shipping_addr

	# Check if customer already exists by shopify_customer_id
	existing_customer = frappe.db.get_value("Customer", {"shopify_customer_id": cstr(customer_id)})

	if existing_customer:
		logger.info(
			"Customer already exists (by shopify_customer_id), updating addresses: %s", existing_customer
		)
		customer_name = existing_customer
		# Sync addresses
		billing_addr, shipping_addr = _sync_addresses(order, customer_name)
		# Get or create contact for existing customer
		contact_name = _create_contact(shopify_customer, customer_name)
		return customer_name, contact_name, billing_addr, shipping_addr

	# Try to find existing customer by email via Contact
	email = shopify_customer.get("email")
	if email:
		contacts = frappe.get_all(
			"Contact",
			filters=[
				["Contact Email", "email_id", "=", email],
			],
			fields=["name"],
		)

		if contacts:
			contact_names = [c.name for c in contacts]
			linked_customers = frappe.get_all(
				"Dynamic Link",
				filters={
					"parent": ["in", contact_names],
					"parenttype": "Contact",
					"link_doctype": "Customer",
				},
				fields=["link_name"],
			)

			if len(linked_customers) == 1:
				# Exact match - use customer and set shopify_customer_id
				customer_name = linked_customers[0].link_name
				frappe.db.set_value("Customer", customer_name, "shopify_customer_id", cstr(customer_id))
				logger.info(
					"Customer found by email (exact match), set shopify_customer_id: %s", customer_name
				)
				billing_addr, shipping_addr = _sync_addresses(order, customer_name)
				contact_name = _create_contact(shopify_customer, customer_name)
				return customer_name, contact_name, billing_addr, shipping_addr
			elif len(linked_customers) > 1:
				# Multiple matches - use first, don't set shopify_customer_id
				customer_name = linked_customers[0].link_name
				logger.info(
					"Multiple customers found by email, using first match (not setting shopify_customer_id): %s",
					customer_name,
				)
				billing_addr, shipping_addr = _sync_addresses(order, customer_name)
				contact_name = _create_contact(shopify_customer, customer_name)
				return customer_name, contact_name, billing_addr, shipping_addr

	# Note: We intentionally do NOT fetch from Customer API here.
	# The order payload contains all necessary customer data in billing/shipping addresses.
	# API fetch would only return default_address, which is less useful than order addresses.
	# (ecommerce_integrations also uses this approach)
	logger.info(
		"Using enriched order data for customer creation. first=%s, last=%s, email=%s, has_billing=%s, has_shipping=%s",
		shopify_customer.get("first_name"),
		shopify_customer.get("last_name"),
		shopify_customer.get("email"),
		bool(shopify_customer.get("billing_address")),
		bool(shopify_customer.get("shipping_address")),
	)

	# Create new customer
	customer_name = _create_customer(shopify_customer, store)
	billing_addr, shipping_addr = _sync_addresses(order, customer_name)
	contact_name = _create_contact(shopify_customer, customer_name)

	return customer_name, contact_name, billing_addr, shipping_addr


def _create_customer(shopify_customer: dict, store) -> str:
	"""Create a new Customer from Shopify data.

	Extracts customer name from multiple sources in order:
	1. Customer object (first_name, last_name)
	2. Billing address (first_name, last_name, name)
	3. Shipping address (first_name, last_name, name)
	4. Default address (first_name, last_name, name)
	5. Email
	6. Shopify Customer ID (final fallback)
	"""
	logger = get_logger()
	logger.info("Creating new customer from Shopify ID: %s", shopify_customer.get("id"))

	# Try customer-level first
	first_name = shopify_customer.get("first_name") or ""
	last_name = shopify_customer.get("last_name") or ""

	# Fallback 1: billing address
	billing = shopify_customer.get("billing_address") or {}
	if not first_name:
		first_name = billing.get("first_name") or ""
	if not last_name:
		last_name = billing.get("last_name") or ""
	# Try billing.name (full name field)
	if not first_name and billing.get("name"):
		parts = billing.get("name", "").split()
		first_name = parts[0] if parts else ""
		if not last_name and len(parts) > 1:
			last_name = " ".join(parts[1:])

	# Fallback 2: shipping address
	shipping = shopify_customer.get("shipping_address") or {}
	if not first_name:
		first_name = shipping.get("first_name") or ""
	if not last_name:
		last_name = shipping.get("last_name") or ""
	if not first_name and shipping.get("name"):
		parts = shipping.get("name", "").split()
		first_name = parts[0] if parts else ""
		if not last_name and len(parts) > 1:
			last_name = " ".join(parts[1:])

	# Fallback 3: default_address
	default_addr = shopify_customer.get("default_address") or {}
	if not first_name:
		first_name = default_addr.get("first_name") or ""
	if not last_name:
		last_name = default_addr.get("last_name") or ""
	if not first_name and default_addr.get("name"):
		parts = default_addr.get("name", "").split()
		first_name = parts[0] if parts else ""
		if not last_name and len(parts) > 1:
			last_name = " ".join(parts[1:])

	customer_name = f"{first_name} {last_name}".strip()

	# Fallback 4: email
	email = shopify_customer.get("email") or ""
	if not customer_name:
		customer_name = email

	# Final fallback: Shopify ID
	if not customer_name:
		customer_name = f"Shopify Customer {shopify_customer.get('id')}"
		logger.warning("No customer name found from any source, using ID fallback: %s", customer_name)

	logger.info(
		"Customer name resolved: %s (from sources: customer=%s/%s, billing=%s, shipping=%s, default=%s, email=%s)",
		customer_name,
		shopify_customer.get("first_name"),
		shopify_customer.get("last_name"),
		billing.get("name"),
		shipping.get("name"),
		default_addr.get("name"),
		email,
	)

	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_group": store.customer_group
			or frappe.db.get_single_value("Selling Settings", "customer_group"),
			"territory": frappe.db.get_single_value("Selling Settings", "territory"),
			"customer_type": "Individual",
			"shopify_customer_id": cstr(shopify_customer.get("id")),
		}
	)
	customer.flags.ignore_mandatory = True
	customer.insert(ignore_permissions=True)

	logger.info("Customer created: %s for Shopify Customer: %s", customer.name, shopify_customer.get("id"))
	return customer.name


def _sync_addresses(order: dict, customer_name: str) -> tuple[str | None, str | None]:
	"""Sync billing and shipping addresses from order.

	Returns:
		Tuple of (billing_address_name, shipping_address_name)
	"""
	logger = get_logger()
	logger.info("Syncing addresses for order: %s", order.get("id"))
	billing_address = order.get("billing_address")
	shipping_address = order.get("shipping_address")

	billing_address_name = None
	shipping_address_name = None

	if billing_address:
		billing_address_name = _create_or_update_address(billing_address, customer_name, "Billing")

	if shipping_address:
		shipping_address_name = _create_or_update_address(shipping_address, customer_name, "Shipping")

	return billing_address_name, shipping_address_name


def _find_existing_address(
	customer_name: str, address_title: str, address_type: str, address_line1: str, city: str, country: str
) -> str | None:
	"""Find existing address by content match."""
	addresses = frappe.get_all(
		"Address",
		filters=[
			["Dynamic Link", "link_doctype", "=", "Customer"],
			["Dynamic Link", "link_name", "=", customer_name],
			["address_title", "=", address_title],
			["address_type", "=", address_type],
			["address_line1", "=", address_line1],
			["city", "=", city],
			["country", "=", country],
		],
		pluck="name",
	)
	return addresses[0] if addresses else None


def _create_or_update_address(address_data: dict, customer_name: str, address_type: str) -> str | None:
	"""Create an address for a customer if it doesn't already exist."""
	logger = get_logger()
	if not address_data:
		logger.warning("No %s address data provided", address_type)
		return None

	# Validate required field
	if not address_data.get("address1"):
		logger.warning("No address1 in %s address data", address_type)
		return None

	# Build address fields
	address_title = cstr(address_data.get("name") or customer_name).strip()
	address_line1 = cstr(address_data.get("address1", "")).strip() or "-"
	city = cstr(address_data.get("city", "")).strip() or "-"
	country = cstr(address_data.get("country", "")).strip()

	# Check if address already exists
	existing = _find_existing_address(
		customer_name, address_title, address_type, address_line1, city, country
	)
	if existing:
		logger.info("Address already exists: %s", existing)
		return existing

	# Create new address
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": address_title,
			"address_type": address_type,
			"address_line1": address_line1,
			"address_line2": cstr(address_data.get("address2", "")).strip(),
			"city": city,
			"state": cstr(address_data.get("province", "")).strip(),
			"pincode": cstr(address_data.get("zip", "")).strip(),
			"country": country,
			"phone": cstr(address_data.get("phone", "")).strip(),
			"links": [{"link_doctype": "Customer", "link_name": customer_name}],
		}
	)
	address.flags.ignore_mandatory = True
	address.insert(ignore_permissions=True)
	logger.info("Address created: %s", address.name)
	return address.name


def _create_contact(shopify_customer: dict, customer_name: str) -> str | None:
	"""Create a contact for the customer if one doesn't already exist.

	Searches multiple sources for email, phone, and name:
	1. Customer object directly
	2. Billing address
	3. Shipping address
	4. Default address
	5. Customer name as fallback for first_name

	Returns:
		Contact name if created or found, None if no email/phone provided
	"""
	logger = get_logger()

	# Extract addresses for fallback lookups
	billing = shopify_customer.get("billing_address") or {}
	shipping = shopify_customer.get("shipping_address") or {}
	default_addr = shopify_customer.get("default_address") or {}

	# Try multiple sources for email
	email = (
		shopify_customer.get("email")
		or billing.get("email")
		or shipping.get("email")
		or default_addr.get("email")
	)

	# Try multiple sources for phone
	phone = (
		shopify_customer.get("phone")
		or billing.get("phone")
		or shipping.get("phone")
		or default_addr.get("phone")
	)

	logger.info(
		"Contact data sources - customer_email: %s, billing_phone: %s, shipping_phone: %s, default_phone: %s",
		shopify_customer.get("email"),
		billing.get("phone"),
		shipping.get("phone"),
		default_addr.get("phone"),
	)
	logger.info("Contact resolved - email: %s, phone: %s", email, phone)

	if not email and not phone:
		logger.warning("No email or phone found for contact creation, customer: %s", customer_name)
		return None

	# Check if contact with this email already exists for this customer
	if email:
		existing_contacts = frappe.get_all(
			"Contact",
			filters=[
				["Dynamic Link", "link_doctype", "=", "Customer"],
				["Dynamic Link", "link_name", "=", customer_name],
				["Contact Email", "email_id", "=", email],
			],
			limit=1,
		)
		if existing_contacts:
			logger.info("Contact already exists for customer: %s, email: %s", customer_name, email)
			return existing_contacts[0].name

	# Get name from multiple sources for contact
	first_name = (
		shopify_customer.get("first_name")
		or billing.get("first_name")
		or shipping.get("first_name")
		or default_addr.get("first_name")
	)
	last_name = (
		shopify_customer.get("last_name")
		or billing.get("last_name")
		or shipping.get("last_name")
		or default_addr.get("last_name")
	)

	# Try name field from addresses if first_name still empty
	if not first_name:
		name_field = billing.get("name") or shipping.get("name") or default_addr.get("name")
		if name_field:
			parts = name_field.split()
			first_name = parts[0] if parts else ""
			if not last_name and len(parts) > 1:
				last_name = " ".join(parts[1:])

	# Final fallback to customer name
	if not first_name:
		first_name = customer_name.split()[0] if customer_name else "Customer"

	logger.info(
		"Contact name resolved - first: %s, last: %s (from customer: %s/%s, billing: %s, shipping: %s)",
		first_name,
		last_name,
		shopify_customer.get("first_name"),
		shopify_customer.get("last_name"),
		billing.get("name"),
		shipping.get("name"),
	)

	# Create new contact
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first_name,
			"last_name": last_name or "",
			"links": [{"link_doctype": "Customer", "link_name": customer_name}],
		}
	)

	if email:
		contact.append("email_ids", {"email_id": email, "is_primary": 1})

	if phone:
		contact.append("phone_nos", {"phone": phone, "is_primary_phone": 1})

	contact.flags.ignore_mandatory = True
	contact.insert(ignore_permissions=True)

	logger.info("Contact created: %s for Shopify Customer: %s", contact.name, shopify_customer.get("id"))
	return contact.name


def _create_sales_order(
	order: dict,
	store,
	customer_name: str,
	contact_name: str | None = None,
	billing_address: str | None = None,
	shipping_address: str | None = None,
):
	"""
	Create a Sales Order from Shopify order data.

	Args:
		order: Shopify order data
		store: Shopify Store document
		customer_name: Customer to use
		contact_name: Contact to use (optional)
		billing_address: Billing address name (optional)
		shipping_address: Shipping address name (optional)

	Returns:
		Sales Order document
	"""
	# Get line items
	items = _get_order_items(order, store)

	# Get taxes
	taxes = _get_order_taxes(order, store, items)

	# Create Sales Order
	so = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"naming_series": store.sales_order_series or "SO-Shopify-",
			"shopify_store": store.name,
			"shopify_order_id": cstr(order.get("id")),
			"shopify_order_number": order.get("name"),
			"shopify_financial_status": order.get("financial_status"),
			"shopify_fulfillment_status": order.get("fulfillment_status") or "unfulfilled",
			"customer": customer_name,
			"contact_person": contact_name,
			"customer_address": billing_address,
			"shipping_address_name": shipping_address,
			"currency": order.get("currency"),
			"transaction_date": getdate(order.get("created_at")) or nowdate(),
			"delivery_date": getdate(order.get("created_at")) or nowdate(),
			"company": store.company,
			"ignore_pricing_rule": 1,
			"items": items,
			"taxes": taxes,
		}
	)

	so.flags.ignore_mandatory = True
	so.insert(ignore_permissions=True)

	# Add order note as comment
	if order.get("note"):
		so.add_comment(text=f"Order Note: {order.get('note')}")

	return so


def _get_order_items(order: dict, store) -> list:
	"""
	Map Shopify line items to Sales Order items.

	Raises exception if any SKU is not found.

	Args:
		order: Shopify order data
		store: Shopify Store document

	Returns:
		List of item dicts for Sales Order
	"""
	logger = get_logger()
	items = []
	line_items = order.get("line_items", [])
	taxes_inclusive = order.get("taxes_included", False)
	delivery_date = getdate(order.get("created_at")) or nowdate()

	for line_item in line_items:
		sku = line_item.get("sku")
		if not sku:
			# Try variant ID or product ID as fallback
			sku = cstr(line_item.get("variant_id") or line_item.get("product_id"))

		# Find item by SKU
		item_code = frappe.db.get_value("Item", {"name": sku})
		if not item_code:
			# Try item_code field
			item_code = frappe.db.get_value("Item", {"item_code": sku})

		if not item_code:
			logger.error(
				"Item with SKU '%s' not found. Order: %s, Line item: %s",
				sku,
				order.get("name"),
				line_item.get("title"),
			)
			raise ValueError(f"Item with SKU '{sku}' not found. Please create the item first.")

		# Calculate item price
		price = _get_item_price(line_item, taxes_inclusive)

		# Calculate per-item discount
		total_discount = _get_total_discount(line_item)
		qty = cint(line_item.get("quantity")) or 1
		per_item_discount = total_discount / qty if qty else 0

		items.append(
			{
				"item_code": item_code,
				"item_name": line_item.get("title") or line_item.get("name"),
				"rate": price,
				"qty": qty,
				"delivery_date": delivery_date,
				"warehouse": store.warehouse,
				"shopify_item_discount": per_item_discount,
			}
		)

	return items


def _get_item_price(line_item: dict, taxes_inclusive: bool) -> float:
	"""Calculate item price, handling discounts and tax-inclusive pricing."""
	price = flt(line_item.get("price"))
	qty = cint(line_item.get("quantity")) or 1

	# Remove line item level discounts
	total_discount = _get_total_discount(line_item)

	if not taxes_inclusive:
		return price - (total_discount / qty)

	# For tax-inclusive pricing, subtract taxes
	total_taxes = sum(flt(tax.get("price")) for tax in line_item.get("tax_lines", []))

	return price - (total_taxes + total_discount) / qty


def _get_total_discount(line_item: dict) -> float:
	"""Get total discount amount from line item."""
	discount_allocations = line_item.get("discount_allocations") or []
	return sum(flt(discount.get("amount")) for discount in discount_allocations)


def _get_order_taxes(order: dict, store, items: list) -> list:
	"""
	Build tax rows from Shopify order taxes.

	Args:
		order: Shopify order data
		store: Shopify Store document
		items: List of SO item dicts

	Returns:
		List of tax dicts for Sales Order
	"""
	taxes = []
	line_items = order.get("line_items", [])

	# Process line item taxes
	for line_item in line_items:
		for tax in line_item.get("tax_lines", []):
			tax_account = _get_tax_account(tax.get("title"), store, "sales_tax")
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": tax_account,
					"rate": flt(tax.get("rate")) * 100,
					"description": f"{tax.get('title')} - {flt(tax.get('rate')) * 100:.2f}%",
					"tax_amount": flt(tax.get("price")),
					"cost_center": store.cost_center,
					"dont_recompute_tax": 1,
				}
			)

	# Process shipping
	_add_shipping_charges(order, store, items, taxes)

	return taxes


def _add_shipping_charges(order: dict, store, items: list, taxes: list):
	"""Add shipping charges as item or tax based on store settings."""
	shipping_lines = order.get("shipping_lines", [])
	taxes_inclusive = order.get("taxes_included", False)
	delivery_date = items[-1]["delivery_date"] if items else nowdate()

	for shipping in shipping_lines:
		if not shipping.get("price"):
			continue

		# Calculate shipping amount
		shipping_discounts = shipping.get("discount_allocations") or []
		total_discount = sum(flt(d.get("amount")) for d in shipping_discounts)

		shipping_taxes = shipping.get("tax_lines") or []
		total_tax = sum(flt(t.get("price")) for t in shipping_taxes)

		shipping_amount = flt(shipping["price"]) - total_discount
		if taxes_inclusive:
			shipping_amount -= total_tax

		# Add as item or tax
		if store.add_shipping_as_item and store.shipping_item:
			items.append(
				{
					"item_code": store.shipping_item,
					"item_name": shipping.get("title") or "Shipping",
					"rate": shipping_amount,
					"qty": 1,
					"delivery_date": delivery_date,
					"warehouse": store.warehouse,
				}
			)
		else:
			tax_account = _get_tax_account(shipping.get("title"), store, "shipping")
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": tax_account,
					"description": shipping.get("title") or "Shipping",
					"tax_amount": shipping_amount,
					"cost_center": store.cost_center,
				}
			)

		# Add shipping taxes
		for tax in shipping_taxes:
			tax_account = _get_tax_account(tax.get("title"), store, "sales_tax")
			taxes.append(
				{
					"charge_type": "Actual",
					"account_head": tax_account,
					"rate": flt(tax.get("rate")) * 100,
					"description": f"{tax.get('title')} - {flt(tax.get('rate')) * 100:.2f}%",
					"tax_amount": flt(tax.get("price")),
					"cost_center": store.cost_center,
					"dont_recompute_tax": 1,
				}
			)


def _get_tax_account(tax_title: str, store, charge_type: str) -> str:
	"""
	Get tax account for a Shopify tax.

	Looks up in store's tax_accounts mapping, falls back to defaults.

	Args:
		tax_title: Shopify tax title
		store: Shopify Store document
		charge_type: 'sales_tax' or 'shipping'

	Returns:
		Account name
	"""
	# Look up in tax mapping
	for mapping in store.tax_accounts or []:
		if mapping.shopify_tax == tax_title:
			return mapping.tax_account

	# Fallback to defaults
	if charge_type == "shipping":
		if store.default_shipping_charges_account:
			return store.default_shipping_charges_account
	else:
		if store.default_sales_tax_account:
			return store.default_sales_tax_account

	frappe.throw(_("Tax Account not configured for Shopify tax: {0}").format(tax_title))


def _create_sales_invoice(so, order: dict, store) -> "Document | None":
	"""
	Create Sales Invoice from Sales Order.

	Args:
		so: Sales Order document
		order: Shopify order data
		store: Shopify Store document

	Returns:
		Sales Invoice document or None
	"""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	# Check if invoice already exists
	if frappe.db.get_value("Sales Invoice", {"shopify_order_id": cstr(order.get("id"))}):
		return None

	# SO must be submitted and not billed
	if so.docstatus != 1 or so.per_billed:
		return None

	posting_date = getdate(order.get("created_at")) or nowdate()

	si = make_sales_invoice(so.name, ignore_permissions=True)
	si.shopify_store = store.name
	si.shopify_order_id = cstr(order.get("id"))
	si.shopify_order_number = order.get("name")
	si.set_posting_time = 1
	si.posting_date = posting_date
	si.due_date = posting_date
	si.naming_series = store.sales_invoice_series or "SI-Shopify-"

	# Set cost center on items
	for item in si.items:
		item.cost_center = store.cost_center

	si.flags.ignore_mandatory = True
	si.insert(ignore_permissions=True)
	si.submit()

	return si


def _create_payment_entry(si, store, posting_date=None):
	"""
	Create Payment Entry against Sales Invoice.

	Args:
		si: Sales Invoice document
		store: Shopify Store document
		posting_date: Date for the payment entry
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	if not store.cash_bank_account:
		frappe.log_error(
			title="Shopify Payment Entry Error",
			message=f"Cash/Bank account not configured for store {store.name}",
		)
		return

	posting_date = posting_date or nowdate()

	pe = get_payment_entry(si.doctype, si.name, bank_account=store.cash_bank_account)
	pe.reference_no = si.name
	pe.posting_date = posting_date
	pe.reference_date = posting_date
	pe.flags.ignore_mandatory = True
	pe.insert(ignore_permissions=True)
	pe.submit()

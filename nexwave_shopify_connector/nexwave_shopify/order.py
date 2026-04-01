# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

from typing import Optional

import frappe
import pytz
from erpnext.accounts.utils import get_currency_precision
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, cstr, flt, get_datetime, get_system_timezone, getdate, now, nowdate
from shopify.collection import PaginatedIterator
from shopify.resources import Order
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.fulfillment import create_delivery_notes_from_fulfillments
from nexwave_shopify_connector.nexwave_shopify.tax import TaxBuilder, apply_rounding_adjustment
from nexwave_shopify_connector.nexwave_shopify.utils import create_shopify_log, sanitize_phone_number
from nexwave_shopify_connector.utils.logger import get_logger

# =============================================================================
# Core Shared Function
# =============================================================================


def _process_order(order: dict, store, request_id: str | None = None) -> str | None:
	"""
	Core order sync logic - creates Sales Order from Shopify order data.

	This is the shared function used by both webhook handlers and manual sync.

	Each phase (customer sync, SO creation, SO submission, invoice, payment,
	fulfillment) commits independently to prevent long-running transactions
	that cause database deadlocks under concurrent load. A failure in a later
	phase leaves earlier phases committed (partial state is acceptable — e.g.
	an SO without an SI is a normal ERPNext state).

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

	# Skip cancelled Shopify orders
	if order.get("cancelled_at"):
		logger.info("Skipping cancelled Shopify order: %s", order.get("id"))
		return None

	# Check for duplicate
	if frappe.db.get_value(
		"Sales Order", filters={"shopify_order_id": cstr(order.get("id")), "docstatus": ["!=", 2]}
	):
		logger.info("Order already exists, skipping: %s", order.get("id"))
		return None

	# Phase 1: Sync customer and addresses
	customer_name, contact_name, billing_addr, shipping_addr = _sync_customer(order, store)
	frappe.db.commit()

	# Phase 2: Create Sales Order
	so = _create_sales_order(order, store, customer_name, contact_name, billing_addr, shipping_addr)
	frappe.db.commit()

	logger.info(
		"Sales Order created: %s for Shopify Order ID: %s, financial status: %s",
		so.name,
		order.get("id"),
		order.get("financial_status"),
	)

	# Handle prepaid orders
	if order.get("financial_status") == "paid" and store.auto_submit_sales_order:
		# Phase 3: Submit Sales Order
		so.reload()  # Refresh after Phase 2 commit for check_if_latest()
		so.submit()
		frappe.db.commit()
		logger.info("Sales Order submitted: %s for Shopify Order ID: %s", so.name, order.get("id"))

		if store.auto_create_invoice:
			# Phase 4: Create and submit Sales Invoice
			so.reload()  # Refresh docstatus (now 1) after Phase 3 commit — needed for _create_sales_invoice guard check
			si = _create_sales_invoice(so, order, store)
			if si:
				frappe.db.commit()
				logger.info("Sales Invoice created: %s for Shopify Order ID: %s", si.name, order.get("id"))
			else:
				logger.info(
					"Sales Invoice not created for Shopify Order ID: %s — invoice may already exist "
					"or SO %s is not submitted/already billed",
					order.get("id"),
					so.name,
				)

			if store.auto_create_payment_entry and si and si.grand_total > 0:
				# Phase 5: Create Payment Entries
				_create_payment_entries(si, order, store, getdate(order.get("created_at")))
				frappe.db.commit()
				logger.info("Payment Entry created for Shopify Order ID: %s", order.get("id"))

		# Auto-create Delivery Notes if order is already fulfilled (best-effort, non-blocking)
		if store.enable_webhook_fulfillment and order.get("fulfillment_status") == "fulfilled":
			try:
				# Phase 6: Create Delivery Notes
				result = create_delivery_notes_from_fulfillments(order, store)
				if result.get("created"):
					frappe.db.commit()
					logger.info(
						"Auto-created Delivery Notes for pre-fulfilled order %s: %s",
						order.get("id"),
						result["created"],
					)
				elif result.get("failed"):
					frappe.db.rollback()
					logger.warning(
						"Failed to auto-create Delivery Notes for pre-fulfilled order %s: %s",
						order.get("id"),
						result.get("message"),
					)
				else:
					# Legitimate skip (no fulfillments, already exists, SO not submitted, etc.)
					frappe.db.commit()
					logger.info(
						"Skipped Delivery Note creation for pre-fulfilled order %s: %s",
						order.get("id"),
						result.get("message"),
					)
			except Exception as e:
				frappe.db.rollback()
				logger.error(
					"Failed to auto-create Delivery Notes for order %s: %s",
					order.get("id"),
					str(e),
					exc_info=True,
				)
				create_shopify_log(
					status="Error",
					message=f"Failed to auto-create Delivery Notes for pre-fulfilled order: {str(e)}",
					exception=frappe.get_traceback(),
					shopify_store=store.name,
					reference_doctype="Sales Order",
					reference_name=so.name,
				)

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
	logger = get_logger()
	logger.info(
		"[orders/create] Webhook received - order_id: %s, order_number: %s, store: %s, request_id: %s",
		payload.get("id"),
		payload.get("name"),
		shopify_store,
		request_id,
	)
	logger.info(
		"[orders/create] Order details - financial_status: %s, fulfillment_status: %s, total_price: %s, currency: %s",
		payload.get("financial_status"),
		payload.get("fulfillment_status"),
		payload.get("total_price"),
		payload.get("currency"),
	)
	frappe.flags.request_id = request_id

	# Set user context for permission checks (webhook runs as Guest)
	if frappe.session.user == "Guest":
		frappe.set_user("Administrator")
	else:
		logger.info(
			"[orders/create] Running as user %s, skipping elevation to Administrator", frappe.session.user
		)

	store = frappe.get_doc("Shopify Store", shopify_store)
	logger.info(
		"[orders/create] Store settings - auto_submit: %s, auto_invoice: %s, auto_payment: %s",
		store.auto_submit_sales_order,
		store.auto_create_invoice,
		store.auto_create_payment_entry,
	)

	# Check for duplicate first
	if frappe.db.get_value(
		"Sales Order", filters={"shopify_order_id": cstr(payload["id"]), "docstatus": ["!=", 2]}
	):
		logger.warning(
			"[orders/create] Duplicate detected - order %s already exists, skipping. Store: %s",
			payload["id"],
			shopify_store,
		)
		create_shopify_log(
			status="Warning",
			message="Sales order already exists, not synced",
			shopify_store=shopify_store,
		)
		return

	try:
		logger.info("[orders/create] Processing order %s", payload["id"])
		so_name = _process_order(payload, store, request_id)

		if so_name:
			logger.info(
				"[orders/create] Successfully created Sales Order %s for Shopify order %s",
				so_name,
				payload["id"],
			)
			create_shopify_log(
				status="Success",
				message=f"Created Sales Order {so_name}",
				shopify_store=shopify_store,
				request_data=payload,
				reference_doctype="Sales Order",
				reference_name=so_name,
			)
		else:
			logger.info("[orders/create] Order %s was skipped (duplicate or cancelled)", payload["id"])
			create_shopify_log(
				status="Warning",
				message="Sales order already exists, not synced",
				shopify_store=shopify_store,
			)

	except Exception as e:
		logger.error(
			"[orders/create] Error processing order %s: %s",
			payload["id"],
			str(e),
			exc_info=True,
		)
		frappe.db.rollback()
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.commit()
		raise


def process_paid_order(payload: dict, request_id: str | None = None, shopify_store: str | None = None):
	"""
	Webhook handler for orders/paid event.

	For COD orders that are now paid - submits SO and creates SI/PE.

	Each phase (financial status update, SO submission, invoice+payment) commits
	independently to prevent long-running transactions that cause database
	deadlocks under concurrent load.

	Args:
		payload: Shopify order data
		request_id: Log entry name for tracking
		shopify_store: Shopify Store name
	"""
	logger = get_logger()

	frappe.flags.request_id = request_id

	# Set user context for permission checks (webhook runs as Guest)
	if frappe.session.user == "Guest":
		frappe.set_user("Administrator")
	else:
		logger.info(
			"[orders/paid] Running as user %s, skipping elevation to Administrator", frappe.session.user
		)

	order = payload
	logger.info(
		"[orders/paid] Webhook received - order_id: %s, order_number: %s, store: %s, request_id: %s",
		order.get("id"),
		order.get("name"),
		shopify_store,
		request_id,
	)
	logger.info(
		"[orders/paid] Order details - financial_status: %s, total_price: %s, currency: %s",
		order.get("financial_status"),
		order.get("total_price"),
		order.get("currency"),
	)

	store = frappe.get_doc("Shopify Store", shopify_store)
	logger.info(
		"[orders/paid] Store settings - auto_submit: %s, auto_invoice: %s, auto_payment: %s",
		store.auto_submit_sales_order,
		store.auto_create_invoice,
		store.auto_create_payment_entry,
	)

	try:
		# Find existing Sales Order
		so_name = frappe.db.get_value("Sales Order", {"shopify_order_id": cstr(order["id"])})

		if not so_name:
			logger.warning(
				"[orders/paid] Sales Order not found for Shopify order %s, skipping",
				order["id"],
			)
			create_shopify_log(
				status="Warning",
				message="Sales Order not found for paid order",
				shopify_store=shopify_store,
			)
			return

		so = frappe.get_doc("Sales Order", so_name)
		logger.info(
			"[orders/paid] Found Sales Order %s - docstatus: %s, per_billed: %s",
			so.name,
			so.docstatus,
			so.per_billed,
		)

		# Phase A: Update financial status
		frappe.db.set_value("Sales Order", so_name, "shopify_financial_status", "paid")
		frappe.db.commit()
		logger.info("[orders/paid] Updated financial status to 'paid' for SO %s", so_name)

		# Phase B: Submit if draft and auto-submit enabled
		so.reload()  # Refresh docstatus after Phase A commit (may have been submitted by concurrent webhook)
		if so.docstatus == 0 and store.auto_submit_sales_order:
			so.submit()
			frappe.db.commit()
			logger.info("[orders/paid] Submitted Sales Order %s", so.name)
		elif so.docstatus == 0:
			logger.info("[orders/paid] SO %s is draft but auto_submit is disabled", so.name)

		# Phase C: Create invoice if enabled and SO is submitted
		if store.auto_create_invoice and so.docstatus == 1 and not so.per_billed:
			so.reload()  # Refresh docstatus and modified after Phase B commit
			logger.info("[orders/paid] Creating Sales Invoice for SO %s", so.name)
			si = _create_sales_invoice(so, order, store)

			if si:
				frappe.db.commit()
				logger.info("[orders/paid] Created Sales Invoice %s", si.name)
				if store.auto_create_payment_entry and si.grand_total > 0:
					logger.info("[orders/paid] Creating Payment Entry for SI %s", si.name)
					_create_payment_entries(si, order, store, getdate(order.get("created_at")))
					frappe.db.commit()
			else:
				logger.info("[orders/paid] Sales Invoice already exists or could not be created")
		elif not store.auto_create_invoice:
			logger.info("[orders/paid] auto_create_invoice is disabled, skipping invoice creation")
		elif so.docstatus != 1:
			logger.info(
				"[orders/paid] SO %s not submitted (docstatus=%s), skipping invoice", so.name, so.docstatus
			)
		elif so.per_billed:
			logger.info("[orders/paid] SO %s already billed (%s%%), skipping invoice", so.name, so.per_billed)

		logger.info("[orders/paid] Successfully processed paid order %s -> SO %s", order["id"], so.name)

		create_shopify_log(
			status="Success",
			message=f"Processed paid order for {so.name}",
			shopify_store=shopify_store,
			reference_doctype="Sales Order",
			reference_name=so.name,
		)

	except Exception as e:
		logger.error(
			"[orders/paid] Error processing paid order %s: %s",
			order["id"],
			str(e),
			exc_info=True,
		)
		frappe.db.rollback()
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.commit()
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
	logger = get_logger()
	# Set user context for permission checks (webhook runs as Guest)
	if frappe.session.user == "Guest":
		frappe.set_user("Administrator")
	else:
		logger.info(
			"[orders/cancelled] Running as user %s, skipping elevation to Administrator", frappe.session.user
		)

	frappe.flags.request_id = request_id

	order = payload
	logger.info(
		"[orders/cancelled] Webhook received - order_id: %s, order_number: %s, store: %s, request_id: %s",
		order.get("id"),
		order.get("name"),
		shopify_store,
		request_id,
	)
	logger.info(
		"[orders/cancelled] Order details - financial_status: %s, cancel_reason: %s",
		order.get("financial_status"),
		order.get("cancel_reason"),
	)

	try:
		order_id = order["id"]
		order_status = order.get("financial_status", "cancelled")

		so_name = frappe.db.get_value("Sales Order", {"shopify_order_id": cstr(order_id)})

		if not so_name:
			logger.warning(
				"[orders/cancelled] Sales Order not found for Shopify order %s, skipping",
				order_id,
			)
			create_shopify_log(
				status="Warning",
				message="Sales Order does not exist for cancellation",
				shopify_store=shopify_store,
			)
			return

		so = frappe.get_doc("Sales Order", so_name)
		logger.info(
			"[orders/cancelled] Found Sales Order %s - docstatus: %s",
			so.name,
			so.docstatus,
		)

		# Check for linked documents
		sales_invoice = frappe.db.get_value("Sales Invoice", {"shopify_order_id": cstr(order_id)})
		delivery_notes = frappe.db.get_list("Delivery Note", filters={"shopify_order_id": cstr(order_id)})

		logger.info(
			"[orders/cancelled] Linked documents - SI: %s, DN count: %s",
			sales_invoice,
			len(delivery_notes),
		)

		# Update status on linked docs
		if sales_invoice:
			frappe.db.set_value("Sales Invoice", sales_invoice, "shopify_financial_status", order_status)
			logger.info(
				"[orders/cancelled] Updated SI %s financial status to '%s'", sales_invoice, order_status
			)

		for dn in delivery_notes:
			frappe.db.set_value("Delivery Note", dn.name, "shopify_financial_status", order_status)
			logger.info("[orders/cancelled] Updated DN %s financial status to '%s'", dn.name, order_status)

		# Cancel SO only if no linked docs and it's submitted
		if not sales_invoice and not delivery_notes and so.docstatus == 1:
			so.cancel()
			logger.info("[orders/cancelled] Cancelled Sales Order %s", so.name)
		elif so.docstatus == 0:
			# Draft - just delete or update status
			frappe.db.set_value("Sales Order", so.name, "shopify_financial_status", "voided")
			logger.info("[orders/cancelled] Marked draft SO %s as voided", so.name)
		else:
			# Has linked docs - just update status
			frappe.db.set_value("Sales Order", so.name, "shopify_financial_status", order_status)
			logger.info(
				"[orders/cancelled] SO %s has linked docs, only updated status to '%s'",
				so.name,
				order_status,
			)

		logger.info(
			"[orders/cancelled] Successfully processed cancellation for order %s -> SO %s", order_id, so.name
		)

		create_shopify_log(
			status="Success",
			message=f"Processed cancellation for {so.name}",
			shopify_store=shopify_store,
			reference_doctype="Sales Order",
			reference_name=so.name,
		)

	except Exception as e:
		logger.error(
			"[orders/cancelled] Error processing cancellation for order %s: %s",
			order.get("id"),
			str(e),
			exc_info=True,
		)
		frappe.db.rollback()
		create_shopify_log(
			status="Error",
			exception=frappe.get_traceback(),
			message=str(e),
			shopify_store=shopify_store,
		)
		frappe.db.commit()
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
	# Set user context for permission checks (manual sync may run as authenticated user)
	if frappe.session.user == "Guest":
		frappe.set_user("Administrator")
	else:
		logger.info("Running order sync as user %s, skipping elevation to Administrator", frappe.session.user)
	store = frappe.get_doc("Shopify Store", shopify_store)

	# Determine sync window
	used_default_from_date = False
	if not from_date:
		if store.last_order_sync:
			from_date = store.last_order_sync
		else:
			from_date = add_days(nowdate(), -30)
			used_default_from_date = True

	if not to_date:
		to_date = now()

	logger.info(
		"Syncing new orders for Shopify Store: %s, from: %s, to: %s (used_default_from_date: %s)",
		shopify_store,
		from_date,
		to_date,
		used_default_from_date,
	)

	# Convert to ISO format for Shopify API using site's timezone
	site_timezone = pytz.timezone(get_system_timezone())
	from_time_iso = site_timezone.localize(get_datetime(from_date)).isoformat()
	to_time_iso = site_timezone.localize(get_datetime(to_date)).isoformat()

	logger.info(
		"Shopify API query params - created_at_min: %s, created_at_max: %s",
		from_time_iso,
		to_time_iso,
	)

	synced, skipped, errors = 0, 0, 0
	total_orders_fetched = 0
	batch_count = 0

	api_version = store.api_version or DEFAULT_API_VERSION
	auth_details = (store.shop_domain, api_version, store.get_password("access_token"))

	with Session.temp(*auth_details):
		# Build query params
		query_params = {
			"created_at_min": from_time_iso,
			"created_at_max": to_time_iso,
			"limit": 250,
		}
		if store.sync_all_order_statuses:
			query_params["status"] = "any"
			logger.info(
				"Fetching all order statuses (including fulfilled/closed) for store: %s", shopify_store
			)

		orders_iter = PaginatedIterator(Order.find(**query_params))

		for orders in orders_iter:
			batch_count += 1
			batch_size = len(orders)
			total_orders_fetched += batch_size
			logger.info(
				"Fetched batch %d with %d orders for Shopify Store: %s",
				batch_count,
				batch_size,
				shopify_store,
			)
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
					frappe.db.rollback()
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
					frappe.db.commit()
					logger.error(
						"Error processing order: %s for Shopify Store: %s, Error: %s",
						order.id,
						shopify_store,
						str(e),
						exc_info=True,
					)

	# Log summary
	if total_orders_fetched == 0:
		logger.info(
			"No orders found in Shopify for store: %s in date range %s to %s",
			shopify_store,
			from_time_iso,
			to_time_iso,
		)
	else:
		logger.info(
			"Fetched %d orders in %d batches from Shopify for store: %s",
			total_orders_fetched,
			batch_count,
			shopify_store,
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
		# No customer in order (e.g. Shopify POS) - use default customer
		if not store.default_customer:
			frappe.throw(
				_(
					"Shopify order {0} has no customer data (e.g. POS order) and no Default Customer "
					"is configured on Shopify Store '{1}'. Please set a Default Customer in the "
					"Shopify Store settings to handle guest/POS orders."
				).format(order.get("name") or order.get("id"), store.name)
			)

		logger.info("No customer in order, using default customer: %s", store.default_customer)
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
	customer_name: str, address_type: str, address_line1: str, city: str, country: str
) -> str | None:
	"""Find existing address by physical address fields.

	Matches on customer, address_type, address_line1, city, and country only.
	address_title is intentionally excluded because different people (e.g. employees)
	can place orders from the same company address, and including it causes false
	negatives that create duplicate address records.
	"""
	addresses = frappe.get_all(
		"Address",
		filters=[
			["Dynamic Link", "link_doctype", "=", "Customer"],
			["Dynamic Link", "link_name", "=", customer_name],
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
	# Priority for address_title: 1) Shopify company name (for B2B invoicing),
	# 2) Shopify address "name" (person), 3) Customer display name fallback
	shopify_company = cstr(address_data.get("company")).strip()
	shopify_addr_name = cstr(address_data.get("name")).strip()
	if shopify_company:
		address_title = shopify_company
	elif shopify_addr_name:
		address_title = shopify_addr_name
	else:
		address_title = frappe.db.get_value("Customer", customer_name, "customer_name") or customer_name
	address_line1 = cstr(address_data.get("address1", "")).strip() or "-"
	city = cstr(address_data.get("city", "")).strip() or "-"
	country = cstr(address_data.get("country", "")).strip()

	# Check if address already exists (matches on physical address fields only,
	# not address_title, to prevent duplicates when different people order from same address)
	existing = _find_existing_address(
		customer_name, address_type, address_line1, city, country
	)
	if existing:
		# Upgrade address_title to company name if the existing record has a person
		# name and the incoming Shopify data provides a company. This fixes legacy
		# addresses created before the company-priority logic was added.
		if shopify_company:
			existing_title = frappe.db.get_value("Address", existing, "address_title")
			if existing_title != shopify_company:
				frappe.db.set_value("Address", existing, "address_title", shopify_company)
				logger.info(
					"Updated address_title from %s to %s on %s",
					existing_title, shopify_company, existing,
				)
		logger.info("Address already exists: %s", existing)
		return existing

	# Sanitize phone number to comply with Frappe validation
	raw_phone = cstr(address_data.get("phone", "")).strip()
	sanitized_phone, original_phone = sanitize_phone_number(raw_phone)

	# Create new address
	address_fields = {
		"doctype": "Address",
		"address_title": address_title,
		"address_type": address_type,
		"address_line1": address_line1,
		"address_line2": cstr(address_data.get("address2", "")).strip(),
		"city": city,
		"state": cstr(address_data.get("province", "")).strip(),
		"pincode": cstr(address_data.get("zip", "")).strip(),
		"country": country,
		"phone": sanitized_phone or "",
		"links": [{"link_doctype": "Customer", "link_name": customer_name}],
	}
	if original_phone:
		address_fields["fax"] = original_phone

	address = frappe.get_doc(address_fields)
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
	raw_phone = (
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

	# Sanitize phone number to comply with Frappe validation
	phone, original_phone = sanitize_phone_number(raw_phone)
	logger.info("Contact resolved - email: %s, phone: %s (raw: %s)", email, phone, raw_phone)

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
		phone_row = {"phone": phone, "is_primary_phone": 1}
		if original_phone and frappe.db.has_column("Contact Phone", "shopify_original_phone"):
			phone_row["shopify_original_phone"] = original_phone
		contact.append("phone_nos", phone_row)

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
			"shopify_customer_note": order.get("note"),
			"customer": customer_name,
			"contact_person": contact_name,
			"customer_address": billing_address,
			"shipping_address_name": shipping_address,
			"currency": order.get("currency"),
			"transaction_date": getdate(order.get("created_at")) or nowdate(),
			"delivery_date": getdate(order.get("created_at")) or nowdate(),
			"company": store.company,
			"cost_center": store.cost_center,
			"selling_price_list": frappe.db.get_value("Customer", customer_name, "default_price_list") or store.price_list,
			"ignore_pricing_rule": 1,
			"items": items,
			"taxes": taxes,
		}
	)

	so.flags.ignore_mandatory = True
	so.insert(ignore_permissions=True)

	# Apply rounding adjustment to match Shopify total
	apply_rounding_adjustment(so, order, store=store)

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
				"cost_center": store.cost_center,
				"shopify_item_discount": per_item_discount,
			}
		)

	return items


def _get_item_price(line_item: dict, taxes_inclusive: bool) -> float:
	"""Calculate item price, handling discounts and tax-inclusive pricing.

	Calculates the line-level amount first to match Shopify's calculation,
	then derives the rate with high precision to minimize rounding errors.

	Uses the system's currency precision setting to round line amounts correctly
	for different currencies (e.g., 0 for JPY, 3 for KWD, 2 for most others).
	"""
	price = flt(line_item.get("price"))
	qty = cint(line_item.get("quantity")) or 1

	# Remove line item level discounts
	total_discount = _get_total_discount(line_item)

	# Get currency precision from system settings (defaults to 2 if not configured)
	precision = get_currency_precision() or 2

	# NOTE: We calculate the line amount first, then derive the per-unit rate.
	# This avoids rounding errors that occur when dividing tax/discount by quantity first.
	# For example, if tax is $10.00 for qty 3, dividing first gives $3.333... per unit,
	# which when multiplied back (3.33 * 3 = 9.99) causes cumulative cent discrepancies.
	# By computing the line amount first (rounded to currency precision to match Shopify),
	# then deriving the rate with high precision, ERPNext's rate * qty reproduces the
	# exact line amount Shopify calculated.

	if not taxes_inclusive:
		line_amount = flt(price * qty - total_discount, precision)
		return flt(line_amount / qty, 9)

	# For tax-inclusive pricing, subtract taxes
	total_taxes = sum(flt(tax.get("price")) for tax in line_item.get("tax_lines", []))

	line_amount = flt(price * qty - total_taxes - total_discount, precision)
	return flt(line_amount / qty, 9)


def _get_total_discount(line_item: dict) -> float:
	"""Get total discount amount from line item."""
	discount_allocations = line_item.get("discount_allocations") or []
	return sum(flt(discount.get("amount")) for discount in discount_allocations)


def _get_order_taxes(order: dict, store, items: list) -> list:
	"""
	Build tax rows from Shopify order taxes using TaxBuilder.

	Uses "On Net Total" tax calculation instead of per-line-item "Actual" taxes.
	This results in cleaner tax rows that automatically recalculate when items change.

	Args:
		order: Shopify order data
		store: Shopify Store document
		items: List of SO item dicts (may be modified for zero-rating and shipping)

	Returns:
		List of tax dicts for Sales Order
	"""
	builder = TaxBuilder(order, store, items)
	return builder.build()


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
	if frappe.db.get_value(
		"Sales Invoice", {"shopify_order_id": cstr(order.get("id")), "docstatus": ["!=", 2]}
	):
		return None

	# SO must be submitted and not billed
	if so.docstatus != 1 or so.per_billed:
		return None

	posting_date = getdate(order.get("created_at")) or nowdate()

	si = make_sales_invoice(so.name, ignore_permissions=True)
	si.shopify_store = store.name
	si.shopify_order_id = cstr(order.get("id"))
	si.shopify_order_number = order.get("name")
	si.shopify_customer_note = order.get("note")
	si.set_posting_time = 1
	si.posting_date = posting_date
	si.due_date = posting_date
	si.naming_series = store.sales_invoice_series or "SI-Shopify-"
	si.cost_center = store.cost_center

	# Set cost center on items
	for item in si.items:
		item.cost_center = store.cost_center

	si.flags.ignore_mandatory = True
	si.insert(ignore_permissions=True)
	si.submit()

	return si


def _create_payment_entries(si, order: dict, store, posting_date=None):
	"""
	Create Payment Entries against Sales Invoice using payment method mapping.

	Parses the order's transactions to determine payment amounts per gateway,
	then creates a Payment Entry for each gateway using the configured mapping.

	Args:
		si: Sales Invoice document
		order: Shopify order data containing transactions
		store: Shopify Store document
		posting_date: Date for the payment entries

	Raises:
		ValueError: If no payment method mapping exists for a gateway
	"""
	logger = get_logger()
	posting_date = posting_date or nowdate()

	# Get payment amounts per gateway from transactions
	gateway_amounts = _get_payment_amounts_by_gateway(order)

	if not gateway_amounts:
		logger.warning(
			"No successful payment transactions found in order %s, skipping payment entry creation",
			order.get("id"),
		)
		return

	logger.info(
		"Creating payment entries for order %s: gateways=%s",
		order.get("id"),
		list(gateway_amounts.keys()),
	)

	# Build mapping lookup dict
	payment_mapping = {m.shopify_gateway: m for m in store.payment_method_mapping or []}

	# Validate that payment mappings are configured
	if not payment_mapping:
		frappe.throw(
			_(
				"No payment method mappings configured for Shopify Store '{0}'. "
				"Please configure payment method mappings before enabling auto-create payment entry."
			).format(store.name)
		)

	# Create a Payment Entry for each gateway
	remaining_amount = flt(si.grand_total)

	for gateway, amount in gateway_amounts.items():
		# Look up mapping
		mapping = payment_mapping.get(gateway)
		if not mapping:
			frappe.throw(
				_(
					"Payment method mapping not configured for Shopify gateway '{0}'. "
					"Please add a mapping in Shopify Store settings."
				).format(gateway)
			)

		# Determine the amount for this payment entry
		# For the last gateway, use remaining amount to handle rounding
		if len(gateway_amounts) > 1 and gateway == list(gateway_amounts.keys())[-1]:
			payment_amount = remaining_amount
		else:
			payment_amount = min(flt(amount), remaining_amount)

		if payment_amount <= 0:
			logger.warning("Skipping payment entry for gateway %s with zero/negative amount", gateway)
			continue

		_create_single_payment_entry(
			si=si,
			amount=payment_amount,
			mode_of_payment=mapping.mode_of_payment,
			account=mapping.account,
			posting_date=posting_date,
			gateway=gateway,
		)

		remaining_amount -= payment_amount
		logger.info(
			"Created payment entry for gateway %s: amount=%s, mode=%s",
			gateway,
			payment_amount,
			mapping.mode_of_payment,
		)


def _get_payment_amounts_by_gateway(order: dict) -> dict:
	"""
	Parse Shopify order transactions to get payment amounts grouped by gateway.

	Only includes successful sale/capture transactions, excludes refunds/voids.

	Args:
		order: Shopify order data

	Returns:
		Dict mapping gateway name to total amount
	"""
	gateway_amounts = {}
	transactions = order.get("transactions") or []

	for txn in transactions:
		# Only count successful sale or capture transactions
		if txn.get("status") != "success":
			continue
		if txn.get("kind") not in ("sale", "capture"):
			continue

		gateway = txn.get("gateway") or "unknown"
		amount = flt(txn.get("amount"))

		if gateway in gateway_amounts:
			gateway_amounts[gateway] += amount
		else:
			gateway_amounts[gateway] = amount

	# If no transactions found, fall back to payment_gateway_names with total amount
	# But only if there's a single gateway - for split payments we can't determine amounts
	if not gateway_amounts:
		logger = get_logger()
		payment_gateways = order.get("payment_gateway_names") or []
		if payment_gateways and order.get("financial_status") == "paid":
			if len(payment_gateways) > 1:
				# Multiple gateways without transaction data - can't determine split amounts
				logger.warning(
					"Order %s has multiple payment gateways %s but no transaction data. "
					"Cannot determine payment split - skipping automatic payment entry creation.",
					order.get("id"),
					payment_gateways,
				)
				# Return empty to skip payment entry creation
				return gateway_amounts

			logger.warning(
				"No transaction data found for paid order %s, using fallback: gateway=%s, amount=%s",
				order.get("id"),
				payment_gateways[0],
				flt(order.get("total_price")),
			)
			# Single gateway - safe to use full amount
			gateway_amounts[payment_gateways[0]] = flt(order.get("total_price"))

	return gateway_amounts


def _create_single_payment_entry(
	si,
	amount: float,
	mode_of_payment: str,
	account: str | None,
	posting_date,
	gateway: str,
):
	"""
	Create a single Payment Entry for a specific amount and mode of payment.

	Args:
		si: Sales Invoice document
		amount: Payment amount
		mode_of_payment: Mode of Payment to use
		account: Optional account override
		posting_date: Date for the payment entry
		gateway: Shopify gateway name (for reference)
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	# Get the default account for this mode of payment if not specified
	if not account:
		account = frappe.db.get_value(
			"Mode of Payment Account",
			{"parent": mode_of_payment, "company": si.company},
			"default_account",
		)

	if not account:
		frappe.throw(
			_(
				"No account configured for Mode of Payment '{0}' in company '{1}'. "
				"Please configure the account in Mode of Payment or Shopify Store settings."
			).format(mode_of_payment, si.company)
		)

	pe = get_payment_entry(si.doctype, si.name, bank_account=account)

	# Set the payment amount (may be less than invoice total for split payments)
	pe.paid_amount = amount
	pe.received_amount = amount

	# Adjust the reference amount
	if pe.references:
		pe.references[0].allocated_amount = amount

	pe.mode_of_payment = mode_of_payment
	pe.reference_no = si.name
	pe.posting_date = posting_date
	pe.reference_date = posting_date
	pe.remarks = f"Payment via Shopify gateway: {gateway}"
	pe.flags.ignore_mandatory = True
	pe.insert(ignore_permissions=True)
	pe.submit()

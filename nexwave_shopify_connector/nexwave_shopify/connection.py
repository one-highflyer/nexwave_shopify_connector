# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import base64
import functools
import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.model.document import Document
from shopify.resources import Webhook
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.utils import create_shopify_log

# Default API version (use a recent stable version)
DEFAULT_API_VERSION = "2024-10"

# Webhook events to register
WEBHOOK_EVENTS = [
	"orders/create",
	"orders/paid",
	"orders/cancelled",
	"orders/fulfilled",
	"orders/partially_fulfilled",
]

# Event to handler mapping
EVENT_MAPPER = {
	"orders/create": "nexwave_shopify_connector.nexwave_shopify.order.sync_sales_order",
	"orders/paid": "nexwave_shopify_connector.nexwave_shopify.order.process_paid_order",
	"orders/cancelled": "nexwave_shopify_connector.nexwave_shopify.order.cancel_order",
	"orders/fulfilled": "nexwave_shopify_connector.nexwave_shopify.fulfillment.sync_fulfillment",
	"orders/partially_fulfilled": "nexwave_shopify_connector.nexwave_shopify.fulfillment.sync_fulfillment",
}


def shopify_session(shopify_store: str | Document | None = None, allow_implicit: bool = False):
	"""
	Decorator that establishes a temporary Shopify API session for a specific store.

	Args:
		shopify_store: Name of Shopify Store doc, or the doc itself.
		               If None and allow_implicit=True, uses the single enabled store (if exactly one exists).
		allow_implicit: If True, allows implicit store selection when only one enabled store exists.

	Usage:
		@shopify_session(shopify_store="mystore.myshopify.com")
		def sync_products():
			# Shopify API is available here
			pass

		# Or with allow_implicit for single-store setups
		@shopify_session(allow_implicit=True)
		def sync_products():
			pass
	"""

	def decorator(func):
		@functools.wraps(func)
		def wrapper(*args, **kwargs):
			# No auth in testing
			if frappe.flags.in_test:
				return func(*args, **kwargs)

			# Resolve store
			store = _resolve_shopify_store(shopify_store, allow_implicit, kwargs)
			if not store:
				frappe.throw(_("Could not resolve Shopify Store"))

			# Set store context
			frappe.flags.shopify_store = store.name

			# Get auth details - supports both Legacy and OAuth methods
			api_version = store.api_version or DEFAULT_API_VERSION
			access_token = get_access_token(store)
			auth_details = (store.shop_domain, api_version, access_token)

			try:
				with Session.temp(*auth_details):
					return func(*args, **kwargs)
			finally:
				# Clear store context
				frappe.flags.shopify_store = None

		return wrapper

	return decorator


def _resolve_shopify_store(
	shopify_store: str | Document | None, allow_implicit: bool, kwargs: dict
) -> Document | None:
	"""Resolve Shopify Store document from various inputs."""

	# Check if store is passed in kwargs
	if "shopify_store" in kwargs:
		shopify_store = kwargs.pop("shopify_store")

	# If already a document, return it
	if shopify_store and hasattr(shopify_store, "doctype"):
		return shopify_store

	# If string, look up by name or domain
	if shopify_store:
		return get_shopify_store(shopify_store)

	# Try implicit resolution
	if allow_implicit:
		enabled_stores = frappe.get_all("Shopify Store", filters={"enabled": 1}, limit=2)
		if len(enabled_stores) == 1:
			return frappe.get_doc("Shopify Store", enabled_stores[0].name)
		elif len(enabled_stores) > 1:
			frappe.throw(_("Multiple Shopify Stores are enabled. Please specify which store to use."))

	return None


def get_shopify_store(name_or_domain: str, require_enabled: bool = True) -> "Document":
	"""
	Get Shopify Store document by name or domain.

	Args:
		name_or_domain: Store name or shop domain
		require_enabled: If True, throws error if store is not enabled

	Returns:
		Shopify Store document
	"""
	# First try by name
	if frappe.db.exists("Shopify Store", name_or_domain):
		store = frappe.get_doc("Shopify Store", name_or_domain)
	else:
		# Try by domain
		normalized_domain = normalize_shop_domain(name_or_domain)
		store_name = frappe.db.get_value("Shopify Store", {"shop_domain": normalized_domain})
		if not store_name:
			frappe.throw(_("Shopify Store not found: {0}").format(name_or_domain))
		store = frappe.get_doc("Shopify Store", store_name)

	if require_enabled and not store.enabled:
		frappe.throw(_("Shopify Store {0} is not enabled").format(store.name))

	return store


def get_shopify_store_by_domain(shop_domain: str) -> str | None:
	"""
	Get Shopify Store name by domain.

	Args:
		shop_domain: Shopify shop domain (will be normalized)

	Returns:
		Store name if found, None otherwise
	"""
	normalized_domain = normalize_shop_domain(shop_domain)
	return frappe.db.get_value("Shopify Store", {"shop_domain": normalized_domain})


def normalize_shop_domain(domain: str) -> str:
	"""
	Normalize shop domain to standard format.

	Strips protocol, trailing slashes, /admin suffix, query strings.

	Args:
		domain: Raw shop domain

	Returns:
		Normalized domain (e.g., "mystore.myshopify.com")
	"""
	if not domain:
		return domain

	domain = domain.strip()

	# Remove protocol
	for prefix in ["https://", "http://"]:
		if domain.startswith(prefix):
			domain = domain[len(prefix) :]

	# Remove trailing slash
	domain = domain.rstrip("/")

	# Remove /admin suffix
	if domain.endswith("/admin"):
		domain = domain[:-6]

	# Remove query string
	if "?" in domain:
		domain = domain.split("?")[0]

	return domain


def get_access_token(store: "Document") -> str:
	"""
	Get access token for the store.

	Both Legacy and OAuth methods now store the token in the same field.
	OAuth tokens are stored by the oauth.callback() endpoint after successful authorization.

	Args:
		store: Shopify Store document

	Returns:
		Access token string

	Raises:
		frappe.ValidationError: If token cannot be retrieved
	"""
	access_token = store.get_password("access_token")

	if not access_token:
		auth_method = getattr(store, "auth_method", None) or "Legacy (Access Token)"
		if auth_method == "OAuth":
			frappe.throw(
				_("OAuth not connected for store {0}. Please click 'Connect to Shopify' to authorize.").format(
					store.name
				)
			)
		else:
			frappe.throw(_("Access Token is required for store {0}").format(store.name))

	return access_token


def get_callback_url(store: Document | None = None) -> str:
	"""
	Get webhook callback URL for the current site.

	If developer_mode is enabled and localtunnel_url is set in site config,
	callback url is set to localtunnel_url.

	Args:
		store: Optional Shopify Store document (for future store-specific URLs)

	Returns:
		Full callback URL
	"""
	url = get_current_domain_name()
	return f"https://{url}/api/method/nexwave_shopify_connector.nexwave_shopify.connection.store_request_data"


def get_current_domain_name() -> str:
	"""
	Get current site domain name.

	If developer_mode is enabled and localtunnel_url is set in site config,
	domain is set to localtunnel_url.

	Returns:
		Domain name (e.g., "mysite.com")
	"""
	if frappe.conf.developer_mode and frappe.conf.localtunnel_url:
		return frappe.conf.localtunnel_url
	else:
		return frappe.request.host


def register_webhooks(store: Document) -> list[Webhook]:
	"""
	Register required webhooks with Shopify for a specific store.

	Args:
		store: Shopify Store document

	Returns:
		List of registered Webhook objects
	"""
	new_webhooks = []

	# Clear stale webhooks first
	unregister_webhooks(store)

	api_version = store.api_version or DEFAULT_API_VERSION
	auth_details = (store.shop_domain, api_version, store.get_password("access_token"))

	with Session.temp(*auth_details):
		for topic in WEBHOOK_EVENTS:
			webhook = Webhook.create({"topic": topic, "address": get_callback_url(store), "format": "json"})

			if webhook.is_valid():
				new_webhooks.append(webhook)
			else:
				create_shopify_log(
					status="Error",
					shopify_store=store.name,
					response_data=webhook.to_dict(),
					exception=webhook.errors.full_messages(),
				)

	return new_webhooks


def unregister_webhooks(store: Document) -> None:
	"""
	Unregister all webhooks from Shopify that correspond to current site URL.

	Args:
		store: Shopify Store document
	"""
	url = get_current_domain_name()
	api_version = store.api_version or DEFAULT_API_VERSION
	auth_details = (store.shop_domain, api_version, store.get_password("access_token"))

	with Session.temp(*auth_details):
		for webhook in Webhook.find():
			if url in webhook.address:
				webhook.destroy()


@frappe.whitelist(allow_guest=True)
def store_request_data() -> None:
	"""
	Webhook endpoint for Shopify.

	Receives webhook calls, validates HMAC, resolves store, and enqueues processing.
	"""
	if not frappe.request:
		return

	# Get shop domain from header to resolve store
	shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")
	if not shop_domain:
		frappe.throw(_("Missing X-Shopify-Shop-Domain header"))

	# Resolve store
	store_name = get_shopify_store_by_domain(shop_domain)
	if not store_name:
		frappe.throw(_("Unknown Shopify Store: {0}").format(shop_domain))

	store = frappe.get_doc("Shopify Store", store_name)

	# Validate HMAC using store's shared secret
	hmac_header = frappe.get_request_header("X-Shopify-Hmac-Sha256")
	_validate_request(frappe.request, hmac_header, store.shared_secret)

	# Set store context
	frappe.flags.shopify_store = store.name

	# Parse data
	data = json.loads(frappe.request.data)
	event = frappe.request.headers.get("X-Shopify-Topic")

	if event not in EVENT_MAPPER:
		create_shopify_log(
			status="Error", shopify_store=store.name, request_data=data, exception=f"Unknown event: {event}"
		)
		return

	# Create log
	log = create_shopify_log(method=EVENT_MAPPER[event], shopify_store=store.name, request_data=data)

	# Enqueue background job with store context
	frappe.enqueue(
		method=EVENT_MAPPER[event],
		queue="short",
		timeout=300,
		is_async=True,
		payload=data,
		request_id=log.name,
		shopify_store=store.name,
	)


def _validate_request(req, hmac_header: str, secret_key: str) -> None:
	"""
	Validate webhook request HMAC.

	Args:
		req: The request object
		hmac_header: HMAC header value from Shopify
		secret_key: Store's shared secret
	"""
	if not secret_key:
		frappe.throw(_("Shared secret not configured for this store"))

	sig = base64.b64encode(hmac.new(secret_key.encode("utf8"), req.data, hashlib.sha256).digest())

	if sig != bytes(hmac_header.encode()):
		shop_domain = frappe.get_request_header("X-Shopify-Shop-Domain")
		store_name = get_shopify_store_by_domain(shop_domain)
		create_shopify_log(status="Error", shopify_store=store_name, request_data=req.data)
		frappe.throw(_("Unverified Webhook Data"))

# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
Self-contained OAuth 2.0 implementation for Shopify.

This module handles the complete OAuth flow without depending on Frappe's
Connected App or Token Cache patterns. The access token is stored directly
on the Shopify Store document.
"""

import secrets
import typing
from urllib.parse import urlencode

import frappe
import requests
from frappe import _

from nexwave_shopify_connector.nexwave_shopify.connection import normalize_shop_domain
from nexwave_shopify_connector.utils.logger import get_logger

if typing.TYPE_CHECKING:
	from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store.shopify_store import ShopifyStore

# All scopes needed for full connector functionality
# Requested upfront so users can toggle features without reconnecting
OAUTH_SCOPES = [
	"read_orders",
	"write_orders",
	"read_customers",
	"write_customers",
	"read_products",
	"write_products",
	"read_inventory",
	"write_inventory",
	"read_locations",
	"read_fulfillments",
	"write_fulfillments",
]


def get_oauth_scopes() -> list[str]:
	"""Return all scopes needed for the connector."""
	return OAUTH_SCOPES


@frappe.whitelist()
def get_callback_url() -> str:
	"""Get the OAuth callback URL for this site."""
	site_url = frappe.utils.get_url()
	return f"{site_url}/api/method/nexwave_shopify_connector.nexwave_shopify.oauth.callback"


def generate_state_token(shopify_store: str) -> str:
	"""
	Generate CSRF state token and store temporarily.

	Args:
	    shopify_store: Name of the Shopify Store document

	Returns:
	    State token string
	"""
	token = secrets.token_urlsafe(32)

	# Store in cache with 10-minute expiry
	frappe.cache().set_value(f"shopify_oauth_state:{token}", shopify_store, expires_in_sec=600)
	return token


def validate_state_token(state: str) -> str | None:
	"""
	Validate state token and return associated store name.

	Args:
	    state: State token from OAuth callback

	Returns:
	    Store name if valid, None otherwise
	"""
	store_name = frappe.cache().get_value(f"shopify_oauth_state:{state}")
	if store_name:
		# Clear the token (single-use)
		frappe.cache().delete_value(f"shopify_oauth_state:{state}")
	return store_name


@frappe.whitelist()
def authorize(shopify_store: str):
	"""
	Build Shopify OAuth authorization URL and return it.

	Called when user clicks "Connect to Shopify" button.

	Args:
	    shopify_store: Name of the Shopify Store document

	Returns:
	    Authorization URL to redirect user to
	"""
	logger = get_logger()

	if not shopify_store:
		frappe.throw(_("Missing shopify_store parameter"))

	if not frappe.db.exists("Shopify Store", shopify_store):
		frappe.throw(_("Shopify Store not found: {0}").format(shopify_store))

	store: ShopifyStore = frappe.get_doc("Shopify Store", shopify_store)

	# Verify permission
	if not store.has_permission("write"):
		frappe.throw(_("You do not have permission to connect this Shopify Store"), frappe.PermissionError)

	if store.auth_method != "OAuth":
		frappe.throw(_("Store is not configured for OAuth authentication"))

	if not store.client_id:
		frappe.throw(_("Client ID is required for OAuth"))

	if not store.shop_domain:
		frappe.throw(_("Shop domain is required"))

	# Build callback URL
	callback_url = get_callback_url()

	# Get all scopes (requested upfront for full functionality)
	scopes = get_oauth_scopes()

	# Generate state token for CSRF protection
	state = generate_state_token(shopify_store)

	# Build authorization URL
	params = {
		"client_id": store.client_id,
		"scope": ",".join(scopes),
		"redirect_uri": callback_url,
		"state": state,
	}

	auth_url = f"https://{store.shop_domain}/admin/oauth/authorize?" + urlencode(params)

	logger.info("OAuth authorization initiated for store %s", shopify_store)

	return auth_url


@frappe.whitelist()
def callback():
	"""
	OAuth callback - exchanges authorization code for access token.

	Shopify redirects here after user approves permissions.
	This endpoint handles the token exchange directly without using
	Connected App or Token Cache.
	"""
	logger = get_logger()
	logger.info("OAuth callback received")

	# Get parameters from Shopify's redirect
	code = frappe.form_dict.get("code")
	state = frappe.form_dict.get("state")
	shop = frappe.form_dict.get("shop")  # Shopify includes this

	# Check for error response from Shopify
	error = frappe.form_dict.get("error")
	if error:
		error_description = frappe.form_dict.get("error_description", "Unknown error")
		logger.error("OAuth error from Shopify: %s - %s", error, error_description)
		frappe.throw(_("Shopify OAuth error: {0}").format(error_description))

	if not code:
		logger.error("Missing authorization code in callback")
		frappe.throw(_("Missing authorization code"))

	if not state:
		logger.error("Missing state parameter in callback")
		frappe.throw(_("Missing state parameter"))

	if not shop:
		logger.error("Missing shop parameter in callback")
		frappe.throw(_("Missing shop parameter"))

	# Validate state token and get store name
	shopify_store = validate_state_token(state)
	if not shopify_store:
		logger.error("Invalid or expired state token in callback")
		frappe.throw(_("Invalid or expired state token. Please try connecting again."))

	logger.info("Received callback for store %s", shopify_store)

	store: ShopifyStore = frappe.get_doc("Shopify Store", shopify_store)

	# Verify shop domain matches (security check)
	normalized_shop = normalize_shop_domain(shop)
	if normalized_shop != store.shop_domain:
		logger.error("Shop domain mismatch: expected %s, got %s", store.shop_domain, normalized_shop)
		frappe.throw(_("Shop domain mismatch. Please ensure you're authorizing the correct store."))

	# Exchange authorization code for access token
	token_url = f"https://{store.shop_domain}/admin/oauth/access_token"

	logger.info("Exchanging authorization code for access token. Shopify store: %s", store.shop_domain)

	try:
		response = requests.post(
			token_url,
			data={
				"client_id": store.client_id,
				"client_secret": store.get_password("client_secret"),
				"code": code,
			},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=30,
		)
	except requests.RequestException as e:
		logger.error("Failed to connect to Shopify: %s", str(e), exc_info=True)
		frappe.throw(_("Failed to connect to Shopify: {0}").format(str(e)))

	if response.status_code != 200:
		logger.error("Token exchange failed: %s", response.text)
		frappe.throw(_("Token exchange failed: {0}").format(response.text))

	token_data = response.json()
	access_token = token_data.get("access_token")

	if not access_token:
		logger.error("No access token in response: %s", token_data)
		frappe.throw(_("No access token received from Shopify"))

	logger.info("OAuth token exchange successful for store %s", shopify_store)

	# Store the token directly on the Shopify Store document
	# Using db_set to avoid triggering validation (which might clear OAuth fields)
	store.db_set("access_token", access_token)
	store.db_set("connected_user", frappe.session.user)
	store.db_set("oauth_status", "Connected")
	frappe.db.commit()

	frappe.msgprint(_("Successfully connected to Shopify!"), indicator="green", alert=True)
	logger.info(
		"Successfully connected to Shopify! Redirecting to store form. Shopify store: %s", store.shop_domain
	)

	# Redirect to the Shopify Store form
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = f"/app/shopify-store/{store.name}"

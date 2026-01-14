# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe import _
from werkzeug.wrappers import Response

from nexwave_shopify_connector.utils.logger import get_logger


def _raw_response(data: dict, status: int = 200):
    """
    Return a raw JSON response bypassing Frappe's response wrapper.

    This is needed because requests_oauthlib expects standard OAuth2 token format,
    not Frappe's {"message": ...} wrapper.
    """
    response = Response(
        json.dumps(data),
        status=status,
        content_type="application/json"
    )
    frappe.local.response = response
    return response


@frappe.whitelist(allow_guest=True)
def exchange_token():
    """
    Proxy endpoint for Shopify token exchange.

    Shopify's OAuth token response doesn't include 'token_type' field,
    but Frappe's Token Cache requires it. This endpoint proxies the token
    request to Shopify and adds the missing 'token_type: Bearer' to the response.

    IMPORTANT: This endpoint returns RAW JSON (not Frappe-wrapped) because
    requests_oauthlib expects standard OAuth2 token response format.

    Configure Connected App to use this endpoint as the Token URI:
    https://{site}/api/method/nexwave_shopify_connector.nexwave_shopify.oauth.exchange_token
    """
    logger = get_logger()
    logger.info("Exchange token request received")

    # Get the request data from Frappe's OAuth2Session.fetch_token()
    data = frappe.form_dict

    # Get required OAuth parameters
    code = data.get("code")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")

    if not all([code, client_id, client_secret]):
        return _raw_response(
            {"error": "invalid_request", "error_description": "Missing required parameters"},
            status=400
        )

    # Find the Connected App by client_id
    connected_app_name = frappe.db.get_value("Connected App", {"client_id": client_id}, "name")

    if not connected_app_name:
        return _raw_response(
            {"error": "invalid_client", "error_description": "Connected App not found"},
            status=400
        )

    # Find the Shopify Store that uses this Connected App
    store_name = frappe.db.get_value("Shopify Store", {"connected_app": connected_app_name}, "name")

    if not store_name:
        return _raw_response(
            {"error": "invalid_client", "error_description": "Shopify Store not found"},
            status=400
        )

    store = frappe.get_doc("Shopify Store", store_name)
    shop_domain = store.shop_domain

    if not shop_domain:
        return _raw_response(
            {"error": "invalid_client", "error_description": "Shop domain not configured"},
            status=400
        )

    # Build the actual Shopify token URL
    shopify_token_url = f"https://{shop_domain}/admin/oauth/access_token"
    logger.info("Making token exchange request to Shopify for %s", shop_domain)

    # Make the token exchange request to Shopify
    try:
        response = requests.post(
            shopify_token_url,
            data={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to Shopify: %s", str(e), exc_info=True)
        return _raw_response(
            {"error": "server_error", "error_description": f"Failed to connect to Shopify: {e}"},
            status=500
        )

    if response.status_code != 200:
        logger.error("Shopify token exchange failed: %s", response.text)
        # Pass through Shopify's error response
        frappe.local.response = Response(
            response.text,
            status=response.status_code,
            content_type="application/json"
        )
        return frappe.local.response

    token_data = response.json()
    logger.info("Shopify token exchange successful")

    # Add the missing token_type that Frappe's Token Cache requires
    token_data["token_type"] = "Bearer"

    # Add expires_in if not present (Shopify offline tokens don't expire)
    # Frappe's Token Cache uses expires_in to determine if token is expired.
    # Without this, is_expired() returns True immediately, causing get_active_token() to fail.
    # Set to 1 year (in seconds) for effectively non-expiring offline tokens.
    if "expires_in" not in token_data:
        token_data["expires_in"] = 31536000  # 1 year in seconds

    # Return RAW JSON response (not wrapped in Frappe's {"message": ...})
    return _raw_response(token_data, status=200)


@frappe.whitelist()
def callback(shopify_store: str = None):
    """
    OAuth success callback - called after Frappe's Connected App
    completes the OAuth flow and stores token in Token Cache.

    This endpoint is called via the success_uri parameter passed to
    Connected App's initiate_web_application_flow().

    It verifies the token exists and updates the Shopify Store status.

    Args:
        shopify_store: Name of the Shopify Store document
    """
    if not shopify_store:
        frappe.throw(_("Missing shopify_store parameter"))

    if not frappe.db.exists("Shopify Store", shopify_store):
        frappe.throw(_("Shopify Store not found: {0}").format(shopify_store))

    store = frappe.get_doc("Shopify Store", shopify_store)

    # Verify the current user has permission to modify this store
    if not store.has_permission("write"):
        frappe.throw(_("You do not have permission to connect this Shopify Store"), frappe.PermissionError)

    if store.auth_method != "OAuth":
        frappe.throw(_("Store is not configured for OAuth authentication"))

    if not store.connected_app:
        frappe.throw(_("Store does not have a Connected App configured"))

    # Verify token exists in Token Cache
    connected_app = frappe.get_doc("Connected App", store.connected_app)
    token_cache = connected_app.get_token_cache(frappe.session.user)

    if not token_cache:
        frappe.throw(_("OAuth authorization failed - no token received. Please try again."))

    access_token = token_cache.get_password("access_token", raise_exception=False)
    if not access_token:
        frappe.throw(_("OAuth authorization failed - no access token in token cache. Please try again."))

    # Update store with connected user and status
    store.db_set("connected_user", frappe.session.user)
    store.db_set("oauth_status", "Connected")
    frappe.db.commit()

    frappe.msgprint(_("Successfully connected to Shopify!"), indicator="green", alert=True)

    # Redirect to the Shopify Store form
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"/app/shopify-store/{store.name}"

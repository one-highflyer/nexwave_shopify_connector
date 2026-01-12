# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe import _


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

    frappe.msgprint(
        _("Successfully connected to Shopify!"),
        indicator="green",
        alert=True
    )

    # Redirect to the Shopify Store form
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"/app/shopify-store/{store.name}"

# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from shopify.api_version import ApiVersion
from shopify.resources import CustomCollection, Location, Shop, SmartCollection
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION, get_access_token
from nexwave_shopify_connector.nexwave_shopify.oauth import get_callback_url
from nexwave_shopify_connector.utils.logger import get_logger


class ShopifyStore(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store_collection_mapping.shopify_store_collection_mapping import (
			ShopifyStoreCollectionMapping,
		)
		from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store_item_field.shopify_store_item_field import (
			ShopifyStoreItemField,
		)
		from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store_item_filter.shopify_store_item_filter import (
			ShopifyStoreItemFilter,
		)
		from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store_tax_account.shopify_store_tax_account import (
			ShopifyStoreTaxAccount,
		)
		from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store_warehouse_mapping.shopify_store_warehouse_mapping import (
			ShopifyStoreWarehouseMapping,
		)
		from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store_webhook.shopify_store_webhook import (
			ShopifyStoreWebhook,
		)

		access_token: DF.Password | None
		add_shipping_as_item: DF.Check
		api_version: DF.Data | None
		auth_method: DF.Literal["Legacy (Access Token)", "OAuth"]
		auto_create_collections: DF.Check
		auto_create_invoice: DF.Check
		auto_create_payment_entry: DF.Check
		auto_submit_sales_order: DF.Check
		callback_url: DF.Data | None
		cash_bank_account: DF.Link | None
		client_id: DF.Data | None
		client_secret: DF.Password | None
		collection_mapping: DF.Table[ShopifyStoreCollectionMapping]
		company: DF.Link
		connected_user: DF.Link | None
		cost_center: DF.Link | None
		customer_group: DF.Link | None
		default_customer: DF.Link | None
		default_sales_tax_account: DF.Link | None
		default_shipping_charges_account: DF.Link | None
		delivery_note_series: DF.Literal[None]
		enable_image_sync: DF.Check
		enable_inventory_sync: DF.Check
		enable_item_sync: DF.Check
		enabled: DF.Check
		inventory_sync_frequency: DF.Int
		item_field_map: DF.Table[ShopifyStoreItemField]
		item_filters: DF.Table[ShopifyStoreItemFilter]
		item_group: DF.Link | None
		last_inventory_sync: DF.Datetime | None
		last_order_sync: DF.Datetime | None
		oauth_status: DF.Literal["Not Connected", "Connected"]
		price_list: DF.Link | None
		sales_invoice_series: DF.Literal[None]
		sales_order_series: DF.Literal[None]
		shared_secret: DF.Data | None
		shipping_item: DF.Link | None
		shop_domain: DF.Data
		sync_delivery_note: DF.Check
		sync_orders: DF.Check
		sync_sales_invoice: DF.Check
		tax_accounts: DF.Table[ShopifyStoreTaxAccount]
		update_shopify_on_item_update: DF.Check
		warehouse: DF.Link | None
		warehouse_mapping: DF.Table[ShopifyStoreWarehouseMapping]
		webhooks: DF.Table[ShopifyStoreWebhook]
	# end: auto-generated types
	def validate(self):
		self.normalize_shop_domain()
		self.validate_auth_method()
		self.validate_payment_method_mapping()

	def normalize_shop_domain(self):
		"""Normalize shop domain to just the domain without protocol or trailing slashes."""
		if self.shop_domain:
			domain = self.shop_domain.strip()
			# Remove protocol
			for prefix in ["https://", "http://"]:
				if domain.startswith(prefix):
					domain = domain[len(prefix) :]
			# Remove trailing slash
			domain = domain.rstrip("/")
			# Remove /admin suffix
			if domain.endswith("/admin"):
				domain = domain[:-6]
			self.shop_domain = domain

	def validate_auth_method(self):
		"""Validate and clean up fields based on authentication method."""
		auth_method = self.auth_method or "Legacy (Access Token)"

		if auth_method == "OAuth":
			# Set callback URL for OAuth (computed from site URL)
			self.callback_url = get_callback_url()
		else:
			# Clear OAuth fields when using Legacy
			if self.client_id:
				self.client_id = None
			if self.client_secret:
				self.client_secret = None
			if self.callback_url:
				self.callback_url = None
			if self.connected_user:
				self.connected_user = None
			if self.oauth_status and self.oauth_status != "Not Connected":
				self.oauth_status = "Not Connected"

	def validate_payment_method_mapping(self):
		"""Validate that there are no duplicate Shopify gateways in payment method mapping."""
		if not self.payment_method_mapping:
			return

		seen_gateways = set()
		for row in self.payment_method_mapping:
			if row.shopify_gateway in seen_gateways:
				frappe.throw(
					_("Duplicate payment method mapping for Shopify gateway '{0}'. Each gateway can only be mapped once.").format(
						row.shopify_gateway
					)
				)
			seen_gateways.add(row.shopify_gateway)

	def on_update(self):
		# TODO: Handle webhook registration/deregistration
		pass

	def _get_auth_details(self):
		"""Get authentication details for Shopify API session.

		Supports both Legacy (Access Token) and OAuth authentication methods.
		"""
		api_version = self.api_version or DEFAULT_API_VERSION
		access_token = get_access_token(self)
		return (self.shop_domain, api_version, access_token)

	def _init_shopify_api_versions(self):
		"""Initialize Shopify API versions by fetching from Shopify."""
		if not ApiVersion.versions:
			ApiVersion.fetch_known_versions()

	@frappe.whitelist()
	def test_connection(self):
		"""Test the Shopify API connection."""
		logger = get_logger()
		try:
			# Fetch available API versions from Shopify
			self._init_shopify_api_versions()

			auth_details = self._get_auth_details()

			with Session.temp(*auth_details):
				shop = Shop.current()

				frappe.msgprint(
					_("Connection successful!")
					+ "<br><br>"
					+ _("<b>Shop Name:</b> {0}").format(shop.name)
					+ "<br>"
					+ _("<b>Domain:</b> {0}").format(shop.domain)
					+ "<br>"
					+ _("<b>Email:</b> {0}").format(shop.email)
					+ "<br>"
					+ _("<b>Currency:</b> {0}").format(shop.currency)
					+ "<br>"
					+ _("<b>Plan:</b> {0}").format(shop.plan_name),
					title=_("Shopify Connection Test"),
					indicator="green",
				)
				logger.info("Connection successful! Shopify store: %s", self.shop_domain)

		except Exception as e:
			logger.error(
				"Connection failed for Shopify store: %s, error: %s", self.shop_domain, str(e), exc_info=True
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Shopify Connection Test Failed - {0}").format(self.shop_domain),
			)
			frappe.db.commit()
			frappe.throw(_("Connection failed: {0}").format(str(e)), title=_("Shopify Connection Error"))

	@frappe.whitelist()
	def fetch_shopify_locations(self):
		"""Fetch locations from Shopify and return them for the JS to populate the table."""
		logger = get_logger()
		logger.info("Fetching locations from Shopify for store: %s", self.shop_domain)
		try:
			# Fetch available API versions from Shopify
			self._init_shopify_api_versions()

			auth_details = self._get_auth_details()

			with Session.temp(*auth_details):
				locations = Location.find()

				# Build existing mappings lookup to preserve ERPNext warehouse selections
				existing_mappings = {
					row.shopify_location_id: row.erpnext_warehouse for row in self.warehouse_mapping
				}

				# Build locations list to return
				locations_data = []
				for location in locations:
					location_id = str(location.id)
					locations_data.append(
						{
							"shopify_location_id": location_id,
							"shopify_location_name": location.name,
							"erpnext_warehouse": existing_mappings.get(location_id) or "",
						}
					)

				logger.info(
					"Successfully fetched %s locations from Shopify for store: %s, locations: %s",
					len(locations_data),
					self.shop_domain,
					locations_data,
				)

				frappe.msgprint(
					_("Successfully fetched {0} location(s) from Shopify.").format(len(locations_data))
					+ "<br><br>"
					+ _("Please map each Shopify location to a NexWave warehouse and save the document."),
					title=_("Shopify Locations"),
					indicator="green",
				)

				return locations_data

		except Exception as e:
			logger.error(
				"Failed to fetch locations for Shopify store: %s, error: %s",
				self.shop_domain,
				str(e),
				exc_info=True,
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Fetch Shopify Locations Failed - {0}").format(self.shop_domain),
			)
			frappe.db.commit()
			frappe.throw(_("Failed to fetch locations: {0}").format(str(e)), title=_("Shopify Error"))

	@frappe.whitelist()
	def fetch_products_and_map_by_sku(self):
		"""Fetch products from Shopify and auto-map by SKU."""
		# TODO: Implement SKU matching logic
		frappe.msgprint(_("Fetching products and mapping by SKU..."))

	@frappe.whitelist()
	def sync_all_items(self):
		"""
		Manual trigger to sync all eligible items to this Shopify store.

		Enqueues sync jobs for all items that:
		- Have an Item Shopify Store row for this store with enabled=1
		- Match the store's item filters (if any)
		"""
		if not self.enabled:
			frappe.throw(_("Store is not enabled"))

		if not self.enable_item_sync:
			frappe.throw(_("Item sync is not enabled for this store"))

		from nexwave_shopify_connector.nexwave_shopify.product import sync_items_to_store

		sync_items_to_store(self.name)

	@frappe.whitelist()
	def sync_inventory(self):
		"""
		Manual trigger to sync all inventory to this Shopify store.

		Syncs inventory levels for all items that have Shopify product/variant IDs.
		"""
		from nexwave_shopify_connector.nexwave_shopify.inventory import manual_inventory_sync

		manual_inventory_sync(self.name)

	@frappe.whitelist()
	def fetch_shopify_collections(self):
		"""
		Fetch all collections (custom and smart) from Shopify.

		Returns collection data for JS to populate the collection_mapping table.
		Preserves existing field_value mappings where possible.
		"""
		logger = get_logger()
		logger.info("Fetching collections from Shopify for store: %s", self.shop_domain)
		try:
			self._init_shopify_api_versions()
			auth_details = self._get_auth_details()

			with Session.temp(*auth_details):
				# Fetch both custom collections and smart collections
				custom_collections = CustomCollection.find()
				smart_collections = SmartCollection.find()

				# Build existing mappings lookup to preserve field_value mappings
				existing_mappings = {}
				for row in self.collection_mapping:
					existing_mappings[row.shopify_collection_id] = row.field_value

				# Build collections list to return
				collections_data = []

				for collection in custom_collections:
					collection_id = str(collection.id)
					collections_data.append(
						{
							"shopify_collection_id": collection_id,
							"shopify_collection_title": collection.title,
							"field_value": existing_mappings.get(collection_id) or collection.title,
						}
					)

				for collection in smart_collections:
					collection_id = str(collection.id)
					collections_data.append(
						{
							"shopify_collection_id": collection_id,
							"shopify_collection_title": f"{collection.title} (Smart)",
							"field_value": existing_mappings.get(collection_id) or collection.title,
						}
					)

				frappe.msgprint(
					_("Successfully fetched {0} collection(s) from Shopify.").format(len(collections_data))
					+ "<br><br>"
					+ _("Review the Field Value for each collection, then save the document."),
					title=_("Shopify Collections"),
					indicator="green",
				)

				return collections_data

		except Exception as e:
			logger.error(
				"Failed to fetch collections for Shopify store: %s, error: %s",
				self.shop_domain,
				str(e),
				exc_info=True,
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Fetch Shopify Collections Failed - {0}").format(self.shop_domain),
			)
			frappe.db.commit()
			frappe.throw(_("Failed to fetch collections: {0}").format(str(e)), title=_("Shopify Error"))

	@frappe.whitelist()
	def fetch_and_sync_orders(self):
		"""
		Manual trigger to sync new orders from Shopify.

		Fetches orders created since the last sync (or last 30 days if first sync)
		and creates Sales Orders in NexWave.
		"""
		logger = get_logger()
		logger.info("Manual trigger to sync new orders from Shopify for store: %s", self.shop_domain)
		if not self.enabled:
			logger.warning("Shopify store: %s is not enabled", self.shop_domain)
			frappe.throw(_("Store is not enabled"))

		from nexwave_shopify_connector.nexwave_shopify.order import sync_new_orders

		logger.info("Syncing new orders from Shopify for store: %s", self.shop_domain)

		result = sync_new_orders(self.name)

		message = _("Order sync completed.") + "<br><br>"
		message += _("<b>Synced:</b> {0} orders").format(result["synced"]) + "<br>"
		message += _("<b>Skipped:</b> {0} (already exist)").format(result["skipped"]) + "<br>"
		message += _("<b>Errors:</b> {0}").format(result["errors"])

		if result["errors"] > 0:
			message += "<br><br>" + _("Check Error Log for details on failed orders.")
			logger.warning(
				"Order sync completed for store: %s, errors: %s", self.shop_domain, result["errors"]
			)

		logger.info("Order sync completed for store: %s, result: %s", self.shop_domain, result)

		frappe.msgprint(
			message,
			title=_("Shopify Order Sync"),
			indicator="green" if result["errors"] == 0 else "orange",
		)

	@frappe.whitelist()
	def register_webhooks(self):
		"""
		Register webhooks with Shopify for this store.

		Clears existing webhooks and registers all required webhook events.
		This is useful after adding new webhook events or when webhooks need to be re-registered.
		"""
		logger = get_logger()
		logger.info("Registering webhooks for Shopify store: %s", self.shop_domain)

		if not self.enabled:
			frappe.throw(_("Store is not enabled"))

		from nexwave_shopify_connector.nexwave_shopify.connection import register_webhooks

		try:
			webhooks = register_webhooks(self)
			webhook_topics = [w.topic for w in webhooks]

			logger.info(
				"Successfully registered %s webhook(s) for store %s: %s",
				len(webhooks),
				self.shop_domain,
				webhook_topics,
			)

			frappe.msgprint(
				_("Registered {0} webhook(s) with Shopify:").format(len(webhooks))
				+ "<br><br>"
				+ "<br>".join(f"• {topic}" for topic in webhook_topics),
				title=_("Webhooks Registered"),
				indicator="green",
			)

		except Exception as e:
			logger.error(
				"Failed to register webhooks for store %s: %s",
				self.shop_domain,
				str(e),
				exc_info=True,
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Webhook Registration Failed - {0}").format(self.shop_domain),
			)
			frappe.db.commit()
			frappe.throw(_("Failed to register webhooks: {0}").format(str(e)))

# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from shopify.api_version import ApiVersion
from shopify.collection import PaginatedIterator
from shopify.resources import CustomCollection, Location, Product, Shop, SmartCollection
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import (
	DEFAULT_API_VERSION,
	WEBHOOK_EVENT_FLAGS,
	WEBHOOK_EVENTS,
	get_access_token,
)
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

	# end: auto-generated types
	def validate(self):
		self.normalize_shop_domain()
		self.normalize_shop_domain_alias()
		self.validate_shop_domain_alias()
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

	def normalize_shop_domain_alias(self):
		"""Normalize shop domain alias using same logic as shop_domain."""
		if self.shop_domain_alias:
			domain = self.shop_domain_alias.strip()
			# Remove protocol
			for prefix in ["https://", "http://"]:
				if domain.startswith(prefix):
					domain = domain[len(prefix) :]
			# Remove trailing slash
			domain = domain.rstrip("/")
			# Remove /admin suffix
			if domain.endswith("/admin"):
				domain = domain[:-6]
			self.shop_domain_alias = domain

	def validate_shop_domain_alias(self):
		"""Validate that shop_domain_alias is unique across all stores."""
		if not self.shop_domain_alias:
			return

		# Cannot be same as own shop_domain
		if self.shop_domain_alias == self.shop_domain:
			frappe.throw(_("Shop Domain Alias cannot be the same as Shop Domain"))

		# Check not used as shop_domain in another store
		existing = frappe.db.get_value(
			"Shopify Store",
			{"shop_domain": self.shop_domain_alias, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(_("This alias is already used as Shop Domain in store: {0}").format(existing))

		# Check not used as alias in another store
		existing = frappe.db.get_value(
			"Shopify Store",
			{"shop_domain_alias": self.shop_domain_alias, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(_("This alias is already used in store: {0}").format(existing))

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
					_(
						"Duplicate payment method mapping for Shopify gateway '{0}'. Each gateway can only be mapped once."
					).format(row.shopify_gateway)
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
		"""Fetch products from Shopify and auto-map by SKU to ERPNext Items.

		Iterates all Shopify products (paginated), matches each variant's SKU
		against ERPNext item_code, and creates or updates Item Shopify Store rows.
		"""
		logger = get_logger()
		logger.info("Fetching products and mapping by SKU for store: %s", self.shop_domain)

		try:
			self._init_shopify_api_versions()
			auth_details = self._get_auth_details()

			# Build upfront set of valid item_codes — single DB round-trip
			erpnext_skus = _build_erpnext_sku_set()
			logger.info(
				"Built ERPNext SKU set with %d entries for store: %s",
				len(erpnext_skus),
				self.shop_domain,
			)

			total_variants = 0
			skipped_no_sku = 0
			not_found = 0
			updated = 0
			created = 0
			errors = 0

			with Session.temp(*auth_details):
				products_iter = PaginatedIterator(Product.find(limit=250))

				for products_batch in products_iter:
					for product in products_batch:
						for variant in product.variants:
							total_variants += 1
							sku = (variant.sku or "").strip()

							if not sku:
								skipped_no_sku += 1
								continue

							if sku not in erpnext_skus:
								not_found += 1
								logger.info(
									"SKU '%s' (product %s, variant %s) not found in ERPNext",
									sku,
									product.id,
									variant.id,
								)
								continue

							try:
								action = _upsert_item_store_mapping(
									item_code=sku,
									store_name=self.name,
									product_id=str(product.id),
									variant_id=str(variant.id),
									sku=sku,
								)
								if action == "updated":
									updated += 1
								else:
									created += 1
								frappe.db.commit()
							except Exception:
								errors += 1
								frappe.db.rollback()
								logger.error(
									"Failed to map SKU '%s'",
									sku,
									exc_info=True,
								)
								frappe.log_error(
									message=frappe.get_traceback(),
									title=_("SKU Mapping Error - {0}").format(self.shop_domain),
								)
								frappe.db.commit()

			matched = updated + created
			message = _("SKU mapping complete.") + "<br><br>"
			message += _("<b>Total Shopify variants scanned:</b> {0}").format(total_variants) + "<br>"
			message += _("<b>Matched to NexWave items:</b> {0}").format(matched) + "<br>"
			message += _("<b>&nbsp;&nbsp;— Updated existing:</b> {0}").format(updated) + "<br>"
			message += _("<b>&nbsp;&nbsp;— Created new:</b> {0}").format(created) + "<br>"
			message += _("<b>Skipped (no SKU on Shopify):</b> {0}").format(skipped_no_sku) + "<br>"
			message += _("<b>Not found in NexWave:</b> {0}").format(not_found) + "<br>"
			message += _("<b>Errors:</b> {0}").format(errors)

			if errors > 0:
				message += "<br><br>" + _("Check Error Log for details on failed mappings.")

			logger.info(
				"SKU mapping complete for store %s: matched=%d, updated=%d, created=%d, "
				"skipped_no_sku=%d, not_found=%d, errors=%d",
				self.shop_domain,
				matched,
				updated,
				created,
				skipped_no_sku,
				not_found,
				errors,
			)

			frappe.msgprint(
				message,
				title=_("Shopify SKU Mapping"),
				indicator="green" if errors == 0 else "orange",
			)

		except Exception as e:
			logger.error(
				"Failed to fetch products and map by SKU for store: %s, error: %s",
				self.shop_domain,
				str(e),
				exc_info=True,
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Fetch Products & Map by SKU Failed - {0}").format(self.shop_domain),
			)
			frappe.db.commit()
			frappe.throw(
				_("Failed to fetch products and map by SKU: {0}").format(str(e)),
				title=_("Shopify Error"),
			)

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

	def get_expected_webhook_topics(self) -> list[str]:
		"""
		Get the list of webhook topics that should be registered based on store settings.

		Returns:
			List of webhook topic strings that are enabled for this store.
		"""
		return [event for event in WEBHOOK_EVENTS if getattr(self, WEBHOOK_EVENT_FLAGS.get(event, ""), True)]

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
			expected_topics = self.get_expected_webhook_topics()

			# Determine missing topics
			missing_topics = [t for t in expected_topics if t not in webhook_topics]

			if missing_topics:
				logger.warning(
					"Partial webhook registration for store %s: missing topics %s",
					self.shop_domain,
					missing_topics,
				)

			if len(webhook_topics) == len(expected_topics) and not missing_topics:
				# Full success
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
			elif len(webhook_topics) == 0:
				# Complete failure
				logger.error(
					"Failed to register any webhooks for store %s: expected %s",
					self.shop_domain,
					expected_topics,
				)
				frappe.msgprint(
					_("Failed to register any webhooks with Shopify.")
					+ "<br><br>"
					+ _("<b>Expected:</b>")
					+ "<br>"
					+ "<br>".join(f"• {topic}" for topic in expected_topics)
					+ "<br><br>"
					+ _("Check NexWave Shopify Log for error details."),
					title=_("Webhook Registration Failed"),
					indicator="red",
				)
			else:
				# Partial success
				logger.warning(
					"Partially registered webhooks for store %s: registered %s, missing %s",
					self.shop_domain,
					webhook_topics,
					missing_topics,
				)
				frappe.msgprint(
					_("Partially registered webhooks with Shopify.")
					+ "<br><br>"
					+ _("<b>Registered ({0}):</b>").format(len(webhook_topics))
					+ "<br>"
					+ "<br>".join(f"• {topic}" for topic in webhook_topics)
					+ "<br><br>"
					+ _("<b>Missing ({0}):</b>").format(len(missing_topics))
					+ "<br>"
					+ "<br>".join(f"• {topic}" for topic in missing_topics)
					+ "<br><br>"
					+ _("Check NexWave Shopify Log for error details."),
					title=_("Webhook Registration Incomplete"),
					indicator="orange",
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

	@frappe.whitelist()
	def fetch_webhooks(self):
		"""Fetch registered webhooks from Shopify for this site.

		Returns:
			list: List of webhook dicts with topic, id, and address keys.
		"""
		from shopify.resources import Webhook

		from nexwave_shopify_connector.nexwave_shopify.connection import get_current_domain_name

		logger = get_logger()
		logger.info("Fetching webhooks from Shopify for store: %s", self.shop_domain)

		if not self.enabled:
			frappe.throw(_("Store is not enabled"))

		try:
			self._init_shopify_api_versions()
			auth_details = self._get_auth_details()

			with Session.temp(*auth_details):
				webhooks = Webhook.find()

				# Filter to webhooks for this site
				url = get_current_domain_name()
				site_webhooks = [
					{"topic": w.topic, "id": w.id, "address": w.address} for w in webhooks if url in w.address
				]

				logger.info(
					"Fetched %s webhook(s) for store %s",
					len(site_webhooks),
					self.shop_domain,
				)

				return site_webhooks

		except Exception as e:
			logger.error(
				"Failed to fetch webhooks for store %s: %s",
				self.shop_domain,
				str(e),
				exc_info=True,
			)
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Fetch Webhooks Failed - {0}").format(self.shop_domain),
			)
			frappe.db.commit()
			frappe.throw(_("Failed to fetch webhooks: {0}").format(str(e)))


def _build_erpnext_sku_set() -> set[str]:
	"""Build a set of all non-disabled item_code values for O(1) SKU lookups.

	The Shopify connector uses item_code as SKU (product.py build_product_payload),
	so a matching SKU string is itself the item_code -- no key-to-value mapping needed.
	"""
	return set(
		frappe.get_all("Item", filters={"disabled": 0}, pluck="item_code")
	)


def _upsert_item_store_mapping(
	item_code: str,
	store_name: str,
	product_id: str,
	variant_id: str,
	sku: str,
) -> str:
	"""Create or update an Item Shopify Store mapping row for a specific variant.

	Update priority (single pass over shopify_stores):
	1. Exact match on store + variant_id -> update in place
	2. Existing row for this store with no variant_id (blank stub) -> claim it
	3. No row exists -> append a new row

	Returns:
		"updated" or "created"
	"""
	item = frappe.get_doc("Item", item_code)

	update_data = {
		"shopify_product_id": product_id,
		"shopify_variant_id": variant_id,
		"shopify_sku": sku,
	}

	# Single pass: find exact match or blank stub
	exact_row = None
	blank_row = None
	for row in getattr(item, "shopify_stores", []) or []:
		if row.shopify_store != store_name:
			continue
		if row.shopify_variant_id == variant_id:
			exact_row = row
			break  # Priority 1 found, no need to continue
		if not row.shopify_variant_id and not blank_row:
			blank_row = row

	existing_row = exact_row or blank_row
	if existing_row:
		frappe.db.set_value(
			"Item Shopify Store", existing_row.name, update_data, update_modified=False
		)
		return "updated"

	# No suitable row -- create new
	item.reload()
	item.append(
		"shopify_stores",
		{
			"shopify_store": store_name,
			"enabled": 1,
			**update_data,
		},
	)
	item.flags.ignore_validate = True
	item.flags.ignore_mandatory = True
	item.save(ignore_permissions=True)
	return "created"

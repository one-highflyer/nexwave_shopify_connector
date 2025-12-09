# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from shopify.api_version import ApiVersion
from shopify.resources import Location, Shop
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION


class ShopifyStore(Document):
	def validate(self):
		self.normalize_shop_domain()

	def normalize_shop_domain(self):
		"""Normalize shop domain to just the domain without protocol or trailing slashes."""
		if self.shop_domain:
			domain = self.shop_domain.strip()
			# Remove protocol
			for prefix in ["https://", "http://"]:
				if domain.startswith(prefix):
					domain = domain[len(prefix):]
			# Remove trailing slash
			domain = domain.rstrip("/")
			# Remove /admin suffix
			if domain.endswith("/admin"):
				domain = domain[:-6]
			self.shop_domain = domain

	def on_update(self):
		# TODO: Handle webhook registration/deregistration
		pass

	def _get_auth_details(self):
		"""Get authentication details for Shopify API session."""
		api_version = self.api_version or DEFAULT_API_VERSION
		access_token = self.get_password("access_token")
		if not access_token:
			frappe.throw(_("Access Token is required"))
		return (self.shop_domain, api_version, access_token)

	def _init_shopify_api_versions(self):
		"""Initialize Shopify API versions by fetching from Shopify."""
		if not ApiVersion.versions:
			ApiVersion.fetch_known_versions()

	@frappe.whitelist()
	def test_connection(self):
		"""Test the Shopify API connection."""
		try:
			# Fetch available API versions from Shopify
			self._init_shopify_api_versions()

			auth_details = self._get_auth_details()

			with Session.temp(*auth_details):
				shop = Shop.current()

				frappe.msgprint(
					_("Connection successful!") + "<br><br>"
					+ _("<b>Shop Name:</b> {0}").format(shop.name) + "<br>"
					+ _("<b>Domain:</b> {0}").format(shop.domain) + "<br>"
					+ _("<b>Email:</b> {0}").format(shop.email) + "<br>"
					+ _("<b>Currency:</b> {0}").format(shop.currency) + "<br>"
					+ _("<b>Plan:</b> {0}").format(shop.plan_name),
					title=_("Shopify Connection Test"),
					indicator="green"
				)

		except Exception as e:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Shopify Connection Test Failed - {0}").format(self.shop_domain)
			)
			frappe.db.commit()
			frappe.throw(
				_("Connection failed: {0}").format(str(e)),
				title=_("Shopify Connection Error")
			)

	@frappe.whitelist()
	def fetch_shopify_locations(self):
		"""Fetch locations from Shopify and return them for the JS to populate the table."""
		try:
			# Fetch available API versions from Shopify
			self._init_shopify_api_versions()

			auth_details = self._get_auth_details()

			with Session.temp(*auth_details):
				locations = Location.find()

				# Build existing mappings lookup to preserve ERPNext warehouse selections
				existing_mappings = {row.shopify_location_id: row.erpnext_warehouse for row in self.warehouse_mapping}

				# Build locations list to return
				locations_data = []
				for location in locations:
					location_id = str(location.id)
					locations_data.append({
						"shopify_location_id": location_id,
						"shopify_location_name": location.name,
						"erpnext_warehouse": existing_mappings.get(location_id) or ""
					})

				frappe.msgprint(
					_("Successfully fetched {0} location(s) from Shopify.").format(len(locations_data))
					+ "<br><br>"
					+ _("Please map each Shopify location to an ERPNext warehouse and save the document."),
					title=_("Shopify Locations"),
					indicator="green"
				)

				return locations_data

		except Exception as e:
			frappe.log_error(
				message=frappe.get_traceback(),
				title=_("Fetch Shopify Locations Failed - {0}").format(self.shop_domain)
			)
			frappe.db.commit()
			frappe.throw(
				_("Failed to fetch locations: {0}").format(str(e)),
				title=_("Shopify Error")
			)

	@frappe.whitelist()
	def fetch_products_and_map_by_sku(self):
		"""Fetch products from Shopify and auto-map by SKU."""
		# TODO: Implement SKU matching logic
		frappe.msgprint(_("Fetching products and mapping by SKU..."))

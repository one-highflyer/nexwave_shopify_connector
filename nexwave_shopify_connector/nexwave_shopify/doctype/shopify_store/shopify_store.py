# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


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

	@frappe.whitelist()
	def fetch_shopify_locations(self):
		"""Fetch locations from Shopify and populate warehouse mapping."""
		# TODO: Implement using Shopify API
		frappe.msgprint(_("Fetching Shopify locations..."))

	@frappe.whitelist()
	def fetch_products_and_map_by_sku(self):
		"""Fetch products from Shopify and auto-map by SKU."""
		# TODO: Implement SKU matching logic
		frappe.msgprint(_("Fetching products and mapping by SKU..."))

	@frappe.whitelist()
	def test_connection(self):
		"""Test the Shopify API connection."""
		# TODO: Implement connection test
		frappe.msgprint(_("Testing connection..."))

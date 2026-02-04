# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
TaxDetector: Detects zero-rated and tax-exempt items from Shopify order data.

Zero-rated items are detected based on:
1. Shopify `taxable` field is False
2. Empty `tax_lines` array
3. All `tax_lines` have rate == 0
"""

import json

import frappe
from frappe.utils import cstr, flt

from nexwave_shopify_connector.utils.logger import get_logger


class TaxDetector:
	"""Detect zero-rated and tax-exempt items from Shopify order data."""

	def __init__(self, order: dict, store):
		"""
		Initialize TaxDetector.

		Args:
		    order: Shopify order JSON
		    store: Shopify Store document
		"""
		self.order = order
		self.store = store
		self.logger = get_logger()
		self._zero_rated_skus: set[str] = set()
		self._item_tax_rates: dict[str, dict] = {}  # sku -> {account: rate}
		self._detect_zero_rated()

	def is_zero_rated(self, sku: str) -> bool:
		"""
		Check if an item SKU is zero-rated.

		Args:
		    sku: Item SKU from Shopify

		Returns:
		    True if item has no taxes or all tax rates are 0
		"""
		return sku in self._zero_rated_skus

	def get_zero_rated_skus(self) -> set[str]:
		"""Get set of zero-rated item SKUs."""
		return self._zero_rated_skus.copy()

	def get_item_tax_rate_json(self, sku: str) -> str | None:
		"""
		Get item_tax_rate JSON for a zero-rated item.

		This JSON is used to override the default tax rate for the item
		in ERPNext's tax calculation.

		Args:
		    sku: Item SKU from Shopify

		Returns:
		    JSON string like '{"GST Account": 0}' or None if not zero-rated
		"""
		if sku not in self._item_tax_rates:
			return None
		return json.dumps(self._item_tax_rates[sku])

	def get_item_tax_template(self, sku: str, tax_title: str) -> str | None:
		"""
		Get the appropriate Item Tax Template for an item.

		Returns the configured Item Tax Template based on whether the item
		is zero-rated or taxable. Used for GST/BAS reporting compliance.

		Args:
		    sku: Item SKU from Shopify
		    tax_title: Shopify tax title to find the mapping

		Returns:
		    Item Tax Template name or None if not configured
		"""
		mapping = self._find_tax_mapping(tax_title)
		if not mapping:
			return None

		if self.is_zero_rated(sku):
			return mapping.zero_rated_item_tax_template
		else:
			return mapping.taxable_item_tax_template

	def _find_tax_mapping(self, tax_title: str):
		"""
		Find tax account mapping for a Shopify tax title.

		Args:
		    tax_title: Shopify tax title (e.g., "GST", "VAT")

		Returns:
		    Tax account mapping row or None if not found
		"""
		for mapping in self.store.tax_accounts or []:
			if mapping.shopify_tax == tax_title:
				return mapping
		return None

	def _detect_zero_rated(self):
		"""
		Detect zero-rated items from Shopify line_items.

		Logic:
		- Check if line_item has empty tax_lines OR
		- Check if line_item.taxable == False OR
		- Check if all tax_lines have rate == 0

		For zero-rated items, build item_tax_rate JSON with 0% for the tax account.
		"""
		# Get the default tax account for zero-rating
		tax_account = self._get_default_tax_account()

		for line_item in self.order.get("line_items", []):
			sku = self._get_sku(line_item)
			if not sku:
				continue

			# Check taxable flag
			is_taxable = line_item.get("taxable", True)
			tax_lines = line_item.get("tax_lines", [])

			# Detect zero-rated status
			is_zero_rated = False

			if not is_taxable:
				is_zero_rated = True
			elif not tax_lines:
				is_zero_rated = True
			elif all(flt(t.get("rate")) == 0 for t in tax_lines):
				is_zero_rated = True

			if is_zero_rated:
				self._zero_rated_skus.add(sku)
				# Build item_tax_rate JSON with 0% rate
				if tax_account:
					self._item_tax_rates[sku] = {tax_account: 0}
				else:
					self.logger.warning(
						"Zero-rated item '%s' detected but no tax account configured to apply 0%% rate. "
						"The item may be incorrectly taxed. Configure tax_accounts mapping or "
						"default_sales_tax_account in Shopify Store settings.",
						sku,
					)

	def _get_sku(self, line_item: dict) -> str:
		"""Extract SKU from line_item."""
		sku = line_item.get("sku")
		if not sku:
			sku = cstr(line_item.get("variant_id") or line_item.get("product_id"))
		return sku

	def _get_default_tax_account(self) -> str | None:
		"""Get the default tax account for building zero-rate JSON."""
		# Try to get from tax_accounts mapping
		for mapping in self.store.tax_accounts or []:
			if mapping.tax_account:
				return mapping.tax_account

		# Fallback to default
		return self.store.default_sales_tax_account

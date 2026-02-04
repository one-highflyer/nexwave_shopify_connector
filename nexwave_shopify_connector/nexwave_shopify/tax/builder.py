# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
TaxBuilder: Main orchestrator for building ERPNext tax rows from Shopify order data.

Uses "On Net Total" tax calculation instead of per-line-item "Actual" taxes.
This results in cleaner tax rows that automatically recalculate when items change.

Key features:
- Consolidates taxes by tax type (e.g., one GST row instead of 50)
- Auto-detects zero-rated items and applies Item Tax Templates or item_tax_rate JSON
- Supports Item Tax Templates for GST/BAS reporting compliance
- Handles shipping via ShippingTaxHandler
- Supports Sales Taxes and Charges Template for tax configuration
"""

import frappe
from frappe import _
from frappe.utils import cstr, flt

from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector
from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler
from nexwave_shopify_connector.utils.logger import get_logger


class TaxBuilder:
	"""Build ERPNext tax rows from Shopify order data using template-based approach."""

	def __init__(self, order: dict, store, items: list):
		"""
		Initialize TaxBuilder.

		Args:
		    order: Shopify order JSON
		    store: Shopify Store document
		    items: List of Sales Order item dicts (mutable - may be modified for zero-rating)
		"""
		self.order = order
		self.store = store
		self.items = items
		self.logger = get_logger()
		self.detector = TaxDetector(order, store)
		self.tax_rows: list[dict] = []
		self._sku_to_item_code: dict[str, str] = self._build_sku_lookup()

	def build(self) -> list[dict]:
		"""
		Build all tax rows for the Sales Order.

		Steps:
		1. Apply Item Tax Templates (or item_tax_rate fallback) to items
		2. Build tax rows from templates or manual configuration
		3. Process shipping charges

		Returns:
		    List of tax row dicts for Sales Order.taxes
		"""
		# Step 1: Apply Item Tax Templates or item_tax_rate to items
		self._apply_item_tax_templates()

		# Step 2: Build tax rows based on unique tax types in order
		self._build_tax_rows()

		# Step 3: Process shipping
		self._process_shipping()

		self.logger.info(
			"Built %d tax rows for order %s (zero-rated items: %d)",
			len(self.tax_rows),
			self.order.get("id"),
			len(self.detector.get_zero_rated_skus()),
		)

		return self.tax_rows

	def _apply_item_tax_templates(self):
		"""
		Apply Item Tax Templates or item_tax_rate to items.

		Priority:
		1. Use item_tax_template if configured for the item's tax status
		2. Fall back to item_tax_rate JSON for zero-rated items without template

		This ensures:
		- Correct tax calculation in ERPNext
		- Proper categorization in GST/BAS reports (when using Item Tax Templates)
		"""
		# Get primary tax title from order (first tax type found)
		primary_tax_title = self._get_primary_tax_title()

		for item in self.items:
			sku = self._get_original_sku(item)
			if not sku:
				continue

			# Try to get Item Tax Template
			template = self.detector.get_item_tax_template(sku, primary_tax_title)

			if template:
				item["item_tax_template"] = template
				self.logger.debug(
					"Applied item_tax_template '%s' to item %s",
					template,
					item.get("item_code"),
				)
			elif self.detector.is_zero_rated(sku):
				# Fallback: use item_tax_rate JSON for zero-rated items
				item_tax_rate = self.detector.get_item_tax_rate_json(sku)
				if item_tax_rate:
					item["item_tax_rate"] = item_tax_rate
					self.logger.debug(
						"Applied item_tax_rate fallback to item %s: %s",
						item.get("item_code"),
						item_tax_rate,
					)

	def _get_primary_tax_title(self) -> str | None:
		"""
		Get the primary (first) tax title from order line items.

		Returns:
		    First tax title found in the order, or None if no taxes
		"""
		for line_item in self.order.get("line_items", []):
			for tax_line in line_item.get("tax_lines", []):
				title = tax_line.get("title")
				if title:
					return title
		return None

	def _build_sku_lookup(self) -> dict[str, str]:
		"""
		Build a lookup dict mapping Shopify SKU to ERPNext item_code.

		This is done once at initialization to avoid O(n*m) database queries
		when applying zero-rating to items.

		Returns:
		    Dict mapping SKU -> item_code
		"""
		sku_to_item_code = {}

		for line_item in self.order.get("line_items", []):
			sku = line_item.get("sku") or cstr(line_item.get("variant_id") or line_item.get("product_id"))
			if not sku:
				continue

			# ERPNext Items can be matched by either 'name' (primary key) or 'item_code' field
			matched_item_code = frappe.db.get_value("Item", {"name": sku}) or frappe.db.get_value(
				"Item", {"item_code": sku}
			)

			if matched_item_code:
				sku_to_item_code[sku] = matched_item_code

		return sku_to_item_code

	def _get_original_sku(self, item: dict) -> str | None:
		"""
		Get the original Shopify SKU for an item.

		The item dict has item_code (ERPNext), but we need to find the
		matching Shopify line_item to get the SKU.
		"""
		item_code = item.get("item_code")

		# Use the pre-built lookup to find SKU by item_code (reverse lookup)
		for sku, matched_item_code in self._sku_to_item_code.items():
			if matched_item_code == item_code:
				return sku

		return None

	def _build_tax_rows(self):
		"""
		Build tax rows based on unique tax types found in order.

		For each unique tax type (e.g., "GST"):
		1. Look for a Sales Taxes and Charges Template in the mapping
		2. If template found, use it to create the tax row
		3. If no template, create a manual "On Net Total" row
		"""
		# Collect unique tax types and their rates
		tax_types = self._collect_unique_tax_types()

		if not tax_types:
			self.logger.info("No tax types found in order %s", self.order.get("id"))
			return

		for tax_title, tax_info in tax_types.items():
			# Find mapping for this tax type
			mapping = self._find_tax_mapping(tax_title)

			if mapping and mapping.get("sales_taxes_and_charges_template"):
				# Use template
				self._add_tax_rows_from_template(mapping.sales_taxes_and_charges_template, tax_title)
			else:
				# Create manual "On Net Total" row
				self._add_manual_tax_row(tax_title, tax_info)

	def _collect_unique_tax_types(self) -> dict:
		"""
		Collect unique tax types from line items.

		Returns:
		    Dict mapping tax_title to {rate, account} info
		"""
		tax_types = {}

		for line_item in self.order.get("line_items", []):
			for tax_line in line_item.get("tax_lines", []):
				title = tax_line.get("title")
				rate = flt(tax_line.get("rate")) * 100  # Convert 0.15 to 15

				if title not in tax_types:
					tax_types[title] = {"rate": rate, "title": title}

		self.logger.debug("Unique tax types found: %s", list(tax_types.keys()))
		return tax_types

	def _find_tax_mapping(self, tax_title: str) -> dict | None:
		"""Find tax account mapping for a Shopify tax title."""
		for mapping in self.store.tax_accounts or []:
			if mapping.shopify_tax == tax_title:
				return mapping
		return None

	def _add_tax_rows_from_template(self, template_name: str, tax_title: str):
		"""
		Add tax rows from a Sales Taxes and Charges Template.

		Args:
		    template_name: Name of the template
		    tax_title: Shopify tax title (for logging)
		"""
		try:
			from erpnext.controllers.accounts_controller import get_taxes_and_charges

			template_taxes = get_taxes_and_charges("Sales Taxes and Charges Template", template_name)

			for tax_row in template_taxes:
				# Override cost center if store has one
				if self.store.cost_center:
					tax_row["cost_center"] = self.store.cost_center

				self.tax_rows.append(tax_row)
				self.logger.info(
					"Added tax row from template '%s' for '%s': %s @ %s%%",
					template_name,
					tax_title,
					tax_row.get("account_head"),
					tax_row.get("rate"),
				)
		except Exception as e:
			self.logger.error("Failed to load template '%s': %s", template_name, str(e))
			frappe.throw(_("Failed to load Sales Taxes and Charges Template: {0}").format(template_name))

	def _add_manual_tax_row(self, tax_title: str, tax_info: dict):
		"""
		Add a manual "On Net Total" tax row.

		Used when no template is configured for the tax type.

		Args:
		    tax_title: Shopify tax title
		    tax_info: Dict with rate and other info
		"""
		# Get tax account
		tax_account = self._get_tax_account(tax_title)

		tax_row = {
			"charge_type": "On Net Total",
			"account_head": tax_account,
			"rate": tax_info["rate"],
			"description": f"{tax_title} ({tax_info['rate']:.0f}%)",
			"cost_center": self.store.cost_center,
		}

		self.tax_rows.append(tax_row)
		self.logger.info(
			"Added manual tax row for '%s': %s @ %s%%",
			tax_title,
			tax_account,
			tax_info["rate"],
		)

	def _get_tax_account(self, tax_title: str) -> str:
		"""Get tax account for a Shopify tax title."""
		# Check mapping
		for mapping in self.store.tax_accounts or []:
			if mapping.shopify_tax == tax_title:
				return mapping.tax_account

		# Fallback to default
		if self.store.default_sales_tax_account:
			return self.store.default_sales_tax_account

		frappe.throw(_("Tax Account not configured for Shopify tax: {0}").format(tax_title))

	def _process_shipping(self):
		"""Process shipping charges and add to tax rows."""
		handler = ShippingTaxHandler(
			store=self.store,
			items=self.items,
			order=self.order,
			current_tax_row_count=len(self.tax_rows),
		)

		shipping_rows = handler.build()
		self.tax_rows.extend(shipping_rows)

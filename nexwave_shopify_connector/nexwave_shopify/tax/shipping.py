# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
ShippingTaxHandler: Handles shipping charges and taxes for Shopify orders.

Supports two modes based on store.add_shipping_as_item:
1. Shipping as line item: Added to items list, taxed via "On Net Total"
2. Shipping as tax row: Added as "Actual" charge, GST via "On Previous Row Amount"
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from nexwave_shopify_connector.utils.logger import get_logger


class ShippingTaxHandler:
	"""Handle shipping charges and taxes for Shopify orders."""

	def __init__(self, store, items: list, order: dict, current_tax_row_count: int = 0):
		"""
		Initialize ShippingTaxHandler.

		Args:
		    store: Shopify Store document
		    items: List of Sales Order item dicts (mutable - may append shipping item)
		    order: Shopify order JSON
		    current_tax_row_count: Number of tax rows already added (for row_id calculation)
		"""
		self.store = store
		self.items = items
		self.order = order
		self.current_tax_row_count = current_tax_row_count
		self.logger = get_logger()

	def build(self) -> list[dict]:
		"""
		Build shipping-related tax rows.

		If add_shipping_as_item is True:
		    - Appends shipping item to self.items
		    - Returns empty list (tax handled by On Net Total)

		If add_shipping_as_item is False:
		    - Returns list with shipping charge row + GST on shipping row

		Returns:
		    List of tax row dicts
		"""
		tax_rows = []
		shipping_lines = self.order.get("shipping_lines", [])
		taxes_inclusive = self.order.get("taxes_included", False)
		delivery_date = self.items[-1]["delivery_date"] if self.items else nowdate()
		running_row_count = self.current_tax_row_count

		for shipping in shipping_lines:
			if not flt(shipping.get("price")):
				continue

			# Calculate shipping amount (net of discounts and taxes if inclusive)
			shipping_amount = self._calculate_shipping_amount(shipping, taxes_inclusive)

			if self.store.add_shipping_as_item:
				# Validate shipping_item is configured
				if not self.store.shipping_item:
					frappe.throw(
						_(
							"Shipping Item not configured in Shopify Store '{0}'. "
							"Please configure a Shipping Item or disable 'Add Shipping as Item'."
						).format(self.store.name)
					)
				# Add shipping as line item - tax handled by "On Net Total"
				self._add_shipping_item(shipping, shipping_amount, delivery_date)
			else:
				# Add shipping as tax row + GST on shipping
				shipping_rows = self._add_shipping_tax_rows(shipping, shipping_amount, running_row_count)
				tax_rows.extend(shipping_rows)
				running_row_count += len(shipping_rows)

		return tax_rows

	def _calculate_shipping_amount(self, shipping: dict, taxes_inclusive: bool) -> float:
		"""
		Calculate net shipping amount.

		Args:
		    shipping: Shopify shipping_line dict
		    taxes_inclusive: Whether taxes are included in prices

		Returns:
		    Net shipping amount
		"""
		price = flt(shipping.get("price"))

		# Subtract discounts
		discounts = shipping.get("discount_allocations") or []
		total_discount = sum(flt(d.get("amount")) for d in discounts)

		# Subtract taxes if inclusive
		if taxes_inclusive:
			taxes = shipping.get("tax_lines") or []
			total_tax = sum(flt(t.get("price")) for t in taxes)
			return price - total_discount - total_tax

		return price - total_discount

	def _add_shipping_item(self, shipping: dict, amount: float, delivery_date):
		"""
		Add shipping as a line item.

		The tax will be calculated by the "On Net Total" tax row.

		Args:
		    shipping: Shopify shipping_line dict
		    amount: Net shipping amount
		    delivery_date: Delivery date for the item
		"""
		self.items.append(
			{
				"item_code": self.store.shipping_item,
				"item_name": shipping.get("title") or "Shipping",
				"rate": amount,
				"qty": 1,
				"delivery_date": delivery_date,
				"warehouse": self.store.warehouse,
				"cost_center": self.store.cost_center,
			}
		)
		self.logger.info("Added shipping as item: %s (amount: %s)", self.store.shipping_item, amount)

	def _add_shipping_tax_rows(self, shipping: dict, amount: float, running_row_count: int) -> list[dict]:
		"""
		Add shipping as tax rows with GST on shipping.

		Creates:
		1. "Actual" charge for shipping amount
		2. "On Previous Row Amount" for GST on shipping

		Args:
		    shipping: Shopify shipping_line dict
		    amount: Net shipping amount
		    running_row_count: Current count of tax rows (for correct row_id calculation)

		Returns:
		    List of tax row dicts
		"""
		rows = []
		shipping_taxes = shipping.get("tax_lines") or []

		# 1. Add shipping charge as "Actual"
		shipping_account = self._get_shipping_account(shipping.get("title"))
		shipping_row = {
			"charge_type": "Actual",
			"account_head": shipping_account,
			"description": shipping.get("title") or "Shipping",
			"tax_amount": amount,
			"cost_center": self.store.cost_center,
		}
		rows.append(shipping_row)
		self.logger.info("Added shipping as Actual tax row: %s", amount)

		# 2. Add GST on shipping as "On Previous Row Amount"
		if shipping_taxes:
			# Calculate the row_id for the shipping row we just added
			# row_id is 1-indexed in ERPNext
			shipping_row_id = running_row_count + len(rows)

			for tax in shipping_taxes:
				tax_account = self._get_tax_account(tax.get("title"))
				tax_rate = flt(tax.get("rate")) * 100  # Convert 0.15 to 15

				gst_row = {
					"charge_type": "On Previous Row Amount",
					"account_head": tax_account,
					"rate": tax_rate,
					"row_id": shipping_row_id,
					"description": f"{tax.get('title')} on Shipping ({tax_rate:.0f}%)",
					"cost_center": self.store.cost_center,
				}
				rows.append(gst_row)
				self.logger.info(
					"Added shipping GST as On Previous Row Amount: %s%% (row_id: %s)",
					tax_rate,
					shipping_row_id,
				)

		return rows

	def _get_shipping_account(self, title: str) -> str:
		"""Get shipping charges account."""
		# Try mapping first
		for mapping in self.store.tax_accounts or []:
			if mapping.shopify_tax == title:
				return mapping.tax_account

		# Fallback to default
		if self.store.default_shipping_charges_account:
			return self.store.default_shipping_charges_account

		frappe.throw(_("Shipping charges account not configured for: {0}").format(title))

	def _get_tax_account(self, tax_title: str) -> str:
		"""Get tax account for shipping taxes."""
		for mapping in self.store.tax_accounts or []:
			if mapping.shopify_tax == tax_title:
				return mapping.tax_account

		if self.store.default_sales_tax_account:
			return self.store.default_sales_tax_account

		frappe.throw(_("Tax Account not configured for Shopify tax: {0}").format(tax_title))

# Copyright (c) 2025, HighFlyer and Contributors
# See license.txt

from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from nexwave_shopify_connector.nexwave_shopify.order import _get_item_price


class TestGetItemPrice(FrappeTestCase):
	"""Test _get_item_price with real data from Shopify order #AN1870.

	These tests verify the fix for the rounding error that caused a 7-cent
	mismatch between Shopify order totals and NexWave Sales Order totals.
	"""

	def test_non_terminating_decimal_sku_10708(self):
		"""Test SKU 10708 where tax/qty creates a non-terminating decimal.

		Real data from Shopify order #AN1870:
		- Item: "Her Words More than Almost Anything"
		- Price: $8.25 (tax-inclusive), Qty: 3, Tax: $3.23

		OLD (BUGGY) BEHAVIOR:
		- Calculated rate = price - (tax / qty)
		- rate = 8.25 - (3.23 / 3) = 8.25 - 1.0766666... = 7.173333...
		- After 2-decimal rounding: rate = 7.17
		- Line amount = 7.17 * 3 = 21.51 (WRONG - 1 cent short)

		FIXED BEHAVIOR:
		- Calculate line_amount first: (8.25 * 3) - 3.23 = 21.52
		- Derive rate with high precision: 21.52 / 3 = 7.173333333
		- Line amount = 7.173333333 * 3 = 21.52 (CORRECT)
		"""
		line_item = {
			"price": "8.25",
			"quantity": 3,
			"tax_lines": [{"price": "3.23"}],
			"discount_allocations": [],
		}

		rate = _get_item_price(line_item, taxes_inclusive=True)

		# The fix ensures rate * qty reproduces the exact line amount
		expected_line_amount = 21.52
		actual_line_amount = flt(rate * 3, 2)

		self.assertEqual(actual_line_amount, expected_line_amount)

	def test_single_quantity_sku_10989(self):
		"""Test SKU 10989 with qty=1 (no division issue).

		Real data from Shopify order #AN1870:
		- Item: "Dogs Just Know"
		- Price: $14.97 (tax-inclusive), Qty: 1, Tax: $1.95

		With qty=1, there's no division so no rounding issue occurs.
		Both old and fixed approaches produce the same result.

		CALCULATION:
		- line_amount = 14.97 - 1.95 = 13.02
		- rate = 13.02 / 1 = 13.02
		"""
		line_item = {
			"price": "14.97",
			"quantity": 1,
			"tax_lines": [{"price": "1.95"}],
			"discount_allocations": [],
		}

		rate = _get_item_price(line_item, taxes_inclusive=True)

		self.assertEqual(flt(rate, 2), 13.02)

	def test_evenly_divisible_sku_8035(self):
		"""Test SKU 8035 where the division is clean (no rounding issue).

		Real data from Shopify order #AN1870:
		- Item: "GIFT BOOK THE PERSISTANCE OF YELLOW"
		- Price: $17.48 (tax-inclusive), Qty: 3, Tax: $6.84

		This case divides evenly, so both old and fixed code work.

		CALCULATION:
		- line_amount = (17.48 * 3) - 6.84 = 52.44 - 6.84 = 45.60
		- rate = 45.60 / 3 = 15.20 (divides evenly)
		"""
		line_item = {
			"price": "17.48",
			"quantity": 3,
			"tax_lines": [{"price": "6.84"}],
			"discount_allocations": [],
		}

		rate = _get_item_price(line_item, taxes_inclusive=True)

		expected_line_amount = 45.60
		actual_line_amount = flt(rate * 3, 2)

		self.assertEqual(actual_line_amount, expected_line_amount)

	def test_tax_exclusive_no_discount(self):
		"""Test tax-exclusive pricing (taxes_inclusive=False).

		When taxes are not included in the price, no tax subtraction occurs.

		CALCULATION:
		- line_amount = price * qty = 10.00 * 3 = 30.00
		- rate = 30.00 / 3 = 10.00
		"""
		line_item = {
			"price": "10.00",
			"quantity": 3,
			"discount_allocations": [],
		}

		rate = _get_item_price(line_item, taxes_inclusive=False)

		self.assertEqual(flt(rate * 3, 2), 30.00)

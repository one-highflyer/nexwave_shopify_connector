# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from nexwave_shopify_connector.nexwave_shopify.order import (
	_create_sales_invoice,
	_create_sales_order,
	_get_order_items,
)
from nexwave_shopify_connector.nexwave_shopify.tax.fixtures import (
	create_test_items,
	get_test_store,
	load_shopify_order,
	setup_tax_test_data,
)

test_dependencies = ["Company", "Customer", "Price List"]


class TestShopifyOrderDiscounts(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_tax_test_data()
		cls.store = get_test_store()
		cls.store.price_list = "_Test Price List"
		create_test_items(["TEST-PROMO-ITEM", "TEST-PAID-ITEM"])
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": "TEST-PROMO-ITEM",
				"price_list": cls.store.price_list,
				"price_list_rate": 80,
			}
		).insert()

	def setUp(self):
		self.order = load_shopify_order("discounted_lines.json")
		self.order.update(
			id=self._testMethodName,
			created_at=nowdate(),
			currency=frappe.get_cached_value("Company", self.store.company, "default_currency"),
		)

	def test_mixed_invoice_posts_only_tax_on_paid_goods_and_freight(self):
		so = _create_sales_order(self.order, self.store, "_Test Customer")
		so.submit()
		si = _create_sales_invoice(so, self.order, self.store)
		for doc in (so.reload(), si.reload()):
			free, paid = doc.items
			self.assertEqual(free.rate, 0)
			self.assertEqual(free.price_list_rate, 10)
			self.assertEqual(free.discount_percentage, 100)
			self.assertEqual(free.discount_amount, 10)
			self.assertEqual(free.net_amount, 0)
			self.assertEqual(free.qty, 2)
			self.assertEqual(paid.net_amount, 30)
			self.assertEqual(doc.grand_total, flt(self.order["total_price"]))
			self.assertFalse(any("Rounding Adjustment" in row.description for row in doc.taxes))
		self.assertEqual(so.items[0].shopify_item_discount, 11.5)
		self.assertEqual(so.items[0].shopify_line_item_id, "1")
		entries = frappe.get_all(
			"GL Entry",
			filters={
				"voucher_type": "Sales Invoice",
				"voucher_no": si.name,
				"account": self.store.default_sales_tax_account,
				"is_cancelled": 0,
			},
			fields=["credit", "debit"],
		)
		self.assertEqual(sum(row.credit - row.debit for row in entries), flt(self.order["total_tax"]))

	def test_fully_discounted_tax_exclusive_order(self):
		self.order.update(taxes_included=False, total_price="0.00", total_tax="0.00", shipping_lines=[])
		self.order["line_items"] = self.order["line_items"][:1]
		so = _create_sales_order(self.order, self.store, "_Test Customer")
		so.submit()
		for doc in (so, _create_sales_invoice(so, self.order, self.store)):
			self.assertEqual(doc.items[0].price_list_rate, 11.5)
			self.assertEqual(doc.items[0].discount_percentage, 100)
			self.assertEqual(doc.items[0].amount, 0)
			self.assertEqual(doc.grand_total, 0)

	def test_full_discount_with_large_quantity(self):
		self.order.update(taxes_included=False)
		self.order["line_items"][0].update(
			price="512.06", quantity=10000, discount_allocations=[{"amount": "5120600.00"}]
		)
		item = _get_order_items(self.order, self.store)[0]
		self.assertEqual(item.get("discount_percentage"), 100)
		self.assertEqual(item["price_list_rate"], 512.06)

	def test_one_cent_outstanding_on_a_line_is_a_partial_discount(self):
		line = self.order["line_items"][0]
		line.update(quantity=10, discount_allocations=[{"amount": "114.99"}])
		item = _get_order_items(self.order, self.store)[0]
		self.assertEqual(item["rate"], 0.001)
		self.assertNotEqual(item.get("discount_percentage"), 100)

	def test_fully_discounted_untaxed_line_keeps_original_price(self):
		self.order["line_items"][0].update(taxable=False, tax_lines=[])
		item = _get_order_items(self.order, self.store)[0]
		self.assertEqual(item["price_list_rate"], 11.5)
		self.assertEqual(item["discount_percentage"], 100)

	def test_original_zero_price_is_not_a_discount(self):
		line = self.order["line_items"][0]
		line.update(price="0.00", discount_allocations=[])
		so = _create_sales_order(self.order, self.store, "_Test Customer")
		so.submit()
		for doc in (so, _create_sales_invoice(so, self.order, self.store)):
			item = doc.items[0]
			self.assertEqual(item.rate, 0)
			self.assertEqual(item.price_list_rate, 0)
			self.assertEqual(item.discount_percentage, 0)

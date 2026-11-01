# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note, make_sales_invoice
from frappe.tests.utils import FrappeTestCase, change_settings

from nexwave_shopify_connector.nexwave_shopify._auto_link_test_fixtures import (
	make_item,
	make_order,
	make_store,
)
from nexwave_shopify_connector.nexwave_shopify.order import cancel_order

test_dependencies = ["Company", "Customer", "Warehouse", "Item"]


class TestOrderCancellation(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.documents = []
		self.store = None
		self.addCleanup(self.cleanup_documents)
		self.store = make_store(enabled=0, enable_inventory_sync=0, auto_link_items_by_sku=0)
		self.documents.append((self.store.doctype, self.store.name))
		self.item = make_item(is_stock_item=0)
		self.documents.append((self.item.doctype, self.item.name))
		self.order = make_order(self.store, {"item_code": self.item.name})
		self.documents.append((self.order.doctype, self.order.name))
		self.order.shopify_financial_status = "paid"
		self.order.save()
		self.order.submit()

	def cleanup_documents(self):
		"""The webhook log commits, so explicitly remove this test's documents."""
		frappe.db.rollback()
		if self.store:
			frappe.db.delete("NexWave Shopify Log", {"shopify_store": self.store.name})
		with change_settings("Accounts Settings", {"delete_linked_ledger_entries": 1}):
			for doctype, name in reversed(self.documents):
				if not frappe.db.exists(doctype, name):
					continue
				doc = frappe.get_doc(doctype, name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc(doctype, name, force=True)
				if doctype in ("Sales Invoice", "Delivery Note"):
					for ledger in ("GL Entry", "Payment Ledger Entry", "Stock Ledger Entry"):
						self.assertFalse(
							frappe.db.exists(ledger, {"voucher_type": doctype, "voucher_no": name})
						)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- remove fixtures committed by webhook logging

	def make_linked_document(self, doctype):
		mapper = make_sales_invoice if doctype == "Sales Invoice" else make_delivery_note
		doc = mapper(self.order.name)
		doc.shopify_store = self.store.name
		doc.shopify_order_id = self.order.shopify_order_id
		doc.insert(set_name="_T-Shopify-Cancel-" + frappe.generate_hash(length=12))
		self.documents.append((doc.doctype, doc.name))
		doc.submit()
		return doc

	def cancel_shopify_order(self):
		# A webhook starts after the order and linked documents have committed.
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- preserve real documents across webhook rollback
		cancel_order(
			{"id": self.order.shopify_order_id, "financial_status": "refunded"},
			shopify_store=self.store.name,
		)
		self.order.reload()

	def assert_linked_cancellation(self, *doctypes):
		linked_documents = [self.make_linked_document(doctype) for doctype in doctypes]
		self.cancel_shopify_order()
		self.assertEqual(self.order.docstatus, 1)
		self.assertEqual(self.order.shopify_financial_status, "refunded")
		for doc in linked_documents:
			doc.reload()
			self.assertEqual(doc.docstatus, 1)
			self.assertEqual(doc.shopify_financial_status, "refunded")
		self.assertTrue(
			frappe.db.exists(
				"NexWave Shopify Log",
				{"shopify_store": self.store.name, "status": "Success", "reference_name": self.order.name},
			)
		)

	def test_cancel_with_sales_invoice(self):
		self.assert_linked_cancellation("Sales Invoice")

	def test_cancel_with_delivery_note(self):
		self.assert_linked_cancellation("Delivery Note")

	def test_cancel_with_invoice_and_delivery_note(self):
		self.assert_linked_cancellation("Sales Invoice", "Delivery Note")

	def test_cancel_without_linked_documents(self):
		self.cancel_shopify_order()
		self.assertEqual(self.order.docstatus, 2)

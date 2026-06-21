# Copyright (c) 2025, HighFlyer and Contributors
# See license.txt

import frappe
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from frappe.tests.utils import FrappeTestCase, change_settings
from frappe.utils import flt, nowdate

from nexwave_shopify_connector.nexwave_shopify._inventory_test_fixtures import (
	TEST_COMPANY,
	_get_default_warehouse,
	ensure_test_item,
	ensure_test_shopify_store,
)
from nexwave_shopify_connector.nexwave_shopify.inventory import _bulk_get_stock_qty, get_stock_qty
from nexwave_shopify_connector.nexwave_shopify.order import (
	_create_or_update_address,
	_create_sales_order,
	_get_item_price,
	_reserve_stock_for_draft_shopify_order,
	_sync_customer,
	cancel_order,
)
from nexwave_shopify_connector.nexwave_shopify.utils import sanitize_phone_number


def _ensure_leaf_customer_group():
	"""Ensure a non-group (leaf) Customer Group exists for test customers.

	On a fresh CI site, the default customer group from Selling Settings is
	'All Customer Groups', which is a group node. ERPNext rejects group-type
	customer groups when creating a Customer, so we need a leaf node.
	"""
	leaf_group = "_Test Customer Group"
	if not frappe.db.exists("Customer Group", leaf_group):
		frappe.get_doc(
			{
				"doctype": "Customer Group",
				"customer_group_name": leaf_group,
				"parent_customer_group": "All Customer Groups",
				"is_group": 0,
			}
		).insert(ignore_if_duplicate=True)
	return leaf_group


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


class TestSanitizePhoneNumber(FrappeTestCase):
	"""Test sanitize_phone_number for Shopify phone variations."""

	def test_extension_with_ext(self):
		"""Phone with 'ext 651' should strip alphabetic chars."""
		sanitized, original = sanitize_phone_number("09 836 7700 ext 651")
		self.assertEqual(sanitized, "09 836 7700")
		self.assertEqual(original, "09 836 7700 ext 651")

	def test_extension_with_x(self):
		"""Phone with 'x100' should strip the 'x'."""
		sanitized, original = sanitize_phone_number("+1-555-1234 x100")
		self.assertEqual(sanitized, "+1-555-1234")
		self.assertEqual(original, "+1-555-1234 x100")

	def test_alphabetic_phone(self):
		"""Phone with alphabetic chars like '1-800-FLOWERS' should strip letters."""
		sanitized, original = sanitize_phone_number("1-800-FLOWERS")
		self.assertEqual(sanitized, "1-800-")
		self.assertEqual(original, "1-800-FLOWERS")

	def test_too_long_with_invalid_chars(self):
		"""Phone with invalid chars and >20 chars after stripping."""
		sanitized, original = sanitize_phone_number("+64 21 123 456 extension 789")
		self.assertEqual(sanitized, "+64 21 123 456")
		self.assertEqual(original, "+64 21 123 456 extension 789")

	def test_too_long_valid_chars_only(self):
		"""Phone with only valid chars but exceeding 20 char limit should be truncated."""
		sanitized, original = sanitize_phone_number("+64 21 123 456 78901234")
		self.assertEqual(sanitized, "+64 21 123 456 78901")
		self.assertEqual(original, "+64 21 123 456 78901234")

	def test_already_valid(self):
		"""Valid phone should pass through unchanged."""
		sanitized, original = sanitize_phone_number("+64 21 123 456")
		self.assertEqual(sanitized, "+64 21 123 456")
		self.assertIsNone(original)

	def test_none_input(self):
		"""None input should return (None, None)."""
		sanitized, original = sanitize_phone_number(None)
		self.assertIsNone(sanitized)
		self.assertIsNone(original)

	def test_empty_string(self):
		"""Empty string should return (None, None)."""
		sanitized, original = sanitize_phone_number("")
		self.assertIsNone(sanitized)
		self.assertIsNone(original)

	def test_only_invalid_chars(self):
		"""Phone with only invalid chars should return None for sanitized."""
		sanitized, original = sanitize_phone_number("ext")
		self.assertIsNone(sanitized)
		self.assertEqual(original, "ext")

	def test_whitespace_only(self):
		"""Whitespace-only string should return (None, None)."""
		sanitized, original = sanitize_phone_number("   ")
		self.assertIsNone(sanitized)
		self.assertIsNone(original)

	def test_hash_extension(self):
		"""Phone with '#651' extension should keep # (it's allowed)."""
		sanitized, original = sanitize_phone_number("09 836 7700 #651")
		self.assertEqual(sanitized, "09 836 7700 #651")
		self.assertIsNone(original)

	def test_parentheses_preserved(self):
		"""Parentheses in phone should be preserved."""
		sanitized, original = sanitize_phone_number("(09) 836 7700")
		self.assertEqual(sanitized, "(09) 836 7700")
		self.assertIsNone(original)


class TestSyncCustomerMatching(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.created_contacts = []
		self.created_customers = []

	def tearDown(self):
		for contact_name in reversed(self.created_contacts):
			if frappe.db.exists("Contact", contact_name):
				frappe.delete_doc("Contact", contact_name, force=1, ignore_permissions=True)

		for customer_name in reversed(self.created_customers):
			if frappe.db.exists("Customer", customer_name):
				frappe.delete_doc("Customer", customer_name, force=1, ignore_permissions=True)

		super().tearDown()

	def make_customer(self, customer_name, disabled=0):
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"customer_group": _ensure_leaf_customer_group(),
				"territory": frappe.db.get_single_value("Selling Settings", "territory"),
				"disabled": disabled,
			}
		)
		customer.insert(ignore_permissions=True)
		self.created_customers.append(customer.name)
		return customer

	def make_contact(self, customer, email):
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": customer.customer_name.split()[0],
				"email_ids": [{"email_id": email, "is_primary": 1}],
				"links": [{"link_doctype": "Customer", "link_name": customer.name}],
			}
		)
		contact.insert(ignore_permissions=True)
		self.created_contacts.append(contact.name)
		return contact

	def make_order(self, email, customer_id):
		return {
			"id": f"order-{customer_id}",
			"name": f"#T{customer_id}",
			"email": email,
			"customer": {
				"id": customer_id,
				"email": email,
				"first_name": "_Test",
				"last_name": "Buyer",
			},
			"billing_address": {},
			"shipping_address": {},
		}

	def test_contact_email_match_ignores_disabled_customers(self):
		email = "_test_active_match@example.test"
		disabled_customer = self.make_customer("_Test Disabled Contact Match", disabled=1)
		enabled_customer = self.make_customer("_Test Enabled Contact Match")
		for customer in (disabled_customer, enabled_customer):
			self.make_contact(customer, email)

		customer_name, contact_name, billing_addr, shipping_addr = _sync_customer(
			self.make_order(email, "9001"), frappe._dict({})
		)

		self.assertEqual(customer_name, enabled_customer.name)
		self.assertTrue(contact_name)
		self.assertIsNone(billing_addr)
		self.assertIsNone(shipping_addr)
		self.assertEqual(
			frappe.db.get_value("Customer", enabled_customer.name, "shopify_customer_id"), "9001"
		)

	def test_contact_email_match_blocks_disabled_only_customers(self):
		email = "_test_disabled_only@example.test"
		disabled_customer = self.make_customer("_Test Disabled Only Contact Match", disabled=1)
		self.make_contact(disabled_customer, email)

		with self.assertRaises(frappe.ValidationError):
			_sync_customer(self.make_order(email, "9002"), frappe._dict({}))


class TestDraftShopifyOrderStockReservation(FrappeTestCase):
	def cleanup_doc(self, doctype, name):
		if not frappe.db.exists(doctype, name):
			return

		doc = frappe.get_doc(doctype, name)
		if doctype == "Sales Order":
			self.cancel_sales_order_reservations(doc)

		if getattr(doc, "docstatus", 0) == 1:
			doc.cancel()

		frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)

	def cancel_sales_order_reservations(self, sales_order):
		reservations = frappe.get_all(
			"Stock Reservation Entry",
			filters={
				"voucher_type": "Sales Order",
				"voucher_no": sales_order.name,
				"docstatus": 1,
				"status": ["not in", ["Delivered", "Cancelled"]],
			},
			pluck="name",
		)
		if reservations:
			sales_order.cancel_stock_reservation_entries(sre_list=reservations, notify=False)

	def register_doc_cleanup(self, doc):
		self.addCleanup(self.cleanup_doc, doc.doctype, doc.name)
		return doc

	def cleanup_shopify_logs(self, shopify_store):
		frappe.db.delete("NexWave Shopify Log", {"shopify_store": shopify_store})

	def make_customer(self, customer_name):
		if frappe.db.exists("Customer", customer_name):
			customer = frappe.get_doc("Customer", customer_name)
			self.register_doc_cleanup(customer)
			return customer

		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"customer_group": _ensure_leaf_customer_group(),
				"territory": frappe.db.get_single_value("Selling Settings", "territory"),
			}
		)
		customer.insert(ignore_permissions=True)
		self.register_doc_cleanup(customer)
		return customer

	def make_store(self):
		store = ensure_test_shopify_store(
			shop_domain="_test-draft-reservation.myshopify.com",
			warehouse=_get_default_warehouse(),
			price_list="_Test Price List",
			cost_center=self.get_cost_center(),
			auto_submit_sales_order=0,
			auto_create_invoice=0,
		)
		self.addCleanup(self.cleanup_shopify_logs, store.name)
		return store

	def get_cost_center(self):
		return frappe.db.get_value(
			"Cost Center",
			{"company": TEST_COMPANY, "is_group": 0},
			"name",
		)

	def get_difference_account(self):
		return (
			frappe.db.get_value("Account", "Stock Adjustment - _TC", "name")
			or frappe.db.get_value(
				"Account",
				{"company": TEST_COMPANY, "is_group": 0, "root_type": "Expense"},
				"name",
			)
			or frappe.db.get_value("Account", {"company": TEST_COMPANY, "is_group": 0}, "name")
		)

	def make_order_payload(self, order_id, item_code, qty=2, price=10):
		return {
			"id": order_id,
			"name": f"#{order_id}",
			"financial_status": "pending",
			"fulfillment_status": None,
			"note": None,
			"currency": frappe.db.get_value("Company", TEST_COMPANY, "default_currency") or "NZD",
			"created_at": nowdate(),
			"taxes_included": False,
			"total_price": flt(qty * price),
			"line_items": [
				{
					"sku": item_code,
					"title": item_code,
					"name": item_code,
					"quantity": qty,
					"price": price,
					"discount_allocations": [],
					"tax_lines": [],
				}
			],
			"shipping_lines": [],
		}

	def prepare_stock_item(self, item_code, actual_qty=5):
		warehouse = _get_default_warehouse()
		ensure_test_item(item_code, is_stock_item=1)
		stock_entry = make_stock_entry(
			item_code=item_code,
			target=warehouse,
			qty=actual_qty,
			basic_rate=100,
			expense_account=self.get_difference_account(),
			cost_center=self.get_cost_center(),
		)
		self.register_doc_cleanup(stock_entry)
		frappe.db.set_value(
			"Bin",
			{"item_code": item_code, "warehouse": warehouse},
			"reserved_qty",
			0,
			update_modified=False,
		)
		return warehouse

	def get_active_reservations(self, sales_order):
		return frappe.get_all(
			"Stock Reservation Entry",
			filters={
				"voucher_type": "Sales Order",
				"voucher_no": sales_order.name,
				"docstatus": 1,
			},
			fields=["name", "reserved_qty", "voucher_detail_no"],
			order_by="creation asc",
		)

	def get_bin_reserved_qty(self, item_code, warehouse):
		return flt(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "reserved_qty")
		)

	def assert_shopify_available_qty(self, item_code, warehouse, expected_qty):
		self.assertEqual(get_stock_qty(item_code, warehouse), expected_qty)
		self.assertEqual(
			_bulk_get_stock_qty([(item_code, warehouse)])[(item_code, warehouse)],
			expected_qty,
		)

	@change_settings("Stock Settings", {"enable_stock_reservation": 1, "allow_partial_reservation": 1})
	def test_draft_shopify_order_creates_stock_reservation(self):
		item_code = "_Test Shopify Draft Reserve Item 1"
		warehouse = self.prepare_stock_item(item_code)
		store = self.make_store()
		customer = self.make_customer("_Test Draft Reservation Customer 1")

		so = _create_sales_order(
			self.make_order_payload("draft-reserve-1", item_code, qty=2),
			store,
			customer.name,
		)
		self.register_doc_cleanup(so)

		reservations = self.get_active_reservations(so)
		self.assertEqual(so.docstatus, 0)
		self.assertEqual(so.reserve_stock, 1)
		self.assertEqual(so.items[0].reserve_stock, 1)
		self.assertEqual(len(reservations), 1)
		self.assertEqual(flt(reservations[0].reserved_qty), 2)
		self.assertEqual(flt(so.items[0].stock_reserved_qty), 2)
		self.assertEqual(self.get_bin_reserved_qty(item_code, warehouse), 0)
		self.assert_shopify_available_qty(item_code, warehouse, 3)

	@change_settings("Stock Settings", {"enable_stock_reservation": 1, "allow_partial_reservation": 1})
	def test_draft_reservation_is_not_duplicated_on_submit(self):
		item_code = "_Test Shopify Draft Reserve Item 2"
		self.prepare_stock_item(item_code)
		store = self.make_store()
		customer = self.make_customer("_Test Draft Reservation Customer 2")

		so = _create_sales_order(
			self.make_order_payload("draft-reserve-2", item_code, qty=2),
			store,
			customer.name,
		)
		self.register_doc_cleanup(so)
		_reserve_stock_for_draft_shopify_order(so)
		self.assertEqual(len(self.get_active_reservations(so)), 1)

		so.submit()
		self.assertEqual(len(self.get_active_reservations(so)), 1)
		self.assertEqual(flt(so.items[0].stock_reserved_qty), 2)

	@change_settings("Stock Settings", {"enable_stock_reservation": 1, "allow_partial_reservation": 1})
	def test_draft_reservation_can_be_released_on_cancellation(self):
		item_code = "_Test Shopify Draft Reserve Item 3"
		warehouse = self.prepare_stock_item(item_code)
		store = self.make_store()
		customer = self.make_customer("_Test Draft Reservation Customer 3")
		order = self.make_order_payload("draft-reserve-3", item_code, qty=2)

		so = _create_sales_order(
			order,
			store,
			customer.name,
		)
		self.register_doc_cleanup(so)
		self.assertEqual(len(self.get_active_reservations(so)), 1)

		cancel_order(
			{**order, "financial_status": "voided", "cancel_reason": "customer"}, shopify_store=store.name
		)
		so.reload()

		self.assertEqual(so.shopify_financial_status, "voided")
		self.assertEqual(len(self.get_active_reservations(so)), 0)
		self.assertEqual(self.get_bin_reserved_qty(item_code, warehouse), 0)
		self.assert_shopify_available_qty(item_code, warehouse, 5)


class TestCreateOrUpdateAddress(FrappeTestCase):
	"""Test _create_or_update_address address_title logic and deduplication."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Create a test customer with a numeric-style doc name
		if not frappe.db.exists("Customer", {"customer_name": "_Test Shopify Address Customer"}):
			cls.customer = frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": "_Test Shopify Address Customer",
					"customer_group": _ensure_leaf_customer_group(),
					"territory": frappe.db.get_single_value("Selling Settings", "territory"),
				}
			)
			cls.customer.insert(ignore_permissions=True)
		else:
			cls.customer = frappe.get_doc("Customer", {"customer_name": "_Test Shopify Address Customer"})
		cls.created_addresses = []

	@classmethod
	def tearDownClass(cls):
		for addr_name in cls.created_addresses:
			frappe.delete_doc("Address", addr_name, ignore_permissions=True, force=True)
		frappe.delete_doc("Customer", cls.customer.name, ignore_permissions=True, force=True)
		super().tearDownClass()

	# --- address_title priority tests ---

	def test_address_title_prefers_company_over_name(self):
		"""When Shopify address has both 'company' and 'name', company wins (B2B invoicing)."""
		address_data = {
			"company": "The Wellness Centre",
			"name": "Jeni Gorrie",
			"address1": "50 B2B Street",
			"city": "Auckland",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Billing")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "The Wellness Centre")

	def test_address_title_uses_name_when_no_company(self):
		"""When Shopify address has 'name' but no 'company', use name as address_title."""
		address_data = {
			"name": "Jane Doe",
			"address1": "10 Test Street",
			"city": "Auckland",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Billing")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "Jane Doe")

	def test_address_title_uses_name_when_company_is_empty(self):
		"""When Shopify address 'company' is empty string, fall through to 'name'."""
		address_data = {
			"company": "",
			"name": "John Smith",
			"address1": "55 Empty Company Lane",
			"city": "Hamilton",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Billing")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "John Smith")

	def test_address_title_uses_name_when_company_is_whitespace(self):
		"""When Shopify address 'company' is whitespace only, fall through to 'name'."""
		address_data = {
			"company": "   ",
			"name": "Sarah Connor",
			"address1": "60 Whitespace Blvd",
			"city": "Tauranga",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Billing")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "Sarah Connor")

	def test_address_title_falls_back_to_customer_display_name(self):
		"""When Shopify address has no 'name' and no 'company', use Customer display name."""
		address_data = {
			"address1": "20 Fallback Road",
			"city": "Wellington",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Shipping")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "_Test Shopify Address Customer")

	def test_address_title_falls_back_with_empty_name_and_no_company(self):
		"""When both 'name' and 'company' are empty, use Customer display name."""
		address_data = {
			"name": "",
			"company": "",
			"address1": "30 Empty Name Ave",
			"city": "Christchurch",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Billing")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "_Test Shopify Address Customer")

	def test_address_title_uses_first_and_last_when_no_name_or_company(self):
		"""When Shopify address has first_name/last_name but no 'name' or 'company',
		construct person name from first_name + last_name."""
		address_data = {
			"first_name": "Sarah",
			"last_name": "Connor",
			"address1": "70 Constructed Name Road",
			"city": "Queenstown",
			"country": "New Zealand",
		}
		addr_name = _create_or_update_address(address_data, self.customer.name, "Billing")
		self.created_addresses.append(addr_name)

		addr = frappe.get_doc("Address", addr_name)
		self.assertEqual(addr.address_title, "Sarah Connor")

	# --- deduplication tests ---

	def test_dedup_upgrades_title_to_company_on_existing_address(self):
		"""When an existing address has a person name as title and a subsequent order
		provides a company name, the existing address title should be upgraded."""
		# First order: no company, person name becomes title
		address_data_1 = {
			"name": "Jeni Gorrie",
			"address1": "105 Victoria Street",
			"city": "Dargaville",
			"country": "New Zealand",
		}
		addr_name_1 = _create_or_update_address(address_data_1, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_1)

		addr = frappe.get_doc("Address", addr_name_1)
		self.assertEqual(addr.address_title, "Jeni Gorrie")

		# Second order: same address, now with company name
		address_data_2 = {
			"company": "The Wellness Centre",
			"name": "Jeni Gorrie",
			"address1": "105 Victoria Street",
			"city": "Dargaville",
			"country": "New Zealand",
		}
		addr_name_2 = _create_or_update_address(address_data_2, self.customer.name, "Billing")
		# Should reuse the same address
		self.assertEqual(addr_name_1, addr_name_2)

		# Title should be upgraded to company name
		addr.reload()
		self.assertEqual(addr.address_title, "The Wellness Centre")

	def test_dedup_does_not_downgrade_company_title_to_person(self):
		"""When an existing address already has a company name as title, a subsequent
		order without a company should not downgrade it back to a person name."""
		# First order: with company
		address_data_1 = {
			"company": "Mount Pharmacy",
			"name": "Staff Member",
			"address1": "132 Maunganui Rd",
			"city": "Tauranga",
			"country": "New Zealand",
		}
		addr_name_1 = _create_or_update_address(address_data_1, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_1)

		# Second order: no company, different person
		address_data_2 = {
			"name": "Another Person",
			"address1": "132 Maunganui Rd",
			"city": "Tauranga",
			"country": "New Zealand",
		}
		addr_name_2 = _create_or_update_address(address_data_2, self.customer.name, "Billing")
		self.assertEqual(addr_name_1, addr_name_2)

		# Title should remain as company, not downgraded
		addr = frappe.get_doc("Address", addr_name_1)
		self.assertEqual(addr.address_title, "Mount Pharmacy")

	def test_dedup_finds_existing_address_regardless_of_title(self):
		"""Same physical address with different person names should not create a duplicate.

		Simulates two orders from the same company address placed by different people.
		The second call should return the existing address, not create a new one.
		"""
		# First order: person A from a company
		address_data_1 = {
			"company": "Acme Corp",
			"name": "Alice Anderson",
			"address1": "100 Dedup Drive",
			"city": "Dunedin",
			"country": "New Zealand",
		}
		addr_name_1 = _create_or_update_address(address_data_1, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_1)

		# Second order: person B from the same company address
		address_data_2 = {
			"company": "Acme Corp",
			"name": "Bob Brown",
			"address1": "100 Dedup Drive",
			"city": "Dunedin",
			"country": "New Zealand",
		}
		addr_name_2 = _create_or_update_address(address_data_2, self.customer.name, "Billing")
		# Should reuse the existing address, not create a new one
		self.assertEqual(addr_name_1, addr_name_2)

	def test_dedup_finds_existing_address_different_names_no_company(self):
		"""Same physical address with different person names (no company) should deduplicate.

		Simulates a household where different family members place orders.
		"""
		address_data_1 = {
			"name": "Parent Name",
			"address1": "200 Family Lane",
			"city": "Nelson",
			"country": "New Zealand",
		}
		addr_name_1 = _create_or_update_address(address_data_1, self.customer.name, "Shipping")
		self.created_addresses.append(addr_name_1)

		# Second order from same address, different person
		address_data_2 = {
			"name": "Child Name",
			"address1": "200 Family Lane",
			"city": "Nelson",
			"country": "New Zealand",
		}
		addr_name_2 = _create_or_update_address(address_data_2, self.customer.name, "Shipping")
		self.assertEqual(addr_name_1, addr_name_2)

	def test_dedup_distinguishes_different_physical_addresses(self):
		"""Different physical addresses for the same customer should create separate records."""
		address_data_1 = {
			"name": "Same Person",
			"address1": "300 First Avenue",
			"city": "Napier",
			"country": "New Zealand",
		}
		addr_name_1 = _create_or_update_address(address_data_1, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_1)

		address_data_2 = {
			"name": "Same Person",
			"address1": "400 Second Boulevard",
			"city": "Napier",
			"country": "New Zealand",
		}
		addr_name_2 = _create_or_update_address(address_data_2, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_2)

		self.assertNotEqual(addr_name_1, addr_name_2)

	def test_dedup_same_address1_different_unit_creates_separate_records(self):
		"""Same street address but different units (address2) should create separate records."""
		address_data_1 = {
			"name": "Same Person",
			"address1": "100 Queen Street",
			"address2": "Unit 1",
			"city": "Auckland",
			"country": "New Zealand",
		}
		addr_name_1 = _create_or_update_address(address_data_1, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_1)

		address_data_2 = {
			"name": "Same Person",
			"address1": "100 Queen Street",
			"address2": "Unit 2",
			"city": "Auckland",
			"country": "New Zealand",
		}
		addr_name_2 = _create_or_update_address(address_data_2, self.customer.name, "Billing")
		self.created_addresses.append(addr_name_2)

		self.assertNotEqual(addr_name_1, addr_name_2)

# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Tests for the multi-gateway Payment Entry creation path.

Covers three layers:
  - Parser (`_get_payment_amounts_by_gateway`): no I/O, pure dict transforms.
  - Helper (`_fetch_order_transactions`): wraps Shopify SDK calls with auth
	context and translates SDK exceptions into `ShopifyTransactionFetchError`.
  - Orchestrator (`_create_payment_entries`): decides whether to fetch
	transactions, handles fetch failures, and creates Payment Entries.

External I/O is patched via `unittest.mock.patch`, matching the style used in
`test_inventory.py`. The orchestrator tests rely on a real Sales Invoice and
real Shopify Store records created via `_payment_test_fixtures`.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
	from frappe.model.document import Document

from nexwave_shopify_connector.nexwave_shopify._payment_test_fixtures import (
	TEST_GATEWAY_CARD,
	TEST_GATEWAY_GIFT_CARD,
	TEST_GATEWAY_STORE_CREDIT,
	TEST_MODE_OF_PAYMENT_CARD,
	TEST_MODE_OF_PAYMENT_GIFT_CARD,
	TEST_MODE_OF_PAYMENT_STORE_CREDIT,
	create_test_sales_invoice_for_payment,
	ensure_test_shopify_store_with_payment_mapping,
	load_shopify_order,
)
from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.order import (
	ShopifyTransactionFetchError,
	_create_payment_entries,
	_fetch_order_transactions,
	_get_payment_amounts_by_gateway,
)


@contextmanager
def _noop_session(*args, **kwargs):
	"""Replacement for Session.temp used in patches (no-op context manager)."""
	yield


def _make_txn(gateway: str, amount: str, status: str = "success", kind: str = "sale", **extra) -> dict:
	return {
		"gateway": gateway,
		"amount": amount,
		"status": status,
		"kind": kind,
		**extra,
	}


class TestPaymentAmountsParser(FrappeTestCase):
	"""Direct unit tests for `_get_payment_amounts_by_gateway` (no I/O)."""

	def test_parses_inline_transactions(self):
		"""Order with embedded successful transactions returns gateway amounts."""
		order = {
			"id": 1,
			"financial_status": "paid",
			"payment_gateway_names": [TEST_GATEWAY_STORE_CREDIT, TEST_GATEWAY_CARD],
			"transactions": [
				_make_txn(TEST_GATEWAY_STORE_CREDIT, "100.00"),
				_make_txn(TEST_GATEWAY_CARD, "214.78"),
			],
		}
		amounts = _get_payment_amounts_by_gateway(order)
		self.assertEqual(set(amounts.keys()), {TEST_GATEWAY_STORE_CREDIT, TEST_GATEWAY_CARD})
		self.assertAlmostEqual(amounts[TEST_GATEWAY_STORE_CREDIT], 100.00)
		self.assertAlmostEqual(amounts[TEST_GATEWAY_CARD], 214.78)

	def test_filters_pending_and_void_transactions(self):
		"""Only successful sale/capture survives; pending/void/refund are dropped."""
		order = {
			"id": 2,
			"financial_status": "paid",
			"payment_gateway_names": [TEST_GATEWAY_CARD],
			"transactions": [
				_make_txn(TEST_GATEWAY_CARD, "50.00", status="pending"),
				_make_txn(TEST_GATEWAY_CARD, "75.00", status="success", kind="authorization"),
				_make_txn(TEST_GATEWAY_CARD, "200.00", status="success", kind="sale"),
				_make_txn(TEST_GATEWAY_CARD, "200.00", status="success", kind="refund"),
				_make_txn(TEST_GATEWAY_CARD, "60.00", status="success", kind="capture"),
				_make_txn(TEST_GATEWAY_CARD, "10.00", status="failure", kind="sale"),
			],
		}
		amounts = _get_payment_amounts_by_gateway(order)
		# 200 (sale) + 60 (capture) = 260
		self.assertEqual(list(amounts.keys()), [TEST_GATEWAY_CARD])
		self.assertAlmostEqual(amounts[TEST_GATEWAY_CARD], 260.00)

	def test_single_gateway_fallback(self):
		"""No transactions, single gateway, paid: fall back to total_price."""
		order = {
			"id": 3,
			"financial_status": "paid",
			"payment_gateway_names": [TEST_GATEWAY_CARD],
			"total_price": "150.00",
		}
		amounts = _get_payment_amounts_by_gateway(order)
		self.assertEqual(amounts, {TEST_GATEWAY_CARD: 150.00})

	def test_multi_gateway_no_transactions_returns_empty(self):
		"""Parser stays I/O-free: multi-gateway-no-transactions returns empty.

		The orchestrator (`_create_payment_entries`) is responsible for
		fetching transactions from Shopify in this case.
		"""
		order = {
			"id": 4,
			"financial_status": "paid",
			"payment_gateway_names": [TEST_GATEWAY_STORE_CREDIT, TEST_GATEWAY_CARD],
			"total_price": "314.78",
		}
		amounts = _get_payment_amounts_by_gateway(order)
		self.assertEqual(amounts, {})

	def test_no_gateway_no_transactions_returns_empty(self):
		"""Empty in, empty out."""
		order = {
			"id": 5,
			"financial_status": "paid",
			"payment_gateway_names": [],
		}
		self.assertEqual(_get_payment_amounts_by_gateway(order), {})


class TestFetchOrderTransactions(FrappeTestCase):
	"""Tests for `_fetch_order_transactions` (Shopify SDK helper)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.store = ensure_test_shopify_store_with_payment_mapping()
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture persistence

	@classmethod
	def tearDownClass(cls):
		if frappe.db.exists("Shopify Store", cls.store.name):
			frappe.delete_doc("Shopify Store", cls.store.name, force=True, ignore_missing=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture cleanup
		super().tearDownClass()

	@patch("nexwave_shopify_connector.nexwave_shopify.order.Session")
	def test_fetch_uses_store_auth_context(self, mock_session):
		"""Helper passes (shop_domain, api_version, access_token) to Session.temp."""
		captured = {}

		@contextmanager
		def capturing_temp(*args, **kwargs):
			captured["args"] = args
			captured["kwargs"] = kwargs
			yield

		mock_session.temp = capturing_temp

		with patch("shopify.resources.Transaction") as mock_txn_cls:
			mock_txn_cls.find.return_value = []
			_fetch_order_transactions(123, self.store)

		self.assertEqual(len(captured["args"]), 3)
		shop_domain, api_version, token = captured["args"]
		self.assertEqual(shop_domain, self.store.shop_domain)
		self.assertEqual(api_version, self.store.api_version)
		self.assertEqual(token, "test-token")

	@patch("nexwave_shopify_connector.nexwave_shopify.order.Session")
	def test_fetch_uses_default_api_version_when_unset(self, mock_session):
		"""When store.api_version is empty, helper falls back to DEFAULT_API_VERSION."""
		captured = {}

		@contextmanager
		def capturing_temp(*args, **kwargs):
			captured["args"] = args
			yield

		mock_session.temp = capturing_temp

		# Save and clear api_version on the store, then restore after
		original_api_version = self.store.api_version
		frappe.db.set_value("Shopify Store", self.store.name, "api_version", "")
		try:
			fresh_store = frappe.get_doc("Shopify Store", self.store.name)
			with patch("shopify.resources.Transaction") as mock_txn_cls:
				mock_txn_cls.find.return_value = []
				_fetch_order_transactions(123, fresh_store)
		finally:
			frappe.db.set_value("Shopify Store", self.store.name, "api_version", original_api_version)

		self.assertEqual(captured["args"][1], DEFAULT_API_VERSION)

	@patch("nexwave_shopify_connector.nexwave_shopify.order.Session")
	def test_fetch_wraps_connection_error(self, mock_session):
		"""SDK ConnectionError gets wrapped in ShopifyTransactionFetchError."""
		mock_session.temp = _noop_session

		with patch("shopify.resources.Transaction") as mock_txn_cls:
			mock_txn_cls.find.side_effect = ConnectionError("boom: network unreachable")
			with self.assertRaises(ShopifyTransactionFetchError) as ctx:
				_fetch_order_transactions(123, self.store)
		self.assertIsInstance(ctx.exception.__cause__, ConnectionError)
		self.assertIn("123", str(ctx.exception))

	@patch("nexwave_shopify_connector.nexwave_shopify.order.Session")
	def test_fetch_wraps_resource_not_found(self, mock_session):
		"""Generic SDK exceptions (e.g. ResourceNotFound) wrap the same way."""
		mock_session.temp = _noop_session

		class _ResourceNotFound(Exception):
			pass

		with patch("shopify.resources.Transaction") as mock_txn_cls:
			mock_txn_cls.find.side_effect = _ResourceNotFound("not found: order 999")
			with self.assertRaises(ShopifyTransactionFetchError) as ctx:
				_fetch_order_transactions(999, self.store)
		self.assertIsInstance(ctx.exception.__cause__, _ResourceNotFound)

	@patch("nexwave_shopify_connector.nexwave_shopify.order.Session")
	def test_fetch_returns_empty_list_when_api_returns_none(self, mock_session):
		"""When SDK returns None (no transactions), helper returns []."""
		mock_session.temp = _noop_session

		with patch("shopify.resources.Transaction") as mock_txn_cls:
			mock_txn_cls.find.return_value = None
			result = _fetch_order_transactions(123, self.store)
		self.assertEqual(result, [])

	def test_fetch_wraps_get_password_failure(self):
		"""`store.get_password` raising is wrapped as ShopifyTransactionFetchError.

		The auth tuple is built inside the try/except, so a missing or
		inaccessible encrypted password must not escape as a raw exception.
		"""

		class _StoreStub:
			name = self.store.name
			shop_domain = self.store.shop_domain
			api_version = self.store.api_version

			def get_password(self, _fieldname):
				raise RuntimeError("no encrypted password set")

		with self.assertRaises(ShopifyTransactionFetchError) as ctx:
			_fetch_order_transactions(123, _StoreStub())
		self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
		self.assertIn("123", str(ctx.exception))


class TestCreatePaymentEntriesOrchestrator(FrappeTestCase):
	"""Tests for `_create_payment_entries` (orchestration + fetch logic)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.store = ensure_test_shopify_store_with_payment_mapping()
		cls.created_si_names: list[str] = []
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture persistence

	@classmethod
	def tearDownClass(cls):
		# Cancel and delete every SI/PE we created. PEs reference SIs, so
		# cancel and delete PEs first, then cancel SIs, then delete SIs.
		for si_name in cls.created_si_names:
			pe_names = frappe.get_all(
				"Payment Entry",
				filters={"reference_no": si_name},
				pluck="name",
			)
			for pe_name in pe_names:
				try:
					pe = frappe.get_doc("Payment Entry", pe_name)
					if pe.docstatus == 1:
						pe.cancel()
				except frappe.DoesNotExistError:
					pass
				frappe.delete_doc("Payment Entry", pe_name, force=True, ignore_missing=True)

			try:
				si = frappe.get_doc("Sales Invoice", si_name)
				if si.docstatus == 1:
					si.cancel()
			except frappe.DoesNotExistError:
				pass
			frappe.delete_doc("Sales Invoice", si_name, force=True, ignore_missing=True)

		# Clean up any error logs we generated
		frappe.db.delete("NexWave Shopify Log", {"shopify_store": cls.store.name})

		if frappe.db.exists("Shopify Store", cls.store.name):
			frappe.delete_doc("Shopify Store", cls.store.name, force=True, ignore_missing=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test fixture cleanup
		super().tearDownClass()

	def _create_si(self, grand_total: float, shopify_order_id: str) -> "Document":
		si = create_test_sales_invoice_for_payment(
			self.store,
			grand_total=grand_total,
			shopify_order_id=shopify_order_id,
		)
		self.__class__.created_si_names.append(si.name)
		return si

	def _payment_entries_for_si(self, si_name) -> list[dict]:
		return frappe.get_all(
			"Payment Entry",
			filters={"reference_no": si_name, "docstatus": 1},
			fields=["name", "paid_amount", "mode_of_payment"],
		)

	# ------------------------------------------------------------------
	# Multi-gateway behaviour
	# ------------------------------------------------------------------

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetches_transactions_and_creates_pes(self, mock_fetch):
		"""Multi-gateway paid order with no transactions: fetch and create both PEs."""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		si = self._create_si(grand_total=314.78, shopify_order_id=str(order["id"]))

		mock_fetch.return_value = [
			_make_txn(TEST_GATEWAY_STORE_CREDIT, "100.00"),
			_make_txn(TEST_GATEWAY_CARD, "214.78"),
		]

		_create_payment_entries(si, order, self.store)

		mock_fetch.assert_called_once()
		# First positional arg should be the order id.
		self.assertEqual(mock_fetch.call_args[0][0], order["id"])

		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(len(pes), 2, f"Expected 2 PEs, got: {pes}")
		amounts_by_mop = {pe["mode_of_payment"]: pe["paid_amount"] for pe in pes}
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_STORE_CREDIT], 100.00)
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_CARD], 214.78)

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetch_failure_skips_pe_creation(self, mock_fetch):
		"""Fetch raising ShopifyTransactionFetchError: no PE, no exception, error log written."""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		si = self._create_si(grand_total=314.78, shopify_order_id=str(order["id"]))

		mock_fetch.side_effect = ShopifyTransactionFetchError("boom")

		# Must not raise
		_create_payment_entries(si, order, self.store)

		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(pes, [])

		error_logs = frappe.get_all(
			"NexWave Shopify Log",
			filters={
				"shopify_store": self.store.name,
				"status": "Error",
				"reference_doctype": "Sales Invoice",
				"reference_name": si.name,
			},
			pluck="name",
		)
		self.assertTrue(error_logs, "An Error log row should reference the SI on fetch failure")

	@patch("nexwave_shopify_connector.nexwave_shopify.order.create_shopify_log")
	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetch_failure_logging_failure_is_swallowed(self, mock_fetch, mock_create_log):
		"""If create_shopify_log itself raises, _create_payment_entries still returns cleanly.

		The webhook handler relies on this helper never propagating fetch-failure
		noise so the order.created/order.paid webhook is not retried.
		"""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		si = self._create_si(grand_total=314.78, shopify_order_id=str(order["id"]))

		mock_fetch.side_effect = ShopifyTransactionFetchError("boom")
		mock_create_log.side_effect = Exception("simulated db error")

		# Must not raise even though both fetch and logging fail
		_create_payment_entries(si, order, self.store)

		mock_create_log.assert_called_once()
		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(pes, [])

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetch_returns_empty_skips_pe_creation(self, mock_fetch):
		"""Fetch returns []: no PE, no exception, no Error log."""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		si = self._create_si(grand_total=314.78, shopify_order_id=str(order["id"]))

		mock_fetch.return_value = []

		_create_payment_entries(si, order, self.store)

		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(pes, [])

		error_logs = frappe.get_all(
			"NexWave Shopify Log",
			filters={
				"shopify_store": self.store.name,
				"status": "Error",
				"reference_doctype": "Sales Invoice",
				"reference_name": si.name,
			},
			pluck="name",
		)
		self.assertEqual(error_logs, [], "Empty fetch is not an error condition")

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetch_returns_unmapped_gateway_throws(self, mock_fetch):
		"""Unmapped gateway in fetched transactions raises a ValidationError."""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		si = self._create_si(grand_total=314.78, shopify_order_id=str(order["id"]))

		mock_fetch.return_value = [_make_txn("paypal", "314.78")]

		with self.assertRaises(frappe.ValidationError):
			_create_payment_entries(si, order, self.store)

	# ------------------------------------------------------------------
	# Single-gateway and inline-transactions paths must NOT call fetch
	# ------------------------------------------------------------------

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_single_gateway_no_transactions_does_not_call_api(self, mock_fetch):
		"""Single-gateway-no-transactions uses total_price fallback; no API call."""
		order = load_shopify_order("order_single_gateway_no_transactions.json")
		si = self._create_si(grand_total=314.78, shopify_order_id=str(order["id"]))

		_create_payment_entries(si, order, self.store)

		mock_fetch.assert_not_called()
		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(len(pes), 1)
		self.assertEqual(pes[0]["mode_of_payment"], TEST_MODE_OF_PAYMENT_CARD)

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_inline_transactions_does_not_call_api(self, mock_fetch):
		"""Inline transactions present: parser handles it; no API call."""
		order = load_shopify_order("order_multi_gateway_with_transactions.json")
		# Use a unique shopify_order_id so we don't clash with the
		# no-transactions fixture (same order id) used by other tests.
		shopify_order_id = f"{order['id']}_inline"
		si = self._create_si(grand_total=314.78, shopify_order_id=shopify_order_id)

		_create_payment_entries(si, order, self.store)

		mock_fetch.assert_not_called()
		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(len(pes), 2)
		amounts_by_mop = {pe["mode_of_payment"]: pe["paid_amount"] for pe in pes}
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_STORE_CREDIT], 100.00)
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_CARD], 214.78)

	# ------------------------------------------------------------------
	# Edge cases: rounding, three gateways, non-paid statuses
	# ------------------------------------------------------------------

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetch_last_gateway_absorbs_rounding_remainder(self, mock_fetch):
		"""When fetched transaction amounts don't sum exactly to grand_total,
		the last gateway is paid the remaining amount (not its own raw amount).

		This covers the rounding-protection branch in `_create_payment_entries`
		so that PEs always reconcile to the SI grand_total, even if Shopify's
		transaction amounts and our SI's grand_total disagree by a few cents
		(common with cross-currency payments or rounding of partials).
		"""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		# Use a unique order id so this test's SI doesn't collide with the
		# happy-path test that reuses the same fixture.
		shopify_order_id = f"{order['id']}_rounding"
		si = self._create_si(grand_total=314.78, shopify_order_id=shopify_order_id)

		# Transactions sum to 314.83 but SI grand_total is 314.78
		# -> first gateway gets its raw amount (100.05), last gets remainder (214.73).
		mock_fetch.return_value = [
			_make_txn(TEST_GATEWAY_STORE_CREDIT, "100.05"),
			_make_txn(TEST_GATEWAY_CARD, "214.78"),
		]

		_create_payment_entries(si, order, self.store)

		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(len(pes), 2)
		amounts_by_mop = {pe["mode_of_payment"]: pe["paid_amount"] for pe in pes}
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_STORE_CREDIT], 100.05, places=2)
		# Last gateway absorbs the 0.05 over-payment so PE total = SI grand_total.
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_CARD], 214.73, places=2)
		total_paid = sum(amounts_by_mop.values())
		self.assertAlmostEqual(total_paid, 314.78, places=2)

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_fetch_three_gateways_creates_three_pes(self, mock_fetch):
		"""Fetch path supports 3+ gateways. Last gateway still gets remainder.

		Covers the case where a customer pays with three distinct methods
		(e.g. store credit + gift card + card) and the fetch path is exercised
		because the webhook payload omitted transactions.
		"""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		# Override payment_gateway_names on the order so multi-gateway gating
		# (len > 1) still passes; the actual count doesn't matter, just > 1.
		order = {
			**order,
			"payment_gateway_names": [
				TEST_GATEWAY_STORE_CREDIT,
				TEST_GATEWAY_GIFT_CARD,
				TEST_GATEWAY_CARD,
			],
		}
		shopify_order_id = f"{order['id']}_three"
		si = self._create_si(grand_total=300.00, shopify_order_id=shopify_order_id)

		mock_fetch.return_value = [
			_make_txn(TEST_GATEWAY_STORE_CREDIT, "50.00"),
			_make_txn(TEST_GATEWAY_GIFT_CARD, "100.00"),
			_make_txn(TEST_GATEWAY_CARD, "150.00"),
		]

		_create_payment_entries(si, order, self.store)

		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(len(pes), 3, f"Expected 3 PEs, got: {pes}")
		amounts_by_mop = {pe["mode_of_payment"]: pe["paid_amount"] for pe in pes}
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_STORE_CREDIT], 50.00, places=2)
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_GIFT_CARD], 100.00, places=2)
		self.assertAlmostEqual(amounts_by_mop[TEST_MODE_OF_PAYMENT_CARD], 150.00, places=2)

	@patch("nexwave_shopify_connector.nexwave_shopify.order._fetch_order_transactions")
	def test_multi_gateway_partially_paid_does_not_call_api(self, mock_fetch):
		"""Multi-gateway order with `financial_status != "paid"` skips the fetch.

		Documents the deliberate gating in `_create_payment_entries`: the fetch
		path only runs for fully-paid orders. Pending/partially_paid/authorized
		orders are skipped silently (no PE, no API call). When Shopify later
		fires `orders/paid`, that webhook will re-trigger PE creation.
		"""
		order = load_shopify_order("order_multi_gateway_no_transactions.json")
		order = {**order, "financial_status": "partially_paid"}
		shopify_order_id = f"{order['id']}_partial"
		si = self._create_si(grand_total=314.78, shopify_order_id=shopify_order_id)

		_create_payment_entries(si, order, self.store)

		mock_fetch.assert_not_called()
		pes = self._payment_entries_for_si(si.name)
		self.assertEqual(pes, [])

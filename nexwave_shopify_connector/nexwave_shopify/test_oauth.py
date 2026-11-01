# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from nexwave_shopify_connector.nexwave_shopify import oauth
from nexwave_shopify_connector.nexwave_shopify._store_test_fixtures import (
	TEST_CLIENT_SECRET,
	TEST_COMPANY,
	delete_oauth_store,
	make_oauth_store,
)
from nexwave_shopify_connector.nexwave_shopify.connection import get_access_token


class TestShopifyOAuth(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.original_user = frappe.session.user
		self.original_form = frappe.local.form_dict
		self.original_response = frappe.local.response
		frappe.set_user("Administrator")
		self.store = make_oauth_store()
		self.state = oauth.generate_state_token(self.store.name)
		frappe.local.form_dict = frappe._dict(
			code="synthetic-code", state=self.state, shop=self.store.shop_domain
		)
		frappe.local.response = frappe._dict()

	def tearDown(self):
		try:
			delete_oauth_store()
		finally:
			frappe.cache().delete_value(f"shopify_oauth_state:{self.state}")
			frappe.local.form_dict = self.original_form
			frappe.local.response = self.original_response
			frappe.set_user(self.original_user)
			super().tearDown()

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_callback_encrypts_token_and_preserves_store_settings(self, post):
		post.return_value.status_code = 200
		post.return_value.json.return_value = {"access_token": "synthetic-token-one"}

		self.complete_callback()

		self.assert_stored_token("synthetic-token-one")
		self.assertEqual(self.store.get_password("client_secret"), TEST_CLIENT_SECRET)
		self.assertEqual(self.store.oauth_status, "Connected")
		self.assertEqual(self.store.connected_user, "Administrator")
		self.assertEqual(self.store.auth_method, "OAuth")
		self.assertEqual(self.store.company, TEST_COMPANY)
		self.assertFalse(self.store.enabled)
		self.assertFalse(self.store.sync_orders)
		self.assertFalse(self.store.enable_inventory_sync)
		self.assertEqual(frappe.local.response.type, "redirect")
		self.assertEqual(frappe.local.response.location, f"/app/shopify-store/{self.store.name}")

		self.store.inventory_sync_frequency += 1
		self.store.save(ignore_version=False)
		self.assert_no_token_history("synthetic-token-one", TEST_CLIENT_SECRET)

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_reconnection_replaces_encrypted_token_without_exposing_history(self, post):
		post.return_value.status_code = 200
		for token in ("synthetic-token-one", "synthetic-token-two"):
			frappe.local.form_dict.state = oauth.generate_state_token(self.store.name)
			post.return_value.json.return_value = {"access_token": token}
			self.complete_callback()
			self.assert_stored_token(token)

		self.assert_no_token_history("synthetic-token-one", "synthetic-token-two")

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_reconnect_preserves_inventory_update_during_token_exchange(self, post):
		original_isolation = frappe.db.sql("SELECT @@tx_isolation")[0][0]
		# Avoid the framework's repeatable-read current-row error on newer database versions.
		frappe.db.sql("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
		self.addCleanup(self.restore_transaction_isolation, original_isolation)
		self.store.access_token = "synthetic-existing-token"
		self.store.enabled = 1
		self.store.sync_orders = 1
		self.store.enable_inventory_sync = 1
		self.store.save(ignore_version=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- expose synthetic store to the concurrent sync connection
		sync_time = now_datetime()
		post.return_value.status_code = 200
		post.return_value.json.return_value = {"access_token": "synthetic-reconnected-token"}

		def complete_inventory_sync(*args, **kwargs):
			connection = frappe.db.get_connection()
			try:
				with connection.cursor() as cursor:
					cursor.execute(
						"""UPDATE `tabShopify Store`
						SET last_inventory_sync = %s, modified = %s WHERE name = %s""",
						(sync_time, sync_time, self.store.name),
					)
				connection.commit()
			finally:
				connection.close()
			return post.return_value

		post.side_effect = complete_inventory_sync

		self.complete_callback()

		self.assert_stored_token("synthetic-reconnected-token")
		self.assertEqual(self.store.get_password("client_secret"), TEST_CLIENT_SECRET)
		self.assertEqual(self.store.oauth_status, "Connected")
		self.assertEqual(self.store.last_inventory_sync, sync_time)
		self.assertTrue(self.store.enabled)
		self.assertTrue(self.store.sync_orders)
		self.assertTrue(self.store.enable_inventory_sync)
		self.assert_no_token_history("synthetic-existing-token", "synthetic-reconnected-token")

	def restore_transaction_isolation(self, isolation):
		frappe.db.rollback()
		commands = {
			"READ-UNCOMMITTED": "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED",
			"READ-COMMITTED": "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
			"REPEATABLE-READ": "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
			"SERIALIZABLE": "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE",
		}
		frappe.db.sql(commands[isolation])

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_deleted_store_cannot_receive_an_orphan_token(self, post):
		post.return_value.status_code = 200
		post.return_value.json.return_value = {"access_token": "synthetic-token-one"}

		def delete_store_during_exchange(*args, **kwargs):
			frappe.delete_doc("Shopify Store", self.store.name, force=True, ignore_permissions=True)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- reproduce a committed deletion during token exchange
			return post.return_value

		post.side_effect = delete_store_during_exchange

		with self.assertRaises(frappe.DoesNotExistError):
			self.complete_callback()

		self.assertFalse(
			frappe.db.sql(
				"SELECT 1 FROM `__Auth` WHERE doctype=%s AND name=%s",
				(self.store.doctype, self.store.name),
			)
		)

	def test_existing_tokens_remain_readable_in_both_authentication_methods(self):
		for method in ("Legacy (Access Token)", "OAuth"):
			for storage in ("plaintext", "encrypted"):
				with self.subTest(method=method, storage=storage):
					self.store.auth_method = method
					self.store.client_secret = TEST_CLIENT_SECRET
					self.store.access_token = "synthetic-existing-token"
					self.store.save(ignore_version=True)
					if storage == "plaintext":
						self.store.db_set("access_token", "synthetic-existing-token")
						frappe.db.delete(
							"__Auth",
							{
								"doctype": self.store.doctype,
								"name": self.store.name,
								"fieldname": "access_token",
							},
						)
					self.store.reload()
					stored_value = self.store.access_token

					self.assertEqual(get_access_token(self.store), "synthetic-existing-token")
					self.assertEqual(self.store._get_auth_details()[2], "synthetic-existing-token")
					self.store.reload()
					self.assertEqual(self.store.access_token, stored_value)

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_legacy_plaintext_token_does_not_enter_change_history(self, post):
		self.store.db_set("access_token", "synthetic-legacy-token")
		self.store.db_set("oauth_status", "Connected")
		post.return_value.status_code = 200
		post.return_value.json.return_value = {"access_token": "synthetic-token-two"}

		self.complete_callback()

		self.assert_stored_token("synthetic-token-two")
		self.assert_no_token_history("synthetic-legacy-token", "synthetic-token-two")

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_replayed_state_cannot_exchange_another_token(self, post):
		post.return_value.status_code = 200
		post.return_value.json.return_value = {"access_token": "synthetic-token-one"}
		self.complete_callback()

		with self.assertRaisesRegex(frappe.ValidationError, "Invalid or expired state token"):
			self.complete_callback()

		post.assert_called_once()
		self.assert_stored_token("synthetic-token-one")

	@patch("nexwave_shopify_connector.nexwave_shopify.oauth.requests.post")
	def test_rejected_exchange_keeps_store_disconnected(self, post):
		post.return_value.status_code = 400
		post.return_value.text = "Rejected"

		with self.assertRaisesRegex(frappe.ValidationError, "Token exchange failed"):
			self.complete_callback()

		self.store.reload()
		self.assertEqual(self.store.oauth_status, "Not Connected")
		self.assertFalse(self.store.get_password("access_token", raise_exception=False))

	def test_fixture_cleanup_removes_company_and_owned_records(self):
		self.assertTrue(frappe.db.exists("Company", TEST_COMPANY))
		self.assertTrue(frappe.db.count("Account", {"company": TEST_COMPANY}))

		delete_oauth_store()

		self.assertFalse(frappe.db.exists("Company", TEST_COMPANY))
		for doctype in ("Account", "Warehouse", "Cost Center", "Department"):
			with self.subTest(doctype=doctype):
				self.assertEqual(frappe.db.count(doctype, {"company": TEST_COMPANY}), 0)

	def complete_callback(self):
		in_test = frappe.flags.in_test
		frappe.flags.in_test = False
		try:
			oauth.callback()
		finally:
			frappe.flags.in_test = in_test

	def assert_stored_token(self, expected):
		self.store.reload()
		self.assertTrue(self.store.is_dummy_password(self.store.access_token))
		self.assertEqual(self.store.get_password("access_token"), expected)
		self.assertTrue(
			frappe.db.sql(
				"""select 1 from `__Auth`
				where doctype=%s and name=%s and fieldname='access_token' and encrypted=1""",
				(self.store.doctype, self.store.name),
			)
		)

	def assert_no_token_history(self, *secrets):
		versions = frappe.get_all(
			"Version", filters={"ref_doctype": self.store.doctype, "docname": self.store.name}, pluck="data"
		)
		for secret in secrets:
			self.assertNotIn(secret, json.dumps(versions))

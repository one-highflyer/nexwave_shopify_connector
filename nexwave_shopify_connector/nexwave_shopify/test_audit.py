# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from nexwave_shopify_connector.nexwave_shopify._store_test_fixtures import (
	TEST_COMPANY,
	delete_oauth_store,
	make_oauth_store,
)


class TestShopifyTokenAudit(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.original_user = frappe.session.user
		frappe.set_user("Administrator")
		self.store = make_oauth_store()
		self.versions = []

	def tearDown(self):
		try:
			for name in self.versions:
				frappe.delete_doc("Version", name, force=True, ignore_permissions=True, ignore_missing=True)
			delete_oauth_store()
		finally:
			frappe.set_user(self.original_user)
			super().tearDown()

	def insert_version(self, data, doctype="Shopify Store", name=None):
		version = frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": doctype,
				"docname": name or self.store.name,
				"data": data if isinstance(data, str) else json.dumps(data),
			}
		).insert(ignore_permissions=True)
		self.versions.append(version.name)
		return version

	def test_normal_save_masks_legacy_token_history_without_changing_credentials(self):
		token = "abcd01234567890123456789wxyz"
		self.store.db_set("access_token", token)
		self.store.inventory_sync_frequency += 1

		self.store.save(ignore_version=False)

		self.store.reload()
		self.assertEqual(self.store.get_password("access_token"), token)
		self.assertTrue(self.store.is_dummy_password(self.store.access_token))
		versions = frappe.get_all(
			"Version",
			filters={"ref_doctype": self.store.doctype, "docname": self.store.name},
			fields=["name", "data"],
		)
		self.assertEqual(len(versions), 1)
		self.versions.append(versions[0].name)
		changes = json.loads(versions[0].data)["changed"]
		token_change = next(change for change in changes if change[0] == "access_token")
		self.assertEqual(token_change[1], "abcd********wxyz")
		self.assertTrue(self.store.is_dummy_password(token_change[2]))
		self.assertNotIn(token, versions[0].data)
		self.assertTrue(any(change[0] == "inventory_sync_frequency" for change in changes))

	def test_new_history_masks_only_token_values(self):
		old = "abcd01234567890123456789wxyz"
		new = "efgh98765432109876543210qrst"
		data = {
			"changed": [["access_token", old, new], ["inventory_sync_frequency", 10, 20]],
			"added": [],
			"data_import": "synthetic-import",
		}

		version = self.insert_version(data)

		version.reload()
		stored = json.loads(version.data)
		self.assertEqual(stored["changed"][0], ["access_token", "abcd********wxyz", "efgh********qrst"])
		self.assertEqual(stored["changed"][1], data["changed"][1])
		self.assertEqual(stored["added"], [])
		self.assertEqual(stored["data_import"], "synthetic-import")
		self.assertNotIn(old, version.data)
		self.assertNotIn(new, version.data)

	def test_short_empty_and_masked_values_remain_safe(self):
		for value, expected in (
			("short", "********"),
			("12345678", "********"),
			("****", "****"),
			("********", "********"),
			("********************", "********************"),
			("abcd********wxyz", "abcd********wxyz"),
			("", ""),
			(None, None),
		):
			with self.subTest(value=value):
				version = self.insert_version({"changed": [["access_token", value, value]]})
				version.reload()
				self.assertEqual(json.loads(version.data)["changed"][0], ["access_token", expected, expected])

	def test_other_document_history_is_unchanged(self):
		data = {"changed": [["access_token", "synthetic-original-value", "synthetic-replacement-value"]]}

		version = self.insert_version(data, doctype="Company", name=TEST_COMPANY)

		version.reload()
		self.assertEqual(json.loads(version.data), data)

	def test_existing_history_is_not_rewritten(self):
		original = json.dumps({"changed": [["access_token", "synthetic-original-value", "********"]]})
		existing = self.insert_version({"changed": [["inventory_sync_frequency", 10, 20]]})
		frappe.db.set_value("Version", existing.name, "data", original)

		self.insert_version({"changed": [["access_token", "synthetic-replacement-value", "********"]]})

		existing.reload()
		self.assertEqual(existing.data, original)

	def test_malformed_store_history_is_rejected_without_saving_token_data(self):
		for data in ("not-json", {"changed": "synthetic-token"}, {"changed": [["access_token"]]}):
			with self.subTest(data=data):
				with self.assertRaisesRegex(frappe.ValidationError, "Invalid Shopify Store audit data"):
					self.insert_version(data)

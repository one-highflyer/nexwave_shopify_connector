# Copyright (c) 2025, HighFlyer and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from shopify.api_version import ApiVersion, Release
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION


class TestShopifyStore(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.original_versions = ApiVersion.versions
		self.addCleanup(setattr, ApiVersion, "versions", self.original_versions)

	def test_selected_version_works_with_an_older_sdk_registry(self):
		for selected in ("2024-01", "2026-01", "unstable"):
			with self.subTest(selected=selected):
				ApiVersion.versions = {"2024-01": Release("2024-01")}
				self.assert_session_version(selected)

	def test_selected_and_default_versions_work_with_an_empty_registry(self):
		for selected in (None, "2026-01", "unstable"):
			with self.subTest(selected=selected):
				ApiVersion.versions = {}
				self.assert_session_version(selected)

	def assert_session_version(self, selected):
		store = frappe.get_doc({"doctype": "Shopify Store", "api_version": selected})
		store._init_shopify_api_versions()
		session = Session("example.myshopify.com", selected or DEFAULT_API_VERSION, "synthetic-token")
		self.assertEqual(session.api_version.name, selected or DEFAULT_API_VERSION)

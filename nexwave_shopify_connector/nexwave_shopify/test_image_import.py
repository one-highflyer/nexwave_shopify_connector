# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import io
from unittest.mock import patch
from uuid import uuid4

import frappe
import requests
from frappe.tests.utils import FrappeTestCase, change_settings
from frappe.utils import add_to_date, now_datetime
from PIL import Image

from nexwave_shopify_connector.nexwave_shopify import image_import as importer
from nexwave_shopify_connector.nexwave_shopify._image_import_test_fixtures import (
	add_mapping,
	image_bytes,
	make_item,
	make_store,
)

test_dependencies = ["Company", "Item"]


class FakeResponse:
	def __init__(self, payload=None, content=None, status=200):
		self.payload = payload
		self.content = content or image_bytes()
		self.status_code = status

	def json(self):
		return self.payload

	def iter_content(self, chunk_size):
		yield self.content

	def close(self):
		pass


class TestImageImport(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.prefix = f"_Test Image {uuid4().hex[:8]}"
		self.stores = [make_store(f"test-images-{uuid4().hex[:8]}.myshopify.com")]
		self.store = self.stores[0]
		self.items = []
		self.users = []
		self.variant_images = {}
		self.product_image = "https://cdn.shopify.com/product.png"
		self.http = patch.object(importer.requests, "request", side_effect=self.request).start()
		self.addCleanup(patch.stopall)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		for user in self.users:
			for permission in frappe.get_all("User Permission", filters={"user": user}, pluck="name"):
				frappe.delete_doc("User Permission", permission, ignore_permissions=True)
			frappe.delete_doc("User", user, ignore_permissions=True, force=True)
		for item in self.items:
			for name in frappe.get_all(
				"File", filters={"attached_to_doctype": "Item", "attached_to_name": item.name}, pluck="name"
			):
				frappe.delete_doc("File", name, ignore_permissions=True)
			if frappe.db.exists("Item", item.name):
				frappe.delete_doc("Item", item.name, ignore_permissions=True, force=True)
		for store in self.stores:
			if frappe.db.exists("Shopify Store", store.name):
				frappe.delete_doc("Shopify Store", store.name, ignore_permissions=True, force=True)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
		super().tearDown()

	def item(self, suffix, **values):
		item = make_item(f"{self.prefix} {suffix}", self.store, **values)
		self.items.append(item)
		return item

	def request(self, method, url, **kwargs):
		if method == "GET":
			return FakeResponse()
		variant = kwargs["json"]["variables"]["id"]
		return FakeResponse(
			payload={
				"data": {
					"node": {
						"id": variant,
						"image": {"url": self.variant_images[variant]}
						if self.variant_images.get(variant)
						else None,
						"product": {
							"id": "gid://shopify/Product/100",
							"images": {"nodes": [{"url": self.product_image}] if self.product_image else []},
						},
					}
				}
			}
		)

	def prepare(self):
		state = {
			"run_id": uuid4().hex,
			"cursor": "",
			"processed": 0,
			"imported": 0,
			"existing": 0,
			"no_image": 0,
			"disabled": 0,
			"requested_by": "Administrator",
			"started_at": str(now_datetime()),
			"current_item": "",
			"completed_at": None,
		}
		importer._new_attempt(state)
		importer._write_state(self.store.name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
		return state

	def run_import(self, state=None):
		state = state or self.prepare()
		importer.run_image_import(self.store.name, state["attempt_token"])
		return importer._read_state(self.store.name)

	def test_import_attaches_file_and_preserves_existing_image(self):
		item = self.item("A")
		existing = self.item("B", image="https://example.com/existing.png")
		state = self.run_import()
		self.assertEqual((state["status"], state["imported"], state["existing"]), ("Completed", 1, 1))
		url = frappe.db.get_value("Item", item.name, "image")
		self.assertTrue(url.startswith("/files/"))
		files = frappe.get_all(
			"File", filters={"attached_to_name": item.name, "attached_to_field": "image"}, pluck="file_url"
		)
		self.assertEqual(files, [url])
		self.assertEqual(frappe.db.get_value("Item", existing.name, "image"), existing.image)
		self.assertEqual(self.http.call_count, 2)

	def test_variant_image_precedes_product_image(self):
		self.item("A")
		self.variant_images["gid://shopify/ProductVariant/101"] = "https://cdn.shopify.com/variant.png"
		self.run_import()
		self.assertEqual(self.http.call_args_list[-1].args, ("GET", "https://cdn.shopify.com/variant.png"))

	def test_failure_and_resume_keep_previous_checkpoint(self):
		first = self.item("A")
		second = self.item("B", variant="102")

		def fail_second(method, url, **kwargs):
			if method == "POST" and kwargs["json"]["variables"]["id"].endswith("/102"):
				return FakeResponse(status=403)
			return self.request(method, url, **kwargs)

		self.http.side_effect = fail_second
		state = self.run_import()
		self.assertEqual((state["status"], state["cursor"], state["imported"]), ("Failed", first.name, 1))
		self.assertEqual(state["current_item"], second.name)
		self.assertFalse(frappe.db.get_value("Item", second.name, "image"))
		self.http.side_effect = self.request
		importer._new_attempt(state)
		importer._write_state(self.store.name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
		state = self.run_import(state)
		self.assertEqual((state["status"], state["processed"], state["imported"]), ("Completed", 2, 2))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": first.name}), 1)

	def test_conflicting_mapping_stops_without_cursor_advance(self):
		item = self.item("A")
		add_mapping(item, self.store, variant="102")
		self.variant_images["gid://shopify/ProductVariant/102"] = "https://cdn.shopify.com/other.png"
		state = self.run_import()
		self.assertEqual(state["status"], "Failed")
		self.assertEqual(state["cursor"], "")
		self.assertIn("different Shopify images", state["error"])
		self.assertFalse(frappe.db.get_value("Item", item.name, "image"))

	def test_identical_sources_from_multiple_variants_are_allowed(self):
		item = self.item("A")
		add_mapping(item, self.store, variant="102")
		state = self.run_import()
		self.assertEqual(state["imported"], 1)
		self.assertEqual(frappe.db.count("File", {"attached_to_name": item.name}), 1)

	def test_no_source_and_disabled_are_counted(self):
		self.item("A")
		self.item("B", disabled=1)
		self.product_image = None
		state = self.run_import()
		self.assertEqual((state["no_image"], state["disabled"], state["processed"]), (1, 1, 2))

	def test_stale_attempt_cannot_update_item(self):
		item = self.item("A")
		state = self.prepare()
		importer.run_image_import(self.store.name, "old-token")
		self.assertFalse(frappe.db.get_value("Item", item.name, "image"))
		self.assertEqual(importer._read_state(self.store.name)["attempt_token"], state["attempt_token"])
		self.http.assert_not_called()

	def test_worker_rejects_a_different_requesting_user(self):
		item = self.item("A")
		state = self.prepare()
		state["requested_by"] = "Guest"
		importer._write_state(self.store.name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic request identity must survive the worker claim
		state = self.run_import(state)
		self.assertEqual((state["status"], state["cursor"], state["processed"]), ("Failed", "", 0))
		self.assertEqual(frappe.session.user, "Administrator")
		self.assertFalse(frappe.db.get_value("Item", item.name, "image"))
		self.http.assert_not_called()

	def test_concurrent_image_is_preserved(self):
		item = self.item("A")

		def set_image(method, url, **kwargs):
			if method == "GET":
				frappe.db.set_value("Item", item.name, "image", "/files/user-image.png")
				frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
			return self.request(method, url, **kwargs)

		self.http.side_effect = set_image
		state = self.run_import()
		self.assertEqual(state["existing"], 1)
		self.assertEqual(frappe.db.get_value("Item", item.name, "image"), "/files/user-image.png")
		self.assertEqual(frappe.db.count("File", {"attached_to_name": item.name}), 0)

	def test_unsafe_url_and_invalid_image_fail(self):
		for url in (
			"http://cdn.shopify.com/image.png",
			"https://localhost/image.png",
			"https://cdn.shopify.com.example.com/a.png",
			"https://user:password@cdn.shopify.com/a.png",
		):
			with self.subTest(url=url), self.assertRaises(importer.ImageImportError):
				importer._download_image(url)
		self.http.assert_not_called()
		self.http.return_value = FakeResponse(content=b"<html>not an image</html>")
		self.http.side_effect = None
		with self.assertRaises(importer.ImageImportError):
			importer._download_image("https://cdn.shopify.com/a.png")

	def test_retry_is_bounded_and_redirect_is_rejected(self):
		self.http.side_effect = [FakeResponse(status=503), FakeResponse(status=429), FakeResponse()]
		with patch.object(importer.time, "sleep"):
			importer._download_image("https://cdn.shopify.com/a.png")
		self.assertEqual(self.http.call_count, 3)
		self.http.reset_mock()
		self.http.side_effect = [FakeResponse(status=302)]
		with self.assertRaises(importer.ImageImportError):
			importer._download_image("https://cdn.shopify.com/a.png")
		self.assertEqual(self.http.call_count, 1)

	def test_start_deduplication_recovery_and_fresh_scan(self):
		self.item("A")
		# Queue submission is an external boundary; workers are run directly in this test.
		with patch.object(importer.frappe, "enqueue") as enqueue:
			first = importer.enqueue_image_import(self.store.name)
			second = importer.enqueue_image_import(self.store.name)
			self.assertEqual(first["attempt_token"], second["attempt_token"])
			self.assertEqual(enqueue.call_count, 1)
			first["status"] = "Running"
			first["lease_until"] = str(add_to_date(now_datetime(), seconds=-1))
			importer._write_state(self.store.name, first)
			resumed = importer.enqueue_image_import(self.store.name)
			self.assertNotEqual(first["attempt_token"], resumed["attempt_token"])
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
			state = self.run_import(resumed)
			self.assertEqual(state["status"], "Completed")
			self.item("B", variant="102")
			fresh = importer.enqueue_image_import(self.store.name)
			self.assertNotEqual(fresh["run_id"], state["run_id"])
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
			state = self.run_import(fresh)
			self.assertEqual((state["existing"], state["imported"]), (1, 1))

	def test_guest_cannot_start_import(self):
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
		with self.set_user("Guest"), self.assertRaises(frappe.PermissionError):
			importer.enqueue_image_import(self.store.name)

	def test_store_save_preserves_committed_progress(self):
		stale_store = frappe.get_doc("Shopify Store", self.store.name)
		state = self.prepare()
		stale_store.enable_image_sync = 1
		stale_store.save()
		self.assertEqual(importer._read_state(self.store.name), state)

	def test_failure_after_file_insert_rolls_back_checkpoint(self):
		item = self.item("A")
		state = self.prepare()
		original_write = importer._write_state

		def fail_checkpoint(store_name, progress):
			if progress["processed"]:
				raise RuntimeError("Synthetic checkpoint failure")
			return original_write(store_name, progress)

		with patch.object(importer, "_write_state", side_effect=fail_checkpoint):
			result = self.run_import(state)
		self.assertEqual((result["status"], result["cursor"], result["processed"]), ("Failed", "", 0))
		self.assertFalse(frappe.db.get_value("Item", item.name, "image"))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": item.name}), 0)
		importer._new_attempt(result)
		importer._write_state(self.store.name, result)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
		self.assertEqual(self.run_import(result)["imported"], 1)

	def test_store_disabled_during_download_stops_checkpoint(self):
		item = self.item("A")

		def disable_store(method, url, **kwargs):
			if method == "GET":
				frappe.db.set_value("Shopify Store", self.store.name, "enabled", 0)
				frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
			return self.request(method, url, **kwargs)

		self.http.side_effect = disable_store
		state = self.run_import()
		self.assertEqual(state["status"], "Failed")
		self.assertFalse(frappe.db.get_value("Item", item.name, "image"))

	def test_import_does_not_queue_outbound_sync(self):
		self.item("A")
		frappe.db.set_value("Shopify Store", self.store.name, {"enable_item_sync": 1, "enable_image_sync": 1})
		with patch.object(importer.frappe, "enqueue") as enqueue:
			state = self.run_import()
			self.assertEqual(state["imported"], 1)
			enqueue.assert_not_called()

	def test_graphql_throttle_is_retried(self):
		self.item("A")
		calls = []

		def throttle_once(method, url, **kwargs):
			calls.append(method)
			if len(calls) == 1:
				return FakeResponse(payload={"errors": [{"extensions": {"code": "THROTTLED"}}]})
			return self.request(method, url, **kwargs)

		self.http.side_effect = throttle_once
		with patch.object(importer.time, "sleep"):
			state = self.run_import()
		self.assertEqual(state["imported"], 1)
		self.assertEqual(calls, ["POST", "POST", "GET"])

	def test_item_user_permission_is_checked(self):
		denied = self.item("A")
		allowed = self.item("B", variant="102")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"test-image-{uuid4().hex[:8]}@example.com",
				"first_name": "Image Test",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}, {"role": "Item Manager"}],
			}
		).insert(ignore_permissions=True)
		self.users.append(user.name)
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user.name,
				"allow": "Item",
				"for_value": allowed.name,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		state = self.prepare()
		state["requested_by"] = user.name
		importer._write_state(self.store.name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- synthetic state must survive worker transaction boundaries
		with self.set_user(user.name):
			self.assertFalse(frappe.has_permission("Item", "write", doc=denied.name))
			self.assertTrue(frappe.has_permission("Item", "write", doc=allowed.name))
			state = self.run_import(state)
		self.assertEqual(state["status"], "Failed")
		self.assertEqual(state["cursor"], "")
		self.assertFalse(frappe.db.get_value("Item", denied.name, "image"))
		self.http.assert_not_called()

	def test_incomplete_source_response_does_not_advance_cursor(self):
		item = self.item("A")
		mutations = [
			lambda node: node.pop("image"),
			lambda node: node.update(image={}),
			lambda node: node.update(image={"url": ""}),
			lambda node: node.update(image={"url": 12}),
			lambda node: node["product"].pop("images"),
			lambda node: node["product"].update(images={}),
			lambda node: node["product"].update(images={"nodes": [{}]}),
		]
		for mutate in mutations:
			with self.subTest(mutate=mutate):

				def incomplete(method, url, **kwargs):
					response = self.request(method, url, **kwargs)
					mutate(response.payload["data"]["node"])
					return response

				self.http.side_effect = incomplete
				state = self.run_import()
				self.assertEqual((state["status"], state["cursor"], state["processed"]), ("Failed", "", 0))
				self.assertFalse(frappe.db.get_value("Item", item.name, "image"))

	def test_other_store_mappings_are_not_imported(self):
		own = self.item("A")
		other_store = make_store(f"test-images-other-{uuid4().hex[:8]}.myshopify.com")
		self.stores.append(other_store)
		other = make_item(f"{self.prefix} B", other_store)
		self.items.append(other)
		state = self.run_import()
		self.assertEqual(state["processed"], 1)
		self.assertTrue(frappe.db.get_value("Item", own.name, "image"))
		self.assertFalse(frappe.db.get_value("Item", other.name, "image"))

	def test_variant_from_another_product_fails(self):
		item = self.item("A", product="200")
		state = self.run_import()
		self.assertEqual((state["status"], state["cursor"]), ("Failed", ""))
		self.assertIn("another product", state["error"])
		self.assertFalse(frappe.db.get_value("Item", item.name, "image"))

	def test_chunk_continuation_preserves_checkpoint(self):
		first = self.item("A")
		second = self.item("B", variant="102")
		initial = self.prepare()
		with (
			patch.object(importer, "time", wraps=importer.time) as clock,
			patch.object(importer.frappe, "enqueue") as enqueue,
		):
			clock.monotonic.side_effect = [0, 0, importer.CHUNK_SECONDS + 1]
			queued = self.run_import(initial)
			enqueue.assert_called_once()
			arguments = enqueue.call_args.kwargs
			self.assertEqual(arguments["queue"], "long")
			self.assertTrue(arguments["enqueue_after_commit"])
			self.assertEqual(arguments["attempt_token"], queued["attempt_token"])
			self.assertEqual(arguments["job_id"], f"shopify-images-{queued['attempt_token']}")
			self.assertNotEqual(arguments["job_id"], f"shopify-images-{initial['attempt_token']}")
		self.assertEqual(queued["status"], "Queued")
		self.assertEqual(queued["run_id"], initial["run_id"])
		self.assertEqual((queued["cursor"], queued["processed"], queued["imported"]), (first.name, 1, 1))
		self.assertFalse(frappe.db.get_value("Item", second.name, "image"))
		self.http.reset_mock()
		completed = self.run_import(queued)
		self.assertEqual(
			(completed["status"], completed["processed"], completed["imported"]), ("Completed", 2, 2)
		)
		self.assertEqual(frappe.db.count("File", {"attached_to_name": first.name}), 1)
		self.assertEqual(self.http.call_count, 2)
		self.assertTrue(self.http.call_args_list[0].kwargs["json"]["variables"]["id"].endswith("/102"))

	def test_jpeg_import_uses_native_exif_stripping(self):
		item = self.item("A")
		content = image_bytes(format_name="JPEG", with_exif=True)
		with Image.open(io.BytesIO(content)) as source:
			self.assertTrue(source.getexif())

		def jpeg_response(method, url, **kwargs):
			return FakeResponse(content=content) if method == "GET" else self.request(method, url, **kwargs)

		self.http.side_effect = jpeg_response
		with change_settings("System Settings", {"strip_exif_metadata_from_uploaded_images": 1}, commit=True):
			state = self.run_import()
		self.assertEqual((state["status"], state["imported"]), ("Completed", 1))
		file_name = frappe.db.get_value(
			"File", {"attached_to_name": item.name, "attached_to_field": "image"}, "name"
		)
		file_doc = frappe.get_doc("File", file_name)
		self.assertEqual(frappe.db.get_value("Item", item.name, "image"), file_doc.file_url)
		with Image.open(io.BytesIO(file_doc.get_content())) as saved:
			self.assertEqual(saved.format, "JPEG")
			self.assertFalse(saved.getexif())

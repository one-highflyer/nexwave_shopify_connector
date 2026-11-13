# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

"""Import missing primary Item images with one committed checkpoint per Item."""

import hashlib
import io
import json
import time
from urllib.parse import urlsplit
from uuid import uuid4

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime
from PIL import Image, UnidentifiedImageError

WORKER_TIMEOUT = 600
LEASE_SECONDS = 660
CHUNK_SECONDS = 180
MAX_IMAGE_BYTES = 10 * 1024 * 1024

IMAGE_QUERY = """
query ItemImage($id: ID!) {
  node(id: $id) {
    ... on ProductVariant {
      id
      image { url }
      product { id images(first: 1) { nodes { url } } }
    }
  }
}
"""


class ImageImportError(Exception):
	pass


class StaleAttempt(Exception):
	pass


def _read_state(store_name, *, for_update=False):
	raw = frappe.db.get_value("Shopify Store", store_name, "image_import_state", for_update=for_update)
	return json.loads(raw) if raw else {}


def _write_state(store_name, state):
	state["updated_at"] = str(now_datetime())
	frappe.db.set_value(
		"Shopify Store", store_name, "image_import_state", json.dumps(state), update_modified=False
	)


def _check_store(store_name):
	store = frappe.get_doc("Shopify Store", store_name)
	store.check_permission("write")
	if not frappe.has_permission("Item", "write"):
		frappe.throw(_("You need permission to update Items."), frappe.PermissionError)
	if not store.enabled:
		frappe.throw(_("Enable the Shopify Store before importing images."))
	return store


def _new_attempt(state):
	state.update(
		status="Queued",
		attempt_token=uuid4().hex,
		lease_until=str(add_to_date(now_datetime(), seconds=LEASE_SECONDS)),
		error="",
	)


def _enqueue(store_name, state):
	frappe.enqueue(
		run_image_import,
		queue="long",
		timeout=WORKER_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"shopify-images-{state['attempt_token']}",
		deduplicate=True,
		store_name=store_name,
		attempt_token=state["attempt_token"],
	)


def enqueue_image_import(store_name):
	"""Start a fresh scan, or resume the last incomplete scan."""
	_check_store(store_name)
	state = _read_state(store_name, for_update=True)
	if state.get("status") in ("Queued", "Running") and get_datetime(state["lease_until"]) > now_datetime():
		return state
	if not state or state.get("status") == "Completed":
		state = {
			"run_id": uuid4().hex,
			"cursor": "",
			"processed": 0,
			"imported": 0,
			"existing": 0,
			"no_image": 0,
			"disabled": 0,
			"started_at": str(now_datetime()),
			"completed_at": None,
			"current_item": "",
		}
	state["requested_by"] = frappe.session.user
	_new_attempt(state)
	_write_state(store_name, state)
	_enqueue(store_name, state)
	return state


def _claim_state(store_name, token):
	state = _read_state(store_name, for_update=True)
	if state.get("attempt_token") != token or state.get("status") not in ("Queued", "Running"):
		raise StaleAttempt
	return state


def _next_item(store_name, cursor):
	rows = frappe.db.sql(
		"""select distinct parent from `tabItem Shopify Store`
		where shopify_store=%s and parenttype='Item' and parentfield='shopify_stores'
		and parent > %s order by parent limit 1""",
		(store_name, cursor),
	)
	return rows[0][0] if rows else None


def _mappings(store_name, item_name):
	return frappe.get_all(
		"Item Shopify Store",
		filters={
			"shopify_store": store_name,
			"parent": item_name,
			"parenttype": "Item",
			"parentfield": "shopify_stores",
		},
		fields=["name", "enabled", "shopify_product_id", "shopify_variant_id"],
		order_by="name",
	)


def _mapping_signature(rows):
	return [(r.name, r.enabled, r.shopify_product_id, r.shopify_variant_id) for r in rows]


def _request(method, url, **kwargs):
	"""Retry transient HTTP failures only. Never include credentials in errors."""
	for attempt in range(3):
		try:
			response = requests.request(method, url, timeout=(10, 30), allow_redirects=False, **kwargs)
		except (requests.Timeout, requests.ConnectionError) as exc:
			if attempt == 2:
				raise ImageImportError("Image import request failed after three attempts.") from exc
		else:
			if response.status_code == 200:
				return response
			status = response.status_code
			response.close()
			if status != 429 and status < 500:
				raise ImageImportError(f"Image import request returned HTTP {status}.")
			if attempt == 2:
				raise ImageImportError(f"Image import request returned HTTP {status} after three attempts.")
		time.sleep(2**attempt)


def _source_image(store, rows):
	active = [row for row in rows if row.enabled]
	if not active:
		return None
	# One Item can map to several variants only when their selected image agrees.
	urls = set()
	domain, version, token = store._get_auth_details()
	if not domain or not domain.endswith(".myshopify.com") or any(c in domain for c in "/:@?#"):
		raise ImageImportError("The Shopify store domain is invalid.")
	pairs = {(str(row.shopify_product_id or ""), str(row.shopify_variant_id or "")) for row in active}
	if len(pairs) > 50:
		raise ImageImportError("The Item has more than 50 distinct Shopify mappings. Review its mappings.")
	for product_id, variant_id in sorted(pairs):
		if not product_id.isdecimal() or not variant_id.isdecimal():
			raise ImageImportError("The Item needs an exact Shopify product and variant mapping.")
		for attempt in range(3):
			response = _request(
				"POST",
				f"https://{domain}/admin/api/{version}/graphql.json",
				headers={"X-Shopify-Access-Token": token},
				json={
					"query": IMAGE_QUERY,
					"variables": {"id": f"gid://shopify/ProductVariant/{variant_id}"},
				},
			)
			try:
				payload = response.json()
			finally:
				response.close()
			errors = payload.get("errors") or []
			if not errors:
				break
			if attempt < 2 and all(
				(error.get("extensions") or {}).get("code") == "THROTTLED" for error in errors
			):
				time.sleep(2**attempt)
				continue
			raise ImageImportError("Shopify rejected the image query. Check API access and the API version.")
		node = (payload.get("data") or {}).get("node") or {}
		product = node.get("product") or {}
		if (
			node.get("id") != f"gid://shopify/ProductVariant/{variant_id}"
			or product.get("id") != f"gid://shopify/Product/{product_id}"
		):
			raise ImageImportError("The mapped Shopify variant is missing or belongs to another product.")
		if "image" not in node or not isinstance(product.get("images"), dict):
			raise ImageImportError("Shopify returned incomplete image data. Run the import again.")
		images = product["images"].get("nodes")
		if not isinstance(images, list):
			raise ImageImportError("Shopify returned incomplete image data. Run the import again.")
		variant_url = _image_url(node["image"], allow_null=True)
		product_url = _image_url(images[0]) if images else None
		urls.add(variant_url or product_url)
	if len(urls) != 1:
		raise ImageImportError("The Item mappings select different Shopify images. Review its mappings.")
	return urls.pop()


def _image_url(image, *, allow_null=False):
	if image is None and allow_null:
		return None
	if not isinstance(image, dict) or not isinstance(image.get("url"), str) or not image["url"].strip():
		raise ImageImportError("Shopify returned an invalid image URL. Run the import again.")
	return image["url"]


def _download_image(url):
	parts = urlsplit(url)
	if (
		parts.scheme != "https"
		or parts.hostname != "cdn.shopify.com"
		or parts.username
		or parts.password
		or parts.port not in (None, 443)
	):
		raise ImageImportError("The image URL must use the Shopify HTTPS CDN.")
	limit = min(MAX_IMAGE_BYTES, int(frappe.conf.get("max_file_size") or MAX_IMAGE_BYTES))
	response = _request("GET", url, stream=True)
	try:
		content = bytearray()
		for chunk in response.iter_content(chunk_size=64 * 1024):
			content.extend(chunk)
			if len(content) > limit:
				raise ImageImportError("The Shopify image exceeds the allowed file size.")
	finally:
		response.close()
	try:
		with Image.open(io.BytesIO(content)) as image:
			image.verify()
			format_name = image.format
		if format_name not in ("JPEG", "PNG", "GIF", "WEBP"):
			raise ImageImportError("The Shopify image must be JPEG, PNG, GIF, or WebP.")
	except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
		raise ImageImportError("The Shopify response is not a valid image.") from exc
	extension = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}[format_name]
	return bytes(content), extension


def _save_image(item_name, content, extension):
	filename = f"shopify-{hashlib.sha256(content).hexdigest()}.{extension}"
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"content": content,
			"is_private": 0,
			"attached_to_doctype": "Item",
			"attached_to_name": item_name,
			"attached_to_field": "image",
		}
	)
	try:
		file_doc.insert()
	except frappe.DuplicateEntryError:
		file_doc = frappe.get_doc("File", file_doc.duplicate_entry)
	frappe.db.set_value("Item", item_name, "image", file_doc.file_url)


def _process_item(store, item_name, token):
	item = frappe.get_doc("Item", item_name)
	item.check_permission("write")
	rows = _mappings(store.name, item_name)
	if not rows:
		raise ImageImportError("The Item mapping was removed. Restore it or start a new import after review.")
	outcome = (
		"disabled"
		if item.disabled or not any(row.enabled for row in rows)
		else "existing"
		if item.image
		else None
	)
	content = extension = None
	if not outcome:
		url = _source_image(store, rows)
		if url:
			content, extension = _download_image(url)
			outcome = "imported"
		else:
			outcome = "no_image"
	# End the read transaction before taking locks, so concurrent changes are visible.
	frappe.db.rollback()
	state = _claim_state(store.name, token)
	_check_store(store.name)
	frappe.db.get_value("Item", item_name, "name", for_update=True)
	item = frappe.get_doc("Item", item_name)
	item.check_permission("write")
	if _mapping_signature(_mappings(store.name, item_name)) != _mapping_signature(rows):
		raise ImageImportError("The Item mapping changed during import. Run the import again.")
	if item.disabled:
		outcome = "disabled"
	elif item.image:
		outcome = "existing"
	elif outcome == "existing":
		raise ImageImportError("The Item image changed during import. Run the import again.")
	if outcome == "imported":
		_save_image(item_name, content, extension)
	state["cursor"] = item_name
	state["current_item"] = ""
	state["processed"] += 1
	state[outcome] += 1
	_write_state(store.name, state)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- one durable Item checkpoint


def run_image_import(store_name, attempt_token):
	"""Run a bounded chunk. A later button click can recover a killed worker."""
	current_item = ""
	try:
		state = _claim_state(store_name, attempt_token)
		if state["status"] != "Queued":
			return
		state["status"] = "Running"
		state["lease_until"] = str(add_to_date(now_datetime(), seconds=LEASE_SECONDS))
		_write_state(store_name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- claim worker attempt
		if frappe.session.user != state["requested_by"]:
			frappe.throw(_("The image import must run as its requesting user."), frappe.PermissionError)
		store = _check_store(store_name)
		started = time.monotonic()
		while time.monotonic() - started < CHUNK_SECONDS:
			state = _read_state(store_name)
			current_item = _next_item(store_name, state["cursor"])
			if not current_item:
				state = _claim_state(store_name, attempt_token)
				state.update(status="Completed", completed_at=str(now_datetime()), current_item="", error="")
				_write_state(store_name, state)
				frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- persist completion
				return
			_process_item(store, current_item, attempt_token)
		state = _claim_state(store_name, attempt_token)
		_new_attempt(state)
		_write_state(store_name, state)
		_enqueue(store_name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- queue next chunk after checkpoint
	except StaleAttempt:
		frappe.db.rollback()
	except Exception as exc:
		frappe.db.rollback()
		try:
			state = _claim_state(store_name, attempt_token)
		except StaleAttempt:
			frappe.db.rollback()
			return
		# Avoid saving URLs, access tokens, or HTTP response bodies from unknown exceptions.
		message = (
			str(exc)
			if isinstance(exc, ImageImportError)
			else _("Image import failed ({0}). Check permissions, mappings, and connection settings.").format(
				type(exc).__name__
			)
		)
		state.update(status="Failed", current_item=current_item, error=message)
		_write_state(store_name, state)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- preserve failure without advancing cursor

# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import json
import re
from typing import TYPE_CHECKING, Any

import frappe
from frappe import _

from nexwave_shopify_connector.utils.logger import get_logger

if TYPE_CHECKING:
	from frappe.model.document import Document

# Matches extension markers and everything after them (case-insensitive):
#   "ext 651", "ext. 651", "ext651", "extension 789", "x100", "x 100"
# The "x" pattern requires digits after it to avoid false positives (e.g. "0x" in hex)
_EXTENSION_RE = re.compile(r"\s*(?:ext(?:ension)?\.?\s*\d+|(?<=\d)\s*x\s*\d+).*$", re.IGNORECASE)

# Frappe's phone validation allows: digits, space, +, _, -, comma, period, *, #, parentheses
# Max length: 20 characters
_PHONE_DISALLOWED_RE = re.compile(r"[^0-9 +_\-,.*#()]")
_PHONE_MAX_LENGTH = 20


def sanitize_phone_number(raw_phone: str | None) -> tuple[str | None, str | None]:
	"""Sanitize a phone number to comply with Frappe's phone validation.

	Frappe's validate_phone_number() uses regex [0-9 +_\\-,.*#()] with max 20 chars.
	First strips extension markers (ext, extension, x) and everything after them,
	then removes remaining disallowed characters and truncates.

	Args:
		raw_phone: Raw phone number string from Shopify.

	Returns:
		Tuple of (sanitized_phone, original_if_modified):
		- sanitized_phone: Cleaned phone, or None if empty after cleaning.
		- original_if_modified: Original string only when data was actually
		  stripped/truncated; None if no changes were needed.
	"""
	if not raw_phone:
		return None, None

	raw_phone = raw_phone.strip()
	if not raw_phone:
		return None, None

	# Strip extension markers and everything after them
	cleaned = _EXTENSION_RE.sub("", raw_phone)
	# Strip remaining disallowed characters
	cleaned = _PHONE_DISALLOWED_RE.sub("", cleaned)
	# Collapse runs of whitespace that may result from stripping inner chars
	cleaned = " ".join(cleaned.split())
	# Truncate to Frappe's max length
	cleaned = cleaned[:_PHONE_MAX_LENGTH].rstrip()

	modified = cleaned != raw_phone

	if modified:
		logger = get_logger()
		if not cleaned:
			logger.warning("Phone number completely emptied by sanitization: %r", raw_phone)
		else:
			logger.info("Phone number sanitized: %r -> %r", raw_phone, cleaned)

	if not cleaned:
		return None, raw_phone if modified else None

	return cleaned, raw_phone if modified else None


def create_shopify_log(
	status: str = "Queued",
	method: str | None = None,
	shopify_store: str | None = None,
	request_data: dict[str, Any] | None = None,
	response_data: dict[str, Any] | None = None,
	exception: str | None = None,
	message: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> "Document":
	"""
	Create a NexWave Shopify Log entry for Shopify operations.

	Args:
		status: Log status (Queued, Success, Error)
		method: The method being called
		shopify_store: Name of the Shopify Store
		request_data: Request payload
		response_data: Response data
		exception: Exception message
		message: Additional message
		reference_doctype: Reference DocType (e.g., Item, Sales Order)
		reference_name: Reference document name

	Returns:
		Created log document
	"""
	# Get store from context if not provided
	if not shopify_store:
		shopify_store = frappe.flags.get("shopify_store")

	log = frappe.get_doc({
		"doctype": "NexWave Shopify Log",
		"status": status,
		"shopify_store": shopify_store,
		"method": method,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"request_data": json.dumps(request_data, indent=2) if request_data else None,
		"response_data": json.dumps(response_data, indent=2) if response_data else None,
		"message": message,
		"traceback": exception,
	})

	log.insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- error logging must persist even if caller rolls back
	return log


def get_shopify_store_context() -> str | None:
	"""
	Get the current Shopify Store from context.

	Returns:
		Shopify Store name if set, None otherwise
	"""
	return frappe.flags.get("shopify_store")


def is_item_eligible_for_store(item: "Document", store: "Document") -> bool:
	"""
	Check if an item is eligible to sync to a specific store.

	Eligibility is determined by:
	1. Manual override via Item Shopify Store row with enabled=1
	2. Automatic eligibility via store filters (if no manual row or row exists but not explicitly disabled)

	Args:
		item: Item document
		store: Shopify Store document

	Returns:
		True if item should sync to this store
	"""
	# Check for manual override via Item Shopify Store
	manual_link = get_item_shopify_store_row(item, store)

	if manual_link:
		if manual_link.enabled:
			return True
		else:
			return False  # Explicitly disabled

	# Check auto-eligibility via store filters
	if not store.item_filters:
		return False  # No filters means no auto-eligibility

	for filter_row in store.item_filters:
		if not evaluate_filter(item, filter_row):
			return False

	return True  # All filters passed


def get_item_shopify_store_row(item: "Document", store: "Document") -> "Document | None":
	"""
	Get the Item Shopify Store child row for a specific store.

	Args:
		item: Item document
		store: Shopify Store document

	Returns:
		Item Shopify Store row if found, None otherwise
	"""
	if not hasattr(item, "shopify_stores"):
		return None

	for row in item.shopify_stores:
		if row.shopify_store == store.name:
			return row

	return None


def evaluate_filter(item: "Document", filter_row: "Document") -> bool:
	"""
	Evaluate a single filter condition against an item.

	Args:
		item: Item document
		filter_row: Shopify Store Item Filter row

	Returns:
		True if filter condition passes
	"""
	field_name = filter_row.erpnext_field
	filter_type = filter_row.filter_type
	filter_value = filter_row.field_value

	# Get field value from item
	item_value = getattr(item, field_name, None) if hasattr(item, field_name) else None

	# Also try to get from item dict if not found
	if item_value is None and isinstance(item, dict):
		item_value = item.get(field_name)

	if filter_type == "Field Has Value" or filter_type == "Field Not Empty":
		# Check if field has any value (not None, not empty string, not 0)
		return bool(item_value)

	elif filter_type == "Field Equals":
		# Check if field equals specific value
		return str(item_value) == str(filter_value) if item_value is not None else False

	return False


def get_eligible_stores_for_item(item: "Document") -> list:
	"""
	Get all stores that an item is eligible to sync to.

	Args:
		item: Item document

	Returns:
		List of Shopify Store documents
	"""
	eligible_stores = []

	# Get all enabled stores with item sync enabled
	stores = frappe.get_all(
		"Shopify Store",
		filters={"enabled": 1, "enable_item_sync": 1},
		pluck="name"
	)

	for store_name in stores:
		store = frappe.get_doc("Shopify Store", store_name)
		if is_item_eligible_for_store(item, store):
			eligible_stores.append(store)

	return eligible_stores


def format_shopify_log_message(store_name: str, message: str) -> str:
	"""
	Format a log message with store context.

	Args:
		store_name: Name of the Shopify Store
		message: Log message

	Returns:
		Formatted message with store context
	"""
	return f"[Shopify Store: {store_name}] {message}"

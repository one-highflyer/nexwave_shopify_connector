# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _


def mask_token_history(version, method=None):
	"""Mask full access tokens before inserting new Shopify Store history."""
	if version.ref_doctype != "Shopify Store":
		return

	try:
		data = json.loads(version.data)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid Shopify Store audit data."))
	if not isinstance(data, dict) or not isinstance(data.get("changed", []), list):
		frappe.throw(_("Invalid Shopify Store audit data."))

	masked = False
	for change in data.get("changed", []):
		if not isinstance(change, list) or len(change) != 3 or not isinstance(change[0], str):
			frappe.throw(_("Invalid Shopify Store audit data."))
		if change[0] != "access_token":
			continue
		for index in (1, 2):
			value = _mask_token(change[index])
			if value != change[index]:
				change[index] = value
				masked = True
	if masked:
		version.data = frappe.as_json(data, indent=None, separators=(",", ":"))


def _mask_token(value):
	if value is None or value == "":
		return value
	if not isinstance(value, str):
		return "********"
	if set(value) == {"*"}:
		return value
	if len(value) <= 8:
		return "********"
	return f"{value[:4]}********{value[-4:]}"

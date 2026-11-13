# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import io

import frappe
from PIL import Image


def make_store(domain):
	return frappe.get_doc(
		{
			"doctype": "Shopify Store",
			"shop_domain": domain,
			"company": "_Test Company",
			"auth_method": "Legacy (Access Token)",
			"access_token": "synthetic-image-token",
			"api_version": "2026-01",
			"enabled": 1,
		}
	).insert(ignore_permissions=True)


def make_item(name, store, variant="101", product="100", **values):
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": name,
			"item_name": name,
			"item_group": "All Item Groups",
			"stock_uom": "Nos",
			**values,
		}
	).insert(ignore_permissions=True)
	add_mapping(item, store, variant=variant, product=product)
	return item


def add_mapping(item, store, variant="101", product="100"):
	row = item.append(
		"shopify_stores",
		{
			"shopify_store": store.name,
			"enabled": 1,
			"shopify_product_id": product,
			"shopify_variant_id": variant,
		},
	)
	row.db_insert()
	return row


def image_bytes(color="red", format_name="PNG", with_exif=False):
	buffer = io.BytesIO()
	image = Image.new("RGB", (2, 2), color=color)
	options = {}
	if with_exif:
		metadata = image.getexif()
		metadata[315] = "Synthetic image fixture"
		options["exif"] = metadata
	image.save(buffer, format=format_name, **options)
	return buffer.getvalue()

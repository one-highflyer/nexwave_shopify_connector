# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ShopifyStoreItemField(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		default_value: DF.Data | None
		erpnext_field: DF.Data
		metafield_key: DF.Data | None
		metafield_namespace: DF.Data | None
		metafield_type: DF.Literal["", "single_line_text_field", "multi_line_text_field", "number_integer", "number_decimal", "boolean", "date", "json", "url", "color", "rating"]
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		shopify_field_type: DF.Literal["Standard Field", "Metafield"]
		shopify_standard_field: DF.Literal["", "body_html", "vendor", "product_type", "tags", "handle", "compare_at_price", "price", "weight", "weight_unit", "barcode", "sku"]
	# end: auto-generated types
	pass

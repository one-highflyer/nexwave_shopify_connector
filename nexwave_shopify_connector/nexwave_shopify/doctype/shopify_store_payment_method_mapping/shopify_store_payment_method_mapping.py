# Copyright (c) 2025, HighFlyer and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ShopifyStorePaymentMethodMapping(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account: DF.Link | None
		mode_of_payment: DF.Link
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		shopify_gateway: DF.Data
	# end: auto-generated types

	pass

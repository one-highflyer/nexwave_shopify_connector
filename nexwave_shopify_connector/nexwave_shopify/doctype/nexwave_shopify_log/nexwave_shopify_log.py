# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class NexWaveShopifyLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		message: DF.SmallText | None
		method: DF.Data | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		request_data: DF.Code | None
		response_data: DF.Code | None
		shopify_store: DF.Link | None
		status: DF.Literal["Queued", "Success", "Error"]
		traceback: DF.Code | None
	# end: auto-generated types
	pass

frappe.ui.form.on("Item", {
	refresh(frm) {
		// Show sync button if item has Shopify stores configured
		if (frm.doc.shopify_stores && frm.doc.shopify_stores.length > 0) {
			frm.add_custom_button(
				__("Sync to Shopify"),
				function () {
					frappe.call({
						method: "nexwave_shopify_connector.nexwave_shopify.product.manual_sync_item_to_shopify",
						args: { item_code: frm.doc.name },
						freeze: true,
						freeze_message: __("Syncing to Shopify..."),
						callback: function (r) {
							if (!r.exc) {
								frappe.show_alert({
									message: __("Item sync queued for all configured stores"),
									indicator: "green",
								});
							}
						},
					});
				},
				__("Actions")
			);
		}
	},
});

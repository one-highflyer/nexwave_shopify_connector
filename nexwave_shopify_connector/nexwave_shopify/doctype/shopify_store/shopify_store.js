// Copyright (c) 2024, HighFlyer and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shopify Store", {
	refresh(frm) {
		// Add custom buttons
		if (!frm.is_new()) {
			frm.add_custom_button(__("Test Connection"), function() {
				frm.call({
					method: "test_connection",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Testing connection...")
				});
			}, __("Actions"));

			frm.add_custom_button(__("Fetch Shopify Locations"), function() {
				frm.call({
					method: "fetch_shopify_locations",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Fetching locations...")
				});
			}, __("Actions"));

			frm.add_custom_button(__("Fetch Products & Map by SKU"), function() {
				frappe.confirm(
					__("This will fetch all products from Shopify and create Item Shopify Store mappings for items with matching SKUs. Continue?"),
					function() {
						frm.call({
							method: "fetch_products_and_map_by_sku",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Fetching products and mapping...")
						});
					}
				);
			}, __("Actions"));
		}

		// Populate series options
		frm.set_query("sales_order_series", function() {
			return {
				filters: {
					document_type: "Sales Order"
				}
			};
		});

		frm.set_query("delivery_note_series", function() {
			return {
				filters: {
					document_type: "Delivery Note"
				}
			};
		});

		frm.set_query("sales_invoice_series", function() {
			return {
				filters: {
					document_type: "Sales Invoice"
				}
			};
		});

		// Filter warehouse by company
		frm.set_query("warehouse", function() {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});

		// Filter cost center by company
		frm.set_query("cost_center", function() {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});

		// Filter accounts by company
		frm.set_query("default_sales_tax_account", function() {
			return {
				filters: {
					company: frm.doc.company,
					account_type: "Tax"
				}
			};
		});

		frm.set_query("default_shipping_charges_account", function() {
			return {
				filters: {
					company: frm.doc.company
				}
			};
		});
	},

	company(frm) {
		// Clear company-dependent fields when company changes
		frm.set_value("warehouse", "");
		frm.set_value("cost_center", "");
		frm.set_value("default_sales_tax_account", "");
		frm.set_value("default_shipping_charges_account", "");
	}
});

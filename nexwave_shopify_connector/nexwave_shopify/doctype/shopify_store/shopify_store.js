// Copyright (c) 2024, HighFlyer and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shopify Store", {
	refresh(frm) {
		// Show OAuth status (callback_url is set by backend on save)
		if (frm.doc.auth_method === "OAuth") {
			frm.trigger("show_oauth_status");
		}

		// Add custom buttons
		if (!frm.is_new()) {
			// Show "Connect to Shopify" button for OAuth stores
			if (frm.doc.auth_method === "OAuth" && frm.doc.client_id) {
				frm.add_custom_button(__("Connect to Shopify"), () => {
					frm.trigger("initiate_oauth");
				}, __("Actions"));
			}
			frm.add_custom_button(__("Test Connection"), function () {
				frm.call({
					method: "test_connection",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Testing connection...")
				});
			}, __("Actions"));

			frm.add_custom_button(__("Fetch Shopify Locations"), function () {
				frm.call({
					method: "fetch_shopify_locations",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Fetching locations..."),
					callback: function (r) {
						if (r.message) {
							// Clear existing rows
							frm.clear_table("warehouse_mapping");

							// Add fetched locations to the table
							r.message.forEach(function (location) {
								let row = frm.add_child("warehouse_mapping");
								row.shopify_location_id = location.shopify_location_id;
								row.shopify_location_name = location.shopify_location_name;
								row.erpnext_warehouse = location.erpnext_warehouse;
							});

							// Refresh the field to show updated data
							frm.refresh_field("warehouse_mapping");
						}
					}
				});
			}, __("Actions"));

			frm.add_custom_button(__("Fetch Products & Map by SKU"), function () {
				frappe.confirm(
					__("This will fetch all products from Shopify and create Item Shopify Store mappings for items with matching SKUs. Continue?"),
					function () {
						frm.call({
							method: "fetch_products_and_map_by_sku",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Fetching products and mapping...")
						});
					}
				);
			}, __("Actions"));

			frm.add_custom_button(__("Fetch Shopify Collections"), function () {
				frm.call({
					method: "fetch_shopify_collections",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Fetching collections..."),
					callback: function (r) {
						if (r.message) {
							// Clear existing rows
							frm.clear_table("collection_mapping");

							// Add fetched collections to the table
							r.message.forEach(function (collection) {
								let row = frm.add_child("collection_mapping");
								row.shopify_collection_id = collection.shopify_collection_id;
								row.shopify_collection_title = collection.shopify_collection_title;
								row.field_value = collection.field_value;
							});

							// Refresh the field to show updated data
							frm.refresh_field("collection_mapping");
						}
					}
				});
			}, __("Actions"));

			frm.add_custom_button(__("Register Webhooks"), function () {
				frappe.confirm(
					__("This will re-register all webhooks with Shopify. Existing webhooks for this site will be cleared first. Continue?"),
					function () {
						frm.call({
							method: "register_webhooks",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Registering webhooks with Shopify...")
						});
					}
				);
			}, __("Actions"));

			// Sync buttons - only show when relevant settings are enabled
			if (frm.doc.enabled && frm.doc.enable_item_sync) {
				frm.add_custom_button(__("Sync All Items"), function () {
					frappe.confirm(
						__("This will sync all eligible items to Shopify. This may take a while for large catalogs. Continue?"),
						function () {
							frm.call({
								method: "sync_all_items",
								doc: frm.doc,
								freeze: true,
								freeze_message: __("Queuing items for sync...")
							});
						}
					);
				}, __("Sync"));
			}

			if (frm.doc.enabled && frm.doc.enable_inventory_sync) {
				frm.add_custom_button(__("Sync Inventory"), function () {
					frappe.confirm(
						__("This will sync inventory levels to Shopify for all mapped items. Continue?"),
						function () {
							frm.call({
								method: "sync_inventory",
								doc: frm.doc,
								freeze: true,
								freeze_message: __("Queuing inventory sync...")
							});
						}
					);
				}, __("Sync"));
			}

			if (frm.doc.enabled && frm.doc.sync_orders) {
				frm.add_custom_button(__("Sync Orders"), function () {
					frappe.confirm(
						__("This will fetch new orders from Shopify and create Sales Orders. Continue?"),
						function () {
							frm.call({
								method: "fetch_and_sync_orders",
								doc: frm.doc,
								freeze: true,
								freeze_message: __("Syncing orders from Shopify..."),
								callback: function (r) {
									frm.reload_doc();
								}
							});
						}
					);
				}, __("Sync"));
			}
		}

		// Populate naming series options for Select fields
		frm.trigger("set_naming_series_options");

		// Filter warehouse by company
		frm.set_query("warehouse", function () {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});

		// Filter warehouse mapping by company
		frm.set_query("erpnext_warehouse", "warehouse_mapping", function () {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});

		// Filter cost center by company
		frm.set_query("cost_center", function () {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});

		// Filter accounts by company
		frm.set_query("default_sales_tax_account", function () {
			return {
				filters: {
					company: frm.doc.company,
					account_type: "Tax"
				}
			};
		});

		frm.set_query("default_shipping_charges_account", function () {
			return {
				filters: {
					company: frm.doc.company
				}
			};
		});

		// Filter account in payment method mapping by company and type
		frm.set_query("account", "payment_method_mapping", function () {
			return {
				filters: {
					company: frm.doc.company,
					account_type: ["in", ["Bank", "Cash"]]
				}
			};
		});

		// Filter tax account in child table by company
		frm.set_query("tax_account", "tax_accounts", function () {
			return {
				filters: {
					company: frm.doc.company,
					account_type: "Tax",
					is_group: 0
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
	},

	auth_method(frm) {
		// Clear OAuth fields when switching to Legacy
		if (frm.doc.auth_method === "Legacy (Access Token)") {
			frm.set_value("client_id", "");
			frm.set_value("client_secret", "");
			frm.set_value("callback_url", "");
			frm.set_value("connected_user", "");
			frm.set_value("oauth_status", "Not Connected");
		}
	},

	initiate_oauth(frm) {
		if (!frm.doc.client_id) {
			frappe.msgprint(__("Please enter the Client ID first."));
			return;
		}

		if (!frm.doc.client_secret) {
			frappe.msgprint(__("Please enter the Client Secret first."));
			return;
		}

		// Save the document first if there are unsaved changes
		if (frm.is_dirty()) {
			frappe.msgprint(__("Please save the document before connecting to Shopify."));
			return;
		}

		// Call our authorize endpoint
		frappe.call({
			method: "nexwave_shopify_connector.nexwave_shopify.oauth.authorize",
			args: {
				shopify_store: frm.doc.name
			},
			freeze: true,
			freeze_message: __("Redirecting to Shopify..."),
			callback: (r) => {
				if (r.message) {
					// Redirect to Shopify authorization page
					window.location.href = r.message;
				}
			},
			error: (r) => {
				frappe.msgprint(__("Failed to initiate OAuth flow. Please check your Client ID and Client Secret."));
			}
		});
	},

	show_oauth_status(frm) {
		if (frm.doc.oauth_status === "Connected" && frm.doc.connected_user) {
			frm.dashboard.set_headline_alert(
				__("Connected to Shopify via OAuth as {0}", [frm.doc.connected_user]),
				"green"
			);
		} else if (frm.doc.client_id) {
			frm.dashboard.set_headline_alert(
				__("OAuth configured but not connected. Click 'Connect to Shopify' under Actions to authorize."),
				"yellow"
			);
		}
	},

	set_naming_series_options(frm) {
		// Fetch and set naming series options for each doctype
		const doctypes = [
			{ doctype: "Sales Order", field: "sales_order_series" },
			{ doctype: "Delivery Note", field: "delivery_note_series" },
			{ doctype: "Sales Invoice", field: "sales_invoice_series" }
		];

		doctypes.forEach(function(item) {
			frappe.model.with_doctype(item.doctype, function() {
				let options = frappe.get_meta(item.doctype).fields
					.find(df => df.fieldname === "naming_series");
				if (options && options.options) {
					// Add empty option at the beginning for optional selection
					let series_list = options.options.split("\n").filter(s => s.trim());
					frm.set_df_property(item.field, "options", [""].concat(series_list));
				}
			});
		});
	}
});

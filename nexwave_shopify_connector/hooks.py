app_name = "nexwave_shopify_connector"
app_title = "NexWave Shopify Connector"
app_publisher = "HighFlyer"
app_description = "Multi-store Shopify connector for NexWave (ERPNext)"
app_email = "imesha@highflyerglobal.com"
app_license = "mit"

# Required Apps
# ------------------
required_apps = ["frappe", "erpnext"]

# Fixtures
# ------------------
fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [
			["name", "in", [
				# Item fields
				"Item-shopify_stores_section",
				"Item-shopify_stores",
				# Customer fields
				"Customer-shopify_customer_section",
				"Customer-shopify_customer_id",
				# Sales Order fields
				"Sales Order-shopify_section",
				"Sales Order-shopify_store",
				"Sales Order-shopify_order_id",
				"Sales Order-shopify_order_number",
				# Delivery Note fields
				"Delivery Note-shopify_section",
				"Delivery Note-shopify_store",
				"Delivery Note-shopify_order_id",
				"Delivery Note-shopify_order_number",
				"Delivery Note-shopify_fulfillment_id",
				# Sales Invoice fields
				"Sales Invoice-shopify_section",
				"Sales Invoice-shopify_store",
				"Sales Invoice-shopify_order_id",
				"Sales Invoice-shopify_order_number",
			]]
		]
	}
]

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Item": {
		"on_update": "nexwave_shopify_connector.nexwave_shopify.product.sync_item_to_shopify",
		"after_insert": "nexwave_shopify_connector.nexwave_shopify.product.sync_item_to_shopify",
	},
	"Item Price": {
		"on_update": "nexwave_shopify_connector.nexwave_shopify.product.sync_item_price_to_shopify",
		"after_insert": "nexwave_shopify_connector.nexwave_shopify.product.sync_item_price_to_shopify",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"nexwave_shopify_connector.nexwave_shopify.inventory.update_inventory_on_shopify"
		],
	},
}

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/nexwave_shopify_connector/css/nexwave_shopify_connector.css"
# app_include_js = "/assets/nexwave_shopify_connector/js/nexwave_shopify_connector.js"

# include js, css files in header of web template
# web_include_css = "/assets/nexwave_shopify_connector/css/nexwave_shopify_connector.css"
# web_include_js = "/assets/nexwave_shopify_connector/js/nexwave_shopify_connector.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "nexwave_shopify_connector/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Item": "public/js/item.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "nexwave_shopify_connector/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "nexwave_shopify_connector.utils.jinja_methods",
# 	"filters": "nexwave_shopify_connector.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "nexwave_shopify_connector.install.before_install"
# after_install = "nexwave_shopify_connector.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "nexwave_shopify_connector.uninstall.before_uninstall"
# after_uninstall = "nexwave_shopify_connector.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "nexwave_shopify_connector.utils.before_app_install"
# after_app_install = "nexwave_shopify_connector.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "nexwave_shopify_connector.utils.before_app_uninstall"
# after_app_uninstall = "nexwave_shopify_connector.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "nexwave_shopify_connector.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Testing
# -------

# before_tests = "nexwave_shopify_connector.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "nexwave_shopify_connector.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "nexwave_shopify_connector.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["nexwave_shopify_connector.utils.before_request"]
# after_request = ["nexwave_shopify_connector.utils.after_request"]

# Job Events
# ----------
# before_job = ["nexwave_shopify_connector.utils.before_job"]
# after_job = ["nexwave_shopify_connector.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"nexwave_shopify_connector.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

default_log_clearing_doctypes = {
	"NexWave Shopify Log": 30  # days to retain logs
}

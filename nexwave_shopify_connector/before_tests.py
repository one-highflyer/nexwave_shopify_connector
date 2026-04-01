import frappe
from frappe.utils.data import now_datetime


def before_tests():
	"""Ensure a Company exists before running tests.

	ERPNext v16 removed its own before_tests, so the setup wizard no longer
	runs automatically. This hook creates a minimal Company with a standard
	chart of accounts so that test fixtures that depend on Company, Account,
	Warehouse, etc. can be generated.
	"""
	if frappe.db.a_row_exists("Company"):
		return

	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	current_year = now_datetime().year
	setup_complete(
		{
			"currency": "USD",
			"full_name": "Test User",
			"company_name": "Wind Power LLC",
			"timezone": "America/New_York",
			"company_abbr": "WP",
			"industry": "Manufacturing",
			"country": "United States",
			"fy_start_date": f"{current_year}-01-01",
			"fy_end_date": f"{current_year}-12-31",
			"language": "english",
			"company_tagline": "Testing",
			"email": "test@erpnext.com",
			"password": "test",
			"chart_of_accounts": "Standard",
		}
	)

	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- test bootstrap: must persist Company before test records are generated

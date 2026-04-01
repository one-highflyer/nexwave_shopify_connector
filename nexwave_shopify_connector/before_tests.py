def before_tests():
	"""Bootstrap ERPNext test data before running connector tests.

	ERPNext v16 uses BootStrapTestData (in erpnext.tests.utils) to create
	test Companies, accounts, warehouses, items, etc. It runs at module
	import time. Importing it here ensures the data exists before Frappe's
	test record preloading tries to create documents that depend on them.
	"""
	import erpnext.tests.utils  # noqa: F401

import logging

import frappe


def get_logger():
	logger = frappe.logger("nexwave_shopify_connector", allow_site=True)
	logger.setLevel(logging.INFO)
	return logger

# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
RoundingAdjuster: Applies rounding adjustment after Sales Order save.

This handles small discrepancies between Shopify total and ERPNext calculated total
by adding a write-off entry to match exactly.

The adjustment is applied as an "Actual" tax row using the company's write_off_account.
"""

import frappe
from frappe import _
from frappe.utils import flt

from nexwave_shopify_connector.utils.logger import get_logger


def apply_rounding_adjustment(so, shopify_order: dict) -> float:
	"""
	Apply rounding adjustment to Sales Order after save.

	Compares the Shopify total_price with ERPNext's calculated grand_total.
	If there's a difference (> $0.01), adds an adjustment row using the
	company's write_off_account.

	Args:
	    so: Sales Order document (after insert)
	    shopify_order: Original Shopify order JSON

	Returns:
	    The adjustment amount applied (0 if no adjustment needed)
	"""
	logger = get_logger()

	shopify_total = flt(shopify_order.get("total_price"))
	erpnext_total = flt(so.grand_total)
	difference = flt(shopify_total - erpnext_total, 2)

	# Skip if difference is negligible (< 1 cent)
	tolerance = 0.01
	if abs(difference) <= tolerance:
		logger.debug(
			"No rounding adjustment needed for SO %s: Shopify=%s, ERPNext=%s",
			so.name,
			shopify_total,
			erpnext_total,
		)
		return 0.0

	logger.info(
		"Rounding adjustment needed for SO %s: Shopify=%s, ERPNext=%s, diff=%s",
		so.name,
		shopify_total,
		erpnext_total,
		difference,
	)

	# Get write-off account from company
	write_off_account = frappe.get_cached_value("Company", so.company, "write_off_account")

	if not write_off_account:
		frappe.throw(
			_(
				"Cannot sync order {0}: Rounding adjustment of {1} required but write_off_account "
				"not configured for company {2}. Please configure the Write Off Account in Company settings."
			).format(so.name, difference, so.company)
		)

	# Add adjustment row
	so.append(
		"taxes",
		{
			"charge_type": "Actual",
			"account_head": write_off_account,
			"description": "Rounding Adjustment (Shopify sync)",
			"tax_amount": difference,
			"cost_center": so.cost_center,
		},
	)

	# Save to apply the adjustment
	so.save(ignore_permissions=True)

	logger.info(
		"Applied rounding adjustment to SO %s: %s to account %s. New grand_total: %s",
		so.name,
		difference,
		write_off_account,
		so.grand_total,
	)

	return difference

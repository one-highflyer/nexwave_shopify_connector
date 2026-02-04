# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
Tax handling module for NexWave Shopify Connector.

This module provides clean architecture for building ERPNext tax rows from Shopify order data.

Components:
- TaxBuilder: Main orchestrator for building tax rows
- TaxDetector: Detects zero-rated items from Shopify data
- ShippingTaxHandler: Handles shipping charges and taxes
- RoundingAdjuster: Applies rounding adjustment after SO save

Usage:
    from nexwave_shopify_connector.nexwave_shopify.tax import TaxBuilder

    builder = TaxBuilder(order, store, items)
    taxes = builder.build()
"""

from nexwave_shopify_connector.nexwave_shopify.tax.builder import TaxBuilder
from nexwave_shopify_connector.nexwave_shopify.tax.detector import TaxDetector
from nexwave_shopify_connector.nexwave_shopify.tax.rounding import apply_rounding_adjustment
from nexwave_shopify_connector.nexwave_shopify.tax.shipping import ShippingTaxHandler

__all__ = [
	"TaxBuilder",
	"TaxDetector",
	"ShippingTaxHandler",
	"apply_rounding_adjustment",
]

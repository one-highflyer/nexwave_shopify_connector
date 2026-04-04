# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Overview

NexWave Shopify Connector is a Frappe app that provides multi-store Shopify integration for NexWave (ERPNext). It handles:
- Product sync (Item ↔ Shopify Product)
- Order import (Shopify Order → Sales Order)
- Inventory sync (Stock Ledger → Shopify Inventory)
- Fulfillment sync (Delivery Note → Shopify Fulfillment)

## Commands

### Running Tests
```bash
# Run all tests for the app
bench --site <site> run-tests --app nexwave_shopify_connector

# Run tests for a specific module
bench --site <site> run-tests --app nexwave_shopify_connector --module nexwave_shopify_connector.nexwave_shopify.tax.test_tax

# Run a specific test class
bench --site <site> run-tests --app nexwave_shopify_connector --module <module> --test TestClassName
```

### Migrations
```bash
bench --site <site> migrate
```

## Writing Tests

**IMPORTANT**: Use Frappe-compatible integration tests, NOT pytest with mocks.

This app targets Frappe v16 (`version-16` branch). Use `IntegrationTestCase`:

```python
import frappe
from frappe.tests import IntegrationTestCase

class TestMyFeature(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Setup test data here
```

### Key patterns

- **Create real documents** instead of mocks for Frappe doctypes
- **Use fixtures** for test data setup in a separate `fixtures.py` file (see `nexwave_shopify/tests/fixtures.py`)
- **Mock Shopify API calls** using `MockedRequestTestCase` or `unittest.mock.patch` for HTTP requests
- **Load realistic test data** from JSON files in `test_data/` directories

### Test Location

Place tests in the module being tested:
- `nexwave_shopify/tax/test_tax.py` for tax module tests
- `nexwave_shopify/test_order.py` for order sync tests
- `nexwave_shopify/test_fulfillment.py` for fulfillment sync tests

### What to Test

- Document creation and validation
- Tax calculation accuracy
- Shopify API response handling (with mocked HTTP responses)
- Error scenarios and edge cases

## Key Modules

- `nexwave_shopify/order.py` - Order import from Shopify
- `nexwave_shopify/tax/` - Tax calculation (On Net Total, shipping, rounding)
- `nexwave_shopify/product.py` - Product sync to Shopify
- `nexwave_shopify/inventory.py` - Inventory updates

## Commit Convention

This app uses semantic-release (see `.releaserc`). Follow Angular commit format:
- `feat(scope): description` - New feature (minor release)
- `fix(scope): description` - Bug fix (patch release)
- `refactor(scope): description` - Code refactor (patch release)

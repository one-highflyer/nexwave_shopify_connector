# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Overview

NexWave Shopify Connector is a Frappe v15 app that provides multi-store Shopify integration for NexWave (ERPNext). It handles:
- Product sync (Item ↔ Shopify Product)
- Order import (Shopify Order → Sales Order)
- Inventory sync (Stock Ledger → Shopify Inventory)
- Fulfillment sync (Delivery Note → Shopify Fulfillment)

## Branches

| Branch | Frappe | Python | Status |
|--------|--------|--------|--------|
| `version-15` (default) | >=15,<16 | >=3.10 | Active, CI green |
| `version-16` | >=16,<17 | >=3.12 | v16 compatible, test fixtures need adaptation |

## CI

- **ci.yml**: Server tests (MariaDB + Redis + ERPNext) on push to version-* branches. Skips `chore(release):` commits.
- **linter.yml**: Semgrep (Frappe rules + python.lang.correctness), pre-commit, pip-audit on PRs.
- **release.yml**: Semantic-release on push to version-15.

Semgrep findings are suppressed with `# nosemgrep` comments where intentional (webhook commits, admin context, guest webhook endpoint, file reads from app directories).

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

**IMPORTANT**: When writing tests for this app, use Frappe-compatible unit tests, NOT pytest with mocks.

### Required Pattern

1. **Use FrappeTestCase**:
   ```python
   import frappe
   from frappe.tests.utils import FrappeTestCase

   class TestMyFeature(FrappeTestCase):
       @classmethod
       def setUpClass(cls):
           super().setUpClass()
           # Setup test data here
   ```

2. **Create real documents** instead of mocks:
   ```python
   def test_order_creation(self):
       so = frappe.get_doc({
           "doctype": "Sales Order",
           "customer": "_Test Customer",
           ...
       })
       so.insert()
       self.assertEqual(so.docstatus, 0)
   ```

3. **Use fixtures** for test data setup in a separate `fixtures.py` file

4. **Clean up** created documents in tests:
   ```python
   def test_something(self):
       doc = create_test_doc()
       # ... test logic ...
       doc.cancel()  # or doc.delete()
   ```

### Test Location

Place tests in the module being tested:
- `nexwave_shopify/tax/test_tax.py` for tax module tests
- `nexwave_shopify/test_order.py` for order sync tests

### What to Test

- Document creation and validation
- Tax calculation accuracy
- Shopify API response handling
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

# NexWave Shopify Connector - Testing Guide

This guide walks through setting up a Shopify development store and configuring the connector for testing.

## Prerequisites

- A Frappe/ERPNext site (e.g., `demo.localhost`)
- The `nexwave_shopify_connector` app installed
- A Shopify Partners account (free)

---

## Part 1: Create a Shopify Development Store

### Step 1: Sign Up for Shopify Partners (Free)

1. Go to https://partners.shopify.com
2. Sign up for a free Shopify Partner account

### Step 2: Create a Development Store

1. In the Partners Dashboard, go to **Stores** → **Add store**
2. Select **Create development store**
3. Choose **Create a store to test and build**
4. Enter a store name (e.g., `nexwave-test`)
5. Click **Create development store**

Your store URL will be: `nexwave-test.myshopify.com`

### Step 3: Add Test Products

1. Go to your store admin: `https://nexwave-test.myshopify.com/admin`
2. Navigate to **Products** → **Add product**
3. Create 2-3 test products with:
   - Product title
   - SKU (important for matching with ERPNext items)
   - Price
   - Inventory quantity
4. Save each product

---

## Part 2: Create a Custom App for API Access

### Step 1: Enable Custom App Development

1. Go to your store admin: `https://nexwave-test.myshopify.com/admin`
2. Navigate to **Settings** → **Apps and sales channels**
3. Click **Develop apps** (top right)
4. Click **Allow custom app development** (if prompted)

### Step 2: Create the Custom App

1. Click **Create an app**
2. Name it `NexWave Connector`
3. Click **Create app**

### Step 3: Configure Admin API Scopes

1. Click **Configure Admin API scopes**
2. Enable the following scopes:

| Scope | Purpose |
|-------|---------|
| `read_products` | Fetch products for SKU matching |
| `write_products` | Create/update products (item sync) |
| `read_orders` | Read order data |
| `write_orders` | Update order status |
| `read_inventory` | Read stock levels |
| `write_inventory` | Push stock updates |
| `read_locations` | Fetch Shopify locations |
| `read_customers` | Read customer data |
| `write_customers` | Create customers |
| `read_fulfillments` | Read fulfillment status |
| `write_fulfillments` | Create fulfillments |

3. Click **Save**

### Step 4: Install the App and Get Credentials

1. Click **Install app**
2. Confirm installation
3. **Copy the Admin API access token** (starts with `shpat_`)

   > ⚠️ **Important**: The access token is only shown once! Copy it immediately.

4. Store this token securely - you'll need it for the NexWave configuration

### Step 5: Get the Client Secret (for Webhook Validation)

1. Go to **Shopify Partners Dashboard** → **Apps** → **NexWave Connector**
2. Click **Client credentials** or **Settings**
3. Copy the **Client Secret** (for webhook HMAC validation)

---

## Part 3: Install the Connector in NexWave

```bash
# Install the app on your site
bench --site demo.localhost install-app nexwave_shopify_connector

# Run migrations to create DocTypes and custom fields
bench --site demo.localhost migrate

# Clear cache
bench --site demo.localhost clear-cache
```

---

## Part 4: Configure the Shopify Store in NexWave

### Step 1: Open NexWave

1. Go to `http://demo.localhost:8000`
2. Login as Administrator

### Step 2: Create a Shopify Store Record

1. Search for **Shopify Store** in the awesomebar
2. Click **+ Add Shopify Store**
3. Fill in the configuration:

#### Authentication Section

| Field | Value |
|-------|-------|
| **Enabled** | ✓ Check this |
| **Shop Domain** | `nexwave-test.myshopify.com` |
| **API Version** | `2024-01` (default) |
| **Access Token** | Paste your `shpat_...` token |
| **Shared Secret** | Paste the Client Secret |

#### ERPNext Settings Section

| Field | Value |
|-------|-------|
| **Company** | Select your company |
| **Default Customer** | Create or select a customer (e.g., "Shopify Web Customer") |
| **Customer Group** | E.g., "Commercial" or "Individual" |
| **Cost Center** | Select a cost center |
| **Price List** | E.g., "Standard Selling" |
| **Item Group** | E.g., "Products" or "All Item Groups" |

#### Inventory Settings Section

| Field | Value |
|-------|-------|
| **Default Warehouse** | Select your main warehouse |

### Step 3: Save the Shopify Store

Click **Save**

---

## Part 5: Test the Connection

### Option A: Using the Bench Console

```bash
bench --site demo.localhost console
```

```python
import shopify
from nexwave_shopify_connector.nexwave_shopify.connection import shopify_session, get_shopify_store

# Get the store configuration
store = get_shopify_store("nexwave-test.myshopify.com")
print(f"Store found: {store.name}")

# Test API connection
with shopify_session(store):
    # Fetch products
    products = shopify.Product.find()
    print(f"\n=== Products ({len(products)}) ===")
    for p in products:
        print(f"  - {p.title}")
        for v in p.variants:
            print(f"      SKU: {v.sku}, Price: {v.price}")

    # Fetch locations
    locations = shopify.Location.find()
    print(f"\n=== Locations ({len(locations)}) ===")
    for loc in locations:
        print(f"  - {loc.name} (ID: {loc.id})")

    # Fetch orders
    orders = shopify.Order.find(status="any", limit=5)
    print(f"\n=== Recent Orders ({len(orders)}) ===")
    for o in orders:
        print(f"  - #{o.order_number}: {o.financial_status} - {o.total_price} {o.currency}")
```

### Option B: Quick Connection Test

```bash
bench --site demo.localhost console
```

```python
import shopify
from nexwave_shopify_connector.nexwave_shopify.connection import shopify_session, get_shopify_store

store = get_shopify_store("nexwave-test.myshopify.com")

with shopify_session(store):
    shop = shopify.Shop.current()
    print(f"✓ Connected to: {shop.name}")
    print(f"  Domain: {shop.domain}")
    print(f"  Email: {shop.email}")
    print(f"  Currency: {shop.currency}")
```

---

## Part 6: Test Item Eligibility Logic

```bash
bench --site demo.localhost console
```

```python
import frappe
from nexwave_shopify_connector.nexwave_shopify.utils import (
    is_item_eligible_for_store,
    get_eligible_stores_for_item
)

# Test with an existing item
item_code = "YOUR-ITEM-CODE"  # Replace with actual item code
store_name = "nexwave-test.myshopify.com"

# Check if item is eligible for the store
eligible = is_item_eligible_for_store(item_code, store_name)
print(f"Item '{item_code}' eligible for '{store_name}': {eligible}")

# Get all eligible stores for an item
stores = get_eligible_stores_for_item(item_code)
print(f"Eligible stores: {stores}")
```

---

## Part 7: Test Webhook HMAC Validation

```bash
bench --site demo.localhost console
```

```python
import hmac
import hashlib
import base64
import json

# Simulate webhook validation
shared_secret = "your-client-secret-here"
payload = json.dumps({"test": "data"}).encode('utf-8')

# Generate HMAC (this is what Shopify does)
computed_hmac = base64.b64encode(
    hmac.new(shared_secret.encode('utf-8'), payload, hashlib.sha256).digest()
).decode()

print(f"Computed HMAC: {computed_hmac}")

# Verify (this is what our webhook endpoint does)
def verify_hmac(payload, hmac_header, secret):
    computed = base64.b64encode(
        hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(computed, hmac_header)

is_valid = verify_hmac(payload, computed_hmac, shared_secret)
print(f"HMAC Valid: {is_valid}")
```

---

## Part 6: Test Product Sync (ERPNext → Shopify)

### Step 1: Enable Item Sync on Store

1. Open your Shopify Store record
2. In the **Item Sync Settings** section:
   - Check **Enable Item Sync**
   - Check **Update Shopify on Item Update** (for automatic sync)

### Step 2: Configure Field Mappings

1. In the **Item Field Mapping** table, add mappings:

| ERPNext Field | Shopify Field Type | Shopify Standard Field |
|---------------|-------------------|----------------------|
| `description` | Standard Field | `body_html` |
| `brand` | Standard Field | `vendor` |
| `item_group` | Standard Field | `product_type` |
| `standard_rate` | Standard Field | `price` |
| `item_code` | Standard Field | `sku` |

2. Save the store configuration

### Important: Sync Behavior

The item sync is **one-way (ERPNext → Shopify)**:

- If a Shopify product ID is already mapped to the item → **Update** existing product
- If no product ID mapping exists → **Create new product** in Shopify

When creating new products:
- SKU is automatically set to the ERPNext `item_code`
- Inventory tracking is enabled for stock items (`is_stock_item=1`)
- Title is set to `item_name`

**For existing Shopify products:** If you have products already in Shopify that you want to link to ERPNext items, use the **"Fetch Products & Map by SKU"** action on the Shopify Store form. This will match Shopify SKUs to ERPNext item codes and create the mappings.

### Step 3: Link an Item to the Store

1. Open an Item in ERPNext
2. Scroll to the **Shopify Stores** section (custom field added by connector)
3. Add a row:
   - **Shopify Store**: Select your store
   - **Enabled**: Check
4. Save the Item

### Step 4: Manual Sync

1. Go back to the Shopify Store record
2. Click **Sync** → **Sync All Items**
3. Confirm the action
4. Check the Shopify admin to see if the product was created

### Step 5: Verify in Console

```bash
bench --site demo.localhost console
```

```python
import frappe

# Check Item Shopify Store rows
rows = frappe.get_all(
    "Item Shopify Store",
    filters={"shopify_store": "nexwave-test.myshopify.com"},
    fields=["parent", "shopify_product_id", "shopify_variant_id", "last_sync_at"]
)
for row in rows:
    print(f"Item: {row.parent}")
    print(f"  Product ID: {row.shopify_product_id}")
    print(f"  Variant ID: {row.shopify_variant_id}")
    print(f"  Last Sync: {row.last_sync_at}")
```

---

## Part 7: Test Inventory Sync

### Step 1: Enable Inventory Sync

1. Open your Shopify Store record
2. In the **Inventory Settings** section:
   - Check **Enable Inventory Sync**
   - Set **Inventory Sync Frequency** (e.g., 30 minutes)

### Step 2: Configure Warehouse Mapping

1. Click **Actions** → **Fetch Shopify Locations**
2. For each location, select the corresponding ERPNext warehouse
3. Save the store

### Step 3: Add Stock in ERPNext

1. Create a Stock Entry (Material Receipt) to add stock for your test item
2. Select the warehouse mapped to your Shopify location

### Step 4: Manual Inventory Sync

1. On the Shopify Store, click **Sync** → **Sync Inventory**
2. Confirm the action
3. Check Shopify admin → Inventory to verify stock levels

### Step 5: Verify in Console

```bash
bench --site demo.localhost console
```

```python
import frappe

# Check stock in ERPNext
item_code = "YOUR-ITEM-CODE"
warehouse = "YOUR-WAREHOUSE"

qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
print(f"Stock for {item_code} in {warehouse}: {qty}")
```

---

## Part 8: Test Price Sync

Price sync is handled via the product sync field mappings.

### Step 1: Configure Price Field Mapping

1. Open your Shopify Store record
2. Ensure `price_list` is set to your selling price list
3. In **Item Field Mapping**, ensure you have:
   - ERPNext Field: `standard_rate`
   - Shopify Field Type: Standard Field
   - Shopify Standard Field: `price`

### Step 2: Set Item Price

1. Create an Item Price record for your item:
   - Item: Your test item
   - Price List: The price list configured on the store
   - Rate: e.g., 99.99

### Step 3: Sync and Verify

1. Trigger a product sync (via **Sync All Items** or by updating the item)
2. Check Shopify admin to verify the price

---

## Current Implementation Status

### ✅ Working (Ready to Test)

| Feature | Status |
|---------|--------|
| Shopify Store DocType configuration | ✅ Complete |
| API session management (`@shopify_session`) | ✅ Complete |
| Store resolution by domain | ✅ Complete |
| HMAC validation logic | ✅ Complete |
| Item eligibility filters | ✅ Complete |
| Custom fields on Item, Customer, SO, DN, SI | ✅ Complete |
| Test Connection button | ✅ Complete |
| Fetch Shopify Locations | ✅ Complete |
| **Product Sync (ERPNext → Shopify)** | ✅ Complete |
| **Inventory Sync** | ✅ Complete |
| **Price Sync (via field mapping)** | ✅ Complete |
| **Manual Sync Buttons** | ✅ Complete |
| **Scheduled Inventory Sync** | ✅ Complete |

### ❌ Not Yet Implemented

| Feature | Status |
|---------|--------|
| Order sync (Shopify → ERPNext) | ❌ Webhook handlers not created |
| Invoice creation on payment | ❌ Handler not created |
| Delivery note on fulfillment | ❌ Handler not created |
| Webhook auto-registration | ❌ TODO in code |
| Fetch Products & Map by SKU | ❌ TODO in code |

---

## Troubleshooting

### Error: "Store not found"

Make sure the shop domain in your Shopify Store record matches exactly (e.g., `nexwave-test.myshopify.com`). The connector normalizes domains automatically, but verify there are no typos.

### Error: "401 Unauthorized"

1. Verify the Access Token is correct
2. Ensure the Custom App is installed on the store
3. Check that all required API scopes are enabled

### Error: "Invalid HMAC"

1. Verify the Shared Secret matches the Client Secret from the Partners Dashboard
2. Ensure you're using the correct app's credentials

### Error: Module not found

Run migrations to ensure all DocTypes are created:

```bash
bench --site demo.localhost migrate
bench --site demo.localhost clear-cache
```

---

## Next Steps

After confirming product and inventory sync:

1. **Implement order sync** - Handle `orders/create` webhook to create Sales Orders
2. **Implement webhook registration** - Auto-register webhooks when store is enabled
3. **Implement fulfillment sync** - Create Delivery Notes from Shopify fulfillments
4. **Implement invoice sync** - Create Sales Invoices when orders are paid

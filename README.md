# NexWave Shopify Connector

Multi-store Shopify connector for [NexWave](https://nexwaveapp.com)/ERPNext, designed to connect multiple Shopify stores to a single ERPNext instance with multi-company support. Built and maintained by [HighFlyer](https://highflyerglobal.com/).

> Works with any ERPNext v15 instance. NexWave is HighFlyer's customized distribution of ERPNext for the New Zealand/Australia region, but this connector has no NexWave-specific dependencies.

### Highlights

- **Multi-store, multi-company** - Connect multiple Shopify stores to one ERPNext instance, each mapped to a separate company
- **Real-time order sync** - Webhook-based order and fulfillment ingestion with HMAC validation
- **Product and inventory push** - Sync items and stock levels from ERPNext to Shopify with multi-location support
- **Configurable tax handling** - GST/VAT, zero-rated item detection, shipping tax, and automatic rounding adjustments
- **OAuth 2.0 and legacy auth** - Supports both modern OAuth flow and legacy access tokens
- **Collection mapping** - Automatically assign products to Shopify collections based on item group or brand
- **Payment method mapping** - Map Shopify payment gateways to ERPNext Mode of Payment
- **SKU-based migration tool** - Link existing Shopify products to ERPNext items by matching SKUs

## Overview

![Multi-Store Architecture](docs/multi-store-architecture.svg)

<details>
<summary>ASCII diagram (fallback)</summary>

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           NexWave (ERPNext)                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Company A│  │ Company B│  │ Company C│  │ Company D│  │ Company E│      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       │             │             │             │             │             │
│       └─────────────┴──────┬──────┴─────────────┴─────────────┘             │
│                            │                                                │
│                   ┌────────▼────────┐                                       │
│                   │  Shopify Store  │  (Multi-Store Configuration)          │
│                   │    DocType      │                                       │
│                   └────────┬────────┘                                       │
└────────────────────────────┼────────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  store-au.      │ │  store-nz.      │ │  store-us.      │
│  myshopify.com  │ │  myshopify.com  │ │  myshopify.com  │
│  (Company A)    │ │  (Company B)    │ │  (Company C)    │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

</details>

## Data Flow

![Data Flow](docs/data-flow.svg)

## Key Features

### Multi-Store Architecture
- Connect **multiple Shopify stores** to a single ERPNext instance
- Each store maps to a specific **Company** for proper accounting separation
- Store-scoped API sessions via `@shopify_session` decorator

### Item Sync (Push Only: NexWave → Shopify)
```
┌──────────────────────────────────────────────────────────────────┐
│                         ERPNext Item                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ description │  │ brand       │  │ custom_color│              │
│  │ item_group  │  │ weight      │  │ custom_rrp  │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
└─────────┼────────────────┼────────────────┼─────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│              Shopify Store Item Field Mapping                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Standard Field  │  │ Standard Field  │  │   Metafield     │  │
│  │ body_html       │  │ vendor          │  │ custom.color    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

- **Simple field mapping** (no JSONPath complexity)
- Map ERPNext fields to Shopify **standard fields** or **metafields**
- Per-store mapping configuration

### Item Sync Logic
```
┌─────────────────────────────────────────────────────────────────┐
│                    Item Sync Decision Flow                      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            Item eligible for Shopify Store?             │   │
│  └──────────────────────────┬──────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │         Has shopify_product_id mapping?                 │   │
│  └──────────────────────────┬──────────────────────────────┘   │
│                             │                                   │
│              ┌──────────────┴──────────────┐                   │
│              │                             │                   │
│              ▼ YES                         ▼ NO                │
│  ┌─────────────────────┐       ┌─────────────────────┐        │
│  │  UPDATE existing    │       │  CREATE new         │        │
│  │  Shopify product    │       │  Shopify product    │        │
│  │                     │       │                     │        │
│  │  Uses stored        │       │  Sets:              │        │
│  │  product_id &       │       │  - SKU = item_code  │        │
│  │  variant_id         │       │  - Track inventory  │        │
│  │                     │       │    (if stock item)  │        │
│  └─────────────────────┘       └─────────────────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

**Key Points:**
- Sync is **one-way** (ERPNext → Shopify)
- If no product ID mapping exists, a **new product is created** in Shopify
- SKU is automatically set to the ERPNext `item_code`
- Inventory tracking is enabled for stock items (`is_stock_item=1`)

**For existing Shopify products:** Use the "Fetch Products & Map by SKU" action on the Shopify Store form to link existing Shopify products to ERPNext items by matching SKU = item_code.

### Item Eligibility & Filters
```
┌─────────────────────────────────────────────────────────────────┐
│                    Item Eligibility Logic                       │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Option 1: Manual Override                               │   │
│  │ Item Shopify Store table → enabled = Yes/No             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            OR                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Option 2: Auto-Eligibility Filters                      │   │
│  │ IF custom_au_rrp_incl_gst HAS VALUE → Sync to AU store  │   │
│  │ IF custom_nz_rrp_incl_gst HAS VALUE → Sync to NZ store  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

- **Manual control**: Enable/disable items per store via `Item Shopify Store` table
- **Automatic eligibility**: Define filter rules (e.g., "if field X has value, sync to store Y")
- Filter types: `Field Has Value`, `Field Equals`, `Field Not Empty`

### Collection Mapping
```
┌─────────────────────────────────────────────────────────────────┐
│              Collection Mapping (ERPNext → Shopify)             │
│                                                                 │
│  ┌──────────────────┐         ┌──────────────────────────────┐ │
│  │ item_group =     │ ──────► │ Shopify Collection:          │ │
│  │ "Outdoor"        │         │ "Outdoor Living"             │ │
│  └──────────────────┘         └──────────────────────────────┘ │
│                                                                 │
│  ┌──────────────────┐         ┌──────────────────────────────┐ │
│  │ brand =          │ ──────► │ Shopify Collection:          │ │
│  │ "Artisan"        │         │ "Artisan Collection"         │ │
│  └──────────────────┘         └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

- Map ERPNext field values to Shopify Custom Collections
- Automatic collection assignment during item sync

### Order Sync (Pull: Shopify → NexWave)
```
┌─────────────────┐     Webhook      ┌─────────────────────────────┐
│ Shopify Store   │ ───────────────► │ NexWave Webhook Endpoint    │
│ Order Created   │  X-Shopify-      │                             │
│                 │  Shop-Domain     │ 1. Resolve store by domain  │
└─────────────────┘                  │ 2. Validate HMAC            │
                                     │ 3. Route to correct Company │
                                     │ 4. Create Sales Order       │
                                     └─────────────────────────────┘
                                                   │
                                                   ▼
                                     ┌─────────────────────────────┐
                                     │ Sales Order                 │
                                     │ - shopify_store: AU Store   │
                                     │ - company: Company A        │
                                     │ - shopify_order_id: 12345   │
                                     └─────────────────────────────┘
```

- **Webhook-based** order sync with HMAC validation
- Store resolution via `X-Shopify-Shop-Domain` header
- Orders routed to correct Company based on store configuration
- **Global customers** (email-based lookup across stores)

**Order Automation:**
- Auto-submit Sales Order for paid orders
- Auto-create Sales Invoice (requires auto-submit)
- Auto-create Payment Entry (requires auto-create invoice)
- Configurable Cash/Bank account for payment entries

**Fulfillment Sync (Shopify → NexWave):**
```
┌─────────────────┐     Webhook      ┌─────────────────────────────┐
│ Shopify         │ ───────────────► │ NexWave Webhook Endpoint    │
│ Fulfillment     │  orders/fulfilled│                             │
│ Created         │                  │ 1. Find linked Sales Order  │
└─────────────────┘                  │ 2. Check for existing DNs   │
                                     │ 3. Create Delivery Note     │
                                     │ 4. Add tracking info        │
                                     └─────────────────────────────┘
```

- **Webhook-based**: Listens to `orders/fulfilled` and `orders/partially_fulfilled` events
- **Duplicate prevention**: Checks for existing Delivery Notes before creating new ones
- **Partial fulfillment**: Option to auto-create DNs for remaining quantities
- **Tracking info**: Captures tracking number and company from Shopify fulfillment

**Tax Handling:**
```
┌─────────────────────────────────────────────────────────────────┐
│                    Tax Calculation Flow                         │
│                                                                 │
│  Shopify Order (taxes_included: true)                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Line Items with tax_lines[] + Shipping with tax_lines[] │   │
│  └──────────────────────────┬──────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ TaxBuilder: "On Net Total" approach                     │   │
│  │ - Single GST row at order level (not per-item)          │   │
│  │ - Zero-rated items get item_tax_template override       │   │
│  │ - Detects tax rate from order (15% NZ, 10% AU)          │   │
│  └──────────────────────────┬──────────────────────────────┘   │
│                             │                                   │
│                             ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Sales Order Taxes Table:                                │   │
│  │ 1. GST @ 15% (On Net Total)                             │   │
│  │ 2. Shipping (Actual) - if not added as item             │   │
│  │ 3. GST on Shipping (On Previous Row Amount)             │   │
│  │ 4. Rounding Adjustment (if needed)                      │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

- **"On Net Total" approach**: Single GST tax row instead of per-item tax rows (cleaner, handles large orders efficiently)
- **Zero-rated items**: Automatically detected (taxable=false or empty tax_lines) and assigned zero-rate item_tax_template
- **Multi-region support**: Handles different tax rates (NZ 15% GST, AU 10% GST) based on order data
- **Shipping options**: Add as line item (taxed via On Net Total) or as tax row with separate GST on shipping
- **Rounding adjustments**: Automatic write-off for small discrepancies between Shopify and ERPNext totals
- **Store-level write-off account**: Configure per-store or fall back to company's write_off_account

**Customer & Address Handling:**
- Customer lookup by `shopify_customer_id` first, then by email
- Addresses deduplicated by content (title, address_line1, city, country)
- Both billing and shipping addresses set on Sales Order

### Inventory Sync (Push: NexWave → Shopify)
```
┌─────────────────────────────────────────────────────────────────┐
│                    Warehouse → Location Mapping                 │
│                                                                 │
│  ERPNext                              Shopify                   │
│  ┌─────────────────┐                  ┌─────────────────┐      │
│  │ AU Warehouse    │ ───────────────► │ AU Location     │      │
│  │ (qty: 50)       │                  │ (inventory: 50) │      │
│  └─────────────────┘                  └─────────────────┘      │
│                                                                 │
│  ┌─────────────────┐                  ┌─────────────────┐      │
│  │ NZ Warehouse    │ ───────────────► │ NZ Location     │      │
│  │ (qty: 30)       │                  │ (inventory: 30) │      │
│  └─────────────────┘                  └─────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

- Push stock levels from ERPNext warehouses to Shopify locations
- Configurable sync frequency per store
- Multi-location support

### SKU-Based Pre-Mapping (Migration Tool)
```
┌─────────────────────────────────────────────────────────────────┐
│                    SKU Auto-Mapping                             │
│                                                                 │
│  Shopify Products          ERPNext Items                        │
│  ┌───────────────┐         ┌───────────────┐                   │
│  │ SKU: ABC-001  │ ◄─────► │ item_code:    │  ✓ Matched        │
│  │ product_id: X │         │ ABC-001       │                   │
│  └───────────────┘         └───────────────┘                   │
│                                                                 │
│  ┌───────────────┐         ┌───────────────┐                   │
│  │ SKU: XYZ-999  │    ?    │ (not found)   │  ✗ Unmatched      │
│  └───────────────┘         └───────────────┘                   │
└─────────────────────────────────────────────────────────────────┘
```

- Fetch existing Shopify products and match by SKU
- Auto-create `Item Shopify Store` mappings for matched items
- Migration report: matched, unmatched, conflicts

## DocTypes

| DocType | Description |
|---------|-------------|
| **Shopify Store** | Main configuration - auth, company, tax accounts, write-off account, sync settings |
| Shopify Store Warehouse Mapping | Maps Shopify locations ↔ ERPNext warehouses |
| Shopify Store Item Field | Field mapping (standard fields & metafields) |
| Shopify Store Collection Mapping | ERPNext values → Shopify collections |
| Shopify Store Item Filter | Auto-eligibility rules |
| Shopify Store Tax Account | Tax mapping (Shopify tax → ERPNext account + item tax templates) |
| Shopify Store Payment Method Mapping | Maps Shopify payment gateways → ERPNext Mode of Payment |
| Shopify Store Webhook | Registered webhook tracking |
| Item Shopify Store | Per-item, per-store mapping (child of Item) |

## Custom Fields Added

| DocType | Fields |
|---------|--------|
| **Item** | `shopify_stores` (table) |
| **Customer** | `shopify_customer_id` |
| **Sales Order** | `shopify_store`, `shopify_order_id`, `shopify_order_number`, `shopify_financial_status`, `shopify_fulfillment_status` |
| **Sales Order Item** | `shopify_item_discount` |
| **Delivery Note** | `shopify_store`, `shopify_order_id`, `shopify_order_number` |
| **Sales Invoice** | `shopify_store`, `shopify_order_id`, `shopify_order_number` |

## Permissions

| Role | Access |
|------|--------|
| **Sales Manager** | Full access (create, edit, delete, import/export) |
| **Sales User** | Read-only + reports |
| **Accounts Manager** | Read-only + reports |
| **Accounts User** | Read-only + reports |
| **System Manager** | Full administrative access |

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site [sitename] install-app nexwave_shopify_connector
bench --site [sitename] migrate
```

## Authentication

The connector supports two authentication methods:

### Legacy (Access Token)
Manual access token entry - suitable for existing custom apps created before January 2026.

> **Note:** Shopify deprecated legacy custom apps from 1 January 2026. New integrations should use OAuth.

1. Create a Custom App in Shopify Admin → Settings → Apps and sales channels → Develop apps
2. Configure required API scopes
3. Install the app and copy the Admin API access token
4. Enter the token in the Shopify Store's `Access Token` field

### OAuth 2.0 (Recommended)
OAuth flow for Shopify Dev Dashboard apps - required for new apps created after January 2026.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OAuth 2.0 Flow                                      │
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   NexWave   │    │   Shopify   │    │   NexWave   │    │  Shopify    │  │
│  │   Store     │───►│   Auth      │───►│   Callback  │───►│   Store     │  │
│  │   Form      │    │   Page      │    │   Endpoint  │    │   (token)   │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│        │                                      │                             │
│        │         "Connect to Shopify"         │   Token stored directly    │
│        └──────────────────────────────────────┘   on Shopify Store doc     │
└─────────────────────────────────────────────────────────────────────────────┘
```

The OAuth flow is self-contained within the Shopify Store document - no separate Connected App configuration needed.

#### OAuth Setup Steps

**Step 1: Create Shopify App (Dev Dashboard)**
1. Go to Shopify Admin → Settings → Develop Apps → "Develop apps in Dev Dashboard"
2. This opens [Shopify Dev Dashboard](https://dev.shopify.com/) - click **Apps** → **Create app**
3. Create a new version and add your redirect URI:
   ```
   https://{your-site}/api/method/nexwave_shopify_connector.nexwave_shopify.oauth.callback
   ```
4. Go to **Client credentials** and note the Client ID and Client Secret

**Step 2: Configure Shopify Store in NexWave**
1. Create/edit Shopify Store document
2. Set `Auth Method` = "OAuth"
3. Enter the **Client ID** and **Client Secret** from Shopify
4. Copy the **Callback URL** shown on the form to your Shopify app's redirect URIs
5. Click **Actions → Connect to Shopify**
6. Authorise on Shopify when redirected
7. Verify status shows "Connected"

For detailed setup instructions with screenshots, see the [Shopify Connector Setup Guide](https://docs.nexwaveapp.com/doc/hpVraqmSVk).

All required scopes are requested automatically during the OAuth flow:
- `read_orders`, `write_orders` - Order sync
- `read_customers`, `write_customers` - Customer handling
- `read_products`, `write_products` - Product sync
- `read_inventory`, `write_inventory` - Inventory sync
- `read_locations` - Warehouse/location mapping
- `read_fulfillments`, `write_fulfillments` - Delivery sync

## Configuration

1. Navigate to **Shopify Store** DocType
2. Create a new store with:
   - Shop domain (e.g., `mystore.myshopify.com`)
   - Authentication method (Legacy or OAuth)
   - Company mapping
   - Warehouse and location mappings
3. Configure tax settings:
   - Default Sales Tax Account (for GST/VAT)
   - Default Shipping Charges Account
   - Write Off Account (for rounding adjustments, falls back to Company's if not set)
   - Tax account mappings for specific Shopify tax titles
4. Configure field mappings, collection mappings, and filters as needed
5. Enable the store to start syncing

## Architecture

```
nexwave_shopify_connector/
├── nexwave_shopify/
│   ├── connection.py      # @shopify_session decorator, webhook endpoint
│   ├── oauth.py           # OAuth authorize & callback endpoints
│   ├── order.py           # Order sync logic (webhooks & manual sync)
│   ├── fulfillment.py     # Fulfillment webhook → Delivery Note creation
│   ├── product.py         # Product/item sync to Shopify
│   ├── inventory.py       # Inventory sync to Shopify
│   ├── utils.py           # Logging, eligibility helpers
│   ├── tax/               # Tax calculation module
│   │   ├── __init__.py    # Public exports
│   │   ├── builder.py     # TaxBuilder: orchestrates tax row creation
│   │   ├── detector.py    # TaxDetector: identifies zero-rated items
│   │   ├── shipping.py    # ShippingTaxHandler: shipping charges & GST
│   │   └── rounding.py    # Rounding adjustment for total matching
│   └── doctype/
│       ├── shopify_store/
│       ├── shopify_store_warehouse_mapping/
│       ├── shopify_store_item_field/
│       ├── shopify_store_collection_mapping/
│       ├── shopify_store_item_filter/
│       ├── shopify_store_tax_account/
│       ├── shopify_store_webhook/
│       ├── shopify_store_payment_method_mapping/
│       └── item_shopify_store/
└── fixtures/
    └── custom_field.json  # Custom fields for Item, Customer, SO, DN, SI
```

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/nexwave_shopify_connector
pre-commit install
```

Tools used: ruff, eslint, prettier, pyupgrade

## Documentation

- [Shopify Integration Overview](https://docs.nexwaveapp.com/s/docs/doc/shopify-integration-L1ofBu1m6e) - Feature overview, architecture, and data flow
- [Shopify Connector Setup Guide](https://docs.nexwaveapp.com/s/docs/doc/shopify-connector-setup-guide-hpVraqmSVk) - Step-by-step setup instructions with screenshots

## License

GNU General Public License v3.0 - see [license.txt](license.txt)

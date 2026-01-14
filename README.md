# NexWave Shopify Connector

Multi-store Shopify connector for NexWave (ERPNext) - designed to connect multiple Shopify stores to a single ERPNext instance with multi-company support.

## Overview

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

**Tax Handling:**
- Map Shopify tax titles (e.g., "GST", "VAT") to ERPNext tax accounts
- Shipping charges can be added as line item or tax entry
- Tax rates preserved for GST reporting

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
| **Shopify Store** | Main configuration - auth, company, sync settings |
| Shopify Store Warehouse Mapping | Maps Shopify locations ↔ ERPNext warehouses |
| Shopify Store Item Field | Field mapping (standard fields & metafields) |
| Shopify Store Collection Mapping | ERPNext values → Shopify collections |
| Shopify Store Item Filter | Auto-eligibility rules |
| Shopify Store Tax Account | Tax mapping configuration |
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
Manual access token entry - suitable for custom apps created before January 2025.

1. Create a Custom App in Shopify Admin → Settings → Apps and sales channels → Develop apps
2. Configure required API scopes
3. Install the app and copy the Admin API access token
4. Enter the token in the Shopify Store's `Access Token` field

### OAuth 2.0 (Recommended)
OAuth flow for Shopify Dev Dashboard apps - required for apps created after January 2025.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OAuth 2.0 Flow                                      │
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   NexWave   │    │   Shopify   │    │   Token     │    │   Token     │  │
│  │   Store     │───►│   Auth      │───►│   Proxy     │───►│   Cache     │  │
│  │   Form      │    │   Page      │    │   Endpoint  │    │   (Frappe)  │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│        │                                      │                             │
│        │         "Connect to Shopify"         │   Adds token_type &        │
│        └──────────────────────────────────────┘   expires_in fields        │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Why a Proxy Endpoint?

Shopify's OAuth token response doesn't include fields that Frappe's Token Cache requires:
- `token_type` - Frappe requires "Bearer" or "MAC" (Shopify omits this)
- `expires_in` - Frappe uses this to check token expiration (Shopify offline tokens don't include this)

Our proxy endpoint (`oauth.exchange_token`) intercepts the token exchange, calls Shopify's token endpoint, and adds the missing fields before returning to Frappe.

#### OAuth Setup Steps

**Step 1: Create Shopify App (Dev Dashboard)**
1. Go to [Shopify Partners](https://partners.shopify.com) → Apps → Create app
2. Under "Configuration", add your redirect URI:
   ```
   https://{your-site}/api/method/frappe.integrations.doctype.connected_app.connected_app.callback/{connected-app-name}
   ```
3. Configure required scopes (see table below)
4. Note the Client ID and Client Secret

**Step 2: Create Connected App in NexWave**

| Field | Value |
|-------|-------|
| Provider Name | Shopify - {store_name} |
| Client ID | From Shopify Dev Dashboard |
| Client Secret | From Shopify Dev Dashboard |
| Authorization URI | `https://{shop}.myshopify.com/admin/oauth/authorize` |
| **Token URI** | `https://{your-site}/api/method/nexwave_shopify_connector.nexwave_shopify.oauth.exchange_token` |
| Scopes | Add each required scope as a row |

> **Important**: The Token URI must point to our proxy endpoint, NOT Shopify's direct token URL.

**Step 3: Configure Shopify Store**
1. Create/edit Shopify Store document
2. Set `Auth Method` = "OAuth"
3. Select the Connected App created above
4. Click **Actions → Connect to Shopify**
5. Authorize on Shopify when redirected
6. Verify status shows "Connected"

#### Required OAuth Scopes

| Scope | Required For |
|-------|--------------|
| `read_orders` | Order sync (Shopify → NexWave) |
| `read_customers` | Customer creation from orders |
| `read_products` | Product mapping, SKU matching |
| `write_products` | Product sync (NexWave → Shopify) |
| `read_inventory` | Inventory sync |
| `write_inventory` | Inventory sync |
| `read_locations` | Warehouse/location mapping |
| `read_fulfillments` | Delivery note sync |

## Configuration

1. Navigate to **Shopify Store** DocType
2. Create a new store with:
   - Shop domain (e.g., `mystore.myshopify.com`)
   - Authentication method (Legacy or OAuth)
   - Company mapping
   - Warehouse and location mappings
3. Configure field mappings, collection mappings, and filters as needed
4. Enable the store to start syncing

## Architecture

```
nexwave_shopify_connector/
├── nexwave_shopify/
│   ├── connection.py      # @shopify_session decorator, webhook endpoint
│   ├── oauth.py           # OAuth token proxy & callback endpoints
│   ├── order.py           # Order sync logic (webhooks & manual sync)
│   ├── product.py         # Product/item sync to Shopify
│   ├── utils.py           # Logging, eligibility helpers
│   └── doctype/
│       ├── shopify_store/
│       ├── shopify_store_warehouse_mapping/
│       ├── shopify_store_item_field/
│       ├── shopify_store_collection_mapping/
│       ├── shopify_store_item_filter/
│       ├── shopify_store_tax_account/
│       ├── shopify_store_webhook/
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

## License

MIT

## [1.5.3](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.5.2...v1.5.3) (2026-01-22)


### Bug Fixes

* **webhooks:** use client_secret for OAuth webhook HMAC validation ([fb356f3](https://github.com/one-highflyer/nexwave_shopify_connector/commit/fb356f3a7441ff403de473ba70c05b250b890782))

## [1.5.2](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.5.1...v1.5.2) (2026-01-22)


### Bug Fixes

* **patch:** simplify to only drop old unique index ([cebbd86](https://github.com/one-highflyer/nexwave_shopify_connector/commit/cebbd861731ba243f72efc256dfc9b492b939ffb))

## [1.5.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.5.0...v1.5.1) (2026-01-22)


### Bug Fixes

* **patch:** check if columns exist before creating index ([7036339](https://github.com/one-highflyer/nexwave_shopify_connector/commit/70363393f13c5b53ad965c3550508d5180eceb8b))

# [1.5.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.4.0...v1.5.0) (2026-01-22)


### Bug Fixes

* **fulfillment:** scope fulfillment ID uniqueness per store ([652cdc3](https://github.com/one-highflyer/nexwave_shopify_connector/commit/652cdc3a100de7e91a83a3bbd1c261b29e3c8c01))


### Features

* **fulfillment:** add fulfillment sync to create Delivery Notes from Shopify webhooks ([74d4a08](https://github.com/one-highflyer/nexwave_shopify_connector/commit/74d4a08051e7132100cb9e3099cfe173a471ffa3))
* **webhooks:** add configurable webhook event processing ([b7b8aa3](https://github.com/one-highflyer/nexwave_shopify_connector/commit/b7b8aa38289cc319bc7672cc991d7d6fa491742a))

# [1.4.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.3.1...v1.4.0) (2026-01-21)


### Features

* **order:** add cost center to Sales Order and Sales Invoice ([03dc4c3](https://github.com/one-highflyer/nexwave_shopify_connector/commit/03dc4c3dca989905d401910b09aa7941247ed0b3))

## [1.3.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.3.0...v1.3.1) (2026-01-20)


### Bug Fixes

* **store:** rename ERPNext to NexWave in user-facing message ([2a20532](https://github.com/one-highflyer/nexwave_shopify_connector/commit/2a20532bf961a99b25380ebac29c8d98e88a3f35))

# [1.3.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.2.0...v1.3.0) (2026-01-19)


### Bug Fixes

* **order:** skip payment entry for split payments without transaction data ([c6641c0](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c6641c08e9aab390745a93445256e25b6c93ad79))
* **store:** populate naming series dropdowns and add account filter for payment mapping ([ac29791](https://github.com/one-highflyer/nexwave_shopify_connector/commit/ac29791bc4acb3008ca700357512aab7fcc9346f))


### Features

* **order:** add payment method mapping for automatic payment entries ([544e0c7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/544e0c7a25b6c51c7f78a5c7a297a05adde507a2))

# [1.2.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.1.0...v1.2.0) (2026-01-18)


### Bug Fixes

* **order-sync:** skip cancelled Shopify orders during sync ([b38af5d](https://github.com/one-highflyer/nexwave_shopify_connector/commit/b38af5d937768232b0228b4d38830720e66c5da3))


### Features

* **order-sync:** add toggle to sync all order statuses ([2298837](https://github.com/one-highflyer/nexwave_shopify_connector/commit/2298837a00a8c4bc8a2fec09f8139bbbebbc9153))

# [1.1.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.0.1...v1.1.0) (2026-01-18)


### Features

* **order-sync:** improve logging for easier debugging ([29d1256](https://github.com/one-highflyer/nexwave_shopify_connector/commit/29d12565f1ab733cc9821685b922241dc139250d))

## [1.0.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.0.0...v1.0.1) (2026-01-15)


### Bug Fixes

* add permission check before updating store OAuth status ([f8bc3c7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/f8bc3c70db4190420e86f3f2f6c074dc4b4cee7e))
* address PR review comments for OAuth implementation ([3e01ea2](https://github.com/one-highflyer/nexwave_shopify_connector/commit/3e01ea2e4a636f2e87c0e0a614fbbc955063b58a))
* OAuth token exchange compatibility with Frappe Token Cache ([8f7a071](https://github.com/one-highflyer/nexwave_shopify_connector/commit/8f7a071f1e656902786270360d4d2c99c211d1dc))

# 1.0.0 (2026-01-12)


### Bug Fixes

* Fix Shopify product creation and inventory sync bugs ([12ad210](https://github.com/one-highflyer/nexwave_shopify_connector/commit/12ad210a5c2f680da5a45c5b0439f24a01288610))
* Improve customer data extraction in order sync ([ec04393](https://github.com/one-highflyer/nexwave_shopify_connector/commit/ec04393fce2d9417b42d856820c5b8df78518f49))
* Improve inventory sync logging and rename ERPNext labels to NexWave ([94741e7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/94741e798b63455b5f15e2653a0256d938f2f51d))


### Features

* Add auto_create_collections checkbox to control collection auto-creation ([489a444](https://github.com/one-highflyer/nexwave_shopify_connector/commit/489a44499b8566f9c339c97a431b1908e7b3e6df))
* Add category and collections mapping to Shopify product sync ([ab34558](https://github.com/one-highflyer/nexwave_shopify_connector/commit/ab34558c6089a5d604b989cb8fe7e24ca83bd10b))
* Add Item Price sync trigger and image sync to Shopify ([827c436](https://github.com/one-highflyer/nexwave_shopify_connector/commit/827c436f3a3f66cf059424d8e19d46e175582cf2))
* Add manual Sync to Shopify button on Item form ([8cd9bdf](https://github.com/one-highflyer/nexwave_shopify_connector/commit/8cd9bdf70273e89a9d76ab7878b6e4061db0bc4c))
* Add Phase 1 core DocTypes and infrastructure ([c7fdf91](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c7fdf91a29d76128692ad60d228145b8a9506ade))
* Add product sync, inventory sync, and logging infrastructure ([3dea15e](https://github.com/one-highflyer/nexwave_shopify_connector/commit/3dea15ed522bb2b32091d72e34d326d913165443))
* add semantic release configuration ([3106050](https://github.com/one-highflyer/nexwave_shopify_connector/commit/31060502b65ce2961bc4c463a2cc0b4d4515e8dc))
* Add Shopify order sync with webhook handlers and manual sync ([15cf511](https://github.com/one-highflyer/nexwave_shopify_connector/commit/15cf5118d982be96ce0fd53df86cbd155ff1be98))
* Implement test connection and fetch locations functionality ([761aa74](https://github.com/one-highflyer/nexwave_shopify_connector/commit/761aa74543a76895b633350517589e8ada0b2193))
* Initialize App ([dde7a51](https://github.com/one-highflyer/nexwave_shopify_connector/commit/dde7a51aad008bd597f5381fcff56959b65ef44a))

# Changelog

All notable changes to this project will be documented in this file.

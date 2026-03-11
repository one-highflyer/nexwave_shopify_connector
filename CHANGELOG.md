## [1.11.3](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.2...v1.11.3) (2026-03-11)


### Bug Fixes

* **inventory:** increase sync job timeout to 1 hour ([2f01a48](https://github.com/one-highflyer/nexwave_shopify_connector/commit/2f01a483e90ced002401c09830ad043dfbc5997c))

## [1.11.2](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.1...v1.11.2) (2026-03-11)


### Bug Fixes

* **inventory:** prevent duplicate sync jobs when store has no prior sync ([50d9761](https://github.com/one-highflyer/nexwave_shopify_connector/commit/50d97614c6d4d44e9724d214a351a414fcc93c0c))

## [1.11.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.0...v1.11.1) (2026-03-10)


### Bug Fixes

* **inventory:** add rate limiting and skip untracked items during sync ([cd0a6e1](https://github.com/one-highflyer/nexwave_shopify_connector/commit/cd0a6e13b15476f1e1cbf563494cd2dbabe7e70c))

# [1.11.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.10.0...v1.11.0) (2026-02-23)


### Bug Fixes

* **order:** use customer's default price list on Sales Order creation ([62c1a8e](https://github.com/one-highflyer/nexwave_shopify_connector/commit/62c1a8ed018a7dfdf1af3327dc11319c0514ef63))
* **store:** commit each successful SKU mapping to prevent rollback loss ([cc74878](https://github.com/one-highflyer/nexwave_shopify_connector/commit/cc748780e6965af27ce1e46038502db1a53740e2))


### Features

* **store:** implement "Fetch Products & Map by SKU" for Shopify Store ([c6a368a](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c6a368ae2be38d8cace508b792226f7c1eb5b40d))

# [1.10.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.9.5...v1.10.0) (2026-02-21)


### Bug Fixes

* **order:** lower rounding tolerance so 0.01 discrepancies get written off ([6a2298a](https://github.com/one-highflyer/nexwave_shopify_connector/commit/6a2298a5f936ab86fecbb28c46bf7aed278f0cc7))


### Features

* **order:** store Shopify order payload in log on SO creation ([87594b8](https://github.com/one-highflyer/nexwave_shopify_connector/commit/87594b856c78b529548f275481e3bdbdf2ff62a1))

## [1.9.5](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.9.4...v1.9.5) (2026-02-16)


### Bug Fixes

* **order:** break long transactions into committed phases to prevent deadlocks ([c49e05d](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c49e05d64609eaa32b6bf333bf60c1d0fe03cfaf))
* **order:** distinguish Phase 6 skip vs failure and refresh stale docstatus ([9aad522](https://github.com/one-highflyer/nexwave_shopify_connector/commit/9aad522517852020d8e476a23bb1a2ed8486369c))

## [1.9.4](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.9.3...v1.9.4) (2026-02-13)


### Bug Fixes

* **order:** validate default customer before creating Sales Order for guest/POS orders ([206e6ee](https://github.com/one-highflyer/nexwave_shopify_connector/commit/206e6eee5ab86f65ee725e2774417313b621b402))

## [1.9.3](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.9.2...v1.9.3) (2026-02-12)


### Bug Fixes

* **order:** use customer display name for address title fallback ([91f747b](https://github.com/one-highflyer/nexwave_shopify_connector/commit/91f747b96ab7b6b78cfaeff8f618aadfdea07ea0))

## [1.9.2](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.9.1...v1.9.2) (2026-02-09)


### Bug Fixes

* **order:** use store's price list when creating Sales Order ([8e35a8e](https://github.com/one-highflyer/nexwave_shopify_connector/commit/8e35a8e0ff7cdb469592614c0afe8aaa255eabf5))

## [1.9.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.9.0...v1.9.1) (2026-02-05)


### Bug Fixes

* **order:** address PR review feedback for phone sanitization ([1113c23](https://github.com/one-highflyer/nexwave_shopify_connector/commit/1113c238a0b78b380940c39b45b5d657a514fbf3))
* **order:** sanitize Shopify phone numbers before Frappe validation ([7f746ee](https://github.com/one-highflyer/nexwave_shopify_connector/commit/7f746eee2bc4650bdd3d584692326cd7a0134c35))
* **order:** strip extension markers and trailing digits from phone numbers ([25775f7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/25775f7872a169c7aa3bc702ef1a009a32ac5460))

# [1.9.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.8.1...v1.9.0) (2026-02-05)


### Bug Fixes

* **fulfillment:** prevent duplicate Delivery Notes on fulfillment webhooks ([787f2f7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/787f2f7819c8d694eba4c63240cee44fa77f8210))
* **tax:** collect shipping tax types when add_shipping_as_item is True ([1e1f525](https://github.com/one-highflyer/nexwave_shopify_connector/commit/1e1f525fbf3d7ad204ee8f3649926212e1d2c94b))
* **tax:** correct row_id calculation for multiple shipping lines ([26de31b](https://github.com/one-highflyer/nexwave_shopify_connector/commit/26de31be86e2c25085579089ffbf38d43f36e6ad))
* **tax:** improve logging and add test data for tax module ([81a2048](https://github.com/one-highflyer/nexwave_shopify_connector/commit/81a204866e0ec3eb7af49297d07c4bd46976fd2a))
* **tax:** skip adding tax row for free shipping ([e0896bd](https://github.com/one-highflyer/nexwave_shopify_connector/commit/e0896bde1c311ebf4dc77b9d588c56099076e664))
* **test:** clean up Item Tax Templates in tearDownClass ([c5ff25b](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c5ff25b63dbcd0f34b067a0b9e170f1469b6f023))


### Features

* **tax:** add Item Tax Template support for GST/BAS reporting ([3c8be31](https://github.com/one-highflyer/nexwave_shopify_connector/commit/3c8be31762f08e80be6cd0aa32c1504ec2e884c3))
* **tax:** add store-level write-off account for rounding adjustments ([7ef0ee1](https://github.com/one-highflyer/nexwave_shopify_connector/commit/7ef0ee1eeaf004e66a75ccadc63183ecd60d902e))

## [1.8.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.8.0...v1.8.1) (2026-01-29)


### Bug Fixes

* **webhooks:** skip webhook processing for disabled stores ([d88fdd4](https://github.com/one-highflyer/nexwave_shopify_connector/commit/d88fdd4c04a639e683ab1e7a73faadd9d2ae8b06))

# [1.8.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.7.0...v1.8.0) (2026-01-29)


### Features

* **hooks:** exclude Shopify Store from company deletion cascade ([9ab0272](https://github.com/one-highflyer/nexwave_shopify_connector/commit/9ab0272742f5171b11188373437ad40b18acb325))

# [1.7.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.6.2...v1.7.0) (2026-01-27)


### Features

* **order:** add shopify_customer_note field for customer delivery notes ([e41b4ba](https://github.com/one-highflyer/nexwave_shopify_connector/commit/e41b4ba15bd126c5862ff5c180f9566850cf5a01))
* **order:** auto-create delivery notes for pre-fulfilled orders ([eae60a8](https://github.com/one-highflyer/nexwave_shopify_connector/commit/eae60a80e2a05a8a43073a97086d0fb36ea7a753))

## [1.6.2](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.6.1...v1.6.2) (2026-01-24)


### Bug Fixes

* **order:** correct rounding errors in tax-inclusive item pricing ([4eb54db](https://github.com/one-highflyer/nexwave_shopify_connector/commit/4eb54dbfac2fa77dab2d94018f00d5429f5c7ef3))

## [1.6.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.6.0...v1.6.1) (2026-01-24)


### Bug Fixes

* **sync:** prevent session corruption when manually triggering order sync ([f5cec6f](https://github.com/one-highflyer/nexwave_shopify_connector/commit/f5cec6fc788ef7ed3de9cc09587717b980304fab))

# [1.6.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.5.3...v1.6.0) (2026-01-24)


### Features

* **webhooks:** add shop domain alias for webhook routing ([b808f9e](https://github.com/one-highflyer/nexwave_shopify_connector/commit/b808f9e0821aff8cb08ee0363f332f629ffbeb2e))

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

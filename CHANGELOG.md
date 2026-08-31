## [15.1.5](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.1.4...v15.1.5) (2026-08-31)


### Bug Fixes

* **order:** address item lookup review feedback ([f32008a](https://github.com/one-highflyer/nexwave_shopify_connector/commit/f32008ab7be54cc06752164c4c7ee35bc0646c02))
* **order:** resolve line items by variant mapping ([50e6b5e](https://github.com/one-highflyer/nexwave_shopify_connector/commit/50e6b5ef403eafe338764999d294be070734581b))

## [15.1.4](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.1.3...v15.1.4) (2026-07-11)


### Bug Fixes

* **inventory:** cache inventory ids by mapping row ([dda6e01](https://github.com/one-highflyer/nexwave_shopify_connector/commit/dda6e014ead4c91dd6742a73f0c9bc55239609e3))
* **inventory:** dedupe duplicate inventory mapping rows ([995a5e7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/995a5e7f1f581b5e1fd4e7b7fc7e26b4d5360291))
* **inventory:** harden target ownership and API versioning ([9ffc9ff](https://github.com/one-highflyer/nexwave_shopify_connector/commit/9ffc9ffec8b8214c5642e0239468af872dc006bb))

## [15.1.3](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.1.2...v15.1.3) (2026-06-21)


### Bug Fixes

* **inventory:** gate draft order reservations by store ([4a334b1](https://github.com/one-highflyer/nexwave_shopify_connector/commit/4a334b1c7b8487df2efadbfe8584f7d9242a65bf))
* **order:** reserve stock for draft Shopify orders ([c3d4106](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c3d4106335c26b68a8395e8af1dcb45788e2d347))

## [15.1.2](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.1.1...v15.1.2) (2026-06-21)


### Bug Fixes

* **order:** show disabled customer matches in Shopify error ([155ef6c](https://github.com/one-highflyer/nexwave_shopify_connector/commit/155ef6cfc53a215858a1ae15f284048702adfa25))

## [15.1.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.1.0...v15.1.1) (2026-06-20)


### Bug Fixes

* **order:** deduplicate Shopify email customer matches ([02f95a2](https://github.com/one-highflyer/nexwave_shopify_connector/commit/02f95a237182252bd1f46e2ba04fa132078d6563))
* **order:** ignore disabled customers in Shopify email matching ([7675962](https://github.com/one-highflyer/nexwave_shopify_connector/commit/7675962acde97a4ed8cb23e456162643d7a4db1f))

# [15.1.0](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.0.1...v15.1.0) (2026-06-19)


### Bug Fixes

* **inventory:** use static inventory item queries ([061f9dc](https://github.com/one-highflyer/nexwave_shopify_connector/commit/061f9dceac419800cd30a026ad92b172c4b34d6b))
* **types:** avoid generated literal annotation lint ([0237fdf](https://github.com/one-highflyer/nexwave_shopify_connector/commit/0237fdf03cce6d9eee31dd5ad6f4d732ce497c2d))


### Features

* **inventory:** add changed-bin inventory sync mode ([ec5841f](https://github.com/one-highflyer/nexwave_shopify_connector/commit/ec5841f0b3875d952818887f2761db38cfadbb45))

## [15.0.1](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v15.0.0...v15.0.1) (2026-05-05)


### Bug Fixes

* **order:** fetch transactions from Shopify when payload omits them ([61b077e](https://github.com/one-highflyer/nexwave_shopify_connector/commit/61b077ec7cb3a9a44775a42ffe2ddebde3b0e6f9))
* **order:** harden payment fetch error handling ([75e5300](https://github.com/one-highflyer/nexwave_shopify_connector/commit/75e5300f48b9face014a50673a0f90e80cf89033))
* **test:** use company default currency in payment test fixtures ([1cdcf7a](https://github.com/one-highflyer/nexwave_shopify_connector/commit/1cdcf7a07fda3b8f83a58709ddbcdde6644e20ef))

## [1.11.15](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.14...v1.11.15) (2026-04-12)


### Bug Fixes

* **inventory:** sync available qty instead of in-hand qty to Shopify ([6e6deb5](https://github.com/one-highflyer/nexwave_shopify_connector/commit/6e6deb5735322a62c612b7c84a5b8f6cc168b111))

## [1.11.14](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.13...v1.11.14) (2026-04-10)


### Bug Fixes

* **inventory:** address review findings for GraphQL sync ([2c0d2ca](https://github.com/one-highflyer/nexwave_shopify_connector/commit/2c0d2caa48349947484bbb1585f1b83173f0e55d))
* **inventory:** address second-round review findings ([f494d72](https://github.com/one-highflyer/nexwave_shopify_connector/commit/f494d72da6b3a2282f510823f447d276aecb07c4))
* **inventory:** apply override merge and add batch-size guards ([0c14ebb](https://github.com/one-highflyer/nexwave_shopify_connector/commit/0c14ebb97bef21d3267b2ab8e17a2e373c01bdac))
* **inventory:** batch inventory sync via GraphQL and cache inventory_item_id ([83eb19b](https://github.com/one-highflyer/nexwave_shopify_connector/commit/83eb19bedfc5f4a97e545b4f82ed832a59bd9614))
* **inventory:** ensure_item_shopify_store_row always sets cache field ([a5f63eb](https://github.com/one-highflyer/nexwave_shopify_connector/commit/a5f63ebe18e38ac6f8086693303827f9ef0cc9f7))
* **inventory:** harden retry, error classification, and zero-progress reporting ([dbc3ef2](https://github.com/one-highflyer/nexwave_shopify_connector/commit/dbc3ef2b4e7da673c143632407546cabe21e0d80))
* **inventory:** use InventoryItem.tracked and report backfill errors ([5cf1883](https://github.com/one-highflyer/nexwave_shopify_connector/commit/5cf18838c2dacaf49e07a3306d24a9123b82c5aa))

## [1.11.13](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.12...v1.11.13) (2026-04-09)


### Bug Fixes

* **inventory:** increase sync job timeout to 3 hours ([289ead9](https://github.com/one-highflyer/nexwave_shopify_connector/commit/289ead94847a278017f50420c51c925dddf24dc3))
* **product:** log failed SKU mapping jobs to NexWave Shopify Log ([f2cdad0](https://github.com/one-highflyer/nexwave_shopify_connector/commit/f2cdad01f05aef0390f0ea1d4159fdc3d97c8176))

## [1.11.12](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.11...v1.11.12) (2026-04-09)


### Bug Fixes

* **inventory:** deduplicate manual inventory sync job ([a57749a](https://github.com/one-highflyer/nexwave_shopify_connector/commit/a57749af22ba5569211c1f9f1b0a6a1d7028ba43))

## [1.11.11](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.10...v1.11.11) (2026-04-09)


### Bug Fixes

* **product:** use job_id with deduplicate for SKU mapping job ([c14a042](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c14a042f335fd5d0663737c2da8db79cc1facd94))
* **sku-mapping:** skip sync_item_to_shopify during bulk SKU mapping ([a9ab3ff](https://github.com/one-highflyer/nexwave_shopify_connector/commit/a9ab3ff9e195ab0da44af44c013580480bc663cc))
* **sku-mapping:** update JS button text for async SKU mapping job ([c7d3ded](https://github.com/one-highflyer/nexwave_shopify_connector/commit/c7d3dedd5bae1ca8f7b6db6d1f1644d4b2e66fd6))
* **sku-mapping:** use db_insert in _update_item_shopify_store_row create path ([b42a33d](https://github.com/one-highflyer/nexwave_shopify_connector/commit/b42a33d2154c75a7d6120bf411e628eac1541f66))
* **sku-mapping:** use db_insert in _upsert_item_store_mapping create path ([a906cb3](https://github.com/one-highflyer/nexwave_shopify_connector/commit/a906cb331fef3b585dd78013a107c3101917560e))
* **test:** use leaf customer group in test fixture ([59035a0](https://github.com/one-highflyer/nexwave_shopify_connector/commit/59035a0b93561a0b70073d3de3d2b3f35261a0c3))

## [1.11.10](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.9...v1.11.10) (2026-04-04)


### Bug Fixes

* **ci:** install pkg-config for self-hosted runner ([5e193bd](https://github.com/one-highflyer/nexwave_shopify_connector/commit/5e193bd72a5d0e608b430530e7ef4fe797beed99))

## [1.11.9](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.8...v1.11.9) (2026-04-01)


### Bug Fixes

* **ci:** skip release commits and fix concurrency group ([add185a](https://github.com/one-highflyer/nexwave_shopify_connector/commit/add185a1a56fd69508159c0ba848c41ec47a5489))

## [1.11.8](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.7...v1.11.8) (2026-04-01)


### Bug Fixes

* **ci:** run erpnext.setup.utils.before_tests before test suite ([28b541c](https://github.com/one-highflyer/nexwave_shopify_connector/commit/28b541ccf7a625dca3836366de3730679b76d3e8))

## [1.11.7](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.6...v1.11.7) (2026-04-01)


### Bug Fixes

* **order:** construct person name from first_name/last_name as title fallback ([b2bc55d](https://github.com/one-highflyer/nexwave_shopify_connector/commit/b2bc55d268bb6ab9009a54eeb51153dfdb857411))
* **order:** include address_line2 in dedup to distinguish suites/units ([cdaea98](https://github.com/one-highflyer/nexwave_shopify_connector/commit/cdaea988783101db420f3c484244bba87087af77))
* **order:** upgrade existing address title to company name on dedup match ([a0e2a7c](https://github.com/one-highflyer/nexwave_shopify_connector/commit/a0e2a7c1b4e216aef0e580667f40082f67485378))
* **order:** use Shopify company name for address title and improve dedup ([43f74ab](https://github.com/one-highflyer/nexwave_shopify_connector/commit/43f74abbf0ade0b0eedc940cecf720ad9c71f642))

## [1.11.6](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.5...v1.11.6) (2026-03-15)


### Bug Fixes

* update semantic-release and CI to use version-15 branch ([f5bddc0](https://github.com/one-highflyer/nexwave_shopify_connector/commit/f5bddc068691c088e778402b7e73d7b4972cd714))

## [1.11.5](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.4...v1.11.5) (2026-03-15)


### Bug Fixes

* move documentation links to top of README ([36c37be](https://github.com/one-highflyer/nexwave_shopify_connector/commit/36c37bea770c1125af90d9156990051e559afee6))

## [1.11.4](https://github.com/one-highflyer/nexwave_shopify_connector/compare/v1.11.3...v1.11.4) (2026-03-14)


### Bug Fixes

* add frappe-dependencies for Frappe Cloud marketplace ([58bbce7](https://github.com/one-highflyer/nexwave_shopify_connector/commit/58bbce72f10b45f27aab5bbc4d7c3b341c2d102f))

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

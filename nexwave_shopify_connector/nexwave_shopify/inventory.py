# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

import math
import time
from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, now_datetime
from shopify.api_version import ApiVersion
from shopify.session import Session

from nexwave_shopify_connector.nexwave_shopify.connection import DEFAULT_API_VERSION
from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
	INVENTORY_BATCH_SIZE,
	NODES_BATCH_SIZE,
	THROTTLE_MIN_AVAILABLE,
	BatchResult,
	ShopifyGraphQLError,
	ThrottleStatus,
	fetch_inventory_item_ids,
	set_inventory_batch,
)
from nexwave_shopify_connector.nexwave_shopify.utils import create_shopify_log
from nexwave_shopify_connector.utils.logger import get_logger

if TYPE_CHECKING:
	from nexwave_shopify_connector.nexwave_shopify.doctype.shopify_store.shopify_store import ShopifyStore


INVENTORY_SYNC_MODE_FULL = "Full Inventory"
INVENTORY_SYNC_MODE_CHANGED_BINS = "Changed Bins"

INVENTORY_SYNC_METHOD_FULL = "nexwave_shopify_connector.nexwave_shopify.inventory.sync_store_inventory"
INVENTORY_SYNC_METHOD_CHANGED_BINS = (
	"nexwave_shopify_connector.nexwave_shopify.inventory.sync_changed_bin_inventory"
)


def update_inventory_on_shopify():
	"""
	Scheduler job - sync inventory for all enabled stores.

	Runs every 10 minutes (configured in hooks.py) but checks each store's
	inventory_sync_frequency to determine if it's time to sync.
	"""
	# Get all stores with inventory sync enabled
	stores = frappe.get_all("Shopify Store", filters={"enabled": 1, "enable_inventory_sync": 1}, pluck="name")

	for store_name in stores:
		store = frappe.get_doc("Shopify Store", store_name)

		# Check if it's time to sync based on frequency
		if not _should_sync_inventory(store):
			continue

		sync_method = INVENTORY_SYNC_METHOD_FULL
		if (store.get("inventory_sync_mode") or INVENTORY_SYNC_MODE_FULL) == INVENTORY_SYNC_MODE_CHANGED_BINS:
			sync_method = INVENTORY_SYNC_METHOD_CHANGED_BINS

		# Enqueue inventory sync for this store
		frappe.enqueue(
			sync_method,
			queue="long",
			timeout=10800,  # 3 hour timeout for large inventories
			job_id=f"inventory_sync_{store_name}",
			deduplicate=True,
			store_name=store_name,
		)


def _should_sync_inventory(store) -> bool:
	"""
	Check if inventory sync should run based on store's sync frequency.

	Args:
		store: Shopify Store document

	Returns:
		True if sync should run
	"""
	if not store.last_inventory_sync:
		return True

	frequency_minutes = store.inventory_sync_frequency or 60
	next_sync_time = add_to_date(store.last_inventory_sync, minutes=frequency_minutes)

	return now_datetime() >= next_sync_time


def _get_location_mapping(store) -> list[tuple[str, str]]:
	"""Return valid (Shopify location id, ERPNext warehouse) pairs for a store."""
	return [
		(m.shopify_location_id, m.erpnext_warehouse)
		for m in store.warehouse_mapping or []
		if m.shopify_location_id and m.erpnext_warehouse
	]


def sync_store_inventory(store_name: str, force: bool = False):
	"""
	Sync inventory for all items linked to a specific store.

	Args:
		store_name: Shopify Store name
		force: When True, bypass the sync frequency check. Used by manual
			triggers so the operator can re-run the sync immediately even if
			the last successful sync was recent.
	"""
	logger = get_logger()
	logger.info("Syncing inventory for Shopify store: %s", store_name)
	store = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled or not store.enable_inventory_sync:
		return

	# Check warehouse mappings
	if not store.warehouse_mapping:
		logger.error("No warehouse mappings configured for inventory sync for Shopify store: %s", store_name)
		frappe.log_error(
			title=f"Shopify Inventory Sync - {store_name}",
			message="No warehouse mappings configured for inventory sync",
		)
		return

	# Guard against duplicate jobs: with a single long-queue worker, duplicate
	# jobs run sequentially. Re-check the sync interval with fresh DB data
	# so that if a previous job already completed, this one skips. Manual
	# triggers pass force=True to bypass this check.
	if not force and not _should_sync_inventory(store):
		logger.info(
			"Skipping inventory sync for %s, already synced at %s",
			store_name,
			store.last_inventory_sync,
		)
		return

	# Bench config bailout: allows operators to disable GraphQL inventory sync per-store
	# without touching doctype settings (e.g., during a production incident).
	skip_stores = frappe.conf.get("nexwave_shopify_disable_graphql_inventory_sync") or []
	if store_name in skip_stores:
		logger.warning("Inventory sync disabled via bench config for %s", store_name)
		return

	# Initialize API versions
	_init_shopify_api_versions()

	# Get auth details
	api_version = store.api_version or DEFAULT_API_VERSION
	access_token = store.get_password("access_token")

	if not access_token:
		frappe.log_error(
			title=f"Shopify Inventory Sync Error - {store_name}",
			message=f"Access token not configured for store {store_name}",
		)
		return

	# Get all items with Shopify product/variant IDs for this store
	items_to_sync = get_items_with_shopify_ids(store_name)

	if not items_to_sync:
		logger.warning("No items to sync for Shopify store: %s", store_name)
		frappe.db.set_value("Shopify Store", store_name, "last_inventory_sync", now_datetime())
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- background sync job: phased commit
		return

	# Build location mapping from warehouse_mapping
	location_mapping = _get_location_mapping(store)
	if not location_mapping:
		logger.error("No valid location mappings for inventory sync for Shopify store: %s", store_name)
		frappe.log_error(
			title=f"Shopify Inventory Sync - {store_name}",
			message="No valid location mappings (location_id + warehouse) configured",
		)
		return

	t0 = time.monotonic()
	timestamp_iso = now_datetime().isoformat()
	total_sync = 0
	total_skip = 0
	total_error = 0

	# Bulk read Bin quantities for all (item, warehouse) pairs in one shot
	pairs = [(item["item_code"], wh) for item in items_to_sync for (_loc, wh) in location_mapping]
	qty_by_pair = _bulk_get_stock_qty(pairs)

	try:
		with Session.temp(store.shop_domain, api_version, access_token):
			# Lazy backfill: fill shopify_inventory_item_id on rows that are empty.
			items_to_sync, skipped_backfill, errored_backfill = _resolve_inventory_item_ids(
				store_name, items_to_sync, logger
			)
			total_skip += len(skipped_backfill)
			total_error += len(errored_backfill)
			# Log backfill failures as errors so the sync summary surfaces them
			# instead of masking them as successful skips.
			for item in errored_backfill:
				create_shopify_log(
					status="Error",
					method="sync_store_inventory",
					shopify_store=store_name,
					message=f"Backfill failed for {item.get('item_code')}",
					exception=item.get("_error_reason", ""),
					reference_doctype="Item",
					reference_name=item.get("item_code"),
				)

			# Iterate locations; for each build a list of quantity entries and
			# chunk into INVENTORY_BATCH_SIZE mutations.
			for location_id, warehouse in location_mapping:
				qty_entries = _build_quantities_for_location(
					location_id, warehouse, items_to_sync, qty_by_pair
				)
				if not qty_entries:
					continue

				num_batches = max(1, math.ceil(len(qty_entries) / INVENTORY_BATCH_SIZE))
				for i, chunk in enumerate(_chunked(qty_entries, INVENTORY_BATCH_SIZE), start=1):
					batch_t0 = time.monotonic()
					try:
						result = _execute_batch_with_retry(
							chunk=chunk,
							store_name=store_name,
							timestamp_iso=timestamp_iso,
							logger=logger,
						)
					except ShopifyGraphQLError as e:
						# Whole batch failed after retries
						# TODO: per-item log fan-out for a 250-item batch can
						# produce a burst of Error logs for a single root cause.
						# Future PR: emit one aggregate Error log per failed
						# batch and a debug logger line listing the item codes.
						total_error += len(chunk)
						for q in chunk:
							create_shopify_log(
								status="Error",
								method="sync_store_inventory",
								shopify_store=store_name,
								message=f"Batch failed for {q['item_code']}",
								exception=str(e),
								reference_doctype="Item",
								reference_name=q["item_code"],
								request_data={
									"location_id": location_id,
									"qty": q["qty"],
								},
							)
						continue

					total_sync += len(result.succeeded)
					total_error += len(result.failed)
					for item_code, err_msg in result.failed:
						create_shopify_log(
							status="Error",
							method="sync_store_inventory",
							shopify_store=store_name,
							message=f"Shopify userError for {item_code}",
							exception=err_msg,
							reference_doctype="Item",
							reference_name=item_code,
						)

					batch_elapsed = time.monotonic() - batch_t0
					logger.info(
						"Processed batch %s/%s (%s items) in %.2fs for location %s, %s cost pts remaining",
						i,
						num_batches,
						len(chunk),
						batch_elapsed,
						location_id,
						result.throttle.currently_available,
					)
					_throttle_if_needed(result.throttle, logger)

		# Only update last_inventory_sync when the run made actual progress
		# (at least one item synced successfully). Zero-progress runs are
		# either errors (bad token, schema failure) or config drift (every
		# item has tracking disabled, variants deleted). In both cases we
		# want the scheduler to retry next cycle rather than silently skip
		# for the inventory_sync_frequency window.
		if total_sync > 0:
			frappe.db.set_value("Shopify Store", store_name, "last_inventory_sync", now_datetime())
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- background sync job: phased commit

		# Determine overall status
		if total_error > 0 and total_sync == 0:
			status = "Error"  # Complete failure
		elif total_sync == 0 and total_skip > 0:
			# Zero-progress run: no errors but nothing was actually pushed.
			# Could be a legitimate state (all items untracked) or config
			# drift (e.g., mass variant deletion). Surface as Warning so
			# operators investigate rather than assume success.
			status = "Warning"
		elif total_error > 0:
			status = "Warning"  # Partial success
		else:
			status = "Success"  # All items synced

		elapsed = int(time.monotonic() - t0)
		summary = (
			f"Sync complete: {total_sync} synced, {total_skip} skipped, {total_error} errors, {elapsed}s"
		)
		logger.info("%s - store=%s status=%s", summary, store_name, status)
		create_shopify_log(
			status=status,
			method="sync_store_inventory",
			shopify_store=store_name,
			message=summary,
			reference_doctype="Shopify Store",
			reference_name=store_name,
		)

	except Exception as e:
		logger.error(
			"Store-level sync error for Shopify store: %s, error: %s", store_name, str(e), exc_info=True
		)
		create_shopify_log(
			status="Error",
			method="sync_store_inventory",
			shopify_store=store_name,
			message=f"Store-level sync error: {e!s}",
			exception=frappe.get_traceback(),
			reference_doctype="Shopify Store",
			reference_name=store_name,
		)


def sync_changed_bin_inventory(store_name: str, force: bool = False):
	"""
	Sync only items with changed stock Bin rows or recently synced Shopify mappings.

	An empty cursor falls back to a full inventory sync to establish the first
	baseline. The cursor only advances after all selected items are processed
	without errors; no-op scans also advance the cursor.
	"""
	logger = get_logger()
	logger.info("Syncing changed Bin inventory for Shopify store: %s", store_name)
	store = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled or not store.enable_inventory_sync:
		return

	if not store.warehouse_mapping:
		logger.error("No warehouse mappings configured for inventory sync for Shopify store: %s", store_name)
		frappe.log_error(
			title=f"Shopify Inventory Sync - {store_name}",
			message="No warehouse mappings configured for inventory sync",
		)
		return

	if not force and not _should_sync_inventory(store):
		logger.info(
			"Skipping changed Bin inventory sync for %s, already synced at %s",
			store_name,
			store.last_inventory_sync,
		)
		return

	skip_stores = frappe.conf.get("nexwave_shopify_disable_graphql_inventory_sync") or []
	if store_name in skip_stores:
		logger.warning("Changed Bin inventory sync disabled via bench config for %s", store_name)
		return

	if not store.last_inventory_sync:
		logger.info("No inventory sync cursor for %s; running full inventory sync baseline", store_name)
		sync_store_inventory(store_name, force=True)
		return

	location_mapping = _get_location_mapping(store)
	if not location_mapping:
		logger.error("No valid location mappings for inventory sync for Shopify store: %s", store_name)
		frappe.log_error(
			title=f"Shopify Inventory Sync - {store_name}",
			message="No valid location mappings (location_id + warehouse) configured",
		)
		return

	t0 = time.monotonic()
	scan_started_at = now_datetime()
	item_codes = _get_changed_inventory_item_codes(
		store=store,
		since=store.last_inventory_sync,
		until=scan_started_at,
		location_mapping=location_mapping,
	)

	if not item_codes:
		frappe.db.set_value("Shopify Store", store_name, "last_inventory_sync", scan_started_at)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- background sync job: phased commit
		elapsed = int(time.monotonic() - t0)
		summary = f"Changed Bin sync complete: 0 synced, 0 skipped, 0 errors, {elapsed}s"
		logger.info("%s - store=%s status=Success", summary, store_name)
		create_shopify_log(
			status="Success",
			method="sync_changed_bin_inventory",
			shopify_store=store_name,
			message=summary,
			reference_doctype="Shopify Store",
			reference_name=store_name,
		)
		return

	stats = sync_items_inventory(store_name, item_codes, source="changed_bins")
	if stats["errors"] == 0:
		frappe.db.set_value("Shopify Store", store_name, "last_inventory_sync", scan_started_at)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit -- background sync job: phased commit

	if stats["errors"] > 0 and stats["synced"] == 0:
		status = "Error"
	elif stats["errors"] > 0:
		status = "Warning"
	else:
		status = "Success"

	summary = (
		f"Changed Bin sync complete: {stats['synced']} synced, {stats['skipped']} skipped, "
		f"{stats['errors']} errors, {stats['elapsed']}s"
	)
	logger.info("%s - store=%s status=%s", summary, store_name, status)
	create_shopify_log(
		status=status,
		method="sync_changed_bin_inventory",
		shopify_store=store_name,
		message=summary,
		reference_doctype="Shopify Store",
		reference_name=store_name,
	)


def sync_items_inventory(
	store_name: str,
	item_codes: list[str] | tuple[str, ...] | set[str],
	source: str = "changed_bins",
) -> dict[str, int]:
	"""Sync inventory for a provided set of item codes using the bulk GraphQL path."""
	logger = get_logger()
	method = "sync_changed_bin_inventory" if source == "changed_bins" else "sync_items_inventory"
	t0 = time.monotonic()
	stats = {"synced": 0, "skipped": 0, "errors": 0, "elapsed": 0}
	item_codes = sorted({item_code for item_code in item_codes if item_code})

	if not item_codes:
		return stats

	try:
		store = frappe.get_doc("Shopify Store", store_name)
		if not store.enabled or not store.enable_inventory_sync:
			return stats

		skip_stores = frappe.conf.get("nexwave_shopify_disable_graphql_inventory_sync") or []
		if store_name in skip_stores:
			logger.warning("Inventory sync disabled via bench config for %s", store_name)
			return stats

		location_mapping = _get_location_mapping(store)
		if not location_mapping:
			logger.error("No valid location mappings for inventory sync for Shopify store: %s", store_name)
			create_shopify_log(
				status="Error",
				method=method,
				shopify_store=store_name,
				message="No valid location mappings (location_id + warehouse) configured",
				reference_doctype="Shopify Store",
				reference_name=store_name,
			)
			stats["errors"] = len(item_codes)
			return stats

		_init_shopify_api_versions()
		api_version = store.api_version or DEFAULT_API_VERSION
		access_token = store.get_password("access_token")
		if not access_token:
			create_shopify_log(
				status="Error",
				method=method,
				shopify_store=store_name,
				message=f"Access token not configured for store {store_name}",
				reference_doctype="Shopify Store",
				reference_name=store_name,
			)
			stats["errors"] = len(item_codes)
			return stats

		items_to_sync = get_items_with_shopify_ids(store_name, item_codes=item_codes)
		if not items_to_sync:
			return stats

		pairs = [(item["item_code"], wh) for item in items_to_sync for (_loc, wh) in location_mapping]
		qty_by_pair = _bulk_get_stock_qty(pairs)
		timestamp_iso = now_datetime().isoformat()

		with Session.temp(store.shop_domain, api_version, access_token):
			items_to_sync, skipped_backfill, errored_backfill = _resolve_inventory_item_ids(
				store_name, items_to_sync, logger
			)
			stats["skipped"] += len(skipped_backfill)
			stats["errors"] += len(errored_backfill)
			for item in errored_backfill:
				create_shopify_log(
					status="Error",
					method=method,
					shopify_store=store_name,
					message=f"Backfill failed for {item.get('item_code')}",
					exception=item.get("_error_reason", ""),
					reference_doctype="Item",
					reference_name=item.get("item_code"),
				)

			for location_id, warehouse in location_mapping:
				qty_entries = _build_quantities_for_location(
					location_id, warehouse, items_to_sync, qty_by_pair
				)
				if not qty_entries:
					continue

				num_batches = max(1, math.ceil(len(qty_entries) / INVENTORY_BATCH_SIZE))
				for i, chunk in enumerate(_chunked(qty_entries, INVENTORY_BATCH_SIZE), start=1):
					batch_t0 = time.monotonic()
					try:
						result = _execute_batch_with_retry(
							chunk=chunk,
							store_name=store_name,
							timestamp_iso=timestamp_iso,
							logger=logger,
						)
					except ShopifyGraphQLError as e:
						stats["errors"] += len(chunk)
						for q in chunk:
							create_shopify_log(
								status="Error",
								method=method,
								shopify_store=store_name,
								message=f"Batch failed for {q['item_code']}",
								exception=str(e),
								reference_doctype="Item",
								reference_name=q["item_code"],
								request_data={
									"location_id": location_id,
									"qty": q["qty"],
								},
							)
						continue

					stats["synced"] += len(result.succeeded)
					stats["errors"] += len(result.failed)
					for item_code, err_msg in result.failed:
						create_shopify_log(
							status="Error",
							method=method,
							shopify_store=store_name,
							message=f"Shopify userError for {item_code}",
							exception=err_msg,
							reference_doctype="Item",
							reference_name=item_code,
						)

					batch_elapsed = time.monotonic() - batch_t0
					logger.info(
						"Processed %s batch %s/%s (%s items) in %.2fs for location %s, %s cost pts remaining",
						source,
						i,
						num_batches,
						len(chunk),
						batch_elapsed,
						location_id,
						result.throttle.currently_available,
					)
					_throttle_if_needed(result.throttle, logger)

	except Exception as e:
		logger.error(
			"Item inventory sync error for Shopify store: %s, error: %s",
			store_name,
			str(e),
			exc_info=True,
		)
		create_shopify_log(
			status="Error",
			method=method,
			shopify_store=store_name,
			message=f"Item inventory sync error: {e!s}",
			exception=frappe.get_traceback(),
			reference_doctype="Shopify Store",
			reference_name=store_name,
		)
		stats["errors"] += max(len(item_codes), 1)
	finally:
		stats["elapsed"] = int(time.monotonic() - t0)

	return stats


def _get_changed_inventory_item_codes(
	store,
	since,
	until,
	location_mapping: list[tuple[str, str]] | None = None,
) -> list[str]:
	"""Return mapped item codes whose Bin or store mapping changed inside the cursor window."""
	location_mapping = location_mapping or _get_location_mapping(store)
	warehouses = tuple(sorted({warehouse for (_location, warehouse) in location_mapping if warehouse}))
	if not warehouses or not since or not until:
		return []

	params = {
		"store_name": store.name,
		"warehouses": warehouses,
		"since": since,
		"until": until,
	}
	bin_rows = frappe.db.sql(
		"""
		SELECT DISTINCT iss.parent AS item_code
		FROM `tabItem Shopify Store` iss
		JOIN `tabItem` item ON item.name = iss.parent
		JOIN `tabBin` bin ON bin.item_code = iss.parent
		WHERE iss.shopify_store = %(store_name)s
		  AND iss.enabled = 1
		  AND iss.shopify_variant_id IS NOT NULL
		  AND iss.shopify_variant_id != ''
		  AND item.disabled = 0
		  AND bin.warehouse IN %(warehouses)s
		  AND bin.modified > %(since)s
		  AND bin.modified <= %(until)s
		""",
		params,
		as_dict=True,
	)
	mapping_rows = frappe.db.sql(
		"""
		SELECT DISTINCT iss.parent AS item_code
		FROM `tabItem Shopify Store` iss
		JOIN `tabItem` item ON item.name = iss.parent
		WHERE iss.shopify_store = %(store_name)s
		  AND iss.enabled = 1
		  AND iss.shopify_variant_id IS NOT NULL
		  AND iss.shopify_variant_id != ''
		  AND item.disabled = 0
		  AND iss.last_sync_at IS NOT NULL
		  AND iss.last_sync_at > %(since)s
		  AND iss.last_sync_at <= %(until)s
		""",
		params,
		as_dict=True,
	)

	return sorted({row["item_code"] for row in [*bin_rows, *mapping_rows]})


def _execute_batch_with_retry(
	chunk: list[dict],
	store_name: str,
	timestamp_iso: str,
	logger,
) -> BatchResult:
	"""Wrap set_inventory_batch with retry logic.

	- 429: sleep retry_after if provided, else fall back to exponential
	  backoff (2s, 4s). Retry up to 2 times (3 total attempts).
	- Network / transport (http_status is None): exponential backoff (2s, 4s),
	  up to 2 retries.
	- 5xx: exponential backoff (2s, 4s), up to 2 retries.
	- Top-level GraphQL errors (http_status == -1): non-retryable, raise
	  immediately. These are schema/auth failures that won't recover on retry.
	- Other ShopifyGraphQLError (4xx other than 429): re-raise immediately.
	"""
	backoff = [2.0, 4.0]
	last_error: ShopifyGraphQLError | None = None
	for attempt in range(3):
		try:
			return set_inventory_batch(chunk, store_name, timestamp_iso, logger)
		except ShopifyGraphQLError as e:
			last_error = e
			status = e.http_status
			# Top-level GraphQL error (schema/auth): never retry. These are
			# deterministic failures; retrying wastes time and muddies logs
			# with spurious "network error" messages.
			if status == -1:
				raise
			if status == 429:
				if attempt < 2:
					# Fall back to exponential backoff if Shopify did not
					# supply a Retry-After header (the header is optional
					# per HTTP spec and Shopify sometimes omits it).
					sleep_for = e.retry_after if e.retry_after else backoff[attempt]
					logger.warning(
						"Rate limited, sleeping %.1fs (attempt %s)",
						sleep_for,
						attempt + 1,
					)
					time.sleep(sleep_for)
					continue
				raise
			if status is None or (500 <= (status or 0) < 600):
				if attempt < 2:
					logger.warning(
						"GraphQL error (attempt %s), backing off %ss",
						attempt + 1,
						backoff[attempt],
					)
					time.sleep(backoff[attempt])
					continue
				raise
			# Non-retryable error (4xx other than 429)
			raise
	# Defensive; loop either returns or raises
	if last_error:
		raise last_error
	raise ShopifyGraphQLError("Unknown batch failure")


def _bulk_get_stock_qty(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
	"""Read available-for-sale quantity from tabBin for many (item_code, warehouse) pairs.

	Available quantity is calculated as actual_qty minus submitted Sales Order
	reserved_qty minus draft Sales Order stock reservations, clamped to a
	minimum of 0. Returns a dict keyed by (item_code, warehouse). Missing pairs
	are absent; callers should default to 0.
	"""
	if not pairs:
		return {}

	# Deduplicate to reduce WHERE clause bloat
	unique_pairs = list({p for p in pairs})
	item_codes = list({p[0] for p in unique_pairs})
	warehouses = list({p[1] for p in unique_pairs})

	if not item_codes or not warehouses:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT item_code, warehouse, actual_qty, reserved_qty
		FROM `tabBin`
		WHERE item_code IN %(item_codes)s
		  AND warehouse IN %(warehouses)s
		""",
		{"item_codes": tuple(item_codes), "warehouses": tuple(warehouses)},
		as_dict=True,
	)

	draft_reserved_qty = _bulk_get_draft_reserved_stock_qty(item_codes, warehouses)

	# Use actual_qty - reserved_qty - draft reservations to get the
	# available-for-sale quantity. reserved_qty covers submitted Sales Order
	# commitments pending delivery. Draft Sales Orders are not included there,
	# but Stock Reservation Entries against draft orders represent confirmed
	# Shopify demand that must also be excluded.
	# Future: this could be made configurable per Shopify Store with options
	# such as Actual Qty, Actual minus Reserved, or Projected Qty.
	result: dict[tuple[str, str], float] = {}
	for row in rows:
		key = (row["item_code"], row["warehouse"])
		available = flt(row["actual_qty"]) - flt(row["reserved_qty"]) - flt(draft_reserved_qty.get(key, 0))
		result[key] = max(available, 0)
	return result


def _bulk_get_draft_reserved_stock_qty(
	item_codes: list[str], warehouses: list[str]
) -> dict[tuple[str, str], float]:
	if not item_codes or not warehouses:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			sre.item_code,
			sre.warehouse,
			SUM(sre.reserved_qty - sre.delivered_qty) AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		INNER JOIN `tabSales Order` so
			ON so.name = sre.voucher_no
		WHERE sre.docstatus = 1
		  AND sre.voucher_type = 'Sales Order'
		  AND sre.item_code IN %(item_codes)s
		  AND sre.warehouse IN %(warehouses)s
		  AND sre.status NOT IN ('Delivered', 'Cancelled')
		  AND sre.reserved_qty > sre.delivered_qty
		  AND so.docstatus = 0
		GROUP BY sre.item_code, sre.warehouse
		""",
		{"item_codes": tuple(item_codes), "warehouses": tuple(warehouses)},
		as_dict=True,
	)

	return {(row["item_code"], row["warehouse"]): flt(row["reserved_qty"]) for row in rows}


def _resolve_inventory_item_ids(
	store_name: str, items: list[dict], logger
) -> tuple[list[dict], list[dict], list[dict]]:
	"""Ensure every item has shopify_inventory_item_id. Lazy-backfill via GraphQL nodes query.

	Items already with a cached id are kept as-is. Items without a cached id are
	looked up in batches. Items are classified into three buckets:

	- resolved: ready to sync (has inventory_item_id and tracking is enabled)
	- skipped: intentionally not synced (variant deleted, tracking disabled, etc.)
	- errored: failed to resolve due to a Shopify API error; these should be
	  reported as errors in the sync summary, not silent skips.

	Returns:
		(resolved_items, skipped_items, errored_items)
	"""
	has_cache: list[dict] = []
	needs_lookup: list[dict] = []
	for item in items:
		if item.get("shopify_inventory_item_id"):
			has_cache.append(item)
		else:
			needs_lookup.append(item)

	skipped: list[dict] = []
	errored: list[dict] = []

	if not needs_lookup:
		return has_cache, skipped, errored

	logger.info(
		"Backfilling inventory_item_id for %s items in store %s",
		len(needs_lookup),
		store_name,
	)

	# Map variant_id -> item dict for fast reassembly
	by_variant: dict[str, dict] = {}
	for item in needs_lookup:
		variant_id = str(item.get("shopify_variant_id") or "")
		if variant_id:
			by_variant[variant_id] = item

	variant_ids = list(by_variant.keys())
	resolved: list[dict] = []
	for chunk in _chunked(variant_ids, NODES_BATCH_SIZE):
		try:
			lookup_result = fetch_inventory_item_ids(chunk, logger=logger)
		except ShopifyGraphQLError as e:
			logger.error(
				"Failed to fetch inventory_item_ids for %s variants in store %s: %s",
				len(chunk),
				store_name,
				e,
				exc_info=True,
			)
			# Classify as errors (not skips). These items wanted to be synced
			# but a Shopify API failure prevented it. The sync summary should
			# surface this as an Error, not a silent skip.
			for vid in chunk:
				item = by_variant.get(vid)
				if item:
					errored.append({**item, "_error_reason": f"lookup_failed: {e}"})
			continue

		for vid in chunk:
			item = by_variant.get(vid)
			if not item:
				continue
			info = lookup_result.get(vid)
			if not info:
				# Variant not found in Shopify (likely deleted)
				logger.info(
					"Variant %s (item %s) missing from Shopify; skipping",
					vid,
					item.get("item_code"),
				)
				skipped.append({**item, "_skip_reason": "variant_not_found"})
				continue
			tracked = info.get("tracked", False)
			inv_item_id = info.get("inventory_item_id")
			if not tracked:
				logger.info(
					"Variant %s (item %s) inventory not tracked by Shopify; skipping",
					vid,
					item.get("item_code"),
				)
				skipped.append({**item, "_skip_reason": "not_tracked"})
				continue
			if not inv_item_id:
				logger.info(
					"Variant %s (item %s) has no inventoryItem; skipping",
					vid,
					item.get("item_code"),
				)
				skipped.append({**item, "_skip_reason": "no_inventory_item"})
				continue

			# Cache the inventory_item_id back to the DB for next time
			_cache_inventory_item_id(item["item_code"], store_name, str(inv_item_id))

			enriched = dict(item)
			enriched["shopify_inventory_item_id"] = str(inv_item_id)
			resolved.append(enriched)

	return has_cache + resolved, skipped, errored


def _cache_inventory_item_id(item_code: str, store_name: str, inventory_item_id: str) -> None:
	"""Cache the Shopify inventory_item_id back to Item Shopify Store.

	Uses frappe.db.set_value (not get_doc + save) to bypass on_update hooks
	and avoid recursion into product sync. Matches the pattern in
	shopify_store._upsert_item_store_mapping.
	"""
	frappe.db.set_value(
		"Item Shopify Store",
		{"parent": item_code, "shopify_store": store_name},
		"shopify_inventory_item_id",
		inventory_item_id,
		update_modified=False,
	)


def _build_quantities_for_location(
	location_id: str,
	warehouse: str,
	items: list[dict],
	qty_by_pair: dict[tuple[str, str], float],
) -> list[dict]:
	"""Build a list of quantity change dicts for one Shopify location."""
	logger = get_logger()
	result: list[dict] = []
	dropped = 0
	for item in items:
		inv_item_id = item.get("shopify_inventory_item_id")
		if not inv_item_id:
			# Upstream SQL in get_items_with_shopify_ids filters rows without
			# a variant_id, and the lazy backfill resolves inventory_item_id
			# for the rest. Anything still missing here would have been
			# caught and marked skipped in _resolve_inventory_item_ids. Log
			# a debug line just in case something slips through.
			dropped += 1
			continue
		raw_qty = qty_by_pair.get((item["item_code"], warehouse), 0) or 0
		# Clamp negative to 0 (Shopify doesn't accept negative)
		qty = max(int(raw_qty), 0)
		result.append(
			{
				"item_code": item["item_code"],
				"inventory_item_id": inv_item_id,
				"location_id": location_id,
				"qty": qty,
			}
		)
	if dropped:
		logger.debug(
			"_build_quantities_for_location: dropped %s item(s) without inventory_item_id for location %s",
			dropped,
			location_id,
		)
	return result


def _throttle_if_needed(throttle: ThrottleStatus, logger) -> None:
	"""Sleep if throttle.currently_available < THROTTLE_MIN_AVAILABLE.

	Pause duration math:
	- deficit: how many cost points we are below the safety floor.
	- restore_rate: Shopify returns points per second (typically 50.0).
	- deficit / restore_rate: raw seconds to recover back to the floor.
	- Clamped to [0.5, 10.0] seconds so:
	  * We never busy-loop (min 0.5s).
	  * We never block a worker for a full minute on a single pause
	    (max 10.0s). A long-running sync with repeated throttling still
	    progresses; a stuck bucket surfaces as Shopify 429s instead of
	    a silent stall.
	"""
	if throttle.currently_available >= THROTTLE_MIN_AVAILABLE:
		return
	deficit = THROTTLE_MIN_AVAILABLE - throttle.currently_available
	restore_rate = max(throttle.restore_rate, 1.0)
	pause = max(0.5, min(deficit / restore_rate, 10.0))
	logger.info(
		"Throttling: %s points available (min %s), sleeping %.2fs",
		throttle.currently_available,
		THROTTLE_MIN_AVAILABLE,
		pause,
	)
	time.sleep(pause)


def _chunked(seq, size):
	for i in range(0, len(seq), size):
		yield seq[i : i + size]


def _init_shopify_api_versions():
	"""Initialize Shopify API versions if not already loaded."""
	if not ApiVersion.versions:
		ApiVersion.fetch_known_versions()


def get_items_with_shopify_ids(
	store_name: str,
	item_codes: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict]:
	"""
	Get all items that have Shopify product/variant IDs for this store.

	Args:
		store_name: Shopify Store name
		item_codes: Optional item code filter

	Returns:
		List of dicts with item_code, shopify_product_id, shopify_variant_id,
		shopify_inventory_item_id
	"""
	params = {"store_name": store_name}
	if item_codes is None:
		query = """
			SELECT
				iss.parent as item_code,
				iss.shopify_product_id,
				iss.shopify_variant_id,
				iss.shopify_inventory_item_id
			FROM `tabItem Shopify Store` iss
			JOIN `tabItem` item ON item.name = iss.parent
			WHERE
				iss.shopify_store = %(store_name)s
				AND iss.enabled = 1
				AND iss.shopify_variant_id IS NOT NULL
				AND iss.shopify_variant_id != ''
				AND item.disabled = 0
		"""
	else:
		item_codes = sorted({item_code for item_code in item_codes if item_code})
		if not item_codes:
			return []
		params["item_codes"] = tuple(item_codes)
		query = """
			SELECT
				iss.parent as item_code,
				iss.shopify_product_id,
				iss.shopify_variant_id,
				iss.shopify_inventory_item_id
			FROM `tabItem Shopify Store` iss
			JOIN `tabItem` item ON item.name = iss.parent
			WHERE
				iss.shopify_store = %(store_name)s
				AND iss.enabled = 1
				AND iss.shopify_variant_id IS NOT NULL
				AND iss.shopify_variant_id != ''
				AND item.disabled = 0
				AND iss.parent IN %(item_codes)s
		"""

	return frappe.db.sql(query, params, as_dict=True)


def get_stock_qty(item_code: str, warehouse: str) -> float:
	"""
	Get available-for-sale stock quantity from ERPNext Bin.

	Available quantity is actual_qty minus submitted Sales Order reserved_qty
	minus draft Sales Order stock reservations, clamped to a minimum of 0.

	Args:
		item_code: Item code
		warehouse: Warehouse name

	Returns:
		Available quantity (0 if no bin exists or stock is fully reserved)
	"""
	# Match the bulk path (_bulk_get_stock_qty).
	# Future: this could be made configurable per Shopify Store.
	bin_data = frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		["actual_qty", "reserved_qty"],
		as_dict=True,
	)
	if not bin_data:
		return 0
	available = (
		flt(bin_data.actual_qty)
		- flt(bin_data.reserved_qty)
		- flt(_get_draft_reserved_stock_qty(item_code, warehouse))
	)
	return max(available, 0)


def _get_draft_reserved_stock_qty(item_code: str, warehouse: str) -> float:
	return flt(
		frappe.db.sql(
			"""
			SELECT SUM(sre.reserved_qty - sre.delivered_qty)
			FROM `tabStock Reservation Entry` sre
			INNER JOIN `tabSales Order` so
				ON so.name = sre.voucher_no
			WHERE sre.docstatus = 1
			  AND sre.voucher_type = 'Sales Order'
			  AND sre.item_code = %(item_code)s
			  AND sre.warehouse = %(warehouse)s
			  AND sre.status NOT IN ('Delivered', 'Cancelled')
			  AND sre.reserved_qty > sre.delivered_qty
			  AND so.docstatus = 0
			""",
			{"item_code": item_code, "warehouse": warehouse},
		)[0][0]
	)


def sync_single_item_inventory(item_code: str, store_name: str | None = None):
	"""
	Sync inventory for a single item to one or all stores.

	Can be called manually or from stock entry hooks.

	Args:
		item_code: ERPNext Item code
		store_name: Optional specific store (syncs to all eligible stores if not provided)
	"""
	logger = get_logger()
	item = frappe.get_doc("Item", item_code)

	if item.disabled:
		return

	# Get stores to sync to
	if store_name:
		stores = [frappe.get_doc("Shopify Store", store_name)]
	else:
		store_names = frappe.get_all(
			"Item Shopify Store",
			filters={"parent": item_code, "enabled": 1, "shopify_variant_id": ["is", "set"]},
			pluck="shopify_store",
		)
		stores = [frappe.get_doc("Shopify Store", name) for name in store_names]

	# Bench config bailout: same kill-switch honoured by the bulk sync path.
	# Without this, stock entries would keep syncing to a "disabled" store.
	skip_stores = frappe.conf.get("nexwave_shopify_disable_graphql_inventory_sync") or []

	for store in stores:
		if not store.enabled or not store.enable_inventory_sync:
			# Intentionally silent: disabled stores are an expected state,
			# not an error. No log needed.
			continue

		if store.name in skip_stores:
			logger.warning(
				"Single-item inventory sync disabled via bench config for %s (item %s)",
				store.name,
				item_code,
			)
			continue

		if not store.warehouse_mapping:
			logger.info(
				"Skipping single-item sync for %s -> %s (no warehouse mapping)",
				item_code,
				store.name,
			)
			continue

		_init_shopify_api_versions()

		api_version = store.api_version or DEFAULT_API_VERSION
		access_token = store.get_password("access_token")
		if not access_token:
			# Token rotation or misconfiguration: loud error, not silent.
			# If this silently drops, ERPNext and Shopify drift indefinitely.
			logger.error(
				"Access token missing for Shopify Store %s; single-item sync for %s cannot proceed",
				store.name,
				item_code,
			)
			create_shopify_log(
				status="Error",
				method="sync_single_item_inventory",
				shopify_store=store.name,
				message=f"Access token missing for {store.name}; cannot sync {item_code}",
				reference_doctype="Item",
				reference_name=item_code,
			)
			continue

		# Locate the mapping row for this store
		store_row = None
		for row in item.shopify_stores:
			if row.shopify_store == store.name:
				store_row = row
				break

		if not store_row or not store_row.shopify_variant_id:
			# Item is not mapped to this store. Expected when iterating
			# all stores for an item; not an error.
			continue

		# Build a single-item payload and reuse the batched helpers.
		items_payload = [
			{
				"item_code": item_code,
				"shopify_product_id": store_row.shopify_product_id,
				"shopify_variant_id": store_row.shopify_variant_id,
				"shopify_inventory_item_id": store_row.get("shopify_inventory_item_id"),
			}
		]

		location_mapping = _get_location_mapping(store)
		if not location_mapping:
			# Has warehouse_mapping rows but all are incomplete (missing
			# either the Shopify location_id or the ERPNext warehouse).
			# This is a config error worth surfacing.
			logger.warning(
				"Single-item sync skipped for %s -> %s: warehouse_mapping has no valid (location, warehouse) pairs",
				item_code,
				store.name,
			)
			create_shopify_log(
				status="Warning",
				method="sync_single_item_inventory",
				shopify_store=store.name,
				message=f"No valid warehouse mapping for {store.name}; cannot sync {item_code}",
				reference_doctype="Item",
				reference_name=item_code,
			)
			continue

		pairs = [(item_code, wh) for (_loc, wh) in location_mapping]
		qty_by_pair = _bulk_get_stock_qty(pairs)

		timestamp_iso = now_datetime().isoformat()

		try:
			with Session.temp(store.shop_domain, api_version, access_token):
				items_payload, skipped, errored = _resolve_inventory_item_ids(
					store.name, items_payload, logger
				)
				if not items_payload:
					reasons = [s.get("_skip_reason") for s in skipped] + [
						e.get("_error_reason") for e in errored
					]
					logger.info(
						"Single-item sync skipped for %s -> %s (reasons=%s)",
						item_code,
						store.name,
						reasons,
					)
					# If the skip was due to a backfill error (not a legitimate
					# skip reason), log it as an Error so it's visible.
					for e_item in errored:
						create_shopify_log(
							status="Error",
							method="sync_single_item_inventory",
							shopify_store=store.name,
							message=f"Backfill failed for {item_code}",
							exception=e_item.get("_error_reason", ""),
							reference_doctype="Item",
							reference_name=item_code,
						)
					continue

				for location_id, warehouse in location_mapping:
					qty_entries = _build_quantities_for_location(
						location_id, warehouse, items_payload, qty_by_pair
					)
					if not qty_entries:
						continue
					try:
						result = _execute_batch_with_retry(
							chunk=qty_entries,
							store_name=store.name,
							timestamp_iso=timestamp_iso,
							logger=logger,
						)
					except ShopifyGraphQLError as e:
						logger.error(
							"Single-item sync failed for %s -> %s at location %s: %s",
							item_code,
							store.name,
							location_id,
							e,
							exc_info=True,
						)
						create_shopify_log(
							status="Error",
							method="sync_single_item_inventory",
							shopify_store=store.name,
							message=f"Single-item sync failed for {item_code}",
							exception=str(e),
							reference_doctype="Item",
							reference_name=item_code,
						)
						continue

					for item_code_, err_msg in result.failed:
						create_shopify_log(
							status="Error",
							method="sync_single_item_inventory",
							shopify_store=store.name,
							message=f"Shopify userError for {item_code_}",
							exception=err_msg,
							reference_doctype="Item",
							reference_name=item_code_,
						)
					_throttle_if_needed(result.throttle, logger)

		except Exception as e:
			logger.error(
				"Single-item sync error for %s -> %s: %s",
				item_code,
				store.name,
				str(e),
				exc_info=True,
			)
			frappe.log_error(
				title=f"Shopify Inventory Sync Error - {store.name}",
				message=f"Failed to sync inventory for {item_code}: {e!s}",
			)


def manual_inventory_sync(store_name: str):
	"""
	Manual trigger to sync all inventory for a store.

	Called from "Sync Inventory" button on Shopify Store.

	Args:
		store_name: Shopify Store name
	"""
	logger = get_logger()
	logger.info("Manual inventory sync for Shopify store: %s", store_name)
	store: ShopifyStore = frappe.get_doc("Shopify Store", store_name)

	if not store.enabled:
		logger.error("Shopify store: %s is not enabled", store_name)
		frappe.throw(_("Store is not enabled"))

	if not store.enable_inventory_sync:
		logger.error("Inventory sync is not enabled for Shopify store: %s", store_name)
		frappe.throw(_("Inventory sync is not enabled for this store"))

	if not store.warehouse_mapping:
		logger.error("No warehouse mappings configured for Shopify store: %s", store_name)
		frappe.throw(_("No warehouse mappings configured"))

	# Enqueue sync job with force=True so manual triggers bypass the
	# per-store sync frequency guard. Without this, clicking "Sync Inventory"
	# shortly after a successful run would silently no-op.
	frappe.enqueue(
		"nexwave_shopify_connector.nexwave_shopify.inventory.sync_store_inventory",
		queue="long",
		timeout=10800,
		job_id=f"inventory_sync_{store_name}",
		deduplicate=True,
		store_name=store_name,
		force=True,
	)

	frappe.msgprint(_("Inventory sync has been queued for {0}").format(store_name), indicator="green")
	logger.info("Successfully queued inventory sync for Shopify store: %s", store_name)

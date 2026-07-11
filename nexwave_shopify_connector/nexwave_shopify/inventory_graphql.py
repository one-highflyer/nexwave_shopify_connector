# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
GraphQL helpers for Shopify inventory sync.

Thin wrappers around the shopify SDK's GraphQL client, returning parsed dicts
and exposing throttle state. Called from within an existing
Session.temp() context; no auth handling here.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import shopify

# Public constants
INVENTORY_BATCH_SIZE = 250
NODES_BATCH_SIZE = 250
THROTTLE_MIN_AVAILABLE = 200  # pause below this; typical bucket is 1000
DEFAULT_RESTORE_RATE = 50.0

INVENTORY_SET_RESPONSE_SELECTION = """    inventoryAdjustmentGroup {
      createdAt
      reason
      referenceDocumentUri
      changes {
        name
        delta
        quantityAfterChange
        item { id }
        location { id }
      }
    }
    userErrors {
      field
      message
      code
    }"""


def _build_inventory_set_mutation(*, idempotent: bool) -> str:
	idempotency_variable = ", $idempotencyKey: String!" if idempotent else ""
	idempotency_directive = " @idempotent(key: $idempotencyKey)" if idempotent else ""
	return f"""
mutation inventorySetQuantities($input: InventorySetQuantitiesInput!{idempotency_variable}) {{
  inventorySetQuantities(input: $input){idempotency_directive} {{
{INVENTORY_SET_RESPONSE_SELECTION}
  }}
}}
"""


INVENTORY_SET_MUTATION = _build_inventory_set_mutation(idempotent=False)
IDEMPOTENT_INVENTORY_SET_MUTATION = _build_inventory_set_mutation(idempotent=True)

VARIANT_NODES_QUERY = """
query variantInventoryItems($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id
      inventoryItem {
        id
        tracked
      }
    }
  }
}
"""


def _uses_idempotent_inventory_set(api_version: str | None) -> bool:
	"""Return whether the API version uses Shopify's idempotent inventory contract."""
	if not api_version:
		return False
	if str(api_version).strip().lower() == "unstable":
		return True

	match = re.fullmatch(r"(\d{4})-(\d{2})", str(api_version))
	if not match:
		return False

	return (int(match.group(1)), int(match.group(2))) >= (2026, 1)


@dataclass
class ThrottleStatus:
	currently_available: int = 1000
	maximum_available: int = 1000
	restore_rate: float = DEFAULT_RESTORE_RATE

	@classmethod
	def from_extensions(cls, extensions: dict | None) -> "ThrottleStatus":
		"""Parse extensions.cost.throttleStatus defensively.

		Shopify returns something like::

		    {
		        "cost": {
		            "requestedQueryCost": 10,
		            "actualQueryCost": 10,
		            "throttleStatus": {
		                "maximumAvailable": 1000.0,
		                "currentlyAvailable": 990,
		                "restoreRate": 50.0,
		            },
		        }
		    }
		"""
		if not extensions or not isinstance(extensions, dict):
			return cls()
		cost = extensions.get("cost") or {}
		throttle = cost.get("throttleStatus") or {}
		try:
			currently_available = int(throttle.get("currentlyAvailable", 1000))
		except (ValueError, TypeError):
			currently_available = 1000
		try:
			maximum_available = int(throttle.get("maximumAvailable", 1000))
		except (ValueError, TypeError):
			maximum_available = 1000
		try:
			restore_rate = float(throttle.get("restoreRate", DEFAULT_RESTORE_RATE))
		except (ValueError, TypeError):
			restore_rate = DEFAULT_RESTORE_RATE
		return cls(
			currently_available=currently_available,
			maximum_available=maximum_available,
			restore_rate=restore_rate,
		)


@dataclass
class BatchResult:
	succeeded: list[str] = field(default_factory=list)  # item_codes
	failed: list[tuple[str, str]] = field(default_factory=list)  # (item_code, error)
	throttle: ThrottleStatus = field(default_factory=ThrottleStatus)
	raw_response: dict | None = None


class ShopifyGraphQLError(Exception):
	"""Transport or protocol-level GraphQL failure."""

	def __init__(
		self,
		message: str,
		http_status: int | None = None,
		retry_after: float | None = None,
		body: str | None = None,
	):
		super().__init__(message)
		self.http_status = http_status
		self.retry_after = retry_after
		self.body = body


def execute_graphql(query: str, variables: dict[str, Any]) -> dict:
	"""
	Run a GraphQL call via the Shopify SDK and return the decoded JSON dict.

	Must be called inside a Session.temp() context.

	Error translation:
	- HTTP/transport errors (urllib, pyactiveresource, OSError subclasses):
	  wrapped as ShopifyGraphQLError with the appropriate http_status. 5xx
	  and network errors are retryable; 4xx other than 429 are not.
	- json.JSONDecodeError (malformed response, e.g. HTML error page):
	  wrapped with http_status=-1 so the retry wrapper treats it as
	  non-retryable. If the response isn't parseable JSON, retrying is
	  unlikely to help and the root cause should surface immediately.
	- Top-level GraphQL errors array (schema/auth failures): wrapped with
	  http_status=-1 (see the `result.get("errors")` block below).
	- Any other exception (NameError, TypeError, AttributeError, etc.) is
	  a bug in this module; it propagates up to the caller's store-level
	  handler rather than being silently retried as "network error".
	"""
	try:
		client = shopify.GraphQL()
		result_str = client.execute(query=query, variables=variables)
		result = json.loads(result_str)
	except ShopifyGraphQLError:
		raise
	except json.JSONDecodeError as e:
		raise ShopifyGraphQLError(
			f"Failed to parse Shopify GraphQL response: {e}",
			http_status=-1,
		) from e
	except Exception as e:
		# Only translate errors that look like HTTP/transport failures.
		# Anything else (code bugs, unexpected exception types) propagates
		# up rather than being wrapped as a retryable transient.
		status = getattr(e, "code", None) or getattr(e, "status_code", None)
		is_transport_error = (
			status is not None
			or getattr(e, "response", None) is not None
			or isinstance(e, OSError)  # ConnectionError, TimeoutError, etc.
		)
		if not is_transport_error:
			raise
		retry_after = None
		if status == 429:
			# urllib.error.HTTPError exposes .headers; pyactiveresource errors expose .response.headers
			headers = getattr(e, "headers", None)
			if headers is None:
				headers = getattr(getattr(e, "response", None), "headers", None)
			headers = headers or {}
			try:
				header_items = headers.items() if hasattr(headers, "items") else list(headers)
			except Exception:
				header_items = []
			for key, val in header_items:
				if str(key).lower() == "retry-after":
					try:
						retry_after = max(float(val), 1.0)
					except (ValueError, TypeError):
						pass
					break
		raise ShopifyGraphQLError(str(e), http_status=status, retry_after=retry_after) from e

	# Check for top-level errors array (schema/auth failures).
	# These are deterministic failures (bad query, missing field, expired
	# token, etc.) that won't recover on retry. We use http_status=-1 as a
	# sentinel so the retry wrapper can distinguish them from network errors
	# (http_status=None) and 5xx responses.
	if isinstance(result, dict) and result.get("errors"):
		raise ShopifyGraphQLError(
			f"GraphQL errors: {result['errors']}",
			http_status=-1,
			body=json.dumps(result),
		)
	return result


def set_inventory_batch(
	quantities: list[dict],
	store_name: str,
	timestamp_iso: str,
	logger=None,
	api_version: str | None = None,
	idempotency_key: str | None = None,
) -> BatchResult:
	"""
	Execute one inventorySetQuantities mutation for up to 250 quantities.

	Args:
		quantities: List of dicts with keys: item_code, inventory_item_id,
			location_id, qty. item_code is used to map userErrors back to
			source rows; it is NOT sent to Shopify.
		store_name: For referenceDocumentUri.
		timestamp_iso: For referenceDocumentUri.
		logger: Optional frappe logger.
		api_version: Effective Shopify Admin API version. Missing versions use
			the legacy input shape for backward compatibility.
		idempotency_key: Required for API versions from 2026-01 onward.

	Returns:
		BatchResult with per-item success/failure and throttle state.
	"""
	if not quantities:
		return BatchResult()

	# Shopify's inventorySetQuantities mutation accepts at most INVENTORY_BATCH_SIZE
	# items per call. Callers are expected to chunk via _chunked() before
	# invoking this function; enforce the contract explicitly so a future
	# caller that forgets to chunk fails fast with a clear message instead of
	# round-tripping an oversize payload to Shopify.
	if len(quantities) > INVENTORY_BATCH_SIZE:
		raise ValueError(
			f"set_inventory_batch received {len(quantities)} quantities, "
			f"exceeds Shopify's {INVENTORY_BATCH_SIZE}-item limit "
			f"(store={store_name}, timestamp={timestamp_iso}). "
			f"Chunk the input via _chunked() before calling."
		)

	uses_idempotent_contract = _uses_idempotent_inventory_set(api_version)
	if uses_idempotent_contract and not idempotency_key:
		raise ValueError(f"idempotency_key is required for Shopify API version {api_version}")

	graphql_quantities = [
		{
			"inventoryItemId": f"gid://shopify/InventoryItem/{q['inventory_item_id']}",
			"locationId": f"gid://shopify/Location/{q['location_id']}",
			"quantity": int(q["qty"]),
			**({"changeFromQuantity": None} if uses_idempotent_contract else {}),
		}
		for q in quantities
	]

	variables = {
		"input": {
			"name": "available",
			"reason": "correction",
			"referenceDocumentUri": f"nexwave://inventory-sync/{store_name}/{timestamp_iso}",
			"quantities": graphql_quantities,
		}
	}
	mutation = INVENTORY_SET_MUTATION
	if uses_idempotent_contract:
		mutation = IDEMPOTENT_INVENTORY_SET_MUTATION
		variables["idempotencyKey"] = idempotency_key
	else:
		variables["input"]["ignoreCompareQuantity"] = True

	response = execute_graphql(mutation, variables)

	data = (response or {}).get("data") or {}
	payload = data.get("inventorySetQuantities") or {}
	user_errors = payload.get("userErrors") or []
	throttle = ThrottleStatus.from_extensions((response or {}).get("extensions"))

	# Map userErrors back to source rows. A userError path looks like
	# ["input", "quantities", <index>, <field>]. A global error (e.g.
	# a problem with "input.name") has no index and fails the whole batch.
	failed_indexes: dict[int, list[str]] = {}
	global_errors: list[str] = []
	for err in user_errors:
		if not isinstance(err, dict):
			continue
		field_path = err.get("field") or []
		message = err.get("message") or "Unknown error"
		code = err.get("code")
		full_msg = f"{code}: {message}" if code else message

		quantity_index = None
		if isinstance(field_path, list) and len(field_path) >= 3 and field_path[1] == "quantities":
			try:
				quantity_index = int(field_path[2])
			except (ValueError, TypeError):
				quantity_index = None

		# Validate the index points to a real row in this request. An
		# out-of-range or negative index can't be mapped back to a source
		# item, so treating it as a per-row error would silently mark
		# every real row as succeeded. Escalate to a whole-batch failure.
		if quantity_index is not None and not (0 <= quantity_index < len(quantities)):
			global_errors.append(f"{full_msg} (unresolvable quantity index {quantity_index})")
			continue

		if quantity_index is None:
			global_errors.append(full_msg)
		else:
			failed_indexes.setdefault(quantity_index, []).append(full_msg)

	succeeded: list[str] = []
	failed: list[tuple[str, str]] = []

	if global_errors:
		# Whole batch failed
		combined = "; ".join(global_errors)
		for q in quantities:
			failed.append((q["item_code"], combined))
	else:
		for idx, q in enumerate(quantities):
			if idx in failed_indexes:
				failed.append((q["item_code"], "; ".join(failed_indexes[idx])))
			else:
				succeeded.append(q["item_code"])

	if logger and failed:
		logger.info(
			"Batch had %s failures (of %s) for store %s",
			len(failed),
			len(quantities),
			store_name,
		)

	return BatchResult(
		succeeded=succeeded,
		failed=failed,
		throttle=throttle,
		raw_response=response,
	)


def fetch_inventory_item_ids(
	variant_ids: list[str],
	logger=None,
) -> dict[str, dict]:
	"""
	Fetch inventory_item_id and tracked flag for up to 250 variants.

	Args:
		variant_ids: Numeric variant IDs (strings).

	Returns:
		Dict mapping numeric variant_id -> {
			"inventory_item_id": str | None,
			"tracked": bool,
		}
		Variants missing from the response are absent from the dict.
	"""
	if not variant_ids:
		return {}

	# Shopify's `nodes` query accepts at most NODES_BATCH_SIZE ids per call.
	# Callers in _resolve_inventory_item_ids chunk via _chunked() before
	# invoking this function; enforce the contract explicitly for future
	# callers.
	if len(variant_ids) > NODES_BATCH_SIZE:
		raise ValueError(
			f"fetch_inventory_item_ids received {len(variant_ids)} variant ids, "
			f"exceeds Shopify's {NODES_BATCH_SIZE}-item limit. "
			f"Chunk the input via _chunked() before calling."
		)

	gids = [f"gid://shopify/ProductVariant/{vid}" for vid in variant_ids]
	response = execute_graphql(VARIANT_NODES_QUERY, {"ids": gids})

	data = (response or {}).get("data") or {}
	nodes = data.get("nodes") or []

	result: dict[str, dict] = {}
	for node in nodes:
		if not node or not isinstance(node, dict):
			# Variant was deleted or not a ProductVariant; skipped
			continue
		variant_gid = node.get("id") or ""
		if not variant_gid:
			continue
		# Extract numeric variant_id from gid://shopify/ProductVariant/<id>
		variant_id = variant_gid.rsplit("/", 1)[-1]

		inventory_item = node.get("inventoryItem") or {}
		inventory_item_gid = (inventory_item or {}).get("id") or ""
		# Defensive: only extract when the GID matches the expected prefix,
		# so a malformed response can't produce a bogus numeric id.
		if inventory_item_gid.startswith("gid://shopify/InventoryItem/"):
			inventory_item_id = inventory_item_gid.rsplit("/", 1)[-1] or None
		else:
			inventory_item_id = None

		result[variant_id] = {
			"inventory_item_id": inventory_item_id,
			"tracked": bool(inventory_item.get("tracked")) if inventory_item else False,
		}

	if logger:
		missing = [vid for vid in variant_ids if vid not in result]
		if missing:
			logger.info(
				"fetch_inventory_item_ids: %s of %s variant(s) missing from response",
				len(missing),
				len(variant_ids),
			)

	return result

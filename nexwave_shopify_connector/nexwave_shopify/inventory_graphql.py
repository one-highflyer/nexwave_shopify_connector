# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""
GraphQL helpers for Shopify inventory sync.

Thin wrappers around the shopify SDK's GraphQL client, returning parsed dicts
and exposing throttle state. Called from within an existing
Session.temp() context; no auth handling here.
"""

import json
from dataclasses import dataclass, field
from typing import Any

import shopify

# Public constants
INVENTORY_BATCH_SIZE = 250
NODES_BATCH_SIZE = 250
THROTTLE_MIN_AVAILABLE = 200  # pause below this; typical bucket is 1000
DEFAULT_RESTORE_RATE = 50.0

INVENTORY_SET_MUTATION = """
mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup {
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
    }
  }
}
"""

VARIANT_NODES_QUERY = """
query variantInventoryItems($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id
      inventoryItem { id }
      inventoryManagement
    }
  }
}
"""


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
	Raises ShopifyGraphQLError on HTTP errors (with retry_after set if 429).
	"""
	try:
		client = shopify.GraphQL()
		result_str = client.execute(query=query, variables=variables)
		result = json.loads(result_str)
	except ShopifyGraphQLError:
		raise
	except Exception as e:
		# Translate HTTP errors from urllib / pyactiveresource
		status = getattr(e, "code", None) or getattr(e, "status_code", None)
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

	# Check for top-level errors array (schema/auth failures)
	if isinstance(result, dict) and result.get("errors"):
		raise ShopifyGraphQLError(
			f"GraphQL errors: {result['errors']}",
			http_status=None,
			body=json.dumps(result),
		)
	return result


def set_inventory_batch(
	quantities: list[dict],
	store_name: str,
	timestamp_iso: str,
	logger=None,
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

	Returns:
		BatchResult with per-item success/failure and throttle state.
	"""
	if not quantities:
		return BatchResult()

	graphql_quantities = [
		{
			"inventoryItemId": f"gid://shopify/InventoryItem/{q['inventory_item_id']}",
			"locationId": f"gid://shopify/Location/{q['location_id']}",
			"quantity": int(q["qty"]),
		}
		for q in quantities
	]

	variables = {
		"input": {
			"name": "available",
			"reason": "correction",
			"referenceDocumentUri": f"nexwave://inventory-sync/{store_name}/{timestamp_iso}",
			"ignoreCompareQuantity": True,
			"quantities": graphql_quantities,
		}
	}

	response = execute_graphql(INVENTORY_SET_MUTATION, variables)

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
	Fetch inventory_item_id and inventoryManagement for up to 250 variants.

	Args:
		variant_ids: Numeric variant IDs (strings).

	Returns:
		Dict mapping numeric variant_id -> {
			"inventory_item_id": str | None,
			"inventory_management": str,
		}
		Variants missing from the response are absent from the dict.
	"""
	if not variant_ids:
		return {}

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
		inventory_item_id = inventory_item_gid.rsplit("/", 1)[-1] if inventory_item_gid else None

		result[variant_id] = {
			"inventory_item_id": inventory_item_id,
			"inventory_management": node.get("inventoryManagement") or "",
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

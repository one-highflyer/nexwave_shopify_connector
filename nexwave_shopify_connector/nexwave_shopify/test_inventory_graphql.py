# Copyright (c) 2024, HighFlyer and contributors
# For license information, please see license.txt

"""Unit tests for inventory_graphql helpers.

These tests patch ``execute_graphql`` so the Shopify SDK is never called; the
behaviour under test is: variable shaping, response parsing, and throttle
bookkeeping.
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
	BatchResult,
	ShopifyGraphQLError,
	ThrottleStatus,
	execute_graphql,
	fetch_inventory_item_ids,
	set_inventory_batch,
)


class TestThrottleStatus(FrappeTestCase):
	def test_from_full_extensions(self):
		ext = {
			"cost": {
				"throttleStatus": {
					"currentlyAvailable": 750,
					"maximumAvailable": 1000,
					"restoreRate": 50.0,
				}
			}
		}
		t = ThrottleStatus.from_extensions(ext)
		self.assertEqual(t.currently_available, 750)
		self.assertEqual(t.maximum_available, 1000)
		self.assertEqual(t.restore_rate, 50.0)

	def test_from_missing_extensions_defaults_generous(self):
		t = ThrottleStatus.from_extensions(None)
		self.assertEqual(t.currently_available, 1000)
		self.assertEqual(t.restore_rate, 50.0)

	def test_from_partial_extensions(self):
		t = ThrottleStatus.from_extensions({"cost": {"throttleStatus": {"currentlyAvailable": 500}}})
		self.assertEqual(t.currently_available, 500)
		self.assertEqual(t.maximum_available, 1000)  # default


class TestSetInventoryBatch(FrappeTestCase):
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_all_succeed(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": {
						"createdAt": "2026-04-10T10:00:00Z",
						"reason": "correction",
						"changes": [],
					},
					"userErrors": [],
				}
			},
			"extensions": {
				"cost": {
					"throttleStatus": {
						"currentlyAvailable": 900,
						"maximumAvailable": 1000,
						"restoreRate": 50.0,
					}
				}
			},
		}
		quantities = [
			{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 10},
			{"item_code": "ITEM-B", "inventory_item_id": "1002", "location_id": "loc1", "qty": 0},
		]
		result = set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		self.assertEqual(result.succeeded, ["ITEM-A", "ITEM-B"])
		self.assertEqual(result.failed, [])
		self.assertEqual(result.throttle.currently_available, 900)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_partial_user_errors_mapped_by_index(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": None,
					"userErrors": [
						{
							"field": ["input", "quantities", "1", "quantity"],
							"message": "Inventory item not found",
							"code": "INVENTORY_ITEM_NOT_FOUND",
						}
					],
				}
			},
			"extensions": {
				"cost": {
					"throttleStatus": {
						"currentlyAvailable": 950,
						"maximumAvailable": 1000,
						"restoreRate": 50.0,
					}
				}
			},
		}
		quantities = [
			{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 10},
			{"item_code": "ITEM-B", "inventory_item_id": "1002", "location_id": "loc1", "qty": 0},
		]
		result = set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		# With a user error for index 1, ITEM-B fails, ITEM-A succeeds
		self.assertEqual(result.succeeded, ["ITEM-A"])
		self.assertEqual(len(result.failed), 1)
		self.assertEqual(result.failed[0][0], "ITEM-B")
		self.assertIn("Inventory item not found", result.failed[0][1])

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_global_user_error_fails_whole_batch(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": None,
					"userErrors": [
						{
							"field": ["input", "name"],
							"message": "Invalid quantity name",
							"code": "INVALID",
						}
					],
				}
			},
			"extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 950}}},
		}
		quantities = [
			{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 10},
		]
		result = set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		# When the error isn't for a specific quantity index, all fail
		self.assertEqual(result.succeeded, [])
		self.assertEqual(len(result.failed), 1)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_reference_document_uri_includes_store(self, mock_exec):
		mock_exec.return_value = {
			"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}},
			"extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 900}}},
		}
		quantities = [{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 5}]
		set_inventory_batch(quantities, "my-store.myshopify.com", "2026-04-10T10:00:00")
		variables = mock_exec.call_args[0][1]
		self.assertIn("my-store.myshopify.com", variables["input"]["referenceDocumentUri"])

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_quantities_formatted_as_gids(self, mock_exec):
		mock_exec.return_value = {
			"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}},
			"extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 900}}},
		}
		quantities = [{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 5}]
		set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		query = mock_exec.call_args[0][0]
		variables = mock_exec.call_args[0][1]
		qty_entry = variables["input"]["quantities"][0]
		self.assertEqual(qty_entry["inventoryItemId"], "gid://shopify/InventoryItem/1001")
		self.assertEqual(qty_entry["locationId"], "gid://shopify/Location/loc1")
		self.assertEqual(qty_entry["quantity"], 5)
		# I1: Assert other required payload fields sent to Shopify
		self.assertEqual(variables["input"]["name"], "available")
		self.assertIs(variables["input"]["ignoreCompareQuantity"], True)
		self.assertEqual(variables["input"]["reason"], "correction")
		self.assertNotIn("changeFromQuantity", qty_entry)
		self.assertNotIn("idempotencyKey", variables)
		self.assertNotIn("@idempotent", query)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_2024_01_uses_legacy_mutation_shape(self, mock_exec):
		mock_exec.return_value = {
			"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}}
		}
		quantities = [{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 5}]

		set_inventory_batch(
			quantities,
			"test.myshopify.com",
			"2026-04-10T10:00:00",
			api_version="2024-01",
		)

		query, variables = mock_exec.call_args[0]
		self.assertNotIn("$idempotencyKey", query)
		self.assertNotIn("@idempotent", query)
		self.assertNotIn("idempotencyKey", variables)
		self.assertIs(variables["input"]["ignoreCompareQuantity"], True)
		self.assertNotIn("changeFromQuantity", variables["input"]["quantities"][0])

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_2026_01_and_later_use_idempotent_mutation_shape(self, mock_exec):
		mock_exec.return_value = {
			"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}}
		}
		quantities = [{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 5}]

		for api_version in ("2026-01", "2026-04", "2026-07", "unstable"):
			with self.subTest(api_version=api_version):
				mock_exec.reset_mock()
				idempotency_key = f"inventory-batch-{api_version}"
				set_inventory_batch(
					quantities,
					"test.myshopify.com",
					"2026-04-10T10:00:00",
					api_version=api_version,
					idempotency_key=idempotency_key,
				)

				query, variables = mock_exec.call_args[0]
				self.assertIn("$idempotencyKey: String!", query)
				self.assertIn(
					"inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey)",
					query,
				)
				self.assertEqual(variables["idempotencyKey"], idempotency_key)
				self.assertNotIn("ignoreCompareQuantity", variables["input"])
				self.assertIsNone(variables["input"]["quantities"][0]["changeFromQuantity"])

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_modern_mutation_requires_idempotency_key(self, mock_exec):
		quantities = [{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 5}]

		with self.assertRaisesRegex(ValueError, "idempotency_key is required"):
			set_inventory_batch(
				quantities,
				"test.myshopify.com",
				"2026-04-10T10:00:00",
				api_version="2026-04",
			)

		mock_exec.assert_not_called()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_empty_quantities_returns_empty_batch_result(self, mock_exec):
		result = set_inventory_batch([], "test.myshopify.com", "2026-04-10T10:00:00")
		self.assertEqual(result.succeeded, [])
		self.assertEqual(result.failed, [])
		mock_exec.assert_not_called()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_out_of_range_quantity_index_fails_whole_batch(self, mock_exec):
		"""Regression: a userError with an out-of-range index must not silently
		mark real rows as succeeded. It must fail the whole batch."""
		mock_exec.return_value = {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": None,
					"userErrors": [
						{
							"field": ["input", "quantities", "999", "quantity"],
							"message": "Unresolvable row",
							"code": "SCHEMA",
						}
					],
				}
			},
			"extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 950}}},
		}
		quantities = [
			{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 10},
			{"item_code": "ITEM-B", "inventory_item_id": "1002", "location_id": "loc1", "qty": 0},
		]
		result = set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		self.assertEqual(result.succeeded, [])
		self.assertEqual(len(result.failed), 2)
		# Error message should mention the unresolvable index for ops visibility
		for _, msg in result.failed:
			self.assertIn("unresolvable quantity index 999", msg)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_negative_quantity_index_fails_whole_batch(self, mock_exec):
		"""A negative index is also out of range and must fail the batch."""
		mock_exec.return_value = {
			"data": {
				"inventorySetQuantities": {
					"inventoryAdjustmentGroup": None,
					"userErrors": [
						{
							"field": ["input", "quantities", "-1", "quantity"],
							"message": "Bad index",
						}
					],
				}
			},
			"extensions": {"cost": {"throttleStatus": {"currentlyAvailable": 950}}},
		}
		quantities = [
			{"item_code": "ITEM-A", "inventory_item_id": "1001", "location_id": "loc1", "qty": 10},
		]
		result = set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		self.assertEqual(result.succeeded, [])
		self.assertEqual(len(result.failed), 1)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_exceeding_batch_size_raises_value_error(self, mock_exec):
		"""A caller that forgets to chunk must fail fast, not round-trip an
		oversize payload to Shopify."""
		from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
			INVENTORY_BATCH_SIZE,
		)

		quantities = [
			{
				"item_code": f"ITEM-{i}",
				"inventory_item_id": str(1000 + i),
				"location_id": "loc1",
				"qty": 5,
			}
			for i in range(INVENTORY_BATCH_SIZE + 1)
		]
		with self.assertRaises(ValueError) as ctx:
			set_inventory_batch(quantities, "test.myshopify.com", "2026-04-10T10:00:00")
		self.assertIn(str(INVENTORY_BATCH_SIZE), str(ctx.exception))
		mock_exec.assert_not_called()


class TestFetchInventoryItemIds(FrappeTestCase):
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_maps_gids_to_numeric(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"nodes": [
					{
						"id": "gid://shopify/ProductVariant/2001",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3001", "tracked": True},
					},
					{
						"id": "gid://shopify/ProductVariant/2002",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3002", "tracked": True},
					},
				]
			}
		}
		result = fetch_inventory_item_ids(["2001", "2002"])
		self.assertIn("2001", result)
		self.assertEqual(result["2001"]["inventory_item_id"], "3001")
		self.assertTrue(result["2001"]["tracked"])

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_missing_variant_absent_from_result(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"nodes": [
					None,
					{
						"id": "gid://shopify/ProductVariant/2002",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3002", "tracked": True},
					},
				]
			}
		}
		result = fetch_inventory_item_ids(["2001", "2002"])
		self.assertNotIn("2001", result)
		self.assertIn("2002", result)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_exceeding_nodes_batch_raises_value_error(self, mock_exec):
		"""Same 250-item limit applies to the nodes lookup query."""
		from nexwave_shopify_connector.nexwave_shopify.inventory_graphql import (
			NODES_BATCH_SIZE,
		)

		variant_ids = [str(i) for i in range(NODES_BATCH_SIZE + 1)]
		with self.assertRaises(ValueError) as ctx:
			fetch_inventory_item_ids(variant_ids)
		self.assertIn(str(NODES_BATCH_SIZE), str(ctx.exception))
		mock_exec.assert_not_called()

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_not_tracked_still_returned_with_flag(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"nodes": [
					{
						"id": "gid://shopify/ProductVariant/2001",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3001", "tracked": False},
					}
				]
			}
		}
		result = fetch_inventory_item_ids(["2001"])
		self.assertFalse(result["2001"]["tracked"])


class TestExecuteGraphql(FrappeTestCase):
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_returns_parsed_json(self, mock_shopify):
		client = mock_shopify.GraphQL.return_value
		client.execute.return_value = '{"data": {"foo": "bar"}}'
		result = execute_graphql("query {}", {})
		self.assertEqual(result, {"data": {"foo": "bar"}})

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_raises_on_top_level_errors(self, mock_shopify):
		client = mock_shopify.GraphQL.return_value
		client.execute.return_value = '{"errors": [{"message": "boom"}]}'
		with self.assertRaises(ShopifyGraphQLError):
			execute_graphql("query {}", {})

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_translates_http_429(self, mock_shopify):
		from urllib.error import HTTPError

		client = mock_shopify.GraphQL.return_value
		err = HTTPError("url", 429, "rate limited", {"Retry-After": "3"}, None)
		client.execute.side_effect = err
		with self.assertRaises(ShopifyGraphQLError) as ctx:
			execute_graphql("query {}", {})
		self.assertEqual(ctx.exception.http_status, 429)
		self.assertEqual(ctx.exception.retry_after, 3.0)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_top_level_error_has_schema_sentinel(self, mock_shopify):
		"""Top-level GraphQL errors use http_status=-1 so the retry wrapper
		treats them as non-retryable schema/auth failures."""
		client = mock_shopify.GraphQL.return_value
		client.execute.return_value = '{"errors": [{"message": "Field X not found"}]}'
		with self.assertRaises(ShopifyGraphQLError) as ctx:
			execute_graphql("query {}", {})
		self.assertEqual(ctx.exception.http_status, -1)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_json_decode_error_is_non_retryable(self, mock_shopify):
		"""A malformed response (HTML error page, truncated JSON) must wrap
		as a non-retryable ShopifyGraphQLError, not as a transient
		http_status=None that the retry wrapper would retry."""
		client = mock_shopify.GraphQL.return_value
		client.execute.return_value = "<html>502 Bad Gateway</html>"
		with self.assertRaises(ShopifyGraphQLError) as ctx:
			execute_graphql("query {}", {})
		self.assertEqual(ctx.exception.http_status, -1)
		self.assertIn("parse", str(ctx.exception).lower())

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_non_transport_exception_propagates_unwrapped(self, mock_shopify):
		"""A code bug (e.g. AttributeError) must propagate up, not be
		silently wrapped as a retryable network error."""
		client = mock_shopify.GraphQL.return_value
		client.execute.side_effect = AttributeError("simulated code bug")
		with self.assertRaises(AttributeError):
			execute_graphql("query {}", {})

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.shopify")
	def test_oserror_wraps_as_transport(self, mock_shopify):
		"""ConnectionError (an OSError) must still be wrapped so the retry
		wrapper can retry it as a transient network issue."""
		client = mock_shopify.GraphQL.return_value
		client.execute.side_effect = ConnectionError("connection reset by peer")
		with self.assertRaises(ShopifyGraphQLError) as ctx:
			execute_graphql("query {}", {})
		self.assertIsNone(ctx.exception.http_status)

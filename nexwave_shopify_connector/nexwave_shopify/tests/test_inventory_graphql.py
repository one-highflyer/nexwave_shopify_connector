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
		variables = mock_exec.call_args[0][1]
		qty_entry = variables["input"]["quantities"][0]
		self.assertEqual(qty_entry["inventoryItemId"], "gid://shopify/InventoryItem/1001")
		self.assertEqual(qty_entry["locationId"], "gid://shopify/Location/loc1")
		self.assertEqual(qty_entry["quantity"], 5)
		# I1: Assert other required payload fields sent to Shopify
		self.assertEqual(variables["input"]["name"], "available")
		self.assertIs(variables["input"]["ignoreCompareQuantity"], True)
		self.assertEqual(variables["input"]["reason"], "correction")

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_empty_quantities_returns_empty_batch_result(self, mock_exec):
		result = set_inventory_batch([], "test.myshopify.com", "2026-04-10T10:00:00")
		self.assertEqual(result.succeeded, [])
		self.assertEqual(result.failed, [])
		mock_exec.assert_not_called()


class TestFetchInventoryItemIds(FrappeTestCase):
	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_maps_gids_to_numeric(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"nodes": [
					{
						"id": "gid://shopify/ProductVariant/2001",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3001"},
						"inventoryManagement": "SHOPIFY",
					},
					{
						"id": "gid://shopify/ProductVariant/2002",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3002"},
						"inventoryManagement": "SHOPIFY",
					},
				]
			}
		}
		result = fetch_inventory_item_ids(["2001", "2002"])
		self.assertIn("2001", result)
		self.assertEqual(result["2001"]["inventory_item_id"], "3001")
		self.assertEqual(result["2001"]["inventory_management"], "SHOPIFY")

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_missing_variant_absent_from_result(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"nodes": [
					None,
					{
						"id": "gid://shopify/ProductVariant/2002",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3002"},
						"inventoryManagement": "SHOPIFY",
					},
				]
			}
		}
		result = fetch_inventory_item_ids(["2001", "2002"])
		self.assertNotIn("2001", result)
		self.assertIn("2002", result)

	@patch("nexwave_shopify_connector.nexwave_shopify.inventory_graphql.execute_graphql")
	def test_not_managed_still_returned_with_flag(self, mock_exec):
		mock_exec.return_value = {
			"data": {
				"nodes": [
					{
						"id": "gid://shopify/ProductVariant/2001",
						"inventoryItem": {"id": "gid://shopify/InventoryItem/3001"},
						"inventoryManagement": "NOT_MANAGED",
					}
				]
			}
		}
		result = fetch_inventory_item_ids(["2001"])
		self.assertEqual(result["2001"]["inventory_management"], "NOT_MANAGED")


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

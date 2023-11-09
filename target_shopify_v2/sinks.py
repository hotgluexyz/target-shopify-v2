"""TargetShopifyV2 target sink class, which handles writing streams."""

from target_shopify_v2.graphql_client import shopifyGraphQLV2Sink


class SalesOrdersSink(shopifyGraphQLV2Sink):
    name = "SalesOrders"

    def upsert_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        state_updates = dict()
        if record:
            order_id = self.upload_order(record)
            self.logger.info(f"Returning {order_id}, True, {state_updates}")
            return order_id, True, state_updates

class ProductsSink(shopifyGraphQLV2Sink):
    name = "Products"

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = dict()
        if record:
            res = self.upload_product(record)
            product_id = res.get("data", {}).get("productCreate", {}).get("product", {}).get("id")
            self.logger.info(f"Returning {product_id}, True, {state_updates}")
            return product_id, True, state_updates

class UpdateInventorySink(shopifyGraphQLV2Sink):
    name = "UpdateInventory"

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = dict()
        if record:
            res = self.update_inventory(record)
            self.logger.info(f"Returning {res}, True, {state_updates}")
            return res, True, state_updates
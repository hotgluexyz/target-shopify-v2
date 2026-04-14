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

class FulfillmentsSink(shopifyGraphQLV2Sink):
    name = "Fulfillments"

    def upsert_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        state_updates = dict()
        if record:
            record["fulfilled"] = True
            fulfillment_id = self.fulfil_order(record, record.get("order_id"))
            self.logger.info(f"Returning {fulfillment_id}, True, {state_updates}")
            return fulfillment_id, True, state_updates

class ProductsSink(shopifyGraphQLV2Sink):
    name = "Products"

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = dict()
        if record:
            product_id = self.upload_product(record)
            self.logger.info(f"Returning {product_id}, True, {state_updates}")
            return product_id, True, state_updates

class ProductsDeleteSink(shopifyGraphQLV2Sink):
    name = "Products:delete"

    def upsert_record(self, record: dict, context: dict):
        state_updates = dict()
        if record:
            deleted_id = self.delete_product(record)
            self.logger.info(f"Deleted product {deleted_id}")
            return deleted_id, True, state_updates

class UpdateInventorySink(shopifyGraphQLV2Sink):
    name = "UpdateInventory"

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = dict()
        if record:
            res = self.update_inventory(record)
            self.logger.info(f"Returning {res}, True, {state_updates}")
            return res, True, state_updates
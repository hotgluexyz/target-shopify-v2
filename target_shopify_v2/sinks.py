"""TargetShopifyV2 target sink class, which handles writing streams."""

from target_shopify_v2.graphql_client import shopifyGraphQLV2Sink


class SalesOrdersSink(shopifyGraphQLV2Sink):
    name = "SalesOrders"

    def upsert_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        state_updates = dict()
        if record:
            self.upload_order(record)
            self.logger.info(f"Returning {id}, True, {state_updates}")
            return id, True, state_updates

class ProductsSink(shopifyGraphQLV2Sink):
    name = "Products"

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = dict()
        if record:
            self.upload_product(record)
            self.logger.info(f"Returning {id}, True, {state_updates}")
            return id, True, state_updates

class UpdateInventorySink(shopifyGraphQLV2Sink):
    name = "UpdateInventory"

    def upsert_record(self, record: dict, context: dict) -> None:
        state_updates = dict()
        if record:
            self.update_inventory(record)
            self.logger.info(f"Returning {id}, True, {state_updates}")
            return id, True, state_updates
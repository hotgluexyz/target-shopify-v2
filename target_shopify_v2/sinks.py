"""TargetShopifyV2 target sink class, which handles writing streams."""

from target_shopify_v2.graphql_client import shopifyGraphQLV2Sink


class TargetShopifyV2Sink(shopifyGraphQLV2Sink):
    def process_record(self, record: dict, context: dict) -> None:
        if self.stream_name == "SalesOrders":
            self.upload_order(record)
        if self.stream_name == "Products":
            self.upload_product(record)
        if self.stream_name == "UpdateInventory":
            self.update_inventory(record)

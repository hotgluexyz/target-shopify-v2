"""TargetShopifyV2 target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from target_shopify_v2.sinks import (
    SalesOrdersSink,
    FulfillmentsSink,
    UpdateInventorySink,
    ProductsSink,
    ProductsDeleteSink,
)


class TargetTargetShopifyV2(TargetHotglue):
    """Sample target for TargetShopifyV2."""

    name = "target-shopify-v2"
    config_jsonschema = th.PropertiesList(
        th.Property("shop", th.StringType, required=True)
    ).to_dict()
    SINK_TYPES = [SalesOrdersSink, FulfillmentsSink, UpdateInventorySink, ProductsSink, ProductsDeleteSink]


if __name__ == "__main__":
    TargetTargetShopifyV2.cli()

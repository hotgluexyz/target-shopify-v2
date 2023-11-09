"""TargetShopifyV2 target class."""

from singer_sdk import typing as th
from singer_sdk.target_base import Target
from target_hotglue.target import TargetHotglue
from target_shopify_v2.sinks import (
    SalesOrdersSink,
    UpdateInventorySink,
    ProductsSink
)


class TargetTargetShopifyV2(TargetHotglue):
    """Sample target for TargetShopifyV2."""

    name = "target-shopify-v2"
    config_jsonschema = th.PropertiesList(
        th.Property("shop", th.StringType, required=True)
    ).to_dict()
    SINK_TYPES = [SalesOrdersSink, UpdateInventorySink, ProductsSink]


if __name__ == "__main__":
    TargetTargetShopifyV2.cli()

"""TargetShopifyV2 target class."""

from singer_sdk import typing as th
from singer_sdk.target_base import Target

from target_shopify_v2.sinks import TargetShopifyV2Sink


class TargetTargetShopifyV2(Target):
    """Sample target for TargetShopifyV2."""

    name = "target-shopify-v2"
    config_jsonschema = th.PropertiesList(
        th.Property("shop", th.StringType, required=True),
        th.Property("api_key", th.StringType, required=True),
    ).to_dict()
    default_sink_class = TargetShopifyV2Sink


if __name__ == "__main__":
    TargetTargetShopifyV2.cli()

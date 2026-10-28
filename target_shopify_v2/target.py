"""TargetShopifyV2 target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from target_shopify_v2.auth import ShopifyOAuthAuthenticator, shopify_token_url
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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # The SDK's --access-token flow reads `config_file`; TargetHotglue only sets `_config_file_path`.
        self.config_file = self._config_file_path

    @classmethod
    def access_token_support(cls, connector=None):
        """Authenticator and token URL for token refresh and the ``--access-token`` CLI.

        The SDK calls this with no connector to detect support, before ``shop`` is known.
        """
        if connector is None:
            return ShopifyOAuthAuthenticator, "https://{shop}.myshopify.com/admin/oauth/access_token"
        return ShopifyOAuthAuthenticator, shopify_token_url(connector.config)


if __name__ == "__main__":
    TargetTargetShopifyV2.cli()

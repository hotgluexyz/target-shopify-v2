"""Colocated smoke-test runner for target-shopify-v2 (hotglue-smoke-test)."""

import vcr
from hotglue_smoke_test.vcr.target import VCRTargetTestRunner


class TargetShopifyV2TestRunner(VCRTargetTestRunner):
    PRESERVE_KEYS = {
        "id",
        "sku",
        "externalId",
        "message"
    }

    def module(self) -> str:
        return "target_shopify_v2"

    def launch(self):
        from target_shopify_v2.target import TargetTargetShopifyV2

        TargetTargetShopifyV2.cli()

    def vcr_use_cassette(self, filter_query_parameters):
        # BC batches OData in JSON bodies; default VCR matching is too loose for replay.
        return vcr.use_cassette(
            self.vcr_cassette_path,
            decode_compressed_response=True,
            filter_headers=[
                *self.FILTER_HEADERS,
                ("X-Shopify-Access-Token", "REDACTED"),
                ("x-shopify-access-token", "REDACTED"),
            ],
            filter_post_data_parameters=list(self.TOKEN_KEYS),
            filter_query_parameters=filter_query_parameters,
            match_on=["method", "scheme", "host", "port", "path", "query", "body"],
            before_record_response=self.before_record_response,
        )


if __name__ == "__main__":
    TargetShopifyV2TestRunner.main()

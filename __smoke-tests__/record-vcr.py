import vcr

from hotglue_smoke_test.vcr.target import VCRTargetTestRunner


class TargetShopifyV2TestRunner(VCRTargetTestRunner):
    def module(self) -> str:
        return "target_shopify_v2"

    def launch(self):
        from target_shopify_v2.target import TargetTargetShopifyV2

        TargetTargetShopifyV2.cli()

    def vcr_use_cassette(self, filter_query_parameters):
        my_vcr = vcr.VCR()
        return my_vcr.use_cassette(
            self.vcr_cassette_path,
            decode_compressed_response=True,
            filter_headers=["X-Shopify-Access-Token"],
            filter_post_data_parameters=list(self.TOKEN_KEYS),
            filter_query_parameters=filter_query_parameters,
            match_on=["method", "scheme", "host", "port", "path", "query", "body"],
        )


if __name__ == "__main__":
    TargetShopifyV2TestRunner.main()

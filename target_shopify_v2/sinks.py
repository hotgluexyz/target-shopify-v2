"""TargetShopifyV2 target sink class, which handles writing streams."""


import json

import requests
from singer_sdk.sinks import RecordSink

from target_shopify_v2.mapping import UnifiedMapping


class TargetShopifyV2Sink(RecordSink):
    @property
    def base_url(self):
        return f"https://{self.config.get('shop')}.myshopify.com/admin/api/2021-07/graphql.json"

    def get_http_headers(self):
        headers = {}
        headers["X-Shopify-Access-Token"] = str(self.config.get("access_token"))
        headers["Content-Type"] = "application/json"
        return headers

    def deploy_mutation(self, mutation, variables, input_name="input"):
        res = requests.post(
            url=self.base_url,
            json={"query": mutation, "variables": variables},
            headers=self.get_http_headers(),
        )
        return res.json()

    def upload_order(self, record):
        mapping = UnifiedMapping()
        payload = mapping.prepare_payload(record, "sale_orders", target="shopify")
        mutation = """ 
                mutation draftOrderCreate($input: DraftOrderInput!) {
                draftOrderCreate(input: $input) {
                    draftOrder {
                    id
                    }
                }
                }
        """
        res = self.deploy_mutation(mutation, {"input": payload})
        print(json.dumps(res))

    def upload_product(self, record):
        mapping = UnifiedMapping()
        payload = mapping.prepare_payload(record, "products", target="shopify")
        mutation = """ 
                mutation productCreate($input: ProductInput!) {
                productCreate(input: $input) {
                    product {
                    id
                    }
                }
                }
        """
        res = self.deploy_mutation(mutation, {"input": payload})
        print(json.dumps(res))

    def process_record(self, record: dict, context: dict) -> None:
        if self.stream_name == "sale_orders":
            self.upload_order(record)
        if self.stream_name == "products":
            self.upload_product(record)

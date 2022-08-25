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
        headers["X-Shopify-Access-Token"] = str(self.config.get("api_key"))
        headers["Content-Type"] = "application/json"
        return headers

    def deploy_mutation(self, mutation, variables, input_name="input"):
        res = requests.post(
            url=self.base_url,
            json={"query": mutation, "variables": variables},
            headers=self.get_http_headers(),
        )
        return res.json()

    def shopify_query(self, query, variables, input_name="input"):
        res = requests.post(
            url=self.base_url,
            json={"query": query, "variables": variables},
            headers=self.get_http_headers(),
        )
        return res.json()

    def upload_order(self, record):
        mapping = UnifiedMapping()
        if "customer_name" in record:
            customer = self.query_customers(record["customer_name"])
            if "data" in customer:
                if "customers" in customer["data"]:
                    if "edges" in customer["data"]["customers"]:
                        if len(customer["data"]["customers"]["edges"])>0:
                            customer = customer["data"]["customers"]["edges"][0]["node"]
                            record["customer_id"] = customer["id"]
                            if customer["email"]:
                                record["email"] = customer["email"]
        payload = mapping.prepare_payload(record, "sale_orders", target="shopify")
        payload = self.order_lookups(payload)
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
        self.post_message(res)

    def upload_product(self, record):
        mapping = UnifiedMapping()
        if "location" in record:
            if "name" in record["location"]:
                location = self.query_locations(f"name:{record['location']['name']}")
                if "data" in location:
                    if len(location["data"]["locations"]["edges"])>0:
                        location = location["data"]["locations"]["edges"][0]["node"]
                        record["location"]["id"] = location["id"]
                        record["location"]["name"] = location["name"]
                
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
        self.post_message(res)

    def order_lookups(self,payload):
        lineitems = payload["lineItems"]
        new_lineItems = []
        for lineitem in lineitems:
            new_item = lineitem
            if "id" not in lineitem:
                item = self.query_variants(f"sku:{lineitem['sku']}")
                if "edges" in item["data"]["productVariants"]:
                    if len(item["data"]["productVariants"]["edges"])>0:
                        item = item["data"]["productVariants"]["edges"][0]["node"]
                        new_item.update({"variantId":item["id"],"sku":item["sku"],"title":item["title"]})
            new_lineItems.append(new_item)
        payload["lineItems"] = new_lineItems   
        return payload                

    

    def query_customers(self,filter):
        query = """ 
                query($filter:String){
                customers(first: 10, query: $filter) {
                    edges {
                    node {
                        id
                        displayName
                        email
                    }
                }
                }
                }  
        """ 
        return self.shopify_query(query, {"filter": filter})

    def query_variants(self,filter):
        query = """ 
                query($filter:String){
                productVariants(first: 10, query: $filter) {
                    edges {
                    node {
                        id
                        sku
                        title
                    }
                }
                }
                }  
        """ 
        return self.shopify_query(query, {"filter": filter})

    def query_locations(self,filter):
        query = """ 
                query($filter:String){
                locations(first: 10, query: $filter) {
                    edges {
                    node {
                        id
                        name
                    }
                }
                }
                }  
        """ 
        return self.shopify_query(query, {"filter": filter})
    def query_pducts(self, filter):
        query = """ 
                query($filter:String){
                products(first: 10, query: $filter) {
                    edges {
                    node {
                        id
                        title
                    }
                }
                }
                }  
        """ 
        return self.shopify_query(query, {"filter": filter})

    def process_record(self, record: dict, context: dict) -> None:
        if self.stream_name == "SalesOrders":
            self.upload_order(record)
        if self.stream_name == "Products":
            self.upload_product(record)

    def post_message(self, res):
        if "errors" in res:
            raise Exception(res["errors"])
        print(json.dumps(res))

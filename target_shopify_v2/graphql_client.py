"""TargetShopifyV2 target sink class, which handles writing streams."""


import json
from itertools import product

import requests
from singer_sdk.sinks import RecordSink

from target_shopify_v2.mapping import UnifiedMapping


class shopifyGraphQLV2Sink(RecordSink):
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
                        if len(customer["data"]["customers"]["edges"]) > 0:
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
        res = res["data"]["draftOrderCreate"]["draftOrder"]
        # Check if order needs to be completed
        completed = self.complete_order(record, res, payload)
        # completed = {"data":{"draftOrderComplete":{"draftOrder":{"order":{"id":"gid://shopify/Order/4975640084700"}}}}}
        if "order" in completed["data"]["draftOrderComplete"]["draftOrder"]:
            # Check and fulfil order if there were no errors
            self.fulfil_order(
                record,
                completed["data"]["draftOrderComplete"]["draftOrder"]["order"]["id"],
                payload,
            )

        # Check if order is fully paid
        # self.mark_order_paid(record,res,payload)

    def fulfil_order(self, record, order_id, payload=None):
        try:
            mutation = """ 
                mutation fulfillmentCreateV2($fulfillment: FulfillmentV2Input!) {
                fulfillmentCreateV2(fulfillment: $fulfillment) {
                        fulfillment {
                            # Fulfillment fields
                            id
                        }
                        userErrors {
                                field
                                message
                        }
                    }
                }
        """
            fulfill_items = []
            if "fulfilled" in record:
                if record["fulfilled"] is True:
                    order_details = self.query_order(order_id)
                    if "order" in order_details["data"]:
                        line_items = order_details["data"]["order"][
                            "fulfillmentOrders"
                        ]["edges"]
                        fulfill_item = {"fulfillmentOrderId": order_id}
                        for line_item in line_items:
                            fulfill_item["fulfillmentOrderId"] = line_item["node"]["id"]
                            fulfill_items.append(fulfill_item)
            fulfillment_payload = {"lineItemsByFulfillmentOrder": fulfill_items}
            res_return = self.deploy_mutation(
                mutation, {"fulfillment": fulfillment_payload}
            )
            self.post_message(res_return)
        except Exception:
            raise Exception

    def query_order(self, order_id):
        query = """ 
                query($id:ID!){
                    order(id: $id) {
                        name
                        id
                        lineItems(first:10){
                            edges{
                                node{
                                    id
                                    name
                                    quantity
                                }
                            }
                        }
                        fulfillments(first:50){
                            id    
                        }
                        fulfillmentOrders(first:100){
                            edges{
                                node{
                                    id
                                }
                            }
                        }
                    }
                } 
        """
        return self.shopify_query(query, {"id": order_id})

    def complete_order(self, record, res, payload=None):
        res_return = {}
        mutation = """ 
                mutation draftOrderComplete($id: ID!) {
                    draftOrderComplete(id: $id) {
                        draftOrder {
                            id
                        order {
                            id
                        }
                        }
                        userErrors {
                                field
                                message
                            }
                    }
                }
        """
        if "status" in record:
            if record["status"] == "completed":
                res_return = self.deploy_mutation(mutation, {"id": res["id"]})
                self.post_message(res_return)
        return res_return

    def mark_order_paid(self, record, res, payload=None):
        mutation = """ 
                mutation draftOrderComplete($input: OrderMarkAsPaidInput!) {
                    orderMarkAsPaid(input: $input) {
                        	order {
                                id
                            }
                            
                    }
                }
        """
        if "delivery_status" in record:
            if record["delivery_status"] == "delivered":
                res = self.deploy_mutation(mutation, {"input": {"id": res["id"]}})
                self.post_message(res)

    def upload_product(self, record):
        mapping = UnifiedMapping()
        if "location" in record:
            if "name" in record["location"]:
                location = self.query_locations(f"name:{record['location']['name']}")
                if "data" in location:
                    if len(location["data"]["locations"]["edges"]) > 0:
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

    def order_lookups(self, payload):
        lineitems = payload["lineItems"]
        new_lineItems = []
        for lineitem in lineitems:
            new_item = lineitem
            if "id" not in lineitem:
                item = self.query_variants(f"sku:{lineitem['sku']}")
                if "edges" in item["data"]["productVariants"]:
                    if len(item["data"]["productVariants"]["edges"]) > 0:
                        item = item["data"]["productVariants"]["edges"][0]["node"]
                        new_item.update(
                            {
                                "variantId": item["id"],
                                "sku": item["sku"],
                                "title": item["title"],
                            }
                        )
            new_lineItems.append(new_item)
        payload["lineItems"] = new_lineItems
        return payload

    def query_customers(self, filter):
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

    def query_variants(self, filter):
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

    def query_locations(self, filter):
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
                        totalInventory
                        tracksInventory
                        variants(first: 10){
                            edges{
                                node{
                                    id
                                    inventoryItem{
                                        id
                                        sku
                                        inventoryLevels(first:1){
                                            edges{
                                                node{
                                                    id
                                                    available
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        
                    }
                }
            }
            }  
        """
        return self.shopify_query(query, {"filter": filter})

    def get_product_filter_key(self, item):
        if len(item["id"]) > 0:
            return {"key": "product_id", "val": item["id"]}
        elif len(item["sku"]) > 0:
            return {"key": "sku", "val": item["sku"]}
        elif len(item["product_name"]) > 0:
            return {"key": "title", "val": item["product_name"]}

    def extract_product(self, detail):
        product = None
        if "errors" in detail:
            return None
        product = detail
        if len(detail["data"]["products"]["edges"]) > 0:
            product = detail["data"]["products"]["edges"][0]["node"]
            if len(product["variants"]["edges"]) > 0:
                inventory_item = product["variants"]["edges"][0]["node"][
                    "inventoryItem"
                ]
                if len(inventory_item["inventoryLevels"]["edges"]) > 0:
                    product["inventory_level"] = inventory_item["inventoryLevels"][
                        "edges"
                    ][0]["node"]
        return product

    def update_product_mutation(self, level_id, quantity):

        mutation = """ 
                mutation M($input: InventoryAdjustQuantityInput!) {
                	inventoryAdjustQuantity(input: $input) {
                  	inventoryLevel {
                    	id
                    	available
                    	incoming
                    	item {
                      	id
                      	sku
                    	}
                    	location {
                      	id
                      	name
                    	}
                  	}
                	}
              	}
        """
        res = self.deploy_mutation(
            mutation,
            {"input": {"inventoryLevelId": level_id, "availableDelta": quantity}},
        )
        self.post_message(res)

    def update_inventory(self, item):
        operation = "add"
        filter_key = self.get_product_filter_key(item)
        product = self.query_pducts(f"{filter_key['key']}:{filter_key['val']}")
        product = self.extract_product(product)
        if "operation" in item:
            operation = item["operation"]
        if operation == "subtract":
            quantity = int(f"-{item['quantity']}")
        else:
            quantity = int(item["quantity"])
        if "inventory_level" in product:
            self.update_product_mutation(product["inventory_level"]["id"], quantity)

    def post_message(self, res):
        if "errors" in res:
            raise Exception(res["errors"])
        print(json.dumps(res))

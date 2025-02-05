"""TargetShopifyV2 target sink class, which handles writing streams."""


import json
import re

import requests
from singer_sdk.sinks import RecordSink

from target_shopify_v2.mapping import UnifiedMapping
from singer_sdk.plugin_base import PluginBase
from typing import Dict, List, Optional


class shopifyGraphQLV2Sink(RecordSink):
    def __init__(
        self,
        target: PluginBase,
        stream_name: str,
        schema: Dict,
        key_properties: Optional[List[str]],
    ) -> None:
        super().__init__(target, stream_name, schema, key_properties)
        self.shop_id = self.infer_shop_id()


    def infer_shop_id(self):
        query = """
        {
            shop {
                name
                id
            }
        }
        """

        response = requests.post(
            url=f"https://{self.config.get('shop')}.myshopify.com/admin/api/2021-07/graphql.json",
            json={"query": query},
            headers=self.get_http_headers(),
        )

        shop_id = None
        if hasattr(response, 'url'):
            shop_url = response.url
            shop_id = shop_url.split('/')[2].split('.')[0]
        else:
            self.logger.warning(f"Failed to fetch shop_id via GraphQL. Falling back to provided shop name: {self.config.get('shop')}")
            shop_id = self.config.get('shop')

        return shop_id

    @property
    def base_url(self):
        return f"https://{self.shop_id}.myshopify.com/admin/api/2021-07/graphql.json"

    def get_http_headers(self):
        headers = {}
        # NOTE: We are defaulting to using OAuth access token first, then falling back to API Key
        headers["X-Shopify-Access-Token"] = str(
            self.config.get("access_token", self.config.get("api_key"))
        )
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

        if "id" in record and "order_number" in record:
            self.update_order_by_id(record)
        if "id" in record and not "order_number" in record:
            self.update_order_by_id(record)
        if "id" not in record and "order_number" in record:
            self.update_order_by_number(record)

        if not "id" in record:
            if not "order_number" in record:
                if "customer_name" in record:
                    if record["customer_name"] is not None:
                        customer = self.query_customers(record["customer_name"])
                        if "data" in customer:
                            if customer["data"] is not None:
                                if "customers" in customer["data"]:
                                    if "edges" in customer["data"]["customers"]:
                                        if (
                                            len(customer["data"]["customers"]["edges"])
                                            > 0
                                        ):
                                            customer = customer["data"]["customers"][
                                                "edges"
                                            ][0]["node"]
                                            record["customer_id"] = customer["id"]
                                            if customer["email"]:
                                                record["email"] = customer["email"]
                payload = mapping.prepare_payload(
                    record, "sale_orders", target="shopify"
                )
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
                if (
                    completed
                    and "order" in completed["data"]["draftOrderComplete"]["draftOrder"]
                ):
                    # Check and fulfil order if there were no errors
                    self.fulfil_order(
                        record,
                        completed["data"]["draftOrderComplete"]["draftOrder"]["order"][
                            "id"
                        ],
                        payload,
                    )

            # Check if order is fully paid
            # self.mark_order_paid(record,res,payload)

    def update_order_by_number(self, record):
        order = self.query_order_by_name(record.get("order_number"))
        self.fulfil_order(record, order["data"]["orders"]["edges"][0]["node"]["id"])

    def update_order_by_id(self, record):
        order = self.query_order(record.get("id"))
        self.fulfil_order(
            record,
            order["data"]["order"]["id"],
        )

    def fulfil_order(self, record, order_id, payload=None):
        try:
            mutation = """
                mutation fulfillmentCreateV2($fulfillment: FulfillmentV2Input!) {
                fulfillmentCreateV2(fulfillment: $fulfillment) {
                        fulfillment {
                            # Fulfillment fields
                            id
                            trackingInfo {
                                # TrackingInfo fields
                                number
                                company
                            }
                        }
                        userErrors {
                                field
                                message
                        }
                    }
                }
        """
            fulfill_items = []
            tracking_info = None
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
                    tracking_info = {
                        "company": record.get("carrier"),
                        "number": record.get("tracking_number"),
                    }

            fulfillment_payload = {
                "lineItemsByFulfillmentOrder": fulfill_items,
                "trackingInfo": tracking_info,
            }
            res_return = self.deploy_mutation(
                mutation, {"fulfillment": fulfillment_payload}
            )
            self.post_message(res_return)
        except Exception:
            raise Exception

    def query_sku(self, sku):
        query = """
            query tapShopify($first: Int, $query: String) {
                productVariants(first: $first, query: $query) {
                    edges {
                        cursor node {
                            id
                            product {
                                id
                            }
                        }
                    },
                pageInfo { hasNextPage }
                }
            }
        """
        return self.shopify_query(query, {"first": 1, "query": sku})

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
                            trackingInfo{
                                number
                                company

                            }
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
        location = None
        locations = self.query_locations(None)

        record["variants"] = record.get("variants") or []

        product_id = record.get("id")
        for variant in record["variants"]:
            sku = variant.get("sku")
            if sku:
                ids = self.query_sku(sku)
                try:
                    ids = ids["data"]["productVariants"]["edges"]
                except KeyError:
                    ids = None
                if ids:
                    ids = ids[0]["node"]
                    variant["id"] = ids["id"]
                    product_id = ids["product"]["id"]
        record["id"] = product_id

        if "data" in locations:
            if len(locations["data"]["locations"]["edges"]) > 0:
                locations = locations["data"]["locations"]["edges"]
                valid_locations = [
                    l["node"] for l in locations if l["node"]["isActive"]
                ]
                if len(valid_locations) == 1:
                    location = valid_locations[0]

                elif "location" in record:
                    if "name" in record["location"]:
                        location = next(
                            (
                                l
                                for l in valid_locations
                                if l["name"] == record["location"]["name"]
                            ),
                            None,
                        )
        if location:
            record["location"] = dict(id=location["id"], name=location["name"])
        else:
            raise NameError("Missing location")

        payload = mapping.prepare_payload(record, "products", target="shopify")

        # fix the id if missing prefix
        if payload.get("id"):

            if "gid://shopify/Product/" not in payload["id"]:
                payload["id"] = "gid://shopify/Product/" + str(payload["id"])

            variants_update = []
            variants_create = []
            if payload.get("variants"):
                variants = payload.pop("variants")
                for variant in variants:
                    variant.pop("title")
                    if "id" in variant:
                        variants_update.append(variant)
                    else:
                        variants_create.append(variant)

            mutation = """
                mutation productUpdate($input: ProductInput!) {
                productUpdate(input: $input) {
                    product {
                    id
                    }
                }
                }"""
            res = self.deploy_mutation(mutation, {"input": payload})
            self.post_message(res)

            if variants_update:
                mutation = """
                    mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
                    productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                        product
                        {
                            id
                        }
                        productVariants {
                            id
                        }
                    }
                    }"""
                res = self.deploy_mutation(
                    mutation, {"productId": payload["id"], "variants": variants_update}
                )
                self.post_message(res)

            if variants_create:
                mutation = """
                    mutation productVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
                    productVariantsBulkCreate(productId: $productId, variants: $variants) {
                        product
                        {
                            id
                        }
                        productVariants {
                            id
                        }
                    }
                    }"""
                res = self.deploy_mutation(
                    mutation, {"productId": payload["id"], "variants": variants_create}
                )
                self.post_message(res)

        else:
            mutation = """
                    mutation productCreate($input: ProductInput!) {
                    productCreate(input: $input) {
                        product {
                        id
                        }
                    }
                    }"""
            res = self.deploy_mutation(mutation, {"input": payload})
            self.post_message(res)

    def order_lookups(self, payload):
        lineitems = payload["lineItems"]
        new_lineItems = []
        for lineitem in lineitems:
            new_item = lineitem
            if "id" not in lineitem:
                if "sku" in lineitem:
                    item = self.query_variants(f"sku:{lineitem['sku']}")
                elif "title" in lineitem:
                    item = self.query_variants(f"title:{lineitem['title']}")
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

    def query_order_by_name(self, filter):
        query = """
               query($filter:String){
                orders(first: 10, query: $filter) {
                    edges {
                    node {
                        id
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
                locations(first: 25, query: $filter) {
                    edges {
                    node {
                        id
                        name
                        isActive
                    }
                }
                }
                }
        """
        return self.shopify_query(query, {"filter": filter})

    def query_products(self, filter):
        if "variant_id" in filter:
            query = """
                query tapShopify($id: ID!) {
                    productVariant(id: $id) {
                        id
                        product {
                            id
                        }
                        inventoryItem{
                            id
                            sku
                            inventoryLevels(first:1){
                                edges{
                                    node{
                                        id
                                        location {
                                            id
                                        }
                                        quantities(names: ["available"]) {
                                            name
                                            quantity
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            """
            return self.shopify_query(
                query,
                {"id": "gid://shopify/ProductVariant/" + re.findall("\d+", filter)[0]},
            )
        else:
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
                                                        location {
                                                            id
                                                        }
                                                        quantities(names: ["available"]) {
                                                            name
                                                            quantity
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
                }
            """
            return self.shopify_query(query, {"filter": filter})

    def get_product_filter_key(self, item):
        if len(item.get("variant_id", [])) > 0:
            return {"key": "variant_id", "val": eval(item["variant_id"])}
        if len(item["sku"]) > 0:
            return {"key": "sku", "val": item["sku"]}
        elif len(item["product_name"]) > 0:
            return {"key": "title", "val": item["product_name"]}

    def get_inventory_levels(self, detail):
        if "errors" in detail:
            return None

        inventories = []
        inventory_item = None

        # Gets inventory items
        if detail["data"].get("productVariant"):
            inventory_item = detail["data"]["productVariant"]["inventoryItem"]
        elif len(detail["data"]["products"]["edges"]) > 0:
            for product in detail["data"]["products"]["edges"]:
                for variant in product["node"]["variants"]["edges"]:
                    if variant["node"]["inventoryItem"]:
                        inventory_item = variant["node"]["inventoryItem"]

        if not inventory_item:
            return None

        for level in inventory_item["inventoryLevels"]["edges"]:
            inventories.append(
                {
                    "inventory_id": inventory_item["id"],
                    "available": level["node"]["quantities"][0].get("quantity", 0),
                    "location_id": level["node"]["location"]["id"]
                }
            )

        return inventories

    def update_product_mutation(
        self, location_id, inventory_item_id, quantity, update_field="delta"
    ):

        mutation = """
            mutation inventoryAdjustQuantities($input: InventoryAdjustQuantitiesInput!) {
              inventoryAdjustQuantities(input: $input) {
                userErrors {
                  field
                  message
                }
                inventoryAdjustmentGroup {
                  createdAt
                  reason
                  changes {
                    name
                    delta
                  }
                }
              }
            }
        """
        res = self.deploy_mutation(
            mutation,
            {
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "changes": [
                        {
                            "locationId": location_id,
                            "inventoryItemId": inventory_item_id,
                            update_field: quantity,
                        }
                    ]
                }
            }
        )
        self.post_message(res)

    def update_inventory(self, item):
        operation = "add"
        filter_key = self.get_product_filter_key(item)
        products = self.query_products(f"{filter_key['key']}:{filter_key['val']}")
        inventories = self.get_inventory_levels(products)
        quantity = None

        if "operation" not in item:
            return None

        operation = item["operation"]

        for inventory in inventories:
            if operation == "subtract":
                quantity = int(f"-{item['quantity']}")

            elif operation == "add":
                quantity = int(item["quantity"])

            elif operation == "set":
                # We need to calculate the delta between the current quantity and the desired quantity
                # as the mutation only works adding or subtracting from the current quantity
                quantity = int(item["quantity"])
                available_quantity = inventory["available"]
                quantity = quantity - available_quantity
            else:
                raise Exception("Unrecognized operation for inventory Update")

            if quantity == None:
                raise Exception("No quantity set for inventory Update")

            self.logger.info("Updating inventory for Inventory ID {}".format(inventory["inventory_id"]))
            self.update_product_mutation(
                inventory["location_id"], inventory["inventory_id"], quantity
            )

    def post_message(self, res):
        if "errors" in res:
            raise Exception(res["errors"])
        print(json.dumps(res))

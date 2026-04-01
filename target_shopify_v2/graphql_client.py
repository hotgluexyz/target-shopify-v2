"""TargetShopifyV2 target sink class, which handles writing streams."""


import json
from itertools import product
import re

import requests
from singer_sdk.sinks import RecordSink

from target_shopify_v2.mapping import UnifiedMapping
from target_hotglue.client import HotglueSink
from datetime import datetime
from singer_sdk.exceptions import FatalAPIError, RetriableAPIError


class shopifyGraphQLV2Sink(HotglueSink):
    @property
    def base_url(self):
        base = self.config.get('shop')
        if not base.endswith('.myshopify.com'):
            base = f"{base}.myshopify.com"
        return f"https://{base}/admin/api/2021-07/graphql.json"

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
        self.validate_response(res)
        return res.json()

    def shopify_query(self, query, variables, input_name="input"):
        res = requests.post(
            url=self.base_url,
            json={"query": query, "variables": variables},
            headers=self.get_http_headers(),
        )
        self.logger.debug(f"DEBUG REQUEST- url:{self.base_url} query: {query}, variables: {variables}")
        self.validate_response(res)
        return res.json()

    def upload_order(self, record):
        mapping = UnifiedMapping()

        if "id" in record:
            return self.update_order_by_id(record)
        elif "order_number" in record:
            return self.update_order_by_number(record)
        else:
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
                draft_order = self.deploy_mutation(mutation, {"input": payload})
                draft_order = draft_order["data"]["draftOrderCreate"]["draftOrder"]
                # Complete the draft order
                order_status = record.get("status")
                params = {"id": draft_order["id"]}

                #1.Complete draftorder - create order
                # if status is active create order with pending payment
                if order_status in ["active"]:
                    params = {"id": draft_order["id"], "paymentPending": True}
                # if status is completed create order as completed
                elif order_status in ["completed"]:
                    params = {"id": draft_order["id"]}
                order = self.complete_draft_order(params)
                order_id = order["data"]["draftOrderComplete"]["draftOrder"]["order"]["id"]
                #2. if record["fulfilled"] == true, mark order as fulfilled
                if (
                    order
                    and "order" in order["data"]["draftOrderComplete"]["draftOrder"]
                ):
                    # Check and fulfil order if there were no errors
                    self.fulfil_order(
                        record,
                        order_id,
                        payload,
                    )
                return order_id
            # Check if order is fully paid
            # self.mark_order_paid(record,res,payload)

    def update_order_by_number(self, record):
        order = self.query_order_by_name(record.get("order_number"))
        if order and order["data"]["orders"]["edges"]:
            first_order_node = order["data"]["orders"]["edges"][0]["node"]
            if first_order_node:
                order_id = first_order_node["id"]
                self.fulfil_order(record, order_id)
                return order_id
            else:
                # Handle the case where the order node is not found
                self.logger.warn(f"Unable find Order Number: {record['order_number']} for {self.stream_name}")
        else:
            # Handle the case where the order list is empty or not populated
            self.logger.warn(f"Unable find Order Number: {record['order_number']} for {self.stream_name}")

    def update_order_by_id(self, record):
        order = self.query_order(record.get("id"))
        order_id = order["data"]["order"]["id"]
        self.fulfil_order(
            record,
            order_id,
        )
        return order_id

    def fulfil_order(self, record, order_id, payload=None):
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
                            url
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
                if not order_id.startswith("gid://shopify/Order/"):
                    order_id = "gid://shopify/Order/" + order_id
                order_details = self.query_order(order_id)
                if "order" in order_details["data"]:
                    # Get the fulfillmentOrders associated to this order
                    line_items = order_details["data"]["order"][
                        "fulfillmentOrders"
                    ]["edges"]

                    if not line_items:
                        raise Exception(f"There are no fulfillment orders for this order: {order_details['data']['order']}")

                    # TODO: Why do we have to do this on a line item level?
                    for line_item in line_items:
                        fulfill_item = {}
                        fulfill_item["fulfillmentOrderId"] = line_item["node"]["id"]
                        fulfill_items.append(fulfill_item)

                tracking_info = {
                    "company": record.get("carrier"),
                    "number": record.get("tracking_number"),
                    "url": record.get("tracking_url"),
                }

        fulfillment_payload = {
            "lineItemsByFulfillmentOrder": fulfill_items,
            "trackingInfo": tracking_info,
        }
        res_return = self.deploy_mutation(
            mutation, {"fulfillment": fulfillment_payload}
        )
        self.post_message(res_return)
        return res_return.get('data', {}).get('fulfillmentCreateV2', {}).get('fulfillment', {}).get('id')

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
                                    requiresShipping
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
    
    def complete_draft_order(self, params):
        #completes a draft order to create an order
        res_return = {}
        #create order with pending payment
        if params.get("paymentPending"):
            mutation = """ 
                mutation draftOrderComplete($id: ID!, $paymentPending: Boolean) {
                    draftOrderComplete(id: $id, paymentPending: $paymentPending) {
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
        else:
        #create order as completed
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
        res_return = self.deploy_mutation(mutation, params)
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
            variants = payload.pop("variants", [])
            for variant in variants:
                variant.pop("title", None)
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
                res = self.deploy_mutation(mutation, {"productId": payload["id"], "variants": variants_update})            
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
                res = self.deploy_mutation(mutation, {"productId": payload["id"], "variants": variants_create})
        else:
            variants_create = []
            for variant in payload.pop("variants", []):
                variant.pop("title", None)
                variants_create.append(variant)

            mutation = """
                    mutation productCreate($input: ProductInput!) {
                    productCreate(input: $input) {
                        product {
                        id
                        }
                    }
                    }"""
            res = self.deploy_mutation(mutation, {"input": payload})
            if variants_create:
                product_id = res["data"]["productCreate"]["product"]["id"]
                mutation = """
                    mutation productVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
                    productVariantsBulkCreate(productId: $productId, strategy: REMOVE_STANDALONE_VARIANT, variants: $variants) {
                        product {
                            id
                        }
                        productVariants {
                            id
                        }
                    }
                    }"""
                res = self.deploy_mutation(mutation, {"productId": product_id, "variants": variants_create})
        return res

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
                            inventoryLevels(first:25){
                                edges{
                                    node{
                                        id
                                        quantities(names: ["available"]) {
                                            name
                                            quantity
                                        }
                                        location {
                                            id
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
                                            inventoryLevels(first:25){
                                                edges{
                                                    node{
                                                        id
                                                        quantities(names: ["available"]) {
                                                            name
                                                            quantity
                                                        }
                                                        location {
                                                            id
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
        inventory_item = []

        # Gets inventory items
        if detail["data"].get("productVariant"):
            inv = detail["data"]["productVariant"].get("inventoryItem")
            if inv:
                inventory_item.append(inv)

        elif len(detail["data"]["products"]["edges"]) > 0:
            for product in detail["data"]["products"]["edges"]:
                for variant in product["node"]["variants"]["edges"]:
                    if variant["node"]["inventoryItem"]:
                        inventory_item.append(variant["node"]["inventoryItem"])

        if len(inventory_item) == 0:
            return None

        for item in inventory_item:
            inventory_item_id = item.get("id")
            for level in item["inventoryLevels"]["edges"]:
                node = level.get("node", {})
                quantities = node.get("quantities", [])
                available = quantities[0].get("quantity", 0) if quantities else 0
                inventories.append(
                    {
                        "inventory_id": node.get("id"),
                        "inventory_item_id": inventory_item_id,
                        "available": available,
                        "location_id": node.get("location", {}).get("id"),
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
                    ],
                }
            },
        )
        self.post_message(res)
        return res

    def update_inventory(self, item):
        operation = "add"
        filter_key = self.get_product_filter_key(item)
        products = self.query_products(f"{filter_key['key']}:{filter_key['val']}")
        inventories = self.get_inventory_levels(products)
        quantity = None

        if not inventories:
            raise Exception(f"Inventory lookup failed: no inventory levels found for query '{filter_key['key']}:{filter_key['val']}'")

        if "operation" not in item:
            return None

        operation = item["operation"]

        location_id = item.get("location_id")
        if location_id:
            if not str(location_id).startswith("gid://shopify/Location/"):
                location_id = "gid://shopify/Location/" + str(location_id)
            matched = [inv for inv in inventories if inv["location_id"] == location_id]
            if not matched:
                raise Exception(f"No inventory found for location_id '{location_id}'")
            inventories = matched

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
                inventory["location_id"], inventory["inventory_item_id"], quantity
            )
        return inventories[0]["inventory_item_id"]

    def delete_product(self, record):
        product_id = record.get("id")

        if not product_id:
            raise ValueError("Cannot delete product: no 'id' resolved on record")

        if "gid://shopify/Product/" not in str(product_id):
            product_id = f"gid://shopify/Product/{product_id}"

        mutation = """
            mutation productDelete($input: ProductDeleteInput!) {
              productDelete(input: $input) {
                deletedProductId
                userErrors {
                  message
                }
              }
            }"""

        res = self.deploy_mutation(mutation, {"input": {"id": product_id}})
        self.post_message(res, parse_messages_in_error=True)
        deleted_id = (res.get("data") or {}).get("productDelete", {}).get("deletedProductId")

        if not deleted_id:
            raise Exception(f"productDelete did not return deletedProductId: {res}")

        return deleted_id

    def post_message(self, res, parse_messages_in_error=False):

        if "errors" in res:
            self.update_state({"error_response": res["errors"]})
            raise Exception(res["errors"])
        
        data = res.get("data", {})
        for key in data:
            if data[key].get("userErrors"):
                if parse_messages_in_error:
                    messages = [e.get("message") for e in data[key]["userErrors"] if e.get("message")]
                    if messages:
                        raise Exception("; ".join(messages))
                raise Exception(data[key]["userErrors"])


    def preprocess_record(self, record: dict, context: dict) -> dict:
        for key, value in record.items():
            if isinstance(value, datetime):
                record[key] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return record
    
    def validate_response(self, response: requests.Response) -> None:
        """Validate HTTP response."""
        if response.json().get("errors"):
            raise FatalAPIError(response.text)
        if response.status_code in [429] or 500 <= response.status_code < 600:
            msg = self.response_error_message(response)
            raise RetriableAPIError(msg, response)
        elif 400 <= response.status_code < 500:
            try:
                msg = response.text
            except:
                msg = self.response_error_message(response)
            raise FatalAPIError(msg)

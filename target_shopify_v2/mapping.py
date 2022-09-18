import json
import os
from cgitb import lookup

__location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))


class UnifiedMapping:
    def __init__(self) -> None:
        pass

    def read_json_file(self, filename):
        # read file
        with open(os.path.join(__location__, f"{filename}"), "r") as filetoread:
            data = filetoread.read()

        # parse file
        content = json.loads(data)

        return content

    def map_shopify_lineitems(self, lineitems, lineitems_mapping, payload):
        payload["lineItems"] = []
        if isinstance(lineitems, list):

            if len(lineitems) > 0:
                for line in lineitems:
                    line_item = {}
                    for key in lineitems_mapping.keys():
                        if key in line:
                            if line[key]:
                                line_item[lineitems_mapping[key]] = line[key]
                    if line["discount_amount"] > 0:
                        line_item["appliedDiscount"] = {}
                        line_item["appliedDiscount"]["amount"] = 0
                        line_item["appliedDiscount"]["amount"] = line["discount_amount"]
                        line_item["appliedDiscount"]["value"] = line["discount_amount"]
                        line_item["appliedDiscount"]["valueType"] = "FIXED_AMOUNT"
                    payload["lineItems"].append(line_item)

        return payload

    def map_shopify_address(
        self, addresses, address_mapping, payload, type="billingAddress"
    ):
        address = {}
        countries = self.read_json_file(f"countries.json")
        for key in address_mapping.keys():
            address[address_mapping[key]] = addresses[key]
        address["countryCode"] = countries[address["countryCode"]]
        payload[type] = address
        return payload

    def map_custom_fields(self, payload, fields):
        # Populate custom fields.
        for key, val in fields:
            payload[key] = val
        return payload

    def inject_sopify_product_fields(self, record, payload, mapping):
        images = []
        location_id = ""
        variant_dictionary = {}
        if "location" in record:
            if "id" in record["location"]:
                location_id = record["location"]["id"]
        variant_dictionary = {
            "title": record["variant"],
            "price": record["price"],
            "sku": record["sku"],
            "inventoryItem": {"cost": record["cost"]},
        }
        if len(location_id) > 0:
            variant_dictionary["inventoryQuantities"] = {
                "availableQuantity": record["available_quantity"],
                "locationId": location_id,
            }
        payload["variants"] = [variant_dictionary]

        if "short_description" in record:
            payload["seo"] = {"description": record["short_description"]}

        if "active" in record:
            if record["active"] is True:
                payload["status"] = "ACTIVE"
            else:
                payload["status"] = "DRAFT"
        if "image_urls" in record:
            if len(record["image_urls"]) > 0:
                for image in record["image_urls"]:
                    images.append({"src": image})
        if len(images) > 0:
            payload["images"] = images

        return payload

    def inject_shopify_order_fields(self, record, payload):
        if record["total_discount"] > 0:
            payload["appliedDiscount"] = {}
            payload["appliedDiscount"]["amount"] = record["total_discount"]
            payload["appliedDiscount"]["value"] = record["total_discount"]
            payload["appliedDiscount"]["valueType"] = "FIXED_AMOUNT"
        return payload

    def prepare_payload(self, record, endpoint="contact", target="shopify"):
        mapping = self.read_json_file(f"mapping_{target}.json")
        ignore = mapping["ignore"]
        mapping = mapping[endpoint]
        payload = {}
        payload_return = {}
        lookup_keys = mapping.keys()
        for lookup_key in lookup_keys:
            if lookup_key == "line_items" and target == "shopify":
                line_items = record.get(lookup_key, [])
                if endpoint == "products":
                    line_items.update({"sku": record["sku"]})
                payload = self.map_shopify_lineitems(
                    line_items,
                    mapping[lookup_key],
                    payload,
                )
            elif lookup_key == "billing_address" and target == "shopify":
                payload = self.map_shopify_address(
                    record.get(lookup_key, {}), mapping[lookup_key], payload
                )
            elif lookup_key == "shipping_address" and target == "shopify":
                payload = self.map_shopify_address(
                    record.get(lookup_key, {}),
                    mapping[lookup_key],
                    payload,
                    "shippingAddress",
                )
            elif lookup_key == "custom_fields":
                # handle custom fields
                payload = self.map_custom_fields(payload, mapping[lookup_key])
            else:
                val = record.get(lookup_key, "")
                if val:
                    payload[mapping[lookup_key]] = val

        # Need name for Opportunity
        if endpoint == "oppurtunity" or endpoint == "account":
            ignore.remove("Name")

        # inject special fields of shopify product before returning payload
        if target == "shopify" and endpoint == "products":
            payload = self.inject_sopify_product_fields(record, payload, mapping)

        if target == "shopify" and endpoint == "sale_orders":
            payload = self.inject_shopify_order_fields(record, payload)

        # filter ignored keys
        for key in payload.keys():
            if key not in ignore:
                payload_return[key] = payload[key]
        return payload_return

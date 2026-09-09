import json
import os

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
                    if line.get("discount_amount") and line["discount_amount"] > 0:
                        line_item["appliedDiscount"] = {}
                        line_item["appliedDiscount"]["amount"] = 0
                        line_item["appliedDiscount"]["amount"] = line["discount_amount"]
                        line_item["appliedDiscount"]["value"] = line["discount_amount"]
                        line_item["appliedDiscount"]["valueType"] = "FIXED_AMOUNT"
                    line_item["quantity"] = int(line_item["quantity"])
                    payload["lineItems"].append(line_item)

        return payload

    def map_shopify_address(
        self, addresses, address_mapping, payload, type="billingAddress"
    ):
        if addresses:
            address = {}
            countries = self.read_json_file("countries.json")
            for key in address_mapping.keys():
                if key in addresses:
                    address[address_mapping[key]] = addresses[key]
            if len(address["countryCode"]) == 3:
                address["countryCode"] = countries[address["countryCode"]]
            if address:    
                payload[type] = address
        return payload

    @staticmethod
    def _build_metafield(field: dict) -> dict:
        """Convert a custom_fields entry to a Shopify MetafieldInput.

        name supports dot notation: "namespace.key" splits into namespace and key.
        Without a dot, namespace defaults to "custom".
        type is always single_line_text_field.
        """
        name = field["name"]
        if "." in name:
            namespace, key = name.split(".", 1)
        else:
            namespace, key = "custom", name
        return {
            "namespace": namespace,
            "key": key,
            "type": "single_line_text_field",
            "value": str(field["value"]),
        }

    def map_custom_fields(self, payload, fields):
        # Populate custom fields.
        for key, val in fields:
            payload[key] = val
        return payload

    def inject_sopify_product_fields(self, record, payload, mapping):
        images = []
        location_id = ""

        if "location" in record:
            if "id" in record["location"]:
                location_id = record["location"]["id"]

        payload["variants"] = []
        for variant in record.get("variants"):
            variant_dictionary = {}
            title = variant.get("title") or record.get("name")
            if title:
                variant_dictionary["title"] = title
            if variant.get("price"):
                variant_dictionary["price"] = variant.get("price")
            if variant.get("id"):
                variant_dictionary["id"] = variant["id"]
            if variant.get("sku"):
                variant_dictionary.setdefault("inventoryItem", {})["sku"] = variant["sku"]
            if variant.get("cost"):
                variant_dictionary.setdefault("inventoryItem", {})["cost"] = variant["cost"]
            if variant.get("weight") is not None:
                # unit is the Shopify WeightUnit enum (GRAMS/KILOGRAMS/OUNCES/POUNDS).
                variant_dictionary.setdefault("inventoryItem", {})["measurement"] = {
                    "weight": {
                        "unit": variant.get("mass_unit", "POUNDS"),
                        "value": variant["weight"],
                    }
                }
            if variant.get("id"):
                if "gid://shopify/ProductVariant/" not in variant["id"]:
                    variant_dictionary["id"] = "gid://shopify/ProductVariant/" + str(
                        variant["id"]
                    )
            if "options" in record and variant.get("options"):
                variant_dictionary["options"] = []
                for option in record["options"]:
                    value = next(
                        o["value"] for o in variant["options"] if o["name"] == option
                    )
                    variant_dictionary["options"].append(value)
            inv = variant_dictionary.setdefault("inventoryItem", {})
            if "available_quantity" in variant and variant["available_quantity"] is not None:
                inv["tracked"] = True
                if location_id:
                    variant_dictionary["inventoryQuantities"] = {
                        "availableQuantity": variant["available_quantity"],
                        "locationId": location_id,
                    }
            elif "available_quantity" in variant and variant["available_quantity"] is None:
                # If available_quantity is None, set tracked to False
                inv["tracked"] = False
            elif not variant.get("id"):
                # New variant with omitted available_quantity: default to not tracked
                inv["tracked"] = False

            if "image_urls" in variant:
                images.extend([{"src": i} for i in variant["image_urls"]])
            variant_metafields = [self._build_metafield(f) for f in variant.get("custom_fields", [])]
            if variant_metafields:
                variant_dictionary["metafields"] = variant_metafields
            payload["variants"].append(variant_dictionary)

        if "short_description" in record:
            payload["seo"] = {"description": record["short_description"]}

        if "options" in record:
            payload["options"] = record["options"]

        product_metafields = [self._build_metafield(f) for f in record.get("custom_fields", [])]
        if product_metafields:
            payload["metafields"] = product_metafields
        if record.get("active") is not None:
            if record["active"]:
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
        if record.get("total_discount") and float(record["total_discount"]) > 0:
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

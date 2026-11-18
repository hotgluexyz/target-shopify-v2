"""Unit tests for product and variant mapping."""

import pytest

from target_shopify_v2.mapping import UnifiedMapping

LOCATION_ID = "gid://shopify/Location/1234567890"
VARIANT_ID = "gid://shopify/ProductVariant/123456789"


@pytest.fixture
def mapping():
    return UnifiedMapping()


def _map_variants(mapping, record):
    payload = mapping.inject_sopify_product_fields(record, {}, {})
    return payload["variants"]


def _base_record(variants):
    return {
        "name": "Test Product",
        "location": {"id": LOCATION_ID},
        "variants": variants,
    }


def test_tracked_when_available_quantity_is_positive(mapping):
    variants = _map_variants(
        mapping,
        _base_record([{"sku": "SKU-1", "price": "10.00", "available_quantity": 5}]),
    )
    assert variants[0]["inventoryItem"]["tracked"] is True
    assert variants[0]["inventoryQuantities"] == {
        "availableQuantity": 5,
        "locationId": LOCATION_ID,
    }


def test_tracked_when_available_quantity_is_zero(mapping):
    variants = _map_variants(
        mapping,
        _base_record([{"sku": "SKU-0", "price": "10.00", "available_quantity": 0}]),
    )
    assert variants[0]["inventoryItem"]["tracked"] is True
    assert variants[0]["inventoryQuantities"]["availableQuantity"] == 0


def test_untracked_when_available_quantity_is_null_on_create(mapping):
    variants = _map_variants(
        mapping,
        _base_record([{"sku": "SKU-NULL", "price": "10.00", "available_quantity": None}]),
    )
    assert variants[0]["inventoryItem"]["tracked"] is False
    assert "inventoryQuantities" not in variants[0]


def test_untracked_when_available_quantity_is_null_on_update(mapping):
    variants = _map_variants(
        mapping,
        _base_record(
            [
                {
                    "id": VARIANT_ID,
                    "sku": "SKU-NULL",
                    "price": "10.00",
                    "available_quantity": None,
                }
            ]
        ),
    )
    assert variants[0]["inventoryItem"]["tracked"] is False
    assert "inventoryQuantities" not in variants[0]


def test_untracked_when_available_quantity_is_missing_on_create(mapping):
    variants = _map_variants(
        mapping,
        _base_record([{"sku": "SKU-MISSING", "price": "10.00"}]),
    )
    assert variants[0]["inventoryItem"]["tracked"] is False
    assert "inventoryQuantities" not in variants[0]


def test_update_without_available_quantity_does_not_set_tracked(mapping):
    variants = _map_variants(
        mapping,
        _base_record(
            [
                {
                    "id": VARIANT_ID,
                    "sku": "SKU-UPDATE",
                    "price": "12.00",
                    "cost": "4.50",
                }
            ]
        ),
    )
    assert "tracked" not in variants[0]["inventoryItem"]
    assert "inventoryQuantities" not in variants[0]


def test_cost_does_not_force_tracking_on_create(mapping):
    variants = _map_variants(
        mapping,
        _base_record([{"sku": "SKU-COST", "price": "10.00", "cost": "4.50"}]),
    )
    assert variants[0]["inventoryItem"]["cost"] == "4.50"
    assert variants[0]["inventoryItem"]["tracked"] is False
    assert "inventoryQuantities" not in variants[0]


def test_multi_variant_mixed_tracking_on_create(mapping):
    variants = _map_variants(
        mapping,
        _base_record(
            [
                {"sku": "MIX-S", "price": "10.00", "available_quantity": 8},
                {"sku": "MIX-M", "price": "10.00", "available_quantity": None},
                {"sku": "MIX-L", "price": "10.00"},
            ]
        ),
    )
    assert variants[0]["inventoryItem"]["tracked"] is True
    assert variants[1]["inventoryItem"]["tracked"] is False
    assert variants[2]["inventoryItem"]["tracked"] is False


@pytest.mark.parametrize(
    "record_extra, expected_status",
    [
        ({"active": True}, "ACTIVE"),
        ({"active": False}, "DRAFT"),
        ({"active": None}, None),
        ({}, None),
    ],
)
def test_active_maps_to_status(mapping, record_extra, expected_status):
    record = _base_record([{"sku": "SKU-1", "price": "10.00"}])
    record.update(record_extra)
    payload = mapping.inject_sopify_product_fields(record, {}, {})
    assert payload.get("status") == expected_status


@pytest.mark.parametrize("variant_id", [None, VARIANT_ID], ids=["create", "update"])
@pytest.mark.parametrize("compare_at_price", ["175.00", "0.00", 0, None])
@pytest.mark.parametrize("include_price", [False, True], ids=["compare-only", "with-price"])
def test_compare_at_price_maps_to_variant_input(
    mapping, variant_id, compare_at_price, include_price
):
    variant = {
        "title": "Default Title",
        "sku": "SKU-COMPARE",
        "compare_at_price": compare_at_price,
    }
    if variant_id is not None:
        variant["id"] = variant_id
    if include_price:
        variant["price"] = "150.00"
    record = _base_record([variant])
    if variant_id is not None:
        record["id"] = "gid://shopify/Product/123456789"

    payload = mapping.prepare_payload(record, "products", target="shopify")

    mapped_variant = payload["variants"][0]
    assert mapped_variant["compareAtPrice"] == compare_at_price
    assert "compare_at_price" not in mapped_variant
    assert "compareAtPrice" not in payload
    assert mapped_variant["inventoryItem"]["sku"] == "SKU-COMPARE"
    if variant_id is not None:
        assert mapped_variant["id"] == variant_id
    else:
        assert "id" not in mapped_variant
    if include_price:
        assert mapped_variant["price"] == "150.00"
    else:
        assert "price" not in mapped_variant


@pytest.mark.parametrize("variant_id", [None, VARIANT_ID], ids=["create", "update"])
def test_omitted_compare_at_price_does_not_clear_existing_value(mapping, variant_id):
    variant = {"sku": "SKU-COMPARE", "price": "150.00"}
    if variant_id is not None:
        variant["id"] = variant_id

    payload = mapping.prepare_payload(_base_record([variant]), "products", target="shopify")

    assert "compareAtPrice" not in payload["variants"][0]

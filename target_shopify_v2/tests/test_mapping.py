"""Unit tests for product variant inventory tracking mapping."""

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

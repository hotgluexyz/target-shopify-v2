"""Unit tests for Shopify fulfillment payloads."""

import pytest

from target_shopify_v2.graphql_client import shopifyGraphQLV2Sink

FULFILLMENT_ID = "gid://shopify/Fulfillment/1234567890"
FULFILLMENT_ORDER_ID = "gid://shopify/FulfillmentOrder/1234567890"


@pytest.fixture
def fulfillment_sink(monkeypatch):
    sink = object.__new__(shopifyGraphQLV2Sink)
    captured = {}

    monkeypatch.setattr(shopifyGraphQLV2Sink, "name", "Fulfillments")

    monkeypatch.setattr(
        sink,
        "query_order",
        lambda order_id: {
            "data": {
                "order": {
                    "fulfillmentOrders": {
                        "edges": [{"node": {"id": FULFILLMENT_ORDER_ID}}]
                    }
                }
            }
        },
    )

    def deploy_mutation(mutation, variables):
        captured["mutation"] = mutation
        captured["variables"] = variables
        return {
            "data": {
                "fulfillmentCreateV2": {
                    "fulfillment": {"id": FULFILLMENT_ID}
                }
            }
        }

    monkeypatch.setattr(sink, "deploy_mutation", deploy_mutation)
    monkeypatch.setattr(sink, "post_message", lambda response: None)
    return sink, captured


@pytest.mark.parametrize(
    ("notification_fields", "expected"),
    [
        ({}, False),
        ({"notify_customer": None}, False),
        ({"notify_customer": False}, False),
        ({"notify_customer": 1}, False),
        ({"notify_customer": "true"}, False),
        ({"notify_customer": True}, True),
    ],
)
def test_fulfillment_notify_customer_is_explicit_opt_in(
    fulfillment_sink, notification_fields, expected
):
    sink, captured = fulfillment_sink
    record = {
        "fulfilled": True,
        "tracking_number": "TRACK-123",
        **notification_fields,
    }

    fulfillment_id = sink.fulfil_order(record, "1234567890")

    assert fulfillment_id == FULFILLMENT_ID
    assert (
        captured["variables"]["fulfillment"]["notifyCustomer"] is expected
    )

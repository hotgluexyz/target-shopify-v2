"""Unit tests for partial fulfillment (line-item scoped) payloads."""

import logging

import pytest

from target_shopify_v2.graphql_client import shopifyGraphQLV2Sink

FULFILLMENT_ID = "gid://shopify/Fulfillment/1234567890"
FO_A = "gid://shopify/FulfillmentOrder/1111"
FO_B = "gid://shopify/FulfillmentOrder/2222"


def line_item(foli_id, line_item_id, remaining):
    """One FulfillmentOrderLineItem edge as Shopify returns it."""
    return {
        "node": {
            "id": foli_id,
            "remainingQuantity": remaining,
            "lineItem": {"id": f"gid://shopify/LineItem/{line_item_id}"},
        }
    }


def fulfillment_order(fo_id, line_items):
    return {"node": {"id": fo_id, "lineItems": {"edges": line_items}}}


@pytest.fixture
def fulfillment_sink(monkeypatch):
    """A sink whose Shopify calls are captured rather than sent.

    `fulfillment_orders` is mutable so each test can describe the order it needs.
    """
    sink = object.__new__(shopifyGraphQLV2Sink)
    captured = {"fulfillment_orders": []}

    monkeypatch.setattr(sink, "logger", logging.getLogger("test"), raising=False)

    monkeypatch.setattr(
        sink,
        "query_fulfillment_order_line_items",
        lambda order_id: {
            "data": {"order": {"fulfillmentOrders": {"edges": captured["fulfillment_orders"]}}}
        },
    )
    monkeypatch.setattr(
        sink,
        "query_order",
        lambda order_id: {
            "data": {
                "order": {
                    "fulfillmentOrders": {
                        "edges": [{"node": {"id": FO_A}}]
                    }
                }
            }
        },
    )

    def deploy_mutation(mutation, variables):
        captured["variables"] = variables
        return {"data": {"fulfillmentCreateV2": {"fulfillment": {"id": FULFILLMENT_ID}}}}

    monkeypatch.setattr(sink, "deploy_mutation", deploy_mutation)
    monkeypatch.setattr(sink, "post_message", lambda response: None)
    return sink, captured


def sent_line_items(captured):
    return captured["variables"]["fulfillment"]["lineItemsByFulfillmentOrder"]


def test_absent_line_items_still_fulfills_whole_order(fulfillment_sink):
    """Back-compat: no line_items key means fulfill everything, as before."""
    sink, captured = fulfillment_sink

    fulfillment_id = sink.fulfil_order({"fulfilled": True}, "1234567890")

    assert fulfillment_id == FULFILLMENT_ID
    assert sent_line_items(captured) == [{"fulfillmentOrderId": FO_A}]


def test_partial_fulfillment_sends_only_requested_lines(fulfillment_sink):
    sink, captured = fulfillment_sink
    captured["fulfillment_orders"] = [
        fulfillment_order(
            FO_A,
            [
                line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 5),
                line_item("gid://shopify/FulfillmentOrderLineItem/2", "222", 5),
            ],
        )
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2}]},
        "1234567890",
    )

    assert sent_line_items(captured) == [
        {
            "fulfillmentOrderId": FO_A,
            "fulfillmentOrderLineItems": [
                {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 2}
            ],
        }
    ]


@pytest.mark.parametrize(
    "requested_id",
    ["111", 111, "gid://shopify/LineItem/111"],
)
def test_line_item_ids_match_in_either_form(fulfillment_sink, requested_id):
    sink, captured = fulfillment_sink
    captured["fulfillment_orders"] = [
        fulfillment_order(
            FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 3)]
        )
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": requested_id, "quantity": 1}]},
        "1234567890",
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 1}
    ]


def test_fulfillment_orders_without_requested_items_are_skipped(fulfillment_sink):
    """The whole point: an unrelated FO must not be sent with a blank line item list."""
    sink, captured = fulfillment_sink
    captured["fulfillment_orders"] = [
        fulfillment_order(
            FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 2)]
        ),
        fulfillment_order(
            FO_B, [line_item("gid://shopify/FulfillmentOrderLineItem/9", "999", 2)]
        ),
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2}]},
        "1234567890",
    )

    sent = sent_line_items(captured)
    assert [entry["fulfillmentOrderId"] for entry in sent] == [FO_A]


def test_line_split_across_fulfillment_orders_consumes_quantity(fulfillment_sink):
    """One order line split across two locations fills until the quantity is exhausted."""
    sink, captured = fulfillment_sink
    captured["fulfillment_orders"] = [
        fulfillment_order(
            FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 2)]
        ),
        fulfillment_order(
            FO_B, [line_item("gid://shopify/FulfillmentOrderLineItem/2", "111", 5)]
        ),
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 3}]},
        "1234567890",
    )

    assert sent_line_items(captured) == [
        {
            "fulfillmentOrderId": FO_A,
            "fulfillmentOrderLineItems": [
                {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 2}
            ],
        },
        {
            "fulfillmentOrderId": FO_B,
            "fulfillmentOrderLineItems": [
                {"id": "gid://shopify/FulfillmentOrderLineItem/2", "quantity": 1}
            ],
        },
    ]


def test_quantity_is_clamped_to_remaining(fulfillment_sink):
    sink, captured = fulfillment_sink
    captured["fulfillment_orders"] = [
        fulfillment_order(
            FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 1)]
        )
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 4}]},
        "1234567890",
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 1}
    ]


def test_already_fulfilled_lines_are_not_refulfilled(fulfillment_sink):
    """remainingQuantity 0 covers closed/completed fulfillment orders without a status check."""
    sink, captured = fulfillment_sink
    captured["fulfillment_orders"] = [
        fulfillment_order(
            FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 0)]
        )
    ]

    with pytest.raises(Exception, match="None of the requested line items"):
        sink.fulfil_order(
            {"fulfilled": True, "line_items": [{"id": "111", "quantity": 1}]},
            "1234567890",
        )


def test_empty_line_items_list_is_rejected(fulfillment_sink):
    """[] must not silently mean "fulfill everything"."""
    sink, _ = fulfillment_sink

    with pytest.raises(Exception, match="empty line_items"):
        sink.fulfil_order({"fulfilled": True, "line_items": []}, "1234567890")


def test_unfulfilled_record_short_circuits(fulfillment_sink):
    sink, captured = fulfillment_sink

    assert sink.fulfil_order({"line_items": [{"id": "111", "quantity": 1}]}, "1") is None
    assert "variables" not in captured

"""Unit tests for partial fulfillment (line-item scoped) payloads."""

import logging

import pytest

from target_shopify_v2.graphql_client import shopifyGraphQLV2Sink

FULFILLMENT_ID = "gid://shopify/Fulfillment/1234567890"
FO_A = "gid://shopify/FulfillmentOrder/1111"
FO_B = "gid://shopify/FulfillmentOrder/2222"


def line_item(foli_id, line_item_id, remaining):
    """One FulfillmentOrderLineItem node as Shopify returns it."""
    return {
        "id": foli_id,
        "remainingQuantity": remaining,
        "lineItem": {"id": f"gid://shopify/LineItem/{line_item_id}"},
    }


def fulfillment_order(fo_id, *line_item_pages):
    """A fulfillment order whose lineItems connection spans the given pages."""
    return {"id": fo_id, "line_item_pages": list(line_item_pages) or [[]]}


def _connection(nodes, has_next, cursor_prefix):
    return {
        "pageInfo": {"hasNextPage": has_next},
        "edges": [
            {"cursor": f"{cursor_prefix}{index}", "node": node}
            for index, node in enumerate(nodes)
        ],
    }


@pytest.fixture
def fulfillment_sink(monkeypatch):
    """A sink whose Shopify calls are captured rather than sent.

    `fo_pages` is a list of pages, each a list of fulfillment_order() specs, so tests
    can force pagination on either connection without needing 25+ rows.
    """
    sink = object.__new__(shopifyGraphQLV2Sink)
    captured = {"fo_pages": [[]], "calls": []}

    monkeypatch.setattr(sink, "logger", logging.getLogger("test"), raising=False)
    monkeypatch.setattr(sink, "name", "Fulfillments", raising=False)

    def query_fulfillment_order_line_items(order_id, after=None):
        captured["calls"].append(("fo_page", after))
        index = 0 if after is None else int(after.replace("fo", "")) + 1
        pages = captured["fo_pages"]
        nodes = [
            {
                "id": spec["id"],
                "lineItems": _connection(
                    spec["line_item_pages"][0],
                    has_next=len(spec["line_item_pages"]) > 1,
                    cursor_prefix=f"{spec['id']}-li",
                ),
            }
            for spec in pages[index]
        ]
        return {
            "data": {
                "order": {
                    "fulfillmentOrders": _connection(
                        nodes, has_next=index + 1 < len(pages), cursor_prefix="fo"
                    )
                }
            }
        }

    # Serve successive line-item pages per fulfillment order.
    served = {}

    def li_page(fulfillment_order_id, after):
        captured["calls"].append(("li_page", fulfillment_order_id, after))
        # The cursor must be the last edge of the page we already served, otherwise an
        # implementation that passes None (or the wrong cursor) would still pass below.
        page_index = served.get(fulfillment_order_id, 0)
        expected_after = f"{fulfillment_order_id}-li{page_index}"
        assert after == expected_after, f"expected cursor {expected_after!r}, got {after!r}"
        spec = next(
            s for page in captured["fo_pages"] for s in page if s["id"] == fulfillment_order_id
        )
        next_index = served.get(fulfillment_order_id, 0) + 1
        served[fulfillment_order_id] = next_index
        pages = spec["line_item_pages"]
        nodes = pages[next_index] if next_index < len(pages) else []
        return {
            "data": {
                "node": {
                    "lineItems": _connection(
                        nodes,
                        has_next=next_index + 1 < len(pages),
                        cursor_prefix=f"{fulfillment_order_id}-li",
                    )
                }
            }
        }

    monkeypatch.setattr(sink, "query_fulfillment_order_line_items",
                        query_fulfillment_order_line_items, raising=False)
    monkeypatch.setattr(sink, "query_fulfillment_order_line_items_page", li_page, raising=False)
    monkeypatch.setattr(
        sink, "query_order",
        lambda order_id: {"data": {"order": {"fulfillmentOrders": {"edges": [{"node": {"id": FO_A}}]}}}},
        raising=False,
    )

    def deploy_mutation(mutation, variables):
        captured["variables"] = variables
        return {"data": {"fulfillmentCreateV2": {"fulfillment": {"id": FULFILLMENT_ID}}}}

    monkeypatch.setattr(sink, "deploy_mutation", deploy_mutation, raising=False)
    monkeypatch.setattr(sink, "post_message", lambda response: None, raising=False)
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
    captured["fo_pages"] = [[fulfillment_order(FO_A, [
        line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 5),
        line_item("gid://shopify/FulfillmentOrderLineItem/2", "222", 5),
    ])]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2}]}, "1234567890"
    )

    assert sent_line_items(captured) == [{
        "fulfillmentOrderId": FO_A,
        "fulfillmentOrderLineItems": [
            {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 2}
        ],
    }]


@pytest.mark.parametrize("requested_id", ["111", 111, "gid://shopify/LineItem/111"])
def test_line_item_ids_match_in_either_form(fulfillment_sink, requested_id):
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 3)]
    )]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": requested_id, "quantity": 1}]}, "1234567890"
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 1}
    ]


def test_fulfillment_orders_without_requested_items_are_skipped(fulfillment_sink):
    """The whole point: an unrelated FO must not be sent with a blank line item list."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[
        fulfillment_order(FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 2)]),
        fulfillment_order(FO_B, [line_item("gid://shopify/FulfillmentOrderLineItem/9", "999", 2)]),
    ]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2}]}, "1234567890"
    )

    assert [entry["fulfillmentOrderId"] for entry in sent_line_items(captured)] == [FO_A]


def test_line_split_across_fulfillment_orders_consumes_quantity(fulfillment_sink):
    """One order line split across two locations fills until the quantity is exhausted."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[
        fulfillment_order(FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 2)]),
        fulfillment_order(FO_B, [line_item("gid://shopify/FulfillmentOrderLineItem/2", "111", 5)]),
    ]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 3}]}, "1234567890"
    )

    assert sent_line_items(captured) == [
        {"fulfillmentOrderId": FO_A, "fulfillmentOrderLineItems": [
            {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 2}]},
        {"fulfillmentOrderId": FO_B, "fulfillmentOrderLineItems": [
            {"id": "gid://shopify/FulfillmentOrderLineItem/2", "quantity": 1}]},
    ]


def test_requested_item_on_a_later_fulfillment_order_page_is_found(fulfillment_sink):
    """Regression: the item lives past the first fulfillmentOrders page."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [
        [fulfillment_order(FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "999", 5)])],
        [fulfillment_order(FO_B, [line_item("gid://shopify/FulfillmentOrderLineItem/2", "111", 5)])],
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 1}]}, "1234567890"
    )

    assert sent_line_items(captured) == [{
        "fulfillmentOrderId": FO_B,
        "fulfillmentOrderLineItems": [
            {"id": "gid://shopify/FulfillmentOrderLineItem/2", "quantity": 1}
        ],
    }]
    assert ("fo_page", "fo0") in captured["calls"], "second fulfillmentOrders page not requested"


def test_requested_item_on_a_later_line_item_page_is_found(fulfillment_sink):
    """Regression: the item lives past the first page of ONE order's lineItems."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A,
        [line_item("gid://shopify/FulfillmentOrderLineItem/1", "999", 5)],
        [line_item("gid://shopify/FulfillmentOrderLineItem/2", "111", 5)],
    )]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 4}]}, "1234567890"
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/2", "quantity": 4}
    ]
    assert any(call[0] == "li_page" for call in captured["calls"]), "lineItems page 2 not requested"


def test_quantity_is_clamped_to_remaining(fulfillment_sink):
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 1)]
    )]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 4}]}, "1234567890"
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 1}
    ]


def test_already_fulfilled_lines_are_not_refulfilled(fulfillment_sink):
    """remainingQuantity 0 covers closed fulfillment orders without a status check."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 0)]
    )]]

    with pytest.raises(Exception, match="None of the requested line items"):
        sink.fulfil_order(
            {"fulfilled": True, "line_items": [{"id": "111", "quantity": 1}]}, "1234567890"
        )


@pytest.mark.parametrize("bad_quantity", [1.9, 0, -1, True, None, "2", object()])
def test_invalid_quantities_are_rejected(fulfillment_sink, bad_quantity):
    """int() would truncate 1.9 to 1 and fulfill a different amount than requested."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 5)]
    )]]

    with pytest.raises(ValueError, match="quantity"):
        sink.fulfil_order(
            {"fulfilled": True, "line_items": [{"id": "111", "quantity": bad_quantity}]},
            "1234567890",
        )
    assert "variables" not in captured, "no mutation may be sent for an invalid quantity"


def test_integral_float_quantity_is_accepted(fulfillment_sink):
    """JSON producers commonly emit whole numbers as floats; 2.0 is unambiguous."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 5)]
    )]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2.0}]}, "1234567890"
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/1", "quantity": 2}
    ]


def test_empty_line_items_list_is_rejected(fulfillment_sink):
    """[] must not silently mean "fulfill everything"."""
    sink, _ = fulfillment_sink

    with pytest.raises(Exception, match="empty line_items"):
        sink.fulfil_order({"fulfilled": True, "line_items": []}, "1234567890")


def test_non_fulfillment_sink_ignores_line_items(fulfillment_sink, monkeypatch):
    """SalesOrders passes the ORDER payload here; its line_items are not fulfillment lines."""
    sink, captured = fulfillment_sink
    monkeypatch.setattr(sink, "name", "SalesOrders", raising=False)

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2}]}, "1234567890"
    )

    # Falls back to whole-order fulfillment instead of scoping to the order's own lines.
    assert sent_line_items(captured) == [{"fulfillmentOrderId": FO_A}]
    assert not any(call[0] in ("fo_page", "li_page") for call in captured["calls"])


def test_unfulfilled_record_short_circuits(fulfillment_sink, monkeypatch):
    """An unfulfilled record must make no Shopify calls at all, not merely skip the mutation."""
    sink, captured = fulfillment_sink

    def fail_on_shopify_call(*args, **kwargs):
        pytest.fail("unfulfilled records must not call Shopify")

    monkeypatch.setattr(sink, "query_order", fail_on_shopify_call, raising=False)
    monkeypatch.setattr(sink, "query_fulfillment_order_line_items", fail_on_shopify_call,
                        raising=False)
    monkeypatch.setattr(sink, "query_fulfillment_order_line_items_page", fail_on_shopify_call,
                        raising=False)
    monkeypatch.setattr(sink, "deploy_mutation", fail_on_shopify_call, raising=False)

    assert sink.fulfil_order({"line_items": [{"id": "111", "quantity": 1}]}, "1") is None
    assert "variables" not in captured

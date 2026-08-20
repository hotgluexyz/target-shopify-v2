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


@pytest.fixture
def fulfillment_sink(monkeypatch):
    """A sink whose Shopify calls are captured rather than sent.

    `fo_pages` is a list of pages, each a list of fulfillment_order() specs, so tests
    can force pagination on either connection without needing 25+ rows.

    Every cursor issued is globally unique, and each stub asserts that the caller sent
    back exactly the cursor the stub last emitted for that connection. An implementation
    that passes `None`, a stale cursor, or a cursor from the wrong connection fails here
    rather than quietly appearing to paginate.
    """
    sink = object.__new__(shopifyGraphQLV2Sink)
    captured = {"fo_pages": [[]], "calls": []}
    state = {"cursor_seq": 0, "fo_page": 0, "fo_cursor": None, "li": {}}

    monkeypatch.setattr(sink, "logger", logging.getLogger("test"), raising=False)
    # `name` is a read-only abstract property on HotglueBaseSink, so it has to be patched
    # on the class - an instance setattr raises before monkeypatch can record the old value.
    monkeypatch.setattr(shopifyGraphQLV2Sink, "name", "Fulfillments")

    def connection(nodes, has_next):
        """Build a connection with unique cursors; also return its last cursor."""
        edges = []
        for node in nodes:
            state["cursor_seq"] += 1
            edges.append({"cursor": f"cursor-{state['cursor_seq']}", "node": node})
        payload = {"pageInfo": {"hasNextPage": has_next}, "edges": edges}
        return payload, (edges[-1]["cursor"] if edges else None)

    def query_fulfillment_order_line_items(order_id, after=None):
        """Serve one page of fulfillment orders, asserting the cursor sent back."""
        captured["calls"].append(("fo_page", after))
        assert after == state["fo_cursor"], (
            f"fulfillmentOrders cursor: expected {state['fo_cursor']!r}, got {after!r}"
        )

        pages = captured["fo_pages"]
        page_index = state["fo_page"]
        nodes = []
        for spec in pages[page_index]:
            line_item_pages = spec["line_item_pages"]
            payload, last = connection(line_item_pages[0], has_next=len(line_item_pages) > 1)
            state["li"][spec["id"]] = {"page": 0, "cursor": last}
            nodes.append({"id": spec["id"], "lineItems": payload})

        payload, last = connection(nodes, has_next=page_index + 1 < len(pages))
        state["fo_page"] = page_index + 1
        state["fo_cursor"] = last
        return {"data": {"order": {"fulfillmentOrders": payload}}}

    def li_page(fulfillment_order_id, after):
        """Serve a further page of one fulfillment order's line items."""
        captured["calls"].append(("li_page", fulfillment_order_id, after))
        tracker = state["li"][fulfillment_order_id]
        assert after == tracker["cursor"], (
            f"lineItems cursor for {fulfillment_order_id}: "
            f"expected {tracker['cursor']!r}, got {after!r}"
        )

        spec = next(
            s for page in captured["fo_pages"] for s in page if s["id"] == fulfillment_order_id
        )
        pages = spec["line_item_pages"]
        tracker["page"] += 1
        nodes = pages[tracker["page"]] if tracker["page"] < len(pages) else []
        payload, last = connection(nodes, has_next=tracker["page"] + 1 < len(pages))
        tracker["cursor"] = last
        return {"data": {"node": {"lineItems": payload}}}

    monkeypatch.setattr(sink, "query_fulfillment_order_line_items",
                        query_fulfillment_order_line_items, raising=False)
    monkeypatch.setattr(sink, "query_fulfillment_order_line_items_page", li_page, raising=False)
    monkeypatch.setattr(
        sink, "query_order",
        lambda order_id: {"data": {"order": {"fulfillmentOrders": {"edges": [{"node": {"id": FO_A}}]}}}},
        raising=False,
    )

    def deploy_mutation(mutation, variables):
        """Capture the mutation variables instead of calling Shopify."""
        captured["variables"] = variables
        return {"data": {"fulfillmentCreateV2": {"fulfillment": {"id": FULFILLMENT_ID}}}}

    monkeypatch.setattr(sink, "deploy_mutation", deploy_mutation, raising=False)
    monkeypatch.setattr(sink, "post_message", lambda response: None, raising=False)
    return sink, captured


def sent_line_items(captured):
    """The lineItemsByFulfillmentOrder actually sent to Shopify."""
    return captured["variables"]["fulfillment"]["lineItemsByFulfillmentOrder"]


def test_absent_line_items_still_fulfills_whole_order(fulfillment_sink):
    """Back-compat: no line_items key means fulfill everything, as before."""
    sink, captured = fulfillment_sink

    fulfillment_id = sink.fulfil_order({"fulfilled": True}, "1234567890")

    assert fulfillment_id == FULFILLMENT_ID
    assert sent_line_items(captured) == [{"fulfillmentOrderId": FO_A}]


def test_partial_fulfillment_sends_only_requested_lines(fulfillment_sink):
    """Only the requested line is fulfilled; its sibling in the same FO is left alone."""
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
    """Callers may send a bare numeric id or a gid; Shopify returns gids."""
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
    fo_calls = [call for call in captured["calls"] if call[0] == "fo_page"]
    assert len(fo_calls) == 2, "second fulfillmentOrders page not requested"
    assert fo_calls[1][1] is not None, "second page requested without a cursor"


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
    """Never request more than the fulfillment order line item has left."""
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


def test_item_on_the_third_fulfillment_order_page_is_found(fulfillment_sink):
    """Three pages: cursors must stay unique and keep advancing."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [
        [fulfillment_order(FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "901", 5)])],
        [fulfillment_order(FO_B, [line_item("gid://shopify/FulfillmentOrderLineItem/2", "902", 5)])],
        [fulfillment_order("gid://shopify/FulfillmentOrder/3333",
                           [line_item("gid://shopify/FulfillmentOrderLineItem/3", "111", 5)])],
    ]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 1}]}, "1234567890"
    )

    assert sent_line_items(captured) == [{
        "fulfillmentOrderId": "gid://shopify/FulfillmentOrder/3333",
        "fulfillmentOrderLineItems": [
            {"id": "gid://shopify/FulfillmentOrderLineItem/3", "quantity": 1}
        ],
    }]
    assert len([c for c in captured["calls"] if c[0] == "fo_page"]) == 3


def test_item_on_the_third_line_item_page_is_found(fulfillment_sink):
    """Three line-item pages within one fulfillment order."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A,
        [line_item("gid://shopify/FulfillmentOrderLineItem/1", "901", 5)],
        [line_item("gid://shopify/FulfillmentOrderLineItem/2", "902", 5)],
        [line_item("gid://shopify/FulfillmentOrderLineItem/3", "111", 5)],
    )]]

    sink.fulfil_order(
        {"fulfilled": True, "line_items": [{"id": "111", "quantity": 2}]}, "1234567890"
    )

    assert sent_line_items(captured)[0]["fulfillmentOrderLineItems"] == [
        {"id": "gid://shopify/FulfillmentOrderLineItem/3", "quantity": 2}
    ]
    assert len([c for c in captured["calls"] if c[0] == "li_page"]) == 2


@pytest.mark.parametrize("bad_item", [
    {"quantity": 1},                      # no id at all
    {"id": None, "quantity": 1},          # explicit null id
    {"id": "", "quantity": 1},            # empty id
    "not-an-object",                      # wrong entry type
])
def test_malformed_entries_are_rejected_not_skipped(fulfillment_sink, bad_item):
    """A malformed entry must not be dropped while its siblings are fulfilled."""
    sink, captured = fulfillment_sink
    captured["fo_pages"] = [[fulfillment_order(
        FO_A, [line_item("gid://shopify/FulfillmentOrderLineItem/1", "111", 5)]
    )]]

    with pytest.raises(ValueError):
        sink.fulfil_order(
            {"fulfilled": True, "line_items": [{"id": "111", "quantity": 1}, bad_item]},
            "1234567890",
        )
    assert "variables" not in captured


def test_invalid_input_makes_no_shopify_lookup(fulfillment_sink):
    """Validation runs before the lookup, so a bad request spends no query capacity."""
    sink, captured = fulfillment_sink

    with pytest.raises(ValueError):
        sink.fulfil_order(
            {"fulfilled": True, "line_items": [{"id": "111", "quantity": 1.9}]}, "1234567890"
        )

    assert not [call for call in captured["calls"] if call[0] in ("fo_page", "li_page")]


@pytest.mark.parametrize(
    "bad_quantity",
    [1.9, 2.0, float("inf"), float("nan"), 0, -1, True, None, "2", object()],
)
def test_invalid_quantities_are_rejected(fulfillment_sink, bad_quantity):
    """Shopify types quantity as Int!, so only positive ints are valid.

    Floats are rejected outright rather than coerced - int() would truncate 1.9 to 1,
    and admitting floats at all drags in inf/nan and precision edge cases for no gain.
    """
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


def test_empty_line_items_list_is_rejected(fulfillment_sink):
    """[] must not silently mean "fulfill everything"."""
    sink, _ = fulfillment_sink

    with pytest.raises(Exception, match="empty line_items"):
        sink.fulfil_order({"fulfilled": True, "line_items": []}, "1234567890")


def test_non_fulfillment_sink_ignores_line_items(fulfillment_sink, monkeypatch):
    """SalesOrders passes the ORDER payload here; its line_items are not fulfillment lines."""
    sink, captured = fulfillment_sink
    monkeypatch.setattr(shopifyGraphQLV2Sink, "name", "SalesOrders")

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
        """Any Shopify call in this test is a failure."""
        pytest.fail("unfulfilled records must not call Shopify")

    monkeypatch.setattr(sink, "query_order", fail_on_shopify_call, raising=False)
    monkeypatch.setattr(sink, "query_fulfillment_order_line_items", fail_on_shopify_call,
                        raising=False)
    monkeypatch.setattr(sink, "query_fulfillment_order_line_items_page", fail_on_shopify_call,
                        raising=False)
    monkeypatch.setattr(sink, "deploy_mutation", fail_on_shopify_call, raising=False)

    assert sink.fulfil_order({"line_items": [{"id": "111", "quantity": 1}]}, "1") is None
    assert "variables" not in captured

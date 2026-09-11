"""Tests for the `consolidate_inheritance` bulk operation (Phase 9).

Covers the pure planner (`plan_consolidation`) with every skip reason and the
handler walk over a real (mocked) composition tree, including the dry run and
the idempotency of a second run.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.bulk import (
    build_bulk_handler,
    plan_consolidation,
)
from meadow.worker.jobs import JobMessage

_DEFINITIONS = [
    {"name": "brand", "type": "text", "inheritable": True},
    {"name": "title", "type": "text", "multilingual": True, "inheritable": True},
    {"name": "color", "type": "enum", "inheritable": False},
    {"name": "gross", "type": "number", "inheritable": True, "computed": True},
    {"name": "sku", "type": "text", "inheritable": True, "is_key": True},
]

_DEFINITION_MAP = {str(d["name"]): d for d in _DEFINITIONS}


def _tree() -> dict[str, dict[str, Any]]:
    """A small composition tree: root -> (a -> (a1, a2), b)."""
    return {
        "object:root": {"id": "object:root", "type": "product", "attributes": {}},
        "object:a": {
            "id": "object:a",
            "type": "product",
            "attributes": {"brand": "Acme", "title": {"en": "Bench"}, "color": "red"},
        },
        "object:a1": {"id": "object:a1", "type": "variant", "attributes": {"brand": "Acme"}},
        "object:a2": {"id": "object:a2", "type": "variant", "attributes": {"brand": "Acme"}},
        "object:b": {
            "id": "object:b",
            "type": "product",
            "attributes": {"brand": "Acme", "color": "red"},
        },
    }


_CHILDREN = {
    "object:root": ["object:a", "object:b"],
    "object:a": ["object:a1", "object:a2"],
    "object:a1": [],
    "object:a2": [],
    "object:b": [],
}


def _planner_children(
    node_attributes: dict[str, Any],
    children: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Runs the planner for one node with the standard (inheritable) definitions."""
    return plan_consolidation(
        parent="object:parent",
        parent_attributes=node_attributes,
        inherited={},
        children=[str(child["id"]) for child in children],
        child_attributes=[dict(child.get("attributes") or {}) for child in children],
        definitions=_DEFINITION_MAP,
        attributes=None,
        min_children=2,
    )


def test_planner_promotes_identical_child_values() -> None:
    moves = _planner_children(
        {},
        [{"id": "object:1", "attributes": {"brand": "Acme"}}] * 2,
    )
    assert len(moves) == 1
    move = moves[0]
    assert move["action"] == "promote"
    assert move["parent_written"] is True
    assert (move["attribute"], move["value"]) == ("brand", "Acme")
    assert move["children"] == ["object:1", "object:1"]
    assert move["reason"] is None


def test_planner_skips_a_node_with_too_few_children() -> None:
    moves = _planner_children({}, [{"id": "object:1", "attributes": {"brand": "Acme"}}])
    assert [(m["action"], m["reason"]) for m in moves] == [("skip", "no_children")]


def test_planner_skips_when_a_child_does_not_maintain_the_attribute() -> None:
    moves = _planner_children(
        {},
        [
            {"id": "object:1", "attributes": {"brand": "Acme"}},
            {"id": "object:2", "attributes": {}},
        ],
    )
    assert [(m["action"], m["reason"]) for m in moves] == [("skip", "missing_on_child")]
    assert moves[0]["value"] == "Acme"  # the maintained value is still reported


def test_planner_skips_an_explicitly_empty_child_value() -> None:
    moves = _planner_children(
        {},
        [
            {"id": "object:1", "attributes": {"brand": "Acme"}},
            {"id": "object:2", "attributes": {"brand": None}},
        ],
    )
    assert [(m["action"], m["reason"]) for m in moves] == [("skip", "interrupted")]


def test_planner_skips_differing_child_values() -> None:
    moves = _planner_children(
        {},
        [
            {"id": "object:1", "attributes": {"brand": "Acme"}},
            {"id": "object:2", "attributes": {"brand": "Other"}},
        ],
    )
    assert [(m["action"], m["reason"]) for m in moves] == [("skip", "conflict_child_values")]
    assert moves[0]["value"] is None


def test_planner_skips_a_differently_maintained_parent_value() -> None:
    moves = _planner_children(
        {"brand": "Other"},
        [{"id": "object:1", "attributes": {"brand": "Acme"}}] * 2,
    )
    assert [(m["action"], m["reason"]) for m in moves] == [("skip", "conflict_parent")]


def test_planner_promotes_without_writing_when_the_parent_already_has_the_value() -> None:
    moves = _planner_children(
        {"brand": "Acme"},
        [{"id": "object:1", "attributes": {"brand": "Acme"}}] * 2,
    )
    assert [(m["action"], m["parent_written"]) for m in moves] == [("promote", False)]


def test_planner_promotes_without_writing_when_an_ancestor_provides_the_value() -> None:
    moves = plan_consolidation(
        parent="object:parent",
        parent_attributes={},
        inherited={"brand": "Acme"},
        children=["object:1", "object:2"],
        child_attributes=[{"brand": "Acme"}, {"brand": "Acme"}],
        definitions=_DEFINITION_MAP,
        attributes=None,
        min_children=2,
    )
    assert [(m["action"], m["parent_written"]) for m in moves] == [("promote", False)]


def test_planner_skips_computed_key_and_non_inheritable_attributes() -> None:
    for attribute in ("gross", "sku", "color"):
        moves = _planner_children(
            {},
            [
                {"id": "object:1", "attributes": {attribute: 1}},
                {"id": "object:2", "attributes": {attribute: 1}},
            ],
        )
        assert [(m["attribute"], m["action"], m["reason"]) for m in moves] == [
            (attribute, "skip", "not_inheritable")
        ], attribute


def test_planner_ignores_attributes_without_a_definition() -> None:
    moves = _planner_children(
        {},
        [
            {"id": "object:1", "attributes": {"name": "Bench"}},
            {"id": "object:2", "attributes": {"name": "Bench"}},
        ],
    )
    assert moves == []


def test_planner_respects_the_attribute_filter() -> None:
    moves = plan_consolidation(
        parent="object:parent",
        parent_attributes={},
        inherited={},
        children=["object:1", "object:2"],
        child_attributes=[{"brand": "Acme"}, {"brand": "Acme"}],
        definitions=_DEFINITION_MAP,
        attributes={"other"},
        min_children=2,
    )
    assert moves == []


def _tree_client(state: dict[str, dict[str, Any]], requests: list[httpx.Request] | None = None):
    """Builds a client whose transport serves (and mutates) the mocked tree."""

    async def transport(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        path = request.url.path
        if path == "/attribute-definitions":
            return httpx.Response(200, json=_DEFINITIONS)
        if path.endswith("/children"):
            parent = path[len("/objects/") : -len("/children")]
            children = [
                {**state[child_id], "hasChildren": bool(_CHILDREN.get(child_id))}
                for child_id in _CHILDREN.get(parent, [])
                if child_id in state
            ]
            return httpx.Response(200, json=children)
        if path.startswith("/objects/"):
            object_id = path[len("/objects/") :]
            if request.method == "PATCH":
                payload = json.loads(request.content)
                attributes = dict(state[object_id]["attributes"])
                attributes.update(payload.get("attributes") or {})
                for name in payload.get("remove_attributes") or []:
                    attributes.pop(name, None)
                state[object_id] = {**state[object_id], "attributes": attributes}
                return httpx.Response(200, json=state[object_id])
            if object_id not in state:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=state[object_id])
        return httpx.Response(404, json={"detail": "unexpected"})

    return httpx.AsyncClient(transport=httpx.MockTransport(transport))


def _message(parameters: dict[str, Any], object_ids: list[str] | None = None) -> JobMessage:
    return JobMessage(
        run_id="job_run:1",
        type="bulk_operation",
        parameters={
            "op": "consolidate_inheritance",
            "object_ids": object_ids or ["object:root"],
            "parameters": parameters,
        },
    )


async def test_handler_dry_run_reports_the_proposal_without_writing() -> None:
    state = _tree()
    requests: list[httpx.Request] = []
    client = _tree_client(state, requests)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(_message({"dry_run": True}))

    assert report["op"] == "consolidate_inheritance"
    assert report["dry_run"] is True
    assert report["promoted"] == 2
    assert report["removed"] == 4
    assert report["nodes"] == 2  # root and `object:a`; leaves are not examined
    assert report["failed"] == 0
    assert [request.method for request in requests if request.method == "PATCH"] == []
    moves = {(m["parent"], m["attribute"]): m for m in report["moves"]}
    assert (
        moves[("object:root", "brand")]["action"],
        moves[("object:root", "brand")]["value"],
    ) == (
        "promote",
        "Acme",
    )
    assert moves[("object:root", "title")]["reason"] == "missing_on_child"
    assert moves[("object:root", "color")]["reason"] == "not_inheritable"
    # `object:a` no longer maintains `brand` in the run's own view, so its
    # children only lose their copies (no second write at `object:a`).
    assert moves[("object:a", "brand")]["action"] == "promote"
    assert moves[("object:a", "brand")]["parent_written"] is False


async def test_handler_applies_the_promotions_and_is_idempotent() -> None:
    state = _tree()
    client = _tree_client(state)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(_message({}))

    assert report["dry_run"] is False
    assert report["promoted"] == 2
    assert state["object:root"]["attributes"] == {"brand": "Acme"}
    # The children only keep what they still need: `object:a` keeps `title`/`color`.
    assert state["object:a"]["attributes"] == {"title": {"en": "Bench"}, "color": "red"}
    assert state["object:b"]["attributes"] == {"color": "red"}
    assert state["object:a1"]["attributes"] == {}
    assert state["object:a2"]["attributes"] == {}

    second = await handler(_message({"dry_run": True}))
    assert second["promoted"] == 0
    assert second["removed"] == 0
    assert state["object:root"]["attributes"] == {"brand": "Acme"}


async def test_handler_honours_min_children() -> None:
    state = _tree()
    # With min_children = 3 neither the root (2 children) nor `object:a`
    # (2 children) may push anything up.
    handler = build_bulk_handler("http://core", client=_tree_client(state))

    report = await handler(_message({"min_children": 3}))

    assert report["min_children"] == 3
    assert report["promoted"] == 0
    assert {m["reason"] for m in report["moves"]} == {"no_children"}
    assert state["object:a"]["attributes"] == {
        "brand": "Acme",
        "title": {"en": "Bench"},
        "color": "red",
    }


async def test_handler_reports_a_failing_root() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/attribute-definitions":
            return httpx.Response(200, json=_DEFINITIONS)
        return httpx.Response(500, json={"detail": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(_message({}, object_ids=["object:broken"]))

    assert report["failed"] == 1
    assert report["succeeded"] == 0
    assert report["results"][0]["status"] == "error"

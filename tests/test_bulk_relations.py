"""Tests for the relation bulk operations (`add_relation` / `remove_relation`).

They assign one target object to a whole selection (Phase 9, mass maintenance):
either as a generic reference edge (`POST /references`) or – for a collection
membership – through the collections endpoint, which also maintains
`rank`/`added_at`. Both operations are idempotent.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.bulk import build_bulk_handler
from meadow.worker.jobs import JobMessage

_OBJECTS: dict[str, dict[str, Any]] = {
    "object:1": {"id": "object:1", "type": "product", "attributes": {}},
    "object:2": {"id": "object:2", "type": "product", "attributes": {}},
    "object:target": {"id": "object:target", "type": "product", "attributes": {}},
    "object:coll": {"id": "object:coll", "type": "collection", "attributes": {}},
}


def _client(
    *,
    references: dict[str, list[dict[str, Any]]] | None = None,
    requests: list[httpx.Request] | None = None,
    added: int = 1,
):
    """Transport over a small in-memory world (references per object)."""
    refs = references if references is not None else {}
    edges: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        path = request.url.path
        if path == "/attribute-definitions":
            return httpx.Response(200, json=[])
        if path.startswith("/collections/") and path.endswith("/members"):
            return httpx.Response(200, json={"added": added, "skipped": 1 - added})
        if path == "/references" and request.method == "POST":
            payload = json.loads(request.content)
            edge = {
                "id": f"reference:{len(edges) + 1}",
                "definition_id": payload["definition_id"],
                "in": payload["source"],
                "out": payload["target"],
            }
            edges.append(edge)
            return httpx.Response(201, json=edge)
        if path.startswith("/references/") and request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/references"):
            object_id = path[len("/objects/") : -len("/references")]
            return httpx.Response(200, json=refs.get(object_id, []))
        if path.startswith("/objects/"):
            object_id = path[len("/objects/") :]
            if object_id in _OBJECTS:
                return httpx.Response(200, json=_OBJECTS[object_id])
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(404, json={"detail": "unexpected"})

    return httpx.AsyncClient(transport=httpx.MockTransport(transport))


def _message(
    op: str, parameters: dict[str, Any], object_ids: list[str] | None = None
) -> JobMessage:
    return JobMessage(
        run_id="job_run:1",
        type="bulk_operation",
        parameters={
            "op": op,
            "object_ids": object_ids or ["object:1", "object:2"],
            "parameters": parameters,
        },
    )


async def test_add_relation_creates_one_edge_per_object() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests=requests)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message("add_relation", {"definition_id": "rel_related", "target": "object:target"})
    )

    assert report["op"] == "add_relation"
    assert report["processed"] == 2
    assert report["succeeded"] == 2
    assert report["skipped"] == 0
    assert report["relations"] == 2
    created = [
        json.loads(request.content)
        for request in requests
        if request.url.path == "/references" and request.method == "POST"
    ]
    assert [edge["source"] for edge in created] == ["object:1", "object:2"]
    assert {edge["target"] for edge in created} == {"object:target"}
    assert {edge["definition_id"] for edge in created} == {"rel_related"}


async def test_add_relation_skips_existing_edges() -> None:
    references = {
        "object:1": [
            {
                "id": "reference:existing",
                "definition_id": "rel_related",
                "in": "object:1",
                "out": "object:target",
            }
        ]
    }
    requests: list[httpx.Request] = []
    client = _client(references=references, requests=requests)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message("add_relation", {"definition_id": "rel_related", "target": "object:target"})
    )

    assert report["succeeded"] == 1  # object:2 got a new edge
    assert report["skipped"] == 1  # object:1 was already linked
    assert report["relations"] == 1
    assert report["results"][0]["status"] == "skipped"
    assert report["results"][0]["changed"] == 0
    assert len([r for r in requests if r.url.path == "/references" and r.method == "POST"]) == 1


async def test_add_relation_detects_an_edge_in_the_other_direction() -> None:
    references = {
        "object:1": [
            {
                "id": "reference:reverse",
                "definition_id": "rel_related",
                "in": "object:target",
                "out": "object:1",
            }
        ]
    }
    client = _client(references=references)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message(
            "add_relation",
            {"definition_id": "rel_related", "target": "object:target"},
            object_ids=["object:1"],
        )
    )

    # A symmetric relation must not be created twice.
    assert report["skipped"] == 1
    assert report["results"][0]["status"] == "skipped"


async def test_add_relation_uses_the_collections_endpoint_for_membership() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests=requests)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message(
            "add_relation",
            {"definition_id": "rel_collection_member", "target": "object:coll"},
            object_ids=["object:1"],
        )
    )

    assert report["succeeded"] == 1
    assert report["relations"] == 1
    membership = [r for r in requests if r.url.path == "/collections/object:coll/members"]
    assert len(membership) == 1
    assert json.loads(membership[0].content) == {"object_ids": ["object:1"]}
    # The generic reference endpoint must not be used for a collection membership.
    assert not [r for r in requests if r.url.path == "/references" and r.method == "POST"]


async def test_add_relation_reports_an_existing_membership_as_skipped() -> None:
    client = _client(added=0)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message(
            "add_relation",
            {"definition_id": "rel_collection_member", "target": "object:coll"},
            object_ids=["object:1"],
        )
    )

    assert report["skipped"] == 1
    assert report["succeeded"] == 0
    assert report["results"][0]["changed"] == 0


async def test_remove_relation_deletes_every_matching_edge() -> None:
    references = {
        "object:1": [
            {
                "id": "reference:a",
                "definition_id": "rel_related",
                "in": "object:1",
                "out": "object:target",
            },
            {
                "id": "reference:b",
                "definition_id": "rel_related",
                "in": "object:target",
                "out": "object:1",
            },
            {
                "id": "reference:other",
                "definition_id": "rel_related",
                "in": "object:1",
                "out": "object:elsewhere",
            },
        ]
    }
    requests: list[httpx.Request] = []
    client = _client(references=references, requests=requests)
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message(
            "remove_relation",
            {"definition_id": "rel_related", "target": "object:target"},
            object_ids=["object:1", "object:2"],
        )
    )

    deleted = [r.url.path for r in requests if r.method == "DELETE"]
    assert deleted == ["/references/reference:a", "/references/reference:b"]
    assert report["succeeded"] == 1
    assert report["skipped"] == 1  # object:2 had no such relation
    assert report["results"][0]["removed"] == 2


async def test_relation_operations_require_definition_and_target() -> None:
    client = _client()
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(_message("add_relation", {"definition_id": "rel_related"}))

    assert report["failed"] == 2
    assert (
        report["results"][0]["error"]
        == "`parameters.definition_id` and `parameters.target` are required"
    )


async def test_relation_operations_can_send_for_approval_afterwards() -> None:
    patched: list[str] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/references") and request.method == "GET":
            return httpx.Response(200, json=[])
        if path == "/references" and request.method == "POST":
            return httpx.Response(201, json={"id": "reference:x"})
        if request.method == "PATCH":
            patched.append(json.loads(request.content).get("status", ""))
            return httpx.Response(200, json={"id": "object:1"})
        return httpx.Response(404, json={"detail": "unexpected"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)

    report = await handler(
        _message(
            "add_relation",
            {
                "definition_id": "rel_related",
                "target": "object:target",
                "send_for_approval": True,
            },
            object_ids=["object:1"],
        )
    )

    assert report["succeeded"] == 1
    assert report["approvals"] == 1
    assert patched == ["in_review"]

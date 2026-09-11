"""Tests for the `send_for_approval` bulk operation and approval modifier."""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.bulk import (
    DEFAULT_APPROVAL_STATUS,
    build_bulk_handler,
    request_approval,
)
from meadow.worker.jobs import JobMessage


async def test_request_approval_reports_the_target_status() -> None:
    patched: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/objects/object:1"
        patched.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    outcome = await request_approval(client, "http://core", "object:1")

    assert outcome == {"approval": DEFAULT_APPROVAL_STATUS}
    assert patched == [{"status": "in_review"}]


async def test_request_approval_never_raises_on_a_rejected_transition() -> None:
    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "transition not allowed"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    outcome = await request_approval(client, "http://core", "object:1", "published")

    assert "approval" not in outcome
    assert "409" in outcome["approval_error"]


async def test_handler_sends_the_selection_for_approval() -> None:
    patched: list[tuple[str, dict[str, Any]]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            patched.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"id": "object:1"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:1",
        type="bulk_operation",
        parameters={
            "op": "send_for_approval",
            "object_ids": ["object:1", "object:2"],
            "parameters": {},
        },
    )

    report = await handler(message)

    assert report["succeeded"] == 2
    assert report["approvals"] == 2
    assert [r["approval"] for r in report["results"]] == ["in_review", "in_review"]
    assert [r["status"] for r in report["results"]] == ["ok", "ok"]
    assert patched == [
        ("/objects/object:1", {"status": "in_review"}),
        ("/objects/object:2", {"status": "in_review"}),
    ]


async def test_handler_honours_a_custom_approval_status() -> None:
    statuses: list[Any] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        statuses.append(json.loads(request.content)["status"])
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:2",
        type="bulk_operation",
        parameters={
            "op": "send_for_approval",
            "object_ids": ["object:1"],
            "parameters": {"approval_status": "approved"},
        },
    )

    report = await handler(message)

    assert statuses == ["approved"]
    assert report["results"][0]["approval"] == "approved"


async def test_handler_sends_for_approval_after_a_change() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "object:1", "type": "bench", "attributes": {"supplier": "Acme"}},
            )
        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:3",
        type="bulk_operation",
        parameters={
            "op": "set_attribute",
            "object_ids": ["object:1"],
            "parameters": {
                "attribute": "supplier",
                "value": "Neu GmbH",
                "send_for_approval": True,
            },
        },
    )

    report = await handler(message)

    assert report["succeeded"] == 1
    assert report["approvals"] == 1
    assert report["results"][0]["approval"] == "in_review"
    # The attribute change comes first, the transition second.
    assert calls == [
        ("PATCH", "/objects/object:1", {"attributes": {"supplier": "Neu GmbH"}}),
        ("PATCH", "/objects/object:1", {"status": "in_review"}),
    ]


async def test_handler_keeps_the_change_when_the_transition_is_rejected() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "object:1", "type": "bench", "attributes": {}},
            )
        payload = json.loads(request.content)
        if "status" in payload:
            return httpx.Response(409, json={"error": "workflow guard rejected"})
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:4",
        type="bulk_operation",
        parameters={
            "op": "set_attribute",
            "object_ids": ["object:1"],
            "parameters": {"attribute": "supplier", "value": "X", "send_for_approval": True},
        },
    )

    report = await handler(message)

    # The change itself succeeded; only the approval was blocked.
    assert report["succeeded"] == 1
    assert report["failed"] == 0
    assert report["approvals"] == 0
    assert "approval" not in report["results"][0]
    assert "409" in report["results"][0]["approval_error"]


async def test_handler_skips_the_approval_when_nothing_matched() -> None:
    calls: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/objects/object:1":
            return httpx.Response(
                200,
                json={"id": "object:1", "type": "bench", "attributes": {"name": {"en": "Other"}}},
            )
        if request.method == "GET" and request.url.path == "/attribute-definitions":
            return httpx.Response(200, json=[])
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:5",
        type="bulk_operation",
        parameters={
            "op": "replace_text",
            "object_ids": ["object:1"],
            "parameters": {"search": "Acme", "replace": "Neu", "send_for_approval": True},
        },
    )

    report = await handler(message)

    assert report["skipped"] == 1
    assert report["approvals"] == 0
    assert calls == []  # neither the attributes nor the status were written


async def test_handler_ignores_the_modifier_on_a_status_change() -> None:
    statuses: list[Any] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        statuses.append(json.loads(request.content)["status"])
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:6",
        type="bulk_operation",
        parameters={
            "op": "change_status",
            "object_ids": ["object:1"],
            "parameters": {"status": "published", "send_for_approval": True},
        },
    )

    report = await handler(message)

    assert statuses == ["published"]  # no extra `in_review` transition
    assert report["results"][0]["new_status"] == "published"
    assert report["results"][0]["status"] == "ok"  # record status stays intact

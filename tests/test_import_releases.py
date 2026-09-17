"""Tests for the channel releases of a MEF import (`meadow/worker/imports.py`).

A release is channel state in its own table, so a MEF document carries it per
object (`[{channel, status}]`) and the import replays it with
`PUT /objects/{id}/releases`: without that step a restored instance has the
objects but has released nothing (`plan/detail/workflow-freigaben.md`).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.imports import _import_releases

CORE = "http://127.0.0.1:8080"


def _objects() -> list[dict[str, Any]]:
    return [
        {
            "id": "object:child",
            "type": "variant",
            "releases": [{"channel": "webshop", "status": "published"}],
        },
        {
            "id": "object:unwritten",
            "type": "variant",
            "releases": [{"channel": "print-2026", "status": "in_review"}],
        },
        {"id": "object:plain", "type": "variant"},
    ]


async def _run(
    *,
    ids: dict[str, str] | None = None,
    status: int = 200,
    error: str = "rejected by a workflow_guard rule",
):
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": error})

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _import_releases(
            client, CORE, _objects(), {"object:child": "object:child"} if ids is None else ids
        )
    return result, requests


async def test_releases_are_applied_per_object_and_channel() -> None:
    result, requests = await _run()

    assert result["applied"] == 1
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PUT"
    assert request.url.path == "/objects/object:child/releases"
    assert json.loads(request.content) == {"channel": "webshop", "status": "published"}
    # A release of an object the run did not write is reported, not silently lost.
    assert result["failed"] == 1
    assert result["failures"] == [
        {"id": "object:unwritten", "channel": "print-2026", "error": "object was not written"}
    ]


async def test_a_rejected_release_is_reported_not_raised() -> None:
    result, requests = await _run(status=409)

    assert result["applied"] == 0
    assert len(requests) == 1
    # The rejected release plus the release of the object the run did not write.
    assert [failure["channel"] for failure in result["failures"]] == ["webshop", "print-2026"]
    assert "workflow_guard" in result["failures"][0]["error"]


async def test_objects_without_releases_need_no_request() -> None:
    result, requests = await _run(ids={})

    # `object:child` is not mapped either, so both releases are reported.
    assert requests == []
    assert result["applied"] == 0
    assert result["failed"] == 2

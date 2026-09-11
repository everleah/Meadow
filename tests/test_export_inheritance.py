"""Tests for the inherited-values export option (Phase 9)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.exports import _resolve_objects, _structured_export


def _transport(seen: list[dict[str, Any]], objects: list[dict[str, Any]]):
    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/objects":
            seen.append(dict(request.url.params))
            return httpx.Response(200, json=objects)
        if request.url.path == "/attribute-definitions":
            return httpx.Response(200, json=[])
        if request.url.path == "/languages":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return transport


async def test_resolve_objects_requests_inherited_values() -> None:
    seen: list[dict[str, Any]] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_transport(seen, [])))

    await _resolve_objects(
        client, "http://core", {"kind": "type", "type": "bench"}, resolve_inheritance=True
    )
    await _resolve_objects(client, "http://core", {"kind": "type", "type": "bench"})

    assert seen[0]["resolve_inheritance"] == "true"
    assert seen[0]["type"] == "bench"
    assert "resolve_inheritance" not in seen[1]


async def test_structured_export_exports_inherited_values_on_demand() -> None:
    objects = [
        {
            "id": "object:1",
            "type": "bench",
            "status": "draft",
            "attributes": {"brand": "Acme", "plain": "x"},
        }
    ]
    requests: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/objects":
            requests.append(dict(request.url.params))
            return httpx.Response(200, json=objects)
        if request.url.path == "/attribute-definitions":
            return httpx.Response(200, json=[])
        if request.url.path == "/languages":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    plain = await _structured_export(
        client,
        "http://core",
        {
            "source": {"kind": "type", "type": "bench"},
            "format": "csv",
            "fields": ["id", "brand"],
            "locales": ["en"],
            "options": {},
        },
    )
    inherited = await _structured_export(
        client,
        "http://core",
        {
            "source": {"kind": "type", "type": "bench"},
            "format": "csv",
            "fields": ["id", "brand"],
            "locales": ["en"],
            "options": {"resolve_inheritance": True},
        },
    )

    assert "resolve_inheritance" not in requests[0]
    assert requests[-1]["resolve_inheritance"] == "true"
    # Both exports carry the same columns; the difference is the core's answer.
    assert plain["columns"] == inherited["columns"] == ["id", "brand"]
    assert json.loads(json.dumps(inherited["exported"])) == 1

"""Tests for the business_rule recompute job handler (Phase 5, step 4)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.business_rules import (
    build_business_rule_handler,
    evaluate_object,
    load_objects,
    write_computed,
)
from meadow.worker.jobs import JobMessage


def _computed_script() -> str:
    return (
        "def run(obj, ctx, result):\n"
        "    result.set_value('gross_price', round(obj['attributes']['net_price'] * 1.19, 2))\n"
    )


def test_evaluate_object_computed() -> None:
    obj = {"id": "object:1", "type": "product", "status": "draft", "attributes": {"net_price": 100}}
    outcome = evaluate_object(_computed_script(), "computed", obj)
    assert outcome["computed"] == {"gross_price": 119.0}
    assert outcome["errors"] == []
    assert outcome["warnings"] == []


def test_evaluate_object_reports_errors() -> None:
    script = (
        "def run(obj, ctx, result):\n"
        "    result.add_error('price', 'required', 'price is required')\n"
    )
    obj = {"id": "object:1", "type": "product", "status": "draft", "attributes": {}}
    outcome = evaluate_object(script, "bulk", obj)
    assert outcome["errors"] == [
        {"attr": "price", "code": "required", "message": "price is required"}
    ]


async def test_load_objects_by_type() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/objects"
        assert request.url.params["type"] == "product"
        return httpx.Response(
            200,
            json=[{"id": "object:1", "type": "product", "attributes": {}}],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects = await load_objects(client, "http://core", [], ["product"])
    assert [obj["id"] for obj in objects] == ["object:1"]


async def test_load_objects_by_ids() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/objects/object:7"
        return httpx.Response(200, json={"id": "object:7", "type": "product", "attributes": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects = await load_objects(client, "http://core", ["object:7"], [])
    assert [obj["id"] for obj in objects] == ["object:7"]


async def test_write_computed_sends_values() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/objects/object:1/computed"
        assert request.headers["x-api-key"] == "secret"
        assert json.loads(request.content) == {"values": {"gross_price": 119}}
        return httpx.Response(200, json={"id": "object:1"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        headers={"x-api-key": "secret"},
    )
    await write_computed(client, "http://core", "object:1", {"gross_price": 119})


async def test_handler_recomputes_and_reports() -> None:
    patched: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/objects":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "object:1",
                        "type": "product",
                        "status": "draft",
                        "attributes": {"net_price": 100},
                    }
                ],
            )
        if request.method == "PATCH" and request.url.path == "/objects/object:1/computed":
            patched.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "object:1"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_business_rule_handler("http://core", api_key="secret", client=client)
    message = JobMessage(
        run_id="job_run:1",
        type="business_rule",
        parameters={
            "rule": {"name": "gross_from_net", "type": "computed", "script": _computed_script()},
            "applies_to": ["product"],
            "object_ids": [],
        },
    )

    report = await handler(message)

    assert report["processed"] == 1
    assert report["succeeded"] == 1
    assert report["failed"] == 0
    assert report["results"][0]["computed"] == {"gross_price": 119.0}
    assert patched == [{"values": {"gross_price": 119.0}}]


async def test_handler_isolates_per_object_failures() -> None:
    script = (
        "def run(obj, ctx, result):\n"
        "    if obj['attributes'].get('bad'):\n"
        "        raise ValueError('boom')\n"
        "    result.set_value('ok', True)\n"
    )

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/objects":
            return httpx.Response(
                200,
                json=[
                    {"id": "object:1", "type": "product", "status": "draft", "attributes": {}},
                    {
                        "id": "object:2",
                        "type": "product",
                        "status": "draft",
                        "attributes": {"bad": True},
                    },
                ],
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "object:1"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_business_rule_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:1",
        type="business_rule",
        parameters={
            "rule": {"name": "test", "type": "bulk", "script": script},
            "applies_to": ["product"],
            "object_ids": [],
        },
    )

    report = await handler(message)

    assert report["processed"] == 2
    assert report["succeeded"] == 1
    assert report["failed"] == 1
    statuses = [r["status"] for r in report["results"]]
    assert statuses == ["succeeded", "failed"]
    assert report["results"][1]["error"] == "boom"


async def test_handler_requires_script() -> None:
    handler = build_business_rule_handler("http://core")
    message = JobMessage(run_id="job_run:1", type="business_rule", parameters={})
    try:
        await handler(message)
    except RuntimeError as exc:
        assert "rule.script" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError")

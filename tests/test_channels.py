"""Tests for channels and channel-specific releases (Phase 9).

Covers the pure display helpers (`release_badges`, `channel_label`), the worker's
bulk operations `release_for_channel`/`withdraw_from_channel` (idempotent, via
`PUT`/`DELETE /objects/{id}/releases`) and the channel export source
(`?released_for=`). See `plan/detail/workflow-freigaben.md`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.frontend.display import (
    DEFAULT_RELEASE_STATUS,
    RELEASE_STATUSES,
    channel_label,
    channel_options,
    release_badges,
)
from meadow.worker.bulk import build_bulk_handler
from meadow.worker.exports import _resolve_objects
from meadow.worker.jobs import JobMessage

_CHANNELS: list[dict[str, Any]] = [
    {"id": "channel:webshop", "code": "webshop", "label": {"de": "Webshop", "en": "Web shop"}},
    {"id": "channel:print", "code": "print", "label": {"de": "Printkatalog"}},
]


def test_release_vocabulary_uses_the_object_status_state_machine() -> None:
    assert RELEASE_STATUSES[0] == "draft"
    assert DEFAULT_RELEASE_STATUS == "published"
    assert DEFAULT_RELEASE_STATUS in RELEASE_STATUSES
    # The state machine has five states (`plan/detail/workflow-freigaben.md`):
    # `draft → in_review → approved → published → archived`. `approved` was missing
    # from every selector until 2026-09-21 — an `approved` object answered 500 on the
    # product form (`ValueError: Invalid value: approved`).
    assert RELEASE_STATUSES == ("draft", "in_review", "approved", "published", "archived")


def test_channel_label_falls_back_to_another_language_then_to_the_code() -> None:
    assert channel_label(_CHANNELS[0], "de") == "Webshop"
    assert channel_label(_CHANNELS[1], "de") == "Printkatalog"
    # No label for the requested language -> another language, like every other
    # multilingual data label (`resolve_value`).
    assert channel_label(_CHANNELS[1], "en") == "Printkatalog"
    assert channel_label({"code": "print"}, "de") == "print"
    assert channel_label(None, "de") == ""
    assert channel_options(_CHANNELS, "de") == {"webshop": "Webshop", "print": "Printkatalog"}


def test_release_badges_render_one_chip_per_channel() -> None:
    html = release_badges(
        [{"channel": "webshop", "status": "published"}, {"channel": "print", "status": "draft"}],
        {"webshop": "Web shop", "print": "Print"},
    )
    assert html.count("<span") == 2
    assert "Web shop" in html and "Print" in html
    # The state and the code stay in the tooltip (the chip shows the channel).
    assert 'title="webshop: published"' in html
    assert 'title="print: draft"' in html
    assert "bg-primary" in html  # published
    assert "bg-neutral" in html  # draft


def test_release_badges_are_empty_without_releases() -> None:
    assert release_badges(None) == ""
    assert release_badges([]) == ""


def test_release_badges_escape_channel_names() -> None:
    html = release_badges([{"channel": "web", "status": "published"}], {"web": "<b>x</b>"})
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def _bulk_message(op: str, parameters: dict[str, Any]) -> JobMessage:
    return JobMessage(
        run_id="job_run:1",
        type="bulk_operation",
        parameters={"op": op, "object_ids": ["object:1"], "parameters": parameters},
    )


async def test_bulk_release_for_channel_writes_the_release() -> None:
    written: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/objects/object:1/releases":
            return httpx.Response(200, json=[])
        if request.method == "PUT" and request.url.path == "/objects/object:1/releases":
            written.append(json.loads(request.content))
            return httpx.Response(200, json={"channel": "webshop", "status": "published"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    report = await build_bulk_handler("http://core", client=client)(
        _bulk_message("release_for_channel", {"channel": "webshop"})
    )

    assert written == [{"channel": "webshop", "status": "published"}]
    assert report["succeeded"] == 1
    assert report["skipped"] == 0
    assert report["releases"] == 1
    assert report["results"][0]["release_status"] == "published"


async def test_bulk_release_for_channel_is_idempotent() -> None:
    calls: list[str] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(200, json=[{"channel": "webshop", "status": "published"}])
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    report = await build_bulk_handler("http://core", client=client)(
        _bulk_message("release_for_channel", {"channel": "webshop"})
    )

    assert report["skipped"] == 1
    assert report["succeeded"] == 0
    assert report["releases"] == 0
    assert report["results"][0]["changed"] == 0
    assert calls == ["GET /objects/object:1/releases"]  # no write for an unchanged release


async def test_bulk_withdraw_from_channel_deletes_the_release() -> None:
    calls: list[str] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(200, json=[{"channel": "webshop", "status": "published"}])
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    report = await build_bulk_handler("http://core", client=client)(
        _bulk_message("withdraw_from_channel", {"channel": "webshop"})
    )

    assert calls == ["GET /objects/object:1/releases", "DELETE /objects/object:1/releases/webshop"]
    assert report["succeeded"] == 1
    assert report["releases"] == 1


async def test_bulk_withdraw_from_channel_skips_without_a_release() -> None:
    calls: list[str] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    report = await build_bulk_handler("http://core", client=client)(
        _bulk_message("withdraw_from_channel", {"channel": "webshop"})
    )

    assert calls == ["GET /objects/object:1/releases"]
    assert report["skipped"] == 1


async def test_bulk_release_requires_a_channel() -> None:
    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    report = await build_bulk_handler("http://core", client=client)(
        _bulk_message("release_for_channel", {})
    )

    assert report["failed"] == 1
    assert "parameters.channel" in report["results"][0]["error"]


async def test_export_source_channel_filters_by_release() -> None:
    seen: list[dict[str, str]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": "object:1", "type": "product"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    objects = await _resolve_objects(
        client, "http://core", {"kind": "channel", "channel": "webshop"}
    )

    assert objects == [{"id": "object:1", "type": "product"}]
    # `start` is part of every page request: the list endpoint caps `limit` at 1000,
    # so a source with more objects has to be read page by page.
    assert seen == [{"released_for": "webshop", "limit": "1000", "start": "0"}]


async def test_export_source_channel_accepts_a_release_status() -> None:
    seen: list[dict[str, str]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    await _resolve_objects(
        client,
        "http://core",
        {"kind": "channel", "channel": "webshop", "release_status": "approved"},
    )

    assert seen == [
        {"released_for": "webshop", "limit": "1000", "start": "0", "release_status": "approved"}
    ]


async def test_export_source_channel_requires_a_code() -> None:
    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        await _resolve_objects(client, "http://core", {"kind": "channel"})
    except RuntimeError as error:
        assert "source.kind='channel'" in str(error)
    else:  # pragma: no cover - the call must fail
        raise AssertionError("a channel source without a code must be rejected")

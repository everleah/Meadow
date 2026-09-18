"""Tests for the `replace_text` bulk operation (Find & Replace, Phase 9)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from meadow.worker.bulk import (
    _is_replaceable,
    build_bulk_handler,
    replace_in_value,
)
from meadow.worker.jobs import JobMessage

_DEFINITIONS: list[dict[str, Any]] = [
    {"name": "title", "type": "text", "multilingual": True},
    {"name": "supplier", "type": "text"},
    {"name": "homepage", "type": "url"},
    {"name": "computed_alpha", "type": "text", "computed": True},
    {"name": "color", "type": "enum"},
    {"name": "width", "type": "number"},
]


def _definitions_for(names: list[str]) -> dict[str, dict[str, Any]]:
    """Minimal `{name: definition}` map as the worker builds it per job run."""
    return {name: next(d for d in _DEFINITIONS if d["name"] == name) for name in names}


def test_replace_in_value_handles_plain_strings() -> None:
    value, count = replace_in_value("Acme Bench", "Acme", "Neu")
    assert (value, count) == ("Neu Bench", 1)


def test_replace_in_value_handles_multilingual_maps() -> None:
    value, count = replace_in_value({"de": "Bank Acme", "en": "Acme Bench"}, "Acme", "Neu")
    assert count == 2
    assert value == {"de": "Bank Neu", "en": "Neu Bench"}


def test_replace_in_value_handles_nested_lists_and_ignores_non_strings() -> None:
    value, count = replace_in_value({"tags": ["Acme", 42, {"x": "Acme AG"}], "n": 7}, "Acme", "Neu")
    assert count == 2
    assert value == {"tags": ["Neu", 42, {"x": "Neu AG"}], "n": 7}


def test_replace_in_value_keeps_the_case_of_the_surrounding_text() -> None:
    value, count = replace_in_value("ACME and acme", "acme", "neu")
    assert (value, count) == ("neu and neu", 2)


def test_replace_in_value_is_case_sensitive_on_demand() -> None:
    value, count = replace_in_value("Acme acme", "acme", "neu", case_sensitive=True)
    assert (value, count) == ("Acme neu", 1)


def test_replace_in_value_keeps_backslashes_in_the_replacement() -> None:
    value, count = replace_in_value(r"path ACME", "ACME", r"C:\data\1")
    assert (value, count) == (r"path C:\data\1", 1)


def test_replace_in_value_without_a_match_changes_nothing() -> None:
    value, count = replace_in_value({"de": "Bench"}, "Acme", "Neu")
    assert (value, count) == ({"de": "Bench"}, 0)


def test_is_replaceable_covers_convention_and_text_attributes() -> None:
    definitions = _definitions_for(["title", "computed_alpha", "color", "width"])
    # A convention field has no definition but is replaceable.
    assert _is_replaceable("name", definitions)
    assert _is_replaceable("title", definitions)
    assert not _is_replaceable("computed_alpha", definitions)  # owned by a rule
    assert not _is_replaceable("color", definitions)  # enum code
    assert not _is_replaceable("width", definitions)  # number
    assert not _is_replaceable("_internal", definitions)


def test_is_replaceable_includes_url_attributes() -> None:
    """Rewriting a domain/CDN prefix in URLs is a normal maintenance task.

    The core validates the result, so a broken replacement is rejected per
    object instead of being written silently.
    """
    definitions = _definitions_for(["homepage", "computed_alpha"])
    assert _is_replaceable("homepage", definitions)
    assert not _is_replaceable("computed_alpha", definitions)


async def test_handler_replaces_text_and_reports_skips() -> None:
    patched: list[tuple[str, dict[str, Any]]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/attribute-definitions":
            return httpx.Response(200, json=_DEFINITIONS)
        if request.method == "GET" and request.url.path == "/objects/object:1":
            return httpx.Response(
                200,
                json={
                    "id": "object:1",
                    "type": "bench",
                    "attributes": {
                        "name": {"de": "Acme Bank", "en": "Acme Bench"},
                        "supplier": "Acme GmbH",
                        "color": "Acme",  # enum code: not a text attribute
                        "width": 12,
                    },
                },
            )
        if request.method == "GET" and request.url.path == "/objects/object:2":
            return httpx.Response(
                200,
                json={"id": "object:2", "type": "bench", "attributes": {"name": {"en": "Other"}}},
            )
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
            "op": "replace_text",
            "object_ids": ["object:1", "object:2"],
            "parameters": {"search": "Acme", "replace": "Neu"},
        },
    )

    report = await handler(message)

    assert report["processed"] == 2
    assert report["succeeded"] == 1
    assert report["skipped"] == 1
    assert report["failed"] == 0
    assert report["replacements"] == 3
    assert [r["status"] for r in report["results"]] == ["ok", "skipped"]
    assert report["results"][1]["changed"] == 0
    assert len(patched) == 1
    path, payload = patched[0]
    assert path == "/objects/object:1"
    # `color` (enum) and `width` (number) stay untouched; the computed
    # definition is not part of this object.
    assert payload["attributes"] == {
        "name": {"de": "Neu Bank", "en": "Neu Bench"},
        "supplier": "Neu GmbH",
        "color": "Acme",
        "width": 12,
    }


async def test_handler_honours_a_single_attribute_and_case_sensitivity() -> None:
    patched: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/attribute-definitions":
            return httpx.Response(200, json=_DEFINITIONS)
        if request.method == "GET" and request.url.path == "/objects/object:1":
            return httpx.Response(
                200,
                json={
                    "id": "object:1",
                    "type": "bench",
                    "attributes": {
                        "name": {"en": "acme bench"},
                        "supplier": "ACME GmbH",
                    },
                },
            )
        if request.method == "PATCH":
            patched.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "object:1"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:2",
        type="bulk_operation",
        parameters={
            "op": "replace_text",
            "object_ids": ["object:1"],
            "parameters": {
                "search": "ACME",
                "replace": "Neu",
                "attribute": "supplier",
                "case_sensitive": True,
            },
        },
    )

    report = await handler(message)

    assert report["succeeded"] == 1
    assert patched == [{"attributes": {"name": {"en": "acme bench"}, "supplier": "Neu GmbH"}}]


async def test_handler_requires_a_search_text() -> None:
    async def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = build_bulk_handler("http://core", client=client)
    message = JobMessage(
        run_id="job_run:3",
        type="bulk_operation",
        parameters={
            "op": "replace_text",
            "object_ids": ["object:1"],
            "parameters": {"search": ""},
        },
    )

    report = await handler(message)

    assert report["failed"] == 1
    assert report["results"][0]["error"] == "`parameters.search` is required"

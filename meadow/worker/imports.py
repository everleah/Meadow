"""`inbound_import` job handler: MEF import with idempotent upsert (Phase 6).

The handler receives `parameters.document_id`, fetches the stored MEF document
from the core (`GET /imports/{document_id}`), and imports its objects and
references:

- Objects are upserted by business key (`match_by_key`, e.g. `sku`): an
  existing object with the same type + key value is updated, otherwise a new
  object is created.
- With `match_by_id` a MEF `id` that is an existing core record id
  (`object:<ulid>`, as written by Meadow's own MEF export) updates that object.
- Objects without a key match are deduplicated by **source reference** (the
  MEF object `id` + the document `source`), via `GET /objects/by-source`.
- References are created between the imported objects, mapping the MEF `id`
  (and the key/source lookups) to the core's record ids.
- When `emit_events` is `false` (bulk imports), per-object CRUD events are
  suppressed and a single `import.completed` summary event is emitted.

Per-object failures are isolated: one bad object does not abort the whole run;
a report is returned and stored in `job_run.result`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from meadow.rules import Context, execute_rule
from meadow.worker.business_rules import evaluation_lock
from meadow.worker.http import loopback_host
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)

#: Sandbox budget for normalization rules in the import pipeline (bulk-like).
NORMALIZATION_TIMEOUT_SECONDS = 30.0

#: Default source label when the MEF document carries none.
DEFAULT_SOURCE = "default"


def _key_value(attributes: dict[str, Any], key: str) -> str | None:
    """Returns the business-key value if it is a plain string, else `None`."""
    value = attributes.get(key)
    if isinstance(value, str):
        return value.strip() or None
    return None


async def _fetch_document(
    client: httpx.AsyncClient,
    core_url: str,
    document_id: str,
) -> dict[str, Any]:
    response = await client.get(f"{core_url}/imports/{document_id}")
    response.raise_for_status()
    record = response.json()
    document = record.get("document")
    if not isinstance(document, dict):
        raise RuntimeError(f"import document `{document_id}` has no `document` object")
    return document


async def _load_normalization_rules(
    client: httpx.AsyncClient,
    core_url: str,
) -> list[dict[str, Any]]:
    """Loads active `normalization` rules (R3) from the core."""
    response = await client.get(
        f"{core_url}/rules",
        params={"type": "normalization", "state": "active", "limit": 1000},
    )
    response.raise_for_status()
    return [rule for rule in response.json() if isinstance(rule, dict) and rule.get("script")]


def _rules_for_type(rules: list[dict[str, Any]], object_type: str) -> list[dict[str, Any]]:
    """Returns the rules whose `applies_to` covers `object_type` (empty = all)."""
    selected: list[dict[str, Any]] = []
    for rule in rules:
        applies_to = rule.get("applies_to") or []
        if not applies_to or object_type in applies_to:
            selected.append(rule)
    return selected


def _evaluate_normalization(
    rules: list[dict[str, Any]],
    object_type: str,
    attributes: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    """Evaluates normalization rules against one object's raw MEF values."""
    normalized: dict[str, Any] = {}
    view = {"id": None, "type": object_type, "status": None, "attributes": attributes}
    ctx = Context(
        rule_type="normalization",
        object_type=object_type,
        mode="import",
        actor="system",
    )
    for rule in rules:
        result = execute_rule(str(rule.get("script")), view, ctx, timeout_s=timeout_s)
        normalized.update(result.computed)
    return normalized


async def _normalize_object(
    rules: list[dict[str, Any]],
    object_type: str,
    attributes: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    """Runs the normalization rules (serialized) and returns the new values."""
    if not rules:
        return {}
    async with evaluation_lock:
        return await asyncio.to_thread(
            _evaluate_normalization, rules, object_type, attributes, timeout_s
        )


async def _convert_units(
    client: httpx.AsyncClient,
    core_url: str,
    attributes: dict[str, Any],
) -> None:
    """Converts quantified `{"value":…, "unit":…}` values to canonical SI."""
    for name, value in list(attributes.items()):
        if not (isinstance(value, dict) and "value" in value):
            continue
        number = value.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise RuntimeError(f"quantified value for `{name}` must be numeric")
        unit = value.get("unit")
        if unit is None:
            attributes[name] = number
            continue
        response = await client.get(
            f"{core_url}/units/convert",
            params={"value": number, "from": str(unit)},
        )
        response.raise_for_status()
        attributes[name] = response.json()["value"]


async def _upsert_object(
    client: httpx.AsyncClient,
    core_url: str,
    object_type: str,
    attributes: dict[str, Any],
    status: str,
    match_by_key: str | None,
    source_id: str | None,
    source: str | None,
    emit_events: bool,
    match_by_id: bool = False,
) -> tuple[str, str]:
    """Creates or updates one MEF object; returns `(record_id, action)`.

    The object is matched by business key first, then (with `match_by_id`) by an
    existing core record id, then by source reference (`source` + `source_id`),
    so keyless objects are idempotent too.
    """
    existing_id: str | None = None
    if match_by_key:
        key_value = _key_value(attributes, match_by_key)
        if key_value:
            response = await client.get(
                f"{core_url}/objects/by-key",
                params={
                    "type": object_type,
                    "key": match_by_key,
                    "value": key_value,
                },
            )
            if response.status_code == 200:
                existing_id = str(response.json()["id"])
            elif response.status_code != 404:
                response.raise_for_status()

    if existing_id is None and match_by_id and source_id and source_id.startswith("object:"):
        response = await client.get(f"{core_url}/objects/{source_id}")
        if response.status_code == 200:
            existing_id = str(response.json()["id"])
        elif response.status_code != 404:
            response.raise_for_status()

    if existing_id is None and source_id:
        response = await client.get(
            f"{core_url}/objects/by-source",
            params={"source": source or DEFAULT_SOURCE, "source_id": source_id},
        )
        if response.status_code == 200:
            existing_id = str(response.json()["id"])
        elif response.status_code != 404:
            response.raise_for_status()

    body: dict[str, Any] = {
        "type": object_type,
        "attributes": attributes,
        "status": status,
    }
    if source_id:
        body["source_id"] = source_id
    if source:
        body["source"] = source

    params = None if emit_events else {"emit_events": "false"}
    if existing_id:
        response = await client.patch(
            f"{core_url}/objects/{existing_id}",
            json={"attributes": attributes, "status": status},
            params=params,
        )
        response.raise_for_status()
        return str(response.json()["id"]), "updated"
    response = await client.post(f"{core_url}/objects", json=body, params=params)
    response.raise_for_status()
    return str(response.json()["id"]), "created"


async def _import_references(
    client: httpx.AsyncClient,
    core_url: str,
    references: list[dict[str, Any]],
    id_map: dict[str, str],
    emit_events: bool,
) -> list[dict[str, Any]]:
    """Creates MEF references, mapping external ids via `id_map`.

    The MEF reference `type` is the relation-definition **name** (e.g.
    `related`, `category_member`); the core's `POST /references` expects the
    definition **record key** (seed convention `rel_<name>`, e.g.
    `rel_related`). An already-prefixed id is passed through unchanged.
    """
    results: list[dict[str, Any]] = []
    params = None if emit_events else {"emit_events": "false"}
    for reference in references:
        source = id_map.get(str(reference.get("in") or ""))
        target = id_map.get(str(reference.get("out") or ""))
        definition_name = str(reference.get("type") or "")
        definition_id = (
            definition_name if definition_name.startswith("rel_") else f"rel_{definition_name}"
        )
        attributes = reference.get("attributes") or {}
        try:
            if not source or not target:
                raise RuntimeError(
                    f"cannot resolve reference `in={reference.get('in')} "
                    f"out={reference.get('out')}`"
                )
            if not definition_name:
                raise RuntimeError("reference is missing `type`")
            response = await client.post(
                f"{core_url}/references",
                json={
                    "source": source,
                    "target": target,
                    "definition_id": definition_id,
                    "attributes": attributes,
                },
                params=params,
            )
            response.raise_for_status()
            results.append(
                {"in": source, "out": target, "type": definition_name, "status": "created"}
            )
        except Exception as exc:  # noqa: BLE001 - per-reference isolation
            logger.warning("reference import failed: %s", exc)
            results.append(
                {
                    "in": source or reference.get("in"),
                    "out": target or reference.get("out"),
                    "type": definition_name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return results


async def _complete_import(
    client: httpx.AsyncClient,
    core_url: str,
    document_id: str,
    run_id: str,
    source: str,
    created: int,
    updated: int,
    failed: int,
    processed: int,
) -> None:
    """Emits the `import.completed` summary event (bulk imports)."""
    response = await client.post(
        f"{core_url}/imports/{document_id}/complete",
        json={
            "job_run_id": run_id,
            "source": source or None,
            "created": created,
            "updated": updated,
            "failed": failed,
            "processed": processed,
        },
    )
    response.raise_for_status()


def build_import_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `inbound_import` job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job.
    """
    core_url = loopback_host(core_url)
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        document_id = parameters.get("document_id")
        if not document_id:
            raise RuntimeError("inbound_import job is missing `parameters.document_id`")
        match_by_key = parameters.get("match_by_key")
        if match_by_key is not None:
            match_by_key = str(match_by_key)
        match_by_id = bool(parameters.get("match_by_id", False))
        emit_events = bool(parameters.get("emit_events", True))

        document = await _fetch_document(active, core_url, str(document_id))
        objects = document.get("objects") or []
        references = document.get("references") or []
        if not isinstance(objects, list):
            raise RuntimeError("MEF `objects` must be an array")
        if not isinstance(references, list):
            raise RuntimeError("MEF `references` must be an array")

        source = str(parameters.get("source") or document.get("source") or "")
        normalization_rules = await _load_normalization_rules(active, core_url)

        id_map: dict[str, str] = {}
        created = 0
        updated = 0
        failed = 0
        object_results: list[dict[str, Any]] = []
        for entry in objects:
            external_id = str(entry.get("id") or "")
            object_type = str(entry.get("type") or "").strip()
            try:
                if not object_type:
                    raise RuntimeError("MEF object is missing `type`")
                attributes = entry.get("attributes")
                if not isinstance(attributes, dict):
                    raise RuntimeError("MEF object `attributes` must be an object")
                attributes = dict(attributes)
                status = entry.get("status") or "draft"

                type_rules = _rules_for_type(normalization_rules, object_type)
                if type_rules:
                    attributes.update(
                        await _normalize_object(
                            type_rules,
                            object_type,
                            attributes,
                            NORMALIZATION_TIMEOUT_SECONDS,
                        )
                    )
                await _convert_units(active, core_url, attributes)

                record_id, action = await _upsert_object(
                    active,
                    core_url,
                    object_type,
                    attributes,
                    str(status),
                    match_by_key,
                    external_id or None,
                    source or None,
                    emit_events,
                    match_by_id,
                )
                if external_id:
                    id_map[external_id] = record_id
                if action == "created":
                    created += 1
                else:
                    updated += 1
                object_results.append({"id": record_id, "type": object_type, "status": action})
            except Exception as exc:  # noqa: BLE001 - per-object isolation
                logger.exception("object import failed for %r", external_id or object_type)
                failed += 1
                object_results.append(
                    {"id": external_id, "type": object_type, "status": "failed", "error": str(exc)}
                )

        reference_results = await _import_references(
            active, core_url, references, id_map, emit_events
        )

        if not emit_events:
            await _complete_import(
                active,
                core_url,
                str(document_id),
                message.run_id,
                source,
                created,
                updated,
                failed,
                len(objects),
            )

        return {
            "processed": len(objects),
            "created": created,
            "updated": updated,
            "failed": failed,
            "references": reference_results,
            "results": object_results,
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=60.0, headers=headers) as active:
            return await run(active, message)

    return handle


__all__ = ["build_import_handler"]

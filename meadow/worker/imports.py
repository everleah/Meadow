"""`inbound_import` job handler: MEF import with idempotent upsert (Phase 6).

The handler receives `parameters.document_id`, fetches the stored MEF document
from the core (`GET /imports/{document_id}`), and imports its objects and
references:

- Objects are upserted in **batches** (`POST /objects/batch-upsert`, see
  `BATCH_UPSERT_SIZE`); the core matches each entry like the single write path:
  business key first (`match_by_key`, e.g. `sku`, default: the type's declared key
  attributes), then — with `match_by_id` — a MEF `id` that is an existing core
  record id (`object:<ulid>`, as written by Meadow's own MEF export), then the
  **source reference** (the MEF object `id` + the document `source`). One request
  therefore replaces up to `BATCH_UPSERT_SIZE` single writes, which is what makes
  large imports cheap: the per-object round trips, not the database, dominated
  them (`plan/detail/demodaten-generator.md` §4.3/§5).
- References are created between the imported objects, mapping the MEF `id`
  (and the key/source lookups) to the core's record ids.
- A reference the core reports as a duplicate (`409`: the edge exists already) is
  recorded as `existing`, not as a failure, so re-importing a document is
  idempotent for edges too. The same status also covers a **concurrent write
  conflict** (`409`, "concurrent write conflict") — that one is retryable and is
  reported as `failed`, so the importer's retry pass re-applies the edge instead
  of silently dropping it.
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

#: Record key of the (single) composition relation type.
COMPOSITION_DEFINITION = "rel_depends_on"

#: Answer of the core when a `depends_on` move is asked for an object that has no
#: composition edge (`PATCH /objects/{id}/parent`, `POST /references/batch`
#: `reparents`). A move is a move, so the core rejects it; a MEF document instead
#: describes the **desired** parent, which for an edge-less object means "attach".
#: The message is matched literally (the core sends it unchanged).
MISSING_COMPOSITION_PARENT = "object has no composition parent"


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


def _raise_for_core_error(response: httpx.Response) -> None:
    """Raises for a non-2xx core response, keeping the core's error message.

    `raise_for_status()` drops the body, which is exactly the interesting part
    (`{"error": "concurrent write conflict, please retry: …"}` or
    `{"error": "duplicate business key …"}`) — the message ends up in the run
    report, where an operator reads it.
    """
    if response.is_success:
        return
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        detail = response.text[:200]
    else:
        detail = str(payload.get("error") or payload)[:200] if isinstance(payload, dict) else ""
    raise RuntimeError(f"HTTP {response.status_code} from {response.request.url.path}: {detail}")


#: Objects per `POST /objects/batch-upsert` call. The endpoint checks the
#: business keys, locks them and writes the whole batch in one transaction, so a
#: single request replaces up to this many single writes (measured 2026-09-16:
#: the per-object round trips, not the database, dominated an import —
#: `plan/detail/demodaten-generator.md` §4.3/§5).
BATCH_UPSERT_SIZE = 200

#: References per `POST /references/batch` call (the endpoint allows 500 rows in
#: total, `references` + `reparents` together). One request replaces this many
#: single `POST /references`/`PATCH …/parent` calls — measured 2026-09-16: the
#: object phase of a chunk ran at ~141 objects/s (batched), the reference phase at
#: ~11.5 edges/s (one request per edge), which made a 243 k-edge import a ~6 h
#: affair (`plan/detail/demodaten-generator.md` §4.3).
BATCH_REFERENCE_SIZE = 200


async def _upsert_batch(
    client: httpx.AsyncClient,
    core_url: str,
    entries: list[dict[str, Any]],
    match_by: str,
    emit_events: bool,
    match_key: str | None = None,
) -> list[tuple[str | None, str, str | None]]:
    """Upserts prepared objects in one call; returns `(record_id, action, error)` each.

    `entries` are `{"external_id": str, "payload": {...}}` records. The endpoint
    answers per entry with the same index, so a reordered answer cannot mix up
    objects. A whole-batch failure raises: the caller marks every entry failed.
    """
    params: dict[str, str] = {"match_by": match_by}
    if not emit_events:
        params["emit_events"] = "false"
    body: dict[str, Any] = {
        "match_by": match_by,
        "objects": [item["payload"] for item in entries],
    }
    if match_key:
        # `match_by_key` of the import job: the attribute the core matches on.
        body["key"] = match_key
    response = await client.post(
        f"{core_url}/objects/batch-upsert",
        params=params,
        json=body,
    )
    _raise_for_core_error(response)
    payload = response.json()
    outcomes: list[tuple[str | None, str, str | None]] = [
        (None, "failed", "the core returned no result for this entry") for _ in entries
    ]
    for row in payload.get("results", []):
        try:
            index = int(row.get("index", -1))
        except TypeError, ValueError:
            continue
        if 0 <= index < len(outcomes):
            outcomes[index] = (
                row.get("id"),
                str(row.get("action") or "failed"),
                row.get("error"),
            )
    return outcomes


async def _resolve_missing_endpoint(
    client: httpx.AsyncClient,
    core_url: str,
    source_label: str | None,
    external_id: str,
    id_map: dict[str, str],
) -> str | None:
    """Resolves a MEF id that this document's map does not know, via its source reference.

    A shared object of a chunked import lives in several documents; if its own
    entry failed (a concurrent write, for example) or another chunk is importing
    it right now, the object still exists in the core. Without this lookup every
    reference pointing at it fails with "cannot resolve", and the retry pass
    could only fix the object, not those edges (found 2026-09-16: one conflicted
    category object cost 17 of its `category_member` edges). The result is cached
    in `id_map`, so the lookup happens once per external id.
    """
    cached = id_map.get(external_id)
    if cached:
        return cached
    if not source_label or not external_id:
        return None
    response = await client.get(
        f"{core_url}/objects/by-source",
        params={"source": source_label, "source_id": external_id},
    )
    if response.status_code != 200:
        return None
    record_id = str(response.json().get("id") or "")
    if not record_id:
        return None
    id_map[external_id] = record_id
    return record_id


def _record_batch_results(
    body: dict[str, Any],
    results: list[dict[str, Any] | None],
    *,
    edges: list[dict[str, Any]],
    edge_positions: list[int],
    moves: list[dict[str, Any]],
    move_positions: list[int],
) -> None:
    """Maps the per-entry `results` of one batch response onto the report positions.

    `references` and `reparents` are indexed separately (the endpoint validates
    them in two passes), so each kind is mapped against its own list.
    """
    for entry in body.get("results") or []:
        kind = str(entry.get("kind") or "")
        try:
            index = int(entry.get("index"))
        except TypeError, ValueError:
            continue
        if kind == "reference":
            items, positions = edges, edge_positions
        elif kind == "reparent":
            items, positions = moves, move_positions
        else:
            continue
        if index < 0 or index >= len(items):
            continue
        item = items[index]
        status = str(entry.get("action") or "failed")
        mapped: dict[str, Any] = {
            "in": item["in"],
            "out": item["out"],
            "type": item["type"],
            "status": status,
        }
        if status == "failed":
            mapped["error"] = entry.get("error")
            logger.warning("reference import failed: %s", entry.get("error"))
        results[positions[index]] = mapped


async def _import_references(
    client: httpx.AsyncClient,
    core_url: str,
    references: list[dict[str, Any]],
    id_map: dict[str, str],
    emit_events: bool,
    source_label: str | None = None,
) -> list[dict[str, Any]]:
    """Creates MEF references, mapping external ids via `id_map`.

    The MEF reference `type` is the relation-definition **name** (e.g.
    `related`, `category_member`); the core expects the definition **record key**
    (seed convention `rel_<name>`, e.g. `rel_related`). An already-prefixed id is
    passed through unchanged.

    `depends_on` is special: a new object is attached to the universal root when
    it is created, and the composition invariant allows exactly one parent edge
    (`plan/detail/relationen.md`), so the reference is applied as a
    **re-parenting** instead of a second edge. Without that, a MEF document could
    not express the composition tree at all. An object **without** any composition
    edge cannot be moved — the core answers `MISSING_COMPOSITION_PARENT` — so that
    entry is re-applied as the initial attach.

    The endpoints are resolved per reference (cached in `id_map`), then the edges
    are written through `POST /references/batch` in batches of
    `BATCH_REFERENCE_SIZE` — the batch endpoint reports existing edges as
    `existing` and failures per entry, so one bad edge never fails the rest.
    """
    results: list[dict[str, Any] | None] = [None] * len(references)
    params = None if emit_events else {"emit_events": "false"}
    pending: list[tuple[int, dict[str, Any]]] = []
    for position, reference in enumerate(references):
        in_id = str(reference.get("in") or "")
        out_id = str(reference.get("out") or "")
        source = id_map.get(in_id) or await _resolve_missing_endpoint(
            client, core_url, source_label, in_id, id_map
        )
        target = id_map.get(out_id) or await _resolve_missing_endpoint(
            client, core_url, source_label, out_id, id_map
        )
        definition_name = str(reference.get("type") or "")
        definition_id = (
            definition_name if definition_name.startswith("rel_") else f"rel_{definition_name}"
        )
        reason = None
        if not source or not target:
            reason = (
                f"cannot resolve reference `in={reference.get('in')} out={reference.get('out')}`"
            )
        elif not definition_name:
            reason = "reference is missing `type`"
        if reason is not None:
            logger.warning("reference import failed: %s", reason)
            results[position] = {
                "in": source or reference.get("in"),
                "out": target or reference.get("out"),
                "type": definition_name,
                "status": "failed",
                "error": reason,
            }
            continue
        pending.append(
            (
                position,
                {
                    "in": source,
                    "out": target,
                    "type": definition_name,
                    "definition_id": definition_id,
                    "attributes": reference.get("attributes") or {},
                    "composition": definition_id == COMPOSITION_DEFINITION,
                },
            )
        )

    for start in range(0, len(pending), BATCH_REFERENCE_SIZE):
        batch = pending[start : start + BATCH_REFERENCE_SIZE]
        edges = [item for _, item in batch if not item["composition"]]
        moves = [item for _, item in batch if item["composition"]]
        edge_positions = [position for position, item in batch if not item["composition"]]
        move_positions = [position for position, item in batch if item["composition"]]
        payload = {
            "references": [
                {
                    "source": item["in"],
                    "target": item["out"],
                    "definition_id": item["definition_id"],
                    "attributes": item["attributes"],
                }
                for item in edges
            ],
            "reparents": [{"source": item["in"], "parent": item["out"]} for item in moves],
        }
        try:
            response = await client.post(
                f"{core_url}/references/batch", json=payload, params=params
            )
            _raise_for_core_error(response)
            body = response.json() or {}
        except Exception as exc:  # noqa: BLE001 - per-batch isolation
            # The whole batch was rejected (a `413` or a server/database error):
            # report every entry so the retry pass re-applies exactly these edges.
            logger.warning("reference batch failed: %s", exc)
            for position, item in batch:
                results[position] = {
                    "in": item["in"],
                    "out": item["out"],
                    "type": item["type"],
                    "status": "failed",
                    "error": str(exc),
                }
            continue
        _record_batch_results(
            body,
            results,
            edges=edges,
            edge_positions=edge_positions,
            moves=moves,
            move_positions=move_positions,
        )
        reattach: list[tuple[int, dict[str, Any]]] = []
        for entry in body.get("results") or []:
            if str(entry.get("kind") or "") != "reparent" or entry.get("action") != "failed":
                continue
            if entry.get("error") != MISSING_COMPOSITION_PARENT:
                continue
            index = entry.get("index")
            if not isinstance(index, int) or not 0 <= index < len(moves):
                continue
            reattach.append((move_positions[index], moves[index]))
        if not reattach:
            continue
        # The object has no composition edge at all, so the move has nothing to
        # move. A MEF document states the desired parent, and for an edge-less
        # object that is the **initial attach** — which the `references` side of
        # the endpoint performs (`has_parent == false` is exactly this case).
        # Without this the import could never repair such an object: every run
        # reported the same failure (found 2026-09-17: 65 demo variants stayed
        # parent-less and invisible in the structure tree).
        logger.info("re-applying %d composition edge(s) as an initial attach", len(reattach))
        attach_payload = {
            "references": [
                {
                    "source": item["in"],
                    "target": item["out"],
                    "definition_id": item["definition_id"],
                    "attributes": item["attributes"],
                }
                for _, item in reattach
            ],
            "reparents": [],
        }
        try:
            response = await client.post(
                f"{core_url}/references/batch", json=attach_payload, params=params
            )
            _raise_for_core_error(response)
            attach_body = response.json() or {}
        except Exception as exc:  # noqa: BLE001 - keep the reported failure
            logger.warning("composition attach failed: %s", exc)
            continue
        _record_batch_results(
            attach_body,
            results,
            edges=[item for _, item in reattach],
            edge_positions=[position for position, _ in reattach],
            moves=[],
            move_positions=[],
        )
    # The report keeps the document's order; an entry the core did not report is a
    # failure (the retry pass must see it, otherwise the edge would be lost).
    return [
        result
        if result is not None
        else {
            "in": None,
            "out": None,
            "type": None,
            "status": "failed",
            "error": "the core did not report this reference",
        }
        for result in results
    ]


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
        # The write is batched (`POST /objects/batch-upsert`): only the preparation
        # (normalization rules, unit conversion) stays per object. The endpoint
        # matches like the single write path — business key, then (with
        # `match_by_id`) the record id, then the source reference.
        match_by = "id" if match_by_id else "source"
        batch: list[dict[str, Any]] = []

        async def flush_batch() -> None:
            nonlocal created, updated, failed
            if not batch:
                return
            pending = list(batch)
            batch.clear()
            try:
                outcomes = await _upsert_batch(
                    active, core_url, pending, match_by, emit_events, match_by_key
                )
            except Exception as exc:  # noqa: BLE001 - one batch must not kill the import
                logger.exception("batch upsert failed for %d objects", len(pending))
                message = str(exc)
                for item in pending:
                    object_type = str(item["payload"].get("type") or "")
                    failed += 1
                    object_results.append(
                        {
                            "id": str(item["external_id"] or object_type),
                            "type": object_type,
                            "status": "failed",
                            "error": message,
                        }
                    )
                return
            for item, (record_id, action, error) in zip(pending, outcomes, strict=True):
                external_id = str(item["external_id"] or "")
                object_type = str(item["payload"].get("type") or "")
                if action == "failed" or record_id is None:
                    failed += 1
                    object_results.append(
                        {
                            "id": external_id or object_type,
                            "type": object_type,
                            "status": "failed",
                            "error": error,
                        }
                    )
                    continue
                if external_id:
                    id_map[external_id] = record_id
                if action == "created":
                    created += 1
                else:
                    updated += 1
                object_results.append({"id": record_id, "type": object_type, "status": action})

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

                payload: dict[str, Any] = {
                    "type": object_type,
                    "attributes": attributes,
                    "status": str(status),
                }
                if external_id:
                    payload["source_id"] = external_id
                if source:
                    payload["source"] = source
                batch.append({"external_id": external_id, "payload": payload})
                if len(batch) >= BATCH_UPSERT_SIZE:
                    await flush_batch()
            except Exception as exc:  # noqa: BLE001 - per-object isolation
                logger.exception("object import failed for %r", external_id or object_type)
                failed += 1
                object_results.append(
                    {"id": external_id, "type": object_type, "status": "failed", "error": str(exc)}
                )
        await flush_batch()

        reference_results = await _import_references(
            active, core_url, references, id_map, emit_events, source or None
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

"""`bulk_operation` job handler: applies a mass operation to a selection.

Phase 7 (bulk-edit wizard). The core queues a `bulk_operation` job with
`parameters = {"op": ..., "object_ids": [...], "parameters": {...}}`; the
frontend resolves the selection (all filter hits / page / checked rows) into an
explicit `object_ids` list. This handler loads each object, applies the
operation, and reports a per-object status in `job_run.result`. Object failures
are isolated (one failing object does not abort the run).

`replace_text` implements Find & Replace: it replaces a literal search text in
the object's text attributes (a single `attribute` or every non-computed `text`
attribute plus the convention fields) and reports `changed` (fields) and
`replacements` (occurrences) per object. Objects without a match get the status
`skipped`, and the run result carries a `skipped` counter.

`send_for_approval` pushes the selection through the approval workflow by
transitioning every object into the review status (`parameters.approval_status`,
default `in_review`). The same transition can be appended to any data-changing
operation: with `parameters.send_for_approval = true` the worker sends each
object for approval **after** its change. A change is never rolled back when the
transition is rejected (a `workflow_guard` rule); the object then carries
`approval_error` instead of `approval`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from meadow.worker.http import loopback_host
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)

_ATTR_OPS = {"set_attribute", "clear_attribute"}

#: Fields that live in `attributes` but are never text-replaceable.
_NON_TEXT_FIELDS = frozenset({"id", "type", "status", "parent", "key"})

#: Review state of the workflow state machine (Abschnitt 8 / workflow-freigaben.md).
DEFAULT_APPROVAL_STATUS = "in_review"

#: Operations that must not get an extra approval transition afterwards.
_NO_APPROVAL_OPS = frozenset({"change_status", "send_for_approval", "delete"})


def build_bulk_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `bulk_operation` job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job.
    """
    core_url = loopback_host(core_url)
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        op = str(parameters.get("op") or "")
        object_ids = parameters.get("object_ids") or []
        op_params = parameters.get("parameters") or {}
        if not isinstance(op_params, dict):
            raise RuntimeError("`parameters.parameters` must be an object")
        if not object_ids:
            raise RuntimeError("bulk job is missing `parameters.object_ids`")

        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0
        skipped = 0
        replacements = 0
        approvals = 0
        # Opt-in approval transition appended to a data-changing operation.
        wants_approval = bool(op_params.get("send_for_approval")) and op not in _NO_APPROVAL_OPS
        approval_status = str(op_params.get("approval_status") or DEFAULT_APPROVAL_STATUS)
        # Shared per-run cache (attribute definitions for `replace_text`).
        cache: dict[str, Any] = {}
        for oid in object_ids:
            record: dict[str, Any] = {"object_id": str(oid), "status": "skipped"}
            try:
                detail = await _apply(active, core_url, op, str(oid), op_params, cache)
                if wants_approval and detail.get("changed", 1) != 0:
                    # `changed: 0` means the operation wrote nothing (no match).
                    approval = await request_approval(active, core_url, str(oid), approval_status)
                    detail = {**detail, **approval}
                if op == "replace_text" and not detail.get("changed"):
                    # No match in this object: report it as skipped, not as a change.
                    record.update(detail)
                    skipped += 1
                else:
                    record["status"] = "ok"
                    if detail:
                        record.update(detail)
                    succeeded += 1
                    replacements += int(detail.get("replacements") or 0)
                    approvals += 1 if detail.get("approval") else 0
            except Exception as exc:  # noqa: BLE001 - isolate per-object failures
                record["status"] = "error"
                record["error"] = str(exc)
                failed += 1
            results.append(record)

        logger.info(
            "bulk_operation %s: %d ok, %d skipped, %d failed, %d approvals of %d objects",
            op,
            succeeded,
            skipped,
            failed,
            approvals,
            len(object_ids),
        )
        return {
            "op": op,
            "processed": len(object_ids),
            "succeeded": succeeded,
            "skipped": skipped,
            "failed": failed,
            "replacements": replacements,
            "approvals": approvals,
            "results": results,
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=60.0, headers=headers) as active:
            return await run(active, message)

    return handle


async def _apply(
    active: httpx.AsyncClient,
    core_url: str,
    op: str,
    object_id: str,
    params: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Applies one bulk operation to a single object and returns extra detail.

    An operation that may write nothing reports `changed: 0` (only
    `replace_text` currently does); the caller skips the approval transition in
    that case.
    """
    if op in _ATTR_OPS:
        attribute = str(params.get("attribute") or "")
        if not attribute:
            raise RuntimeError("`parameters.attribute` is required")
        obj = (await active.get(f"{core_url}/objects/{object_id}")).json()
        attributes = dict(obj.get("attributes") or {})
        if op == "set_attribute":
            value = params.get("value")
            if value in (None, ""):
                attributes.pop(attribute, None)
            else:
                attributes[attribute] = value
        else:  # clear_attribute
            attributes.pop(attribute, None)
        response = await active.patch(
            f"{core_url}/objects/{object_id}", json={"attributes": attributes}
        )
        response.raise_for_status()
        return {"attribute": attribute}

    if op == "replace_text":
        return await _replace_text(active, core_url, object_id, params, cache or {})

    if op == "change_status":
        status = str(params.get("status") or "")
        if not status:
            raise RuntimeError("`parameters.status` is required")
        await set_status(active, core_url, object_id, status)
        # `new_status` (not `status`) keeps the per-object record status intact.
        return {"new_status": status}

    if op == "send_for_approval":
        status = str(params.get("approval_status") or DEFAULT_APPROVAL_STATUS)
        await set_status(active, core_url, object_id, status)
        return {"approval": status}

    if op in ("add_category", "remove_category"):
        category = str(params.get("category") or "")
        if not category:
            raise RuntimeError("`parameters.category` is required")
        if op == "add_category":
            response = await active.post(
                f"{core_url}/references",
                json={
                    "source": object_id,
                    "target": category,
                    "definition_id": "rel_category_member",
                },
            )
        else:
            ref_id = await _find_category_member(active, core_url, object_id, category)
            if ref_id is None:
                raise RuntimeError("category membership not found")
            response = await active.delete(f"{core_url}/references/{ref_id}")
        response.raise_for_status()
        return {"category": category}

    if op == "change_parent":
        parent = str(params.get("parent") or "")
        if not parent:
            raise RuntimeError("`parameters.parent` is required")
        response = await active.patch(
            f"{core_url}/objects/{object_id}/parent", json={"parent": parent}
        )
        response.raise_for_status()
        return {"parent": parent}

    if op == "delete":
        response = await active.delete(f"{core_url}/objects/{object_id}")
        response.raise_for_status()
        return {}

    raise RuntimeError(f"unknown bulk operation `{op}`")


async def set_status(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    status: str,
) -> None:
    """Transitions one object into `status` (workflow_guard rules apply)."""
    response = await active.patch(f"{core_url}/objects/{object_id}", json={"status": status})
    response.raise_for_status()


async def request_approval(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    status: str = DEFAULT_APPROVAL_STATUS,
) -> dict[str, Any]:
    """Sends one object for approval and reports the outcome.

    Used as an **appended** step after a data change, so a rejection by a
    `workflow_guard` rule must not fail the object: the change is already saved
    and the outcome is reported as `approval_error` instead.
    """
    try:
        await set_status(active, core_url, object_id, status)
    except Exception as exc:  # noqa: BLE001 - the data change stays applied
        return {"approval_error": str(exc)}
    return {"approval": status}


async def _find_category_member(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    category: str,
) -> str | None:
    """Returns the `category_member` reference id linking object <-> category."""
    refs = (await active.get(f"{core_url}/objects/{object_id}/references")).json()
    for ref in refs:
        if ref.get("definition_id") != "rel_category_member":
            continue
        if ref.get("out") == category or ref.get("in") == category:
            return ref.get("id")
    return None


async def _replace_text(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    params: dict[str, Any],
    cache: dict[str, Any],
) -> dict[str, Any]:
    """Replaces a literal text in the object's text attributes (Find & Replace).

    `parameters`: `search` (required), `replace` (default: empty = delete),
    `attribute` (optional single attribute name; empty = every text attribute),
    `case_sensitive` (default: false). Only attributes that are `text` typed
    (or have no definition, e.g. the `name` convention field) are touched;
    computed values stay with their rule.
    """
    search = str(params.get("search") or "")
    if not search:
        raise RuntimeError("`parameters.search` is required")
    replacement = params.get("replace")
    replacement = "" if replacement is None else str(replacement)
    case_sensitive = bool(params.get("case_sensitive"))
    attribute = str(params.get("attribute") or "")

    obj = (await active.get(f"{core_url}/objects/{object_id}")).json()
    attributes = dict(obj.get("attributes") or {})
    definitions = await _attribute_definitions(active, core_url, cache)

    changed_fields = 0
    replacements = 0
    for name, value in list(attributes.items()):
        if attribute:
            if name != attribute:
                continue
        elif not _is_replaceable(name, definitions):
            continue
        new_value, count = replace_in_value(value, search, replacement, case_sensitive)
        if count:
            attributes[name] = new_value
            changed_fields += 1
            replacements += count

    if not changed_fields:
        return {"changed": 0, "replacements": 0}
    response = await active.patch(
        f"{core_url}/objects/{object_id}", json={"attributes": attributes}
    )
    response.raise_for_status()
    return {"changed": changed_fields, "replacements": replacements}


async def _attribute_definitions(
    active: httpx.AsyncClient,
    core_url: str,
    cache: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Returns `{attribute name: definition}`, fetched once per job run."""
    cached = cache.get("definitions")
    if cached is not None:
        return cached
    response = await active.get(f"{core_url}/attribute-definitions", params={"limit": "1000"})
    response.raise_for_status()
    definitions = {str(d.get("name")): d for d in response.json() if d.get("name")}
    cache["definitions"] = definitions
    return definitions


def _is_replaceable(name: str, definitions: dict[str, dict[str, Any]]) -> bool:
    """True when the attribute may be rewritten by Find & Replace."""
    if name.startswith("_") or name in _NON_TEXT_FIELDS:
        return False
    definition = definitions.get(name)
    if definition is None:
        return True  # convention field such as `name`
    return definition.get("type") == "text" and not definition.get("computed")


def replace_in_value(
    value: Any,
    search: str,
    replacement: str,
    case_sensitive: bool = False,
) -> tuple[Any, int]:
    """Replaces `search` in every string leaf of `value`.

    Returns the (possibly new) value and the number of replacements. Plain
    strings and multilingual maps (`{lang: text}`) are covered alike; other
    types are returned unchanged.
    """
    if isinstance(value, str):
        return _replace_in_string(value, search, replacement, case_sensitive)
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        total = 0
        for key, item in value.items():
            new_item, count = replace_in_value(item, search, replacement, case_sensitive)
            result[key] = new_item
            total += count
        return result, total
    if isinstance(value, list):
        items: list[Any] = []
        total = 0
        for item in value:
            new_item, count = replace_in_value(item, search, replacement, case_sensitive)
            items.append(new_item)
            total += count
        return items, total
    return value, 0


def _replace_in_string(
    value: str,
    search: str,
    replacement: str,
    case_sensitive: bool,
) -> tuple[str, int]:
    """Replaces `search` in one string, returning the result and its count."""
    if not search:
        return value, 0
    if case_sensitive:
        count = value.count(search)
        return (value.replace(search, replacement), count) if count else (value, 0)
    pattern = re.compile(re.escape(search), re.IGNORECASE)
    count = len(pattern.findall(value))
    if not count:
        return value, 0
    # A lambda keeps backslashes and group references in the replacement literal.
    return pattern.sub(lambda _match: replacement, value), count

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

`consolidate_inheritance` repairs redundancy: values that **all** direct children
of a node maintain **identically** are pushed up to that node and removed from
the children, so they inherit again instead of keeping data copies
(`plan/detail/vererbung-konsolidierung.md`). The subtree is walked level by level
(top-down), so a value promoted one level up can be promoted again by the next
level. `parameters.dry_run = true` computes the full proposal without writing
anything; `parameters.attributes` restricts the run to a set of attribute names
and `parameters.min_children` (default 2) requires a minimum number of children
before a value is moved.

`add_relation` / `remove_relation` assign a **relation** of the selection to one
target object (`parameters.definition_id` + `parameters.target`): the edge is
created from every selected object to the target (the stored direction is
`in` = source, `out` = target). Both are **idempotent** – an existing edge (or a
collection membership created by the `/collections/{id}/members` endpoint, which
also maintains `rank`/`added_at`) is reported as `skipped` instead of duplicated.

`release_for_channel` / `withdraw_from_channel` set or remove the
**channel-specific release** of the selection (`parameters.channel`, optional
`parameters.release_status`, default `published`). Both are **idempotent**: a
release that already is in the requested state (or an absent release when
withdrawing) is reported as `skipped`. The channel state is written by the core
(`PUT`/`DELETE /objects/{id}/releases`), so `workflow_guard` rules apply there.
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
_NO_APPROVAL_OPS = frozenset(
    {"change_status", "send_for_approval", "delete", "release_for_channel", "withdraw_from_channel"}
)

#: Operations that report "nothing to do" for an object as `skipped` (they
#: return `changed: 0` in that case).
_SKIPPABLE_OPS = frozenset(
    {
        "replace_text",
        "add_relation",
        "remove_relation",
        "release_for_channel",
        "withdraw_from_channel",
    }
)

#: Default state of a channel release (`published` = released for that channel).
DEFAULT_RELEASE_STATUS = "published"

#: Roles a relation type can have (see `plan/detail/relationen.md`).
ROLE_COMPOSITION = "composition"
ROLE_CATEGORY = "category"
ROLE_COLLECTION = "collection"
ROLE_LINK = "link"

#: Relation type of the built-in primary categorisation, used when a bulk
#: category operation does not name a specific type.
PRIMARY_CATEGORY_DEF = "rel_category_member"

#: Roles of the built-in relation types, used as fallback when the core cannot
#: be asked (e.g. the definition list is unavailable).
_BUILTIN_ROLES = {
    "rel_depends_on": ROLE_COMPOSITION,
    "rel_category_member": ROLE_CATEGORY,
    "rel_collection_member": ROLE_COLLECTION,
    "rel_related": ROLE_LINK,
}

#: Cache key for the relation-type roles fetched once per job run.
_ROLE_CACHE_KEY = "relation_roles"

#: Default minimum number of children before a value is pushed up (see the
#: consolidation plan: with a single child there is no maintenance gain).
CONSOLIDATION_MIN_CHILDREN = 2

#: Children fetched per level; the core caps this at 1000.
CONSOLIDATION_CHILD_LIMIT = 1000

#: Skip reasons reported by the consolidation preview (verbindlich).
CONSOLIDATION_REASONS = (
    "not_inheritable",
    "no_children",
    "missing_on_child",
    "interrupted",
    "conflict_child_values",
    "conflict_parent",
)

#: Sentinel for "the child does not maintain this attribute at all".
_MISSING = object()


def is_interrupting_value(value: Any) -> bool:
    """True when a value deliberately interrupts inheritance ("leer gesetzt")."""
    return value is None or value == "" or value == {}


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

        if op == "consolidate_inheritance":
            # Walks a whole subtree instead of applying one change per object.
            return await _consolidate_inheritance(
                active, core_url, [str(oid) for oid in object_ids], op_params
            )

        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0
        skipped = 0
        replacements = 0
        approvals = 0
        relations = 0
        releases = 0
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
                if not detail.get("changed") and op in _SKIPPABLE_OPS:
                    # Nothing changed for this object: report it as skipped.
                    record.update(detail)
                    skipped += 1
                else:
                    record["status"] = "ok"
                    if detail:
                        record.update(detail)
                    succeeded += 1
                    replacements += int(detail.get("replacements") or 0)
                    relations += int(detail.get("added") or 0) + int(detail.get("removed") or 0)
                    releases += int(detail.get("released") or 0) + int(detail.get("withdrawn") or 0)
                    approvals += 1 if detail.get("approval") else 0
            except Exception as exc:  # noqa: BLE001 - isolate per-object failures
                record["status"] = "error"
                record["error"] = str(exc)
                failed += 1
            results.append(record)

        logger.info(
            "bulk_operation %s: %d ok, %d skipped, %d failed, %d relations, "
            "%d approvals, %d releases of %d objects",
            op,
            succeeded,
            skipped,
            failed,
            relations,
            approvals,
            releases,
            len(object_ids),
        )
        return {
            "op": op,
            "processed": len(object_ids),
            "succeeded": succeeded,
            "skipped": skipped,
            "failed": failed,
            "replacements": replacements,
            "relations": relations,
            "releases": releases,
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

    if op in ("add_relation", "remove_relation"):
        definition_id = str(params.get("definition_id") or "")
        target = str(params.get("target") or "")
        if not definition_id or not target:
            raise RuntimeError("`parameters.definition_id` and `parameters.target` are required")
        role = await _relation_role(
            active, core_url, definition_id, cache if cache is not None else {}
        )
        if op == "add_relation":
            return await _add_relation(active, core_url, object_id, definition_id, role, target)
        return await _remove_relation(active, core_url, object_id, definition_id, target)

    if op == "change_status":
        status = str(params.get("status") or "")
        if not status:
            raise RuntimeError("`parameters.status` is required")
        await set_status(active, core_url, object_id, status)
        # `new_status` (not `status`) keeps the per-object record status intact.
        return {"new_status": status}

    if op in ("release_for_channel", "withdraw_from_channel"):
        channel = str(params.get("channel") or "")
        if not channel:
            raise RuntimeError("`parameters.channel` is required")
        return await _change_release(active, core_url, op, object_id, channel, params)

    if op == "send_for_approval":
        status = str(params.get("approval_status") or DEFAULT_APPROVAL_STATUS)
        await set_status(active, core_url, object_id, status)
        return {"approval": status}

    if op in ("add_category", "remove_category"):
        category = str(params.get("category") or "")
        if not category:
            raise RuntimeError("`parameters.category` is required")
        # Custom category relation types may be addressed explicitly; without
        # `definition_id` the built-in primary type is used.
        definition_id = str(params.get("definition_id") or "") or PRIMARY_CATEGORY_DEF
        if op == "add_category":
            response = await active.post(
                f"{core_url}/references",
                json={
                    "source": object_id,
                    "target": category,
                    "definition_id": definition_id,
                },
            )
        else:
            ref_id = await _find_category_member(
                active, core_url, object_id, category, definition_id
            )
            if ref_id is None:
                raise RuntimeError("category membership not found")
            response = await active.delete(f"{core_url}/references/{ref_id}")
        response.raise_for_status()
        return {"category": category, "relation": definition_id}

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


async def _current_release(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    channel: str,
) -> str | None:
    """Returns the stored release state of one object/channel pair (None = none)."""
    response = await active.get(f"{core_url}/objects/{object_id}/releases")
    response.raise_for_status()
    for row in response.json():
        if str(row.get("channel")) == channel:
            return str(row.get("status") or "")
    return None


async def _change_release(
    active: httpx.AsyncClient,
    core_url: str,
    op: str,
    object_id: str,
    channel: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Releases an object for a channel or withdraws that release (idempotent).

    Both operations report `changed: 0` when the release already is in the
    requested state, so the run summary counts them as `skipped` instead of
    duplicated writes.
    """
    current = await _current_release(active, core_url, object_id, channel)
    if op == "release_for_channel":
        status = str(params.get("release_status") or DEFAULT_RELEASE_STATUS)
        if current == status:
            return {"channel": channel, "changed": 0}
        response = await active.put(
            f"{core_url}/objects/{object_id}/releases",
            json={"channel": channel, "status": status},
        )
        response.raise_for_status()
        return {"channel": channel, "release_status": status, "released": 1, "changed": 1}
    # withdraw_from_channel
    if current is None:
        return {"channel": channel, "changed": 0}
    response = await active.delete(f"{core_url}/objects/{object_id}/releases/{channel}")
    response.raise_for_status()
    return {"channel": channel, "withdrawn": 1, "changed": 1}


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


async def _relation_role(
    active: httpx.AsyncClient,
    core_url: str,
    definition_id: str,
    cache: dict[str, Any],
) -> str:
    """Returns the role of a relation type (`link` when unknown).

    The definition list is fetched at most once per job run; if it cannot be
    read, the roles of the built-in types are used.
    """
    roles = cache.get(_ROLE_CACHE_KEY)
    if roles is None:
        roles = dict(_BUILTIN_ROLES)
        try:
            response = await active.get(f"{core_url}/relation-definitions")
            response.raise_for_status()
            for definition in response.json() or []:
                key = str(definition.get("id") or "")
                key = key.split(":", 1)[1] if ":" in key else key
                if key:
                    roles[key] = str(definition.get("role") or ROLE_LINK)
        except Exception as exc:  # noqa: BLE001 - roles only refine the routing
            logger.warning("relation roles unavailable, using built-ins: %s", exc)
        cache[_ROLE_CACHE_KEY] = roles
    return roles.get(definition_id, ROLE_LINK)


async def _find_category_member(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    category: str,
    definition_id: str = PRIMARY_CATEGORY_DEF,
) -> str | None:
    """Returns the reference id linking object <-> category of a category type."""
    for reference_id in await _reference_ids(active, core_url, object_id, definition_id, category):
        return reference_id
    return None


async def _add_relation(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    definition_id: str,
    role: str,
    target: str,
) -> dict[str, Any]:
    """Links `object_id` to `target` with `definition_id` (idempotent).

    Reports `changed: 0` when the link already exists, so the caller counts the
    object as skipped instead of writing a duplicate edge.
    """
    if role == ROLE_COLLECTION:
        # Collection membership goes through the collections endpoint: it creates
        # the edge in the stored direction (member -> collection) and maintains
        # `rank`/`added_at`.
        response = await active.post(
            f"{core_url}/collections/{target}/members", json={"object_ids": [object_id]}
        )
        response.raise_for_status()
        added = int(response.json().get("added") or 0)
        return {
            "relation": definition_id,
            "target": target,
            "added": added,
            "changed": added,
        }
    if await _reference_ids(active, core_url, object_id, definition_id, target):
        return {"relation": definition_id, "target": target, "added": 0, "changed": 0}
    response = await active.post(
        f"{core_url}/references",
        json={"source": object_id, "target": target, "definition_id": definition_id},
    )
    if response.status_code == 409:
        # The core rejects a duplicate edge: another writer created it in between.
        return {"relation": definition_id, "target": target, "added": 0, "changed": 0}
    response.raise_for_status()
    return {"relation": definition_id, "target": target, "added": 1, "changed": 1}


async def _remove_relation(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    definition_id: str,
    target: str,
) -> dict[str, Any]:
    """Removes the relation between `object_id` and `target` (idempotent)."""
    reference_ids = await _reference_ids(active, core_url, object_id, definition_id, target)
    for reference_id in reference_ids:
        response = await active.delete(f"{core_url}/references/{reference_id}")
        response.raise_for_status()
    return {
        "relation": definition_id,
        "target": target,
        "removed": len(reference_ids),
        "changed": len(reference_ids),
    }


async def _reference_ids(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    definition_id: str,
    other_id: str,
) -> list[str]:
    """Ids of the edges between two objects of one relation type.

    Both directions are matched: the stored direction is `in` = source and
    `out` = target, but a symmetric relation may exist the other way round.
    """
    response = await active.get(f"{core_url}/objects/{object_id}/references")
    response.raise_for_status()
    references = response.json()
    if not isinstance(references, list):
        # An error payload would otherwise fail with a cryptic attribute error.
        raise RuntimeError(f"unexpected references response for `{object_id}`")
    found: list[str] = []
    for reference in references:
        if str(reference.get("definition_id") or "") != definition_id:
            continue
        other = str(
            reference.get("out") if reference.get("in") == object_id else reference.get("in")
        )
        if other == other_id:
            found.append(str(reference.get("id")))
    return found


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


#: Attribute types Find & Replace may rewrite (all string-valued types).
_REPLACEABLE_TYPES = ("text", "url")


def _is_replaceable(name: str, definitions: dict[str, dict[str, Any]]) -> bool:
    """True when the attribute may be rewritten by Find & Replace.

    `url` counts as text: rewriting a domain or CDN prefix in many objects is a
    normal maintenance task. The core validates the result, so a replacement
    that breaks the address is rejected per object (`validation` error in the
    run report) instead of being written silently.
    """
    if name.startswith("_") or name in _NON_TEXT_FIELDS:
        return False
    definition = definitions.get(name)
    if definition is None:
        return True  # convention field such as `name`
    return definition.get("type") in _REPLACEABLE_TYPES and not definition.get("computed")


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


# --- Consolidating inheritance (bulk operation `consolidate_inheritance`) -------
#
# Plan: `plan/detail/vererbung-konsolidierung.md`. A value that **all** direct
# children maintain identically is written to the parent and removed from the
# children, so they inherit again instead of keeping data copies. The subtree is
# walked level by level (top-down), which is why a value pushed up one level can
# be pushed up again by the next level; a second run reports nothing to do.


async def _consolidate_inheritance(
    active: httpx.AsyncClient,
    core_url: str,
    roots: list[str],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Consolidates the subtrees of `roots` and reports the proposal/outcome."""
    dry_run = bool(params.get("dry_run"))
    min_children = _min_children(params.get("min_children"))
    attributes = {str(name) for name in params.get("attributes") or []} or None
    definitions = await _attribute_definitions(active, core_url, {})

    moves: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    nodes = 0
    failed = 0
    for root in roots:
        try:
            visited = await _consolidate_subtree(
                active, core_url, root, definitions, attributes, min_children, dry_run, moves
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-root failures
            results.append({"object_id": root, "status": "error", "error": str(exc)})
            failed += 1
            continue
        nodes += visited
        results.append({"object_id": root, "status": "ok", "nodes": visited})

    promotions = [move for move in moves if move["action"] == "promote"]
    skipped = [move for move in moves if move["action"] == "skip"]
    logger.info(
        "consolidate_inheritance%s: %d promotions, %d skipped, %d nodes, %d failed",
        " (dry run)" if dry_run else "",
        len(promotions),
        len(skipped),
        nodes,
        failed,
    )
    return {
        "op": "consolidate_inheritance",
        "dry_run": dry_run,
        "min_children": min_children,
        "processed": len(roots),
        "succeeded": len(roots) - failed,
        "skipped": len(skipped),
        "failed": failed,
        "nodes": nodes,
        "promoted": len(promotions),
        "removed": sum(len(move["children"]) for move in promotions),
        "moves": moves,
        "results": results,
    }


async def _consolidate_subtree(
    active: httpx.AsyncClient,
    core_url: str,
    root: str,
    definitions: dict[str, dict[str, Any]],
    attributes: set[str] | None,
    min_children: int,
    dry_run: bool,
    moves: list[dict[str, Any]],
) -> int:
    """Walks one subtree top-down, records every case and applies the promotions.

    Returns the number of visited nodes. `dry_run` computes the identical
    proposal but performs no write.
    """
    # Values this run writes (or would write) and keys it removes, so the next
    # level already sees the state the run produces.
    written: dict[str, dict[str, Any]] = {}
    removed: dict[str, set[str]] = {}
    # Keys a node no longer maintains because this run removed them: their value
    # now comes from an ancestor (that is why the parent needs no second write).
    inherited: dict[str, dict[str, Any]] = {}
    queue = [root]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        children = await _children(active, core_url, node)
        parent_attributes = dict((await _object(active, core_url, node)).get("attributes") or {})
        node_attributes = _effective_attributes(
            parent_attributes, written.get(node), removed.get(node)
        )
        child_attributes = [
            _effective_attributes(
                dict(child.get("attributes") or {}),
                written.get(str(child.get("id"))),
                removed.get(str(child.get("id"))),
            )
            for child in children
        ]
        plan = plan_consolidation(
            parent=node,
            parent_label=parent_attributes.get("name"),
            parent_attributes=node_attributes,
            inherited=inherited.get(node, {}),
            children=[str(child.get("id")) for child in children],
            child_attributes=child_attributes,
            definitions=definitions,
            attributes=attributes,
            min_children=min_children,
        )
        moves.extend(plan)
        for move in plan:
            if move["action"] != "promote":
                continue
            attribute = str(move["attribute"])
            if move["parent_written"]:
                written.setdefault(node, {})[attribute] = move["value"]
                if not dry_run:
                    await _set_attribute(active, core_url, node, attribute, move["value"])
            for child_id in move["children"]:
                removed.setdefault(child_id, set()).add(attribute)
                inherited.setdefault(child_id, {})[attribute] = move["value"]
                if not dry_run:
                    await _remove_attribute(active, core_url, child_id, attribute)
        # Only descend into children that are parents themselves (`hasChildren`
        # comes with the children listing) - a leaf has nothing to consolidate.
        queue.extend(str(child.get("id")) for child in children if child.get("hasChildren"))
    return visited


def plan_consolidation(
    *,
    parent: str,
    parent_label: Any = None,
    parent_attributes: dict[str, Any],
    inherited: dict[str, Any],
    children: list[str],
    child_attributes: list[dict[str, Any]],
    definitions: dict[str, dict[str, Any]],
    attributes: set[str] | None,
    min_children: int,
) -> list[dict[str, Any]]:
    """Plans the promotions of one node (pure, unit-tested).

    Returns one entry per candidate attribute: `action: "promote"` when the value
    may be pushed up, otherwise `action: "skip"` with a `reason` from
    `CONSOLIDATION_REASONS`. Candidates are the **defined** attributes at least
    one child maintains; undefined convention fields (`name`) are ignored, and an
    attribute no child maintains is no case at all.
    """
    candidates = sorted(
        {
            name
            for attrs in child_attributes
            for name in attrs
            if not name.startswith("_") and name in definitions
        }
    )
    moves: list[dict[str, Any]] = []
    for attribute in candidates:
        if attributes is not None and attribute not in attributes:
            continue
        child_values = [attrs.get(attribute, _MISSING) for attrs in child_attributes]
        parent_written, reason = _consolidation_decision(
            definition=definitions.get(attribute),
            child_count=len(child_attributes),
            min_children=min_children,
            child_values=child_values,
            attribute=attribute,
            parent_attributes=parent_attributes,
            inherited=inherited,
        )
        moves.append(
            {
                "parent": parent,
                "parent_label": parent_label,
                "attribute": attribute,
                "value": _reported_value(child_values),
                "children": list(children),
                "action": "promote" if parent_written is not None else "skip",
                "reason": reason,
                "parent_written": bool(parent_written),
            }
        )
    return moves


def _consolidation_decision(
    *,
    definition: dict[str, Any] | None,
    child_count: int,
    min_children: int,
    child_values: list[Any],
    attribute: str,
    parent_attributes: dict[str, Any],
    inherited: dict[str, Any],
) -> tuple[bool | None, str | None]:
    """Decides one attribute: `(parent_written, None)` or `(None, reason)`."""
    if child_count < min_children:
        return None, "no_children"
    if any(value is _MISSING for value in child_values):
        return None, "missing_on_child"
    if any(is_interrupting_value(value) for value in child_values):
        return None, "interrupted"
    if any(value != child_values[0] for value in child_values):
        return None, "conflict_child_values"
    if not _is_promotable(definition):
        return None, "not_inheritable"
    value = child_values[0]
    if attribute in inherited:
        # An ancestor already carries this value: the parent needs no write, the
        # redundant copies at the children are simply removed.
        if inherited[attribute] != value:
            return None, "conflict_parent"
        return False, None
    if attribute not in parent_attributes:
        return True, None
    if parent_attributes[attribute] != value:
        return None, "conflict_parent"
    return False, None


def _is_promotable(definition: dict[str, Any] | None) -> bool:
    """True when the attribute may inherit at all (`inheritable`, not computed/key)."""
    if not definition:
        return False
    if not definition.get("inheritable"):
        return False
    return not definition.get("computed") and not definition.get("is_key")


def _reported_value(child_values: list[Any]) -> Any:
    """The value the children agree on, ignoring children without a value.

    Used for the report: a skipped case that is only skipped because one child
    does not maintain the attribute still names the value the others share; a
    real value conflict or an all-empty set reports None.
    """
    values = [
        value
        for value in child_values
        if value is not _MISSING and not is_interrupting_value(value)
    ]
    if not values:
        return None
    if any(value != values[0] for value in values):
        return None
    return values[0]


def _min_children(raw: Any) -> int:
    """Resolves `parameters.min_children` (default 2, never below 2)."""
    try:
        value = int(raw)
    except TypeError, ValueError:
        return CONSOLIDATION_MIN_CHILDREN
    return max(2, value)


def _effective_attributes(
    attributes: dict[str, Any],
    written: dict[str, Any] | None,
    removed: set[str] | None,
) -> dict[str, Any]:
    """Applies what this run already wrote/removed, so the next level sees it."""
    merged = dict(attributes)
    merged.update(written or {})
    for name in removed or ():
        merged.pop(name, None)
    return merged


async def _children(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
) -> list[dict[str, Any]]:
    """Direct composition children of an object (with their own attributes)."""
    response = await active.get(
        f"{core_url}/objects/{object_id}/children",
        params={"limit": str(CONSOLIDATION_CHILD_LIMIT)},
    )
    response.raise_for_status()
    return list(response.json())


async def _object(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
) -> dict[str, Any]:
    """One object as the core returns it (own attributes, not inherited ones)."""
    response = await active.get(f"{core_url}/objects/{object_id}")
    response.raise_for_status()
    return dict(response.json())


async def _set_attribute(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    attribute: str,
    value: Any,
) -> None:
    """Writes one attribute on the node a value is pushed up to."""
    response = await active.patch(
        f"{core_url}/objects/{object_id}", json={"attributes": {attribute: value}}
    )
    response.raise_for_status()


async def _remove_attribute(
    active: httpx.AsyncClient,
    core_url: str,
    object_id: str,
    attribute: str,
) -> None:
    """Drops one attribute key from a child so it inherits the value again."""
    response = await active.patch(
        f"{core_url}/objects/{object_id}", json={"remove_attributes": [attribute]}
    )
    response.raise_for_status()

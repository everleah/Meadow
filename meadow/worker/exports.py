"""`outbound_export` job handler: `json_export` connector (MEF export, Phase 6).

The handler receives `parameters.filter` (`type` and/or `status`), fetches the
matching objects from the core, builds a Meadow Exchange Format (MEF) document,
and stores it in `job_run.result`.

Quantified values (stored as canonical-SI numbers) are re-emitted as
`{"value": …, "unit": …}` in the attribute's display unit via the core's
`GET /units/convert` endpoint.
"""

from __future__ import annotations

import base64
import csv as _csv
import io
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from openpyxl import Workbook

from meadow.worker.http import loopback_host
from meadow.worker.jobs import JobHandler, JobMessage

logger = logging.getLogger(__name__)

#: Default cap for exported objects per run.
DEFAULT_EXPORT_LIMIT = 1000


def _now_iso_utc() -> str:
    """Returns the current time as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


def _is_number(value: Any) -> bool:
    """Whether `value` is a JSON number (excluding booleans)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


async def _load_unit_map(
    client: httpx.AsyncClient,
    core_url: str,
) -> dict[str, tuple[str, str]]:
    """Loads quantified attribute definitions: `name -> (unit_id, display_unit_id)`."""
    response = await client.get(
        f"{core_url}/attribute-definitions",
        params={"limit": 1000},
    )
    response.raise_for_status()
    unit_map: dict[str, tuple[str, str]] = {}
    for definition in response.json():
        name = definition.get("name")
        unit_id = definition.get("unit_id")
        if not name or not unit_id:
            continue
        display_unit_id = definition.get("display_unit_id") or unit_id
        unit_map[str(name)] = (str(unit_id), str(display_unit_id))
    return unit_map


async def _emit_quantified(
    client: httpx.AsyncClient,
    core_url: str,
    attributes: dict[str, Any],
    unit_map: dict[str, tuple[str, str]],
) -> None:
    """Re-emits canonical-SI numbers as `{"value":…,"unit":…}` objects."""
    for name, (unit_id, display_unit_id) in unit_map.items():
        value = attributes.get(name)
        if not _is_number(value):
            continue
        response = await client.get(
            f"{core_url}/units/convert",
            params={"value": value, "from": unit_id, "to": display_unit_id},
        )
        response.raise_for_status()
        converted = response.json()
        attributes[name] = {"value": converted["value"], "unit": converted["unit"]}


# --- Structured export (UI wizard, Phase 9) --------------------------------

_BASE_FIELDS = ("id", "type", "status")


def _strip_object_id(value: Any) -> str:
    """Strips an `object:` prefix, keeping the bare record key."""
    text = str(value)
    return text.split(":", 1)[1] if ":" in text else text


async def _resolve_objects(
    active: httpx.AsyncClient,
    core_url: str,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolves the export source to a list of full object records."""
    kind = source.get("kind")
    if kind == "type":
        object_type = source.get("type")
        if not object_type:
            raise RuntimeError("`source.kind='type'` requires `type`")
        resp = await active.get(f"{core_url}/objects", params={"type": object_type, "limit": 1000})
        resp.raise_for_status()
        return resp.json()
    if kind == "category":
        category = source.get("category")
        if not category:
            raise RuntimeError("`source.kind='category'` requires `category`")
        resp = await active.get(f"{core_url}/objects", params={"category": category, "limit": 1000})
        resp.raise_for_status()
        return resp.json()

    ids: list[str] = []
    if kind == "collection":
        collection_id = source.get("id")
        if not collection_id:
            raise RuntimeError("`source.kind='collection'` requires `id`")
        resp = await active.get(
            f"{core_url}/collections/{collection_id}/members",
            params={"limit": 1000},
        )
        resp.raise_for_status()
        ids = [_strip_object_id(item.get("id")) for item in resp.json()]
    elif kind == "saved_search":
        search_id = source.get("id")
        if not search_id:
            raise RuntimeError("`source.kind='saved_search'` requires `id`")
        resp = await active.post(f"{core_url}/saved-searches/{search_id}/execute")
        resp.raise_for_status()
        ids = [_strip_object_id(item) for item in (resp.json().get("ids") or [])]
    elif kind == "selection":
        ids = [_strip_object_id(item) for item in (source.get("object_ids") or [])]
    else:
        raise RuntimeError(f"unsupported `source.kind` {kind!r}")

    if not ids:
        return []
    resp = await active.get(f"{core_url}/objects", params={"ids": ",".join(ids), "limit": 1000})
    resp.raise_for_status()
    return resp.json()


async def _load_languages(client: httpx.AsyncClient, core_url: str) -> dict[str, list[str]]:
    """Returns `code -> fallback_order` from the language tree."""
    resp = await client.get(f"{core_url}/languages")
    resp.raise_for_status()
    result: dict[str, list[str]] = {}
    for lang in resp.json():
        code = lang.get("code")
        if code:
            result[str(code)] = [str(c) for c in (lang.get("fallback_order") or [])]
    return result


async def _load_multilingual(client: httpx.AsyncClient, core_url: str) -> tuple[set[str], set[str]]:
    """Returns (multilingual attr names, quantified attr unit_map names)."""
    resp = await client.get(f"{core_url}/attribute-definitions", params={"limit": 1000})
    resp.raise_for_status()
    multilingual: set[str] = set()
    quantified: set[str] = set()
    for definition in resp.json():
        name = definition.get("name")
        if not name:
            continue
        if definition.get("multilingual"):
            multilingual.add(str(name))
        if definition.get("unit_id"):
            quantified.add(str(name))
    return multilingual, quantified


def _pick(value: Any, fallback: dict[str, list[str]], locale: str) -> Any:
    """Resolves a multilingual dict via the locale fallback chain.

    A dict is treated as a language map only when at least one of its keys is a
    known language code; structured dicts (e.g. ``{"value":…,"unit":…}``) are
    returned unchanged.
    """
    if not isinstance(value, dict):
        return value
    if not any(key in fallback or key == locale for key in value):
        return value
    chain = [locale, *fallback.get(locale, []), "en"]
    for code in chain:
        if code in value and value[code] is not None:
            return value[code]
    return ""


def _build_columns(fields: list[str], locales: list[str], multilingual: set[str]) -> list[str]:
    """Builds the column list, expanding multilingual attrs per locale."""
    columns: list[str] = []
    for field in fields:
        if field in _BASE_FIELDS or field not in multilingual:
            columns.append(field)
        else:
            for locale in locales:
                columns.append(f"{field}_{locale}")
    if not columns:
        columns = list(_BASE_FIELDS)
    return columns


def _build_rows(
    objects: list[dict[str, Any]],
    columns: list[str],
    locales: list[str],
    fallback: dict[str, list[str]],
    multilingual: set[str],
) -> list[dict[str, Any]]:
    """Builds a row per object, resolving multilingual columns per locale."""
    rows: list[dict[str, Any]] = []
    for obj in objects:
        attrs = obj.get("attributes") or {}
        attributes = attrs if isinstance(attrs, dict) else {}
        row: dict[str, Any] = {}
        for column in columns:
            if column == "id":
                row["id"] = _strip_object_id(obj.get("id"))
            elif column in ("type", "status"):
                row[column] = obj.get(column) or ""
            elif "_" in column and column.rsplit("_", 1)[0] in multilingual:
                # multilingual column `<attr>_<locale>`
                attr_name, locale = column.rsplit("_", 1)
                row[column] = _pick(attributes.get(attr_name), fallback, locale)
            else:
                # plain attribute column: resolve dicts with the first locale
                row[column] = _pick(
                    attributes.get(column, ""), fallback, locales[0] if locales else "de"
                )
        rows.append(row)
    return rows


def _serialize_csv(
    columns: list[str], rows: list[dict[str, Any]], separator: str, header: bool
) -> str:
    """Serializes rows to CSV (UTF-8)."""
    buffer = io.StringIO()
    writer = _csv.writer(buffer, delimiter=separator or ",")
    if header:
        writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_cell(row.get(col, "")) for col in columns])
    return buffer.getvalue()


def _csv_cell(value: Any) -> str:
    """Converts a cell value to a CSV string (JSON for structured values)."""
    if isinstance(value, (dict, list)):
        import json as _json

        return _json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _serialize_xlsx(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    """Serializes rows to an XLSX workbook and returns the bytes."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Export"
    sheet.append(columns)
    for row in rows:
        sheet.append([_csv_cell(row.get(col, "")) for col in columns])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def _structured_export(
    active: httpx.AsyncClient,
    core_url: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Runs a structured export and returns the serializable job result."""
    source = parameters.get("source") or {}
    format_ = str(parameters.get("format") or "json")
    fields = [str(f) for f in (parameters.get("fields") or [])]
    if not fields:
        fields = list(_BASE_FIELDS)
    locales = [str(locale) for locale in (parameters.get("locales") or [])]
    if not locales:
        locales = ["de"]
    options = parameters.get("options") or {}
    header = bool(options.get("header", True))
    separator = str(options.get("separator") or ",")

    multilingual, quantified = await _load_multilingual(active, core_url)
    fallback = await _load_languages(active, core_url)

    # Resolve objects (synchronously for the requested source).
    objects = await _resolve_objects(active, core_url, source)

    if format_ == "json":
        unit_map = await _load_unit_map(active, core_url)
        mef_objects: list[dict[str, Any]] = []
        for obj in objects:
            attributes = dict(obj.get("attributes") or {})
            await _emit_quantified(active, core_url, attributes, unit_map)
            mef_objects.append(
                {
                    "id": obj.get("id"),
                    "type": obj.get("type"),
                    "attributes": attributes,
                    "status": obj.get("status") or "draft",
                }
            )
        document = {
            "format_version": "1.0",
            "source": "meadow",
            "created_at": _now_iso_utc(),
            "objects": mef_objects,
            "references": [],
        }
        return {
            "format": "json",
            "exported": len(mef_objects),
            "columns": fields,
            "document": document,
        }

    columns = _build_columns(fields, locales, multilingual)
    rows = _build_rows(objects, columns, locales, fallback, multilingual)
    if format_ == "xlsx":
        data = _serialize_xlsx(columns, rows)
        return {
            "format": "xlsx",
            "exported": len(objects),
            "columns": columns,
            "xlsx_base64": base64.b64encode(data).decode("ascii"),
        }
    csv_text = _serialize_csv(columns, rows, separator, header)
    return {
        "format": "csv",
        "exported": len(objects),
        "columns": columns,
        "csv": csv_text,
    }


def build_export_handler(
    core_url: str,
    *,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> JobHandler:
    """Builds the `outbound_export` (json_export) job handler.

    `client` may be injected for tests; otherwise a short-lived client is
    created per job.
    """
    core_url = loopback_host(core_url)
    headers = {"x-api-key": api_key} if api_key else {}

    async def run(active: httpx.AsyncClient, message: JobMessage) -> dict[str, Any]:
        parameters = message.parameters
        # Structured (UI wizard) export: `source` is present.
        if parameters.get("source") is not None:
            return await _structured_export(active, core_url, parameters)

        filter_ = parameters.get("filter") or {}
        if not isinstance(filter_, dict):
            raise RuntimeError("`parameters.filter` must be an object")
        object_type = filter_.get("type")
        status = filter_.get("status")
        limit = int(parameters.get("limit") or DEFAULT_EXPORT_LIMIT)

        query: dict[str, Any] = {"limit": min(max(limit, 1), 1000)}
        if object_type:
            query["type"] = str(object_type)
        response = await active.get(f"{core_url}/objects", params=query)
        response.raise_for_status()
        objects = response.json()

        if status:
            objects = [obj for obj in objects if obj.get("status") == str(status)]

        unit_map = await _load_unit_map(active, core_url)

        mef_objects: list[dict[str, Any]] = []
        for obj in objects:
            attributes = obj.get("attributes")
            attributes = dict(attributes) if isinstance(attributes, dict) else {}
            await _emit_quantified(active, core_url, attributes, unit_map)
            mef_objects.append(
                {
                    "id": obj.get("id"),
                    "type": obj.get("type"),
                    "attributes": attributes,
                    "status": obj.get("status") or "draft",
                }
            )

        document: dict[str, Any] = {
            "format_version": "1.0",
            "source": "meadow",
            "created_at": _now_iso_utc(),
            "objects": mef_objects,
            "references": [],
        }

        logger.info("json_export exported %d objects", len(mef_objects))
        return {
            "connector": "json_export",
            "exported": len(mef_objects),
            "document": document,
        }

    async def handle(message: JobMessage) -> dict[str, Any]:
        if client is not None:
            return await run(client, message)
        async with httpx.AsyncClient(timeout=60.0, headers=headers) as active:
            return await run(active, message)

    return handle


__all__ = ["build_export_handler"]

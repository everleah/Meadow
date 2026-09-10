"""Meadow Exchange Format (MEF) document helpers.

Pure parsing/inspection helpers for MEF JSON documents (see
``docs/integration/mef-and-events.md``). The frontend import page uses them to
detect a MEF upload, render a preview and import the document through the
standard ``POST /imports`` pipeline; keeping them here (instead of in the page)
also makes them reusable by a future worker-side adapter.
"""

from __future__ import annotations

import json
from typing import Any

#: MEF format version understood by this implementation.
SUPPORTED_FORMAT_VERSIONS = ("1.0",)


def is_mef_document(data: object) -> bool:
    """Whether ``data`` looks like a MEF document (an object with objects/references)."""
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("objects"), list) or isinstance(data.get("references"), list)


def parse_mef(content: bytes) -> dict[str, Any]:
    """Parses MEF JSON bytes and validates the document envelope.

    Raises ``ValueError`` when the payload is not valid JSON or not a MEF
    document (mirrors the core's ``validate_mef`` so failures surface early).
    """
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise ValueError(f"file is not UTF-8 encoded ({error})") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON ({error.msg} at line {error.lineno})") from error
    if not isinstance(data, dict):
        raise ValueError("MEF document must be a JSON object")
    if not is_mef_document(data):
        raise ValueError("MEF document must contain an `objects` or `references` array")
    return data


def type_counts(document: dict[str, Any]) -> dict[str, int]:
    """Counts the objects per `type` (unknown types are grouped under `""`)."""
    counts: dict[str, int] = {}
    for entry in document.get("objects") or []:
        if not isinstance(entry, dict):
            continue
        object_type = str(entry.get("type") or "")
        counts[object_type] = counts.get(object_type, 0) + 1
    return counts


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    """Returns the headline facts of a MEF document for the import preview."""
    objects = [entry for entry in (document.get("objects") or []) if isinstance(entry, dict)]
    references = [entry for entry in (document.get("references") or []) if isinstance(entry, dict)]
    return {
        "format_version": str(document.get("format_version") or ""),
        "source": str(document.get("source") or ""),
        "created_at": str(document.get("created_at") or ""),
        "objects": len(objects),
        "references": len(references),
        "types": type_counts(document),
        "with_ids": sum(1 for entry in objects if entry.get("id")),
    }


def object_rows(document: dict[str, Any], limit: int = 20) -> list[dict[str, str]]:
    """Builds preview rows (`id`, `type`, `status`, attribute names) for objects."""
    rows: list[dict[str, str]] = []
    for entry in document.get("objects") or []:
        if not isinstance(entry, dict):
            continue
        attributes = entry.get("attributes")
        names = ", ".join(str(name) for name in attributes) if isinstance(attributes, dict) else ""
        rows.append(
            {
                "id": str(entry.get("id") or ""),
                "type": str(entry.get("type") or ""),
                "status": str(entry.get("status") or ""),
                "attributes": names,
            }
        )
        if len(rows) >= limit:
            break
    return rows


__all__ = [
    "SUPPORTED_FORMAT_VERSIONS",
    "is_mef_document",
    "object_rows",
    "parse_mef",
    "summarize",
    "type_counts",
]

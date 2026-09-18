"""Tabular (CSV/XLSX) ↔ MEF adapter.

Turns a spreadsheet into a Meadow Exchange Format (MEF) document so the standard
``inbound_import`` pipeline can upsert the rows (by business key or source
reference). The pure parsing/mapping functions live here so the frontend import
page (preview + mapping) and any future worker-side adapter share one
implementation.

Mapping target syntax:

- ``""`` (empty) — ignore the column.
- ``id`` / ``type`` / ``status`` — a MEF object base field.
- ``<attribute>`` — a plain attribute value.
- ``<attribute>|<locale>`` — a multilingual attribute value written as
  ``{locale: value}`` (e.g. ``title|de``).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Non-attribute target fields (mapped onto the MEF object top level).
BASE_TARGETS = ("id", "type", "status")
#: Target value meaning "ignore this column".
IGNORE = ""
#: Separator between an attribute name and a locale in a mapping target.
LOCALE_SEPARATOR = "|"


@dataclass
class TableData:
    """Parsed tabular data: a header plus row dicts (all values strings)."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)


def parse_target(target: str) -> tuple[str, str | None]:
    """Splits a mapping target into ``(field, locale)`` (``locale`` may be ``None``)."""
    if LOCALE_SEPARATOR in target:
        field_name, _, locale = target.partition(LOCALE_SEPARATOR)
        return field_name, locale or None
    return target, None


def _decode(content: bytes) -> str:
    """Decodes CSV bytes, tolerating Excel's UTF-8 BOM and Windows code pages."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    """Best-effort delimiter detection (comma/semicolon/tab/pipe)."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:  # default to comma
        return ","


def _cell(value: object) -> str:
    """Normalises a raw cell to a trimmed string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _table_from_matrix(rows: list[list[object]]) -> TableData:
    """Builds a :class:`TableData` from a matrix (first row = header)."""
    if not rows:
        return TableData()
    header = [_cell(cell) for cell in rows[0]]
    while header and header[-1] == "":
        header.pop()
    data: list[dict[str, str]] = []
    for raw in rows[1:]:
        values = [_cell(cell) for cell in raw[: len(header)]]
        if not any(values):
            continue
        data.append({header[i]: values[i] for i in range(len(header))})
    return TableData(columns=header, rows=data)


def parse_csv(content: bytes, delimiter: str | None = None) -> TableData:
    """Parses CSV bytes into a :class:`TableData`."""
    text = _decode(content)
    sep = delimiter or _sniff_delimiter(text[:4096])
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    return _table_from_matrix([list(row) for row in reader])


def sheet_names(content: bytes) -> list[str]:
    """Returns the worksheet names of an XLSX workbook."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return list(workbook.sheetnames)


def parse_xlsx(content: bytes, sheet: str | None = None) -> TableData:
    """Parses an XLSX worksheet (first sheet by default) into a :class:`TableData`."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    worksheet = workbook[sheet] if sheet and sheet in workbook.sheetnames else workbook.active
    return _table_from_matrix([list(row) for row in worksheet.iter_rows(values_only=True)])


def parse_table(
    name: str,
    content: bytes,
    *,
    delimiter: str | None = None,
    sheet: str | None = None,
) -> TableData:
    """Parses a CSV/XLSX upload (by file extension) into a :class:`TableData`."""
    if (name or "").lower().endswith((".xlsx", ".xlsm")):
        return parse_xlsx(content, sheet)
    return parse_csv(content, delimiter)


def build_mef(
    table: TableData,
    mapping: dict[str, str],
    *,
    default_type: str,
    source: str | None = None,
) -> dict[str, Any]:
    """Builds a MEF document from parsed rows and a column→target mapping."""
    objects: list[dict[str, Any]] = []
    for row in table.rows:
        attributes: dict[str, Any] = {}
        object_type = default_type
        status = "draft"
        external_id = ""
        for column, target in mapping.items():
            if not target:
                continue
            value = row.get(column, "")
            field_name, locale = parse_target(target)
            if field_name == "type":
                if value:
                    object_type = value
            elif field_name == "status":
                if value:
                    status = value
            elif field_name == "id":
                external_id = value
            elif value:
                if locale:
                    existing = attributes.get(field_name)
                    merged = dict(existing) if isinstance(existing, dict) else {}
                    merged[locale] = value
                    attributes[field_name] = merged
                else:
                    attributes[field_name] = value
        obj: dict[str, Any] = {
            "type": object_type or default_type,
            "attributes": attributes,
            "status": status or "draft",
        }
        if external_id:
            obj["id"] = external_id
        objects.append(obj)
    return {
        "format_version": "1.0",
        "source": source or "tabular-import",
        "created_at": datetime.now(UTC).isoformat(),
        "objects": objects,
        "references": [],
    }


__all__ = [
    "TableData",
    "BASE_TARGETS",
    "IGNORE",
    "LOCALE_SEPARATOR",
    "parse_target",
    "parse_table",
    "parse_csv",
    "parse_xlsx",
    "sheet_names",
    "build_mef",
]

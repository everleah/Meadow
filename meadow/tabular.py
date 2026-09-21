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
import re
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


# --- what a column looks like, and whether a mapping works ----------------------

_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_BOOLEAN_WORDS = {"true", "false", "yes", "no", "ja", "nein", "wahr", "falsch"}


def _value_kind(values: list[str]) -> str:
    """The kind every non-empty value shares (`text` when they differ)."""
    if not values:
        return "empty"
    candidates: tuple[tuple[str, Any], ...] = (
        ("boolean", lambda value: value.lower() in _BOOLEAN_WORDS),
        ("datetime", lambda value: bool(_DATETIME.match(value))),
        ("date", lambda value: bool(_DATE.match(value))),
        ("number", lambda value: bool(_DECIMAL.match(value))),
    )
    for kind, matches in candidates:
        if all(matches(value) for value in values):
            return kind
    return "number" if all(_INTEGER.match(value) for value in values) else "text"


def describe_columns(table: TableData) -> dict[str, str]:
    """Describes what each column *looks like* — without a single cell value.

    An AI mapping suggestion needs more than the header (a file whose columns are
    called `Feld 17` would be pure guesswork), but object values must never reach a
    prompt (`plan/detail/ki-assistenz.md` §6.8). This projection is the compromise:
    the value kind, the length range, how many rows are empty and whether the values
    differ — nothing that identifies a row.
    """
    rows = len(table.rows)
    hints: dict[str, str] = {}
    for column in table.columns:
        filled = [value for value in (row.get(column, "") for row in table.rows) if value]
        kind = _value_kind(filled)
        if kind == "empty":
            hints[column] = "empty in every row"
            continue
        lengths = [len(value) for value in filled]
        parts = [kind]
        parts.append(
            f"{lengths[0]} characters"
            if len(set(lengths)) == 1
            else f"{min(lengths)}-{max(lengths)} characters"
        )
        if len(filled) < rows:
            parts.append(f"empty in {rows - len(filled)} of {rows} rows")
        if len(set(filled)) == len(filled) and len(filled) > 1:
            parts.append("all values different")
        hints[column] = ", ".join(parts)
    return hints


@dataclass(frozen=True)
class MappingCheck:
    """The verdict of [`check_mapping`]: what breaks the import, what merely annoys."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the mapping holds no known mistake."""
        return not self.errors

    def message(self) -> str:
        """One line for a caller that talks to a human (or to a model)."""
        if self.errors:
            return "; ".join(self.errors)
        if self.warnings:
            return "accepted, with notes: " + "; ".join(self.warnings)
        return "accepted"


def check_mapping(
    mapping: dict[str, str],
    *,
    columns: list[str] | tuple[str, ...],
    attributes: dict[str, bool],
    languages: list[str] | tuple[str, ...] = (),
    conventional: list[str] | tuple[str, ...] = (),
    key_attributes: list[str] | tuple[str, ...] = (),
    default_type: str = "",
) -> MappingCheck:
    """Checks a column→target mapping against the file and the data model.

    The check runs where the mapping is executed (`build_mef` right above) with the
    model data the core owns, so a suggestion meets the rules the import applies.

    `attributes` maps every known attribute name to *is it multilingual*;
    `conventional` names attributes that have no definition but are understood by
    convention (an object type's label and key attributes, e.g. `name`).

    **Errors** — the import would do the wrong thing: a column the file does not
    have, two columns writing one target (the later one wins, silently), a language
    outside the configured data languages, a language on a base field, no column
    mapped at all (one empty object per row), or no object type at all.

    **Warnings** — the import runs, but the result is not what a user expects: a
    target without an attribute definition (written, but untyped), a multilingual
    attribute without a language, a column without a header, or a business key no
    column fills (a re-import cannot match the rows).
    """
    errors: list[str] = []
    warnings: list[str] = []
    known_columns = {str(column) for column in columns}
    known_languages = {str(code) for code in languages}
    all_names = set(attributes) | set(conventional)
    used: dict[str, list[str]] = {}

    if not columns:
        errors.append("the file has no columns")
    if mapping and not any(str(target or "").strip() for target in mapping.values()):
        errors.append(
            "no column is mapped — the import would create one object per row without values"
        )
    if "" in known_columns:
        warnings.append("the file has a column without a header — it cannot be mapped")

    for column, raw_target in mapping.items():
        if str(column) not in known_columns:
            errors.append(f"`{column}` is not a column of the file")
            continue
        target = str(raw_target or "").strip()
        field_name, locale = parse_target(target)
        if not field_name:
            continue
        if field_name in BASE_TARGETS:
            if locale:
                errors.append(
                    f"`{target}`: a language applies to an attribute, not to `{field_name}`"
                )
            continue
        if locale and locale not in known_languages:
            errors.append(f"`{target}`: `{locale}` is not a configured data language")
        key = f"{field_name}{LOCALE_SEPARATOR}{locale}" if locale else field_name
        used.setdefault(key, []).append(str(column))
        if field_name not in all_names:
            warnings.append(
                f"`{field_name}` has no attribute definition — the value is written, "
                "but the attribute stays untyped"
            )
        elif field_name in attributes and attributes[field_name] and not locale:
            warnings.append(
                f"`{field_name}` is multilingual — without a language the value is stored "
                f"as a plain value; write `{field_name}{LOCALE_SEPARATOR}<language>`"
            )

    for target, columns_used in used.items():
        if len(columns_used) > 1:
            errors.append(
                f"{', '.join(f'`{column}`' for column in columns_used)} write `{target}` — "
                "only the last one would survive"
            )

    mapped_types = any(
        parse_target(str(target or "").strip())[0] == "type" for target in mapping.values()
    )
    if not default_type and not mapped_types:
        errors.append("no object type: map a `type` column or choose a default type")
    mapped_names = {target.split(LOCALE_SEPARATOR)[0] for target in used}
    missing_keys = [name for name in key_attributes if name not in mapped_names]
    if missing_keys and default_type:
        warnings.append(
            "no column fills the business key "
            + ", ".join(f"`{name}`" for name in missing_keys)
            + f" of `{default_type}` — the rows import, but a re-import cannot match them"
        )

    return MappingCheck(errors=sorted(set(errors)), warnings=sorted(set(warnings)))


__all__ = [
    "TableData",
    "BASE_TARGETS",
    "IGNORE",
    "LOCALE_SEPARATOR",
    "MappingCheck",
    "check_mapping",
    "describe_columns",
    "parse_target",
    "parse_table",
    "parse_csv",
    "parse_xlsx",
    "sheet_names",
    "build_mef",
]

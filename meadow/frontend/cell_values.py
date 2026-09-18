"""Cell values of the product grid: parsing, storage form and the save patch.

The grid shows **typed** cells (number, bool, string, a localized enum label) so AG Grid
offers the matching editor; the core stores the attribute values (a number, a bool, the
enum **code**, a `{language: value}` map). This module owns the two conversions between
those worlds (`editable_cell_value` builds the cell, `storable_cell_value` turns an edit
back) plus the batch patch of the staged edits, which is what actually travels to
`POST /objects/batch-update`.

The rules it encodes:

- An **enum** cell carries the localized label, the stored value is the code.
- A **multilingual** attribute keeps its other languages: an edit merges into the
  working-language key of the original map (and removes the key when it is cleared).
- A cell that was staged but **reverted** to its loaded value is not sent at all — the
  core merges attributes, so "untouched" and "changed back" must look the same.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard


def as_number(value: object) -> object:
    """Parses a cell value to int/float (keeps the value on failure)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return value
    return int(number) if number.is_integer() and "." not in text else number


def as_bool(value: object) -> object:
    """Parses a cell value to a boolean (None stays None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "on", "yes")


def is_multilingual_map(value: object) -> TypeGuard[dict[str, Any]]:
    """Whether a value is a `{language: value}` map (rather than quantified)."""
    return isinstance(value, dict) and "unit" not in value


def storable_cell_value(
    definition: Mapping[str, Any],
    cell_value: object,
    original: object,
    locale: str,
    labels: Mapping[str, str] | None = None,
) -> object:
    """Turns an edited cell value back into a storable attribute value.

    Enum cells hold a localized label -> the stored code is restored. A
    multilingual attribute keeps its other languages: the new value is merged
    into the working-language key of the original map.
    """
    atype = str(definition.get("type") or "text")
    if atype == "enum":
        text = "" if cell_value is None else str(cell_value)
        new_value: object = next(
            (code for code, label in (labels or {}).items() if label == text), text or None
        )
    elif atype == "number":
        new_value = as_number(cell_value)
    elif atype == "boolean":
        new_value = as_bool(cell_value)
    else:
        new_value = None if cell_value in (None, "") else str(cell_value)
    if is_multilingual_map(original):
        merged = dict(original)
        if new_value is None:
            merged.pop(locale, None)
        else:
            merged[locale] = new_value
        return merged
    return new_value


def build_patch(object_id: str, client_name: object, original: dict, locale: str) -> dict:
    """Builds a batch-update item for an edited name value.

    Preserves other locales when the attribute is multilingual; otherwise the
    scalar name is replaced.
    """
    original_name = (original.get("attributes") or {}).get("name")
    new_name: object
    if isinstance(original_name, dict):
        merged = dict(original_name)
        merged[locale] = client_name
        new_name = merged
    else:
        new_name = client_name
    return {"id": object_id, "attributes": {"name": new_name}}


#: The grid field of the object's display value (`_name` is edited like a cell).
NAME_FIELD = "_name"
#: Prefix of the editable attribute columns (`attr_<attribute>`).
ATTRIBUTE_PREFIX = "attr_"


def staged_patches(
    *,
    staged: Mapping[str, set[str]],
    originals: Mapping[str, Mapping[str, Any]],
    live_rows: Mapping[str, Mapping[str, Any]],
    loaded_rows: Mapping[str, Mapping[str, Any]],
    definitions: Mapping[str, Mapping[str, Any]],
    locale: str,
    enum_resolvers: Mapping[str, Mapping[str, str] | None],
) -> list[dict[str, Any]]:
    """The batch-update items for the staged cells — only **real** changes.

    `staged` records the fields a user touched per object; the values come from the
    **live** client rows (what the browser holds now) and are compared against the
    rows as they were loaded, so a cell that was edited and changed back is skipped.
    Objects whose original or live row is gone (deleted, filtered away) are skipped
    too — a patch without a counterpart would overwrite the wrong object.
    """
    patches: list[dict[str, Any]] = []
    for object_id, fields in staged.items():
        original = originals.get(object_id)
        row = live_rows.get(object_id)
        if original is None or row is None:
            continue
        loaded = loaded_rows.get(object_id, {})
        attributes: dict[str, Any] = {}
        if NAME_FIELD in fields and row.get(NAME_FIELD) != loaded.get(NAME_FIELD):
            patch = build_patch(object_id, row.get(NAME_FIELD), dict(original), locale)
            attributes.update(patch["attributes"])
        raw_attributes = original.get("attributes") or {}
        for field in sorted(fields):
            if not str(field).startswith(ATTRIBUTE_PREFIX):
                continue  # the display value is handled above
            name = str(field)[len(ATTRIBUTE_PREFIX) :]
            definition = definitions.get(name)
            if definition is None:
                continue  # read-only (language) column
            if row.get(field) == loaded.get(field):
                continue  # staged but reverted
            attributes[name] = storable_cell_value(
                definition,
                row.get(field),
                raw_attributes.get(name),
                locale,
                enum_resolvers.get(name),
            )
        if attributes:
            patches.append({"id": object_id, "attributes": attributes})
    return patches

"""Object list grid (AG Grid): the main work area (Phase 7, steps 3-5).

The grid lists objects with human-friendly display (resolved name, status
badge), supports inline editing / batch save, offers a column picker and
personal views, and shows a composition (structure) tree that filters the
grid to the selected subtree. It also offers the category/collection views,
the completeness column with its server-side filter bar, and one hidden
per-language column for every multilingual attribute (Sprach-Spalten).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, TypeGuard
from urllib.parse import quote

import httpx
from fastapi import Request
from nicegui import app, ui

from meadow.frontend import config, edit_queue, search_form, taxonomy_tree
from meadow.frontend.api import CoreClient, CoreError, is_session_expired
from meadow.frontend.auth import handle_session_expired
from meadow.frontend.display import (
    DEFAULT_RELEASE_STATUS,
    channel_options,
    completeness_badge,
    dimension_options,
    release_badges,
    resolve_value,
    status_badge,
    taxonomy_options,
    taxonomy_type_names,
)
from meadow.frontend.export_fields import group_members, group_order
from meadow.frontend.filters import filter_select
from meadow.frontend.help_page import chapter_button, info_button
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.lazy_tree import LazyTree
from meadow.frontend.multilang import multilingual_value_editor
from meadow.frontend.object_picker import OBJECT_ROOT_ID, open_object_picker, render_picker_hit
from meadow.frontend.range_values import format_range
from meadow.frontend.reference_data import has_value, visible_in_picker
from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    association_definitions,
    category_definitions,
    category_dimension_bindings,
    definition_key,
    effective_target_types,
    usable_definitions,
)
from meadow.frontend.url_values import media_kind, open_media_dialog

log = logging.getLogger(__name__)

# Maximum hits the grid search pulls per query (picker + row load); the search
# is server-side, so the result set stays bounded regardless of dataset size.
SEARCH_LIMIT = 100
# Debounce (ms) for the filter bar's search field: one core request per pause.
SEARCH_DEBOUNCE_MS = 400
# Separator between a field prefix and the language code in field names of
# per-language columns (e.g. ``attr_title__de`` for the German `title` value).
LANG_SEPARATOR = "__"
# Field prefix of the per-language columns of the convention `name` attribute.
NAME_FIELD_PREFIX = "_name"
# Upper bound (characters) for the content-based column width: a long text must
# not blow up the grid ("Grid auf Inhalt anpassen").
MAX_CONTENT_CHARS = 30
# Rough width of one character in the grid font plus cell padding, in pixels.
CHAR_WIDTH_PX = 8
CELL_PADDING_PX = 26
MIN_COLUMN_PX = 70
# Session key holding the visible column set of the current user.
COLUMNS_STORAGE_KEY = "grid_columns"
# Number of 2-second polls a bulk run may stay `pending` before the progress
# dialog warns that no worker seems to be connected (~20 s).
PENDING_WARN_POLLS = 10
# Review status of the workflow state machine (plan/detail/workflow-freigaben.md);
# the default target of the `send_for_approval` bulk operation.
APPROVAL_STATUS = "in_review"
# Operations that change data and therefore offer the "send for approval
# afterwards" switch (`change_status`, `send_for_approval` and `delete` do not).
_APPROVAL_MODIFIER_OPS = (
    "set_attribute",
    "clear_attribute",
    "add_category",
    "remove_category",
    "add_relation",
    "remove_relation",
    "change_parent",
)
# Relation types with a dedicated bulk operation are hidden in the generic
# relation selection: composition (`change_parent`) and every categorisation
# (`add_category`/`remove_category`). Their usages (`role`) decide this, so
# custom category types behave like the built-in one.
# Field of the per-row inheritance markers (`{field: "inherited from …"}`) and
# the AG Grid column options that render them: NiceGUI converts `:`-prefixed
# keys into real JavaScript functions on the client (`convertDynamicProperties`),
# so an inherited cell is dimmed/italic and carries the source as browser tooltip.
INHERITED_FIELD = "_inherited"
#: Field of the per-row "media URL" markers (`{field: tooltip}`): the cell holds
#: an image/video URL, so it is not rendered as a link and a click opens the
#: preview dialog instead of a new tab.
MEDIA_FIELD = "_media"
#: Field of the per-row "explicitly empty" markers (`{field: tooltip}`): the key
#: exists but carries no value, so the attribute does **not** inherit.
INTERRUPTED_FIELD = "_interrupted"
#: Dimmed + italic cell for inherited values (the marker icon comes from the
#: renderer below, because "not maintained here" must be recognisable at a
#: glance — a grey tone alone is too subtle). An explicitly empty value is
#: tinted like a warning instead, because it silently blocks inheritance.
INHERITED_CELL_STYLE = (
    "(params) => {"
    " const data = params.data || {};"
    " const field = params.colDef.field;"
    " if (data._inherited && data._inherited[field])"
    "  return {fontStyle: 'italic', color: '#6b7280'};"
    " if (data._interrupted && data._interrupted[field])"
    "  return {fontStyle: 'italic', color: '#b45309'};"
    " return null;"
    "}"
)
INHERITED_CELL_TOOLTIP = (
    "(params) => {"
    " const data = params.data || {};"
    " const field = params.colDef.field;"
    " return (data._inherited && data._inherited[field])"
    " || (data._interrupted && data._interrupted[field]) || '';"
    "}"
)
#: Cell renderer that prefixes an inherited value with the `swap_horiz` icon and
#: an explicitly empty one with `link_off` (same symbols as the product form) and
#: keeps the explanation as native tooltip. The renderer only affects the display
#: — `params.value` stays the plain value, so sorting and the inline editor keep
#: working.
INHERITED_CELL_RENDERER = (
    "(params) => {"
    " const data = params.data || {};"
    " const field = params.colDef.field;"
    " const inherited = data._inherited && data._inherited[field];"
    " const interrupted = data._interrupted && data._interrupted[field];"
    " const marker = inherited || interrupted;"
    " const span = document.createElement('span');"
    " const text = params.value === null || params.value === undefined"
    " ? '' : String(params.value);"
    " if (marker) {"
    "  const icon = document.createElement('i');"
    "  icon.className = 'material-icons';"
    "  icon.style.fontSize = '14px';"
    "  icon.style.verticalAlign = '-2px';"
    "  icon.style.marginRight = '2px';"
    "  icon.style.fontStyle = 'normal';"
    "  icon.style.color = inherited ? '#9e9e9e' : '#b45309';"
    "  icon.textContent = inherited ? 'swap_horiz' : 'link_off';"
    "  span.appendChild(icon);"
    "  span.title = marker;"
    " }"
    " span.appendChild(document.createTextNode(text));"
    " return span;"
    "}"
)


#: Cell renderer for `url` attributes: renders the value as a link opening in a
#: new tab, keeps the inheritance markers of `INHERITED_CELL_RENDERER` and turns
#: image/video values into a clickable text (the `_media` marker map drives both
#: the styling here and the preview dialog in `handle_cell_clicked`, so Python
#: stays the single source of truth for "is this media?").
URL_CELL_RENDERER = (
    "(params) => {"
    " const data = params.data || {};"
    " const field = params.colDef.field;"
    " const text = params.value === null || params.value === undefined ? '' : String(params.value);"
    " const inherited = data._inherited && data._inherited[field];"
    " const interrupted = data._interrupted && data._interrupted[field];"
    " const marker = inherited || interrupted;"
    " const media = (data._media && data._media[field]) || '';"
    " const span = document.createElement('span');"
    " if (marker) {"
    "  const icon = document.createElement('i');"
    "  icon.className = 'material-icons';"
    "  icon.style.fontSize = '14px';"
    "  icon.style.verticalAlign = '-2px';"
    "  icon.style.marginRight = '2px';"
    "  icon.style.fontStyle = 'normal';"
    "  icon.style.color = inherited ? '#9e9e9e' : '#b45309';"
    "  icon.textContent = inherited ? 'swap_horiz' : 'link_off';"
    "  span.appendChild(icon);"
    "  span.title = marker;"
    " }"
    " if (text && /^https?:\\/\\//i.test(text)) {"
    "  const link = document.createElement(media ? 'span' : 'a');"
    "  link.textContent = text;"
    "  link.className = 'text-primary' + (media ? ' cursor-pointer' : '');"
    "  if (media) {"
    "   link.title = media;"
    "  } else {"
    "   link.href = text;"
    "   link.target = '_blank';"
    "   link.rel = 'noopener noreferrer';"
    "  }"
    "  span.appendChild(link);"
    " } else {"
    "  span.appendChild(document.createTextNode(text));"
    " }"
    " return span;"
    "}"
)


def media_markers(row: dict, url_fields: set[str], hint: str) -> dict[str, str]:
    """Marks the URL cells of a row whose value is an image or video.

    Returns the `_media` map (`{field: tooltip}`) consumed by the cell renderer
    and the page's click handler. The displayed value decides (the same string
    the user sees), so multilingual columns are covered as well.
    """
    return {field: hint for field in url_fields if media_kind(row.get(field))}


def inherited_cells(object_: dict, locale: str, inheritable_names: list[str] | None = None) -> dict:
    """Builds the per-row marker maps of the attribute fields.

    The core reports the origin of every inherited value in `inherited`
    (`{name: {from, label}}`); the grid marks those cells (dimmed + tooltip)
    via the `:cellStyle`/`:tooltipValueGetter`/`:cellRenderer` column options.
    An attribute that is **inheritable** but carries an own empty value
    (`null`/empty string/empty language map) is marked as *interrupted*: the
    key blocks inheritance. The tooltips are built here, where the locale and
    the translations are available.
    """
    markers: dict[str, str] = {}
    blocked: dict[str, str] = {}
    sources = object_.get("inherited")
    if isinstance(sources, dict):
        for name, source in sources.items():
            if not isinstance(source, dict):
                continue
            label = str(source.get("label") or source.get("from") or "")
            markers[attribute_field(str(name))] = t(
                "grid.inherited_cell", locale=locale, label=label
            )
    attributes = object_.get("attributes")
    if isinstance(attributes, dict):
        for name in inheritable_names or []:
            if attribute_field(name) in markers or name not in attributes:
                continue
            value = attributes.get(name)
            if value is None or value == "" or value == {}:
                blocked[attribute_field(name)] = t("grid.interrupted_cell", locale=locale)
    return {INHERITED_FIELD: markers, INTERRUPTED_FIELD: blocked}


def with_header_tooltips(column_defs: list[dict]) -> list[dict]:
    """Adds a tooltip with the full header text to every column.

    The grid is often too narrow to show a whole header, so hovering it reveals
    the complete label.
    """
    return [
        {**column, "headerTooltip": column["headerName"]} if column.get("headerName") else column
        for column in column_defs
    ]


def content_widths(column_defs: list[dict], rows: list[dict]) -> list[dict]:
    """Sizes every column to its content (capped at `MAX_CONTENT_CHARS`).

    Returns AG Grid's `setColumnWidths` payload (`[{key, newWidth}]`). The width
    is approximated from the longest header/cell text, so the result is a good
    fit instead of a pixel-exact measurement. Markup of HTML cells (status and
    completeness badges) is stripped first, otherwise the badge would claim the
    full character cap.
    """
    widths: list[dict] = []
    for column in column_defs:
        field = str(column.get("field") or "")
        texts = [_plain_text(column.get("headerName"))]
        texts.extend(_plain_text(row.get(field)) for row in rows)
        longest = min(max((len(text) for text in texts), default=0), MAX_CONTENT_CHARS)
        widths.append(
            {
                "key": field,
                "newWidth": max(MIN_COLUMN_PX, longest * CHAR_WIDTH_PX + CELL_PADDING_PX),
            }
        )
    return widths


def _plain_text(value: object) -> str:
    """Returns the visible text of a cell value (HTML markup removed)."""
    if value is None:
        return ""
    return re.sub(r"<[^>]*>", "", str(value))


def restore_columns(stored: object, known_fields: set[str]) -> set[str] | None:
    """Validates a stored column set against the columns that currently exist.

    Returns ``None`` when nothing usable was stored (then the caller keeps its
    default), otherwise the stored fields restricted to ``known_fields`` so a
    removed attribute cannot break the grid.
    """
    if not isinstance(stored, list | tuple | set):
        return None
    fields = {str(field) for field in stored if str(field) in known_fields}
    return fields or None


def language_field(prefix: str, language: str) -> str:
    """Returns the field name of a per-language column (`<prefix>__<lang>`)."""
    return f"{prefix}{LANG_SEPARATOR}{language}"


def present_attribute_names(objects: Iterable[Mapping[str, object]]) -> set[str]:
    """The attribute names the loaded objects actually carry a value for.

    Drives the standard columns: reference data is *read*, not chosen
    (`meadow/frontend/reference_data.py`), so a column of an imported standard is
    offered once data for it exists — and the rows then carry that cell.
    """
    names: set[str] = set()
    for object_ in objects:
        attributes = object_.get("attributes")
        if not isinstance(attributes, Mapping):
            continue
        for name in attributes:
            if has_value(attributes, str(name)):
                names.add(str(name))
    return names


def creatable_types(object_types: Iterable[Mapping[str, object]]) -> list[dict]:
    """The object types the product grid offers for a **new** object.

    System types (`root`, `category`, `collection`, `saved_search`, `asset`) are
    maintained on their own pages and taxonomy node types in the categories view —
    offering them here would file objects where nobody looks for them. `product`
    comes first (the usual case), the rest keeps the registry order.
    """
    offered = [
        dict(item)
        for item in object_types
        if item.get("name") and not item.get("system") and not item.get("taxonomy")
    ]
    return sorted(
        offered, key=lambda item: (str(item.get("name")) != "product", str(item.get("name")))
    )


def offered_definitions(
    definitions: Iterable[dict],
    *,
    visible_fields: Iterable[str],
    present_names: Iterable[str],
) -> list[dict]:
    """The definitions that are worth a grid column.

    Every **own** attribute gets a column (the grid is where its values are read
    and edited), while the vocabulary of an imported classification standard
    (17 377 features in the full ETIM standard) would become 17 377 columns and
    picker rows. A standard attribute is offered when objects on the page carry a
    value for it (then the column has data) or when it is part of the restored
    column set (so a saved personal view stays switchable). Own definitions keep
    their input order, the standard's follow.
    """
    visible = {str(field) for field in visible_fields}
    present = {str(name) for name in present_names}
    own: list[dict] = []
    extra: list[dict] = []
    for definition in definitions:
        name = str(definition.get("name") or "")
        if not name:
            continue
        if not definition.get("reference_data"):
            own.append(definition)
        elif name in present or attribute_field(name) in visible:
            extra.append(definition)
    return [*own, *extra]


def _language_column(header_name: str, field: str) -> dict:
    """Builds one hidden per-language column def."""
    return {"headerName": header_name, "field": field, "width": 140, "hide": True}


def _language_value(value: object, language: str, resolver: dict[str, str] | None) -> str:
    """Resolves an attribute value for exactly one language.

    Deliberately **without** the language-fallback of the working-language
    column: a language column exists to show whether that language is actually
    maintained, so an unset value stays empty instead of borrowing another
    language's value.
    """
    if not isinstance(value, dict):
        return ""
    raw = value.get(language)
    if raw is None:
        return ""
    return resolver.get(str(raw), str(raw)) if resolver is not None else str(raw)


def _display_value(
    value: object,
    locale: str,
    resolver: dict[str, str] | None,
) -> str:
    """Resolves a cell value to a display string, applying an enum resolver."""
    if value is None:
        return ""
    if isinstance(value, dict):
        code = str(value.get(locale) or next(iter(value.values()), "") or "")
        return resolver.get(code, code) if resolver is not None else resolve_value(value, locale)
    if resolver is not None:
        return resolver.get(str(value), str(value))
    return resolve_value(value, locale)


def row_data(
    object_: dict,
    locale: str,
    attribute_names: list[str] | None = None,
    enum_resolvers: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Maps a core object to an AG Grid row.

    With ``attribute_names``, one ``attr_<name>`` field per attribute is added
    (resolved to a display string for the working locale). ``enum_resolvers``
    maps each enum attribute name to a ``{code: label}`` map so stored codes are
    shown as their localized labels. The ``_completeness`` field mirrors the
    core's ``completeness`` value for the working locale.
    """
    attributes = object_.get("attributes") or {}
    row = {
        "_id": str(object_.get("id", "")),
        "_name": resolve_value(attributes.get("name"), locale),
        "_type": str(object_.get("type", "")),
        "_status": status_badge(str(object_.get("status", ""))),
    }
    completeness = object_.get("completeness") or {}
    if isinstance(completeness, dict):
        percent = completeness.get(locale)
        row["_completeness"] = completeness_badge(int(percent)) if percent is not None else ""
    else:
        row["_completeness"] = ""
    for name in attribute_names or []:
        value = attributes.get(name)
        row[f"attr_{name}"] = _display_value(value, locale, (enum_resolvers or {}).get(name))
    # Action cell: open the reference picker for this row.
    row["_refs"] = "🔗"
    return row


def attribute_field(attribute_name: str) -> str:
    """Returns the grid field name of an attribute's working-language value."""
    return f"attr_{attribute_name}"


#: Attribute types that can be edited inline in the grid (working language).
EDITABLE_TYPES = ("text", "url", "number", "boolean", "enum", "date")


def is_editable_definition(definition: dict) -> bool:
    """Whether an attribute may be edited inline in the grid.

    Read-only by design: **computed** values belong to a business rule,
    **quantified** values (a `unit_id`) need the unit-aware editor of the
    product form, and `reference`/media attributes need dedicated pickers.
    """
    return (
        str(definition.get("type") or "text") in EDITABLE_TYPES
        and not definition.get("computed")
        and not definition.get("unit_id")
    )


def _as_number(value: object) -> object:
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


def _as_bool(value: object) -> object:
    """Parses a cell value to a boolean (None stays None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "on", "yes")


def _is_multilingual_map(value: object) -> TypeGuard[dict[str, Any]]:
    """Whether a value is a `{language: value}` map (rather than quantified)."""
    return isinstance(value, dict) and "unit" not in value


def editable_cell_value(
    definition: dict, raw: object, locale: str, labels: dict[str, str] | None = None
) -> object:
    """Builds the **typed** working-language cell value of an editable attribute.

    The cell carries the native type (number, bool, string) so AG Grid offers the
    matching editor; enum cells carry the localized `label` (the stored code is
    restored on save by `storable_cell_value`).
    """
    atype = str(definition.get("type") or "text")
    value = raw.get(locale) if _is_multilingual_map(raw) else raw
    if value is None:
        return None
    if atype == "number":
        return _as_number(value)
    if atype == "range":
        return format_range(value) or None
    if atype == "boolean":
        return _as_bool(value)
    text = str(value)
    if atype == "enum" and labels:
        return labels.get(text, text)
    return text


def editable_cells(
    object_: dict,
    locale: str,
    editable: dict[str, dict] | None,
    enum_labels: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Builds the typed cells of the editable attributes (`attr_<name>`).

    An attribute that has no value in the working language is **left out**, so the
    row keeps the resolved display value from `row_data` (a value maintained in
    another language stays visible; editing it writes the working language).
    """
    attributes = object_.get("attributes") or {}
    cells: dict = {}
    for name, definition in (editable or {}).items():
        raw = attributes.get(name)
        if _is_multilingual_map(raw) and raw.get(locale) is None:
            continue
        cells[attribute_field(name)] = editable_cell_value(
            definition, raw, locale, (enum_labels or {}).get(name)
        )
    return cells


def storable_cell_value(
    definition: dict,
    cell_value: object,
    original: object,
    locale: str,
    labels: dict[str, str] | None = None,
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
        new_value = _as_number(cell_value)
    elif atype == "boolean":
        new_value = _as_bool(cell_value)
    else:
        new_value = None if cell_value in (None, "") else str(cell_value)
    if _is_multilingual_map(original):
        merged = dict(original)
        if new_value is None:
            merged.pop(locale, None)
        else:
            merged[locale] = new_value
        return merged
    return new_value


def _editor_props(definition: dict, labels: dict[str, str] | None) -> dict:
    """Returns the AG Grid editing props of a definition (empty when read-only)."""
    if not is_editable_definition(definition):
        return {}
    atype = str(definition.get("type") or "text")
    if atype == "number":
        return {"editable": True, "cellDataType": "number"}
    if atype == "boolean":
        return {"editable": True, "cellDataType": "boolean"}
    if atype == "date":
        # Values are stored as ISO strings; the date editor writes them back.
        return {"editable": True, "cellEditor": "agDateStringCellEditor"}
    if atype == "enum":
        # Without resolved options the select editor would be useless.
        if not labels:
            return {}
        return {
            "editable": True,
            "cellEditor": "agSelectCellEditor",
            "cellEditorParams": {"values": list(labels.values())},
        }
    return {"editable": True}


def attribute_column_specs(
    definitions: list[dict],
    locale: str,
    languages: list[str] | None = None,
    enum_labels: dict[str, dict[str, str]] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """Builds hidden AG Grid columns for attribute definitions.

    The header uses the definition's (multilingual) label, falling back to the
    attribute name. Columns are hidden by default so the grid stays clean until
    the user enables them via the column picker. For a **multilingual**
    attribute one extra column per language in ``languages`` is added (header
    `<label> · <LANG>`, field `attr_<name>__<lang>`).

    The **working-language** column of an editable attribute (`text`, `number`,
    `boolean`, `enum`) is `editable` and carries the matching editor (see
    `_editor_props`); the per-language columns stay read-only.

    Returns ``(columns, language_fields)``, where ``language_fields`` maps a
    per-language field back to its attribute name (used by the export).
    """
    columns: list[dict] = []
    language_fields: dict[str, str] = {}
    for definition in definitions:
        name = str(definition.get("name", ""))
        if not name:
            continue
        label = resolve_value(definition.get("label"), locale) or name
        column = {"headerName": label, "field": attribute_field(name), "width": 140, "hide": True}
        column.update(_editor_props(definition, (enum_labels or {}).get(name)))
        # `url` values are rendered as links (image/video cells open a preview).
        if str(definition.get("type") or "") == "url":
            column[":cellRenderer"] = URL_CELL_RENDERER
        columns.append(column)
        if not definition.get("multilingual"):
            continue
        for language in languages or []:
            field = language_field(f"attr_{name}", language)
            language_column = _language_column(f"{label} · {language.upper()}", field)
            if str(definition.get("type") or "") == "url":
                language_column[":cellRenderer"] = URL_CELL_RENDERER
            columns.append(language_column)
            language_fields[field] = name
    return columns, language_fields


def name_language_columns(
    name_label: str, languages: list[str] | None = None
) -> tuple[list[dict], dict[str, str]]:
    """Builds the hidden per-language columns of the convention `name`.

    The object's display name is multilingual by convention and is **not** an
    attribute definition, so its language columns are built separately. Returns
    ``(columns, language_fields)`` with ``language_fields`` mapping a field back
    to the `name` attribute (used by the export).
    """
    columns: list[dict] = []
    language_fields: dict[str, str] = {}
    for language in languages or []:
        field = language_field(NAME_FIELD_PREFIX, language)
        columns.append(_language_column(f"{name_label} · {language.upper()}", field))
        language_fields[field] = "name"
    return columns, language_fields


def attribute_columns(
    definitions: list[dict], locale: str, languages: list[str] | None = None
) -> list[dict]:
    """Builds the hidden AG Grid columns for the given attribute definitions."""
    return attribute_column_specs(definitions, locale, languages)[0]


def language_cells(
    object_: dict,
    languages: list[str] | None,
    language_prefixes: dict[str, str] | None,
    enum_resolvers: dict[str, dict[str, dict[str, str]]] | None = None,
) -> dict:
    """Builds the per-language cells (`<prefix>__<lang>`) of a grid row.

    ``language_prefixes`` maps an attribute name to the field prefix of its
    language columns (`name` → `_name`, every other attribute → `attr_<name>`).
    """
    attributes = object_.get("attributes") or {}
    prefixes = language_prefixes or {}
    cells: dict = {}
    for name in sorted(prefixes):
        value = attributes.get(name)
        for language in languages or []:
            resolver = (enum_resolvers or {}).get(language, {}).get(name)
            field = language_field(prefixes[name], language)
            cells[field] = _language_value(value, language, resolver)
    return cells


#: Grid field → export field name for the base columns.
_EXPORT_BASE_FIELDS = {"_id": "id", "_type": "type", "_status": "status", "_name": "name"}


def export_fields(
    column_defs: list[dict],
    visible_fields: set[str],
    language_fields: dict[str, str] | None = None,
) -> list[str]:
    """Maps the visible grid columns to the field names of an export.

    Per-language columns (`attr_<name>__<lang>`) map to their attribute name —
    the export expands multilingual attributes per requested locale — and the
    result is deduplicated, because a working-language column and its language
    columns denote the same attribute. Falls back to the base fields.
    """
    fields: list[str] = []
    mapping = language_fields or {}
    for column in column_defs:
        field = str(column.get("field") or "")
        if field not in visible_fields:
            continue
        if field in _EXPORT_BASE_FIELDS:
            fields.append(_EXPORT_BASE_FIELDS[field])
        elif mapping.get(field):
            fields.append(mapping[field])
        elif field.startswith("attr_"):
            fields.append(field[len("attr_") :])
    return list(dict.fromkeys(fields)) or ["id", "type", "status"]


def collect_subtree(node_id: str, children_by_id: dict[str, list[str]]) -> list[str]:
    """Returns the node id and all its descendants (breadth-first)."""
    result: list[str] = []
    queue = [node_id]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        result.append(current)
        queue.extend(children_by_id.get(current, []))
    return result


def enrich_tree_nodes(
    nodes: list[dict],
    objects_by_id: dict[str, dict],
    locale: str,
    root_id: str = "object:object_root",
    root_label: str = "",
) -> tuple[list[dict], dict[str, list[str]]]:
    """Adds labels to composition-tree nodes and returns the children map.

    Returns ``(nodes_with_labels, children_by_id)``. Node labels come from the
    object's resolved (multilingual) name; the root falls back to
    ``root_label`` when it has no name.
    """
    children_by_id: dict[str, list[str]] = {}

    def enrich(node: dict, is_root: bool) -> dict:
        node_id = str(node.get("id", ""))
        obj = objects_by_id.get(node_id, {})
        obj_type = str(obj.get("type", ""))
        label = resolve_value((obj.get("attributes") or {}).get("name"), locale)
        if not label:
            label = root_label if is_root else (obj_type or node_id)
        raw_children = node.get("children", [])
        children_by_id[node_id] = [str(c.get("id")) for c in raw_children if c.get("id")]
        return {
            "id": node_id,
            "label": label,
            "type": obj_type,
            "children": [enrich(c, False) for c in raw_children],
        }

    enriched = [enrich(n, n.get("id") == root_id) for n in nodes]
    return enriched, children_by_id


def filter_tree_nodes(nodes: list[dict], query: str) -> list[dict]:
    """Filters QTree nodes by label, keeping ancestors of matches.

    An empty query returns the nodes unchanged. A node is kept when its label
    contains the query (case-insensitive) or when a descendant matches; kept
    nodes carry only their matching descendants. A ``lazy`` node (children are
    loaded on demand) that has no matching children is kept **without** a
    ``children`` key so it stays expandable via ``@lazy-load``.
    """
    q = query.strip().lower()
    if not q:
        return nodes

    def keep(node: dict) -> dict | None:
        children = [keep(c) for c in node.get("children", [])]
        children = [c for c in children if c is not None]
        if q in str(node.get("label", "")).lower() or children:
            kept = dict(node)
            if children or not node.get("lazy"):
                kept["children"] = children
            return kept
        return None

    return [n for n in (keep(x) for x in nodes) if n is not None]


def tree_node_from_object(object_: dict, locale: str) -> dict:
    """Builds a QTree node dict from a child object, marking lazy nodes."""
    node_id = str(object_.get("id", ""))
    attributes = object_.get("attributes") or {}
    obj_type = str(object_.get("type", ""))
    # A taxonomy node (category, ETIM class) usually carries a `code` instead of
    # a `name` — without the fallback a list of 5640 classes would be labelled
    # with their object type only.
    code = attributes.get("code")
    label = (
        resolve_value(attributes.get("name"), locale)
        or (str(code) if code else "")
        or obj_type
        or node_id
    )
    node: dict = {"id": node_id, "label": label, "type": obj_type}
    if object_.get("hasChildren"):
        node["lazy"] = True
    if "memberCount" in object_:
        node["count"] = object_.get("memberCount")
    return node


def _saved_search_name(entry: dict) -> str:
    """Display name of a saved search (grid row or full object)."""
    attributes = entry.get("attributes") or {}
    name = entry.get("name") or attributes.get("name")
    return str(name or entry.get("id") or "")


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


async def _wait_export(client: CoreClient, token: str, run_id: str) -> dict:
    """Polls an export job run until it reaches a terminal state."""
    for _ in range(120):
        try:
            runs = await client.list_job_runs(token, ids=[run_id], limit=1)
        except CoreError, httpx.HTTPError:  # keep polling on a transient error
            runs = []
        if runs:
            status = str(runs[0].get("status") or "")
            if status in ("success", "failed", "retry"):
                return runs[0]
        await asyncio.sleep(1)
    return {"status": "failed", "error": "timeout"}


#: Mime type per export format, used for the browser download.
_EXPORT_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}


def _save_download(data: bytes, filename: str, format_: str) -> None:
    """Serves export bytes via a temp file (NiceGUI str/bytes src is unreliable)."""
    with tempfile.NamedTemporaryFile("wb", suffix="." + format_, delete=False) as handle:
        handle.write(data)
        tmp_path = handle.name
    ui.download(tmp_path, filename, _EXPORT_MIME.get(format_, "application/octet-stream"))
    asyncio.get_running_loop().call_later(60, os.remove, tmp_path)


@ui.page("/products", response_timeout=PAGE_TIMEOUT)
async def products_page(request: Request) -> None:
    """Lists objects in the main grid with inline editing + batch save."""
    await build_shell(active="/products")
    locale = current_locale()
    # Active grid filters: seeded from the URL (bookmarkable, e.g. the dashboard
    # shortcut `?incomplete=1&type=product`) and then driven by the filter bar.
    active: dict = {
        "incomplete": request.query_params.get("incomplete") == "1",
        "type": request.query_params.get("type") or None,
        "ancestor": request.query_params.get("ancestor") or None,
        "category": request.query_params.get("category") or None,
        # Category dimension: the category relation type used by the `category`
        # and `uncategorized` filters (empty = built-in primary taxonomy).
        "definition_id": request.query_params.get("definition_id") or None,
        "collection": request.query_params.get("collection") or None,
        "uncategorized": request.query_params.get("uncategorized") == "1",
        # Channel-specific releases: `?released_for=<code>` = released for that channel.
        "released_for": request.query_params.get("released_for") or None,
        "q": request.query_params.get("q") or None,
        # Advanced search (the condition dialog): a compact Query DSL document.
        "where": request.query_params.get("where") or None,
    }
    # Monotonic reload counter: a slow reload never overwrites a newer result.
    load_seq = {"value": 0}

    def has_filter() -> bool:
        """Whether any filter is active (a plain visit deliberately loads nothing)."""
        return bool(
            active["incomplete"]
            or active["type"]
            or active["ancestor"]
            or active["category"]
            or active["collection"]
            or active["uncategorized"]
            or active["released_for"]
            or active["q"]
            or active["where"]
        )

    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.products")).classes("text-h4")
        # Prompt shown when the grid is empty because no node/filter is active.
        selection_hint = ui.label(t("grid.select_tree_node")).classes(
            "text-subtitle1 text-gray-500 pb-2"
        )
        client = CoreClient(config.CORE_URL)
        token = app.storage.user.get("token", "")

        # Channel catalog (channel-specific releases): the filter bar, the release
        # column and the bulk wizard need it; without channels all three are hidden.
        try:
            channels = await client.list_channels(token, active=True)
        except CoreError, httpx.HTTPError:  # degrades to "no channels configured"
            channels = []
        channel_labels = channel_options(channels, locale)

        # Relation definitions (page-wide catalog): the category dimensions, the
        # reference picker and the bulk wizard all read from it.
        try:
            relation_definitions: list[dict] = await client.list_relation_definitions(token)
        except CoreError, httpx.HTTPError:  # degrade to "no custom relation types"
            relation_definitions = []
        # Taxonomy node types (the built-in `category` plus custom types flagged
        # `taxonomy: true`): they are the nodes of the category/classification
        # trees and the valid targets of a categorisation.
        try:
            object_types = await client.list_object_types(token, limit=1000)
        except CoreError, httpx.HTTPError:  # degrade to the built-in taxonomy
            object_types = []
        taxonomy_types = taxonomy_type_names(object_types)
        # The object types a **new** object may have on this page (system and
        # taxonomy types belong to their own pages) — `set_view` needs this to
        # show/hide the create action.
        create_types = creatable_types(object_types)

        async def fetch_filtered() -> list[dict]:
            """Loads the objects matching the active filters (see `active`)."""
            if not has_filter():
                return []
            if active["q"]:
                # Server-side full-text search (the grid may be empty otherwise):
                # search the labels, then load the hits as full grid rows. The
                # hits only supply the ids — the rows carry the completeness — so
                # the per-hit badge is skipped (`code_prefix` matches a partial
                # `code`, e.g. `EC0123`, which the full-text index cannot).
                hits = await client.picker_search(
                    token,
                    active["q"],
                    types=[active["type"]] if active["type"] else None,
                    locale=locale,
                    limit=SEARCH_LIMIT,
                    code_prefix=True,
                    completeness=False,
                )
                ids = [str(hit.get("id")) for hit in hits if hit.get("id")]
                if not ids:
                    return []
                return await client.list_objects(
                    token,
                    ids=ids,
                    missing_required=bool(active["incomplete"]),
                    where=active["where"],
                    locale=locale,
                    resolve_inheritance=True,
                    limit=SEARCH_LIMIT,
                )
            return await client.list_objects(
                token,
                object_type=active["type"],
                ancestor=active["ancestor"],
                category=active["category"],
                # The dimension only matters for the category filters; sending it
                # for other views would be validated but ignored by the core.
                definition_id=(
                    active["definition_id"]
                    if active["category"] or active["uncategorized"]
                    else None
                ),
                collection=active["collection"],
                uncategorized=bool(active["uncategorized"]),
                released_for=active["released_for"],
                releases=bool(channels),
                missing_required=bool(active["incomplete"]),
                where=active["where"],
                locale=locale,
                resolve_inheritance=True,
                limit=1000 if has_filter() else 100,
            )

        # On a plain /products visit we deliberately avoid loading every object:
        # the grid stays empty and the user picks a node in the tree or searches.
        # A filter (e.g. `?incomplete=1&type=product`, `?ancestor=…`, `?category=…`)
        # loads the targeted objects so shortcuts still work.
        objects: list[dict] = []
        try:
            objects = await fetch_filtered()
        except CoreError, httpx.HTTPError:  # keep the page usable (filter bar + grid)
            ui.notify(t("grid.load_failed"), color="negative")
        no_objects_label = ui.label(t("grid.no_objects")).classes("text-subtitle1")
        no_objects_label.set_visibility(has_filter() and not objects)
        selection_hint.set_visibility(not has_filter())

        try:
            # The **slim vocabulary** of *all* definitions, not the paged catalog
            # list: that one is capped at 1000 rows, and with an imported standard
            # those rows are the standard's features (`EF…`) — every own attribute
            # (and with it every own column) was missing here, while 1000 standard
            # columns buried the column picker. Cache-backed in the core, the same
            # source the catalog page and the export wizard use.
            definitions = await client.list_attribute_vocabulary(token)
        except CoreError, httpx.HTTPError:  # degrade to base columns on failure
            definitions = []
        # Attribute groups label the column picker (own catalogue plus the
        # standard's feature groups, which the standard's definitions carry).
        try:
            attribute_groups = await client.list_attribute_groups(token)
        except CoreError, httpx.HTTPError:  # degrade to ungrouped sections
            attribute_groups = []
        groups_by_id = {str(group.get("id")): group for group in attribute_groups}

        # Per-language columns (Sprach-Spalten): the working-language column
        # (`attr_<name>`) is joined by one hidden column per active data language
        # for every multilingual attribute. Only `active` languages are offered,
        # mirroring the product form.
        active_languages: list[str] = []
        language_labels: dict[str, str] = {}
        try:
            languages = await client.list_languages(token)
            for lang in languages:
                code = str(lang.get("code") or "")
                if not code or code == "root" or code == locale or not lang.get("active", True):
                    continue
                active_languages.append(code)
                language_labels[code] = resolve_value(lang.get("label"), locale) or code
        except CoreError, httpx.HTTPError:  # degrade to a single-language grid
            active_languages = []

        # The user's column set is resolved **before** the columns are built: a
        # restored set decides which standard columns have to exist at all (a
        # saved view that carries `EF…` columns stays switchable).
        views = app.storage.user.get("views", {})
        default_fields = {
            "_id",
            "_name",
            "_type",
            "_status",
            "_completeness",
            "_releases",
            "_refs",
        }
        candidate_fields = set(default_fields)
        for definition in definitions:
            name = str(definition.get("name") or "")
            if not name:
                continue
            candidate_fields.add(attribute_field(name))
            if definition.get("multilingual"):
                candidate_fields |= {
                    language_field(f"attr_{name}", language) for language in active_languages
                }
        candidate_fields |= {
            language_field(NAME_FIELD_PREFIX, language) for language in active_languages
        }
        # The **current** column set is persisted as well, so it survives a
        # browser-back from the product form, a reload and a re-login (only the
        # authentication is cleared on logout, see `auth.clear_session`).
        restored = restore_columns(app.storage.user.get(COLUMNS_STORAGE_KEY), candidate_fields)
        visible_fields: set[str] = restored or set(default_fields)
        #: Attribute names the loaded objects carry a value for. Reference data is
        #: *read*, not chosen: a standard column is offered once data exists.
        present_names = present_attribute_names(objects)
        #: Own catalogue plus the standard attributes that carry data (or are
        #: restored) — the vocabulary of an imported standard is 17 377 strong and
        #: must not become 17 377 columns and picker rows.
        active_definitions = offered_definitions(
            definitions, visible_fields=visible_fields, present_names=present_names
        )
        own_definitions = [
            definition for definition in active_definitions if not definition.get("reference_data")
        ]
        reference_definitions = [
            definition for definition in active_definitions if definition.get("reference_data")
        ]
        definitions_by_name = {
            str(definition.get("name")): definition
            for definition in active_definitions
            if definition.get("name")
        }
        #: Cells the rows carry: every own attribute (so a column enabled later is
        #: filled immediately) plus what the loaded objects store under names of
        #: their own (a standard's attribute with a value, e.g. an `EF…` feature).
        own_names = {name for name in (str(d.get("name")) for d in own_definitions) if name}
        cell_names = sorted(own_names) + sorted(present_names - own_names)
        multilingual_names = {
            str(definition.get("name"))
            for definition in active_definitions
            if definition.get("name") and definition.get("multilingual")
        }
        # Field prefix of the language columns per attribute (`name` is the
        # convention label attribute, all others are `attr_<name>` columns).
        language_prefixes = {
            "name": NAME_FIELD_PREFIX,
            **{name: f"attr_{name}" for name in multilingual_names},
        }
        # Attributes that can be edited inline (working language); computed,
        # quantified and date/reference attributes stay read-only (see
        # `is_editable_definition`). Reference data with a value stays editable —
        # a stored value never becomes read-only (`reference_data.visible_in_form`).
        editable_defs: dict[str, dict] = {
            str(definition.get("name")): definition
            for definition in active_definitions
            if definition.get("name") and is_editable_definition(definition)
        }
        # Attributes that participate in inheritance: an own empty value there
        # blocks the inherited one (marked as "interrupted" in the grid).
        inheritable_names = [
            str(definition.get("name"))
            for definition in active_definitions
            if definition.get("name")
            and definition.get("inheritable")
            and not definition.get("computed")
        ]
        # Fields the grid edits inline (`_name` is editable as well); a double
        # click there must open the cell editor instead of the product form.
        editable_fields = {"_name", *(attribute_field(name) for name in editable_defs)}

        # Resolve enum attribute values to localized labels via their enum sets.
        # Enum option labels are multilingual themselves, so every language
        # column resolves the stored code with its own locale.
        enum_resolvers: dict[str, dict[str, str]] = {}
        enum_resolvers_by_language: dict[str, dict[str, dict[str, str]]] = {}
        enum_defs = [d for d in active_definitions if d.get("type") == "enum"]
        if enum_defs:
            try:
                enum_sets = await client.list_enum_sets(token)
            except CoreError, httpx.HTTPError:  # degrade to raw codes on failure
                enum_sets = []
            from meadow.frontend.enums import enum_code_label_map

            def resolvers_for(enum_locale: str) -> dict[str, dict[str, str]]:
                """Builds the `{attribute: {code: label}}` map for one locale."""
                return {
                    str(defn.get("name")): mapping
                    for defn in enum_defs
                    if (mapping := enum_code_label_map(enum_sets, defn, enum_locale))
                }

            enum_resolvers = resolvers_for(locale)
            enum_resolvers_by_language = {
                language: resolvers_for(language) for language in active_languages
            }

        # Column order is fixed: the reference/action column (`_refs`, the link
        # icon that opens the reference picker) is always the **last** column, so
        # attribute columns (incl. the per-language ones) are appended *before*
        # it (pinned right so it stays visible however far the grid is scrolled).
        name_column_defs, name_language_fields = name_language_columns(
            t("grid.name"), active_languages
        )
        own_column_defs, own_language_fields = attribute_column_specs(
            own_definitions, locale, active_languages, enum_resolvers
        )
        reference_column_defs, reference_language_fields = attribute_column_specs(
            reference_definitions, locale, active_languages, enum_resolvers
        )
        attribute_column_defs = [*own_column_defs, *reference_column_defs]
        # The working-language attribute columns carry the inheritance marker
        # (dimmed + source tooltip) for values resolved from an ancestor. The
        # `url` renderer draws those markers itself, so it is not replaced.
        for column in attribute_column_defs:
            field = column.get("field")
            if isinstance(field, str) and LANG_SEPARATOR not in field:
                column[":cellStyle"] = INHERITED_CELL_STYLE
                column[":tooltipValueGetter"] = INHERITED_CELL_TOOLTIP
                column.setdefault(":cellRenderer", INHERITED_CELL_RENDERER)
        language_fields = {
            **name_language_fields,
            **own_language_fields,
            **reference_language_fields,
        }
        # Channel releases: one badge per channel (only with a channel catalog).
        release_columns = (
            [{"headerName": t("grid.releases"), "field": "_releases", "width": 180}]
            if channels
            else []
        )
        column_defs = with_header_tooltips(
            [
                {"headerName": t("grid.id"), "field": "_id", "width": 100, "pinned": "left"},
                {"headerName": t("grid.name"), "field": "_name", "editable": True},
                *name_column_defs,
                {"headerName": t("grid.type"), "field": "_type", "width": 120},
                {"headerName": t("grid.status"), "field": "_status", "width": 140},
                {"headerName": t("grid.completeness"), "field": "_completeness", "width": 140},
            ]
            + release_columns
            + attribute_column_defs
            + [
                {"headerName": "", "field": "_refs", "width": 60, "pinned": "right"},
            ]
        )

        original_by_id = {str(object_.get("id")): object_ for object_ in objects}
        # The rows exactly as they were loaded: staged values are compared against
        # these, so only real edits end up in the save patch.
        loaded_rows_by_id: dict[str, dict] = {}
        root_id = OBJECT_ROOT_ID
        tree_nodes: list[dict] = []

        async def load_children(node_id: str) -> list[dict]:
            # `scope=composition`: the taxonomy nodes (categories, classes) hang
            # under the same root but are the nodes of the category/classification
            # trees — they must not appear in the product structure.
            children = await client.list_object_children(
                token, node_id, limit=100, scope="composition"
            )
            return [tree_node_from_object(child, locale) for child in children]

        try:
            top_children = await client.list_object_children(
                token, root_id, limit=100, scope="composition"
            )
            tree_nodes = [tree_node_from_object(child, locale) for child in top_children]
        except CoreError, httpx.HTTPError:  # degrade to a grid without the tree
            tree_nodes = []

        # `url` attributes: their cells are links (media cells open a preview),
        # so the row carries a marker map for exactly the URL columns.
        url_names = {
            str(definition.get("name"))
            for definition in active_definitions
            if definition.get("name") and str(definition.get("type") or "") == "url"
        }
        url_fields = {attribute_field(name) for name in url_names} | {
            field for field, name in language_fields.items() if name in url_names
        }
        url_hint = t("url.preview_hint")

        def make_rows(selected_objects: list[dict]) -> list[dict]:
            """Builds the grid rows: display values, typed editable cells, languages."""
            rows = [
                row_data(object_, locale, cell_names, enum_resolvers)
                | editable_cells(object_, locale, editable_defs, enum_resolvers)
                | language_cells(
                    object_,
                    active_languages,
                    language_prefixes,
                    enum_resolvers_by_language,
                )
                | inherited_cells(object_, locale, inheritable_names)
                for object_ in selected_objects
            ]
            for row, object_ in zip(rows, selected_objects, strict=True):
                row[MEDIA_FIELD] = media_markers(row, url_fields, url_hint)
                row["_releases"] = release_badges(object_.get("releases"), channel_labels)
            return rows

        all_rows = make_rows(objects)
        loaded_rows_by_id = {str(row["_id"]): dict(row) for row in all_rows}

        def filters_query() -> str:
            """Builds the query string of the active filters."""
            parts = []
            if active["incomplete"]:
                parts.append("incomplete=1")
            if active["type"]:
                parts.append(f"type={quote(str(active['type']))}")
            if active["ancestor"]:
                parts.append(f"ancestor={quote(str(active['ancestor']))}")
            if active["category"]:
                parts.append(f"category={quote(str(active['category']))}")
                if active["definition_id"] and active["definition_id"] != PRIMARY_CATEGORY_DEF:
                    parts.append(f"definition_id={quote(str(active['definition_id']))}")
            if active["collection"]:
                parts.append(f"collection={quote(str(active['collection']))}")
            if active["released_for"]:
                parts.append(f"released_for={quote(str(active['released_for']))}")
            if active["uncategorized"]:
                parts.append("uncategorized=1")
                if active["definition_id"] and active["definition_id"] != PRIMARY_CATEGORY_DEF:
                    parts.append(f"definition_id={quote(str(active['definition_id']))}")
            if active["q"]:
                parts.append(f"q={quote(str(active['q']))}")
            if active["where"]:
                parts.append(f"where={quote(str(active['where']))}")
            return "&".join(parts)

        def current_url() -> str:
            """The URL of the current grid view (filters included)."""
            query = filters_query()
            return f"/products{'?' + query if query else ''}"

        def sync_url_query() -> None:
            """Keeps the URL in sync with the active filters (bookmarkable)."""
            ui.run_javascript(f"history.replaceState(null, '', '{current_url()}')")

        async def start_sequential() -> None:
            """Opens the selected rows one after another in the product form."""
            try:
                selected_rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                selected_rows = []
            ids = [oid for oid in (_row_id(row) for row in selected_rows) if oid]
            if not ids:
                ui.notify(t("seq.no_selection"), color="warning")
                return
            edit_queue.start_queue(ids, current_url())
            ui.navigate.to(f"/products/{quote(ids[0], safe='')}")

        async def apply_rows(selected_objects: list[dict]) -> None:
            nonlocal original_by_id, all_rows, loaded_rows_by_id
            original_by_id = {str(o.get("id")): o for o in selected_objects}
            all_rows = make_rows(selected_objects)
            loaded_rows_by_id = {str(row["_id"]): dict(row) for row in all_rows}
            grid.run_grid_method("setGridOption", "rowData", all_rows)
            selection_hint.set_visibility(len(selected_objects) == 0 and not has_filter())
            no_objects_label.set_visibility(len(selected_objects) == 0 and has_filter())

        async def reload_grid() -> None:
            """Re-fetches the grid rows for the active filters (no page reload)."""
            load_seq["value"] += 1
            seq = load_seq["value"]
            try:
                new_objects = await fetch_filtered()
            except CoreError, httpx.HTTPError:  # keep the current rows
                ui.notify(t("grid.load_failed"), color="negative")
                return
            if seq != load_seq["value"]:
                return  # a newer reload already applied its rows
            await apply_rows(new_objects)
            sync_url_query()

        async def handle_tree_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            active.update(ancestor=selected, category=None, collection=None, uncategorized=False)
            active_collection["id"] = None
            await reload_grid()

        # Category tree (left-panel view): aggregation-based navigation. Category
        # nodes are objects of type `category`; their hierarchy is `depends_on`
        # (so the generic LazyTree renders it), but selecting a node filters the
        # grid via `?category=<id>` (category_member aggregation), not `?ancestor=`.
        category_roots: list[dict] = []
        # Currently selected category tree root id (None = show all roots/trees).
        selected_tree: dict = {"id": None}
        # The taxonomy (node type) shown in the category view: the nodes of a
        # category/classification tree are objects of a `taxonomy: true` type and
        # hang under the same universal root as the products. One taxonomy at a
        # time — a catalogue with a classification standard installed has
        # thousands of nodes, and mixing every tree is neither readable nor cheap.
        #
        # The **dimension** decides it where it can: a categorisation with an
        # explicit `target_types` is bound to that taxonomy (the core rejects every
        # other target with `400`), so the tree follows it and the taxonomy switcher
        # is disabled. A generic dimension (empty `target_types`, e.g. the built-in
        # primary one which accepts every taxonomy type) leaves the switcher to the
        # user.
        initial_dimension = active["definition_id"] or PRIMARY_CATEGORY_DEF
        dimension_bindings = category_dimension_bindings(relation_definitions, taxonomy_types)
        taxonomy_state: dict = {
            "key": dimension_bindings.get(initial_dimension)
            or (taxonomy_types[0] if taxonomy_types else None)
        }
        # Special "Nicht kategorisiert" entry (lists objects with no category_member).
        uncategorized_id = "__uncategorized__"
        uncategorized_node = {"id": uncategorized_id, "label": t("grid.uncategorized")}

        async def load_category_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(
                token,
                node_id,
                limit=100,
                count_members=True,
                definition_id=active["definition_id"],
                scope="taxonomy",
                object_type=taxonomy_state["key"],
            )
            return [tree_node_from_object(child, locale) for child in children]

        async def load_category_roots(offset: int = 0) -> list[dict]:
            """Loads **one page** of the shown taxonomy's roots (+ member counts).

            A taxonomy can carry thousands of nodes (ETIM 10.0: 5640 classes), so
            the panel pages (`taxonomy_tree.PAGE_SIZE`) and offers a "load more"
            node plus a server-side search — instead of one truncated page.
            """
            try:
                top_children = await client.list_object_children(
                    token,
                    root_id,
                    limit=taxonomy_tree.PAGE_SIZE,
                    offset=offset,
                    count_members=True,
                    definition_id=active["definition_id"],
                    scope="taxonomy",
                    object_type=taxonomy_state["key"],
                )
            except CoreError, httpx.HTTPError:  # degrade to a structure-only panel
                return []
            return [tree_node_from_object(child, locale) for child in top_children]

        category_roots = await load_category_roots()
        #: Whether more roots of this taxonomy are unloaded (the tree then carries a
        #: "load more" node — the panel never truncates silently).
        roots_has_more = taxonomy_tree.has_more(len(category_roots))
        #: Server-side search hits shown instead of the tree (empty = show the tree).
        tree_override: list[dict] = []
        #: The query the current `tree_override` was loaded for.
        tree_query: dict = {"value": ""}
        #: Ancestor branch of a hit the user opened in the tree (empty = normal tree).
        tree_path: list[dict] = []

        async def load_more_roots() -> None:
            """Appends the next page of taxonomy roots (the "load more" node)."""
            nonlocal category_roots, roots_has_more
            page = await load_category_roots(offset=len(category_roots))
            category_roots = taxonomy_tree.append_page(category_roots, page)
            roots_has_more = taxonomy_tree.has_more(len(category_roots))
            refresh_tree_options()
            render_tree("category")

        # Category dimension: the category relation type whose members are counted
        # in the tree and filtered by `?category=`/`?uncategorized=`. Its binding (see
        # `dimension_bindings` above) decides the shown tree where it exists.
        dimension: dict = {"key": initial_dimension}
        dimensions = dimension_options(
            relation_definitions, locale, primary_label=t("categories.dimension_primary")
        )
        #: The taxonomy switcher of the category view (built later in this scope).
        taxonomy_select: ui.select | None = None

        def apply_dimension_taxonomy(key: str) -> None:
            """Follows a bound dimension: its taxonomy becomes the shown tree.

            Does not load anything — the caller reloads the roots, so a dimension
            switch costs one tree load and not two.
            """
            bound = dimension_bindings.get(key)
            if taxonomy_select is not None:
                taxonomy_select.set_enabled(not bound)
                taxonomy_select.props(f'hint="{t("grid.taxonomy_bound")}"' if bound else 'hint=""')
                if bound:
                    taxonomy_select.set_value(bound)
                taxonomy_select.update()
            if bound:
                taxonomy_state["key"] = bound

        async def change_dimension(value: str) -> None:
            """Switches the counted/filtered category dimension and reloads the view."""
            nonlocal category_roots, roots_has_more
            key = str(value or PRIMARY_CATEGORY_DEF)
            if key == dimension["key"]:
                return
            dimension["key"] = key
            active["definition_id"] = None if key == PRIMARY_CATEGORY_DEF else key
            apply_dimension_taxonomy(key)
            selected_tree["id"] = None
            tree_override.clear()
            tree_path.clear()
            tree_query["value"] = ""
            category_roots = await load_category_roots()
            roots_has_more = taxonomy_tree.has_more(len(category_roots))
            refresh_tree_options()
            render_tree("category")
            if active["category"] or active["uncategorized"]:
                await reload_grid()

        async def change_taxonomy(value: str) -> None:
            """Switches the shown taxonomy (node type) and loads its roots."""
            nonlocal category_roots, roots_has_more
            key = str(value or "")
            if not key or key == taxonomy_state["key"]:
                return
            taxonomy_state["key"] = key
            selected_tree["id"] = None
            tree_override.clear()
            tree_path.clear()
            tree_query["value"] = ""
            filter_input.set_value("")
            category_roots = await load_category_roots()
            roots_has_more = taxonomy_tree.has_more(len(category_roots))
            refresh_tree_options()
            render_tree("category")

        def refresh_tree_options() -> None:
            """Refills the root select of the category view (keeps "all trees")."""
            tree_select.options = {
                "all": t("grid.all_trees"),
                **{r["id"]: r["label"] for r in category_roots},
            }
            tree_select.value = "all"
            tree_select.update()

        async def select_category(selected: str | None) -> None:
            """Filters the grid by a tree node (also used by the search hit list)."""
            active.update(ancestor=None, category=None, collection=None, uncategorized=False)
            if selected == uncategorized_id:
                active["uncategorized"] = True
            elif selected:
                active["category"] = selected
            await reload_grid()

        async def handle_category_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            if selected == taxonomy_tree.MORE_ID:
                # The trailing node of a paged taxonomy: append the next page.
                await load_more_roots()
                return
            await select_category(selected)

        # Materialized collections (flat, no tree): a collection view filters the
        # grid via `?collection=<id>` (collection_member aggregation).
        collections: list[dict] = []
        try:
            collections = await client.list_collections(token, limit=1000)
        except CoreError, httpx.HTTPError:  # degrade to an empty collection list
            collections = []

        def _collection_label(c: dict) -> str:
            return (
                resolve_value((c.get("attributes") or {}).get("name"), locale)
                or str(c.get("name") or "")
                or str(c.get("id") or "")
            )

        def _collection_node(c: dict) -> dict:
            return {
                "id": str(c.get("id")),
                "label": _collection_label(c),
                "type": "collection",
                "count": int(c.get("member_count") or 0),
            }

        # Tracks the currently selected collection so leaving the collection view
        # can clear the `?collection=` filter and URL.
        active_collection: dict = {"id": None}

        async def handle_collection_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            active_collection["id"] = selected or None
            active.update(
                ancestor=None,
                category=None,
                uncategorized=False,
                collection=selected or None,
            )
            await reload_grid()

        # Staged edits: object id -> changed grid fields (`_name`, `attr_<name>`).
        staged_cells: dict[str, set[str]] = {}

        def _staged_count() -> int:
            return sum(len(fields) for fields in staged_cells.values())

        def mark_changed(event) -> None:
            args = event.args if isinstance(event.args, dict) else {}
            data = args.get("data")
            field = args.get("colId")
            oid = data.get("_id") if isinstance(data, dict) else None
            if oid and field:
                staged_cells.setdefault(str(oid), set()).add(str(field))
                staged_label.set_text(t("grid.staged_changes").format(count=_staged_count()))

        async def save() -> None:
            """Saves the staged cells as one batch update (attrs + name)."""
            client_rows = await grid.get_client_data()
            rows_by_id = {
                str(row.get("_id")): row for row in client_rows if row.get("_id") is not None
            }
            patches = []
            for oid, fields in staged_cells.items():
                original = original_by_id.get(oid)
                row = rows_by_id.get(oid)
                if original is None or row is None:
                    continue
                loaded = loaded_rows_by_id.get(oid, {})
                attributes: dict = {}
                if "_name" in fields and row.get("_name") != loaded.get("_name"):
                    patch = build_patch(oid, row.get("_name"), original, locale)
                    attributes.update(patch["attributes"])
                raw_attributes = original.get("attributes") or {}
                for field in sorted(fields):
                    if not str(field).startswith("attr_"):
                        continue  # `_name` is handled above
                    name = str(field)[len("attr_") :]
                    definition = editable_defs.get(name)
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
                    patches.append({"id": oid, "attributes": attributes})
            if not patches:
                ui.notify(t("grid.no_changes"))
                return
            try:
                results = await client.batch_update(token, patches)
            except CoreError, httpx.HTTPError:  # show a friendly message on failure
                ui.notify(t("grid.save_failed"), color="negative")
                return
            if is_session_expired(results):
                ui.notify(t("grid.session_expired"), color="warning")
                handle_session_expired()
                return
            ok = sum(1 for result in results if result.get("success"))
            ui.notify(t("grid.saved_count").format(ok=ok, total=len(results)))
            ui.navigate.reload()

        def discard() -> None:
            ui.navigate.reload()

        # --- Column picker state ---------------------------------------------
        # One source of truth: `visible_fields`. The grid, every picker checkbox
        # (an attribute may sit in several groups, so a field can have several
        # boxes), the group toggles and the language counters are mirrored from
        # it by `sync_picker()`. `syncing` suppresses the change handlers while
        # the picker is written from code — `element.value = x` **fires**
        # `on_value_change` in NiceGUI, which would call back into here.
        checkbox_refs: dict[str, list[ui.checkbox]] = {}
        group_boxes: dict[str, ui.checkbox] = {}
        #: Group id -> the column **fields** of that group (an attribute name may
        #: sit in several groups, its column stays one) and the field prefixes
        #: whose language columns the group toggle clears when it is switched off.
        group_fields: dict[str, list[str]] = {}
        group_prefixes: dict[str, list[str]] = {}
        language_counters: dict[str, ui.label] = {}
        #: Field prefix -> `{language: field}` of every attribute with language
        #: columns (`name` uses `_name`, attributes `attr_<name>`).
        language_fields_by_prefix = {
            prefix: {language: language_field(prefix, language) for language in active_languages}
            for prefix in language_prefixes.values()
        }
        syncing = {"value": False}

        def apply_visibility(fields: set[str]) -> None:
            for column in column_defs:
                grid.run_grid_method(
                    "setColumnsVisible", [column["field"]], column["field"] in fields
                )

        def remember_columns() -> None:
            """Persists the visible column set of the current user."""
            app.storage.user[COLUMNS_STORAGE_KEY] = sorted(visible_fields)

        def sync_picker() -> None:
            """Mirrors `visible_fields` into the picker (boxes, groups, counters).

            Called after every change, so a field that belongs to several groups
            stays consistent in all of them and a restored personal view shows up
            in the picker as well.
            """
            syncing["value"] = True
            try:
                for field, boxes in checkbox_refs.items():
                    state = field in visible_fields
                    for box in boxes:
                        if bool(box.value) != state:
                            box.value = state
                for group_id, box in group_boxes.items():
                    members = group_fields.get(group_id, [])
                    state = bool(members) and all(field in visible_fields for field in members)
                    if bool(box.value) != state:
                        box.value = state
                for prefix, counter in language_counters.items():
                    fields = language_fields_by_prefix.get(prefix, {})
                    counter.set_text(
                        t("grid.columns_languages_count").format(
                            on=sum(1 for field in fields.values() if field in visible_fields),
                            total=len(fields),
                        )
                    )
            finally:
                syncing["value"] = False

        def set_visibility(fields: set[str]) -> None:
            """Applies a column set, syncs the picker and persists it."""
            visible_fields.clear()
            visible_fields.update(fields)
            apply_visibility(visible_fields)
            sync_picker()
            remember_columns()

        def toggle_field(field: str, visible: bool) -> None:
            """Shows/hides one column and brings the whole picker in line."""
            if visible:
                visible_fields.add(field)
            else:
                visible_fields.discard(field)
            grid.run_grid_method("setColumnsVisible", [field], visible)
            sync_picker()
            remember_columns()

        def on_field_change(field: str, event) -> None:
            """Checkbox handler: ignored while the picker is written from code."""
            if syncing["value"]:
                return
            toggle_field(field, bool(event.value))

        def toggle_group(group_id: str, visible: bool) -> None:
            """Toggles a whole attribute group.

            Turning a group on enables the group's attributes (the working-language
            column each, the language columns stay a per-attribute choice); turning
            it off also clears those language columns, so a hidden group leaves no
            orphan language column behind.
            """
            for field in group_fields.get(group_id, []):
                toggle_field(field, visible)
            if not visible:
                for prefix in group_prefixes.get(group_id, []):
                    for language_column in language_fields_by_prefix.get(prefix, {}).values():
                        if language_column in visible_fields:
                            toggle_field(language_column, False)
            sync_picker()

        def fit_to_view() -> None:
            """Fits the columns into the available width."""
            grid.run_grid_method("sizeColumnsToFit")

        def fit_to_content() -> None:
            """Sizes every column to its content (capped at `MAX_CONTENT_CHARS`)."""
            grid.run_grid_method("setColumnWidths", content_widths(column_defs, all_rows))

        def save_view() -> None:
            name = None if view_input.value is None else view_input.value.strip()
            if not name:
                ui.notify(t("grid.view_name_required"), color="warning")
                return
            views[name] = sorted(visible_fields)
            app.storage.user["views"] = views
            view_input.value = ""
            refresh_views()
            ui.notify(t("grid.view_saved").format(name=name))

        def load_view(name: str) -> None:
            set_visibility(set(views.get(name, [])))
            ui.notify(t("grid.view_loaded").format(name=name))

        def delete_view(name: str) -> None:
            views.pop(name, None)
            app.storage.user["views"] = views
            refresh_views()

        def refresh_views() -> None:
            views_container.clear()
            for name in views:
                with views_container, ui.row().classes("items-center gap-1 w-full"):
                    ui.button(name, on_click=lambda n=name: load_view(n)).props("flat dense")
                    ui.button(icon="delete", on_click=lambda n=name: delete_view(n)).props(
                        "flat dense color=negative"
                    )

        with ui.dialog() as views_dialog, ui.card().classes("w-96"):
            ui.label(t("grid.views")).classes("text-h6")
            with ui.row().classes("items-center gap-2 w-full"):
                view_input = ui.input(t("grid.view_name")).props("outline dense").classes("grow")
                ui.button(t("grid.save_view"), icon="save", on_click=save_view).props(
                    "outline dense"
                )
            views_container = ui.column()
            refresh_views()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=views_dialog.close).props("flat dense")

        # Reference picker (modal): display + add/remove an object's references.
        relation_options: dict[str, str] = {}
        relation_def_by_key: dict[str, dict] = {}
        for definition in relation_definitions:
            if str(definition.get("kind")) != "aggregation":
                continue
            # The core's `create_reference` expects the definition's short
            # record key (e.g. `rel_category_member`, from the record id
            # `relation_definition:rel_category_member`), not the full id or
            # the display `name` (which differs, e.g. `category_member`).
            def_key = definition_key(definition)
            relation_def_by_key[def_key] = definition
            relation_options[def_key] = resolve_value(definition.get("label"), locale) or def_key

        # The bulk wizard assigns relations generically; types with a dedicated
        # operation (composition parent, categorisation) are hidden there to
        # avoid duplicates. Custom category types are offered by the category
        # operation instead.
        bulk_relation_options = {
            definition_key(definition): resolve_value(definition.get("label"), locale)
            or definition_key(definition)
            for definition in association_definitions(relation_definitions)
        }
        category_bulk_options = {
            definition_key(definition): resolve_value(definition.get("label"), locale)
            or definition_key(definition)
            for definition in category_definitions(relation_definitions)
        }

        current_ref_object: dict = {"id": None, "type": ""}

        def ref_target_types() -> list[str] | None:
            """Target types of the relation type currently chosen in the dialog."""
            definition = relation_def_by_key.get(str(ref_definition.value or ""))
            return effective_target_types(definition)

        ref_dialog = ui.dialog()
        with ref_dialog, ui.card().classes("w-[640px]"):
            ui.label(t("grid.references")).classes("text-h6")
            ref_object_label = ui.label().classes("text-caption text-gray-500")
            ref_chips = ui.column()
            with ui.row().classes("items-center gap-2 w-full"):
                ref_definition = (
                    ui.select(relation_options, value=None, label=t("grid.relation_type"))
                    .props("outline dense")
                    .classes("grow")
                )
            ref_search = ui.input(t("grid.reference_search")).props("outline dense").classes("grow")
            ui.button(
                icon="account_tree",
                on_click=lambda: open_object_picker(
                    client=client,
                    token=token,
                    locale=locale,
                    title=t("form.pick_target"),
                    on_pick=add_reference,
                    target_types=ref_target_types(),
                    exclude_ids=[str(current_ref_object["id"] or "")],
                ),
            ).props("outline dense").tooltip(t("form.pick_in_structure"))
            ref_hits = ui.column()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=ref_dialog.close).props("flat dense")

        async def remove_reference(reference_id: str) -> None:
            try:
                await client.delete_reference(token, reference_id)
            except CoreError, httpx.HTTPError:  # show a friendly message
                ui.notify(t("grid.ref_remove_failed"), color="negative")
            await refresh_references()

        async def refresh_references() -> None:
            ref_chips.clear()
            target = current_ref_object["id"]
            if not target:
                return
            try:
                references = await client.list_object_references(token, target, locale=locale)
            except CoreError, httpx.HTTPError:  # degrade to no chips
                references = []
            # Resolve relation-type labels for the group headings (all kinds,
            # including the composition `rel_depends_on`).
            def_labels: dict[str, str] = {}
            try:
                for definition in await client.list_relation_definitions(token):
                    dk = str(definition["id"]).split(":", 1)[-1]
                    def_labels[dk] = resolve_value(definition.get("label"), locale) or dk
            except CoreError, httpx.HTTPError:  # fall back to raw keys
                def_labels = {}
            # Group the object's references by relation type; the chip shows the
            # "other" object (the one *this* object points to / is pointed by).
            groups: dict[str, list[dict]] = {}
            for reference in references:
                dk = str(reference.get("definition_id") or "")
                other = (
                    reference.get("in_object")
                    if reference.get("out") == target
                    else reference.get("out_object")
                ) or {}
                groups.setdefault(dk, []).append(
                    {
                        "id": reference.get("id"),
                        "label": other.get("label") or "",
                        "type": other.get("type") or "",
                        "definition_id": dk,
                        "removable": dk != "rel_depends_on",
                    }
                )
            if not groups:
                with ref_chips, ui.column().classes("w-full"):
                    ui.label(t("grid.ref_empty")).classes("text-caption text-gray-500")
                return

            # Order groups: composition first, then aggregations by their label.
            def group_sort_key(dk: str) -> tuple:
                rank = 0 if dk == "rel_depends_on" else 1
                return (rank, def_labels.get(dk, dk).lower())

            for dk in sorted(groups, key=group_sort_key):
                label = def_labels.get(dk, dk)
                with ref_chips, ui.column().classes("w-full gap-1"):
                    ui.label(label).classes("text-caption font-medium text-gray-500")
                    for chip in sorted(groups[dk], key=lambda c: (c["label"].lower(), c["type"])):
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.label(f"{chip['label']} · {chip['type']}").classes(
                                "bg-neutral px-2 py-0.5 rounded text-xs"
                            )
                            if chip["removable"]:
                                ui.button(
                                    icon="close",
                                    on_click=lambda rid=chip["id"]: remove_reference(rid),
                                ).props("flat dense size=sm").tooltip(t("grid.ref_remove"))

        async def add_reference(hit: dict) -> None:
            definition_id = ref_definition.value
            if not definition_id:
                ui.notify(t("grid.ref_relation_required"), color="warning")
                return
            try:
                await client.create_reference(
                    token, current_ref_object["id"], hit["id"], definition_id
                )
            except CoreError, httpx.HTTPError:  # show a friendly message
                ui.notify(t("grid.ref_add_failed"), color="negative")
                return
            ref_search.value = ""
            ref_hits.clear()
            await refresh_references()

        async def search_references(value: str) -> None:
            q = (value or "").strip()
            ref_hits.clear()
            if not q or not current_ref_object["id"]:
                return
            try:
                hits = await client.picker_search(
                    token,
                    q,
                    types=ref_target_types(),
                    locale=locale,
                    limit=20,
                    # A partial `code` must find the target too; the hits keep the
                    # completeness badge.
                    code_prefix=True,
                )
            except CoreError, httpx.HTTPError:  # degrade to no hits
                return
            own_id = str(current_ref_object["id"])
            for hit in hits:
                if str(hit.get("id")) == own_id:
                    continue
                with ref_hits:
                    render_picker_hit(hit, add_reference, locale)

        async def open_references(object_id: str, object_type: str = "") -> None:
            """Opens the reference dialog for one row: only usable types are offered."""
            current_ref_object["id"] = object_id
            current_ref_object["type"] = object_type
            ref_object_label.set_text(object_id)
            # Relation types whose `source_types` do not accept this object would
            # be rejected by the core (400), so they are not offered at all.
            usable = usable_definitions(
                [relation_def_by_key[key] for key in relation_options], object_type
            )
            if usable:
                options = {
                    definition_key(definition): relation_options[definition_key(definition)]
                    for definition in usable
                }
                ref_definition.set_options(options)
                ref_definition.value = next(iter(options))
            else:
                ref_definition.set_options(relation_options)
                ref_definition.value = None
            ref_search.value = ""
            ref_hits.clear()
            await refresh_references()
            ref_dialog.open()

        async def handle_cell_clicked(e) -> None:
            """Cell clicks with a side effect.

            The `_refs` column opens the reference picker. A `url` cell that
            holds an image or video opens the preview dialog — every other URL
            cell is a real link and opens itself in a new tab, so the click
            needs no server round-trip.
            """
            args = e.args if isinstance(e.args, dict) else {}
            col_id = str(args.get("colId") or "")
            data = args.get("data") or {}
            if col_id == "_refs":
                if data and data.get("_id"):
                    await open_references(data["_id"], str(data.get("_type") or ""))
                return
            if (data.get(MEDIA_FIELD) or {}).get(col_id):
                open_media_dialog(data.get(col_id))

        def handle_open_cell(e) -> None:
            """Opens the product form on a row double-click.

            Editable cells are the exception: AG Grid starts the inline editor
            there, so the double-click must not navigate away.
            """
            args = e.args if isinstance(e.args, dict) else {}
            col_id = str(args.get("colId") or "")
            if col_id == "_refs" or col_id in editable_fields:
                return
            data = args.get("data")
            if data and data.get("_id"):
                ui.navigate.to(f"/products/{quote(str(data['_id']), safe='')}")

        ref_search.on_value_change(lambda e: search_references(e.value))

        # --- Bulk-Edit-Wizard (Phase 7) -------------------------------------
        bulk_selected_ids: set[str] = set()
        bulk_selected_label: ui.label | None = None
        bulk_scope: ui.select | None = None
        replace_scope: ui.select | None = None
        bulk_dialog: ui.dialog | None = None

        def _row_id(row: object) -> str | None:
            """Extract the object id from a selected row (data dict or row node)."""
            if not isinstance(row, dict):
                return None
            oid = row.get("_id")
            if not oid:
                data = row.get("data")
                if isinstance(data, dict):
                    oid = data.get("_id")
            return str(oid) if oid else None

        async def handle_selection(event) -> None:
            try:
                selected_rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                selected_rows = []
            bulk_selected_ids.clear()
            for row in selected_rows:
                oid = _row_id(row)
                if oid:
                    bulk_selected_ids.add(oid)
            if bulk_selected_label is not None:
                bulk_selected_label.set_text(
                    t("grid.bulk_selected").format(count=len(bulk_selected_ids))
                )
            if bulk_scope is not None:
                bulk_scope.set_value("selected" if bulk_selected_ids else "filtered")
            if replace_scope is not None:
                replace_scope.set_value("selected" if bulk_selected_ids else "filtered")

        # Bulk-wizard target selection uses the shared object picker (search +
        # structure tree); the chosen object per field key lives in `bulk_picked`.
        bulk_picked: dict[str, dict] = {}

        # Target selection for the bulk wizard: the own attributes only — an
        # imported standard's vocabulary is not a maintenance target
        # (`meadow/frontend/reference_data.py`).
        bulk_attr_options = {
            str(definition.get("name")): str(definition.get("name"))
            for definition in definitions
            if definition.get("name") and visible_in_picker(definition)
        }
        bulk_status_options = {
            "draft": "draft",
            "in_review": "in_review",
            "published": "published",
            "archived": "archived",
        }
        bulk_fields: dict[str, ui.input | ui.select | ui.number | ui.switch] = {}

        bulk_dialog = ui.dialog()
        with bulk_dialog, ui.card().classes("w-[44rem] max-w-[94vw] max-h-[85vh] overflow-y-auto"):
            ui.label(t("grid.bulk_title")).classes("text-h6")
            ui.label(t("grid.bulk_selection")).classes("text-caption text-gray-600")
            bulk_scope = (
                ui.select(
                    {
                        "filtered": t("grid.bulk_scope_filtered").format(count=len(all_rows)),
                        "page": t("grid.bulk_scope_page"),
                        "selected": t("grid.bulk_scope_selected"),
                    },
                    value="selected" if bulk_selected_ids else "filtered",
                )
                .props("filled dense")
                .classes("w-full")
            )
            bulk_selected_label = ui.label("").classes("text-caption")
            ui.label(t("grid.bulk_operation")).classes("text-caption text-gray-600")

            async def _on_bulk_op_change(_event) -> None:
                """Re-renders the wizard config area, loading pickers lazily."""
                await refresh_config()

            bulk_op = (
                ui.select(
                    {
                        "set_attribute": t("grid.op_set_attribute"),
                        "clear_attribute": t("grid.op_clear_attribute"),
                        "change_status": t("grid.op_change_status"),
                        "send_for_approval": t("grid.op_send_for_approval"),
                        "add_category": t("grid.op_add_category"),
                        "remove_category": t("grid.op_remove_category"),
                        "add_relation": t("grid.op_add_relation"),
                        "remove_relation": t("grid.op_remove_relation"),
                        "change_parent": t("grid.op_change_parent"),
                        "release_for_channel": t("grid.op_release_for_channel"),
                        "withdraw_from_channel": t("grid.op_withdraw_from_channel"),
                        "delete": t("grid.op_delete"),
                    },
                    value=None,
                    on_change=_on_bulk_op_change,
                )
                .props("filled dense")
                .classes("w-full")
            )
            ui.label(t("grid.bulk_areas_hint")).classes("text-caption text-gray-400")
            config_area = ui.column().classes("w-full mt-2 gap-2")

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=bulk_dialog.close).props("flat")
                ui.button(
                    t("grid.bulk_start"), icon="play_arrow", on_click=lambda: start_bulk()
                ).props("flat")

        async def refresh_config(_event=None) -> None:  # noqa: ARG001 - handler arg unused
            config_area.clear()
            bulk_fields.clear()
            op = bulk_op.value or ""
            with config_area:
                if op in ("set_attribute", "clear_attribute"):
                    select = (
                        ui.select(bulk_attr_options, label=t("grid.bulk_attribute"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["attribute"] = select
                    if op == "set_attribute":
                        value = (
                            ui.input(label=t("grid.bulk_value"))
                            .props("filled dense")
                            .classes("w-full")
                        )
                        bulk_fields["value"] = value
                elif op == "change_status":
                    select = (
                        ui.select(
                            bulk_status_options, value=None, label=t("grid.bulk_status_label")
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["status"] = select
                elif op == "send_for_approval":
                    select = (
                        ui.select(
                            bulk_status_options,
                            value=APPROVAL_STATUS,
                            label=t("grid.approval_status"),
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["approval_status"] = select
                elif op in ("add_category", "remove_category", "change_parent"):
                    field_key = "category" if op != "change_parent" else "parent"
                    label_key = (
                        "grid.bulk_parent" if op == "change_parent" else "grid.bulk_category"
                    )
                    target_types = taxonomy_types if op != "change_parent" else None

                    async def choose_target(field_key=field_key, target_types=target_types):
                        async def on_pick(hit: dict) -> None:
                            bulk_picked[field_key] = hit
                            label.set_text(f"{hit.get('label')} · {hit.get('type')}")

                        await open_object_picker(
                            client=client,
                            token=token,
                            locale=locale,
                            title=t("form.pick_target"),
                            on_pick=on_pick,
                            target_types=target_types,
                        )

                    with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                        label = ui.label(t("grid.bulk_nothing_picked")).classes(
                            "grow text-caption text-gray-500"
                        )
                        ui.button(icon="account_tree", on_click=choose_target).props(
                            "outline dense"
                        ).tooltip(t("grid.bulk_pick_label").format(label=t(label_key)))
                    if op in ("add_category", "remove_category") and len(category_bulk_options) > 1:
                        # Several categorisations may exist; the primary type is
                        # preselected so the common case stays a single click.
                        category_type = (
                            ui.select(
                                category_bulk_options,
                                value=(
                                    PRIMARY_CATEGORY_DEF
                                    if PRIMARY_CATEGORY_DEF in category_bulk_options
                                    else next(iter(category_bulk_options))
                                ),
                                label=t("grid.bulk_category_type"),
                            )
                            .props("filled dense")
                            .classes("w-full")
                        )
                        bulk_fields["category_definition"] = category_type
                        ui.label(t("grid.bulk_category_type_hint")).classes(
                            "text-caption text-gray-400"
                        )
                elif op in ("add_relation", "remove_relation"):
                    relation = (
                        ui.select(
                            bulk_relation_options,
                            value=None,
                            label=t("grid.bulk_relation"),
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["relation"] = relation

                    async def choose_relation_target():
                        """Picks the target object the relation points to."""
                        definition = relation_def_by_key.get(str(relation.value or "")) or {}

                        async def on_pick(hit: dict) -> None:
                            bulk_picked["relation_target"] = hit
                            relation_label.set_text(f"{hit.get('label')} · {hit.get('type')}")

                        await open_object_picker(
                            client=client,
                            token=token,
                            locale=locale,
                            title=t("form.pick_target"),
                            on_pick=on_pick,
                            target_types=effective_target_types(definition),
                        )

                    def reset_relation_target() -> None:
                        """A different relation type needs a new target object."""
                        bulk_picked.pop("relation_target", None)
                        relation_label.set_text(t("grid.bulk_nothing_picked"))

                    relation.on_value_change(lambda _event: reset_relation_target())
                    with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                        relation_label = ui.label(t("grid.bulk_nothing_picked")).classes(
                            "grow text-caption text-gray-500"
                        )
                        ui.button(icon="account_tree", on_click=choose_relation_target).props(
                            "outline dense"
                        ).tooltip(
                            t("grid.bulk_pick_label").format(label=t("grid.bulk_relation_target"))
                        )
                    ui.label(t("grid.bulk_relation_hint")).classes("text-caption text-gray-400")
                elif op == "delete":
                    ui.label(t("grid.bulk_delete_warn")).classes("text-caption text-negative")
                elif op in ("release_for_channel", "withdraw_from_channel"):
                    select = (
                        ui.select(channel_labels, value=None, label=t("grid.bulk_channel"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    bulk_fields["channel"] = select
                    if op == "release_for_channel":
                        status_select = (
                            ui.select(
                                bulk_status_options,
                                value=DEFAULT_RELEASE_STATUS,
                                label=t("grid.release_status"),
                            )
                            .props("filled dense")
                            .classes("w-full")
                        )
                        bulk_fields["release_status"] = status_select
                    ui.label(t("grid.bulk_channel_hint")).classes("text-caption text-gray-400")
                if op in _APPROVAL_MODIFIER_OPS:
                    bulk_fields["send_for_approval"] = ui.switch(
                        t("grid.bulk_send_for_approval")
                    ).tooltip(f"{t('grid.approval_status')}: {APPROVAL_STATUS}")

        async def _current_page_ids() -> list[str]:
            """Return the object ids of the rows on the current grid page."""
            try:
                page = int(await grid.run_grid_method("paginationGetCurrentPage") or 0)
                size = int(await grid.run_grid_method("paginationGetPageSize") or 50)
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                page, size = 0, 50
            start = page * size
            end = start + size
            return [oid for oid in (_row_id(r) for r in all_rows[start:end]) if oid]

        async def _scope_ids(scope: str) -> list[str]:
            """Resolves a selection scope (`filtered`/`page`/`selected`) to ids."""
            if scope == "selected":
                rows: list = await grid.get_selected_rows()
            elif scope == "page":
                return await _current_page_ids()
            else:
                rows = all_rows
            return [oid for oid in (_row_id(row) for row in rows) if oid]

        async def start_bulk() -> None:
            op = bulk_op.value
            if not op:
                ui.notify(t("grid.bulk_op_required"), color="warning")
                return
            ids = await _scope_ids(str(bulk_scope.value or "filtered"))
            if not ids:
                ui.notify(t("grid.bulk_no_objects"), color="warning")
                return
            parameters: dict = {}
            if op in ("set_attribute", "clear_attribute"):
                attribute = bulk_fields.get("attribute")
                parameters["attribute"] = attribute.value if attribute else None
                if op == "set_attribute":
                    value = bulk_fields.get("value")
                    parameters["value"] = value.value if value else None
            elif op == "change_status":
                status = bulk_fields.get("status")
                parameters["status"] = status.value if status else None
            elif op == "send_for_approval":
                target = bulk_fields.get("approval_status")
                parameters["approval_status"] = target.value if target else None
            elif op in ("add_category", "remove_category"):
                category = bulk_picked.get("category")
                parameters["category"] = category.get("id") if category else None
                category_type = bulk_fields.get("category_definition")
                if category_type is not None and category_type.value:
                    parameters["definition_id"] = str(category_type.value)
            elif op == "change_parent":
                parent = bulk_picked.get("parent")
                parameters["parent"] = parent.get("id") if parent else None
            elif op in ("add_relation", "remove_relation"):
                relation = bulk_fields.get("relation")
                parameters["definition_id"] = relation.value if relation else None
                picked_target = bulk_picked.get("relation_target")
                parameters["target"] = picked_target.get("id") if picked_target else None
            elif op in ("release_for_channel", "withdraw_from_channel"):
                channel = bulk_fields.get("channel")
                parameters["channel"] = channel.value if channel else None
                if op == "release_for_channel":
                    status_select = bulk_fields.get("release_status")
                    if status_select is not None and status_select.value:
                        parameters["release_status"] = str(status_select.value)

            if op in _APPROVAL_MODIFIER_OPS:
                approval = bulk_fields.get("send_for_approval")
                if approval is not None and approval.value:
                    parameters["send_for_approval"] = True

            missing = [k for k, v in parameters.items() if not v]
            if missing:
                ui.notify(t("grid.bulk_config_incomplete"), color="warning")
                return
            try:
                run = await client.post_bulk(token, op=op, object_ids=ids, parameters=parameters)
            except CoreError, httpx.HTTPError:  # show a friendly message
                ui.notify(t("grid.bulk_start_failed"), color="negative")
                return
            run_id = str(run.get("id") or "")
            bulk_dialog.close()
            ui.notify(t("grid.bulk_started"), color="positive")
            if run_id:
                await poll_bulk(run_id)

        async def poll_bulk(run_id: str) -> None:
            with ui.dialog() as progress_dialog, ui.card().classes("w-[32rem]"):
                ui.label(t("grid.bulk_running")).classes("text-subtitle1")
                status_label = ui.label("").classes("text-caption")
                pending_hint = ui.label("").classes("text-caption text-warning")
                ui.linear_progress(value=0.5).classes("w-full")
                with ui.row().classes("w-full justify-end"):
                    ui.button(t("grid.close"), on_click=progress_dialog.close).props("flat")
            progress_dialog.open()
            runs: list[dict] = []
            pending_polls = 0
            for _ in range(120):
                try:
                    runs = await client.list_job_runs(token, ids=[run_id], limit=1)
                except CoreError, httpx.HTTPError:  # retry on transient errors
                    runs = []
                if not runs:
                    break
                status = str(runs[0].get("status") or "")
                status_label.set_text(t("grid.bulk_status").format(status=status))
                # A queued job is only picked up by a running worker; without one
                # it would stay `pending` forever, so say so explicitly.
                pending_polls = pending_polls + 1 if status == "pending" else 0
                if pending_polls == PENDING_WARN_POLLS:
                    pending_hint.set_text(t("grid.bulk_pending_hint"))
                if status in ("success", "failed"):
                    break
                await asyncio.sleep(2)
            progress_dialog.close()
            if runs and runs[0].get("status") == "success":
                _show_bulk_report(runs[0].get("result") or {})
            else:
                ui.notify(t("grid.bulk_failed"), color="negative")

        def _show_bulk_report(result: dict) -> None:
            processed = str(result.get("processed", 0))
            succeeded = str(result.get("succeeded", 0))
            failed = str(result.get("failed", 0))
            with (
                ui.dialog() as report_dialog,
                ui.card().classes("w-[32rem] max-h-[60vh] overflow-y-auto"),
            ):
                ui.label(t("grid.bulk_report")).classes("text-subtitle1")
                ui.label(
                    t("grid.bulk_report_summary").format(
                        processed=processed, succeeded=succeeded, failed=failed
                    )
                ).classes("text-caption")
                if result.get("op") == "replace_text":
                    ui.label(
                        t("grid.replace_summary").format(
                            replacements=result.get("replacements", 0),
                            skipped=result.get("skipped", 0),
                        )
                    ).classes("text-caption")
                if result.get("op") in ("add_relation", "remove_relation"):
                    ui.label(
                        t("grid.relation_summary").format(
                            relations=result.get("relations", 0),
                            skipped=result.get("skipped", 0),
                        )
                    ).classes("text-caption")
                errors = [r for r in result.get("results", []) if r.get("status") == "error"]
                if errors:
                    with ui.column().classes("w-full mt-2 gap-1"):
                        for r in errors:
                            ui.label(f"{r.get('object_id')} · {r.get('error')}").classes(
                                "text-caption text-negative"
                            )
                blocked = [r for r in result.get("results", []) if r.get("approval_error")]
                if blocked:
                    # The data change is saved; only the workflow transition was
                    # rejected (e.g. by a `workflow_guard` rule).
                    with ui.column().classes("w-full mt-2 gap-1"):
                        ui.label(
                            t("grid.bulk_approval_blocked").format(count=len(blocked))
                        ).classes("text-caption text-warning")
                        for r in blocked:
                            ui.label(f"{r.get('object_id')} · {r.get('approval_error')}").classes(
                                "text-caption text-warning"
                            )
                with ui.row().classes("w-full justify-end mt-2"):
                    ui.button(t("grid.close"), on_click=report_dialog.close).props("flat")
            report_dialog.open()

        # --- Find & Replace (Phase 9) ---------------------------------------
        # Replaces a literal text in the string-valued attributes (`text`, `url`)
        # of the selection. It runs as the `replace_text` bulk operation (same
        # scope/progress/report machinery as the wizard) so large selections are
        # processed in the worker instead of blocking the page.
        replace_attr_options: dict[str, str] = {"": t("grid.replace_all_attributes")}
        replace_attr_options["name"] = t("grid.name")
        for definition in definitions:
            name = str(definition.get("name") or "")
            if not name or definition.get("computed"):
                continue
            if str(definition.get("type") or "") not in ("text", "url"):
                continue
            if not visible_in_picker(definition):
                continue
            label = str(resolve_value(definition.get("label"), locale) or name)
            replace_attr_options[name] = label if label == name else f"{label} ({name})"

        replace_dialog = ui.dialog()
        with replace_dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
            ui.label(t("grid.replace_title")).classes("text-h6")
            ui.label(t("grid.replace_hint")).classes("text-caption text-gray-600")
            ui.label(t("grid.bulk_selection")).classes("text-caption text-gray-600")
            replace_scope = (
                ui.select(
                    {
                        "filtered": t("grid.bulk_scope_filtered").format(count=len(all_rows)),
                        "page": t("grid.bulk_scope_page"),
                        "selected": t("grid.bulk_scope_selected"),
                    },
                    value="selected" if bulk_selected_ids else "filtered",
                )
                .props("filled dense")
                .classes("w-full")
            )
            replace_attribute = (
                ui.select(replace_attr_options, value="", label=t("grid.bulk_attribute"))
                .props("filled dense")
                .classes("w-full")
            )
            replace_search = (
                ui.input(label=t("grid.replace_search")).props("filled dense").classes("w-full")
            )
            replace_with = (
                ui.input(label=t("grid.replace_with")).props("filled dense").classes("w-full")
            )
            replace_case = ui.switch(t("grid.replace_case_sensitive"))
            replace_approval = ui.switch(t("grid.bulk_send_for_approval")).tooltip(
                f"{t('grid.approval_status')}: {APPROVAL_STATUS}"
            )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=replace_dialog.close).props("flat")
                ui.button(
                    t("grid.replace_start"),
                    icon="find_replace",
                    on_click=lambda: start_replace(),
                ).props("flat")

        async def start_replace() -> None:
            search = replace_search.value or ""
            if not search:
                ui.notify(t("grid.replace_search_required"), color="warning")
                return
            ids = await _scope_ids(str(replace_scope.value or "filtered"))
            if not ids:
                ui.notify(t("grid.bulk_no_objects"), color="warning")
                return
            parameters = {
                "search": search,
                "replace": replace_with.value or "",
                "attribute": str(replace_attribute.value or ""),
                "case_sensitive": bool(replace_case.value),
            }
            if replace_approval.value:
                parameters["send_for_approval"] = True
            try:
                run = await client.post_bulk(
                    token, op="replace_text", object_ids=ids, parameters=parameters
                )
            except CoreError, httpx.HTTPError:  # show a friendly message
                ui.notify(t("grid.bulk_start_failed"), color="negative")
                return
            run_id = str(run.get("id") or "")
            replace_dialog.close()
            ui.notify(t("grid.bulk_started"), color="positive")
            if run_id:
                await poll_bulk(run_id)

        current_tree: dict = {"tree": None, "nodes": []}
        # Tracks the active left-panel view so the action button (collapse vs.
        # collection reload) and the `?collection=` reset are correct.
        active_view: dict = {"value": "structure"}

        def collapse_all() -> None:
            if current_tree["tree"] is not None:
                current_tree["tree"].collapse()

        async def search_taxonomy(query: str) -> None:
            """Loads the server-side search hits of the shown taxonomy.

            `GET /objects/picker` searches the whole object set, so a class is found
            by its code **or** description even when it sits far beyond the loaded
            page: the full-text index matches whole tokens (a description word, a
            complete code), `code_prefix=true` adds the partial code (`EC0123`).
            `completeness=false` skips the badge — it costs ~0.3 s **per hit** in
            the core and the tree shows no badge. The label comes from the core (it
            falls back to `code`).
            """
            types = [taxonomy_state["key"]] if taxonomy_state["key"] else None
            try:
                hits = await client.picker_search(
                    token,
                    query,
                    types=types,
                    locale=locale,
                    limit=SEARCH_LIMIT,
                    code_prefix=True,
                    completeness=False,
                )
            except CoreError, httpx.HTTPError:  # keep the tree on failure
                ui.notify(t("grid.taxonomy_search_failed"), color="negative")
                return
            tree_override[:] = taxonomy_tree.search_nodes(hits)
            tree_path.clear()
            tree_query["value"] = query
            render_tree("category")

        async def show_in_tree(node_id: str) -> None:
            """Shows a search hit in its hierarchy (`GET .../ancestors`).

            A hit can sit below a class that is far beyond the loaded page (or
            outside the shown taxonomy) — a flat hit list cannot say where it
            belongs. The tree renders the hit's ancestor branch then, expanded down
            to the node; clearing the filter brings the normal tree back.
            """
            try:
                path = await client.list_object_ancestors(token, node_id)
            except CoreError, httpx.HTTPError:  # keep the hit list on failure
                ui.notify(t("grid.taxonomy_path_failed"), color="negative")
                return
            nodes = [tree_node_from_object(node, locale) for node in path]
            tree_path[:] = taxonomy_tree.path_nodes(nodes)
            render_tree("category")

        async def apply_tree_filter(value: str) -> None:
            """Filters the tree — server-side for a **paged** taxonomy.

            A taxonomy with thousands of nodes is not loaded completely, so
            filtering only the loaded ones would hide every node beyond the first
            page (`taxonomy_tree.uses_server_search`). The core's picker search
            covers the whole taxonomy (`GET /objects/picker`); the hits replace the
            tree until the query is cleared.
            """
            view = active_view["value"]
            query = (value or "").strip()
            if view == "category" and taxonomy_tree.uses_server_search(query, paged=roots_has_more):
                await search_taxonomy(query)
                return
            if tree_override or tree_query["value"] or tree_path:
                tree_override.clear()
                tree_path.clear()
                tree_query["value"] = ""
                render_tree(view)
                return
            tree = current_tree["tree"]
            if tree is not None:
                tree.set_nodes(filter_tree_nodes(current_tree["nodes"], query))

        def toggle_panel() -> None:
            if splitter.value is None:
                splitter.value = 25
            splitter.value = 0 if splitter.value > 0 else 25

        host: ui.column | None = None

        def render_tree(view: str) -> None:
            nonlocal current_tree
            if host is None:
                return
            host.clear()
            # A tree is only built when the panel shows the hierarchy; the flat
            # search hit list is rendered as rows (each hit can be selected or
            # opened in the tree).
            tree: LazyTree | None = None
            hit_list = bool(tree_override) and not tree_path
            # The child loader follows the shown view; annotated once so every branch
            # can assign its own loader.
            loader: Callable[[str], Awaitable[list[dict]]]
            if view == "collection" and collections:

                async def _no_children(_node_id: str) -> list[dict]:
                    return []

                nodes = [_collection_node(c) for c in collections]
                loader = _no_children
                on_select = handle_collection_select
            elif view == "category" and (category_roots or tree_override or tree_path):
                if tree_path:
                    # Ancestor branch of the hit the user opened in the tree.
                    nodes = tree_path
                elif hit_list:
                    # Server-side search hits: flat, no hierarchy to load.
                    nodes = tree_override
                else:
                    selected_id = selected_tree["id"]
                    roots = category_roots
                    if selected_id:
                        filtered = [r for r in category_roots if r["id"] == selected_id]
                        if filtered:
                            roots = filtered
                    nodes = taxonomy_tree.tree_nodes(
                        roots,
                        more_label=(
                            t("grid.taxonomy_more") if roots_has_more and not selected_id else None
                        ),
                        uncategorized=uncategorized_node,
                    )
                loader = load_category_children
                on_select = handle_category_select
            elif tree_nodes:
                nodes = tree_nodes
                loader = load_children
                on_select = handle_tree_select
            else:
                with host:
                    ui.label(t("grid.tree_load_failed")).classes("text-caption text-negative")
                current_tree = {"tree": None, "nodes": []}
                return
            with host:
                chain = taxonomy_tree.path_chain(tree_path) if tree_path else []
                if chain:
                    ui.label(t("grid.taxonomy_path").format(label=chain[-1]["label"])).classes(
                        "text-caption text-orange-600"
                    )
                elif hit_list:
                    ui.label(
                        t("grid.taxonomy_results").format(
                            count=len(tree_override), query=tree_query["value"]
                        )
                    ).classes("text-caption text-orange-600")
                if hit_list:
                    with ui.column().classes("w-full gap-0"):
                        for hit in tree_override:
                            with (
                                ui.row()
                                .classes("items-center gap-1 w-full py-0")
                                .style("flex-wrap: nowrap")
                            ):
                                ui.button(
                                    str(hit["label"]),
                                    on_click=lambda n=hit: select_category(str(n["id"])),
                                ).props("flat dense no-caps align=left").classes(
                                    "grow justify-start text-left"
                                )
                                ui.button(
                                    icon="account_tree",
                                    on_click=lambda n=hit: show_in_tree(str(n["id"])),
                                ).props("flat dense").tooltip(t("grid.taxonomy_in_tree"))
                else:
                    tree = LazyTree(
                        nodes,
                        on_select=on_select,
                        load_children=loader,
                        virtual_scroll=True,
                    ).classes("w-full")
                    tree.add_slot(
                        "default-header",
                        '<span :props="props">{{ props.node.label }}'
                        '<small v-if="props.node.type" class="text-caption text-gray-500">'
                        " &middot; {{ props.node.type }}</small>"
                        '<small v-if="props.node.count !== undefined" '
                        'class="text-caption text-gray-500">'
                        " &middot; {{ props.node.count }}</small></span>",
                    )
                    if tree_path:
                        tree.set_path(nodes, [str(node["id"]) for node in chain[:-1]])
            current_tree = {"tree": tree, "nodes": nodes}

        async def reload_collection() -> None:
            """Reloads the selected collection: re-materializes its members
            (``POST /collections/{id}/refresh``), refreshes the collection list
            (member counts) and re-fetches the grid for the active filter.
            The tree is left untouched while a collection is selected — re-sending
            the node list would corrupt the selection (the node toggles off on the
            next click). Without a selection the tree is re-rendered to update the
            member counts only."""
            nonlocal collections
            col_id = active_collection["id"]
            try:
                if col_id:
                    await client.refresh_collection(token, col_id)
                collections = await client.list_collections(token, limit=1000)
            except (CoreError, httpx.HTTPError) as error:  # keep current data on failure
                log.warning("collection refresh failed, keeping the shown data (%s)", error)
            if active_view["value"] == "collection" and not col_id:
                render_tree("collection")
            if col_id:
                await reload_grid()

        # --- Export (selection / current filter) ---------------------------
        def _export_fields() -> list[str]:
            """Maps the currently visible grid columns to export field names."""
            return export_fields(column_defs, visible_fields, language_fields)

        export_dialog = ui.dialog()
        with export_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("grid.export_title")).classes("text-h6")
            ui.label(t("grid.export_hint")).classes("text-caption text-gray-600")
            export_scope = (
                ui.select(
                    {
                        "filtered": t("grid.export_scope_filtered").format(count=len(all_rows)),
                        "selected": t("grid.export_scope_selected"),
                    },
                    value="selected" if bulk_selected_ids else "filtered",
                )
                .props("filled dense")
                .classes("w-full")
            )
            export_format = (
                ui.select(
                    {"csv": "CSV", "xlsx": "XLSX", "json": "JSON"},
                    value="csv",
                    label=t("grid.export_format"),
                )
                .props("filled dense")
                .classes("w-full")
            )

            async def _do_export() -> None:
                if export_scope.value == "selected":
                    selected: list = await grid.get_selected_rows()
                    ids = [oid for oid in (_row_id(row) for row in selected) if oid]
                else:
                    ids = [oid for oid in (_row_id(row) for row in all_rows) if oid]
                if not ids:
                    ui.notify(t("grid.export_no_objects"), color="warning")
                    return
                fmt = str(export_format.value or "csv")
                try:
                    queued = await client.create_export(
                        token,
                        source={"kind": "selection", "object_ids": ids},
                        fields=_export_fields(),
                        format=fmt,
                        locales=[locale],
                    )
                    run_id = str(queued.get("job_run_id") or "")
                    if not run_id:
                        raise RuntimeError(t("grid.export_failed").format(err="no job id"))
                    run = await _wait_export(client, token, run_id)
                    if run.get("status") != "success":
                        raise RuntimeError(run.get("error") or run.get("status") or "failed")
                    data, filename = await client.get_export_file(token, run_id, format=fmt)
                except (CoreError, httpx.HTTPError) as exc:  # surface a friendly message
                    ui.notify(t("grid.export_failed").format(err=exc), color="negative")
                    return
                exported = (run.get("result") or {}).get("exported", len(ids))
                export_dialog.close()
                _save_download(data, filename, fmt)
                ui.notify(t("grid.export_ok").format(count=exported), color="positive")

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=export_dialog.close).props("flat")
                ui.button(t("grid.export"), icon="download", on_click=_do_export).props("flat")

        with ui.splitter(value=25, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            # Left: resizable tree card (structure or categories via toggle).
            with splitter.before, ui.card().classes("w-full h-full p-0"):  # noqa: SIM117 - layout nesting
                # Line 1: view selector as icon buttons (active highlighted).
                def style_view_button(btn: ui.button, active: bool) -> None:
                    if active:
                        btn.props("color=primary text-white", remove="flat")
                    else:
                        btn.props("flat", remove="color text-white")

                def set_view(view: str) -> None:
                    nonlocal active_view
                    active_view["value"] = view
                    style_view_button(structure_btn, view == "structure")
                    style_view_button(category_btn, view == "category")
                    style_view_button(collection_btn, view == "collection")
                    # Only built when the catalog carries taxonomies.
                    if taxonomy_select is not None:
                        taxonomy_select.set_visibility(view == "category")
                    taxonomy_help.set_visibility(view == "category")
                    tree_select.set_visibility(view == "category")
                    dimension_select.set_visibility(view == "category")
                    dimension_help.set_visibility(view == "category")
                    collapse_btn.set_visibility(
                        view != "collection" and bool(tree_nodes or category_roots)
                    )
                    reload_btn.set_visibility(view == "collection")
                    # A new object hangs in the composition tree, so the action
                    # belongs to the structure view (categories have their own page).
                    create_btn.set_visibility(view == "structure" and bool(create_types))
                    if view != "collection" and active["collection"]:
                        active["collection"] = None
                        active_collection["id"] = None
                        sync_url_query()
                    render_tree(view)

                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    with ui.button_group().props("dense"):
                        structure_btn = ui.button(icon="account_tree").tooltip(t("grid.structure"))
                        category_btn = ui.button(icon="category").tooltip(t("grid.category"))
                        collection_btn = ui.button(icon="collections_bookmark").tooltip(
                            t("grid.collection")
                        )
                    structure_btn.on_click(lambda: set_view("structure"))
                    category_btn.on_click(lambda: set_view("category"))
                    collection_btn.on_click(lambda: set_view("collection"))
                    # The taxonomy (node type) shown in the category view: one at a
                    # time. Without the selector every taxonomy root — categories
                    # *and* the thousands of classification classes — would share
                    # one tree, which is neither readable nor a bounded query.
                    taxonomy_select = (
                        ui.select(
                            taxonomy_options(object_types, locale),
                            value=taxonomy_state["key"],
                            label=t("categories.taxonomy"),
                        )
                        .props("outline dense")
                        .classes("shrink-0")
                    )
                    # The node type behind the tree is a concept, not a field: the
                    # explanation lives in the handbook (one place, not per screen).
                    taxonomy_help = info_button("categories.taxonomy_hint", "product-grid")
                    taxonomy_select.set_visibility(False)
                    taxonomy_help.set_visibility(False)
                    taxonomy_select.on_value_change(lambda e: change_taxonomy(str(e.value or "")))
                    tree_select = (
                        ui.select(
                            {
                                "all": t("grid.all_trees"),
                                **{r["id"]: r["label"] for r in category_roots},
                            },
                            value="all",
                        )
                        .props("outline dense")
                        .classes("grow")
                    )

                    def on_tree_select(value: str) -> None:
                        selected_tree["id"] = None if value in (None, "all") else value
                        render_tree("category")

                    tree_select.on_value_change(lambda e: on_tree_select(e.value))
                    dimension_select = (
                        ui.select(
                            dimensions,
                            value=dimension["key"],
                            label=t("grid.category_dimension"),
                        )
                        .props("outline dense")
                        .classes("shrink-0")
                    )
                    dimension_help = info_button("grid.category_dimension_hint", "product-grid")
                    dimension_select.on_value_change(lambda e: change_dimension(e.value))
                    # A dimension chosen via `?definition_id=` may be bound to a
                    # taxonomy: apply it to the switcher once the switcher exists.
                    apply_dimension_taxonomy(dimension["key"])
                # Line 2: filter + action button (collapse for structure/category,
                # collection reload for the flat Collections view).
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    create_btn = (
                        ui.button(icon="add", on_click=lambda: open_create())
                        .props("flat dense shrink-0")
                        .tooltip(t("grid.create_object"))
                    )
                    collapse_btn = (
                        ui.button(icon="unfold_less", on_click=collapse_all)
                        .props("flat dense shrink-0")
                        .tooltip(t("grid.collapse_all"))
                    )
                    reload_btn = (
                        ui.button(icon="refresh", on_click=reload_collection)
                        .props("flat dense shrink-0")
                        .tooltip(t("grid.collection_reload"))
                    )
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                set_view("structure")

                async def on_filter_change(event) -> None:
                    """Tree filter: client-side, or the server search of a paged taxonomy."""
                    await apply_tree_filter(str(getattr(event, "value", "") or ""))

                filter_input.on_value_change(on_filter_change)
            # Right: grid area (fills the remaining width and height).
            with splitter.after, ui.column().classes("w-full h-full pl-3"):  # noqa: SIM117 - layout
                with ui.row().classes("items-center gap-2 w-full"):
                    # Panel toggle (structure tree): separate function, far left.
                    ui.button(icon="view_sidebar", on_click=toggle_panel).props(
                        "flat dense"
                    ).tooltip(t("grid.toggle_tree"))
                    staged_label = ui.label(t("grid.staged_changes").format(count=0)).classes(
                        "text-caption"
                    )
                    # Right-aligned data actions + view controls.
                    ui.button(t("grid.save"), icon="save", on_click=save).classes(
                        "bg-primary text-white ml-auto"
                    )
                    ui.button(icon="undo", on_click=discard).props("outline").tooltip(
                        t("grid.discard")
                    )
                    ui.button(icon="bookmarks", on_click=views_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.views"))
                    ui.button(icon="fact_check", on_click=bulk_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.bulk_actions"))
                    ui.button(icon="find_replace", on_click=replace_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.replace_actions"))
                    ui.button(icon="edit_note", on_click=start_sequential).props("outline").tooltip(
                        t("seq.start")
                    )
                    ui.button(icon="download", on_click=export_dialog.open).props(
                        "outline"
                    ).tooltip(t("grid.export"))
                    # One handbook entry point per working area (the toolbar itself
                    # explains its icons via tooltips; the concepts live in /help).
                    chapter_button("product-grid").props("outline")

                    # --- Column picker -------------------------------------
                    # A dialog (not a `ui.menu`: Quasar's QMenu auto-open doesn't
                    # fire in Firefox/Zen) whose list is **grouped by attribute
                    # group**, like the export wizard's field selection — a
                    # catalogue with an imported standard carries thousands of
                    # attributes and a flat list of them is unusable. An attribute
                    # may sit in several groups: it shows up in each of them while
                    # staying **one** column (`checkbox_refs` holds every box of a
                    # field and `sync_picker()` keeps them in sync). The
                    # per-language columns are a **hybrid** like the product form:
                    # the attribute's own box is the working-language column, the
                    # `translate` button opens its language columns.
                    column_fields = {str(column.get("field") or "") for column in column_defs}
                    base_labels = {
                        "_id": t("grid.id"),
                        "_name": t("grid.name"),
                        "_type": t("grid.type"),
                        "_status": t("grid.status"),
                        "_completeness": t("grid.completeness"),
                        "_releases": t("grid.releases"),
                        "_refs": t("grid.columns_refs"),
                    }
                    base_fields = [field for field in base_labels if field in column_fields]
                    grouped_names, ungrouped_names = group_members(own_definitions)

                    def group_title(group_id: str) -> str:
                        group = groups_by_id.get(group_id) or {}
                        return (
                            resolve_value(group.get("label"), locale)
                            or str(group.get("name") or "")
                            or group_id
                        )

                    ordered_group_ids = group_order(grouped_names, groups_by_id, group_title)

                    def field_checkbox(field: str, text: str) -> ui.checkbox:
                        """One picker box for a field (a field may get several)."""
                        box = (
                            ui.checkbox(text, value=field in visible_fields)
                            .props("dense")
                            .on_value_change(lambda e, f=field: on_field_change(f, e))
                        )
                        checkbox_refs.setdefault(field, []).append(box)
                        return box

                    language_dialogs: dict[str, ui.dialog] = {}

                    def open_language_dialog(prefix: str, title: str) -> None:
                        """Opens the language columns of one attribute (hybrid part)."""
                        dlg = language_dialogs.get(prefix)
                        if dlg is None:
                            with ui.dialog() as dlg, ui.card().classes("w-[24rem] max-w-[92vw]"):
                                ui.label(title).classes("text-h6")
                                ui.label(t("grid.columns_languages_hint")).classes(
                                    "text-caption text-gray-500"
                                )
                                for language, field in (
                                    language_fields_by_prefix.get(prefix) or {}
                                ).items():
                                    field_checkbox(
                                        field,
                                        f"{language_labels.get(language, language)} · "
                                        f"{language.upper()}",
                                    )
                                with ui.row().classes("w-full justify-end"):
                                    ui.button(t("grid.close"), on_click=dlg.close).props(
                                        "flat dense"
                                    )
                            language_dialogs[prefix] = dlg
                        dlg.open()

                    def language_row(prefix: str, title: str) -> None:
                        """The hybrid language control of one attribute."""
                        fields = language_fields_by_prefix.get(prefix) or {}
                        if not fields:
                            return
                        counter = ui.label().classes("text-caption text-gray-500")
                        language_counters[prefix] = counter
                        ui.button(icon="translate").props("flat round dense").on_click(
                            lambda p=prefix, title_=title: open_language_dialog(p, title_)
                        ).tooltip(t("grid.columns_languages_hint"))

                    def add_section(
                        group_id: str,
                        title: str,
                        rows: list[tuple[str, str, str]],
                        *,
                        open_: bool = False,
                    ) -> None:
                        """One collapsible section: group toggle + `(field, text, prefix)` rows.

                        `prefix` is the field prefix of the attribute's language
                        columns (empty when it has none), so the group toggle can
                        clear them together with the group.
                        """
                        if not rows:
                            return
                        with (
                            ui.expansion(f"{title} ({len(rows)})", value=open_)
                            .classes("w-full")
                            .props("dense")
                        ):
                            group_box = (
                                ui.checkbox(t("grid.columns_group_all"))
                                .props("dense")
                                .on_value_change(
                                    lambda e, g=group_id: (
                                        None if syncing["value"] else toggle_group(g, bool(e.value))
                                    )
                                )
                            )
                            group_boxes[group_id] = group_box
                            group_fields[group_id] = [field for field, _, _ in rows]
                            group_prefixes[group_id] = [prefix for _, _, prefix in rows if prefix]
                            for field, text, prefix in rows:
                                with ui.row().classes("w-full items-center gap-1 pl-4"):
                                    field_checkbox(field, text)
                                    language_row(prefix, text)

                    def attribute_rows(names: list[str]) -> list[tuple[str, str, str]]:
                        """Picker rows for attribute names (label + name, language prefix)."""
                        rows: list[tuple[str, str, str]] = []
                        for name in names:
                            definition = definitions_by_name.get(name) or {}
                            label = resolve_value(definition.get("label"), locale) or name
                            text = name if label == name else f"{label} ({name})"
                            rows.append(
                                (attribute_field(name), text, language_prefixes.get(name, ""))
                            )
                        return rows

                    # Column picker as a dialog (not a `ui.menu`): Quasar's QMenu
                    # auto-open doesn't fire in Firefox/Zen, while `ui.dialog` opens
                    # reliably in all browsers (same pattern as the working "views").
                    columns_dialog = ui.dialog()
                    with columns_dialog, ui.card().classes("w-[26rem] max-w-[94vw]"):
                        ui.label(t("grid.columns")).classes("text-h6")
                        with ui.column().classes("w-full gap-0 max-h-[70vh] overflow-y-auto"):
                            add_section(
                                "_base",
                                t("grid.columns_base"),
                                [
                                    (
                                        field,
                                        base_labels[field],
                                        NAME_FIELD_PREFIX if field == "_name" else "",
                                    )
                                    for field in base_fields
                                ],
                                open_=True,
                            )
                            for group_id in ordered_group_ids:
                                add_section(
                                    group_id,
                                    group_title(group_id),
                                    attribute_rows(grouped_names[group_id]),
                                )
                            add_section(
                                "_ungrouped",
                                t("grid.columns_ungrouped"),
                                attribute_rows(ungrouped_names),
                            )
                            if reference_definitions:
                                add_section(
                                    "_standard",
                                    t("grid.columns_reference"),
                                    attribute_rows(
                                        [str(d.get("name")) for d in reference_definitions]
                                    ),
                                )
                            hidden_standard = sum(
                                1 for d in definitions if d.get("reference_data")
                            ) - len(reference_definitions)
                            if hidden_standard > 0:
                                ui.label(
                                    t("grid.columns_reference_hidden").format(count=hidden_standard)
                                ).classes("text-caption text-gray-500 mt-2")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(t("grid.close"), on_click=columns_dialog.close).props("flat")
                        # Initial state: the field boxes are built with the current
                        # set, the group toggles and language counters are filled
                        # from it (a restored personal view must show up here).
                        sync_picker()
                    ui.button(icon="view_column").props("outline").tooltip(
                        t("grid.columns")
                    ).on_click(columns_dialog.open)

                # --- New object ------------------------------------------
                # Creating an object needs its **composition parent** (root
                # contract), so the dialog passes it with the write: the selected
                # node of the structure tree is the parent, the universal root is
                # the fallback (`POST /objects` defaults to it). The type registry
                # supplies the display attribute (`label_attribute`) and the
                # mandatory business keys (`key_attributes`) — a key counts as a
                # required field for completeness (`cache.rs`, `completeness.rs`).
                type_options = {
                    str(item.get("name")): (
                        resolve_value(item.get("label"), locale) or str(item.get("name"))
                    )
                    for item in create_types
                }
                type_by_name = {str(item.get("name")): item for item in create_types}
                create_state: dict = {"parent": None}
                create_key_inputs: dict[str, ui.element] = {}

                def label_attribute_of(type_name: str) -> str:
                    """The type's display attribute (`label_attribute`, default `name`)."""
                    return str((type_by_name.get(type_name) or {}).get("label_attribute") or "name")

                def is_multilingual_name(attribute: str) -> bool:
                    """`name` is multilingual by convention, others by definition."""
                    if attribute == "name":
                        return True
                    return bool((definitions_by_name.get(attribute) or {}).get("multilingual"))

                @ui.refreshable
                def render_create_fields() -> None:
                    """Label attribute + business keys of the chosen type."""
                    create_key_inputs.clear()
                    type_name = str(create_type_select.value or "")
                    label_attribute = label_attribute_of(type_name)
                    if is_multilingual_name(label_attribute):
                        multilingual_value_editor(
                            field_label=t("grid.create_name"),
                            locale=locale,
                            languages=languages,
                            value=None,
                            store=create_state,
                            key=label_attribute,
                        )
                    else:
                        single = (
                            ui.input(t("grid.create_name")).props("filled dense").classes("w-full")
                        )
                        single.on_value_change(
                            lambda e, k=label_attribute: create_state.__setitem__(
                                k, str(e.value or "")
                            )
                        )
                    for key_name in [
                        str(item)
                        for item in (type_by_name.get(type_name) or {}).get("key_attributes") or []
                    ]:
                        definition = definitions_by_name.get(key_name) or {}
                        label = resolve_value(definition.get("label"), locale) or key_name
                        create_key_inputs[key_name] = (
                            ui.input(label if label == key_name else f"{label} ({key_name})")
                            .props("filled dense")
                            .classes("w-full")
                        )
                    if create_key_inputs:
                        ui.label(t("grid.create_key_hint")).classes("text-caption text-gray-500")

                async def parent_caption(parent: str | None) -> str:
                    """`Placement: <name>` of the composition parent (id as fallback)."""
                    if not parent:
                        return t("grid.create_parent_root")
                    try:
                        parent_object = await client.get_object(token, parent, locale=locale)
                    except CoreError, httpx.HTTPError:  # show the raw reference
                        return str(parent)
                    attributes = parent_object.get("attributes") or {}
                    label = resolve_value(attributes.get("name"), locale)
                    return str(label or parent_object.get("id") or parent)

                async def open_create() -> None:
                    """Opens the create dialog for the tree's selection (else the root)."""
                    create_state.clear()
                    create_state["parent"] = active["ancestor"]
                    default_type = next(iter(type_options), None)
                    if create_type_select.value != default_type:
                        create_type_select.set_value(default_type)
                    render_create_fields.refresh()
                    create_parent_label.set_text(
                        t("grid.create_parent").format(
                            parent=await parent_caption(active["ancestor"])
                        )
                    )
                    create_dialog.open()

                async def save_new_object() -> None:
                    type_name = str(create_type_select.value or "")
                    label_attribute = label_attribute_of(type_name)
                    raw_name = create_state.get(label_attribute)
                    if isinstance(raw_name, dict):
                        name_value: object = {
                            str(lang): str(text)
                            for lang, text in raw_name.items()
                            if str(text or "").strip()
                        }
                        has_name = bool(name_value)
                    else:
                        name_value = str(raw_name or "").strip()
                        has_name = bool(name_value)
                    if not has_name:
                        ui.notify(t("grid.create_name_required"), color="warning")
                        return
                    attributes: dict[str, object] = {label_attribute: name_value}
                    for key_name, field in create_key_inputs.items():
                        value = str(getattr(field, "value", "") or "").strip()
                        if not value:
                            ui.notify(
                                t("grid.create_key_required").format(name=key_name), color="warning"
                            )
                            return
                        attributes[key_name] = value
                    try:
                        created = await client.create_object(
                            token,
                            object_type=type_name,
                            attributes=attributes,
                            parent=create_state.get("parent"),
                        )
                    except CoreError as error:
                        ui.notify(f"{t('grid.create_failed')}: {error}", color="negative")
                        return
                    create_dialog.close()
                    type_label = type_options.get(type_name, type_name)
                    ui.notify(t("grid.created").format(type=type_label), color="positive")
                    # Straight into the form: the new object is still empty.
                    ui.navigate.to(f"/products/{created['id']}")

                create_dialog = ui.dialog()
                with create_dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
                    ui.label(t("grid.create_title")).classes("text-h6")
                    ui.label(t("grid.create_hint")).classes("text-caption text-gray-600")
                    create_type_select = (
                        ui.select(type_options, label=t("grid.create_type"))
                        .props("filled dense")
                        .classes("w-full")
                    )
                    create_type_select.on_value_change(lambda e: render_create_fields.refresh())
                    create_parent_label = ui.label().classes("text-caption text-gray-500")
                    with ui.column().classes("w-full gap-2"):
                        render_create_fields()
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button(t("grid.close"), on_click=create_dialog.close).props("flat")
                        ui.button(
                            t("grid.create_save"),
                            icon="add",
                            on_click=lambda: save_new_object(),
                        ).props("flat")

                # --- Filter bar (server-side filters) --------------------------
                # The grid can be empty (no tree node picked), so both filters are
                # resolved in the core: the search via `/objects/picker` +
                # `/objects?ids=`, "only incomplete" via `?missing_required=true`.
                suppress_change = {"value": False}

                async def on_search_change(value: str | None) -> None:
                    if suppress_change["value"]:
                        return
                    text = (value or "").strip() or None
                    if text == active["q"]:
                        return
                    active["q"] = text
                    await reload_grid()

                async def on_incomplete_change(event) -> None:
                    if suppress_change["value"]:
                        return
                    active["incomplete"] = bool(event.value)
                    await reload_grid()

                async def on_channel_change(value: str) -> None:
                    """Filters the grid to the objects released for one channel."""
                    if value == (active["released_for"] or ""):
                        return
                    active["released_for"] = value or None
                    await reload_grid()

                async def reset_filters() -> None:
                    """Clears the filter bar; the tree/category/collection stays."""
                    active.update(incomplete=False, q=None, where=None, released_for=None)
                    suppress_change["value"] = True
                    try:
                        incomplete_switch.value = False
                        search_input.value = ""
                        if channel_filter is not None:
                            channel_filter.value = ""
                    finally:
                        suppress_change["value"] = False
                    update_where_chip()
                    await reload_grid()

                # --- Advanced search (Query DSL) ------------------------------
                # The same field catalog and the same condition form as the saved
                # searches; the query travels to the core as `?where=<json>` and is
                # validated there against the catalog.
                where_state: dict = {"form": None, "stored": {}, "catalog": {}, "saved": []}

                def where_summary() -> str:
                    """Human-readable summary of the active condition tree."""
                    query = search_form.parse_json(active["where"]) or {}
                    return search_form.summary_text(query, where_state["catalog"])

                def update_where_chip() -> None:
                    """Shows/updates the chip of the active advanced search."""
                    is_active = bool(active["where"])
                    where_chip.set_text(
                        t("grid.advanced_active", summary=where_summary()) if is_active else ""
                    )
                    where_chip.set_visibility(is_active)

                def current_where_query() -> dict:
                    """The query the dialog currently represents."""
                    form = where_state["form"]
                    if form is None:
                        return dict(where_state["stored"])
                    return search_form.query_from_state(
                        form.read_state(), where_state["catalog"], where_state["stored"]
                    )

                def has_advanced_conditions(query: dict) -> bool:
                    """Whether a query restricts anything (empty = no filter)."""
                    return bool(
                        query.get("where")
                        or query.get("references")
                        or str((query.get("fulltext") or {}).get("query") or "").strip()
                    )

                def ensure_where_form() -> search_form.SearchForm:
                    """Renders the condition form once (after the catalog loaded)."""
                    # The host exists as soon as the dialog is built, which happens
                    # before this runs - the guard keeps the call order checkable.
                    if where_state["form"] is None and where_host is not None:
                        where_host.clear()
                        with where_host:
                            where_state["form"] = search_form.SearchForm(
                                where_state["catalog"],
                                sections=("conditions", "references", "fulltext"),
                                client=client,
                                token=token,
                            )
                    return where_state["form"]

                def _fill_where_dialog() -> None:
                    """Fills the dialog from the active filter (or the last edit)."""
                    stored = search_form.parse_json(active["where"]) or where_state["stored"]
                    where_state["stored"] = copy.deepcopy(stored)
                    form = ensure_where_form()
                    form.set_state(search_form.state_from_query(stored, where_state["catalog"]))

                async def open_advanced() -> None:
                    """Opens the advanced-search dialog (loads catalog + saved searches)."""
                    try:
                        where_state["catalog"] = await client.list_search_fields(token)
                    except CoreError, httpx.HTTPError:  # without a catalog there is no form
                        ui.notify(t("grid.advanced_failed"), color="negative")
                        return
                    try:
                        saved = await client.list_saved_searches(token, limit=1000)
                    except CoreError, httpx.HTTPError:  # the picker is optional
                        saved = []
                    where_state["saved"] = saved
                    saved_select.set_options(
                        {str(s.get("id")): _saved_search_name(s) for s in saved}
                    )
                    _fill_where_dialog()
                    where_dialog.open()

                async def apply_advanced() -> None:
                    """Applies the condition tree as a server-side filter."""
                    query = current_where_query()
                    errors = search_form.validate_state(
                        search_form.state_from_query(query, where_state["catalog"]),
                        where_state["catalog"],
                    )
                    if errors:
                        ui.notify("; ".join(errors), color="warning")
                        return
                    active["where"] = (
                        search_form.compact_json(search_form.object_filter(query))
                        if has_advanced_conditions(query)
                        else None
                    )
                    update_where_chip()
                    where_dialog.close()
                    await reload_grid()

                async def clear_advanced() -> None:
                    """Drops the advanced-search filter."""
                    active["where"] = None
                    where_state["stored"] = {}
                    form = where_state["form"]
                    if form is not None:
                        form.set_state(search_form.default_state(where_state["catalog"]))
                    update_where_chip()
                    where_dialog.close()
                    await reload_grid()

                async def load_saved_search() -> None:
                    """Loads the picked saved search into the dialog."""
                    search_id = str(saved_select.value or "")
                    entry = next(
                        (s for s in where_state["saved"] if str(s.get("id")) == search_id), None
                    )
                    if entry is None:
                        return
                    query = dict(entry.get("attributes", {}).get("query") or {})
                    where_state["stored"] = copy.deepcopy(query)
                    form = where_state["form"]
                    if form is not None:
                        form.set_state(search_form.state_from_query(query, where_state["catalog"]))

                async def save_as_search() -> None:
                    """Creates a saved search from the current condition tree."""
                    name = (save_name_input.value or "").strip()
                    if not name:
                        ui.notify(t("grid.advanced_save_name_required"), color="warning")
                        return
                    try:
                        await client.create_saved_search(
                            token, name=name, query=current_where_query()
                        )
                    except (CoreError, httpx.HTTPError) as error:  # report the core message
                        ui.notify(f"{t('grid.advanced_save_failed')}: {error}", color="negative")
                        return
                    save_name_input.value = ""
                    ui.notify(t("grid.advanced_saved", name=name), color="positive")

                where_host: ui.column | None = None
                where_dialog = ui.dialog()
                with where_dialog, ui.card().classes("w-[52rem] max-w-[96vw]"):
                    ui.label(t("grid.advanced_title")).classes("text-h6")
                    ui.label(t("grid.advanced_hint")).classes("text-caption text-gray-500")
                    with ui.row().classes("w-full items-center gap-2"):
                        saved_select = (
                            ui.select({}, label=t("grid.advanced_load"), with_input=False)
                            .props("dense filled clearable")
                            .classes("grow")
                        )
                        saved_select.on_value_change(lambda _: load_saved_search())
                    where_host = ui.column().classes("w-full gap-1")
                    with ui.row().classes("w-full items-center gap-2"):
                        save_name_input = (
                            ui.input(label=t("grid.advanced_save"))
                            .props("dense filled")
                            .classes("grow")
                        )
                        ui.button(
                            t("grid.advanced_save_button"),
                            icon="bookmark_add",
                            on_click=lambda: save_as_search(),
                        ).props("flat dense")
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button(
                            t("grid.advanced_clear"), on_click=lambda: clear_advanced()
                        ).props("flat")
                        ui.button(t("grid.close"), on_click=where_dialog.close).props("flat")
                        ui.button(
                            t("grid.advanced_apply"),
                            icon="check",
                            on_click=lambda: apply_advanced(),
                        ).props("flat")

                with ui.row().classes("items-center gap-2 w-full pb-1").style("flex-wrap: nowrap"):
                    search_input = (
                        ui.input(
                            placeholder=t("grid.filter_search"),
                            value=active["q"] or "",
                        )
                        .props(f"outline dense clearable debounce={SEARCH_DEBOUNCE_MS}")
                        .classes("grow")
                    )
                    search_input.on_value_change(lambda e: on_search_change(e.value))
                    incomplete_switch = ui.switch(
                        t("grid.filter_incomplete"), value=bool(active["incomplete"])
                    ).props("dense shrink-0")
                    incomplete_switch.on_value_change(on_incomplete_change)
                    channel_filter = None
                    if channels:
                        channel_filter = filter_select(
                            {"": t("grid.filter_all_channels"), **channel_labels},
                            value=active["released_for"] or "",
                            classes="w-48 shrink-0",
                            tooltip=t("grid.filter_released_for"),
                            on_change=on_channel_change,
                        )
                    ui.button(icon="filter_alt_off", on_click=reset_filters).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.filter_reset"))
                    ui.button(icon="filter_alt", on_click=lambda: open_advanced()).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.advanced_open"))
                    # Column sizing: fit the columns into the available width, or
                    # widen every column so its content fits (capped at 30 chars).
                    ui.button(icon="fit_screen", on_click=fit_to_view).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.fit_to_view"))
                    ui.button(icon="straighten", on_click=fit_to_content).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.fit_to_content"))
                # Active advanced search: a clickable summary line below the bar.
                where_chip = (
                    ui.button("", on_click=lambda: open_advanced())
                    .props("flat dense no-caps")
                    .classes("text-caption text-primary pb-1")
                    .tooltip(t("grid.advanced_open"))
                )
                where_chip.set_visibility(False)
                update_where_chip()

                grid = ui.aggrid(
                    {
                        "columnDefs": column_defs,
                        "rowData": all_rows,
                        "rowSelection": {"mode": "multiRow"},
                        "pagination": True,
                        "paginationPageSize": 50,
                        # Header tooltips via the native browser tooltip (`title`):
                        # AG Grid's own tooltip component does not show up here.
                        "enableBrowserTooltips": True,
                    },
                    html_columns=[
                        i
                        for i, column in enumerate(column_defs)
                        if column["field"] in ("_status", "_completeness", "_releases")
                    ],
                ).classes("w-full grow")
                grid.on("cellValueChanged", mark_changed, ["data", "colId"])
                grid.on("cellClicked", handle_cell_clicked, ["data", "colId"])
                grid.on("cellDoubleClicked", handle_open_cell, ["data", "colId"])
                grid.on("selectionChanged", handle_selection, ["selectedRows"])
                # Restore the column set the user had before (reload, browser-back
                # from the form, re-login) — the columns start hidden per definition.
                apply_visibility(visible_fields)

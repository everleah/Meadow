"""Object list grid (AG Grid): the main work area (Phase 7, steps 3-5).

The grid lists objects with human-friendly display (resolved name, status
badge), supports inline editing / batch save, offers a column picker and
personal views, and shows a composition (structure) tree that filters the
grid to the selected subtree. It also offers the category/collection views,
the completeness column with its server-side filter bar, and one hidden
per-language column for every multilingual attribute (Sprach-Spalten).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request
from nicegui import app, ui

from meadow.frontend import config, edit_queue, grid_query, taxonomy_tree
from meadow.frontend.api import CoreClient, CoreError, is_session_expired
from meadow.frontend.auth import handle_session_expired
from meadow.frontend.bulk_edit import (
    ids_from_rows,
    row_id,
)
from meadow.frontend.bulk_report import replace_target_options
from meadow.frontend.bulk_wizard import BulkWizard, WizardCatalogs
from meadow.frontend.cell_values import (
    as_bool,
    as_number,
    is_multilingual_map,
    staged_patches,
)
from meadow.frontend.column_picker import ColumnPicker, PickerCatalogs
from meadow.frontend.column_views import ColumnViews
from meadow.frontend.create_object import CreateObjectDialog
from meadow.frontend.display import (
    channel_options,
    completeness_badge,
    dimension_options,
    release_badges,
    resolve_value,
    status_badge,
    taxonomy_options,
    taxonomy_type_names,
)
from meadow.frontend.export_dialog import ExportDialog
from meadow.frontend.filters import filter_select
from meadow.frontend.help_page import chapter_button, info_button
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.object_picker import OBJECT_ROOT_ID
from meadow.frontend.range_values import format_range
from meadow.frontend.reference_data import has_value, visible_in_picker
from meadow.frontend.references_dialog import ReferencesDialog
from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    association_definitions,
    category_definitions,
    category_dimension_bindings,
    definition_key,
)
from meadow.frontend.replace_dialog import ReplaceDialog
from meadow.frontend.tree_panel import TreePanel, TreeState
from meadow.frontend.url_values import media_kind, open_media_dialog
from meadow.frontend.view_switch import ViewSwitch
from meadow.frontend.where_dialog import WhereDialog

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


def sort_field(
    column_field: str,
    *,
    definitions: Mapping[str, Mapping[str, object]],
    locale: str,
    languages: Iterable[str] = (),
) -> str | None:
    """The core sort field of a grid column, or ``None`` when it cannot be sorted.

    The grid's columns are display fields; the core sorts on the **stored** field and
    only accepts a *sortable* field of the search catalog (`GET /search-fields`, see
    `crates/AGENTS.md`), so a column either maps to such a field or the grid must not
    offer sorting for it at all — a sort that only reorders the loaded page while the
    view stays unsorted would be a lie.

    The mapping of the three mappable shapes:

    - ``_type``/``_status`` are the stored `type`/`status` columns.
    - ``_name`` is the **conventional label** (`attributes.name`). The base key is used
      deliberately: it is the one spelling that works for both shapes an object may
      store (a plain string, as collections do, and a language map), and on the dev data
      it orders exactly like the `de` value (`de` is the first entry of every map).
    - ``attr_<name>`` is the attribute's working-language value — a multilingual
      attribute is sorted by its **language** entry (`attributes.<name>.<lang>`), which
      is the value the column shows. The column's own per-language columns
      (`attr_<name>__<lang>`, `_name__<lang>`) sort by that language.
    """
    if column_field in ("_type", "_status"):
        return column_field[1:]
    if column_field == NAME_FIELD_PREFIX:
        return "attributes.name"
    if column_field.startswith(f"{NAME_FIELD_PREFIX}{LANG_SEPARATOR}"):
        language = column_field.split(LANG_SEPARATOR, 1)[1]
        return f"attributes.name.{language}" if language else None
    if not column_field.startswith("attr_"):
        return None
    name = column_field[len("attr_") :]
    if not name:
        return None
    if LANG_SEPARATOR in name:
        attribute, language = name.split(LANG_SEPARATOR, 1)
        return f"attributes.{attribute}.{language}" if attribute and language else None
    definition = definitions.get(name) or {}
    if definition.get("multilingual"):
        # The core only knows the languages its catalog declares. A working language
        # outside of them (e.g. `de-DE` without a declared variant) falls back to the
        # attribute's base language when there is one, otherwise to the base field.
        declared = {str(code) for code in languages}
        for candidate in (locale, locale.split("-")[0] if locale else ""):
            if candidate and candidate in declared:
                return f"attributes.{name}.{candidate}"
    return f"attributes.{name}"


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


def editable_cell_value(
    definition: dict, raw: object, locale: str, labels: dict[str, str] | None = None
) -> object:
    """Builds the **typed** working-language cell value of an editable attribute.

    The cell carries the native type (number, bool, string) so AG Grid offers the
    matching editor; enum cells carry the localized `label` (the stored code is
    restored on save by `storable_cell_value`).
    """
    atype = str(definition.get("type") or "text")
    value = raw.get(locale) if is_multilingual_map(raw) else raw
    if value is None:
        return None
    if atype == "number":
        return as_number(value)
    if atype == "range":
        return format_range(value) or None
    if atype == "boolean":
        return as_bool(value)
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
        if is_multilingual_map(raw) and raw.get(locale) is None:
            continue
        cells[attribute_field(name)] = editable_cell_value(
            definition, raw, locale, (enum_labels or {}).get(name)
        )
    return cells


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


@ui.page("/products", response_timeout=PAGE_TIMEOUT)
async def products_page(request: Request) -> None:
    """Lists objects in the main grid with inline editing + batch save."""
    await build_shell(active="/products")
    locale = current_locale()
    # Active grid filters: seeded from the URL (bookmarkable, e.g. the dashboard
    # shortcut `?incomplete=1&type=product`) and then driven by the filter bar. The
    # state and its URL form live in `meadow/frontend/grid_query.py`.
    active: dict[str, Any] = grid_query.read_filters(request.query_params)
    # Monotonic reload counter: a slow reload never overwrites a newer result.
    load_seq = {"value": 0}

    def has_filter() -> bool:
        """Whether any filter is active (a plain visit deliberately loads nothing)."""
        return grid_query.has_filter(active)

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
            """Loads the current **page** of the objects matching the filters.

            The page is `?page=` of the view (`grid_query.PAGE_SIZE` rows from
            `page_start`) and comes **ordered** by the core: sorting and paging belong to
            the whole view, not to the loaded rows, so the grid renders what the server
            decided (`grid_query.list_arguments`). The search path stays a single capped
            request (`SEARCH_LIMIT` hits) — a hit list is a result, not a page of a view.
            """
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
                    str(active["q"]),
                    types=grid_query.search_types(active),
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
                    **grid_query.search_arguments(
                        active, ids=ids, limit=SEARCH_LIMIT, locale=locale
                    ),
                )
            return await client.list_objects(
                token,
                **grid_query.list_arguments(active, has_channels=bool(channels), locale=locale),
            )

        async def fetch_total() -> int | None:
            """The number of objects in the view (`None` = no pager).

            `GET /objects/count` takes exactly the list's filters (`grid_query` builds
            both from one function), so the total can never describe another view. A
            search or a plain visit has no pager: the hit list is capped by design.
            """
            if not has_filter() or active["q"]:
                return None
            payload = await client.count_objects(
                token, **grid_query.count_arguments(active, locale=locale)
            )
            count = payload.get("count")
            return int(count) if isinstance(count, int) else None

        async def fetch_usage_names() -> set[str]:
            """The attribute names the **whole view** carries data for.

            The column set used to follow the loaded rows, so a standard attribute got
            its column when a page happened to carry its first value. With paging that
            would make the columns depend on the page: the core answers it for the view
            (`GET /objects/attribute-usage`, `grid_query.usage_arguments`).
            """
            if not has_filter():
                return set()
            payload = await client.attribute_usage(
                token, **grid_query.usage_arguments(active, locale=locale)
            )
            return {str(name) for name in payload.get("names") or []}

        # On a plain /products visit we deliberately avoid loading every object:
        # the grid stays empty and the user picks a node in the tree or searches.
        # A filter (e.g. `?incomplete=1&type=product`, `?ancestor=…`, `?category=…`)
        # loads the targeted objects so shortcuts still work.
        objects: list[dict] = []
        total: int | None = None
        try:
            objects = await fetch_filtered()
            total = await fetch_total()
        except CoreError, httpx.HTTPError:  # keep the page usable (filter bar + grid)
            ui.notify(t("grid.load_failed"), color="negative")
        no_objects_label = ui.label(t("grid.no_objects")).classes("text-subtitle1")
        no_objects_label.set_visibility(has_filter() and not objects)
        selection_hint.set_visibility(not has_filter())

        # The grid pages **server-side** (`grid_query.PAGE_SIZE` rows per page): every row
        # carries all own attributes plus the language and inheritance cells, so one page
        # of 1000 rows was 5,6 MB of HTML (measured 2026-09-22). The core owns the order
        # and the total, this row only steps through the pages and always says which rows
        # of how many are on screen — nothing is truncated silently.
        loaded_objects: list[dict] = list(objects)
        view_state: dict[str, int | None] = {"total": total}
        with ui.row().classes("items-center gap-2 pt-1") as pager_row:
            pager_row.mark("grid-pager")
            first_button = (
                ui.button(icon="first_page", on_click=lambda: load_page(1))
                .props("flat dense round")
                .mark("grid-page-first")
                .tooltip(t("grid.page_first"))
            )
            prev_button = (
                ui.button(
                    icon="chevron_left",
                    on_click=lambda: load_page(grid_query.page_number(active) - 1),
                )
                .props("flat dense round")
                .mark("grid-page-prev")
                .tooltip(t("grid.page_prev"))
            )
            range_label = ui.label("").classes("text-caption")
            range_label.mark("grid-page-range")
            next_button = (
                ui.button(
                    icon="chevron_right",
                    on_click=lambda: load_page(grid_query.page_number(active) + 1),
                )
                .props("flat dense round")
                .mark("grid-page-next")
                .tooltip(t("grid.page_next"))
            )
            last_button = (
                ui.button(
                    icon="last_page",
                    on_click=lambda: load_page(grid_query.page_count(view_state["total"] or 0)),
                )
                .props("flat dense round")
                .mark("grid-page-last")
                .tooltip(t("grid.page_last"))
            )
            pages_label = ui.label("").classes("text-caption text-gray-600")
            pages_label.mark("grid-page-label")

        def update_pager_controls() -> None:
            """Shows the page controls and what they can do (`None` total = no pager)."""
            total = view_state["total"]
            if total is None or not has_filter() or active["q"]:
                # No total (a search, a plain visit or a failed count): the rows are
                # shown without a pager instead of pretending a page count.
                pager_row.set_visibility(False)
                return
            pager_row.set_visibility(True)
            page = grid_query.page_number(active)
            pages = grid_query.page_count(total)
            first_row = grid_query.page_start(active) + 1
            last_row = min(total, first_row + len(loaded_objects) - 1) if loaded_objects else 0
            range_label.set_text(t("grid.page_range", first=first_row, last=last_row, total=total))
            pages_label.set_text(t("grid.page_label", page=page, pages=pages))
            first_button.set_enabled(page > 1)
            prev_button.set_enabled(page > 1)
            next_button.set_enabled(page < pages)
            last_button.set_enabled(page < pages)

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
        #: Language rows (`GET /languages`) for the create dialog's name editor. Bound
        #: before the call so a rejected request degrades like every other catalog of
        #: this page: an unbound name here turned a `401` (session lost after a core
        #: restart, 2026-09-23) into `500 UnboundLocalError` on the whole grid, hiding
        #: the session handler's way back to the login page.
        languages: list[dict] = []
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
            languages = []

        # The user's column set is resolved **before** the columns are built: a
        # restored set decides which standard columns have to exist at all (a
        # saved view that carries `EF…` columns stays switchable).
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
        #: Attribute names the view carries a value for. Reference data is *read*, not
        #: chosen: a standard column is offered once data exists — and with server
        #: paging that must be a property of the **view**, not of the page on screen,
        #: so the core answers it (`GET /objects/attribute-usage`) and the loadable rows
        #: only add what the sample did not cover.
        usage_names: set[str] = set()
        try:
            usage_names = await fetch_usage_names()
        except CoreError, httpx.HTTPError:  # degrade to the loaded rows
            usage_names = set()
        present_names = present_attribute_names(objects) | usage_names
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
        # Which core field a column sorts by (`None` = not sortable). The core sorts the
        # **view**, so a column either has such a field or the header offers no sorting at
        # all — a sort that only reordered the loaded page would mean something else on
        # the next page (a computed column like `_completeness` has no stored field).
        server_sort: dict[str, str] = {}
        for column in column_defs:
            field = str(column.get("field") or "")
            core_field = sort_field(
                field,
                definitions=definitions_by_name,
                locale=locale,
                languages=[str(row.get("code") or "") for row in languages],
            )
            column["sortable"] = core_field is not None
            if core_field:
                server_sort[field] = core_field
        #: Set while the column state is applied programmatically: AG Grid reports that as
        #: a sort change, and it must not start a reload of its own.
        suppress_sort: dict[str, bool] = {"value": False}

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

        def current_url() -> str:
            """The URL of the current grid view (filters included)."""
            return grid_query.products_url(active)

        def sync_url_query() -> None:
            """Keeps the URL in sync with the active filters (bookmarkable)."""
            ui.run_javascript(f"history.replaceState(null, '', '{current_url()}')")

        async def start_sequential() -> None:
            """Opens the selected rows one after another in the product form."""
            try:
                selected_rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                selected_rows = []
            ids = ids_from_rows(selected_rows)
            if not ids:
                ui.notify(t("seq.no_selection"), color="warning")
                return
            edit_queue.start_queue(ids, current_url())
            ui.navigate.to(f"/products/{quote(ids[0], safe='')}")

        async def apply_rows(selected_objects: list[dict]) -> None:
            nonlocal original_by_id, all_rows, loaded_rows_by_id, loaded_objects
            original_by_id = {str(o.get("id")): o for o in selected_objects}
            all_rows = make_rows(selected_objects)
            loaded_rows_by_id = {str(row["_id"]): dict(row) for row in all_rows}
            loaded_objects = list(selected_objects)
            grid.run_grid_method("setGridOption", "rowData", all_rows)
            selection_hint.set_visibility(len(selected_objects) == 0 and not has_filter())
            no_objects_label.set_visibility(len(selected_objects) == 0 and has_filter())
            update_pager_controls()

        async def reload_grid(*, reset_page: bool = True) -> None:
            """Fetches the view's current page and total (no page reload).

            A filter change starts at page 1 (`reset_page`), a page step keeps the page it
            was given. Rows and total are two requests against the same filter chain
            (`grid_query`), and the core shares one completeness walk between them.
            """
            if reset_page:
                active["page"] = None
                view_state["total"] = None
            load_seq["value"] += 1
            seq = load_seq["value"]
            try:
                new_objects = await fetch_filtered()
                total = await fetch_total()
            except CoreError, httpx.HTTPError:  # keep the current rows
                ui.notify(t("grid.load_failed"), color="negative")
                return
            if seq != load_seq["value"]:
                return  # a newer reload already applied its rows
            view_state["total"] = total
            await apply_rows(new_objects)
            sync_url_query()

        async def load_page(page: int) -> None:
            """Jumps to a page of the current view (`grid_query.PAGE_SIZE` rows each)."""
            if not has_filter() or active["q"]:
                return
            total = view_state["total"]
            target = max(1, page)
            if total is not None:
                target = min(target, grid_query.page_count(total))
            if target == grid_query.page_number(active):
                return
            active["page"] = None if target == 1 else str(target)
            await reload_grid(reset_page=False)

        async def handle_sort_changed() -> None:
            """Sorts the **view** when the user clicks a column header.

            AG Grid sorts the rows it holds (all of them: one page), but which rows are on
            a page is the server's decision — a sort that only reordered the loaded page
            would silently mean something else on the next page. The core sorts on the
            stored field (`sort_field`), so a column without one is not sortable at all
            (`column_defs`) and never reaches this handler.
            """
            if suppress_sort["value"] or not has_filter() or active["q"]:
                return
            try:
                state = await grid.run_grid_method("getColumnState")
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                return
            sorted_columns = [
                column
                for column in (state or [])
                if isinstance(column, dict) and column.get("sort")
            ]
            if not sorted_columns:
                # The third click on a header clears the sort: back to the core's order.
                if not active["sort"]:
                    return
                active.update(sort=None, order=None)
            else:
                first = sorted_columns[0]
                field = server_sort.get(str(first.get("colId") or ""))
                if not field:
                    return
                order = "desc" if str(first.get("sort")) == "desc" else "asc"
                if (active["sort"], active["order"]) == (field, order):
                    return
                active.update(sort=field, order=order)
            await reload_grid()

        async def on_sort_changed(_event) -> None:
            """AG Grid event → the view's sort (see `handle_sort_changed`)."""
            await handle_sort_changed()

        def restore_sort_state() -> None:
            """Shows the view's `?sort=` in the grid's header.

            The rows already arrive ordered by the core; this only sets the column state,
            so the header shows which field the view is sorted by and the next click on it
            continues the asc/desc cycle. AG Grid reports the programmatic change as a
            sort change, hence `suppress_sort`.
            """
            field = active["sort"]
            if not field:
                return
            column = next(
                (
                    str(candidate.get("field") or "")
                    for candidate in column_defs
                    if server_sort.get(str(candidate.get("field") or "")) == field
                ),
                "",
            )
            if not column:
                return
            suppress_sort["value"] = True
            try:
                grid.run_grid_method(
                    "applyColumnState",
                    {
                        "state": [{"colId": column, "sort": active["order"] or "asc"}],
                        "defaultState": {"sort": None},
                    },
                )
            finally:
                suppress_sort["value"] = False

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
            patches = staged_patches(
                staged=staged_cells,
                originals=original_by_id,
                live_rows=rows_by_id,
                loaded_rows=loaded_rows_by_id,
                definitions=editable_defs,
                locale=locale,
                enum_resolvers=enum_resolvers,
            )
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
        # The picker (state, sync and dialog) lives in
        # `meadow/frontend/column_picker.py`. `visible_fields` is its single source of
        # truth — the page holds the same set object for reading (export, views).
        #: The field prefix of every attribute with language columns (`name` uses
        #: `_name`, attributes `attr_<name>`) -> `{language: field}`.
        language_fields_by_prefix = {
            prefix: {language: language_field(prefix, language) for language in active_languages}
            for prefix in language_prefixes.values()
        }

        def apply_visibility(fields: set[str]) -> None:
            for column in column_defs:
                grid.run_grid_method(
                    "setColumnsVisible", [column["field"]], column["field"] in fields
                )

        def set_column_visibility(field: str, visible: bool) -> None:
            """Shows or hides **one** column in the grid (the picker's single change)."""
            grid.run_grid_method("setColumnsVisible", [field], visible)

        def remember_columns() -> None:
            """Persists the visible column set of the current user."""
            app.storage.user[COLUMNS_STORAGE_KEY] = sorted(visible_fields)

        def set_visibility(fields: set[str]) -> None:
            """Applies a column set (the picker syncs and persists it)."""
            picker.set_visibility(fields)

        def fit_to_view() -> None:
            """Fits the columns into the available width."""
            grid.run_grid_method("sizeColumnsToFit")

        def fit_to_content() -> None:
            """Sizes every column to its content (capped at `MAX_CONTENT_CHARS`)."""
            grid.run_grid_method("setColumnWidths", content_widths(column_defs, all_rows))

        # The user's named column sets: the dialog lives in
        # `meadow/frontend/column_views.py`, the page supplies the visible fields and
        # how a loaded view is applied.
        column_views = ColumnViews(
            get_fields=lambda: visible_fields,
            apply_fields=set_visibility,
        )
        column_views.build()

        # Reference picker (modal): display + add/remove an object's references.
        # The dialog lives in `meadow/frontend/references_dialog.py`; the page supplies
        # the aggregation relation types by record key (the wizard needs them too).
        relation_def_by_key: dict[str, dict] = {}
        for definition in relation_definitions:
            if str(definition.get("kind")) != "aggregation":
                continue
            # The core's `create_reference` expects the definition's short
            # record key (e.g. `rel_category_member`, from the record id
            # `relation_definition:rel_category_member`), not the full id or
            # the display `name` (which differs, e.g. `category_member`).
            relation_def_by_key[definition_key(definition)] = definition

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

        references_dialog = ReferencesDialog(
            client=client,
            token=token,
            locale=locale,
            relation_definitions=relation_def_by_key,
        )
        references_dialog.build()

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
                    await references_dialog.open(data["_id"], str(data.get("_type") or ""))
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

        # --- Bulk-Edit-Wizard (Phase 7) -------------------------------------
        bulk_selected_ids: set[str] = set()

        async def handle_selection(event) -> None:
            try:
                selected_rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                selected_rows = []
            bulk_selected_ids.clear()
            for row in selected_rows:
                oid = row_id(row)
                if oid:
                    bulk_selected_ids.add(oid)
            bulk_wizard.set_selection(bulk_selected_ids)
            replace_dialog.set_selection(bool(bulk_selected_ids))

        # Target selection for the bulk wizard: the own attributes only — an
        # imported standard's vocabulary is not a maintenance target
        # (`meadow/frontend/reference_data.py`).
        bulk_attr_options = {
            str(definition.get("name")): str(definition.get("name"))
            for definition in definitions
            if definition.get("name") and visible_in_picker(definition)
        }

        async def view_ids() -> list[str]:
            """Every object id of the current **view** (the grid holds one page of it).

            The scope "current filter" of the export dialog and of the bulk wizard means
            the view, not the rows on screen: with server paging those are one page, and
            an export or a bulk run over a single page would silently leave the rest of
            the view untouched. The core pages through `GET /objects` (`limit` caps at
            1000, `grid_query.VIEW_PAGE`) until a short page says the view is complete.
            """
            if not has_filter():
                return []
            ids: list[str] = []
            while True:
                chunk = await client.list_objects(
                    token,
                    **grid_query.list_arguments(
                        active,
                        has_channels=False,
                        locale=locale,
                        start=len(ids),
                        limit=grid_query.VIEW_PAGE,
                    ),
                )
                # `GET /objects` answers core objects (`id`), not grid rows (`_id`).
                for row in chunk:
                    object_id = row.get("id") if isinstance(row, dict) else None
                    if object_id:
                        ids.append(str(object_id))
                if len(chunk) < grid_query.VIEW_PAGE:
                    return ids

        async def _current_page_ids() -> list[str]:
            """The object ids of the page on screen (the grid shows exactly one)."""
            return ids_from_rows(all_rows)

        async def _scope_ids(scope: str) -> list[str]:
            """Resolves a selection scope (`filtered`/`page`/`selected`) to ids."""
            if scope == "selected":
                rows: list = await grid.get_selected_rows()
            elif scope == "page":
                return await _current_page_ids()
            elif scope == "filtered":
                return await view_ids()
            else:
                rows = all_rows
            return ids_from_rows(rows)

        # The bulk-edit wizard owns its dialog, its configuration area and its run
        # (`meadow/frontend/bulk_wizard.py`); the page supplies the catalogs and
        # how a scope resolves to object ids.
        bulk_wizard = BulkWizard(
            client=client,
            token=token,
            locale=locale,
            catalogs=WizardCatalogs(
                rows=all_rows,
                filtered_total=view_state["total"] or len(all_rows),
                attributes=bulk_attr_options,
                relation_options=bulk_relation_options,
                relation_definitions=relation_def_by_key,
                category_options=category_bulk_options,
                taxonomy_types=taxonomy_types,
                channel_labels=channel_labels,
            ),
            resolve_ids=_scope_ids,
        )
        bulk_wizard.build()

        # --- Find & Replace (Phase 9) ---------------------------------------
        # Replaces a literal text in the string-valued attributes (`text`, `url`)
        # of the selection. It runs as the `replace_text` bulk operation (same
        # scope/progress/report machinery as the wizard) so large selections are
        # processed in the worker instead of blocking the page. The dialog lives in
        # `meadow/frontend/replace_dialog.py`.
        replace_dialog = ReplaceDialog(
            client=client,
            token=token,
            attr_options={
                "": t("grid.replace_all_attributes"),
                "name": t("grid.name"),
                **replace_target_options(definitions, locale),
            },
            scope_ids=_scope_ids,
            count=lambda: view_state["total"] or len(all_rows),
            initial_scope="selected" if bulk_selected_ids else "filtered",
            on_started=bulk_wizard.poll,
        )
        replace_dialog.build()

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
            """Renders the navigation tree of `view` (see `frontend/tree_panel.py`)."""
            nonlocal current_tree
            if host is None:
                return
            rendered = tree_panel.render(view)
            current_tree = {"tree": rendered.tree, "nodes": rendered.nodes}

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

        async def _selected_ids() -> list[str]:
            """The ids of the rows the user selected in the grid."""
            try:
                selected: list = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                return []
            return ids_from_rows(selected)

        # The dialog lives in `meadow/frontend/export_dialog.py`; it resolves both scopes
        # to ids on the page, so the core never re-evaluates a filter.
        export_dialog = ExportDialog(
            client=client,
            token=token,
            locale=locale,
            fields=_export_fields,
            filtered_ids=view_ids,
            selected_ids=_selected_ids,
            count=lambda: view_state["total"] or len(all_rows),
            has_selection=bool(bulk_selected_ids),
        )
        export_dialog.build()

        with ui.splitter(value=25, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            # Left: resizable tree card (structure or categories via toggle).
            with splitter.before, ui.card().classes("w-full h-full p-0"):  # noqa: SIM117 - layout nesting
                # Line 1: view selector as icon buttons (active highlighted).
                view_switch = ViewSwitch()

                def set_view(view: str) -> None:
                    active_view["value"] = view
                    view_switch.apply(view)
                    if view != "collection" and active["collection"]:
                        active["collection"] = None
                        active_collection["id"] = None
                        sync_url_query()
                    render_tree(view)

                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    with ui.button_group().props("dense"):
                        structure_btn = (
                            ui.button(icon="account_tree")
                            .tooltip(t("grid.structure"))
                            .mark("view-structure")
                        )
                        category_btn = (
                            ui.button(icon="category")
                            .tooltip(t("grid.category"))
                            .mark("view-category")
                        )
                        collection_btn = (
                            ui.button(icon="collections_bookmark")
                            .tooltip(t("grid.collection"))
                            .mark("view-collection")
                        )
                    structure_btn.on_click(lambda: set_view("structure"))
                    category_btn.on_click(lambda: set_view("category"))
                    collection_btn.on_click(lambda: set_view("collection"))
                # Line 2: the category controls. They get a **row of their own**: next
                # to the view buttons three selectors (taxonomy, tree, dimension) plus
                # their help buttons were one dense line — and the row hides as a whole
                # outside the category view, so no empty gap is left behind.
                with (
                    ui.row()
                    .classes("items-center gap-2 w-full")
                    .style("flex-wrap: wrap") as category_row
                ):
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
                        .mark("taxonomy-select")
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
                        .mark("tree-select")
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
                        .mark("dimension-select")
                    )
                    dimension_help = info_button("grid.category_dimension_hint", "product-grid")
                    dimension_select.on_value_change(lambda e: change_dimension(e.value))
                    # A dimension chosen via `?definition_id=` may be bound to a
                    # taxonomy: apply it to the switcher once the switcher exists.
                    apply_dimension_taxonomy(dimension["key"])
                # Line 3: filter + action button (collapse for structure/category,
                # collection reload for the flat Collections view).
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    create_btn = (
                        ui.button(icon="add", on_click=lambda: create_dialog.open())
                        .props("flat dense shrink-0")
                        .mark("create-open")
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
                tree_panel = TreePanel(
                    host=host,
                    locale=locale,
                    state=lambda: TreeState(
                        collections=collections,
                        category_roots=category_roots,
                        roots_have_more=bool(roots_has_more),
                        uncategorized=uncategorized_node,
                        selected_id=selected_tree["id"],
                        structure_nodes=tree_nodes,
                        hits=tree_override,
                        hits_query=str(tree_query["value"]),
                        path=tree_path,
                    ),
                    load_children=load_children,
                    load_category_children=load_category_children,
                    on_tree_select=handle_tree_select,
                    on_category_select=handle_category_select,
                    on_collection_select=handle_collection_select,
                    select_category=select_category,
                    show_in_tree=show_in_tree,
                )
                # The panel controls belong to specific views (`view_switch.py`): the
                # taxonomy/dimension switcher and the tree filter to the category view,
                # collapse to any tree, reload to the flat collection list, create to the
                # structure. The switch reads the two facts it needs lazily.
                panel_widgets: dict[str, ui.element] = {
                    "category_row": category_row,
                    "taxonomy_help": taxonomy_help,
                    "tree_select": tree_select,
                    "dimension_select": dimension_select,
                    "dimension_help": dimension_help,
                    "collapse": collapse_btn,
                    "reload": reload_btn,
                    "create": create_btn,
                }
                if taxonomy_select is not None:
                    panel_widgets["taxonomy_select"] = taxonomy_select
                view_switch.bind(
                    buttons={
                        "structure": structure_btn,
                        "category": category_btn,
                        "collection": collection_btn,
                    },
                    widgets=panel_widgets,
                    has_tree=lambda: bool(tree_nodes or category_roots),
                    has_create=lambda: bool(create_types),
                )
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
                    ui.button(icon="bookmarks", on_click=column_views.open).props("outline").mark(
                        "views-open"
                    ).tooltip(t("grid.views"))
                    ui.button(icon="fact_check", on_click=bulk_wizard.open).props("outline").mark(
                        "bulk-open"
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
                    # staying **one** column (the picker keeps every box of a field
                    # in sync). The per-language columns are a **hybrid** like the
                    # product form: the attribute's own box is the working-language
                    # column, the `translate` button opens its language columns.
                    # State, sync and dialog live in `meadow/frontend/column_picker.py`.
                    picker = ColumnPicker(
                        locale=locale,
                        visible=visible_fields,
                        apply_set=apply_visibility,
                        set_field=set_column_visibility,
                        persist=lambda fields: remember_columns(),
                        catalogs=PickerCatalogs(
                            column_fields=frozenset(
                                str(column.get("field") or "") for column in column_defs
                            ),
                            name_prefix=NAME_FIELD_PREFIX,
                            definitions=definitions_by_name,
                            own_definitions=own_definitions,
                            groups=groups_by_id,
                            language_fields=language_fields_by_prefix,
                            language_labels=language_labels,
                            language_prefixes=language_prefixes,
                            reference_definitions=reference_definitions,
                            hidden_standard=sum(
                                1 for definition in definitions if definition.get("reference_data")
                            )
                            - len(reference_definitions),
                        ),
                    )

                    # Column picker as a dialog (not a `ui.menu`): Quasar's QMenu
                    # auto-open doesn't fire in Firefox/Zen, while `ui.dialog` opens
                    # reliably in all browsers (same pattern as the working "views").
                    columns_dialog = picker.build()
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
                # The dialog itself (types, business keys, parent caption) lives in
                # `meadow/frontend/create_object.py`; the page supplies the registry,
                # the attribute definitions and the composition parent.
                create_dialog: CreateObjectDialog = CreateObjectDialog(
                    client=client,
                    token=token,
                    locale=locale,
                    types=create_types,
                    definitions=definitions_by_name,
                    languages=languages,
                    get_parent=lambda: active["ancestor"],
                )
                create_dialog.build()

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
                # The condition form and its dialog live in
                # `meadow/frontend/where_dialog.py`; the page owns the filter state
                # (`active["where"]`), the chip in the filter bar and the reload.
                def update_where_chip() -> None:
                    """Shows/updates the chip of the active advanced search."""
                    is_active = bool(active["where"])
                    where_chip.set_text(
                        t("grid.advanced_active", summary=where_dialog.summary(active["where"]))
                        if is_active
                        else ""
                    )
                    where_chip.set_visibility(is_active)

                where_dialog = WhereDialog(
                    client=client,
                    token=token,
                    get_where=lambda: active["where"],
                    set_where=lambda value: active.update(where=value),
                    on_applied=update_where_chip,
                    reload=reload_grid,
                )
                where_dialog.build()

                with ui.row().classes("items-center gap-2 w-full pb-1").style("flex-wrap: nowrap"):
                    search_input = (
                        ui.input(
                            placeholder=t("grid.filter_search"),
                            value=active["q"] or "",
                        )
                        .props(f"outline dense clearable debounce={SEARCH_DEBOUNCE_MS}")
                        .classes("grow")
                        .mark("filter-search")
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
                    ).mark("filter-reset").tooltip(t("grid.filter_reset"))
                    ui.button(icon="filter_alt", on_click=lambda: where_dialog.open()).props(
                        "flat dense shrink-0"
                    ).mark("advanced-open").tooltip(t("grid.advanced_open"))
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
                    ui.button("", on_click=lambda: where_dialog.open())
                    .props("flat dense no-caps")
                    .classes("text-caption text-primary pb-1")
                    .tooltip(t("grid.advanced_open"))
                )
                where_chip.set_visibility(False)
                update_where_chip()

                grid = (
                    ui.aggrid(
                        {
                            "columnDefs": column_defs,
                            "rowData": all_rows,
                            "rowSelection": {"mode": "multiRow"},
                            # The rows are already one **page** of the view (the core
                            # ordered and sliced it): AG Grid's own pagination would page
                            # through that page and report its size as the total. The
                            # pager below the grid is the view's, and the header click
                            # sorts the view on the server (`handle_sort_changed`).
                            "pagination": False,
                            # Header tooltips via the native browser tooltip (`title`):
                            # AG Grid's own tooltip component does not show up here.
                            "enableBrowserTooltips": True,
                        },
                        html_columns=[
                            i
                            for i, column in enumerate(column_defs)
                            if column["field"] in ("_status", "_completeness", "_releases")
                        ],
                    )
                    .classes("w-full grow")
                    .mark("objects-grid")
                )
                grid.on("cellValueChanged", mark_changed, ["data", "colId"])
                grid.on("cellClicked", handle_cell_clicked, ["data", "colId"])
                grid.on("cellDoubleClicked", handle_open_cell, ["data", "colId"])
                grid.on("selectionChanged", handle_selection, ["selectedRows"])
                grid.on("sortChanged", on_sort_changed)
                # Restore the column set the user had before (reload, browser-back
                # from the form, re-login) — the columns start hidden per definition.
                apply_visibility(visible_fields)
                # A sorted view goes through the URL: show the arrow of the header the
                # view is sorted by (the data itself is already ordered by the core).
                restore_sort_state()
                # The view's page controls (page/range/enabled steps).
                update_pager_controls()

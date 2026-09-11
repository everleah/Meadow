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
import os
import re
import tempfile
from urllib.parse import quote

from fastapi import Request
from nicegui import app, ui

from meadow.frontend import config, edit_queue
from meadow.frontend.api import CoreClient, is_session_expired
from meadow.frontend.auth import handle_session_expired
from meadow.frontend.display import completeness_badge, resolve_value, status_badge
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.lazy_tree import LazyTree
from meadow.frontend.object_picker import OBJECT_ROOT_ID, open_object_picker, render_picker_hit

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
# Relation types that have a dedicated bulk operation and are therefore hidden in
# the generic relation selection: composition (`change_parent`), category
# assignment (`add_category`/`remove_category`).
_DEDICATED_RELATION_DEFS = ("rel_depends_on", "rel_category_member")
# Field of the per-row inheritance markers (`{field: "inherited from …"}`) and
# the AG Grid column options that render them: NiceGUI converts `:`-prefixed
# keys into real JavaScript functions on the client (`convertDynamicProperties`),
# so an inherited cell is dimmed/italic and carries the source as browser tooltip.
INHERITED_FIELD = "_inherited"
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
EDITABLE_TYPES = ("text", "number", "boolean", "enum", "date")


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


def _is_multilingual_map(value: object) -> bool:
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
        columns.append(column)
        if not definition.get("multilingual"):
            continue
        for language in languages or []:
            field = language_field(f"attr_{name}", language)
            columns.append(_language_column(f"{label} · {language.upper()}", field))
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
    cells: dict = {}
    for name in sorted(language_prefixes or {}):
        value = attributes.get(name)
        for language in languages or []:
            resolver = (enum_resolvers or {}).get(language, {}).get(name)
            field = language_field(language_prefixes[name], language)
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
    for column in column_defs:
        field = str(column.get("field") or "")
        if field not in visible_fields:
            continue
        if field in _EXPORT_BASE_FIELDS:
            fields.append(_EXPORT_BASE_FIELDS[field])
        elif (language_fields or {}).get(field):
            fields.append(language_fields[field])
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
    label = resolve_value(attributes.get("name"), locale) or obj_type or node_id
    node: dict = {"id": node_id, "label": label, "type": obj_type}
    if object_.get("hasChildren"):
        node["lazy"] = True
    if "memberCount" in object_:
        node["count"] = object_.get("memberCount")
    return node


def build_patch(object_id: str, client_name: object, original: dict, locale: str) -> dict:
    """Builds a batch-update item for an edited name value.

    Preserves other locales when the attribute is multilingual; otherwise the
    scalar name is replaced.
    """
    original_name = (original.get("attributes") or {}).get("name")
    if isinstance(original_name, dict):
        new_name = dict(original_name)
        new_name[locale] = client_name
    else:
        new_name = client_name
    return {"id": object_id, "attributes": {"name": new_name}}


async def _wait_export(client: CoreClient, token: str, run_id: str) -> dict:
    """Polls an export job run until it reaches a terminal state."""
    for _ in range(120):
        try:
            runs = await client.list_job_runs(token, ids=[run_id], limit=1)
        except Exception:  # noqa: BLE001 - keep polling on a transient error
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


@ui.page("/products", response_timeout=10)
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
        "collection": request.query_params.get("collection") or None,
        "uncategorized": request.query_params.get("uncategorized") == "1",
        "q": request.query_params.get("q") or None,
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
            or active["q"]
        )

    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.products")).classes("text-h4")
        # Prompt shown when the grid is empty because no node/filter is active.
        selection_hint = ui.label(t("grid.select_tree_node")).classes(
            "text-subtitle1 text-gray-500 pb-2"
        )
        client = CoreClient(config.CORE_URL)
        token = app.storage.user.get("token", "")

        async def fetch_filtered() -> list[dict]:
            """Loads the objects matching the active filters (see `active`)."""
            if not has_filter():
                return []
            if active["q"]:
                # Server-side full-text search (the grid may be empty otherwise):
                # search the labels, then load the hits as full grid rows.
                hits = await client.picker_search(
                    token,
                    active["q"],
                    types=[active["type"]] if active["type"] else None,
                    locale=locale,
                    limit=SEARCH_LIMIT,
                )
                ids = [str(hit.get("id")) for hit in hits if hit.get("id")]
                if not ids:
                    return []
                return await client.list_objects(
                    token,
                    ids=ids,
                    missing_required=bool(active["incomplete"]),
                    locale=locale,
                    resolve_inheritance=True,
                    limit=SEARCH_LIMIT,
                )
            return await client.list_objects(
                token,
                object_type=active["type"],
                ancestor=active["ancestor"],
                category=active["category"],
                collection=active["collection"],
                uncategorized=bool(active["uncategorized"]),
                missing_required=bool(active["incomplete"]),
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
        except Exception:  # noqa: BLE001 - keep the page usable (filter bar + grid)
            ui.notify(t("grid.load_failed"), color="negative")
        no_objects_label = ui.label(t("grid.no_objects")).classes("text-subtitle1")
        no_objects_label.set_visibility(has_filter() and not objects)
        selection_hint.set_visibility(not has_filter())

        try:
            definitions = await client.list_attribute_definitions(token)
        except Exception:  # noqa: BLE001 - degrade to base columns on failure
            definitions = []
        attribute_names = [
            str(definition.get("name")) for definition in definitions if definition.get("name")
        ]

        # Per-language columns (Sprach-Spalten): the working-language column
        # (`attr_<name>`) is joined by one hidden column per active data language
        # for every multilingual attribute. Only `active` languages are offered,
        # mirroring the product form.
        active_languages: list[str] = []
        try:
            languages = await client.list_languages(token)
            active_languages = [
                str(lang.get("code"))
                for lang in languages
                if lang.get("code") not in (None, "root", locale) and lang.get("active", True)
            ]
        except Exception:  # noqa: BLE001 - degrade to a single-language grid
            active_languages = []
        multilingual_names = {
            str(definition.get("name"))
            for definition in definitions
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
        # `is_editable_definition`).
        editable_defs: dict[str, dict] = {
            str(definition.get("name")): definition
            for definition in definitions
            if definition.get("name") and is_editable_definition(definition)
        }
        # Attributes that participate in inheritance: an own empty value there
        # blocks the inherited one (marked as "interrupted" in the grid).
        inheritable_names = [
            str(definition.get("name"))
            for definition in definitions
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
        enum_defs = [d for d in definitions if d.get("type") == "enum"]
        if enum_defs:
            try:
                enum_sets = await client.list_enum_sets(token)
            except Exception:  # noqa: BLE001 - degrade to raw codes on failure
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
        attribute_column_defs, attribute_language_fields = attribute_column_specs(
            definitions, locale, active_languages, enum_resolvers
        )
        # The working-language attribute columns carry the inheritance marker
        # (dimmed + source tooltip) for values resolved from an ancestor.
        for column in attribute_column_defs:
            field = column.get("field")
            if isinstance(field, str) and LANG_SEPARATOR not in field:
                column[":cellStyle"] = INHERITED_CELL_STYLE
                column[":tooltipValueGetter"] = INHERITED_CELL_TOOLTIP
                column[":cellRenderer"] = INHERITED_CELL_RENDERER
        language_fields = {**name_language_fields, **attribute_language_fields}
        column_defs = with_header_tooltips(
            [
                {"headerName": t("grid.id"), "field": "_id", "width": 100, "pinned": "left"},
                {"headerName": t("grid.name"), "field": "_name", "editable": True},
                *name_column_defs,
                {"headerName": t("grid.type"), "field": "_type", "width": 120},
                {"headerName": t("grid.status"), "field": "_status", "width": 140},
                {"headerName": t("grid.completeness"), "field": "_completeness", "width": 140},
            ]
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
            children = await client.list_object_children(token, node_id, limit=100)
            return [tree_node_from_object(child, locale) for child in children]

        try:
            top_children = await client.list_object_children(token, root_id, limit=100)
            tree_nodes = [tree_node_from_object(child, locale) for child in top_children]
        except Exception:  # noqa: BLE001 - degrade to a grid without the tree
            tree_nodes = []

        def make_rows(selected_objects: list[dict]) -> list[dict]:
            """Builds the grid rows: display values, typed editable cells, languages."""
            return [
                row_data(object_, locale, attribute_names, enum_resolvers)
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
            if active["collection"]:
                parts.append(f"collection={quote(str(active['collection']))}")
            if active["uncategorized"]:
                parts.append("uncategorized=1")
            if active["q"]:
                parts.append(f"q={quote(str(active['q']))}")
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
            except Exception:  # noqa: BLE001 - degrade to "nothing selected"
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
            except Exception:  # noqa: BLE001 - keep the current rows
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
        # Special "Nicht kategorisiert" entry (lists objects with no category_member).
        uncategorized_id = "__uncategorized__"
        uncategorized_node = {"id": uncategorized_id, "label": t("grid.uncategorized")}

        async def load_category_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(
                token, node_id, limit=100, count_members=True
            )
            return [
                tree_node_from_object(child, locale)
                for child in children
                if child.get("type") == "category"
            ]

        try:
            top_children = await client.list_object_children(
                token, root_id, limit=100, count_members=True
            )
            category_roots = [
                tree_node_from_object(child, locale)
                for child in top_children
                if child.get("type") == "category"
            ]
        except Exception:  # noqa: BLE001 - degrade to a structure-only panel
            category_roots = []

        async def handle_category_select(e) -> None:
            selected = e.value if hasattr(e, "value") else None
            active.update(ancestor=None, category=None, collection=None, uncategorized=False)
            if selected == uncategorized_id:
                active["uncategorized"] = True
            elif selected:
                active["category"] = selected
            await reload_grid()

        # Materialized collections (flat, no tree): a collection view filters the
        # grid via `?collection=<id>` (collection_member aggregation).
        collections: list[dict] = []
        try:
            collections = await client.list_collections(token, limit=1000)
        except Exception:  # noqa: BLE001 - degrade to an empty collection list
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
            except Exception:  # noqa: BLE001 - show a friendly message on failure
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

        # Personal views (step 4b): save/restore the visible column set per user.
        views = app.storage.user.get("views", {})
        default_fields = {"_id", "_name", "_type", "_status", "_completeness", "_refs"}
        # The **current** column set is persisted as well, so it survives a
        # browser-back from the product form, a reload and a re-login (only the
        # authentication is cleared on logout, see `auth.clear_session`).
        known_fields = {str(column.get("field") or "") for column in column_defs}
        restored = restore_columns(app.storage.user.get(COLUMNS_STORAGE_KEY), known_fields)
        visible_fields: set[str] = restored or set(default_fields)
        checkbox_refs: dict[str, ui.checkbox] = {}

        def apply_visibility(fields: set[str]) -> None:
            for column in column_defs:
                grid.run_grid_method(
                    "setColumnsVisible", [column["field"]], column["field"] in fields
                )

        def remember_columns() -> None:
            """Persists the visible column set of the current user."""
            app.storage.user[COLUMNS_STORAGE_KEY] = sorted(visible_fields)

        def set_visibility(fields: set[str]) -> None:
            """Applies a column set, syncs the picker checkboxes and persists it."""
            visible_fields.clear()
            visible_fields.update(fields)
            apply_visibility(visible_fields)
            for column in column_defs:
                checkbox = checkbox_refs.get(str(column.get("field") or ""))
                if checkbox is not None:
                    checkbox.value = column["field"] in visible_fields
            remember_columns()

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
                    ui.button(name, on_click=lambda n=name: load_view(n)).props("flat dense")  # type: ignore
                    ui.button(icon="delete", on_click=lambda n=name: delete_view(n)).props(  # type: ignore
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
        try:
            for definition in await client.list_relation_definitions(token):
                if str(definition.get("kind")) != "aggregation":
                    continue
                # The core's `create_reference` expects the definition's short
                # record key (e.g. `rel_category_member`, from the record id
                # `relation_definition:rel_category_member`), not the full id or
                # the display `name` (which differs, e.g. `category_member`).
                def_key = str(definition["id"]).split(":", 1)[-1]
                relation_def_by_key[def_key] = definition
                relation_options[def_key] = (
                    resolve_value(definition.get("label"), locale) or def_key
                )
        except Exception:  # noqa: BLE001 - degrade to an empty picker
            relation_options = {}
            relation_def_by_key = {}

        # The bulk wizard assigns relations generically; types with a dedicated
        # operation (parent, categories) are hidden there to avoid duplicates.
        bulk_relation_options = {
            key: label
            for key, label in relation_options.items()
            if key not in _DEDICATED_RELATION_DEFS
        }

        current_ref_object: dict = {"id": None}

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
                    exclude_ids=[str(current_ref_object["id"] or "")],
                ),
            ).props("outline dense").tooltip(t("form.pick_in_structure"))
            ref_hits = ui.column()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=ref_dialog.close).props("flat dense")

        async def remove_reference(reference_id: str) -> None:
            try:
                await client.delete_reference(token, reference_id)
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.ref_remove_failed"), color="negative")
            await refresh_references()

        async def refresh_references() -> None:
            ref_chips.clear()
            target = current_ref_object["id"]
            if not target:
                return
            try:
                references = await client.list_object_references(token, target, locale=locale)
            except Exception:  # noqa: BLE001 - degrade to no chips
                references = []
            # Resolve relation-type labels for the group headings (all kinds,
            # including the composition `rel_depends_on`).
            def_labels: dict[str, str] = {}
            try:
                for definition in await client.list_relation_definitions(token):
                    dk = str(definition["id"]).split(":", 1)[-1]
                    def_labels[dk] = resolve_value(definition.get("label"), locale) or dk
            except Exception:  # noqa: BLE001 - fall back to raw keys
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
                                    on_click=lambda rid=chip["id"]: remove_reference(rid),  # type: ignore
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
            except Exception:  # noqa: BLE001 - show a friendly message
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
                hits = await client.picker_search(token, q, locale=locale, limit=20)
            except Exception:  # noqa: BLE001 - degrade to no hits
                return
            for hit in hits:
                with ref_hits:
                    render_picker_hit(hit, add_reference, locale)

        async def open_references(object_id: str) -> None:
            current_ref_object["id"] = object_id
            ref_object_label.set_text(object_id)
            ref_definition.value = next(iter(relation_options), None) if relation_options else None
            ref_search.value = ""
            ref_hits.clear()
            await refresh_references()
            ref_dialog.open()

        async def handle_ref_cell_clicked(e) -> None:
            args = e.args if isinstance(e.args, dict) else {}
            if args.get("colId") == "_refs":
                data = args.get("data")
                if data and data.get("_id"):
                    await open_references(data["_id"])

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

        ref_search.on_value_change(lambda e: search_references(e.value))  # type: ignore

        # --- Bulk-Edit-Wizard (Phase 7) -------------------------------------
        bulk_selected_ids: set[str] = set()
        bulk_selected_label: ui.element | None = None
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
            except Exception:  # noqa: BLE001 - degrade if the grid is not ready
                selected_rows = []
            bulk_selected_ids.clear()
            for row in selected_rows:
                oid = _row_id(row)
                if oid:
                    bulk_selected_ids.add(oid)
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

        bulk_attr_options = {name: name for name in attribute_names}
        bulk_status_options = {
            "draft": "draft",
            "in_review": "in_review",
            "published": "published",
            "archived": "archived",
        }
        bulk_fields: dict[str, ui.element] = {}

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
                    target_types = ["category"] if op != "change_parent" else None

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
                            target_types=(definition.get("target_types") or []) or None,
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
                if op in _APPROVAL_MODIFIER_OPS:
                    bulk_fields["send_for_approval"] = ui.switch(
                        t("grid.bulk_send_for_approval")
                    ).tooltip(f"{t('grid.approval_status')}: {APPROVAL_STATUS}")

        async def _current_page_ids() -> list[str]:
            """Return the object ids of the rows on the current grid page."""
            try:
                page = int(await grid.run_grid_method("paginationGetCurrentPage") or 0)
                size = int(await grid.run_grid_method("paginationGetPageSize") or 50)
            except Exception:  # noqa: BLE001 - fall back to the configured page size
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
            elif op == "change_parent":
                parent = bulk_picked.get("parent")
                parameters["parent"] = parent.get("id") if parent else None
            elif op in ("add_relation", "remove_relation"):
                relation = bulk_fields.get("relation")
                parameters["definition_id"] = relation.value if relation else None
                target = bulk_picked.get("relation_target")
                parameters["target"] = target.get("id") if target else None

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
            except Exception:  # noqa: BLE001 - show a friendly message
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
                except Exception:  # noqa: BLE001 - retry on transient errors
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
        # Replaces a literal text in the text attributes of the selection. It
        # runs as the `replace_text` bulk operation (same scope/progress/report
        # machinery as the wizard) so large selections are processed in the
        # worker instead of blocking the page.
        replace_attr_options: dict[str, str] = {"": t("grid.replace_all_attributes")}
        replace_attr_options["name"] = t("grid.name")
        for definition in definitions:
            name = str(definition.get("name") or "")
            if not name or definition.get("type") != "text" or definition.get("computed"):
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
            except Exception:  # noqa: BLE001 - show a friendly message
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

        def apply_tree_filter(value: str) -> None:
            tree = current_tree["tree"]
            if tree is not None:
                tree.set_nodes(filter_tree_nodes(current_tree["nodes"], value))

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
            if view == "collection" and collections:

                async def _no_children(_node_id: str) -> list[dict]:
                    return []

                nodes = [_collection_node(c) for c in collections]
                loader = _no_children
                on_select = handle_collection_select
            elif view == "category" and category_roots:
                selected_id = selected_tree["id"]
                if selected_id:
                    nodes = [r for r in category_roots if r["id"] == selected_id]
                    if not nodes:
                        nodes = category_roots
                else:
                    nodes = category_roots
                nodes = [uncategorized_node, *nodes]
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
            except Exception:  # noqa: BLE001 - keep current data on failure
                pass
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
                except Exception as exc:  # noqa: BLE001 - surface a friendly message
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
                    tree_select.set_visibility(view == "category")
                    collapse_btn.set_visibility(
                        view != "collection" and bool(tree_nodes or category_roots)
                    )
                    reload_btn.set_visibility(view == "collection")
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
                # Line 2: filter + action button (collapse for structure/category,
                # collection reload for the flat Collections view).
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
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
                filter_input.on_value_change(lambda e: apply_tree_filter(e.value or ""))
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

                    def make_toggler(field: str):
                        def toggle(event) -> None:
                            grid.run_grid_method("setColumnsVisible", [field], event.value)
                            if event.value:
                                visible_fields.add(field)
                            else:
                                visible_fields.discard(field)
                            remember_columns()

                        return toggle

                    # Column picker as a dialog (not a `ui.menu`): Quasar's QMenu
                    # auto-open doesn't fire in Firefox/Zen, while `ui.dialog` opens
                    # reliably in all browsers (same pattern as the working "views").
                    columns_dialog = ui.dialog()
                    with columns_dialog, ui.card().classes("w-[22rem] max-w-[92vw]"):
                        ui.label(t("grid.columns")).classes("text-h6")
                        with ui.column().classes("w-full gap-1 max-h-80 overflow-y-auto"):
                            for column in column_defs:
                                field = str(column.get("field") or "")
                                checkbox = ui.checkbox(
                                    column["headerName"], value=field in visible_fields
                                ).on_value_change(make_toggler(field))
                                checkbox_refs[field] = checkbox
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(t("grid.close"), on_click=columns_dialog.close).props("flat")
                    ui.button(icon="view_column").props("outline").tooltip(
                        t("grid.columns")
                    ).on_click(columns_dialog.open)

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

                async def reset_filters() -> None:
                    """Clears the filter bar; the tree/category/collection stays."""
                    active.update(incomplete=False, q=None)
                    suppress_change["value"] = True
                    try:
                        incomplete_switch.value = False
                        search_input.value = ""
                    finally:
                        suppress_change["value"] = False
                    await reload_grid()

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
                    ui.button(icon="filter_alt_off", on_click=reset_filters).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.filter_reset"))
                    # Column sizing: fit the columns into the available width, or
                    # widen every column so its content fits (capped at 30 chars).
                    ui.button(icon="fit_screen", on_click=fit_to_view).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.fit_to_view"))
                    ui.button(icon="straighten", on_click=fit_to_content).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.fit_to_content"))

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
                        if column["field"] in ("_status", "_completeness")
                    ],
                ).classes("w-full grow")
                grid.on("cellValueChanged", mark_changed, ["data", "colId"])
                grid.on("cellClicked", handle_ref_cell_clicked, ["data", "colId"])
                grid.on("cellDoubleClicked", handle_open_cell, ["data", "colId"])
                grid.on("selectionChanged", handle_selection, ["selectedRows"])
                # Restore the column set the user had before (reload, browser-back
                # from the form, re-login) — the columns start hidden per definition.
                apply_visibility(visible_fields)

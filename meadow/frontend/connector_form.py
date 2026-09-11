"""Schema-driven connector parameter form (Stage 1).

The core describes every connector type's parameters (`GET /connector-types`:
groups, fields, types, defaults, enums, conditional visibility) and validates
them on write. This module turns that description into an editable form and
back into a partial `parameters` update:

* **Pure helpers** (unit-tested, no NiceGUI): path access, value
  formatting/parsing, validation, unknown-key detection, patch building and the
  list summary. The form only ever sends the **changed** fields
  (`parameters_mode: merge`), so undeclared keys and masked secrets survive.
* **`render_parameter_form`** builds the widgets for one connector type and
  exposes `read_values()`, `set_values()`, `refresh_visibility()`.

The JSON expert tab stays available in the page; this module is the guided path.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from nicegui import ui

from meadow.frontend.i18n import t

#: Marks "the user did not set this field" (distinct from `None` = delete).
MISSING = object()


def fields_of(schema: dict | None) -> list[dict]:
    """All declared fields of a connector type (groups flattened)."""
    fields: list[dict] = []
    for group in (schema or {}).get("groups") or []:
        fields.extend(group.get("fields") or [])
    return fields


def dotted(path: str) -> list[str]:
    return [part for part in str(path).split(".") if part]


def get_path(parameters: dict | None, key: str) -> Any:
    """Reads a dotted path (`filter.event_types`) from a parameters object."""
    current: Any = parameters or {}
    for part in dotted(key):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def set_path(parameters: dict, key: str, value: Any) -> None:
    """Writes a dotted path, creating parent objects as needed."""
    parts = dotted(key)
    if not parts:
        return
    current = parameters
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def nest(readings: dict[str, Any]) -> dict:
    """Turns `{"a.b": 1}` into `{"a": {"b": 1}}` (keeping `None` values)."""
    nested: dict[str, Any] = {}
    for key, value in readings.items():
        set_path(nested, key, value)
    return nested


def project_fields(schema: dict | None, parameters: dict | None) -> dict[str, Any]:
    """Flat `{path: parsed value}` view of a parameters object.

    This is the **baseline** for change detection: it always mirrors what is
    actually stored, so edits made in the JSON tab are recognised as changes
    even after the form was re-rendered.
    """
    return {
        str(field["key"]): parse_value(field, get_path(parameters or {}, str(field["key"])))
        for field in fields_of(schema)
    }


def default_values(schema: dict | None) -> dict[str, Any]:
    """The declared defaults as a flat `{path: value}` map."""
    defaults: dict[str, Any] = {}
    for field in fields_of(schema):
        if "default" in field:
            defaults[str(field["key"])] = field["default"]
    return defaults


def known_top_level(schema: dict | None) -> set[str]:
    """Top-level parameter keys the schema declares."""
    return {dotted(field["key"])[0] for field in fields_of(schema) if dotted(field["key"])}


def unknown_keys(schema: dict | None, parameters: dict | None) -> list[str]:
    """Top-level keys that no declared field uses (shown as a hint, never removed)."""
    known = known_top_level(schema)
    return sorted(key for key in (parameters or {}) if key not in known)


def is_visible(field: dict, values: dict[str, Any]) -> bool:
    """Evaluates the field's `show_if` condition against the current values.

    `values` is the flat `{path: value}` map of the form.
    """
    condition = field.get("show_if")
    if not isinstance(condition, dict):
        return True
    controlling = values.get(str(condition.get("key") or ""), MISSING)
    return controlling is not MISSING and controlling == condition.get("equals")


def format_value(field: dict, value: Any) -> Any:
    """Stored value → widget value."""
    kind = str(field.get("type") or "string")
    widget = str(field.get("widget") or "")
    empty = value is MISSING or value is None
    if widget == "switch":
        return bool(value) if not empty else False
    if widget == "multiselect":
        return list(value) if isinstance(value, list) else []
    if widget == "key_value":
        return dict(value) if isinstance(value, dict) else {}
    if widget == "number":
        return None if empty else value
    if kind == "string" and empty:
        return ""
    return "" if empty else value


def parse_value(field: dict, raw: Any) -> Any:
    """Widget value → stored value (`None` means "delete the key")."""
    widget = str(field.get("widget") or "")
    kind = str(field.get("type") or "string")
    if raw is MISSING:
        raw = None
    if widget == "switch":
        return bool(raw)
    if widget == "multiselect":
        items = raw if isinstance(raw, (list, tuple)) else ([raw] if raw else [])
        return [str(item) for item in items if str(item).strip()]
    if widget == "key_value":
        entries = raw if isinstance(raw, dict) else {}
        return {str(key).strip(): str(value) for key, value in entries.items() if str(key).strip()}
    if widget == "number":
        if raw is None or str(raw).strip() == "":
            return None
        try:
            number = float(raw)
        except TypeError, ValueError:
            return None
        return int(number) if kind == "integer" else number
    text = "" if raw is None else str(raw).strip()
    return text or None


def validate_values(
    schema: dict | None, values: dict[str, Any], *, locale: str | None = None
) -> list[str]:
    """Client-side pre-flight check; returns one message per problem.

    Mirrors the core's rules (required, enum, range) so the user gets the
    message before the request; the server validates again.
    """
    problems: list[str] = []
    for field in fields_of(schema):
        key = str(field["key"])
        if not is_visible(field, values):
            continue
        value = values.get(key, MISSING)
        if value is MISSING or value is None or value == "" or value == [] or value == {}:
            if field.get("required"):
                problems.append(t("connector_form.field_required", locale=locale, field=key))
            continue
        allowed = field.get("enum")
        if allowed:
            permitted = [str(item) for item in allowed]
            chosen = (
                [str(item) for item in value] if isinstance(value, (list, tuple)) else [str(value)]
            )
            if any(item not in permitted for item in chosen):
                problems.append(
                    t(
                        "connector_form.field_enum",
                        locale=locale,
                        field=key,
                        values=", ".join(permitted),
                    )
                )
                continue
        minimum, maximum = field.get("minimum"), field.get("maximum")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if minimum is not None and value < minimum:
                problems.append(
                    t("connector_form.field_minimum", locale=locale, field=key, value=minimum)
                )
            elif maximum is not None and value > maximum:
                problems.append(
                    t("connector_form.field_maximum", locale=locale, field=key, value=maximum)
                )
    return problems


def build_patch(initial: dict[str, Any], current: dict[str, Any]) -> dict:
    """Nested patch of the fields that changed (`None` deletes a key).

    Only changed fields are sent, so the core's `merge` mode keeps undeclared
    keys (and values the form does not show) untouched.
    """
    changed: dict[str, Any] = {}
    for key, value in current.items():
        before = initial.get(key, MISSING)
        if before is MISSING and value is None:
            continue
        if before == value:
            continue
        changed[key] = value
    return nest(changed)


def apply_patch(parameters: dict | None, patch: dict | None) -> dict:
    """Applies a nested patch to a parameters object (`None` = delete a key).

    Mirrors the core's `parameters_mode: "merge"` so the editor can show the
    result before saving.
    """
    result = copy.deepcopy(parameters or {})
    _merge_into(result, patch or {})
    return result


def _merge_into(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
            continue
        existing = target.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            _merge_into(existing, value)
        else:
            target[key] = value


def summary_parts(parameters: dict | None, *, locale: str | None = None) -> list[tuple[str, str]]:
    """Parts of the list summary: `[(i18n_key, value), …]`."""
    parameters = parameters or {}
    parts: list[tuple[str, str]] = []
    url = get_path(parameters, "url")
    if url not in (MISSING, None, ""):
        parts.append(("connectors.summary_url", str(url)))
    filters: list[str] = []
    for key, label in (
        ("filter.event_types", "connectors.summary_event_types"),
        ("filter.object_types", "connectors.summary_object_types"),
        ("filter.attribute_changes", "connectors.summary_attributes"),
    ):
        value = get_path(parameters, key)
        if isinstance(value, list) and value:
            filters.append(f"{len(value)} {t(label, locale=locale)}")
    if filters:
        parts.append(("connectors.summary_filter", " · ".join(filters)))
    coalesce = get_path(parameters, "coalesce.enabled")
    if coalesce is True:
        window = get_path(parameters, "coalesce.window_seconds")
        parts.append(
            (
                "connectors.summary_coalesce",
                str(int(window)) if isinstance(window, (int, float)) else "",
            )
        )
    return parts


def summary_text(schema: dict | None, parameters: dict | None, *, locale: str | None = None) -> str:
    """One-line description of a connector's configuration for the grid."""
    parts = [
        t(key, locale=locale, value=value)
        for key, value in summary_parts(parameters, locale=locale)
    ]
    if not parts:
        declared = len(fields_of(schema))
        return (
            t("connectors.summary_plain", locale=locale)
            if declared
            else t("connectors.summary_unwired", locale=locale)
        )
    extra = len(unknown_keys(schema, parameters))
    if extra:
        parts.append(t("connectors.summary_undeclared", locale=locale, count=extra))
    return " · ".join(parts)


class KeyValueEditor:
    """Small key/value table (HTTP headers, later mapping tables)."""

    def __init__(self, value: dict | None = None, *, secret: bool = False) -> None:
        self._rows: list[dict[str, Any]] = []
        self._secret = secret
        self._container = ui.column().classes("w-full gap-1")
        with self._container:
            self._rows_host = ui.column().classes("w-full gap-1")
            ui.button(t("connector_form.add_row"), icon="add", on_click=self.add_row).props(
                "flat dense"
            )
        for key, entry in (value or {}).items():
            self._append_row(str(key), str(entry))
        if not self._rows:
            self.add_row()

    def _append_row(self, key: str = "", value: str = "") -> None:
        row: dict[str, Any] = {"key": key, "value": value, "host": None}
        with self._rows_host:
            host = ui.row().classes("items-center gap-1 w-full")
            with host:
                row["key_input"] = (
                    ui.input(placeholder=t("connector_form.row_key"))
                    .props("dense outlined")
                    .classes("w-1/3")
                )
                row["value_input"] = (
                    ui.input(
                        placeholder=t("connector_form.row_value"),
                        password=self._secret,
                    )
                    .props("dense outlined")
                    .classes("grow")
                )
                row["key_input"].set_value(key)
                row["value_input"].set_value(value)
                ui.button(icon="close", on_click=lambda r=row: self._remove_row(r)).props(
                    "flat dense size=sm"
                )
        row["host"] = host
        self._rows.append(row)

    def _remove_row(self, row: dict[str, Any]) -> None:
        host = row.get("host")
        if host is not None:
            host.delete()
        self._rows = [entry for entry in self._rows if entry is not row]
        if not self._rows:
            self.add_row()

    def add_row(self) -> None:
        self._append_row()

    def read(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for row in self._rows:
            key = str(row["key_input"].value or "").strip()
            if not key:
                continue
            entries[key] = str(row["value_input"].value or "")
        return entries

    def set_value(self, value: dict | None) -> None:
        for row in self._rows:
            host = row.get("host")
            if host is not None:
                host.delete()
        self._rows = []
        for key, entry in (value or {}).items():
            self._append_row(str(key), str(entry))
        if not self._rows:
            self.add_row()


class ParameterForm:
    """Handle of a rendered parameter form."""

    def __init__(self, schema: dict) -> None:
        self.schema = schema
        self._fields: dict[str, dict] = {}
        self._readers: dict[str, Callable[[], Any]] = {}
        self._setters: dict[str, Callable[[Any], None]] = {}
        self._hosts: dict[str, ui.element] = {}

    def values(self) -> dict[str, Any]:
        """Current values as a flat `{path: value}` map (parsed)."""
        return {
            key: parse_value(self._fields[key], reader()) for key, reader in self._readers.items()
        }

    def refresh_visibility(self) -> None:
        current = self.values()
        for key, host in self._hosts.items():
            host.set_visibility(is_visible(self._fields[key], current))

    def set_values(self, parameters: dict | None) -> None:
        """Fills the widgets from a parameters object (e.g. after JSON edits)."""
        for key, field in self._fields.items():
            value = get_path(parameters or {}, key)
            self._setters[key](format_value(field, value))
        self.refresh_visibility()


def _options_for(field: dict, sources: dict[str, list[str]], current: Any) -> dict[str, str]:
    """Select options: declared enum and/or a dynamic source, plus current values."""
    values: list[str] = []
    declared = field.get("enum")
    if declared:
        values.extend(str(item) for item in declared)
    source = field.get("options_source")
    if source:
        values.extend(str(item) for item in sources.get(str(source), []) if str(item).strip())
    given = current if isinstance(current, list) else ([current] if current else [])
    values.extend(str(item) for item in given if str(item).strip())
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(value, value)
    return unique


def render_parameter_form(
    schema: dict,
    parameters: dict | None,
    *,
    option_sources: dict[str, list[str]] | None = None,
    on_change: Callable[[], None] | None = None,
) -> ParameterForm:
    """Renders the parameter form of one connector type into the current slot."""
    sources = option_sources or {}
    form = ParameterForm(schema)
    for group in schema.get("groups") or []:
        title = t(str(group.get("label_key") or group.get("key") or ""))
        with (
            ui.expansion(title, value=True).classes("w-full"),
            ui.column().classes("w-full gap-2 p-2"),
        ):
            for field in group.get("fields") or []:
                _render_field(form, field, parameters, sources, on_change)
    form.refresh_visibility()
    return form


def _render_field(
    form: ParameterForm,
    field: dict,
    parameters: dict | None,
    sources: dict[str, list[str]],
    on_change: Callable[[], None] | None,
) -> None:
    key = str(field["key"])
    value = get_path(parameters or {}, key)
    host = ui.column().classes("w-full gap-0")
    form._fields[key] = field
    form._hosts[key] = host
    label = t(str(field.get("label_key") or key))
    if field.get("required"):
        label = f"{label} *"
    widget: ui.element | None = None
    with host:
        if str(field.get("widget")) == "switch":
            widget = ui.switch(label, value=bool(format_value(field, value)))
            form._readers[key] = lambda w=widget: w.value
            form._setters[key] = lambda new, w=widget: w.set_value(bool(new))
        elif str(field.get("widget")) == "number":
            widget = ui.number(
                label,
                value=format_value(field, value),
                min=field.get("minimum"),
                max=field.get("maximum"),
            ).props("filled dense")
            form._readers[key] = lambda w=widget: w.value
            form._setters[key] = lambda new, w=widget: w.set_value(new)
        elif str(field.get("widget")) in ("select", "multiselect"):
            multiple = str(field.get("widget")) == "multiselect"
            current = format_value(field, value)
            if not multiple and current in ("", None):
                # `ui.select` rejects a value that is not part of the options.
                current = None
            widget = ui.select(
                _options_for(field, sources, current),
                label=label,
                value=current,
                multiple=multiple,
            ).props("filled dense")
            form._readers[key] = lambda w=widget: w.value
            form._setters[key] = lambda new, w=widget: w.set_value(new)
        elif str(field.get("widget")) == "key_value":
            ui.label(label).classes("text-caption text-gray-500")
            editor = KeyValueEditor(format_value(field, value), secret=bool(field.get("secret")))
            form._readers[key] = editor.read
            form._setters[key] = editor.set_value
        else:
            widget = (
                ui.textarea(label, value=format_value(field, value))
                if str(field.get("widget")) in ("textarea", "json")
                else ui.input(
                    label,
                    value=format_value(field, value),
                    password=bool(field.get("secret")),
                )
            ).props("filled dense")
            form._readers[key] = lambda w=widget: w.value
            form._setters[key] = lambda new, w=widget: w.set_value(new)
        if field.get("help_key"):
            ui.label(t(str(field["help_key"]))).classes("text-caption text-gray-500")
    if on_change is not None and widget is not None:
        widget.on_value_change(lambda _event: on_change())

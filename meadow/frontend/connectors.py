"""Admin page for connector configurations (admin/superadmin only).

Lists persisted inbound/outbound connectors (``GET /connectors``) and provides
create/edit (``POST``/``PATCH /connectors``), enable/disable (``PATCH`` with
``enabled``) and delete (``DELETE /connectors/{id}``).

Parameters are configured in two ways (Stage 1, ``plan/detail/
konnektor-konfiguration.md``):

* **Formular** — the field set, grouped sections, defaults, enums and
  conditional fields come from the core's parameter schema
  (``GET /connector-types``); the form sends only the changed fields with
  ``parameters_mode=merge`` so undeclared keys (and masked secrets) survive.
* **JSON** — the expert tab edits the raw ``parameters`` object and sends it as
  a full replacement. Undeclared keys are listed as a hint, never removed.

The dialog can **test** a stored connector (``POST /connectors/{id}/test``):
webhooks get a synthetic event, the import/export profiles a dry run of the
values they would contribute; the result dialog reports status, latency and an
excerpt (or the resolved profile values).
"""

from __future__ import annotations

import asyncio
import copy
import json as _json
import logging
from datetime import datetime
from typing import Any, Protocol

import httpx
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.ai_assist import ai_state, open_suggestion_dialog
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.connector_form import (
    apply_patch,
    build_patch,
    connector_test_lines,
    default_values,
    nest,
    project_fields,
    render_parameter_form,
    summary_text,
    unknown_keys,
    validate_values,
)
from meadow.frontend.filters import filter_select
from meadow.frontend.i18n import t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell

log = logging.getLogger(__name__)

#: Connector types used only when the schema endpoint is unreachable (the schema
#: from `GET /connector-types` is the source of truth for types and labels).
_FALLBACK_TYPES = ("webhook", "json_import", "json_export")

#: Connection test: number of polls and the wait between them (~15 s budget).
TEST_POLLS = 30
TEST_POLL_SECONDS = 0.5


def _type_label(connector_type: str) -> str:
    """A localized label for a connector type."""
    return {
        "webhook": t("connectors.type_webhook"),
        "json_import": t("connectors.type_json_import"),
        "json_export": t("connectors.type_json_export"),
    }.get(connector_type, connector_type)


def _enabled_badge(enabled: object) -> str:
    """A coloured badge for the enabled state."""
    if enabled:
        cls = "bg-positive text-white"
        label = t("connectors.state_enabled")
    else:
        cls = "bg-neutral text-gray-600"
        label = t("connectors.state_disabled")
    return f'<span class="px-2 py-0.5 rounded text-xs {cls}">{label}</span>'


def _fmt_dt(value: str | None) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:  # leave the raw value on parse failure
        return str(value)


def _json_pretty(value: object) -> str:
    """Pretty-prints a JSON value for the parameters textarea."""
    try:
        return _json.dumps(value, indent=2, ensure_ascii=False)
    except TypeError, ValueError:  # fall back to the raw string
        return str(value)


def _parse_parameters(text: str | None) -> dict | None:
    """Parses the parameters textarea JSON into a dict (or None on error)."""
    value = (text or "").strip()
    if not value:
        return {}
    try:
        parsed = _json.loads(value)
    except ValueError as exc:  # a body that is not JSON shows a warning
        ui.notify(f"{t('connectors.invalid_json')}: {exc}", color="warning")
        return None
    return parsed if isinstance(parsed, dict) else {}


class OptionSourceClient(Protocol):
    """The client surface `load_option_sources` reads.

    A test stub implements exactly these calls; the protocol keeps the dependency
    visible and checked instead of widening the parameter to `Any`.
    """

    async def list_object_types(self, token: str, *, limit: int = 1000) -> list[dict]: ...

    async def list_attribute_vocabulary(self, token: str) -> list[dict]: ...

    async def list_languages(self, token: str) -> list[dict]: ...

    async def list_channels(
        self, token: str, *, active: bool | None = None, limit: int = 1000
    ) -> list[dict]: ...


async def load_option_sources(client: OptionSourceClient, token: str) -> dict[str, list[str]]:
    """Loads the dynamic option sources of the parameter form.

    Each source is optional: a failing request degrades the form to the values the
    user typed himself, and the reason is logged instead of swallowed, so a degraded
    form stays diagnosable. Rendering continues either way.
    """

    option_sources: dict[str, list[str]] = {
        "object_types": [],
        "attribute_names": [],
        "languages": [],
        "channels": [],
    }
    try:
        object_types = await client.list_object_types(token, limit=1000)
        option_sources["object_types"] = [
            str(item.get("name")) for item in object_types if item.get("name")
        ]
    except (CoreError, httpx.HTTPError) as error:  # the picker then only offers entered values
        log.warning("connector form: object types unavailable (%s)", error)
    try:
        # The whole vocabulary (one request): a connector writes into own attributes
        # and into the fields of an installed standard alike, and the capped paged
        # list would offer nothing but the first 1000 `EF…` features.
        definitions = await client.list_attribute_vocabulary(token)
        option_sources["attribute_names"] = [
            str(item.get("name")) for item in definitions if item.get("name")
        ]
    except (CoreError, httpx.HTTPError) as error:  # the picker then only offers entered values
        log.warning("connector form: attribute names unavailable (%s)", error)
    try:
        languages = await client.list_languages(token)
        option_sources["languages"] = [
            str(item.get("code"))
            for item in languages
            if item.get("active") and item.get("code") not in (None, "root")
        ]
    except (CoreError, httpx.HTTPError) as error:  # the picker then only offers entered values
        log.warning("connector form: languages unavailable (%s)", error)
    try:
        channels = await client.list_channels(token, active=True)
        option_sources["channels"] = [
            str(item.get("code")) for item in channels if item.get("code")
        ]
    except (CoreError, httpx.HTTPError) as error:  # the picker then only offers entered values
        log.warning("connector form: channels unavailable (%s)", error)
    return option_sources


@ui.page("/connectors", response_timeout=PAGE_TIMEOUT)
async def connectors_page() -> None:
    await build_shell(active="/connectors")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        connectors = await client.list_connectors(token, limit=1000)
    except CoreError, httpx.HTTPError:  # show a friendly message on failure
        ui.label(t("connectors.load_failed")).classes("text-subtitle1 text-negative")
        return

    # Parameter schema per connector type (source of truth for the form) plus the
    # dynamic option sources it references (`object_types`, `attribute_names`).
    schemas: dict[str, dict] = {}
    type_labels: dict[str, str] = {}
    try:
        for entry in await client.list_connector_types(token):
            key = str(entry.get("type") or "")
            if not key:
                continue
            schemas[key] = entry
            type_labels[key] = t(str(entry.get("label_key") or key))
    except CoreError, httpx.HTTPError:  # degrade to the fallback type list
        schemas = {}
        type_labels = {}
    if not type_labels:
        type_labels = {
            connector_type: _type_label(connector_type) for connector_type in _FALLBACK_TYPES
        }

    option_sources = await load_option_sources(client, token)
    # Whether the AI assistant can be used (opt-in, configured, admin page) — and
    # why not, so the editor says it instead of hiding the button silently.
    ai = await ai_state(client, token)

    with ui.column().classes("w-full p-6"):
        ui.label(t("connectors.title")).classes("text-h4")
        ui.label(t("connectors.subtitle")).classes("text-subtitle1")

        filters: dict[str, str] = {"name": "", "type": "", "enabled": ""}

        def _matches(c: dict) -> bool:
            if filters["name"]:
                q = filters["name"].lower()
                if q not in str(c.get("name") or "").lower():
                    return False
            if filters["type"] and str(c.get("type") or "") != filters["type"]:
                return False
            return not (filters["enabled"] and str(c.get("enabled")) != filters["enabled"])

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(c.get("id")),
                    "name": str(c.get("name") or ""),
                    "type": type_labels.get(str(c.get("type")), str(c.get("type") or "")),
                    "config": summary_text(schemas.get(str(c.get("type"))), c.get("parameters")),
                    "enabled": _enabled_badge(c.get("enabled")),
                    "updated": _fmt_dt(c.get("updated_at")),
                    "updated_by": str(c.get("updated_by") or "").split(":")[-1],
                }
                for c in connectors
                if _matches(c)
            ]

        # Filter bar (free-text name / type / enabled).
        with ui.row().classes("w-full items-center gap-2 mt-2"):
            (
                ui.input(t("connectors.filter_name"))
                .props("filled dense")
                .classes("w-48")
                .on_value_change(lambda e: _set_filter("name", str(e.value)))
            )
            filter_select(
                type_labels,
                label=t("connectors.filter_type"),
                classes="w-44",
                on_change=lambda value: _set_filter("type", str(value)),
            )
            filter_select(
                {
                    "True": t("connectors.state_enabled"),
                    "False": t("connectors.state_disabled"),
                },
                label=t("connectors.filter_enabled"),
                classes="w-44",
                on_change=lambda value: _set_filter("enabled", str(value)),
            )

        def _apply_filters() -> None:
            grid.run_grid_method("setGridOption", "rowData", _rows())

        def _set_filter(key: str, value: str) -> None:
            filters[key] = value
            _apply_filters()

        column_defs = [
            {"headerName": t("connectors.col_name"), "field": "name", "width": 200},
            {"headerName": t("connectors.col_type"), "field": "type", "width": 130},
            {"headerName": t("connectors.col_config"), "field": "config", "width": 380},
            {"headerName": t("connectors.col_enabled"), "field": "enabled", "width": 110},
            {"headerName": t("connectors.col_updated"), "field": "updated", "width": 180},
            {
                "headerName": t("connectors.col_updated_by"),
                "field": "updated_by",
                "width": 110,
            },
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[i for i, col in enumerate(column_defs) if col["field"] == "enabled"],
        ).classes("w-full")

        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(
                    t("connectors.new"),
                    icon="add",
                    on_click=lambda: open_editor(None),
                ).props("flat")
                ui.button(
                    t("connectors.edit"), icon="edit", on_click=lambda: edit_selected()
                ).props("flat")
                ui.button(
                    t("connectors.field_enabled"),
                    icon="power_settings_new",
                    on_click=lambda: toggle_enabled(),
                ).props("flat")
                ui.button(
                    t("connectors.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat")
            ui.button(t("connectors.reload"), icon="refresh", on_click=lambda: reload_rows()).props(
                "flat"
            )

        editor_dialog = ui.dialog()
        with (
            editor_dialog,
            ui.card().classes("w-[46rem] max-w-[94vw] max-h-[88vh] overflow-y-auto"),
        ):
            editor_title = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full gap-2"):
                    name_input = (
                        ui.input(t("connectors.field_name")).props("filled dense").classes("grow")
                    )
                    type_select = (
                        ui.select(
                            type_labels,
                            label=t("connectors.field_type"),
                            value=next(iter(type_labels)),
                        )
                        .props("filled dense")
                        .classes("w-56")
                    )
                enabled_switch = ui.switch(t("connectors.field_enabled"), value=True)
                unwired_hint = ui.label(t("connectors.unwired_hint")).classes(
                    "text-caption text-warning"
                )
                with ui.tabs().classes("w-full") as tabs:
                    form_tab = ui.tab("form", t("connectors.tab_form"))
                    json_tab = ui.tab("json", t("connectors.tab_json"))
                with ui.tab_panels(tabs, value=form_tab).classes("w-full"):
                    with ui.tab_panel(form_tab).classes("p-0"):
                        form_host = ui.column().classes("w-full gap-2")
                        unknown_hint = ui.label("").classes("text-caption text-gray-500")
                        reset_switch = ui.switch(t("connectors.reset_parameters")).tooltip(
                            t("connectors.reset_parameters_hint")
                        )
                    with ui.tab_panel(json_tab).classes("p-0"):
                        parameters_area = (
                            ui.textarea(t("connectors.field_parameters"))
                            .props("filled dense")
                            .classes("w-full font-mono text-sm")
                        )
                        ui.label(t("connectors.parameters_hint")).classes(
                            "text-caption text-gray-500"
                        )
                        with ui.row().classes("w-full items-center gap-2"):
                            if ai.get("available"):
                                # The suggestion replaces the JSON tab's content; the
                                # user still saves through Save (never auto-stored).
                                ui.button(
                                    t("connectors.ai_suggest"),
                                    icon="auto_awesome",
                                    on_click=lambda: suggest_parameters(),
                                ).props("flat dense")
                                ui.label(t("connectors.ai_suggest_hint")).classes(
                                    "text-caption text-gray-500"
                                )
                            else:
                                ui.label(t(str(ai.get("reason") or "ai.unavailable"))).classes(
                                    "text-caption text-gray-500"
                                )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                test_button = ui.button(
                    t("connectors.test"),
                    icon="network_check",
                    on_click=lambda: run_connector_test(),
                ).props("flat")
                test_button.tooltip(t("connectors.test_hint"))
                ui.button(
                    t("connectors.save"),
                    icon="save",
                    on_click=lambda: save_connector(),
                ).props("flat")

        # --- Editor state -----------------------------------------------------
        # `stored` is what the core currently has (baseline for the change
        # detection), `values` the working copy of the dialog and `initial` the
        # form projection of `stored` (so a save sends only real changes).
        editor_state: dict[str, Any] = {
            "id": None,
            "type": str(next(iter(type_labels))),
            "tab": "form",
            "stored": {},
            "values": {},
            "initial": {},
            "form": None,
        }

        def _form_values() -> dict[str, Any]:
            """Current form values as a flat `{path: value}` map."""
            form = editor_state["form"]
            return dict(form.values()) if form is not None else {}

        def _current_parameters() -> dict:
            """The parameters the editor currently represents (form or JSON tab)."""
            if editor_state["tab"] == "json":
                parsed = _parse_parameters(parameters_area.value)
                return parsed if parsed is not None else dict(editor_state["values"])
            if editor_state["form"] is None:
                return dict(editor_state["values"])
            return apply_patch(editor_state["values"], nest(_form_values()))

        def _update_hints() -> None:
            """Shows the undeclared parameters of the current configuration."""
            schema = schemas.get(str(editor_state["type"])) or {}
            if not schema.get("wired"):
                unknown_hint.set_visibility(False)
                return
            names = unknown_keys(schema, _current_parameters())
            if names:
                unknown_hint.set_text(
                    t("connectors.unknown_parameters").format(names=", ".join(names))
                )
            unknown_hint.set_visibility(bool(names))

        def _refresh_visibility() -> None:
            form = editor_state["form"]
            if form is not None:
                form.refresh_visibility()
            _update_hints()

        def _render_form() -> None:
            """(Re)renders the guided form for the selected connector type."""
            schema = schemas.get(str(editor_state["type"])) or {}
            wired = bool(schema.get("wired")) and bool(schema.get("groups"))
            form_host.clear()
            editor_state["form"] = None
            unwired_hint.set_visibility(not wired)
            reset_switch.set_visibility(wired)
            with form_host:
                if wired:
                    editor_state["form"] = render_parameter_form(
                        schema,
                        editor_state["values"],
                        option_sources=option_sources,
                        on_change=_refresh_visibility,
                    )
            # Baseline: what the *stored* parameters look like in this form, so
            # every difference (form or JSON tab) becomes part of the patch.
            editor_state["initial"] = project_fields(schema, editor_state["stored"])
            if not wired:
                tabs.set_value("json")
                editor_state["tab"] = "json"
            _update_hints()

        def _on_tab_change(event) -> None:
            """Keeps both tabs in sync (form values ↔ JSON object)."""
            target = str(event.value or "")
            if target == editor_state["tab"]:
                return
            if target == "json":
                parameters_area.set_value(_json_pretty(_current_parameters()))
            else:
                parsed = _parse_parameters(parameters_area.value)
                if parsed is None:
                    tabs.set_value("json")
                    return
                editor_state["values"] = parsed
                form = editor_state["form"]
                if form is not None:
                    form.set_values(parsed)
            editor_state["tab"] = target
            _update_hints()

        tabs.on_value_change(_on_tab_change)

        def _on_type_change() -> None:
            editor_state["type"] = str(type_select.value or editor_state["type"])
            editor_state["values"] = _current_parameters()
            _render_form()

        type_select.on_value_change(lambda _event: _on_type_change())

        def suggest_parameters() -> None:
            """AI suggestion for the parameters; applying it fills the JSON tab.

            The tab state is set **before** switching tabs so `_on_tab_change` does
            not render the form's values over the suggestion.
            """

            def apply(reply: dict) -> None:
                config = reply.get("config")
                if not isinstance(config, dict):
                    return
                parameters_area.set_value(_json_pretty(config))
                editor_state["values"] = copy.deepcopy(config)
                editor_state["tab"] = "json"
                tabs.set_value("json")
                _update_hints()

            open_suggestion_dialog(
                client=client,
                token=token,
                area="connector",
                title_key="connectors.ai_title",
                config_kind=lambda: str(editor_state["type"]),
                current_script=lambda: str(parameters_area.value or ""),
                on_apply=apply,
            )

        def _fill_editor(connector: dict | None) -> None:
            editing = connector is not None
            connector = connector or {}  # the reads below only run while editing
            editor_title.set_text(t("connectors.edit") if editing else t("connectors.new"))
            editor_state["id"] = str(connector.get("id")) if editing else None
            name_input.set_value(str(connector.get("name") or "") if editing else "")
            editor_state["type"] = (
                str(connector.get("type") or next(iter(type_labels)))
                if editing
                else next(iter(type_labels))
            )
            type_select.set_value(editor_state["type"])
            enabled_switch.set_value(bool(connector.get("enabled")) if editing else True)
            parameters = (connector.get("parameters") if editing else None) or {}
            editor_state["stored"] = copy.deepcopy(parameters)
            editor_state["values"] = copy.deepcopy(parameters)
            parameters_area.set_value(_json_pretty(parameters))
            reset_switch.set_value(False)
            editor_state["tab"] = "form"
            _render_form()

        def open_editor(connector: dict | None) -> None:
            _fill_editor(connector)
            editor_dialog.open()

        async def save_connector() -> None:
            editing_id = editor_state["id"]
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("connectors.name_required"), color="warning")
                return
            connector_type = str(type_select.value or editor_state["type"])
            schema = schemas.get(connector_type) or {}
            enabled = bool(enabled_switch.value)
            parameters: dict | None = None
            parameters_mode: str | None = None
            if editor_state["tab"] == "json" or editor_state["form"] is None:
                parsed = _parse_parameters(parameters_area.value)
                if parsed is None:
                    return
                parameters, parameters_mode = parsed, "replace"
            else:
                problems = validate_values(schema, _form_values())
                if problems:
                    ui.notify(" · ".join(problems), color="warning")
                    return
                if bool(reset_switch.value):
                    # Drop everything the previous type declared: start from the
                    # new type's defaults and keep what the form shows.
                    parameters = apply_patch(default_values(schema), nest(_form_values()))
                    parameters_mode = "replace"
                else:
                    patch = build_patch(editor_state["initial"], _form_values())
                    if patch:
                        parameters, parameters_mode = patch, "merge"
            try:
                if editing_id:
                    await client.update_connector(
                        token,
                        editing_id,
                        name=name,
                        connector_type=connector_type,
                        parameters=parameters,
                        parameters_mode=parameters_mode,
                        enabled=enabled,
                    )
                else:
                    await client.create_connector(
                        token,
                        name=name,
                        connector_type=connector_type,
                        parameters=nest(_form_values()) if parameters is None else parameters,
                        enabled=enabled,
                    )
            except CoreError as error:
                ui.notify(f"{t('connectors.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload_rows()
            ui.notify(t("connectors.saved"), color="positive")

        async def run_connector_test() -> None:
            """Queues a connection test in the worker and shows the result."""
            connector_id = editor_state["id"]
            if not connector_id:
                ui.notify(t("connectors.test_unsaved"), color="warning")
                return
            try:
                run_id = await client.test_connector(token, str(connector_id))
            except CoreError as error:
                ui.notify(f"{t('connectors.test_failed')}: {error}", color="negative")
                return
            if not run_id:
                ui.notify(t("connectors.test_failed"), color="negative")
                return

            result: dict | None = None
            with ui.dialog() as result_dialog, ui.card().classes("w-[38rem] max-w-[92vw]"):
                ui.label(t("connectors.test_title")).classes("text-subtitle1")
                body = ui.column().classes("w-full gap-1")
                with body:
                    ui.spinner(size="lg")
                    ui.label(t("connectors.test_running")).classes("text-caption")
                with ui.row().classes("w-full justify-end mt-2"):
                    ui.button(t("grid.close"), on_click=result_dialog.close).props("flat")
            result_dialog.open()

            for _ in range(TEST_POLLS):
                try:
                    runs = await client.list_job_runs(token, ids=[run_id], limit=1)
                except CoreError as error:
                    ui.notify(f"{t('connectors.test_failed')}: {error}", color="negative")
                    break
                run = runs[0] if runs else {}
                if run.get("status") in ("success", "failed"):
                    result = run.get("result") if isinstance(run.get("result"), dict) else None
                    if result is None:
                        result = {"ok": False, "error": run.get("error") or ""}
                    break
                await asyncio.sleep(TEST_POLL_SECONDS)

            body.clear()
            with body:
                if result is None:
                    ui.label(t("connectors.test_timeout")).classes("text-caption text-negative")
                    return
                for index, line in enumerate(connector_test_lines(result)):
                    style = "text-body2"
                    if index == 0:
                        style = (
                            "text-subtitle2 text-positive"
                            if result.get("ok")
                            else ("text-subtitle2 text-negative")
                        )
                    ui.label(line).classes(style)

        async def reload_rows() -> None:
            nonlocal connectors
            try:
                connectors = await client.list_connectors(token, limit=1000)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def get_selected() -> dict | None:
            try:
                rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                rows = []
            if not rows:
                return None
            row = rows[0] if isinstance(rows, list) else rows
            if isinstance(row, dict) and row.get("id"):
                return next((c for c in connectors if str(c.get("id")) == str(row.get("id"))), row)
            return row if isinstance(row, dict) else None

        async def edit_selected() -> None:
            connector = await get_selected()
            if not connector:
                ui.notify(t("connectors.no_selection"), color="warning")
                return
            open_editor(connector)

        async def toggle_enabled() -> None:
            connector = await get_selected()
            if not connector:
                ui.notify(t("connectors.no_selection"), color="warning")
                return
            connector_id = str(connector.get("id"))
            new_enabled = not bool(connector.get("enabled"))
            try:
                await client.update_connector(token, connector_id, enabled=new_enabled)
            except CoreError as error:
                ui.notify(f"{t('connectors.save_failed')}: {error}", color="negative")
                return
            await reload_rows()
            ui.notify(t("connectors.saved"), color="positive")

        async def do_delete() -> None:
            connector = await get_selected()
            if not connector:
                ui.notify(t("connectors.no_selection"), color="warning")
                return
            connector_id = str(connector.get("id"))
            name = str(connector.get("name") or "")
            message = t("connectors.delete_confirm").format(name=name)
            with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                ui.label(message).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")
                    ui.button(
                        t("connectors.delete"),
                        on_click=lambda: do_delete_confirm(connector_id, confirm),
                    ).props("color=negative")
            confirm.open()

        async def do_delete_confirm(connector_id: str, confirm: ui.dialog) -> None:
            confirm.close()
            try:
                await client.delete_connector(token, connector_id)
            except CoreError as error:
                ui.notify(f"{t('connectors.delete_failed')}: {error}", color="negative")
                return
            await reload_rows()
            ui.notify(t("connectors.deleted"), color="positive")

        def _connector_for(data: object) -> dict | None:
            """Resolves a double-click payload to the full connector record.

            The grid row only carries display fields (label as `type`, no
            `parameters`), so the record is looked up in the loaded list.
            """
            identifier = data.get("id") if isinstance(data, dict) else data
            if identifier is None:
                return None
            return next((c for c in connectors if str(c.get("id")) == str(identifier)), None)

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(_connector_for(e.args.get("data"))),
            ["data"],
        )

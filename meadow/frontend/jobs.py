"""Admin page for job definitions and job runs (admin/superadmin only).

Two tabs:
- **Definitions**: list job definitions (``GET /jobs``) with create/edit dialog
  (``POST``/``PATCH /jobs``), enable/disable, a manual run (``POST
  /jobs/{id}/run``) and a delete action (``DELETE /jobs/{id}``, which also
  removes the runs referencing the definition).
- **Runs**: the execution history (``GET /jobs/runs``), newest first, with a
  status filter and a "dead letter only" view. Double-click opens a detail
  dialog (result / error); a single run can be deleted (``DELETE
  /jobs/runs/{run_id}``).
"""

from __future__ import annotations

import json as _json
from datetime import datetime

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.filters import filter_select
from meadow.frontend.i18n import t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell

#: Known job types (mirrors the core's `VALID_JOB_TYPES`).
JOB_TYPES = (
    "outbound_export",
    "inbound_import",
    "business_rule",
    "bulk_operation",
    "collection_refresh",
    "config_import",
    "connector_test",
)
#: Known job-run statuses (matches the core's VALID_RUN_STATUSES).
RUN_STATUSES = ("pending", "running", "success", "failed", "retry")


def _job_status_badge(status: str) -> str:
    """A coloured badge for a job-run status."""
    classes = {
        "pending": "bg-neutral text-gray-600",
        "running": "bg-info text-white",
        "success": "bg-positive text-white",
        "failed": "bg-negative text-white",
        "retry": "bg-warning text-white",
    }.get(status, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{status}</span>'


def _fmt_dt(value: str | None) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - leave the raw value on parse failure
        return str(value)


def _duration(started: str | None, finished: str | None) -> str:
    """Returns the run duration in seconds ('' when not finished)."""
    if not started or not finished:
        return ""
    try:
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
        return f"{(end - start).total_seconds():.1f}s"
    except Exception:  # noqa: BLE001 - ignore malformed timestamps
        return ""


def _json_pretty(value: object) -> str:
    """Pretty-prints a JSON value for the detail dialog."""
    try:
        return _json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - fall back to the raw string
        return str(value)


def _run_job_label(run: dict) -> str:
    """A human-friendly job label for a run (bare definition key or ``ad-hoc``)."""
    definition_id = run.get("job_definition_id")
    if definition_id:
        return str(definition_id).split(":")[-1]
    return "ad-hoc"


@ui.page("/jobs", response_timeout=PAGE_TIMEOUT)
async def jobs_page() -> None:
    await build_shell(active="/jobs")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    # Definition being edited: shared between the render helper and the save handler
    # (a button attribute would be invisible to the reader and the type checker).
    editing_id: str | None = None

    try:
        defs = await client.list_job_definitions(token, limit=500)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("jobs.load_failed")).classes("text-subtitle1 text-negative")
        return
    try:
        runs = await client.list_job_runs(token, limit=100, include_result=False)
    except Exception:  # noqa: BLE001 - degrade to no runs
        runs = []

    with ui.column().classes("w-full p-6"):
        ui.label(t("jobs.title")).classes("text-h4")
        ui.label(t("jobs.subtitle")).classes("text-subtitle1")

        with ui.tabs().classes("w-full") as tabs:
            defs_tab = ui.tab(t("jobs.tab_defs"))
            runs_tab = ui.tab(t("jobs.tab_runs"))
        with ui.tab_panels(tabs, value=defs_tab).classes("w-full"):
            # --- Definitions -----------------------------------------------
            with ui.tab_panel(defs_tab):
                current_type = {"value": ""}

                def _def_rows() -> list[dict]:
                    def _matches(definition: dict) -> bool:
                        if not current_type["value"]:
                            return True
                        return str(definition.get("type") or "") == current_type["value"]

                    return [
                        {
                            "id": str(d.get("id")),
                            "name": str(d.get("name") or ""),
                            "type": str(d.get("type") or ""),
                            "schedule": str(d.get("schedule") or "") or t("jobs.manual_only"),
                            "enabled": str(d.get("enabled")),
                            "updated": _fmt_dt(d.get("updated_at")),
                        }
                        for d in defs
                        if _matches(d)
                    ]

                with ui.row().classes("w-full gap-2 items-center mb-2"):
                    filter_select(
                        {"": t("jobs.all_types"), **{typ: typ for typ in JOB_TYPES}},
                        label=t("jobs.filter_type"),
                        value="",
                        classes="w-52",
                        on_change=lambda value: _apply_type_filter(str(value)),
                    )

                    def _apply_type_filter(value: str) -> None:
                        current_type["value"] = value
                        def_grid.run_grid_method("setGridOption", "rowData", _def_rows())

                def_grid = ui.aggrid(
                    {
                        "columnDefs": [
                            {"headerName": t("jobs.col_name"), "field": "name", "width": 200},
                            {"headerName": t("jobs.col_type"), "field": "type", "width": 150},
                            {
                                "headerName": t("jobs.col_schedule"),
                                "field": "schedule",
                                "width": 160,
                            },
                            {
                                "headerName": t("jobs.col_enabled"),
                                "field": "enabled",
                                "width": 100,
                            },
                            {
                                "headerName": t("jobs.col_updated"),
                                "field": "updated",
                                "width": 190,
                            },
                        ],
                        "rowData": _def_rows(),
                        "rowSelection": {"mode": "singleRow"},
                    }
                ).classes("w-full")
                with ui.row().classes("w-full justify-between mt-2 gap-2"):
                    with ui.row().classes("gap-2"):
                        ui.button(
                            t("jobs.new"), icon="add", on_click=lambda: open_editor(None)
                        ).props("flat")
                        ui.button(
                            t("jobs.run_btn"),
                            icon="play_arrow",
                            on_click=lambda: run_selected(),
                        ).props("flat")
                        ui.button(
                            t("jobs.delete"), icon="delete", on_click=lambda: delete_selected()
                        ).props("flat color=negative")
                    ui.button(
                        t("jobs.reload"), icon="refresh", on_click=lambda: reload_defs()
                    ).props("flat")

                def_dialog = ui.dialog()
                with def_dialog, ui.card().classes("w-[38rem] max-w-[94vw]"):
                    def_title = ui.label("").classes("text-h6")
                    with ui.column().classes("w-full mt-2 gap-2"):
                        with ui.row().classes("w-full gap-2"):
                            name_input = (
                                ui.input(t("jobs.field_name")).props("filled dense").classes("grow")
                            )
                            type_select = (
                                ui.select(
                                    {typ: typ for typ in JOB_TYPES},
                                    label=t("jobs.field_type"),
                                    value="business_rule",
                                )
                                .props("filled dense")
                                .classes("w-44")
                            )
                        schedule_input = (
                            ui.input(t("jobs.field_schedule"))
                            .props("filled dense")
                            .classes("w-full")
                        )
                        ui.label(t("jobs.schedule_hint")).classes("text-caption text-gray-500")
                        enabled_switch = ui.switch(t("jobs.field_enabled"), value=True)
                        parameters_area = (
                            ui.textarea(t("jobs.field_parameters"))
                            .props("filled dense")
                            .classes("w-full")
                        )
                    with ui.row().classes("w-full justify-end mt-2 gap-2"):
                        ui.button(t("grid.close"), on_click=def_dialog.close).props("flat")
                        ui.button(t("jobs.save"), icon="save", on_click=lambda: save_def()).props(
                            "flat"
                        )

                def _parse_parameters() -> dict | None:
                    text = parameters_area.value or ""
                    if not text.strip():
                        return {}
                    try:
                        value = _json.loads(text)
                    except Exception as exc:  # noqa: BLE001 - show a friendly warning
                        ui.notify(f"{t('jobs.invalid_json')}: {exc}", color="warning")
                        return None
                    return value if isinstance(value, dict) else {}

                def _fill_def(definition: dict | None) -> None:
                    editing = definition is not None
                    def_title.set_text(t("jobs.edit") if editing else t("jobs.new"))
                    name_input.set_value(str(definition.get("name") or "") if editing else "")
                    type_select.set_value(
                        str(definition.get("type") or "business_rule")
                        if editing
                        else "business_rule"
                    )
                    schedule_input.set_value(
                        str(definition.get("schedule") or "") if editing else ""
                    )
                    enabled_switch.set_value(bool(definition.get("enabled")) if editing else True)
                    params = definition.get("parameters") if editing else None
                    parameters_area.set_value(
                        _json.dumps(params, indent=2, ensure_ascii=False)
                        if params is not None
                        else ""
                    )

                def open_editor(definition: dict | None) -> None:
                    nonlocal editing_id
                    _fill_def(definition)
                    editing_id = definition.get("id") if definition else None
                    def_dialog.open()

                async def save_def() -> None:
                    name = (name_input.value or "").strip()
                    if not name:
                        ui.notify(t("jobs.name_required"), color="warning")
                        return
                    params = _parse_parameters()
                    if params is None:
                        return
                    schedule = (schedule_input.value or "").strip() or None
                    enabled = bool(enabled_switch.value)
                    try:
                        if editing_id:
                            await client.update_job_definition(
                                token,
                                editing_id,
                                name=name,
                                schedule=schedule,
                                enabled=enabled,
                                parameters=params,
                            )
                        else:
                            await client.create_job_definition(
                                token,
                                name=name,
                                job_type=type_select.value or "business_rule",
                                schedule=schedule,
                                parameters=params,
                                enabled=enabled,
                            )
                    except Exception as exc:  # noqa: BLE001 - show a friendly error
                        ui.notify(f"{t('jobs.save_failed')}: {exc}", color="negative")
                        return
                    def_dialog.close()
                    await reload_defs()
                    ui.notify(t("jobs.saved"), color="positive")

                async def reload_defs() -> None:
                    nonlocal defs
                    try:
                        defs = await client.list_job_definitions(token, limit=500)
                    except Exception:  # noqa: BLE001 - keep the cached list on failure
                        return
                    def_grid.run_grid_method("setGridOption", "rowData", _def_rows())

                async def do_run(definition_id: str, name: str) -> None:
                    with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                        ui.label(t("jobs.run_confirm").format(name=name)).classes("text-subtitle1")
                        with ui.row().classes("w-full justify-end mt-2 gap-2"):
                            ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                            async def _go() -> None:
                                confirm.close()
                                try:
                                    await client.run_job(token, definition_id)
                                except CoreError as error:
                                    ui.notify(f"{t('jobs.run_failed')}: {error}", color="negative")
                                    return
                                ui.notify(t("jobs.running"), color="positive")
                                tabs.set_value(runs_tab)
                                await reload_runs()

                            ui.button(t("jobs.run_btn"), on_click=_go).props("color=secondary")
                    confirm.open()

                async def run_selected() -> None:
                    try:
                        selected = await def_grid.get_selected_rows()
                    except Exception:  # noqa: BLE001 - degrade to no selection
                        selected = []
                    if not selected:
                        ui.notify(t("jobs.no_selection"), color="warning")
                        return
                    row = selected[0] if isinstance(selected, list) else selected
                    oid = row.get("id") if isinstance(row, dict) else None
                    if not oid:
                        ui.notify(t("jobs.no_selection"), color="warning")
                        return
                    await do_run(oid, str(row.get("name") or ""))

                async def delete_selected() -> None:
                    """Deletes the selected job definition (with confirmation)."""
                    try:
                        selected = await def_grid.get_selected_rows()
                    except Exception:  # noqa: BLE001 - degrade to no selection
                        selected = []
                    if not selected:
                        ui.notify(t("jobs.no_selection"), color="warning")
                        return
                    row = selected[0] if isinstance(selected, list) else selected
                    oid = row.get("id") if isinstance(row, dict) else None
                    if not oid:
                        ui.notify(t("jobs.no_selection"), color="warning")
                        return
                    name = str(row.get("name") or "")
                    with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                        ui.label(t("jobs.delete_confirm").format(name=name)).classes(
                            "text-subtitle1"
                        )
                        ui.label(t("jobs.delete_warn")).classes("text-caption text-gray-600")
                        with ui.row().classes("w-full justify-end mt-2 gap-2"):
                            ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                            async def _go() -> None:
                                confirm.close()
                                try:
                                    await client.delete_job_definition(token, oid)
                                except CoreError as error:
                                    ui.notify(
                                        f"{t('jobs.delete_failed')}: {error}", color="negative"
                                    )
                                    return
                                ui.notify(t("jobs.deleted"), color="positive")
                                await reload_defs()

                            ui.button(t("jobs.delete"), on_click=_go).props("flat color=negative")
                    confirm.open()

                def _definition_for(data: object) -> dict | None:
                    """Resolves a double-click payload to a definition row.

                    ``cellDoubleClicked`` may deliver the row object or the row's
                    id (string); resolve by id from ``defs`` in the latter case.
                    """
                    if isinstance(data, dict):
                        return data
                    return next((d for d in defs if str(d.get("id")) == str(data)), None)

                def_grid.on(
                    "cellDoubleClicked",
                    lambda e: open_editor(_definition_for(e.args.get("data"))),
                    ["data"],
                )

            # --- Runs --------------------------------------------------------
            with ui.tab_panel(runs_tab):
                current_status = {"value": ""}
                dead_only = {"value": False}

                def _run_rows() -> list[dict]:
                    rows: list[dict] = []
                    for r in runs:
                        error_text = str(r.get("error") or "")
                        run_status = str(r.get("status") or "")
                        if current_status["value"] and run_status != current_status["value"]:
                            continue
                        if dead_only["value"] and "dead lettered" not in error_text.lower():
                            continue
                        rows.append(
                            {
                                "id": str(r.get("id")),
                                "job": _run_job_label(r),
                                "job_definition_id": r.get("job_definition_id"),
                                "created_at": _fmt_dt(r.get("created_at")),
                                "trigger": str(r.get("trigger") or ""),
                                "status": _job_status_badge(run_status),
                                "status_raw": run_status,
                                "started": _fmt_dt(r.get("started_at")),
                                "finished": _fmt_dt(r.get("finished_at")),
                                "duration": _duration(r.get("started_at"), r.get("finished_at")),
                                "error": error_text[:80],
                                "dead": "❗" if "dead lettered" in error_text.lower() else "",
                            }
                        )
                    return rows

                with ui.row().classes("w-full gap-2 items-center mb-2"):
                    status_select = (
                        ui.select(
                            {"": t("jobs.all_statuses"), **{s: s for s in RUN_STATUSES}},
                            label=t("jobs.filter_status"),
                        )
                        .props("filled dense")
                        .classes("w-52")
                    )
                    dead_switch = ui.switch(t("jobs.dead_letter_only"), value=False)
                    ui.button(
                        t("jobs.reload"), icon="refresh", on_click=lambda: reload_runs()
                    ).props("flat")
                    ui.space()
                    ui.button(
                        t("jobs.delete"), icon="delete", on_click=lambda: delete_selected_run()
                    ).props("flat color=negative")

                def _apply_filters() -> None:
                    current_status["value"] = str(status_select.value or "")
                    runs_grid.run_grid_method("setGridOption", "rowData", _run_rows())

                status_select.on_value_change(_apply_filters)
                dead_switch.on_value_change(lambda e: _set_dead_only(e.value))

                def _set_dead_only(value: bool) -> None:
                    dead_only["value"] = bool(value)
                    runs_grid.run_grid_method("setGridOption", "rowData", _run_rows())

                runs_column_defs = [
                    {"headerName": t("jobs.col_job"), "field": "job", "width": 160},
                    {"headerName": t("jobs.col_trigger"), "field": "trigger", "width": 100},
                    {"headerName": t("jobs.col_status"), "field": "status", "width": 110},
                    {"headerName": t("jobs.col_started"), "field": "started", "width": 180},
                    {"headerName": t("jobs.col_finished"), "field": "finished", "width": 180},
                    {"headerName": t("jobs.col_duration"), "field": "duration", "width": 90},
                    {"headerName": t("jobs.col_error"), "field": "error", "width": 260},
                    {"headerName": t("jobs.col_dead"), "field": "dead", "width": 60},
                ]
                runs_grid = ui.aggrid(
                    {
                        "columnDefs": runs_column_defs,
                        "rowData": _run_rows(),
                        "rowSelection": {"mode": "singleRow"},
                    },
                    html_columns=[
                        i
                        for i, column in enumerate(runs_column_defs)
                        if column["field"] == "status"
                    ],
                ).classes("w-full")

                detail_dialog = ui.dialog()
                with (
                    detail_dialog,
                    ui.card().classes("w-[46rem] max-w-[95vw] max-h-[85vh] overflow-y-auto"),
                ):
                    detail_title = ui.label("").classes("text-h6")
                    detail_info = ui.label("").classes("text-subtitle2 text-gray-500")
                    detail_body = ui.element("div").classes("w-full mt-2")

                    async def show_run_detail(run: dict) -> None:
                        # The listing omits the `result` payload (a bulk import stores
                        # megabytes there) — fetch the single run when the report is
                        # actually shown.
                        run_id = str(run.get("id") or "")
                        detail = run
                        if run_id:
                            try:
                                fetched = await client.list_job_runs(token, ids=[run_id], limit=1)
                                if fetched:
                                    detail = fetched[0]
                            except Exception:  # noqa: BLE001 - fall back to the listing row
                                detail = run
                        detail_title.set_text(t("jobs.detail_title"))
                        status = str(detail.get("status_raw") or detail.get("status") or "")
                        detail_info.set_text(
                            f"{_run_job_label(detail)} · {_fmt_dt(detail.get('created_at'))}"
                            f" · {status}"
                        )
                        detail_body.clear()
                        with detail_body:
                            ui.html(_job_status_badge(status)).classes("mb-2")
                            if detail.get("error"):
                                ui.label(t("jobs.col_error")).classes("text-subtitle2")
                                ui.code(str(detail.get("error"))).classes("w-full")
                            result = detail.get("result")
                            if result is not None:
                                ui.label("result").classes("text-subtitle2 mt-2")
                                ui.code(_json_pretty(result)).classes("w-full")
                        detail_dialog.open()

                    with ui.row().classes("w-full justify-end mt-2 gap-2"):
                        ui.button(t("grid.close"), on_click=detail_dialog.close).props("flat")

                async def reload_runs() -> None:
                    nonlocal runs
                    try:
                        runs = await client.list_job_runs(token, limit=100, include_result=False)
                    except Exception:  # noqa: BLE001 - keep the cached list on failure
                        return
                    runs_grid.run_grid_method("setGridOption", "rowData", _run_rows())

                def _run_for(data: object) -> dict | None:
                    """Resolves a double-click payload to a job-run row."""
                    if isinstance(data, dict):
                        return data
                    return next((r for r in runs if str(r.get("id")) == str(data)), None)

                async def delete_selected_run() -> None:
                    """Deletes the selected job run (with confirmation)."""
                    try:
                        selected = await runs_grid.get_selected_rows()
                    except Exception:  # noqa: BLE001 - degrade to no selection
                        selected = []
                    if not selected:
                        ui.notify(t("jobs.no_selection_run"), color="warning")
                        return
                    row = selected[0] if isinstance(selected, list) else selected
                    oid = row.get("id") if isinstance(row, dict) else None
                    if not oid:
                        ui.notify(t("jobs.no_selection_run"), color="warning")
                        return
                    with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                        ui.label(t("jobs.delete_run_confirm")).classes("text-subtitle1")
                        with ui.row().classes("w-full justify-end mt-2 gap-2"):
                            ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                            async def _go() -> None:
                                confirm.close()
                                try:
                                    await client.delete_job_run(token, oid)
                                except CoreError as error:
                                    ui.notify(
                                        f"{t('jobs.delete_failed')}: {error}", color="negative"
                                    )
                                    return
                                ui.notify(t("jobs.deleted"), color="positive")
                                await reload_runs()

                            ui.button(t("jobs.delete"), on_click=_go).props("flat color=negative")
                    confirm.open()

                async def _open_run_detail(event) -> None:
                    await show_run_detail(_run_for((event.args or {}).get("data")))

                runs_grid.on("cellDoubleClicked", _open_run_detail, ["data"])

                ui.timer(5.0, lambda: reload_runs() if tabs.value == runs_tab else None)

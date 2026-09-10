"""UI-guided data export wizard (`/export`, editor+).

Lets a normal user configure and run an export of instance data (objects) from
the surface instead of hand-writing connector JSON:

1. **Source** — object type, category, collection, saved search, or a selection.
2. **Fields** — base fields plus attribute definitions, in order, multilingual
   attributes expanded to one `<attr>_<locale>` column per chosen language.
3. **Format + language + options** — CSV / XLSX / JSON, one or more locales,
   header row, separator and `include_system`.
4. **Preview** (columns + approximate object count) and **export**, which queues
   an `outbound_export` job, stays inline, and offers a download of the result
   via `GET /exports/{run_id}/file`.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime

from fastapi import Request
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.grid import resolve_value
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell

#: Base fields always offered (the worker maps these onto each object row).
BASE_FIELDS = ("id", "type", "status")
#: The selectable export source kinds (matches the core `ExportSource.kind`).
SOURCE_KINDS = ("type", "category", "collection", "saved_search", "selection")
#: The selectable output formats (matches the core's `format`).
FORMATS = ("csv", "xlsx", "json")
#: Mime type per format, used for the browser download.
MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}


def _split_ids(raw: str) -> list[str]:
    """Splits a raw object-id input into a list of trimmed non-empty ids."""
    return [part.strip() for part in raw.replace(",", "\n").splitlines() if part.strip()]


def _fmt_dt(value: object) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - leave the raw value on parse failure
        return str(value)


@ui.page("/export", response_timeout=15)
async def export_page(request: Request) -> None:
    """The structured export wizard page."""
    await build_shell(active="/export")
    if not auth.is_editor():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")
    locale = current_locale()
    # When opened from the collections page, preselect that collection source.
    initial_kind = "type"
    initial_collection = request.query_params.get("collection")
    if initial_collection:
        initial_kind = "collection"

    async def _safe(coro_type: str) -> list[dict]:
        """Runs a reference-data loader and degrades to an empty list."""
        try:
            if coro_type == "object_types":
                return await client.list_object_types(token)
            if coro_type == "collections":
                return await client.list_collections(token, limit=1000)
            if coro_type == "saved_searches":
                return await client.list_saved_searches(token, limit=1000)
            if coro_type == "categories":
                return await client.list_objects(token, object_type="category", limit=1000)
            if coro_type == "languages":
                return await client.list_languages(token)
            return await client.list_attribute_definitions(token, limit=1000)
        except Exception:  # noqa: BLE001 - degrade so the wizard still opens
            return []

    object_types = await _safe("object_types")
    collections = await _safe("collections")
    saved_searches = await _safe("saved_searches")
    categories = await _safe("categories")
    languages = await _safe("languages")
    attribute_definitions = await _safe("attribute_definitions")

    def _type_label(obj: dict) -> str:
        return resolve_value(obj.get("label"), locale) or str(obj.get("name"))

    def _obj_name(obj: dict) -> str:
        raw_name = (obj.get("attributes") or {}).get("name")
        return resolve_value(raw_name, locale) or str(obj.get("id"))

    # Ordered field choices: base fields then every attribute definition name.
    field_names: list[str] = list(BASE_FIELDS)
    seen: set[str] = set()
    for definition in attribute_definitions:
        name = str(definition.get("name") or "")
        if name and name not in seen:
            seen.add(name)
            field_names.append(name)
    multilingual = {
        str(definition.get("name"))
        for definition in attribute_definitions
        if definition.get("multilingual")
    }

    async def _estimate_count(kind: str, detail_value: str) -> int | None:
        """Best-effort object count for the selected source (may be capped)."""
        if not detail_value:
            return None
        try:
            if kind == "collection":
                members = await client.list_collection_members(token, detail_value, limit=1000)
                return len(members)
            if kind == "saved_search":
                result = await client.execute_saved_search(token, detail_value)
                return int(result.get("count") or 0)
            if kind == "selection":
                ids = _split_ids(detail_value)
                return len(ids)
            if kind == "type":
                return len(await client.list_objects(token, object_type=detail_value, limit=1000))
            if kind == "category":
                return len(await client.list_objects(token, category=detail_value, limit=1000))
        except Exception:  # noqa: BLE001 - count is best-effort
            return None
        return None

    def _available_detail_options(kind: str) -> dict[str, str]:
        if kind == "type":
            return {str(o.get("name")): _type_label(o) for o in object_types}
        if kind == "category":
            return {str(c.get("id")): _obj_name(c) for c in categories}
        if kind == "collection":
            return {str(c.get("id")): _obj_name(c) for c in collections}
        if kind == "saved_search":
            return {str(s.get("id")): _obj_name(s) for s in saved_searches}
        return {}

    def _source_payload(kind: str, detail_value: str) -> dict:
        if kind == "type":
            return {"kind": "type", "type": detail_value}
        if kind == "category":
            return {"kind": "category", "category": detail_value}
        if kind == "collection":
            return {"kind": "collection", "id": detail_value}
        if kind == "saved_search":
            return {"kind": "saved_search", "id": detail_value}
        return {"kind": "selection", "object_ids": _split_ids(detail_value)}

    def _selected_fields() -> list[str]:
        return [name for name in field_names if field_checks[name].value]

    def _compute_columns(fields: list[str], locales: list[str]) -> list[str]:
        columns: list[str] = []
        for field in fields:
            if field in BASE_FIELDS or field not in multilingual:
                columns.append(field)
            else:
                for lcode in locales:
                    columns.append(f"{field}_{lcode}")
        return columns or list(BASE_FIELDS)

    async def _download(run_id: str, format_: str) -> None:
        """Fetches the export file and serves it via a temp file."""
        try:
            data, filename = await client.get_export_file(token, run_id, format=format_)
        except CoreError as exc:
            ui.notify(t("export.download_failed").format(err=exc), type="negative")
            return
        suffix = "." + format_
        with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as f:
            f.write(data)
            tmp_path = f.name
        # NiceGUI treats `str` as a URL and `bytes` as an immediately-revoked
        # Blob; writing to a temp file and downloading the path is reliable.
        ui.download(tmp_path, filename, MIME.get(format_, "application/octet-stream"))
        asyncio.get_running_loop().call_later(60, os.remove, tmp_path)

    def _show_report(run_id: str, exported: int, columns: list[str], format_: str) -> None:
        """Renders the export success report with a download button."""
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
            ui.label(t("export.report_title")).classes("text-subtitle1")
            ui.label(t("export.report_count").format(count=exported)).classes(
                "text-subtitle1 font-medium"
            )
            ui.label(t("export.report_format").format(format=format_.upper())).classes(
                "text-caption"
            )
            ui.separator()
            ui.label(t("export.report_columns")).classes("text-subtitle2")
            with ui.row().classes("flex-wrap gap-2 mt-1"):
                for column in columns:
                    ui.badge(column).props("outline")
            with ui.row().classes("mt-3 w-full justify-end"):
                ui.button(t("export.download"), on_click=lambda: _download(run_id, format_)).props(
                    "unelevated"
                )
                ui.button(t("export.close"), on_click=dialog.close).props("flat")
        dialog.open()

    with ui.column().classes("w-full p-6"):
        ui.label(t("export.title")).classes("text-h4")
        ui.label(t("export.subtitle")).classes("text-subtitle1")

        # --- Source --------------------------------------------------------
        with ui.card().classes("w-full mt-4"):
            ui.label(t("export.source_section")).classes("text-subtitle1")
            kind_select = (
                ui.select(
                    {k: t(f"export.source_{k}") for k in SOURCE_KINDS},
                    value=initial_kind,
                )
                .props("filled dense")
                .classes("mt-2 w-56")
            )

            detail_select = (
                ui.select(
                    _available_detail_options("type"),
                    value=None,
                    label=t("export.source_detail"),
                )
                .props("filled dense")
                .classes("mt-2 w-96")
            )

            ids_box = (
                ui.input(t("export.object_ids"), value="").props("outlined").classes("mt-2 w-full")
            )

            def update_source_editor(kind: str) -> None:
                if kind == "selection":
                    detail_select.set_visibility(False)
                    ids_box.set_visibility(True)
                else:
                    detail_select.set_visibility(True)
                    ids_box.set_visibility(False)
                    detail_select.options = _available_detail_options(kind)
                    if detail_select.value not in detail_select.options:
                        detail_select.value = None
                    detail_select.update()

            kind_select.on_value_change(lambda event: update_source_editor(event.value))
            update_source_editor(kind_select.value)
            if initial_kind == "collection" and initial_collection in detail_select.options:
                detail_select.value = initial_collection
                detail_select.update()

        # --- Fields --------------------------------------------------------
        with ui.card().classes("w-full mt-4"):
            ui.label(t("export.fields_section")).classes("text-subtitle1")
            ui.label(t("export.fields_hint")).classes("text-caption text-gray-500")

            field_checks: dict[str, ui.checkbox] = {}
            with ui.row().classes("flex-wrap gap-4 mt-2"):
                for name in field_names:
                    field_checks[name] = ui.checkbox(
                        name, value=name in ("id", "type", "status")
                    ).props("dense")
            all_fields = ui.checkbox(t("export.select_all")).props("dense").classes("mt-2")

            def toggle_all(event) -> None:
                for box in field_checks.values():
                    box.value = event.value

            all_fields.on_value_change(toggle_all)

        # --- Format + language + options -----------------------------------
        with ui.card().classes("w-full mt-4"):
            ui.label(t("export.format_section")).classes("text-subtitle1")
            with ui.row().classes("flex-wrap gap-6 mt-2"):
                format_select = (
                    ui.select({fmt: fmt.upper() for fmt in FORMATS}, value="csv")
                    .props("filled dense")
                    .classes("w-40")
                )

                data_languages = [str(lang.get("code")) for lang in languages]
                locale_select = (
                    ui.select(
                        data_languages,
                        value=data_languages[0] if data_languages else "de",
                        multiple=True,
                        label=t("export.locale"),
                    )
                    .props("filled dense")
                    .classes("w-64")
                )

            with ui.row().classes("flex-wrap gap-6 mt-3"):
                header_switch = ui.switch(t("export.header"), value=True)
                separator_select = (
                    ui.select(
                        {";": "Semicolon (;)", ",": "Comma (,)"},
                        value=",",
                        label=t("export.separator"),
                    )
                    .props("filled dense")
                    .classes("w-56")
                )
                include_system_switch = ui.switch(t("export.include_system"))

            email_input = (
                ui.input(t("export.email_to")).props("outlined dense").classes("w-full mt-3")
            )
            ui.label(t("export.email_hint")).classes("text-caption text-gray-500")

        # --- Actions -------------------------------------------------------
        with ui.row().classes("mt-4 gap-2 items-center"):
            preview_button = ui.button(t("export.preview")).props("outline")
            export_button = ui.button(t("export.export")).props("unelevated")
            schedule_button: ui.button | None = None
            if auth.is_admin():
                schedule_button = ui.button(t("export.schedule"), icon="schedule").props("flat")

        preview_box = ui.column().classes("w-full mt-4")

        def _locale_values() -> list[str]:
            return list(locale_select.value) if isinstance(locale_select.value, list) else ["de"]

        def _format() -> str:
            return str(format_select.value or "csv")

        def _email_recipients() -> list[str]:
            raw = str(email_input.value or "")
            return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]

        async def on_preview() -> None:
            kind = str(kind_select.value)
            detail_value = (
                str(detail_select.value) if kind != "selection" else str(ids_box.value or "")
            )
            if not detail_value:
                ui.notify(t("export.source_required"), type="warning")
                return
            fields = _selected_fields()
            columns = _compute_columns(fields, _locale_values())
            count = await _estimate_count(kind, detail_value)
            preview_box.clear()
            with preview_box, ui.card().classes("w-full"):
                ui.label(t("export.preview_title")).classes("text-subtitle1")
                count_text = "~" if count is None else count
                ui.label(t("export.preview_info").format(count=count_text)).classes(
                    "text-caption text-gray-500"
                )
                ui.label(t("export.preview_columns")).classes("text-subtitle2 mt-2")
                with ui.row().classes("flex-wrap gap-2 mt-1"):
                    for column in columns:
                        ui.badge(column).props("outline")

        async def on_export() -> None:
            kind = str(kind_select.value)
            detail_value = (
                str(detail_select.value) if kind != "selection" else str(ids_box.value or "")
            )
            if not detail_value:
                ui.notify(t("export.source_required"), type="warning")
                return
            fields = _selected_fields()
            if not fields:
                ui.notify(t("export.fields_required"), type="warning")
                return
            source = _source_payload(kind, detail_value)
            options = {
                "header": bool(header_switch.value),
                "separator": str(separator_select.value or ","),
                "include_system": bool(include_system_switch.value),
            }
            recipients = _email_recipients()
            if recipients:
                options["email_to"] = recipients
            export_button.props("loading")
            export_button.enabled = False
            preview_button.enabled = False
            try:
                queued = await client.create_export(
                    token,
                    source=source,
                    fields=fields,
                    format=_format(),
                    locales=_locale_values(),
                    options=options,
                )
                run_id = str(queued.get("job_run_id") or "")
                if not run_id:
                    raise CoreError(t("export.queued_no_id"))
                run = await _wait_for_run(client, token, run_id)
            except CoreError as exc:
                ui.notify(t("export.export_failed").format(err=exc), type="negative")
                return
            finally:
                export_button.props(remove="loading")
                export_button.enabled = True
                preview_button.enabled = True

            if run.get("status") != "success":
                error = run.get("error") or run.get("status")
                ui.notify(t("export.export_failed").format(err=error), type="negative")
                return

            result = run.get("result") or {}
            exported = int(result.get("exported") or 0)
            columns = [str(c) for c in (result.get("columns") or [])]
            _show_report(run_id, exported, columns, result.get("format") or _format())

        preview_button.on_click(on_preview)
        export_button.on_click(on_export)

        async def _run_now(definition_id: str) -> None:
            try:
                await client.run_job(token, definition_id)
            except CoreError as exc:
                ui.notify(t("export.schedule_failed").format(err=exc), type="negative")
                return
            ui.notify(t("export.schedule_started"), type="positive")
            # The run needs a moment to finish before it shows a result.
            await asyncio.sleep(3)
            await _render_recent()

        async def _delete_schedule(definition_id: str) -> None:
            try:
                await client.delete_job_definition(token, definition_id)
            except CoreError as exc:
                ui.notify(t("export.schedule_failed").format(err=exc), type="negative")
                return
            ui.notify(t("export.schedule_deleted"), type="positive")
            # Deleting a definition also removes its runs, so refresh both lists.
            await _render_schedules()
            await _render_recent()

        async def _toggle_schedule(definition: dict, enabled: bool) -> None:
            try:
                await client.update_job_definition(
                    token,
                    str(definition.get("id") or ""),
                    name=str(definition.get("name") or ""),
                    schedule=definition.get("schedule"),
                    enabled=enabled,
                    parameters=definition.get("parameters") or {},
                )
            except CoreError as exc:
                ui.notify(t("export.schedule_failed").format(err=exc), type="negative")

        async def _render_schedules() -> None:
            schedule_box.clear()
            if not auth.is_admin():
                with schedule_box:
                    ui.label(t("export.schedule_admin_only")).classes("text-caption text-gray-500")
                return
            try:
                definitions = await client.list_job_definitions(
                    token, job_type="outbound_export", limit=200
                )
            except Exception:  # noqa: BLE001 - degrade to an empty list
                definitions = []
            structured = [d for d in definitions if (d.get("parameters") or {}).get("source")]
            with schedule_box:
                if not structured:
                    ui.label(t("export.schedule_none")).classes("text-caption text-gray-500")
                    return
                for definition in structured:
                    definition_id = str(definition.get("id") or "")
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.label(str(definition.get("name") or "")).classes("font-medium")
                        ui.badge(str(definition.get("schedule") or t("jobs.manual_only"))).props(
                            "outline"
                        )
                        ui.switch(
                            value=bool(definition.get("enabled")),
                            on_change=lambda e, d=definition: _toggle_schedule(d, e.value),
                        ).props("dense")
                        ui.button(
                            icon="play_arrow",
                            on_click=lambda d=definition_id: _run_now(d),
                        ).props("flat dense").tooltip(t("export.schedule_run_now"))
                        ui.button(
                            icon="delete",
                            on_click=lambda d=definition_id: _delete_schedule(d),
                        ).props("flat dense color=negative").tooltip(t("export.schedule_delete"))

        #: Run ids of the currently rendered "Recent exports" rows (for "delete all").
        recent_ids: list[str] = []

        async def _delete_run(run_id: str) -> None:
            try:
                await client.delete_job_run(token, run_id)
            except CoreError as exc:
                ui.notify(t("export.recent_delete_failed").format(err=exc), type="negative")
                return
            ui.notify(t("export.recent_deleted"), type="positive")
            await _render_recent()

        def _confirm_delete_all(run_ids: list[str]) -> None:
            if not run_ids:
                return
            with ui.dialog() as dialog, ui.card().classes("w-96 max-w-[92vw]"):
                ui.label(t("export.recent_delete_all_confirm").format(count=len(run_ids))).classes(
                    "text-subtitle1"
                )
                ui.label(t("export.recent_delete_all_hint")).classes("text-caption text-gray-500")

                async def _go() -> None:
                    dialog.close()
                    for run_id in run_ids:
                        try:
                            await client.delete_job_run(token, run_id)
                        except CoreError:  # noqa: PERF203 - keep deleting the rest
                            continue
                    await _render_recent()

                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("export.close"), on_click=dialog.close).props("flat")
                    ui.button(t("export.recent_delete_all"), on_click=_go).props("color=negative")
            dialog.open()

        async def _render_recent() -> None:
            recent_box.clear()
            recent_ids.clear()
            try:
                runs = await client.list_job_runs(token, limit=100)
            except Exception:  # noqa: BLE001 - degrade to an empty list
                runs = []
            exports = [
                run for run in runs if str((run.get("result") or {}).get("format") or "") in FORMATS
            ]
            with recent_box:
                if not exports:
                    ui.label(t("export.recent_none")).classes("text-caption text-gray-500")
                    return
                for run in exports[:20]:
                    result = run.get("result") or {}
                    fmt = str(result.get("format") or "csv")
                    run_id = str(run.get("id") or "")
                    recent_ids.append(run_id)
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.label(_fmt_dt(run.get("created_at"))).classes("text-caption w-44")
                        ui.badge(fmt.upper()).props("outline")
                        ui.label(
                            t("export.recent_count").format(count=result.get("exported", 0))
                        ).classes("text-caption")
                        ui.label(str(run.get("trigger") or "")).classes(
                            "text-caption text-gray-500"
                        )
                        ui.button(
                            icon="download",
                            on_click=lambda rid=run_id, f=fmt: _download(rid, f),
                        ).props("flat dense ml-auto").tooltip(t("export.download"))
                        if auth.is_admin():
                            ui.button(
                                icon="delete",
                                on_click=lambda rid=run_id: _delete_run(rid),
                            ).props("flat dense color=negative").tooltip(t("export.recent_delete"))

        with ui.card().classes("w-full mt-4"):
            ui.label(t("export.schedule_section")).classes("text-subtitle1")
            ui.label(t("export.schedule_hint")).classes("text-caption text-gray-500")
            schedule_box = ui.column().classes("w-full mt-2")

        with ui.card().classes("w-full mt-4"):
            with ui.row().classes("w-full items-center"):
                ui.label(t("export.recent_section")).classes("text-subtitle1")
                with ui.row().classes("ml-auto gap-1"):
                    if auth.is_admin():
                        ui.button(
                            t("export.recent_delete_all"),
                            icon="delete_sweep",
                            on_click=lambda: _confirm_delete_all(recent_ids),
                        ).props("flat dense color=negative")
                    ui.button(icon="refresh", on_click=lambda: _render_recent()).props("flat dense")
            recent_box = ui.column().classes("w-full mt-2")

        schedule_dialog = ui.dialog()
        with schedule_dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
            ui.label(t("export.schedule_title")).classes("text-h6")
            sched_name = (
                ui.input(t("export.schedule_name")).props("outlined dense").classes("w-full")
            )
            sched_cron = (
                ui.input(t("export.schedule_cron"), value="0 0 3 * * *")
                .props("outlined dense")
                .classes("w-full")
            )
            ui.label(t("export.schedule_cron_hint")).classes("text-caption text-gray-500")
            sched_enabled = ui.switch(t("export.schedule_enabled"), value=True)

            async def _create_schedule() -> None:
                kind = str(kind_select.value)
                detail_value = (
                    str(detail_select.value) if kind != "selection" else str(ids_box.value or "")
                )
                if not detail_value:
                    ui.notify(t("export.source_required"), type="warning")
                    return
                fields = _selected_fields()
                if not fields:
                    ui.notify(t("export.fields_required"), type="warning")
                    return
                name = str(sched_name.value or "").strip()
                if not name:
                    ui.notify(t("export.schedule_name_required"), type="warning")
                    return
                cron = str(sched_cron.value or "").strip()
                if not cron:
                    ui.notify(t("export.schedule_cron_required"), type="warning")
                    return
                parameters = {
                    "source": _source_payload(kind, detail_value),
                    "fields": fields,
                    "format": _format(),
                    "locales": _locale_values(),
                    "options": {
                        "header": bool(header_switch.value),
                        "separator": str(separator_select.value or ","),
                        "include_system": bool(include_system_switch.value),
                    },
                }
                recipients = _email_recipients()
                if recipients:
                    parameters["options"]["email_to"] = recipients
                try:
                    await client.create_job_definition(
                        token,
                        name=name,
                        job_type="outbound_export",
                        schedule=cron,
                        parameters=parameters,
                        enabled=bool(sched_enabled.value),
                    )
                except CoreError as exc:
                    ui.notify(t("export.schedule_failed").format(err=exc), type="negative")
                    return
                schedule_dialog.close()
                ui.notify(t("export.schedule_created"), type="positive")
                await _render_schedules()

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("export.close"), on_click=schedule_dialog.close).props("flat")
                ui.button(
                    t("export.schedule_create"), icon="schedule", on_click=_create_schedule
                ).props("unelevated")

        if schedule_button is not None:
            schedule_button.on_click(schedule_dialog.open)
        await _render_schedules()
        await _render_recent()


async def _wait_for_run(client: CoreClient, token: str, run_id: str) -> dict:
    """Polls a job run until it reaches a terminal state."""
    for _ in range(120):
        runs = await client.list_job_runs(token, ids=[run_id], limit=1)
        if runs:
            run = runs[0]
            status = str(run.get("status") or "")
            if status in ("success", "failed", "retry"):
                return run
        await asyncio.sleep(1)
    return {"status": "failed", "error": "timeout"}


__all__ = ["export_page"]

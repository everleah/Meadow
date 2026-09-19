"""Saved export profile admin page (`/export-profiles`, admin).

An **export profile** is the reusable configuration of the export wizard: the
format plus the field list, the locales, the options and the `bmecat` block — the
`ExportRequest` **without `source`**. The wizard (`/export`) loads a profile to
prefill its form and can save the current settings as a new one; this page is the
place to maintain them (rename by re-creating, change the parameters as JSON,
delete).

The parameter editor is a JSON textarea on purpose: the profile carries exactly
what `POST /exports` accepts, so an operator can copy a working request body in
and out. The core validates the shape (`fields`/`locales` arrays, `options`/
`bmecat` objects, unknown keys rejected) and reports the reason, which is shown
on save failure.
See `plan/detail/bmecat-export.md` §5 and `plan/detail/datenexport-import-ui.md`.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.ai_assist import ai_state, open_suggestion_dialog
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.export_page import FORMATS
from meadow.frontend.i18n import t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell


def _fmt_dt(value: object) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:  # leave the raw value on parse failure
        return str(value)


def _parameters_text(profile: dict) -> str:
    """The profile parameters as pretty JSON for the editor."""
    parameters = profile.get("parameters")
    return json.dumps(parameters or {}, indent=2, ensure_ascii=False, sort_keys=True)


def _parse_text(raw: str) -> dict:
    """The parameters text as an object (`{}` when it does not parse yet).

    Used to feed the **current** profile to the assistant as the baseline for
    "improve"; the editor's own validation still happens on save.
    """
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _summarize(parameters: object) -> str:
    """One-line summary of a profile's parameters for the table."""
    if not isinstance(parameters, dict):
        return ""
    parts: list[str] = []
    fields = parameters.get("fields")
    if isinstance(fields, list):
        parts.append(f"fields: {len(fields)}")
    locales = parameters.get("locales")
    if isinstance(locales, list) and locales:
        parts.append("locales: " + ", ".join(str(code) for code in locales))
    options = parameters.get("options")
    if isinstance(options, dict) and options:
        parts.append("options: " + ", ".join(sorted(str(key) for key in options)))
    bmecat = parameters.get("bmecat")
    if isinstance(bmecat, dict) and bmecat:
        dialect = str(bmecat.get("version") or "2005")
        variant = bmecat.get("schema_variant")
        parts.append(f"bmecat: {dialect}" + (f"/{variant}" if variant else ""))
    return " | ".join(parts) or t("export_profiles.no_parameters")


@ui.page("/export-profiles", response_timeout=PAGE_TIMEOUT)
async def export_profiles_page() -> None:
    await build_shell(active="/export-profiles")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        profiles = await client.list_export_profiles(token)
    except CoreError, httpx.HTTPError:  # show a friendly message on failure
        ui.label(t("export_profiles.load_failed")).classes("text-subtitle1 text-negative")
        return
    profiles = sorted(profiles, key=lambda row: str(row.get("name") or "").lower())
    # Whether the AI assistant can be used (opt-in, configured, admin page) — and
    # why not, so the editor says it instead of hiding the button silently.
    ai = await ai_state(client, token)

    def _rows() -> list[dict]:
        return [
            {
                "id": str(profile.get("id") or ""),
                "name": str(profile.get("name") or ""),
                "format": str(profile.get("format") or "").upper(),
                "parameters": _summarize(profile.get("parameters")),
                "notes": str(profile.get("notes") or ""),
                "updated_at": _fmt_dt(profile.get("updated_at")),
            }
            for profile in profiles
        ]

    with ui.column().classes("w-full p-6"):
        ui.label(t("export_profiles.title")).classes("text-h4")
        ui.label(t("export_profiles.subtitle")).classes("text-subtitle1")

        column_defs = [
            {"headerName": t("export_profiles.col_name"), "field": "name", "width": 240},
            {"headerName": t("export_profiles.col_format"), "field": "format", "width": 110},
            {"headerName": t("export_profiles.col_parameters"), "field": "parameters", "flex": 1},
            {"headerName": t("export_profiles.col_notes"), "field": "notes", "width": 220},
            {"headerName": t("export_profiles.col_updated"), "field": "updated_at", "width": 170},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            }
        ).classes("w-full")
        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            ui.button(
                t("export_profiles.new"), icon="add", on_click=lambda: open_editor(None)
            ).props("flat")
            ui.button(
                t("export_profiles.reload"), icon="refresh", on_click=lambda: reload_profiles()
            ).props("flat")

        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[46rem] max-w-[94vw]"):
            editor_title = ui.label("").classes("text-h6")
            state: dict = {"editing_id": None}
            with ui.column().classes("w-full mt-2 gap-2"):
                name_input = (
                    ui.input(t("export_profiles.field_name"))
                    .props("filled dense")
                    .classes("w-full")
                )
                format_select = (
                    ui.select(
                        {fmt: fmt.upper() for fmt in FORMATS},
                        value="csv",
                        label=t("export_profiles.field_format"),
                    )
                    .props("filled dense")
                    .classes("w-40")
                )
                notes_input = (
                    ui.input(t("export_profiles.field_notes"))
                    .props("filled dense")
                    .classes("w-full")
                )
                parameters_input = (
                    ui.textarea(t("export_profiles.field_parameters"), value="{}")
                    .props("filled dense autogrow input-style=font-family:monospace")
                    .classes("w-full")
                )
                ui.label(t("export_profiles.parameters_hint")).classes("text-caption text-gray-500")
                with ui.row().classes("w-full items-center gap-2"):
                    if ai.get("available"):
                        ui.button(
                            t("export_profiles.ai_suggest"),
                            icon="auto_awesome",
                            on_click=lambda: suggest_profile(),
                        ).props("flat dense")
                        ui.label(t("export_profiles.ai_suggest_hint")).classes(
                            "text-caption text-gray-500"
                        )
                    else:
                        ui.label(t(str(ai.get("reason") or "ai.unavailable"))).classes(
                            "text-caption text-gray-500"
                        )
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("export_profiles.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                    ui.button(
                        t("export_profiles.save"), icon="save", on_click=lambda: do_save()
                    ).props("flat")

        def _fill_editor(profile: dict | None) -> None:
            editing = profile is not None
            profile = profile or {}  # the reads below only run while editing
            editor_title.set_text(
                t("export_profiles.edit") if editing else t("export_profiles.new")
            )
            state["editing_id"] = profile.get("id") if editing else None
            delete_btn.visible = editing
            name_input.set_value(str(profile.get("name") or "") if editing else "")
            # The name is the record key, so it cannot change after creation.
            name_input.disable() if editing else name_input.enable()
            format_select.set_value(
                str(profile.get("format") or "csv").lower() if editing else "csv"
            )
            notes_input.set_value(str(profile.get("notes") or "") if editing else "")
            parameters_input.set_value(_parameters_text(profile) if editing else "{}")

        def open_editor(profile: dict | None) -> None:
            _fill_editor(profile)
            editor_dialog.open()

        def suggest_profile() -> None:
            """AI suggestion for format + parameters; the answer fills both fields."""

            def apply(reply: dict) -> None:
                profile = reply.get("profile")
                if not isinstance(profile, dict):
                    return
                fmt = str(profile.get("format") or "").lower()
                if fmt in FORMATS:
                    format_select.set_value(fmt)
                parameters_input.set_value(
                    json.dumps(profile.get("parameters") or {}, indent=2, ensure_ascii=False)
                )

            open_suggestion_dialog(
                client=client,
                token=token,
                area="export_profile",
                title_key="export_profiles.ai_title",
                config_kind=lambda: str(format_select.value or ""),
                current_script=lambda: json.dumps(
                    {
                        "format": str(format_select.value or ""),
                        "parameters": _parse_text(str(parameters_input.value or "")),
                    },
                    ensure_ascii=False,
                ),
                on_apply=apply,
            )

        async def reload_profiles() -> None:
            nonlocal profiles
            try:
                profiles = await client.list_export_profiles(token)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                return
            profiles = sorted(profiles, key=lambda row: str(row.get("name") or "").lower())
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def do_save() -> None:
            raw = str(parameters_input.value or "").strip() or "{}"
            try:
                parameters = json.loads(raw)
            except ValueError as error:
                ui.notify(t("export_profiles.invalid_json").format(err=error), color="negative")
                return
            if not isinstance(parameters, dict):
                ui.notify(t("export_profiles.invalid_root"), color="negative")
                return
            editing_id = state.get("editing_id")
            try:
                if editing_id:
                    await client.update_export_profile(
                        token,
                        str(editing_id),
                        format=str(format_select.value or "csv"),
                        notes=str(notes_input.value or ""),
                        parameters=parameters,
                    )
                else:
                    name = str(name_input.value or "").strip()
                    if not name:
                        ui.notify(t("export_profiles.name_required"), color="warning")
                        return
                    await client.create_export_profile(
                        token,
                        name=name,
                        format=str(format_select.value or "csv"),
                        notes=str(notes_input.value or ""),
                        parameters=parameters,
                    )
            except CoreError as error:
                ui.notify(f"{t('export_profiles.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload_profiles()
            ui.notify(t("export_profiles.saved"), color="positive")

        async def do_delete() -> None:
            editing_id = state.get("editing_id")
            if not editing_id:
                return
            name = str(name_input.value or "")
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("export_profiles.delete_confirm").format(name=name)).classes(
                    "text-subtitle1"
                )
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_export_profile(token, str(editing_id))
                        except CoreError as error:
                            ui.notify(
                                f"{t('export_profiles.delete_failed')}: {error}", color="negative"
                            )
                            return
                        editor_dialog.close()
                        await reload_profiles()
                        ui.notify(t("export_profiles.deleted"), color="positive")

                    ui.button(t("export_profiles.delete"), on_click=_do).props("color=negative")
            confirm.open()

        def _profile_for(data: object) -> dict | None:
            """Resolves a double-click payload to the full API profile record."""
            profile_id = str(data.get("id") or "") if isinstance(data, dict) else str(data or "")
            if not profile_id:
                return None
            return next((p for p in profiles if str(p.get("id")) == profile_id), None)

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(_profile_for(e.args.get("data"))),
            ["data"],
        )

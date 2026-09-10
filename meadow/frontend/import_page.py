"""UI-guided tabular import page (`/import`, editor+).

Uploads a CSV/XLSX file, lets the user map its columns to object fields
(`id`/`type`/`status`) or attributes (incl. multilingual `attr|locale` targets),
shows a preview of the resulting rows, and imports them through the existing
MEF pipeline (`POST /imports` → `inbound_import` worker, upsert by business key
or source reference).
"""

from __future__ import annotations

import asyncio

from nicegui import app, ui

from meadow import tabular
from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell

#: Import job types whose runs we poll (the `inbound_import` worker result).
_TERMINAL_STATUSES = ("success", "failed", "retry")


def _auto_target(column: str, target_options: dict[str, str]) -> str:
    """Auto-maps a column whose (normalised) name matches a mapping target."""
    key = (column or "").strip().lower()
    for target in target_options:
        if target and target.lower() == key:
            return target
    return ""


async def _read_upload(event) -> tuple[str, bytes]:
    """Reads the uploaded file's name and bytes across NiceGUI/Starlette shapes."""
    file = getattr(event, "file", None)
    if file is None:
        return "", b""
    name = getattr(file, "filename", None) or getattr(file, "name", "") or ""
    data: object = b""
    reader = getattr(file, "read", None)
    if reader is not None:
        result = reader()
        data = await result if asyncio.iscoroutine(result) else result
    if not data and hasattr(file, "content"):
        content = file.content
        data = await content if asyncio.iscoroutine(content) else content
    if isinstance(data, str):
        data = data.encode("utf-8")
    return str(name), bytes(data or b"")


async def _wait_run(client: CoreClient, token: str, run_id: str) -> dict:
    """Polls a job run until it reaches a terminal state."""
    for _ in range(120):
        try:
            runs = await client.list_job_runs(token, ids=[run_id], limit=1)
        except Exception:  # noqa: BLE001 - keep polling on a transient error
            runs = []
        if runs:
            run = runs[0]
            if str(run.get("status") or "") in _TERMINAL_STATUSES:
                return run
        await asyncio.sleep(1)
    return {"status": "failed", "error": "timeout"}


@ui.page("/import", response_timeout=15)
async def import_page() -> None:
    """The tabular import page."""
    await build_shell(active="/import")
    if not auth.is_editor():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    async def _safe(coro) -> list[dict]:
        try:
            return await coro
        except Exception:  # noqa: BLE001 - degrade so the page still opens
            return []

    object_types = await _safe(client.list_object_types(token))
    definitions = await _safe(client.list_attribute_definitions(token, limit=1000))
    languages = await _safe(client.list_languages(token))
    data_languages = [str(lang.get("code")) for lang in languages] or ["de"]
    attribute_names = [str(d.get("name")) for d in definitions if d.get("name")]
    multilingual = {str(d.get("name")) for d in definitions if d.get("multilingual")}

    type_options = {str(o.get("name")): str(o.get("name")) for o in object_types}

    # Convention attributes: object types name a label attribute (and key attributes)
    # that need not exist as an attribute definition — e.g. `name`, which the grid and
    # the exports use as the display name. Offer them as mapping targets explicitly so
    # a grid export (`id,type,name,…`) can be re-imported without manual typing.
    conventional_names: list[str] = []
    for object_type in object_types:
        candidates = [
            object_type.get("label_attribute"),
            *(object_type.get("key_attributes") or []),
        ]
        for candidate in candidates:
            candidate_name = str(candidate or "")
            if candidate_name in ("", *conventional_names, *attribute_names):
                continue
            conventional_names.append(candidate_name)

    target_options: dict[str, str] = {
        "": t("import.ignore"),
        "id": "id",
        "type": "type",
        "status": "status",
    }
    for name in [*attribute_names, *conventional_names]:
        target_options[name] = (
            f"{name} ({t('import.current_language')})" if name in conventional_names else name
        )
        if name in multilingual or name in conventional_names:
            for lang in data_languages:
                target_options[f"{name}{tabular.LOCALE_SEPARATOR}{lang}"] = f"{name} [{lang}]"

    key_names = [*attribute_names, *conventional_names]
    # Convention attributes are multilingual by convention (e.g. `name` is stored as
    # `{lang: value}`); a target without an explicit locale is written in the UI language.
    main_lang = current_locale().split("-")[0] or "de"

    state: dict = {
        "table": tabular.TableData(),
        "filename": "",
        "content": b"",
        "sheets": [],
        "sheet": "",
    }
    column_selects: dict[str, ui.select] = {}

    with ui.column().classes("w-full p-6"):
        ui.label(t("import.title")).classes("text-h4")
        ui.label(t("import.subtitle")).classes("text-subtitle1")

        with ui.card().classes("w-full mt-4"):
            ui.label(t("import.upload")).classes("text-subtitle1")
            ui.label(t("import.upload_hint")).classes("text-caption text-gray-500")
            parse_status = ui.label("").classes("text-caption text-gray-600")

            def _set_parse_status() -> None:
                table = state["table"]
                parse_status.set_text(
                    t("import.parsed_file").format(
                        file=state["filename"],
                        rows=len(table.rows),
                        columns=len(table.columns),
                    )
                )

            @ui.refreshable
            def mapping_area() -> None:
                column_selects.clear()
                sheets = state.get("sheets") or []
                if len(sheets) > 1:

                    def _on_sheet(event) -> None:
                        sheet = str(event.value or "")
                        state["sheet"] = sheet
                        state["table"] = tabular.parse_xlsx(state["content"], sheet)
                        preview_box.clear()
                        _set_parse_status()
                        mapping_area.refresh()

                    ui.select(
                        {name: name for name in sheets},
                        value=state.get("sheet") or sheets[0],
                        label=t("import.sheet"),
                        on_change=_on_sheet,
                    ).props("filled dense").classes("w-56")

                columns = state["table"].columns
                ui.label(t("import.mapping_section")).classes("text-subtitle1")
                if not columns:
                    message = t("import.empty_sheet") if state["filename"] else t("import.no_file")
                    ui.label(message).classes("text-caption text-gray-500")
                    return
                ui.label(t("import.mapping_hint")).classes("text-caption text-gray-500")
                with ui.row().classes("flex-wrap gap-4 mt-2"):
                    for column in columns:
                        select = (
                            ui.select(
                                target_options,
                                value=_auto_target(column, target_options),
                                label=column,
                                new_value_mode="add-unique",
                            )
                            .props("filled dense use-input")
                            .classes("w-56")
                        )
                        column_selects[column] = select

            async def on_upload(event) -> None:
                name, content = await _read_upload(event)
                if not content:
                    ui.notify(t("import.empty_file"), type="negative")
                    return
                try:
                    parsed = tabular.parse_table(name, content)
                    sheets = (
                        tabular.sheet_names(content)
                        if name.lower().endswith((".xlsx", ".xlsm"))
                        else []
                    )
                except Exception as exc:  # noqa: BLE001 - unreadable file
                    ui.notify(t("import.parse_failed").format(err=exc), type="negative")
                    return
                state["table"] = parsed
                state["filename"] = name
                state["content"] = content
                state["sheets"] = sheets
                state["sheet"] = sheets[0] if sheets else ""
                mapping_area.refresh()
                preview_box.clear()
                _set_parse_status()

            upload = ui.upload(on_upload=on_upload, auto_upload=True).props(
                "accept=.csv,.xlsx,.xlsm flat max-files=1"
            )
            upload.classes("mt-2")

            with ui.row().classes("flex-wrap gap-6 mt-3 items-center"):
                default_type_select = (
                    ui.select(
                        type_options,
                        value=(next(iter(type_options), None)),
                        label=t("import.default_type"),
                    )
                    .props("filled dense")
                    .classes("w-56")
                )
                key_select = (
                    ui.select(
                        {"": t("import.key_none"), **{name: name for name in key_names}},
                        value="",
                        label=t("import.key_attribute"),
                        new_value_mode="add-unique",
                    )
                    .props("filled dense use-input")
                    .classes("w-56")
                )
                source_input = (
                    ui.input(t("import.source"), value="tabular-import")
                    .props("outlined dense")
                    .classes("w-56")
                )
                emit_events_switch = ui.switch(t("import.emit_events"), value=True)

        with ui.card().classes("w-full mt-4"):
            mapping_area()

        preview_box = ui.column().classes("w-full mt-4")

        def _current_mapping() -> dict[str, str]:
            mapping = {column: str(select.value or "") for column, select in column_selects.items()}
            for column, target in mapping.items():
                if target in conventional_names:
                    mapping[column] = f"{target}{tabular.LOCALE_SEPARATOR}{main_lang}"
            return mapping

        def _build_document() -> dict:
            default_type = str(default_type_select.value or "") or next(
                iter(type_options), "product"
            )
            return tabular.build_mef(
                state["table"],
                _current_mapping(),
                default_type=default_type,
                source=str(source_input.value or "tabular-import"),
            )

        def _types_summary(objects: list[dict]) -> str:
            counts: dict[str, int] = {}
            for obj in objects:
                key = str(obj.get("type") or "")
                counts[key] = counts.get(key, 0) + 1
            return ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))

        async def on_preview() -> None:
            if not state["table"].columns:
                ui.notify(t("import.no_file"), type="warning")
                return
            document = _build_document()
            objects = document["objects"]
            preview_box.clear()
            with preview_box, ui.card().classes("w-full"):
                ui.label(t("import.preview_section")).classes("text-subtitle1")
                ui.label(
                    t("import.preview_summary").format(
                        rows=len(objects), types=_types_summary(objects)
                    )
                ).classes("text-caption text-gray-500")
                ui.table(
                    columns=[
                        {"name": "type", "label": "type", "field": "type", "align": "left"},
                        {"name": "status", "label": "status", "field": "status", "align": "left"},
                        {
                            "name": "attributes",
                            "label": t("import.attributes"),
                            "field": "attributes",
                            "align": "left",
                        },
                    ],
                    rows=[
                        {
                            "type": obj["type"],
                            "status": obj["status"],
                            "attributes": ", ".join(obj["attributes"].keys()),
                        }
                        for obj in objects[:20]
                    ],
                ).classes("w-full")

        def _show_report(result: dict) -> None:
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
                ui.label(t("import.report_title")).classes("text-subtitle1")
                ui.label(
                    t("import.report_summary").format(
                        processed=result.get("processed", 0),
                        created=result.get("created", 0),
                        updated=result.get("updated", 0),
                        failed=result.get("failed", 0),
                    )
                ).classes("text-subtitle1 font-medium")
                failures = [
                    item for item in (result.get("results") or []) if item.get("status") == "failed"
                ]
                if failures:
                    ui.separator()
                    ui.label(t("import.report_failures")).classes("text-subtitle2")
                    lines = [
                        f"- **{item.get('type') or ''}**: {item.get('error')}"
                        for item in failures[:20]
                    ]
                    ui.markdown("\n".join(lines)).classes("max-h-72 overflow-auto")
                with ui.row().classes("mt-3 w-full justify-end"):
                    ui.button(t("import.close"), on_click=dialog.close).props("flat")
            dialog.open()

        async def on_import() -> None:
            if not state["table"].columns:
                ui.notify(t("import.no_file"), type="warning")
                return
            document = _build_document()
            if not document["objects"]:
                ui.notify(t("import.no_rows"), type="warning")
                return
            key = str(key_select.value or "") or None
            import_button.props("loading")
            import_button.enabled = False
            preview_button.enabled = False
            try:
                queued = await client.create_import_mef(
                    token,
                    document,
                    match_by_key=key,
                    source=str(source_input.value or "tabular-import"),
                    emit_events=bool(emit_events_switch.value),
                )
                run_id = str(queued.get("job_run_id") or "")
                run = await _wait_run(client, token, run_id)
            except CoreError as exc:
                ui.notify(t("import.import_failed").format(err=exc), type="negative")
                return
            finally:
                import_button.props(remove="loading")
                import_button.enabled = True
                preview_button.enabled = True
            if run.get("status") != "success":
                error = run.get("error") or run.get("status")
                ui.notify(t("import.import_failed").format(err=error), type="negative")
                return
            _show_report(run.get("result") or {})

        with ui.row().classes("mt-4 gap-2"):
            preview_button = ui.button(t("import.preview"), on_click=on_preview).props("outline")
            import_button = ui.button(t("import.import"), icon="upload", on_click=on_import).props(
                "unelevated"
            )


__all__ = ["import_page"]

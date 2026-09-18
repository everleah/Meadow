"""UI-guided import page (`/import`, editor+).

Two modes, detected from the uploaded file:

- **Tabular** (CSV/XLSX): columns are mapped to object fields (`id`/`type`/`status`)
  or attributes (incl. multilingual `attr|locale` targets) via :mod:`meadow.tabular`.
- **MEF JSON**: a Meadow Exchange Format document (as written by the export
  wizard/`json_export` connector) is imported as-is; the page shows a summary and
  object preview, plus the `source`, `match_by_key` and `match_by_id` options.

Both modes preview the result and import through the existing pipeline
(`POST /imports` → `inbound_import` worker, upsert by business key, object id or
source reference).
"""

from __future__ import annotations

import asyncio

import httpx
from nicegui import app, ui

from meadow import mef, tabular
from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.help_page import chapter_button
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell

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
    if not isinstance(data, (bytes, bytearray)):
        # The old code called `bytes(data)` here, which raised a bare TypeError for
        # an unexpected payload; name the problem instead of hiding it.
        raise TypeError(f"uploaded file {name!r} carries {type(data).__name__}, not bytes")
    return str(name), bytes(data)


async def _wait_run(client: CoreClient, token: str, run_id: str) -> dict:
    """Polls a job run until it reaches a terminal state."""
    for _ in range(120):
        try:
            runs = await client.list_job_runs(token, ids=[run_id], limit=1)
        except CoreError, httpx.HTTPError:  # keep polling on a transient error
            runs = []
        if runs:
            run = runs[0]
            if str(run.get("status") or "") in _TERMINAL_STATUSES:
                return run
        await asyncio.sleep(1)
    return {"status": "failed", "error": "timeout"}


@ui.page("/import", response_timeout=PAGE_TIMEOUT)
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
        except CoreError, httpx.HTTPError:  # degrade so the page still opens
            return []

    object_types = await _safe(client.list_object_types(token))
    # The **whole** vocabulary: a mapping target may be an own attribute or a
    # standard feature (a supplier file often fills the standard's fields), and the
    # capped paged list would hide every own attribute behind 1000 `EF…` rows.
    definitions = await _safe(client.list_attribute_vocabulary(token))
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
        "mode": "",
        "table": tabular.TableData(),
        "document": {},
        "summary": {},
        "filename": "",
        "content": b"",
        "sheets": [],
        "sheet": "",
    }
    column_selects: dict[str, ui.select] = {}

    with ui.column().classes("w-full p-6"):
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label(t("import.title")).classes("text-h4 grow")
            # One handbook entry point per working area (see docs/AGENTS.md).
            chapter_button("import")
        ui.label(t("import.subtitle")).classes("text-subtitle1")

        with ui.card().classes("w-full mt-4"):
            ui.label(t("import.upload")).classes("text-subtitle1")
            ui.label(t("import.upload_hint")).classes("text-caption text-gray-500")
            parse_status = ui.label("").classes("text-caption text-gray-600")

            def _set_parse_status() -> None:
                if state["mode"] == "mef":
                    summary = state["summary"]
                    parse_status.set_text(
                        t("import.parsed_mef").format(
                            file=state["filename"],
                            objects=summary.get("objects", 0),
                            references=summary.get("references", 0),
                        )
                    )
                    return
                table = state["table"]
                parse_status.set_text(
                    t("import.parsed_file").format(
                        file=state["filename"],
                        rows=len(table.rows),
                        columns=len(table.columns),
                    )
                )

            def _fact(label: str, value: str) -> None:
                with ui.column().classes("gap-0"):
                    ui.label(label).classes("text-caption text-gray-500")
                    ui.label(value).classes("text-body1")

            def _types_text(types: dict) -> str:
                return ", ".join(f"{name or '?'}: {count}" for name, count in sorted(types.items()))

            def _render_mef_summary() -> None:
                summary = state["summary"]
                ui.label(t("import.mef_section")).classes("text-subtitle1")
                ui.label(t("import.mef_hint")).classes("text-caption text-gray-500")
                with ui.row().classes("flex-wrap gap-6 mt-2"):
                    _fact(t("import.mef_version"), summary.get("format_version") or "–")
                    _fact(t("import.mef_source"), summary.get("source") or "–")
                    _fact(t("import.mef_objects"), str(summary.get("objects", 0)))
                    _fact(t("import.mef_references"), str(summary.get("references", 0)))
                    _fact(t("import.mef_with_ids"), str(summary.get("with_ids", 0)))
                types = summary.get("types") or {}
                if types:
                    ui.label(t("import.mef_types").format(types=_types_text(types))).classes(
                        "text-caption text-gray-600"
                    )

            @ui.refreshable
            def detail_area() -> None:
                if state["mode"] == "mef":
                    _render_mef_summary()
                    return

                column_selects.clear()
                sheets = state.get("sheets") or []
                if len(sheets) > 1:

                    def _on_sheet(event) -> None:
                        sheet = str(event.value or "")
                        state["sheet"] = sheet
                        state["table"] = tabular.parse_xlsx(state["content"], sheet)
                        preview_box.clear()
                        _set_parse_status()
                        detail_area.refresh()

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

            def _apply_mode_visibility() -> None:
                is_mef = state["mode"] == "mef"
                default_type_select.set_visibility(not is_mef)
                match_by_id_switch.set_visibility(is_mef)

            def _set_key_options(names: list[str]) -> None:
                options = {"": t("import.key_none"), **{name: name for name in names}}
                key_select.options = options
                key_select.update()

            async def on_upload(event) -> None:
                name, content = await _read_upload(event)
                if not content:
                    ui.notify(t("import.empty_file"), type="negative")
                    return
                lowered = name.lower()
                is_workbook = lowered.endswith((".xlsx", ".xlsm"))
                is_json = lowered.endswith((".json", ".mef")) or content.lstrip()[:1] in (
                    b"{",
                    b"[",
                )
                try:
                    if is_json:
                        document = mef.parse_mef(content)
                        state["document"] = document
                        state["summary"] = mef.summarize(document)
                        state["mode"] = "mef"
                        # A Meadow export carries `source: "meadow"` and the core record
                        # ids in `id` → default to matching by object id (update instead
                        # of duplicating on re-import).
                        source_input.value = state["summary"].get("source") or ""
                        match_by_id_switch.value = state["summary"].get("source") == "meadow"
                        document_names = [
                            str(attribute)
                            for entry in document.get("objects") or []
                            if isinstance(entry, dict) and isinstance(entry.get("attributes"), dict)
                            for attribute in entry["attributes"]
                        ]
                        _set_key_options(sorted({*key_names, *document_names}))
                    else:
                        parsed = tabular.parse_table(name, content)
                        sheets = tabular.sheet_names(content) if is_workbook else []
                        state["table"] = parsed
                        state["sheets"] = sheets
                        state["sheet"] = sheets[0] if sheets else ""
                        state["mode"] = "tabular"
                        state["document"] = {}
                        state["summary"] = {}
                        _set_key_options(key_names)
                except Exception as exc:  # noqa: BLE001 - the CSV/XLSX/JSON decoders raise their own types
                    ui.notify(t("import.parse_failed").format(err=exc), type="negative")
                    return
                state["filename"] = name
                state["content"] = content
                detail_area.refresh()
                _apply_mode_visibility()
                preview_box.clear()
                _set_parse_status()

            upload = ui.upload(on_upload=on_upload, auto_upload=True).props(
                "accept=.csv,.xlsx,.xlsm,.json flat max-files=1"
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
                match_by_id_switch = ui.switch(t("import.match_by_id"), value=False)
                match_by_id_switch.tooltip(t("import.match_by_id_hint"))
                match_by_id_switch.set_visibility(False)

        with ui.card().classes("w-full mt-4"):
            detail_area()

        preview_box = ui.column().classes("w-full mt-4")

        def _current_mapping() -> dict[str, str]:
            mapping = {column: str(select.value or "") for column, select in column_selects.items()}
            for column, target in mapping.items():
                if target in conventional_names:
                    mapping[column] = f"{target}{tabular.LOCALE_SEPARATOR}{main_lang}"
            return mapping

        def _build_document() -> dict:
            if state["mode"] == "mef":
                return state["document"]
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

        def _preview_table(columns: list[dict], rows: list[dict]) -> None:
            ui.table(columns=columns, rows=rows).classes("w-full")

        async def on_preview() -> None:
            if not state["mode"]:
                ui.notify(t("import.no_file"), type="warning")
                return
            preview_box.clear()
            if state["mode"] == "mef":
                summary = state["summary"]
                with preview_box, ui.card().classes("w-full"):
                    ui.label(t("import.preview_section")).classes("text-subtitle1")
                    ui.label(
                        t("import.preview_summary").format(
                            rows=summary.get("objects", 0),
                            types=_types_text(summary.get("types") or {}) or "–",
                        )
                    ).classes("text-caption text-gray-500")
                    _preview_table(
                        [
                            {"name": "id", "label": "id", "field": "id", "align": "left"},
                            {"name": "type", "label": "type", "field": "type", "align": "left"},
                            {
                                "name": "status",
                                "label": "status",
                                "field": "status",
                                "align": "left",
                            },
                            {
                                "name": "attributes",
                                "label": t("import.attributes"),
                                "field": "attributes",
                                "align": "left",
                            },
                        ],
                        mef.object_rows(state["document"]),
                    )
                return
            if not state["table"].columns:
                ui.notify(t("import.no_file"), type="warning")
                return
            document = _build_document()
            objects = document["objects"]
            with preview_box, ui.card().classes("w-full"):
                ui.label(t("import.preview_section")).classes("text-subtitle1")
                ui.label(
                    t("import.preview_summary").format(
                        rows=len(objects), types=_types_summary(objects)
                    )
                ).classes("text-caption text-gray-500")
                _preview_table(
                    [
                        {"name": "type", "label": "type", "field": "type", "align": "left"},
                        {"name": "status", "label": "status", "field": "status", "align": "left"},
                        {
                            "name": "attributes",
                            "label": t("import.attributes"),
                            "field": "attributes",
                            "align": "left",
                        },
                    ],
                    [
                        {
                            "type": obj["type"],
                            "status": obj["status"],
                            "attributes": ", ".join(obj["attributes"].keys()),
                        }
                        for obj in objects[:20]
                    ],
                )

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
            if not state["mode"]:
                ui.notify(t("import.no_file"), type="warning")
                return
            is_mef = state["mode"] == "mef"
            if not is_mef and not state["table"].columns:
                ui.notify(t("import.no_file"), type="warning")
                return
            document = _build_document()
            if not document.get("objects") and not document.get("references"):
                ui.notify(t("import.no_rows"), type="warning")
                return
            key = str(key_select.value or "") or None
            source = str(source_input.value or "") or None
            if not is_mef and not source:
                source = "tabular-import"
            import_button.props("loading")
            import_button.enabled = False
            preview_button.enabled = False
            try:
                queued = await client.create_import_mef(
                    token,
                    document,
                    match_by_key=key,
                    match_by_id=is_mef and bool(match_by_id_switch.value),
                    source=source,
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

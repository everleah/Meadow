"""Admin page for system logs (admin/superadmin only).

Table (newest first) of curated log entries (``GET /system/logs``) with a filter
bar (service, category, level, time range, text search), polling (~5 s) and a
detail dialog (full message, ``context`` as JSON, ``trace_id``, context links).
"""

from __future__ import annotations

import json as _json
from datetime import datetime

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import build_shell

SERVICES = ("pim-core", "pim-worker", "pim-frontend")
CATEGORIES = ("app", "job", "rule", "auth", "integration")
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")


def _level_badge(level: str) -> str:
    """A coloured badge for a log level."""
    classes = {
        "DEBUG": "bg-neutral text-gray-600",
        "INFO": "bg-info text-white",
        "WARN": "bg-warning text-white",
        "ERROR": "bg-negative text-white",
        "CRITICAL": "bg-dark text-white",
    }.get(level, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{level}</span>'


def _fmt_dt(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001 - leave the raw value on parse failure
        return str(value)


def _json_pretty(value: object) -> str:
    try:
        return _json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - fall back to the raw string
        return str(value)


@ui.page("/system-logs", response_timeout=15)
async def logs_page() -> None:
    build_shell(active="/system-logs")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("logs.title")).classes("text-h4")
        ui.label(t("logs.subtitle")).classes("text-subtitle1")

        # Filter state (read by _rows on reload).
        filters: dict[str, object] = {"service": "", "category": "", "level": ""}
        logs: list[dict] = []

        def _context_links(context: object) -> list[tuple[str, str]]:
            """Builds `(label, href)` links from known context id keys."""
            if not isinstance(context, dict):
                return []
            links: list[tuple[str, str]] = []
            job_id = context.get("job_run_id") or context.get("run_id")
            if job_id:
                links.append((t("logs.link_job"), "/jobs"))
            object_id = context.get("object_id") or context.get("id")
            if isinstance(object_id, str) and object_id.startswith("object:"):
                links.append((t("logs.link_object"), f"/products/{object_id}"))
            rule = context.get("rule") or context.get("rule_name")
            if rule:
                links.append((t("logs.link_rule"), "/jobs"))
            return links

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(e.get("id")),
                    "time": _fmt_dt(e.get("created_at")),
                    "created_at": e.get("created_at"),
                    "service": str(e.get("service") or ""),
                    "category": str(e.get("category") or ""),
                    "level": _level_badge(str(e.get("level") or "")),
                    "level_raw": str(e.get("level") or ""),
                    "message": str(e.get("message") or "")[:120],
                    "trace_id": e.get("trace_id"),
                    "context": e.get("context"),
                }
                for e in logs
            ]

        with ui.row().classes("w-full gap-2 items-center flex-wrap mb-2"):
            service_select = (
                ui.select(
                    {"": t("logs.all"), **{s: s for s in SERVICES}},
                    label=t("logs.filter_service"),
                )
                .props("filled dense")
                .classes("w-40")
            )
            category_select = (
                ui.select(
                    {"": t("logs.all"), **{c: c for c in CATEGORIES}},
                    label=t("logs.filter_category"),
                )
                .props("filled dense")
                .classes("w-40")
            )
            level_select = (
                ui.select(
                    {"": t("logs.all"), **{level: level for level in LEVELS}},
                    label=t("logs.filter_level"),
                )
                .props("filled dense")
                .classes("w-40")
            )
            from_input = (
                ui.input(t("logs.filter_from"))
                .props("filled dense placeholder=2026-09-03T12:00:00Z")
                .classes("w-56")
            )
            to_input = (
                ui.input(t("logs.filter_to"))
                .props("filled dense placeholder=2026-09-03T13:00:00Z")
                .classes("w-56")
            )
            q_input = (
                ui.input(t("logs.filter_q")).props("filled dense").classes("grow min-w-[12rem]")
            )
            ui.button(t("jobs.reload"), icon="refresh", on_click=lambda: reload()).props("flat")

        try:
            logs = await client.list_system_logs(token, limit=200)
        except Exception:  # noqa: BLE001 - show an empty table on failure
            logs = []

        async def reload() -> None:
            nonlocal logs
            filters["service"] = service_select.value if service_select.value else ""
            filters["category"] = category_select.value if category_select.value else ""
            filters["level"] = level_select.value if level_select.value else ""
            try:
                logs = await client.list_system_logs(
                    token,
                    limit=200,
                    service=filters["service"] or None,
                    category=filters["category"] or None,
                    level=filters["level"] or None,
                    from_dt=from_input.value or None,
                    to_dt=to_input.value or None,
                    q=q_input.value or None,
                )
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        grid = (
            ui.aggrid(
                {
                    "columnDefs": [
                        {"headerName": t("logs.col_time"), "field": "time", "width": 180},
                        {"headerName": t("logs.col_service"), "field": "service", "width": 130},
                        {"headerName": t("logs.col_category"), "field": "category", "width": 110},
                        {"headerName": t("logs.col_level"), "field": "level", "width": 100},
                        {"headerName": t("logs.col_message"), "field": "message", "width": 480},
                    ],
                    "rowData": _rows(),
                    "rowSelection": {"mode": "singleRow"},
                    "domLayout": "normal",
                },
                html_columns=[3],
            )
            .classes("w-full")
            .style("height: calc(100vh - 318px)")
        )

        detail_dialog = ui.dialog()
        with (
            detail_dialog,
            ui.card().classes("w-[44rem] max-w-[94vw] max-h-[85vh] overflow-y-auto"),
        ):
            detail_title = ui.label("").classes("text-h6")
            detail_meta = ui.label("").classes("text-subtitle2 text-gray-500")
            detail_links = ui.row().classes("gap-2 mt-1")
            detail_body = ui.element("div").classes("w-full mt-2")

            def show_detail(entry: dict | None) -> None:
                if not entry:
                    return
                detail_title.set_text(f"{t('logs.detail_title')} · {entry.get('level_raw') or ''}")
                detail_meta.set_text(
                    f"{_fmt_dt(entry.get('created_at'))} · {entry.get('service')} · "
                    f"{entry.get('category')}"
                )
                detail_links.clear()
                with detail_links:
                    for label, href in _context_links(entry.get("context")):
                        ui.button(label, on_click=lambda h=href: ui.navigate.to(h)).props(
                            "flat size=sm"
                        )
                detail_body.clear()
                with detail_body:
                    ui.label(t("logs.col_message")).classes("text-subtitle2")
                    ui.code(str(entry.get("message") or "")).classes("w-full")
                    if entry.get("trace_id"):
                        ui.label("trace_id").classes("text-subtitle2 mt-2")
                        ui.code(str(entry.get("trace_id"))).classes("w-full")
                    context = entry.get("context")
                    if context is not None:
                        ui.label("context").classes("text-subtitle2 mt-2")
                        ui.code(_json_pretty(context)).classes("w-full")
                detail_dialog.open()

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=detail_dialog.close).props("flat")

        def _entry_for(data: object) -> dict | None:
            if isinstance(data, dict):
                return data
            return next((e for e in logs if str(e.get("id")) == str(data)), None)

        grid.on(
            "cellDoubleClicked",
            lambda e: show_detail(_entry_for(e.args.get("data"))),
            ["data"],
        )

        ui.timer(5.0, lambda: reload())

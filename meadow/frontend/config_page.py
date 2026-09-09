"""Admin page for Meadow Configuration Format (MCF) export/import.

`/config` (admin/superadmin only) lets an operator:

- **Export** the data model & reference data as an MCF document — choose which
  sections to include (all by default) and whether to include seeded/system
  entries — and download the resulting JSON.
- **Import** an MCF document from a JSON file/paste in `dry_run` (preview),
  `merge` (idempotent) or `replace` mode, and view the per-section report.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import build_shell

#: The MCF sections in dependency order (same as the core `/config/export`).
SECTIONS = (
    "languages",
    "units",
    "attribute_groups",
    "enum_sets",
    "object_types",
    "attribute_definitions",
    "relation_definitions",
    "business_rules",
)
#: Import modes selectable in the UI.
MODES = ("dry_run", "merge", "replace")


def _summary_text(report: dict) -> str:
    summary = report.get("summary") or {}
    return (
        f"mode={report.get('mode', '')} · "
        f"created={summary.get('created', 0)} · "
        f"updated={summary.get('updated', 0)} · "
        f"skipped={summary.get('skipped', 0)} · "
        f"deleted={summary.get('deleted', 0)} · "
        f"errors={summary.get('errors', 0)}"
    )


def _section_detail(report: dict) -> str:
    """Renders the per-section lists as compact text."""
    lines: list[str] = []
    sections = report.get("sections") or {}
    for section, detail in sections.items():
        parts: list[str] = []
        for field in ("created", "updated", "skipped", "deleted", "errors"):
            items = detail.get(field) or []
            if items:
                label = field[0].upper()
                parts.append(f"{label}:{', '.join(map(str, items))}")
        if parts:
            lines.append(f"- **{section}**: {' | '.join(parts)}")
    return "\n".join(lines) if lines else t("config.no_changes")


@ui.page("/config", response_timeout=15)
async def config_page() -> None:
    await build_shell(active="/config")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("config.title")).classes("text-h4")
        ui.label(t("config.subtitle")).classes("text-subtitle1")

        # --- Export -------------------------------------------------------
        with ui.card().classes("w-full mt-4"):
            ui.label(t("config.export_section")).classes("text-subtitle1")
            ui.label(t("config.export_hint")).classes("text-caption text-gray-500")
            checks: dict[str, ui.element] = {}
            with ui.row().classes("flex-wrap gap-4 mt-2"):
                for section in SECTIONS:
                    checks[section] = ui.checkbox(section, value=True).props("dense")
            include_system = ui.switch(t("config.include_system"))

            async def on_export() -> None:
                selected = [name for name, box in checks.items() if box.value]
                try:
                    doc = await client.export_config(
                        token,
                        sections=selected or None,
                        include_system=bool(include_system.value),
                    )
                except CoreError as exc:
                    ui.notify(t("config.export_failed").format(err=exc), type="negative")
                    return
                except Exception as exc:  # noqa: BLE001 - degrade to notify
                    ui.notify(t("config.export_failed").format(err=exc), type="negative")
                    return
                filename = "meadow-config.json"
                # NiceGUI treats a `str` `src` as a URL and a `bytes` `src` as a
                # Blob that is revoked immediately (both unreliable). Writing to
                # a temp file and downloading the path serves it over HTTP, which
                # the browser saves reliably.
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False, encoding="utf-8"
                ) as f:
                    json.dump(doc, f, indent=2)
                    tmp_path = f.name
                ui.download(tmp_path, filename, "application/json")
                # Remove the temp file after the browser has fetched it.
                asyncio.get_running_loop().call_later(60, os.remove, tmp_path)
                ui.notify(t("config.export_ok"), type="positive")

            with ui.row().classes("mt-3"):
                ui.button(t("config.export"), on_click=on_export).props("unelevated")

        # --- Import -------------------------------------------------------
        with ui.card().classes("w-full mt-4"):
            ui.label(t("config.import_section")).classes("text-subtitle1")
            ui.label(t("config.import_hint")).classes("text-caption text-gray-500")

            # The JSON body lives in the textarea; the upload fills it.
            body_box = (
                ui.textarea(t("config.body")).props("outlined monospace").classes("w-full h-40")
            )

            async def on_upload(event) -> None:
                body_box.value = await event.file.text("utf-8")
                body_box.update()

            ui.upload(on_upload=on_upload, auto_upload=True).props(
                "multiple=false accept=.json,application/json"
            ).classes("mt-2")

            mode_select = (
                ui.select({m: m for m in MODES}, value="merge")
                .props("filled dense")
                .classes("mt-2 w-48")
            )

            def _parse_body() -> dict:
                raw = (body_box.value or "").strip()
                if not raw:
                    raise ValueError(t("config.empty_body"))
                doc = json.loads(raw)
                if not isinstance(doc, dict):
                    raise ValueError(t("config.invalid_document"))
                return doc

            async def _run_import(mode: str) -> None:
                try:
                    document = _parse_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    ui.notify(str(exc), type="negative")
                    return
                try:
                    report = await client.import_config(token, document, mode=mode)
                except CoreError as exc:
                    ui.notify(t("config.import_failed").format(err=exc), type="negative")
                    return
                except Exception as exc:  # noqa: BLE001 - degrade to notify
                    ui.notify(t("config.import_failed").format(err=exc), type="negative")
                    return

                with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
                    ui.label(t("config.report")).classes("text-subtitle1")
                    ui.label(_summary_text(report)).classes("text-subtitle1 font-medium")
                    ui.separator()
                    ui.label(t("config.report_detail")).classes("text-subtitle2")
                    ui.markdown(_section_detail(report)).classes("max-h-72 overflow-auto")
                    with ui.row().classes("mt-2 w-full justify-end"):
                        ui.button(t("config.close"), on_click=dialog.close).props("flat")
                dialog.open()

            with ui.row().classes("mt-3 gap-2"):
                ui.button(t("config.preview"), on_click=lambda: _run_import("dry_run")).props(
                    "outline"
                )
                ui.button(t("config.apply"), on_click=lambda: _run_import(mode_select.value)).props(
                    "unelevated"
                )


__all__ = ["config_page"]

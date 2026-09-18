"""The export dialog of the product grid (CSV/XLSX/JSON of a selection or the filter).

The export runs as a background job: the dialog queues it, polls the run and downloads the
result file. `export_source` is the pure part — it decides what the core gets as the source
of the export, and the two scopes differ only in the ids they carry.

The bytes are served through a temp file, because NiceGUI's `str`/`bytes` download sources
proved unreliable for larger files; the file is removed a minute after the download.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.i18n import t

#: Formats the dialog offers (`CSV` is the cheap default).
EXPORT_FORMATS = {"csv": "CSV", "xlsx": "XLSX", "json": "JSON"}

#: Mime type per export format, used for the browser download.
EXPORT_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}

#: How often and how long the dialog waits for the export run.
POLL_INTERVAL_SECONDS = 1
POLL_ATTEMPTS = 120


def export_source(ids: Sequence[str]) -> dict[str, Any]:
    """The core's source description of an export: an explicit object list.

    Both scopes (a selection, the current filter) resolve to ids on the page, so the core
    never has to re-evaluate a filter — what the user saw is what is exported.
    """
    return {"kind": "selection", "object_ids": [str(object_id) for object_id in ids]}


async def wait_for_export(client: CoreClient, token: str, run_id: str) -> dict[str, Any]:
    """Polls an export job run until it reaches a terminal state.

    `include_result=False`: polling only asks for the status, and the `result` of an
    export carries its **whole document** (megabytes) — the core answers with
    `result_summary` instead, so a poll stays a poll (see `recent_exports` in
    `export_page.py` for the size that made a listing reset the DB connection).
    """
    for _ in range(POLL_ATTEMPTS):
        try:
            runs = await client.list_job_runs(token, ids=[run_id], limit=1, include_result=False)
        except CoreError, httpx.HTTPError:  # keep polling on a transient error
            runs = []
        if runs:
            status = str(runs[0].get("status") or "")
            if status in ("success", "failed", "retry"):
                return runs[0]
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return {"status": "failed", "error": "timeout"}


def save_download(data: bytes, filename: str, format_: str) -> None:
    """Serves export bytes via a temp file (NiceGUI str/bytes src is unreliable)."""
    with tempfile.NamedTemporaryFile("wb", suffix="." + format_, delete=False) as handle:
        handle.write(data)
        tmp_path = handle.name
    ui.download(tmp_path, filename, EXPORT_MIME.get(format_, "application/octet-stream"))
    asyncio.get_running_loop().call_later(60, os.remove, tmp_path)


class ExportDialog:
    """The modal that queues an export of the selection or the current filter."""

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        locale: str,
        fields: Callable[[], list[str]],
        filtered_ids: Callable[[], list[str]],
        selected_ids: Callable[[], Awaitable[list[str]]],
        count: Callable[[], int],
        has_selection: bool,
    ) -> None:
        """
        :param fields: the export fields of the currently visible columns.
        :param filtered_ids: the ids of the current filter result.
        :param selected_ids: the ids of the rows the user selected in the grid.
        :param count: how many objects the current filter holds (shown in the scope).
        """
        self.client = client
        self.token = token
        self.locale = locale
        self.fields = fields
        self.filtered_ids = filtered_ids
        self.selected_ids = selected_ids
        self.count = count
        self.has_selection = has_selection
        self.dialog: ui.dialog | None = None
        self.scope_select: ui.select | None = None
        self.format_select: ui.select | None = None

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("grid.export_title")).classes("text-h6")
            ui.label(t("grid.export_hint")).classes("text-caption text-gray-600")
            self.scope_select = (
                ui.select(
                    {
                        "filtered": t("grid.export_scope_filtered").format(count=self.count()),
                        "selected": t("grid.export_scope_selected"),
                    },
                    value="selected" if self.has_selection else "filtered",
                )
                .props("filled dense")
                .classes("w-full")
                .mark("export-scope")
            )
            self.format_select = (
                ui.select(EXPORT_FORMATS, value="csv", label=t("grid.export_format"))
                .props("filled dense")
                .classes("w-full")
                .mark("export-format")
            )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                ui.button(t("grid.export"), icon="download", on_click=self.start).props(
                    "flat"
                ).mark("export-start")
        self.dialog = dialog
        return dialog

    def open(self) -> None:
        """Opens the dialog (the scope keeps the choice of the previous run)."""
        if self.dialog is not None:
            self.dialog.open()

    async def start(self) -> None:
        """Queues the export, waits for the run and hands the file to the browser."""
        scope = (
            str(self.scope_select.value or "filtered")
            if self.scope_select is not None
            else "filtered"
        )
        if scope == "selected":
            ids = await self.selected_ids()
        else:
            ids = self.filtered_ids()
        if not ids:
            ui.notify(t("grid.export_no_objects"), color="warning")
            return
        fmt = str(self.format_select.value or "csv") if self.format_select is not None else "csv"
        try:
            queued = await self.client.create_export(
                self.token,
                source=export_source(ids),
                fields=self.fields(),
                format=fmt,
                locales=[self.locale],
            )
            run_id = str(queued.get("job_run_id") or "")
            if not run_id:
                raise RuntimeError(t("grid.export_failed").format(err="no job id"))
            run = await wait_for_export(self.client, self.token, run_id)
            if run.get("status") != "success":
                raise RuntimeError(run.get("error") or run.get("status") or "failed")
            data, filename = await self.client.get_export_file(self.token, run_id, format=fmt)
        except (CoreError, httpx.HTTPError) as exc:  # surface a friendly message
            ui.notify(t("grid.export_failed").format(err=exc), color="negative")
            return
        exported = (run.get("result_summary") or {}).get("exported", len(ids))
        if self.dialog is not None:
            self.dialog.close()
        save_download(data, filename, fmt)
        ui.notify(t("grid.export_ok").format(count=exported), color="positive")

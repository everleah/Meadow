"""Find & Replace of the product grid: a literal text in the selected objects.

The operation itself runs as the `replace_text` **bulk operation**, so a large selection
is processed in the worker instead of blocking the page — the dialog only collects the
parameters and hands them to `POST /bulk` (the same progress/report machinery the bulk
wizard uses). `replace_parameters` is the pure contract of that payload: the worker reads
exactly these keys, and `send_for_approval` rides along **only** when the switch is on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.i18n import t


def replace_parameters(
    *,
    search: str,
    replace: str,
    attribute: str,
    case_sensitive: bool,
    approval: bool,
) -> dict[str, Any]:
    """The parameters of the `replace_text` operation.

    An empty `attribute` means "every replaceable attribute" — the worker decides which
    ones those are (string-valued `text` and `url`), the page does not guess.
    """
    parameters: dict[str, Any] = {
        "search": search,
        "replace": replace,
        "attribute": attribute,
        "case_sensitive": case_sensitive,
    }
    if approval:
        parameters["send_for_approval"] = True
    return parameters


class ReplaceDialog:
    """The modal that collects the find & replace parameters and starts the job."""

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        attr_options: dict[str, str],
        scope_ids: Callable[[str], Awaitable[list[str]]],
        count: Callable[[], int],
        initial_scope: str,
        on_started: Callable[[str], Awaitable[None]],
    ) -> None:
        """
        :param attr_options: the attributes a replace may target, first entry = all.
        :param scope_ids: resolves a scope (`filtered`/`page`/`selected`) to object ids.
        :param count: how many objects the current filter holds (shown in the scope).
        :param on_started: called with the run id (the page polls the progress through it).
        """
        self.client = client
        self.token = token
        self.attr_options = attr_options
        self.scope_ids = scope_ids
        self.count = count
        self.initial_scope = initial_scope
        self.on_started = on_started
        self.dialog: ui.dialog | None = None
        self.scope_select: ui.select | None = None
        self.attribute_select: ui.select | None = None
        self.search_input: ui.input | None = None
        self.replace_input: ui.input | None = None
        self.case_switch: ui.switch | None = None
        self.approval_switch: ui.switch | None = None

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
            ui.label(t("grid.replace_title")).classes("text-h6")
            ui.label(t("grid.replace_hint")).classes("text-caption text-gray-600")
            ui.label(t("grid.bulk_selection")).classes("text-caption text-gray-600")
            self.scope_select = (
                ui.select(
                    {
                        "filtered": t("grid.bulk_scope_filtered").format(count=self.count()),
                        "page": t("grid.bulk_scope_page"),
                        "selected": t("grid.bulk_scope_selected"),
                    },
                    value=self.initial_scope,
                )
                .props("filled dense")
                .classes("w-full")
                .mark("replace-scope")
            )
            self.attribute_select = (
                ui.select(self.attr_options, value="", label=t("grid.bulk_attribute"))
                .props("filled dense")
                .classes("w-full")
                .mark("replace-attribute")
            )
            self.search_input = (
                ui.input(label=t("grid.replace_search"))
                .props("filled dense")
                .classes("w-full")
                .mark("replace-search")
            )
            self.replace_input = (
                ui.input(label=t("grid.replace_with"))
                .props("filled dense")
                .classes("w-full")
                .mark("replace-with")
            )
            self.case_switch = ui.switch(t("grid.replace_case_sensitive")).mark("replace-case")
            self.approval_switch = ui.switch(t("grid.bulk_send_for_approval")).tooltip(
                f"{t('grid.approval_status')}: in_review"
            )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                ui.button(t("grid.replace_start"), icon="find_replace", on_click=self.start).props(
                    "flat"
                ).mark("replace-start")
        self.dialog = dialog
        return dialog

    def open(self) -> None:
        """Opens the dialog (the scope keeps the choice of the previous run)."""
        if self.dialog is not None:
            self.dialog.open()

    def set_selection(self, has_selection: bool) -> None:
        """Follows the grid selection: the scope switches with it."""
        if self.scope_select is not None:
            self.scope_select.set_value("selected" if has_selection else "filtered")

    async def start(self) -> None:
        """Validates the input and starts the `replace_text` bulk run."""
        search = str(self.search_input.value or "") if self.search_input is not None else ""
        if not search:
            ui.notify(t("grid.replace_search_required"), color="warning")
            return
        scope = (
            str(self.scope_select.value or "filtered")
            if self.scope_select is not None
            else "filtered"
        )
        ids = await self.scope_ids(scope)
        if not ids:
            ui.notify(t("grid.bulk_no_objects"), color="warning")
            return
        parameters = replace_parameters(
            search=search,
            replace=str(self.replace_input.value or "") if self.replace_input is not None else "",
            attribute=str(self.attribute_select.value or "")
            if self.attribute_select is not None
            else "",
            case_sensitive=bool(self.case_switch.value) if self.case_switch is not None else False,
            approval=(
                bool(self.approval_switch.value) if self.approval_switch is not None else False
            ),
        )
        try:
            run = await self.client.post_bulk(
                self.token, op="replace_text", object_ids=ids, parameters=parameters
            )
        except CoreError, httpx.HTTPError:  # show a friendly message
            ui.notify(t("grid.bulk_start_failed"), color="negative")
            return
        run_id = str(run.get("id") or "")
        if self.dialog is not None:
            self.dialog.close()
        ui.notify(t("grid.bulk_started"), color="positive")
        if run_id:
            await self.on_started(run_id)

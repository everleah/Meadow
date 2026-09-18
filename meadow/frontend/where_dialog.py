"""The advanced-search condition dialog of the products page.

The condition form and the query DSL live in `search_form.py`; this module owns the
dialog around it — loading the field catalog and the saved searches, filling the
form from the active filter, validating and applying it, and the saved-search
actions. The page keeps the filter bar, the chip that opens the dialog and the
filter state itself.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from nicegui import ui

from meadow.frontend import search_form
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.i18n import t


def saved_search_name(entry: dict) -> str:
    """Display name of a saved search (grid row or full object)."""
    attributes = entry.get("attributes") or {}
    name = entry.get("name") or attributes.get("name")
    return str(name or entry.get("id") or "")


def has_conditions(query: Mapping[str, Any]) -> bool:
    """Whether a query restricts anything (an empty one is no filter at all)."""
    return bool(
        query.get("where")
        or query.get("references")
        or str((query.get("fulltext") or {}).get("query") or "").strip()
    )


class WhereDialog:
    """The dialog for the advanced search (`?where=<json>`).

    :param client: core client (field catalog, saved searches, saving).
    :param token: bearer token of the session.
    :param get_where: reads the page's active `where` filter (JSON or `None`).
    :param set_where: writes the page's `where` filter.
    :param on_applied: called whenever the filter changed (updates the chip).
    :param reload: reloads the grid after a change.
    """

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        get_where: Callable[[], str | None],
        set_where: Callable[[str | None], None],
        on_applied: Callable[[], None],
        reload: Callable[[], Awaitable[None]],
    ) -> None:
        self.client = client
        self.token = token
        self.get_where = get_where
        self.set_where = set_where
        self.on_applied = on_applied
        self.reload = reload
        self.dialog: ui.dialog | None = None
        self.form_host: ui.column | None = None
        self.saved_select: ui.select | None = None
        self.name_input: ui.input | None = None
        self._form: search_form.SearchForm | None = None
        #: The last condition tree shown, kept while the dialog is reopened.
        self._stored: dict = {}
        #: The field catalog of the core (`list_search_fields`).
        self._catalog: dict = {}
        self._saved: list[dict] = []

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with ui.dialog() as dialog, ui.card().classes("w-[52rem] max-w-[96vw]"):
            ui.label(t("grid.advanced_title")).classes("text-h6")
            ui.label(t("grid.advanced_hint")).classes("text-caption text-gray-500")
            with ui.row().classes("w-full items-center gap-2"):
                self.saved_select = (
                    ui.select({}, label=t("grid.advanced_load"), with_input=False)
                    .props("dense filled clearable")
                    .classes("grow")
                )
                self.saved_select.on_value_change(lambda _: self._load_saved())
            self.form_host = ui.column().classes("w-full gap-1")
            with ui.row().classes("w-full items-center gap-2"):
                self.name_input = (
                    ui.input(label=t("grid.advanced_save")).props("dense filled").classes("grow")
                )
                ui.button(
                    t("grid.advanced_save_button"),
                    icon="bookmark_add",
                    on_click=lambda: self._save_as_search(),
                ).props("flat dense")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("grid.advanced_clear"), on_click=lambda: self.clear()).props("flat")
                ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                ui.button(
                    t("grid.advanced_apply"), icon="check", on_click=lambda: self.apply()
                ).props("flat").mark("advanced-apply")
        self.dialog = dialog
        return dialog

    def summary(self, where: str | None) -> str:
        """Human-readable summary of the condition tree of `where`."""
        query = search_form.parse_json(where) or {}
        return search_form.summary_text(query, self._catalog)

    async def open(self) -> None:
        """Opens the dialog (loads the field catalog and the saved searches)."""
        try:
            self._catalog = await self.client.list_search_fields(self.token)
        except CoreError, httpx.HTTPError:  # without a catalog there is no form
            ui.notify(t("grid.advanced_failed"), color="negative")
            return
        try:
            saved = await self.client.list_saved_searches(self.token, limit=1000)
        except CoreError, httpx.HTTPError:  # the picker is optional
            saved = []
        self._saved = saved
        if self.saved_select is not None:
            self.saved_select.set_options({str(s.get("id")): saved_search_name(s) for s in saved})
        self._fill()
        if self.dialog is not None:
            self.dialog.open()

    async def apply(self) -> None:
        """Applies the condition tree as a server-side filter."""
        query = self._current_query()
        errors = search_form.validate_state(
            search_form.state_from_query(query, self._catalog), self._catalog
        )
        if errors:
            ui.notify("; ".join(errors), color="warning")
            return
        self.set_where(
            search_form.compact_json(search_form.object_filter(query))
            if has_conditions(query)
            else None
        )
        self.on_applied()
        if self.dialog is not None:
            self.dialog.close()
        await self.reload()

    async def clear(self) -> None:
        """Drops the advanced-search filter."""
        self.set_where(None)
        self._stored = {}
        if self._form is not None:
            self._form.set_state(search_form.default_state(self._catalog))
        self.on_applied()
        if self.dialog is not None:
            self.dialog.close()
        await self.reload()

    def _fill(self) -> None:
        """Fills the dialog from the active filter (or the last edit)."""
        stored = search_form.parse_json(self.get_where()) or self._stored
        self._stored = copy.deepcopy(stored)
        form = self._ensure_form()
        if form is not None:
            form.set_state(search_form.state_from_query(stored, self._catalog))

    def _ensure_form(self) -> search_form.SearchForm | None:
        """Renders the condition form once (after the catalog loaded)."""
        if self._form is None and self.form_host is not None:
            self.form_host.clear()
            with self.form_host:
                self._form = search_form.SearchForm(
                    self._catalog,
                    sections=("conditions", "references", "fulltext"),
                    client=self.client,
                    token=self.token,
                )
        return self._form

    def _current_query(self) -> dict:
        """The query the dialog currently represents."""
        form = self._form
        if form is None:
            return dict(self._stored)
        return search_form.query_from_state(form.read_state(), self._catalog, self._stored)

    async def _load_saved(self) -> None:
        """Loads the picked saved search into the dialog."""
        search_id = str(self.saved_select.value or "") if self.saved_select else ""
        entry = next((s for s in self._saved if str(s.get("id")) == search_id), None)
        if entry is None:
            return
        query = dict(entry.get("attributes", {}).get("query") or {})
        self._stored = copy.deepcopy(query)
        if self._form is not None:
            self._form.set_state(search_form.state_from_query(query, self._catalog))

    async def _save_as_search(self) -> None:
        """Creates a saved search from the current condition tree."""
        name = (self.name_input.value or "").strip() if self.name_input else ""
        if not name:
            ui.notify(t("grid.advanced_save_name_required"), color="warning")
            return
        try:
            await self.client.create_saved_search(
                self.token, name=name, query=self._current_query()
            )
        except (CoreError, httpx.HTTPError) as error:  # report the core message
            ui.notify(f"{t('grid.advanced_save_failed')}: {error}", color="negative")
            return
        if self.name_input is not None:
            self.name_input.value = ""
        ui.notify(t("grid.advanced_saved", name=name), color="positive")

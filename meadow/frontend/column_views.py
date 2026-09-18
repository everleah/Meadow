"""The user's named column sets of the products grid ("views").

A view is a name plus the visible grid fields; it is stored per user in the NiceGUI
session storage. The dialog to save, load and delete views lives here — the visible
field logic itself (picker, persistence of the *current* set) stays in `grid.py`,
which passes in how to read and apply the fields.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import app, ui

from meadow.frontend.i18n import t

#: Session key of the per-user views (`{name: [field, …]}`).
VIEWS_STORAGE_KEY = "views"


def read_views(value: object) -> dict[str, list[str]]:
    """The stored views as `{name: [field, …]}`, ignoring malformed entries.

    Storage is user-writable data (it survives upgrades), so a view that does not
    look like one is dropped instead of breaking the grid.
    """
    if not isinstance(value, dict):
        return {}
    views: dict[str, list[str]] = {}
    for name, fields in value.items():
        if isinstance(fields, list):
            views[str(name)] = [str(field) for field in fields]
    return views


class ColumnViews:
    """The dialog for the user's named column sets.

    :param get_fields: the currently visible grid fields (what a save stores).
    :param apply_fields: applies a loaded view (columns + picker of the page).
    """

    def __init__(
        self,
        *,
        get_fields: Callable[[], set[str]],
        apply_fields: Callable[[set[str]], None],
    ) -> None:
        self.get_fields = get_fields
        self.apply_fields = apply_fields
        self.views = read_views(app.storage.user.get(VIEWS_STORAGE_KEY))
        self.dialog: ui.dialog | None = None
        self.host: ui.column | None = None
        self.name_input: ui.input | None = None

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(t("grid.views")).classes("text-h6")
            with ui.row().classes("items-center gap-2 w-full"):
                self.name_input = (
                    ui.input(t("grid.view_name"))
                    .props("outline dense")
                    .classes("grow")
                    .mark("views-name")
                )
                ui.button(t("grid.save_view"), icon="save", on_click=self.save).props(
                    "outline dense"
                ).mark("views-save")
            self.host = ui.column()
            self.refresh()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat dense")
        self.dialog = dialog
        return dialog

    def open(self) -> None:
        """Opens the dialog (the page's bookmarks button calls this)."""
        if self.dialog is not None:
            self.dialog.open()

    def save(self) -> None:
        """Stores the currently visible fields under the name in the input."""
        raw: Any = None if self.name_input is None else self.name_input.value
        name = None if raw is None else str(raw).strip()
        if not name:
            ui.notify(t("grid.view_name_required"), color="warning")
            return
        self.views[name] = sorted(self.get_fields())
        self._store()
        if self.name_input is not None:
            self.name_input.value = ""
        self.refresh()
        ui.notify(t("grid.view_saved").format(name=name))

    def load(self, name: str) -> None:
        """Applies a stored view to the grid."""
        self.apply_fields(set(self.views.get(name, [])))
        ui.notify(t("grid.view_loaded").format(name=name))

    def delete(self, name: str) -> None:
        """Removes a stored view."""
        self.views.pop(name, None)
        self._store()
        self.refresh()

    def refresh(self) -> None:
        """Re-renders the list of stored views."""
        if self.host is None:
            return
        self.host.clear()
        for name in self.views:
            with self.host, ui.row().classes("items-center gap-1 w-full"):
                ui.button(name, on_click=lambda n=name: self.load(n)).props("flat dense")
                ui.button(icon="delete", on_click=lambda n=name: self.delete(n)).props(
                    "flat dense color=negative"
                )

    def _store(self) -> None:
        """Writes the views back to the user's session storage."""
        app.storage.user[VIEWS_STORAGE_KEY] = self.views

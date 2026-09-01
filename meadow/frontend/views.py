"""Page views: home/dashboard and placeholder pages for later Phase 7 steps."""

from __future__ import annotations

from nicegui import ui

from meadow.frontend import (
    form,  # noqa: F401  (registers the product form page)
    grid,  # noqa: F401  (registers the /products page)
)
from meadow.frontend.i18n import t
from meadow.frontend.layout import NAV_ITEMS, build_shell

#: Routes that have a real page and must be skipped by the placeholder
#: registration.
_IMPLEMENTED_ROUTES = {"/products"}


@ui.page("/")
def home() -> None:
    """Dashboard overview (expanded in a later Phase 7 step)."""
    build_shell(active="/")
    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.dashboard")).classes("text-h4")
        ui.label(t("dashboard.overview_placeholder")).classes("text-subtitle1")
        with ui.card().classes("w-full"):
            ui.label(t("dashboard.welcome")).classes("text-body1")
            ui.label(t("dashboard.milestone_note")).classes("text-body2")


def register_placeholders() -> None:
    """Registers placeholder pages for routes built in later steps."""
    for key, path, _admin_only in NAV_ITEMS:
        if path == "/" or path in _IMPLEMENTED_ROUTES:
            continue

        @ui.page(path)
        def page(path: str = path, key: str = key) -> None:
            build_shell(active=path)
            with ui.column().classes("w-full p-6"):
                ui.label(t(key)).classes("text-h4")
                ui.label(t("placeholder.under_construction")).classes("text-subtitle1")


def register_pages() -> None:
    """Registers all frontend views."""
    register_placeholders()

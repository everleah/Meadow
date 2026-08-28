"""Page views: home/dashboard and placeholder pages for later Phase 7 steps."""

from __future__ import annotations

from nicegui import ui

from meadow.frontend.layout import NAV_ITEMS, build_shell


@ui.page("/")
def home() -> None:
    """Dashboard overview (expanded in a later Phase 7 step)."""
    build_shell(active="/")
    with ui.column().classes("w-full p-6"):
        ui.label("Dashboard").classes("text-h4")
        ui.label("Work overview will follow in a later Phase 7 step.").classes("text-subtitle1")
        with ui.card().classes("w-full"):
            ui.label("Welcome to Meadow PIM.").classes("text-body1")
            ui.label("This first milestone provides the app shell, login and branding.").classes(
                "text-body2"
            )


def register_placeholders() -> None:
    """Registers placeholder pages for routes built in later steps."""
    for label, path, _admin_only in NAV_ITEMS:
        if path == "/":
            continue

        @ui.page(path)
        def page(path: str = path, label: str = label) -> None:
            build_shell(active=path)
            with ui.column().classes("w-full p-6"):
                ui.label(label).classes("text-h4")
                ui.label("This page will be implemented in a later Phase 7 step.").classes(
                    "text-subtitle1"
                )


def register_pages() -> None:
    """Registers all frontend views."""
    register_placeholders()

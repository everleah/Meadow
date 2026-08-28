"""Shared app layout: header, drawer navigation and role-based menu."""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend.auth import is_admin, logout

#: Navigation items: (label, path, admin_only).
NAV_ITEMS: list[tuple[str, str, bool]] = [
    ("Dashboard", "/", False),
    ("Products", "/products", False),
    ("Attribute Definitions", "/attribute-definitions", True),
    ("Jobs", "/jobs", True),
    ("Users", "/users", True),
    ("Groups", "/groups", True),
    ("Connectors", "/connectors", True),
    ("System Settings", "/system-settings", True),
    ("System Logs", "/system-logs", True),
]


def build_shell(*, active: str) -> None:
    """Builds the page shell (header + drawer) for an authenticated page."""
    with ui.header(elevated=True).classes("items-center justify-between bg-primary"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round color=white")
            ui.label("Meadow PIM").classes("text-lg font-bold text-white")
        with ui.row().classes("items-center gap-3"):
            ui.label(app.storage.user.get("username", "")).classes("text-white")
            ui.button(icon="logout", on_click=logout).props("flat round color=white")

    with ui.left_drawer(value=True, top_corner=True, bottom_corner=True).props(
        "bordered"
    ) as drawer:
        ui.label("Navigation").classes("text-subtitle1 px-4 pt-4")
        for label, path, admin_only in NAV_ITEMS:
            if admin_only and not is_admin():
                continue
            classes = "items-center w-full px-4 py-2 cursor-pointer"
            if path == active:
                classes += " bg-primary text-white"
            with ui.row().classes(classes).on("click", lambda p=path: ui.navigate.to(p)):
                ui.label(label)

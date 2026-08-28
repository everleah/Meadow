"""Shared app layout: header, drawer navigation and role-based menu."""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend.auth import is_admin, logout
from meadow.frontend.i18n import current_locale, set_locale, supported_locales, t

#: Navigation items: (i18n key, path, admin_only).
NAV_ITEMS: list[tuple[str, str, bool]] = [
    ("nav.dashboard", "/", False),
    ("nav.products", "/products", False),
    ("nav.attribute_definitions", "/attribute-definitions", True),
    ("nav.jobs", "/jobs", True),
    ("nav.users", "/users", True),
    ("nav.groups", "/groups", True),
    ("nav.connectors", "/connectors", True),
    ("nav.system_settings", "/system-settings", True),
    ("nav.system_logs", "/system-logs", True),
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
        ui.label(t("nav.title")).classes("text-subtitle1 px-4 pt-4")
        for key, path, admin_only in NAV_ITEMS:
            if admin_only and not is_admin():
                continue
            classes = "items-center w-full px-4 py-2 cursor-pointer"
            if path == active:
                classes += " bg-primary text-white"
            with ui.row().classes(classes).on("click", lambda p=path: ui.navigate.to(p)):
                ui.label(t(key))
        ui.separator()
        with ui.row().classes("items-center gap-2 px-4 py-2"):
            ui.label(t("language")).classes("text-caption")
            ui.select(
                {code: code.upper() for code in supported_locales()},
                value=current_locale(),
                on_change=lambda event: set_locale(event.value),
            ).classes("w-28")

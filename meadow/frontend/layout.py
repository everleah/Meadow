"""Shared app layout: header, drawer navigation and role-based menu."""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend.auth import is_admin, logout
from meadow.frontend.i18n import current_locale, set_locale, supported_locales, t
from meadow.frontend.theme import MEADOW_LOGO, NAV_GRADIENT_BOTTOM, NAV_GRADIENT_TOP

#: Keep the drawer language selector text readable on the dark gradient.
ui.add_css(
    ".q-drawer .q-field__native, "
    ".q-drawer .q-field__append, "
    ".q-drawer .q-field__control { color: white; }",
    shared=True,
)

#: Navigation items: (i18n key, path, admin_only).
NAV_ITEMS: list[tuple[str, str, bool]] = [
    ("nav.dashboard", "/", False),
    ("nav.products", "/products", False),
    ("nav.attribute_definitions", "/attribute-definitions", True),
    ("nav.categories", "/categories", True),
    ("nav.jobs", "/jobs", True),
    ("nav.users", "/users", True),
    ("nav.groups", "/groups", True),
    ("nav.connectors", "/connectors", True),
    ("nav.system_settings", "/system-settings", True),
    ("nav.system_logs", "/system-logs", True),
]


def build_shell(*, active: str) -> None:
    """Builds the page shell (header + drawer) for an authenticated page."""
    impersonator = app.storage.user.get("impersonator")
    if impersonator:
        with ui.row().classes("w-full items-center gap-3 px-4 py-2 bg-warning text-white"):
            ui.icon("swap_horiz").classes("shrink-0")
            ui.label(
                t("users.banner").format(
                    username=app.storage.user.get("username", ""),
                    actor=impersonator.get("username", ""),
                )
            ).classes("grow text-subtitle2")
            ui.button(t("users.back_to_self"), on_click=logout).props("flat color=white size=sm")
    with ui.header(elevated=True).classes("items-center justify-between bg-primary"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round color=white")
            ui.image(MEADOW_LOGO).classes("w-8 h-8 object-contain")
            ui.label("Meadow PIM").classes("text-lg font-bold text-white")
        with ui.row().classes("items-center gap-3"):
            ui.label(app.storage.user.get("username", "")).classes("text-white")
            ui.button(icon="logout", on_click=logout).props("flat round color=white")

    with (
        ui.left_drawer(value=True, top_corner=True, bottom_corner=True)
        .props("bordered")
        .style(
            "background: linear-gradient(180deg, "
            f"{NAV_GRADIENT_TOP} 0%, {NAV_GRADIENT_BOTTOM} 100%);"
        ) as drawer
    ):
        ui.label(t("nav.title")).classes("text-subtitle1 px-4 pt-4 text-white")
        for key, path, admin_only in NAV_ITEMS:
            if admin_only and not is_admin():
                continue
            classes = "items-center w-full px-4 py-2 cursor-pointer text-white"
            if path == active:
                classes += " bg-primary"
            with ui.row().classes(classes).on("click", lambda p=path: ui.navigate.to(p)):  # type: ignore
                ui.label(t(key))
        ui.separator().props("color=white")
        with ui.row().classes("items-center gap-2 px-4 py-2"):
            ui.label(t("language")).classes("text-caption text-white")
            ui.select(
                {code: code.upper() for code in supported_locales()},
                value=current_locale(),
                on_change=lambda event: set_locale(event.value),
            ).classes("w-28 text-white")

"""Shared app layout: header, drawer navigation and role-based menu."""

from __future__ import annotations

import os

import httpx
from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.auth import is_admin, logout
from meadow.frontend.i18n import current_locale, set_locale, supported_locales, t
from meadow.frontend.password import open_change_password_dialog, refresh_banner
from meadow.frontend.theme import MEADOW_LOGO, NAV_GRADIENT_BOTTOM, NAV_GRADIENT_TOP

#: Seconds a page build may take before NiceGUI answers with a **500**.
#:
#: **Hard rule: a slow core must never turn into a 500.** The data reads are tuned
#: to stay in the low milliseconds, but a cold cache or a core that is still
#: starting must not kill the page — hence this generous ceiling instead of the
#: former per-page 15 s. Every page sets it; `PIM_PAGE_TIMEOUT` overrides it.
PAGE_TIMEOUT = float(os.environ.get("PIM_PAGE_TIMEOUT", "60"))

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
    ("nav.export", "/export", False),
    ("nav.import", "/import", False),
    ("nav.attribute_definitions", "/attribute-definitions", True),
    ("nav.categories", "/categories", True),
    ("nav.languages", "/languages", True),
    ("nav.jobs", "/jobs", True),
    ("nav.users_groups", "/users", True),
    ("nav.rules", "/rules", True),
    ("nav.connectors", "/connectors", True),
    ("nav.units", "/units", True),
    ("nav.object_types", "/object-types", True),
    ("nav.relation_definitions", "/relation-definitions", True),
    ("nav.enum_sets", "/enum-sets", True),
    ("nav.channels", "/channels", True),
    ("nav.saved_searches", "/saved-searches", True),
    ("nav.export_profiles", "/export-profiles", True),
    ("nav.config", "/config", True),
    ("nav.system_settings", "/system-settings", True),
    ("nav.system_logs", "/system-logs", True),
    ("nav.help", "/help", False),
]


async def _active_ui_languages() -> list[str]:
    """Returns the `active` + `is_ui` language codes the frontend can translate.

    Falls back to the fixed `supported_locales()` set when the core is
    unreachable or yields no usable UI languages (only codes with a
    translation file are offered, so the UI never shows an untranslated key).
    """
    token = app.storage.user.get("token", "")
    try:
        langs = await CoreClient(config.CORE_URL).list_languages(token)
    except CoreError, httpx.HTTPError:  # fall back to the fixed set
        return list(supported_locales())
    active_ui = [
        str(lang.get("code")) for lang in langs if lang.get("active", True) and lang.get("is_ui")
    ]
    known = set(supported_locales())
    result = [c for c in active_ui if c in known]
    return result or list(supported_locales())


async def build_shell(*, active: str) -> None:
    """Builds the page shell (header + drawer) for an authenticated page."""
    # The drawer is created before the header on purpose: the header's menu button
    # toggles it, so it has to exist (and be typed) by the time the header is built.
    drawer = (
        ui.left_drawer(value=True, top_corner=True, bottom_corner=True)
        .props("bordered")
        .style(
            "background: linear-gradient(180deg, "
            f"{NAV_GRADIENT_TOP} 0%, {NAV_GRADIENT_BOTTOM} 100%);"
        )
    )
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
    refresh_banner()
    with ui.header(elevated=True).classes("items-center justify-between bg-primary"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round color=white")
            ui.image(MEADOW_LOGO).classes("w-8 h-8 object-contain")
            ui.label("Meadow PIM").classes("text-lg font-bold text-white")
        with ui.row().classes("items-center gap-3"):
            ui.label(app.storage.user.get("username", "")).classes("text-white")
            ui.button(icon="key", on_click=open_change_password_dialog).props(
                "flat round color=white"
            ).tooltip(t("password.change"))
            ui.button(icon="logout", on_click=logout).props("flat round color=white")

    with drawer:
        ui.label(t("nav.title")).classes("text-subtitle1 px-4 pt-4 text-white")
        for key, path, admin_only in NAV_ITEMS:
            if admin_only and not is_admin():
                continue
            classes = "px-4 py-2 text-white"
            if path == active:
                classes += " bg-primary"
            # A real anchor, not a click handler. `ui.navigate.to` travels through the
            # websocket (the browser opens the URL only after the server told it to),
            # so a click in a tab whose socket is gone (suspended/stale tab, restarted
            # server) silently does nothing — and a click handler looks broken while a
            # plain link keeps working. Native link = native navigation, no socket.
            with (
                ui.element("a")
                .props(f'href="{path}"')
                .classes(classes)
                .style("display: block; width: 100%; text-decoration: none;")
                .mark("nav-link")
            ):
                ui.label(t(key))
        ui.separator().props("color=white")
        ui_langs = await _active_ui_languages()
        current = current_locale()
        if current not in ui_langs:
            current = ui_langs[0] if ui_langs else "en"
        with ui.row().classes("items-center gap-2 px-4 py-2"):
            ui.label(t("language")).classes("text-caption text-white")
            ui.select(
                {code: code.upper() for code in ui_langs},
                value=current,
                on_change=lambda event: set_locale(event.value),
            ).classes("w-28 text-white")

"""Redirect for the former `/groups` page.

Groups are now managed on the combined `/users` admin page (mirroring the
attribute-definitions page, which hosts attribute definitions + attribute
groups). This route is kept so old bookmarks still land on the right screen.
"""

from __future__ import annotations

from nicegui import ui

from meadow.frontend.layout import PAGE_TIMEOUT


@ui.page("/groups", response_timeout=PAGE_TIMEOUT)
async def groups_page() -> None:
    ui.navigate.to("/users")

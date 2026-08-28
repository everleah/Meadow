"""Authentication: JWT login against the core and role-based access control."""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware

from meadow.frontend import config
from meadow.frontend.api import CoreClient

#: Static routes that must be reachable without authentication.
UNRESTRICTED_ROUTES = {"/login", "/favicon.ico"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirects unauthenticated users to the login page."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            app.storage.user.get("authenticated")
            or path in UNRESTRICTED_ROUTES
            or path.startswith("/_nicegui")
        ):
            return await call_next(request)
        return RedirectResponse(f"/login?redirect_to={path}")


def is_authenticated() -> bool:
    """Whether the current session is authenticated."""
    return bool(app.storage.user.get("authenticated"))


def roles() -> list[str]:
    """The current user's roles."""
    return list(app.storage.user.get("roles", []))


def is_admin() -> bool:
    """Whether the current user has admin or superadmin privileges."""
    current_roles = roles()
    return "admin" in current_roles or "superadmin" in current_roles


@ui.page("/login")
def login(redirect_to: str = "/") -> RedirectResponse | None:
    """Login page: exchanges credentials with the core and stores the JWT."""

    async def try_login() -> None:
        client = CoreClient(config.CORE_URL)
        try:
            login_response = await client.login(username.value, password.value)
            me = await client.me(login_response.token)
        except httpx.HTTPStatusError:
            ui.notify("Invalid username or password", color="negative")
            return
        except httpx.HTTPError:
            ui.notify("Core is unreachable", color="negative")
            return
        app.storage.user.update(
            token=login_response.token,
            sub=me.sub,
            username=me.username,
            roles=me.roles,
            authenticated=True,
        )
        ui.navigate.to(redirect_to)

    with ui.card().classes("absolute-center items-stretch w-96"):
        ui.label("Meadow PIM").classes("text-h5 font-bold text-primary")
        ui.label("Sign in").classes("text-subtitle1")
        username = (
            ui.input("Username")
            .props("autofocus")
            .on("keydown.enter", lambda: password.run_method("focus"))
        )
        password = ui.input("Password", password=True, password_toggle_button=True).on(
            "keydown.enter", try_login
        )
        ui.button("Log in", on_click=try_login).classes("bg-primary text-white")

    return None


def logout() -> None:
    """Clears the session and returns to the login page."""
    app.storage.user.clear()
    ui.navigate.to("/login")


def register() -> None:
    """Registers the authentication middleware."""
    app.add_middleware(AuthMiddleware)

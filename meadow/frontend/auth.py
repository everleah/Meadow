"""Authentication: JWT login against the core and role-based access control."""

from __future__ import annotations

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware

from meadow.frontend import config
from meadow.frontend.api import CoreClient
from meadow.frontend.i18n import best_locale, t
from meadow.frontend.theme import LOGIN_BG_PNG, LOGIN_BG_WEBP, MEADOW_LOGO

#: Static routes that must be reachable without authentication.
UNRESTRICTED_ROUTES = {"/login", "/favicon.ico"}
#: User-storage keys that belong to the **authentication session** only.
#: `clear_session` removes exactly these, so user preferences (saved grid views,
#: the visible column set, pinned attributes, locale) survive a logout, an
#: expired token or a re-login.
SESSION_KEYS = (
    "authenticated",
    "token",
    "sub",
    "username",
    "roles",
    "must_change_password",
    "password_changed_at",
    "impersonator",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirects unauthenticated users to the login page."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            app.storage.user.get("authenticated")
            or path in UNRESTRICTED_ROUTES
            or path.startswith("/_nicegui")
            or path.startswith("/static")
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


def is_editor() -> bool:
    """Whether the current user may edit data (editor, admin or superadmin)."""
    current_roles = roles()
    return any(role in current_roles for role in ("editor", "admin", "superadmin"))


def is_superadmin() -> bool:
    """Whether the current user has superadmin privileges (impersonation)."""
    return "superadmin" in roles()


@ui.page("/login")
def login(request: Request, redirect_to: str = "/") -> RedirectResponse | None:
    """Login page: exchanges credentials with the core and stores the JWT."""
    if is_authenticated():
        return RedirectResponse("/")
    if not app.storage.user.get("locale"):
        app.storage.user["locale"] = best_locale(request.headers.get("accept-language"))

    # Full-viewport meadow background: WebP with a PNG fallback and a dark base.
    ui.query("body").style(
        f"background-image:url('{LOGIN_BG_PNG}');"
        f"background-image:url('{LOGIN_BG_WEBP}');"
        "background-size:cover;background-position:center;background-repeat:no-repeat;"
        "background-color:#1e293b;"
    )

    async def try_login() -> None:
        if not username.value or not password.value:
            ui.notify(t("login.missing_credentials"), color="negative")
            return
        client = CoreClient(config.CORE_URL)
        try:
            login_response = await client.login(username.value, password.value)
            me = await client.me(login_response.token)
        except httpx.HTTPStatusError:
            ui.notify(t("login.invalid_credentials"), color="negative")
            return
        except httpx.HTTPError:
            ui.notify(t("login.core_unreachable"), color="negative")
            return
        app.storage.user.update(
            token=login_response.token,
            sub=me.sub,
            username=me.username,
            roles=me.roles,
            must_change_password=me.must_change_password,
            password_changed_at=me.password_changed_at,
            impersonator=None,
            authenticated=True,
        )
        ui.navigate.to(redirect_to)

    with ui.card().classes("absolute-center items-stretch w-96"):
        ui.image(MEADOW_LOGO).classes("w-32 self-center mb-2")
        ui.label(t("login.sign_in")).classes("text-subtitle1 self-center")
        username = (
            ui.input(t("login.username"))
            .props("autofocus")
            .on("keydown.enter", lambda: password.run_method("focus"))
        )
        password = ui.input(t("login.password"), password=True, password_toggle_button=True).on(
            "keydown.enter", try_login
        )
        ui.button(t("login.log_in"), on_click=try_login).classes("bg-primary text-white")

    return None


def clear_session() -> None:
    """Drops the authentication (JWT, roles, impersonation).

    User preferences live in the same user storage but belong to the person
    rather than to the login, so they are deliberately kept.
    """
    for key in SESSION_KEYS:
        app.storage.user.pop(key, None)


def logout() -> None:
    """Clears the authentication and returns to the login page."""
    clear_session()
    ui.navigate.to("/login")


def register() -> None:
    """Registers the authentication middleware."""
    app.add_middleware(AuthMiddleware)

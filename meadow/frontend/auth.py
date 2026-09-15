"""Authentication: JWT login against the core and role-based access control."""

from __future__ import annotations

import contextlib
from urllib.parse import quote

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware

from meadow.frontend import api, config
from meadow.frontend.api import CoreClient
from meadow.frontend.i18n import best_locale, t
from meadow.frontend.theme import LOGIN_BG_PNG, LOGIN_BG_WEBP, MEADOW_LOGO

#: Static routes that must be reachable without authentication.
UNRESTRICTED_ROUTES = {"/login", "/favicon.ico"}
#: Path prefixes served without authentication (brand assets, handbook images):
#: an image request must not be redirected to the login page while a session
#: expires, or the handbook shows broken screenshots.
UNRESTRICTED_PREFIXES = ("/static", "/help-assets")
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
            or path.startswith(UNRESTRICTED_PREFIXES)
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
def login(
    request: Request, redirect_to: str = "/", expired: bool = False
) -> RedirectResponse | None:
    """Login page: exchanges credentials with the core and stores the JWT.

    `expired` is set by `handle_session_expired`, so a session that was lost
    explains itself instead of just showing the form again.
    """
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
        if expired:
            ui.label(t("login.session_expired")).classes("text-caption text-warning self-center")
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


def current_page_path() -> str:
    """The route of the page the user is currently on (``/`` as fallback).

    Used to send the user back to where they were after a re-login. Without a
    client/slot context (e.g. a background task) there is no page route.
    """
    try:
        path = ui.context.client.page.path
    except Exception:  # noqa: BLE001 - no page context available
        return "/"
    return path if path and path != "/login" else "/"


def handle_session_expired() -> None:
    """Clears a lost session and returns the user to the login page.

    Registered as the core client's session-expired callback, so **any** call
    rejected with an invalid/expired token leads back to the login page instead
    of leaving the page half-loaded (the login page remembers where the user
    came from via `?redirect_to=`). On the login page itself (and for an
    already cleaned session) nothing happens, so a failed login keeps showing
    its error message. Without a client context (background task) there is
    nothing to redirect.
    """
    with contextlib.suppress(RuntimeError):
        if not is_authenticated() or current_page_path() == "/login":
            return
        redirect_to = current_page_path()
        clear_session()
        ui.navigate.to(f"/login?redirect_to={quote(redirect_to)}&expired=1")


def register() -> None:
    """Registers the authentication middleware and the expiry callback."""
    app.add_middleware(AuthMiddleware)
    api.set_session_expired_handler(handle_session_expired)

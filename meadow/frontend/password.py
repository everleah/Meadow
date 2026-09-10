"""Self-service password change dialog and the "set your own password" banner.

The dialog is rendered from the shared layout header, so it is reachable from
every authenticated page. The banner is driven by `must_change_password`
(`GET /auth/me`): it is on for the seeded `admin`/`admin` and for users an admin
created or reset with "require a new password" checked — and off when the admin
vouches for the password (e.g. integration accounts) or the user changed it.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t

#: Minimum length of a new password (mirrors the core's `MIN_PASSWORD_LENGTH`).
MIN_PASSWORD_LENGTH = 8


def needs_password_change() -> bool:
    """Whether the session still has to set its own password."""
    if app.storage.user.get("impersonator"):
        return False
    return bool(app.storage.user.get("must_change_password", True))


def open_change_password_dialog() -> None:
    """Renders and opens the change-password dialog for the current session."""
    if app.storage.user.get("impersonator"):
        ui.notify(t("password.impersonated"), color="warning")
        return
    token = str(app.storage.user.get("token") or "")

    async def _submit() -> None:
        current = str(current_input.value or "")
        new = str(new_input.value or "")
        confirm = str(confirm_input.value or "")
        if not current or not new:
            ui.notify(t("password.missing"), color="warning")
            return
        if len(new) < MIN_PASSWORD_LENGTH:
            ui.notify(t("password.too_short").format(length=MIN_PASSWORD_LENGTH), color="warning")
            return
        if new != confirm:
            ui.notify(t("password.mismatch"), color="warning")
            return
        submit_button.props("loading")
        try:
            response = await CoreClient(CORE_URL).change_password(
                token, current_password=current, new_password=new
            )
        except CoreError as error:
            ui.notify(t("password.failed").format(err=error), color="negative")
            return
        except Exception as error:  # noqa: BLE001 - surface transport problems too
            ui.notify(t("password.failed").format(err=error), color="negative")
            return
        finally:
            submit_button.props(remove="loading")
        # The core bumps the token version: keep the session alive with the
        # freshly issued token and clear the "set your own password" hint.
        app.storage.user.update(
            token=response.token,
            must_change_password=False,
            password_changed_at="changed",
        )
        ui.notify(t("password.changed"), color="positive")
        dialog.close()
        refresh_banner.refresh()

    with ui.dialog() as dialog, ui.card().classes("w-[26rem] max-w-[92vw]"):
        ui.label(t("password.title")).classes("text-h6")
        ui.label(t("password.hint").format(length=MIN_PASSWORD_LENGTH)).classes(
            "text-caption text-gray-600"
        )
        current_input = (
            ui.input(t("password.current"), password=True, password_toggle_button=True)
            .props("filled dense autofocus")
            .classes("w-full")
        )
        new_input = (
            ui.input(t("password.new"), password=True, password_toggle_button=True)
            .props("filled dense")
            .classes("w-full")
        )
        confirm_input = (
            ui.input(t("password.confirm"), password=True, password_toggle_button=True)
            .props("filled dense")
            .classes("w-full")
        )
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(t("password.cancel"), on_click=dialog.close).props("flat")
            submit_button = ui.button(t("password.save"), icon="key", on_click=_submit).props(
                "unelevated"
            )
    dialog.open()


@ui.refreshable
def refresh_banner() -> None:
    """Asks the user to set their own password while `must_change_password` is on."""
    if not needs_password_change():
        return
    with ui.row().classes("w-full items-center gap-3 px-4 py-2 bg-info text-white"):
        ui.icon("key").classes("shrink-0")
        ui.label(t("password.banner")).classes("grow text-subtitle2")
        ui.button(t("password.change"), on_click=open_change_password_dialog).props(
            "flat color=white size=sm"
        )


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "needs_password_change",
    "open_change_password_dialog",
    "refresh_banner",
]

"""Admin page for system settings (admin/superadmin only).

Shows the runtime settings (``GET /system/settings?include_source=true``) with
their resolved value and source (`default` | `env` | `db`), allows editing each
(``PUT /system/settings/{key}``) and resetting to the default
(``DELETE /system/settings/{key}``). Changes are audited by the core.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import build_shell

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")
CATEGORIES = ("app", "job", "rule", "auth", "integration")


def _source_badge(source: str) -> str:
    """A coloured badge for a setting's source."""
    classes = {
        "default": "bg-neutral text-gray-600",
        "env": "bg-info text-white",
        "db": "bg-warning text-white",
    }.get(source, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{source}</span>'


@ui.page("/system-settings", response_timeout=15)
async def settings_page() -> None:
    await build_shell(active="/system-settings")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        settings = await client.list_system_settings(token, include_source=True)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("settings.load_failed")).classes("text-subtitle1 text-negative")
        return

    with ui.column().classes("w-full p-6"):
        ui.label(t("settings.title")).classes("text-h4")
        ui.label(t("settings.subtitle")).classes("text-subtitle1")

        #: Each entry is a small card with an editor + Save/Reset.
        editors: dict[str, ui.element] = {}
        #: Settings grouped into coloured expansions (default closed).
        GROUPS = (
            (
                "settings.group_logging",
                "green",
                (
                    "log_persist_level",
                    "log_persist_categories",
                    "log_retention_days",
                    "log_rate_limit",
                ),
            ),
            (
                "settings.group_rules",
                "blue",
                (
                    "rules_degrade",
                    "rules_breaker_threshold",
                    "rules_timeout_ms",
                ),
            ),
        )

        def _make_card(key: str, entry: dict) -> None:
            value = entry.get("value")
            source = entry.get("source") or "default"
            with ui.card().classes("w-full mt-2"):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(t(f"settings.{key}")).classes("text-subtitle1 font-medium")
                    ui.html(_source_badge(str(source))).classes("ml-1")
                with ui.row().classes("w-full items-center gap-2 mt-1"):
                    if key == "log_persist_level":
                        editor = (
                            ui.select(
                                {level: level for level in LEVELS},
                                value=value if isinstance(value, str) else "WARN",
                            )
                            .props("filled dense")
                            .classes("grow max-w-[16rem]")
                        )
                    elif key == "log_persist_categories":
                        editor = (
                            ui.select(
                                {c: c for c in CATEGORIES},
                                value=list(value) if isinstance(value, list) else [],
                                multiple=True,
                            )
                            .props("filled dense")
                            .classes("grow max-w-[24rem]")
                        )
                    elif key == "rules_degrade":
                        editor = ui.switch(
                            t("settings.rules_degrade"),
                            value=bool(value) if isinstance(value, bool) else False,
                        )
                    elif key == "log_retention_days":
                        editor = (
                            ui.number(value=int(value) if isinstance(value, (int, float)) else 30)
                            .props("filled dense")
                            .classes("w-32")
                        )
                    elif key == "rules_breaker_threshold":
                        editor = (
                            ui.number(value=int(value) if isinstance(value, (int, float)) else 3)
                            .props("filled dense")
                            .classes("w-32")
                        )
                    elif key == "rules_timeout_ms":
                        editor = (
                            ui.number(value=int(value) if isinstance(value, (int, float)) else 2000)
                            .props("filled dense")
                            .classes("w-32")
                        )
                    else:
                        editor = (
                            ui.number(value=int(value) if isinstance(value, (int, float)) else 1000)
                            .props("filled dense")
                            .classes("w-32")
                        )
                    editors[key] = editor
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button(
                        t("settings.save"), icon="save", on_click=lambda k=key: save_setting(k)
                    ).props("flat")
                    ui.button(
                        t("settings.reset"),
                        icon="restart_alt",
                        on_click=lambda k=key: reset_setting(k),
                    ).props("flat color=negative")

        for group_title, color, keys in GROUPS:
            with (
                ui.expansion(t(group_title), icon="tune")
                .classes("w-full mt-3")
                .props(f"header-class=bg-{color}-2 text-{color}-9")
            ):
                for key in keys:
                    _make_card(key, settings.get(key, {"value": None, "source": "default"}))

        async def reload() -> None:
            nonlocal settings
            try:
                settings = await client.list_system_settings(token, include_source=True)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            # Update editors to the resolved values.
            for key, editor in editors.items():
                entry = settings.get(key, {})
                value = entry.get("value")
                if key == "log_persist_categories":
                    editor.set_value(list(value) if isinstance(value, list) else [])
                elif key == "rules_degrade":
                    editor.set_value(bool(value) if isinstance(value, bool) else False)
                elif key in (
                    "log_retention_days",
                    "log_rate_limit",
                    "rules_breaker_threshold",
                    "rules_timeout_ms",
                ):
                    editor.set_value(int(value) if isinstance(value, (int, float)) else 0)
                else:
                    editor.set_value(value if isinstance(value, str) else "")
            # Re-render source badges by updating the cards' html is complex;
            # a full page reload gives the freshest source labels.
            ui.navigate.to("/system-settings")

        async def save_setting(key: str) -> None:
            editor = editors.get(key)
            try:
                value: object
                if key == "log_persist_categories":
                    value = list(editor.value or [])
                elif key == "rules_degrade":
                    value = bool(editor.value)
                elif key in (
                    "log_retention_days",
                    "log_rate_limit",
                    "rules_breaker_threshold",
                    "rules_timeout_ms",
                ):
                    value = int(editor.value or 0)
                else:
                    value = editor.value or "WARN"
                await client.put_setting(token, key, value)
            except CoreError as error:
                ui.notify(f"{t('settings.save_failed')}: {error}", color="negative")
                return
            await reload()
            ui.notify(t("settings.saved"), color="positive")

        async def reset_setting(key: str) -> None:
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("settings.reset_confirm").format(key=key)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_setting(token, key)
                        except CoreError as error:
                            ui.notify(f"{t('settings.reset_failed')}: {error}", color="negative")
                            return
                        await reload()
                        ui.notify(t("settings.reset_done"), color="positive")

                    ui.button(t("settings.reset"), on_click=_do).props("color=negative")
            confirm.open()

        ui.button(t("jobs.reload"), icon="refresh", on_click=lambda: reload()).props("flat")

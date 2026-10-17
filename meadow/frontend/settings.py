"""Admin page for system settings (admin/superadmin only).

Shows the runtime settings (``GET /system/settings?include_source=true``) with
their resolved value and source (`default` | `env` | `db`), allows editing each
(``PUT /system/settings/{key}``) and resetting to the default
(``DELETE /system/settings/{key}``). Changes are audited by the core.
"""

from __future__ import annotations

from typing import Any

import httpx
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")
CATEGORIES = ("app", "job", "rule", "auth", "integration")
#: Providers the core accepts for `ai_provider` (see `crates/pim-core/src/api/system.rs`).
PROVIDERS = ("generic", "openai", "azure_openai", "ollama")

#: Example values for a first setup (`settings.ai_preset_apply`).
#:
#: The core resolves an empty endpoint/model to "not configured", so a fresh
#: installation needs three values to get going. The button fills these in **only**
#: where the field is still empty — an administrator's own value is never replaced.
PROVIDER_EXAMPLES: dict[str, dict[str, str]] = {
    "generic": {
        "ai_endpoint": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
    },
    "openai": {
        "ai_endpoint": "https://api.openai.com/v1",
        "ai_model": "gpt-4o-mini",
    },
    "azure_openai": {
        "ai_endpoint": "https://<resource>.openai.azure.com",
        "ai_model": "<deployment name>",
    },
    "ollama": {
        "ai_endpoint": "http://localhost:11434",
        "ai_model": "llama3.1",
    },
}

#: Editor kinds per setting key. One table instead of if-chains in three places
#: (build, reload, save) — a key added here works in all of them.
SWITCH_KEYS = ("rules_degrade", "connector_strict_parameters", "ai_enabled")
INT_KEYS = (
    "log_retention_days",
    "log_rate_limit",
    "rules_breaker_threshold",
    "rules_timeout_ms",
    "ai_max_output_tokens",
    "ai_timeout_ms",
    "ai_max_requests_per_hour",
)
FLOAT_KEYS = ("ai_temperature",)
TEXT_KEYS = ("ai_endpoint", "ai_model", "ai_api_key")
#: Settings the core never sends a value for (write-only, `secret` in the response).
SECRET_KEYS = ("ai_api_key",)


def editor_value(key: str, value: object) -> Any:
    """A setting value in the shape its editor expects.

    The response is JSON, so a number may arrive as `float`, a boolean may be
    missing and a secret is always `null`; each editor gets the type it needs.
    """
    if key in SWITCH_KEYS:
        return bool(value) if isinstance(value, bool) else False
    if key in INT_KEYS:
        return int(value) if isinstance(value, (int, float)) else 0
    if key in FLOAT_KEYS:
        return float(value) if isinstance(value, (int, float)) else 0.0
    if key == "log_persist_categories":
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
    if key in TEXT_KEYS:
        return str(value) if isinstance(value, str) else ""
    return value


def _source_badge(source: str) -> str:
    """A coloured badge for a setting's source."""
    classes = {
        "default": "bg-neutral text-gray-600",
        "env": "bg-info text-white",
        "db": "bg-warning text-white",
    }.get(source, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{source}</span>'


@ui.page("/system-settings", response_timeout=PAGE_TIMEOUT)
async def settings_page() -> None:
    await build_shell(active="/system-settings")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    try:
        settings = await client.list_system_settings(token, include_source=True)
    except CoreError, httpx.HTTPError:  # show a friendly message on failure
        ui.label(t("settings.load_failed")).classes("text-subtitle1 text-negative")
        return

    with ui.column().classes("w-full p-6"):
        ui.label(t("settings.title")).classes("text-h4")
        ui.label(t("settings.subtitle")).classes("text-subtitle1")

        #: Each entry is a small card with an editor + Save/Reset. The editor type
        #: follows the key (select, switch, number, text or password) - see `_make_card`.
        editors: dict[str, ui.select | ui.switch | ui.number | ui.input] = {}
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
            (
                "settings.group_connectors",
                "teal",
                ("connector_strict_parameters",),
            ),
            (
                "settings.group_ai",
                "purple",
                (
                    "ai_enabled",
                    "ai_provider",
                    "ai_endpoint",
                    "ai_model",
                    "ai_api_key",
                    "ai_temperature",
                    "ai_max_output_tokens",
                    "ai_timeout_ms",
                    "ai_max_requests_per_hour",
                ),
            ),
        )
        #: The API key is write-only: the response carries `configured`, never a
        #: value, so the card shows whether something is set instead of a field.
        secrets: dict[str, bool] = {
            key: bool((settings.get(key) or {}).get("configured")) for key in SECRET_KEYS
        }

        def _make_card(key: str, entry: dict) -> None:
            value = entry.get("value")
            source = entry.get("source") or "default"
            secret = key in SECRET_KEYS
            # An environment value wins over the database, so the field would be
            # ineffective - lock it and say why instead of ignoring the input.
            locked = source == "env"
            # Declared before the chain: the editor type depends on the key.
            editor: ui.select | ui.switch | ui.number | ui.input
            with ui.card().classes("w-full mt-2"):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(t(f"settings.{key}")).classes("text-subtitle1 font-medium")
                    ui.html(_source_badge(str(source))).classes("ml-1")
                    if secret:
                        state = (
                            "settings.secret_set"
                            if entry.get("configured")
                            else ("settings.secret_unset")
                        )
                        ui.label(t(state)).classes(
                            "text-caption "
                            + ("text-positive" if entry.get("configured") else "text-gray-500")
                        )
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
                    elif key in SWITCH_KEYS:
                        editor = ui.switch(
                            t(f"settings.{key}"),
                            value=editor_value(key, value),
                        )
                    elif key == "ai_provider":
                        editor = (
                            ui.select(
                                {
                                    provider: t(f"settings.ai_provider_{provider}")
                                    for provider in PROVIDERS
                                },
                                value=value if isinstance(value, str) else "generic",
                            )
                            .props("filled dense")
                            .classes("grow max-w-[16rem]")
                        )
                    elif key == "ai_api_key":
                        # Write-only: an empty field means "leave it as it is".
                        editor = (
                            ui.input(
                                placeholder=t("settings.ai_key_placeholder"),
                                password=True,
                                password_toggle_button=True,
                            )
                            .props("filled dense")
                            .classes("grow max-w-[32rem]")
                        )
                    elif key in TEXT_KEYS:
                        editor = (
                            ui.input(value=str(value) if isinstance(value, str) else "")
                            .props("filled dense")
                            .classes("grow max-w-[32rem]")
                        )
                    elif key in FLOAT_KEYS:
                        editor = (
                            ui.number(
                                value=editor_value(key, value),
                                step=0.1,
                                min=0,
                                max=2,
                            )
                            .props("filled dense")
                            .classes("w-32")
                        )
                    else:
                        editor = (
                            ui.number(value=editor_value(key, value))
                            .props("filled dense")
                            .classes("w-32")
                        )
                    editors[key] = editor
                    editor.mark(f"setting-{key}")
                    if locked:
                        editor.props("disable")
                if locked:
                    ui.label(t("settings.env_locked")).classes("text-caption text-gray-500 mt-1")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button(
                        t("settings.save"), icon="save", on_click=lambda k=key: save_setting(k)
                    ).props("flat")
                    ui.button(
                        t("settings.reset"),
                        icon="restart_alt",
                        on_click=lambda k=key: reset_setting(k),
                    ).props("flat color=negative")

        async def test_ai(result_label: ui.label) -> None:
            """Asks the worker for a minimal provider round trip."""
            result_label.classes(remove="text-positive text-negative")
            result_label.text = t("settings.ai_test_running")
            try:
                reply = await client.ai_test(token)
            except CoreError as error:
                result_label.text = f"{t('settings.ai_test_failed')}: {error}"
                result_label.classes("text-negative")
                return
            if reply.get("ok"):
                result_label.text = t("settings.ai_test_ok").format(
                    model=reply.get("model") or "?",
                    ms=reply.get("latency_ms") or 0,
                )
                result_label.classes("text-positive")
                return
            errors = reply.get("errors") or [t("settings.ai_test_failed")]
            result_label.text = "; ".join(str(error) for error in errors)
            result_label.classes("text-negative")

        def _apply_provider_example() -> None:
            """Fills the chosen provider's example into the **empty** AI fields.

            A commissioning aid: with `generic`/`ollama` the endpoint and the model
            name are the two things an administrator has to know. Whatever is already
            set stays — a wrong example is worse than none.
            """
            provider = str(editors["ai_provider"].value or "generic")
            filled: list[str] = []
            for key, value in PROVIDER_EXAMPLES.get(provider, {}).items():
                field = editors.get(key)
                # Only the text fields carry an example, and only an empty one is
                # filled: `isinstance` is the type check the TypeChecker needs.
                if not isinstance(field, ui.input) or str(field.value or "").strip():
                    continue
                field.set_value(value)
                filled.append(t(f"settings.{key}"))
            ui.notify(
                t("settings.ai_preset_applied").format(fields=", ".join(filled))
                if filled
                else t("settings.ai_preset_kept"),
                color="positive" if filled else "info",
            )

        for group_title, color, keys in GROUPS:
            with (
                ui.expansion(t(group_title), icon="tune")
                .classes("w-full mt-3")
                .props(f"header-class=bg-{color}-2 text-{color}-9")
            ):
                for key in keys:
                    _make_card(key, settings.get(key, {"value": None, "source": "default"}))
                if "ai_enabled" in keys:
                    # The only way to tell a wrong endpoint from a wrong key without
                    # saving a rule first (`plan/detail/ki-assistenz.md` §4.1).
                    ui.label(t("settings.ai_test_hint")).classes("text-caption text-gray-500 mt-3")
                    with ui.row().classes("w-full items-center gap-2"):
                        test_result = ui.label("").classes("text-caption")
                        ui.button(
                            t("settings.ai_test"),
                            icon="network_check",
                            on_click=lambda label=test_result: test_ai(label),
                        ).props("flat")
                    ui.label(t("settings.ai_preset_hint")).classes(
                        "text-caption text-gray-500 mt-3"
                    )
                    ui.button(
                        t("settings.ai_preset_apply"),
                        icon="auto_fix_high",
                        on_click=_apply_provider_example,
                    ).props("flat").mark("ai-preset-apply")

        async def reload() -> None:
            nonlocal settings
            try:
                settings = await client.list_system_settings(token, include_source=True)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                return
            # Update editors to the resolved values. The shape of the value is decided
            # by the key (`editor_value`), the union of editors has no single value
            # type. A secret has no value in the response, so its field stays empty: it
            # is write-only.
            for key, editor in editors.items():
                if key in SECRET_KEYS:
                    continue
                editor.set_value(editor_value(key, settings.get(key, {}).get("value")))
            for key in SECRET_KEYS:
                entry = settings.get(key) or {}
                secrets[key] = bool(entry.get("configured"))
            # Re-render source badges by updating the cards' html is complex;
            # a full page reload gives the freshest source labels.
            ui.navigate.to("/system-settings")

        async def save_setting(key: str) -> None:
            # `_make_card` registers every key a button exists for, so the editor is
            # present; the shape of the value is decided by the key.
            editor = editors[key]
            current: Any = editor.value
            if key in SECRET_KEYS and not str(current or ""):
                # An empty secret field means "leave it as it is": sending it would
                # delete the key on a page that never showed it.
                ui.notify(t("settings.ai_key_empty"), color="warning")
                return
            value: object = editor_value(key, current)
            try:
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

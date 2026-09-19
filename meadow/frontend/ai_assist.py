"""Shared AI-assist surface for the editors (rule editor, attribute dialog).

Two entry points:

* [`ai_state`][meadow.frontend.ai_assist.ai_state] — whether the assistant is
  switched on *and* configured. The settings response never carries the API key,
  only `configured` (`crates/pim-core/src/api/system.rs`), so the UI can decide
  without ever seeing the secret.
* [`open_suggestion_dialog`][meadow.frontend.ai_assist.open_suggestion_dialog] —
  the dialog both editors use: a description, a mode, the generated suggestion,
  the policy-check verdict, and an **apply** step.

Two properties of the server side show up here:

* the suggestion is **never stored**: the dialog hands the fields to the caller,
  which puts them into the open editor, and the user saves through the normal
  path (`plan/detail/ki-assistenz.md` §6.1);
* a refusal (`ok: false` with errors) is a normal answer, not an exception — the
  errors are shown verbatim so the user can rephrase.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.i18n import current_locale, t

#: Modes offered for a suggestion.
MODES = ("create", "edit", "explain")


async def ai_state(client: CoreClient, token: str) -> dict[str, object]:
    """Whether the assistant can be used, and why not when it cannot.

    Returns `{"available": bool, "reason": str}`; the reason is an i18n key, so a
    page can say why the button is missing instead of hiding it silently.
    """
    try:
        settings = await client.list_system_settings(token, include_source=True)
    except Exception:  # noqa: BLE001 - a missing feature must not break the page
        return {"available": False, "reason": "ai.unavailable"}
    if not (settings.get("ai_enabled") or {}).get("value"):
        return {"available": False, "reason": "ai.disabled"}
    if not (settings.get("ai_api_key") or {}).get("configured"):
        return {"available": False, "reason": "ai.not_configured"}
    for key in ("ai_endpoint", "ai_model"):
        if not str((settings.get(key) or {}).get("value") or "").strip():
            return {"available": False, "reason": "ai.not_configured"}
    return {"available": True, "reason": ""}


def open_suggestion_dialog(
    *,
    client: CoreClient,
    token: str,
    area: str,
    on_apply: Callable[[dict], None],
    title_key: str = "ai.title",
    object_type: Callable[[], str] | None = None,
    rule_type: Callable[[], str] | None = None,
    current_script: Callable[[], str] | None = None,
    target_attribute: str | None = None,
    declared_computed: Callable[[], list[str]] | None = None,
) -> None:
    """Opens the suggestion dialog; `on_apply` receives the accepted reply.

    The callables read the *current* editor state when a suggestion is requested,
    so a dialog opened once always works with the latest values.
    """
    with ui.dialog() as dialog, ui.card().classes("w-[46rem] max-w-[95vw]"):
        ui.label(t(title_key)).classes("text-h6")
        ui.label(t("ai.hint")).classes("text-caption text-gray-500")
        with ui.row().classes("w-full gap-2 mt-2"):
            mode_select = (
                ui.select(
                    {mode: t(f"ai.mode_{mode}") for mode in MODES},
                    label=t("ai.mode"),
                    value="create",
                )
                .props("filled dense")
                .classes("w-48")
            )
            description_input = (
                ui.input(label=t("ai.description")).props("filled dense").classes("grow")
            )
        preview = (
            ui.codemirror("", language="Python")
            .classes("w-full h-64 font-mono text-sm")
            .props("readonly")
        )
        status = ui.label(t("ai.idle")).classes("text-caption text-gray-500")
        errors_box = ui.column().classes("w-full gap-1")
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("grid.close"), on_click=dialog.close).props("flat")
            apply_button = ui.button(t("ai.apply"), icon="check").props("flat")
            generate_button = ui.button(t("ai.generate"), icon="auto_awesome").props("flat")

    #: The last successful reply — what `apply` hands to the caller.
    state: dict[str, object] = {"reply": None}

    def apply() -> None:
        reply = state.get("reply")
        if not isinstance(reply, dict):
            return
        on_apply(reply)
        dialog.close()

    async def run() -> None:
        status.text = t("ai.running")
        status.classes(remove="text-negative text-positive")
        errors_box.clear()
        apply_button.set_enabled(False)
        state["reply"] = None
        mode = str(mode_select.value or "create")
        payload = {
            "area": area,
            "mode": mode,
            "description": description_input.value or "",
            "rule_type": rule_type() if rule_type else None,
            "object_type": (object_type() if object_type else None) or None,
            "current_script": (current_script() if current_script else None) or None,
            "target_attribute": target_attribute,
            "declared_computed": declared_computed() if declared_computed else None,
            "language": current_locale(),
        }
        try:
            reply = await client.ai_generate(token, payload)
        except CoreError as error:
            status.text = f"{t('ai.failed')}: {error}"
            status.classes("text-negative")
            return
        if not reply.get("ok"):
            status.text = t("ai.rejected")
            status.classes("text-negative")
            for message in reply.get("errors") or [t("ai.failed")]:
                with errors_box:
                    ui.label(f"• {message}").classes("text-caption text-negative")
            return
        if mode == "explain":
            preview.value = str(reply.get("description") or "")
        elif area == "search":
            # A query, not a script: show the JSON the core accepted.
            preview.value = json.dumps(reply.get("query") or {}, indent=2, ensure_ascii=False)
        else:
            preview.value = str(reply.get("script") or "")
        status.text = t("ai.ok").format(
            attempts=reply.get("attempts") or 1,
            ms=reply.get("latency_ms") or 0,
            model=reply.get("model") or "?",
        )
        status.classes("text-positive")
        state["reply"] = reply
        apply_button.set_enabled(True)

    apply_button.set_enabled(False)
    apply_button.on_click(apply)
    generate_button.on_click(run)
    dialog.open()

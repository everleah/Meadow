"""Shared AI-assist surface for the editors (rule editor, attribute dialog).

Three entry points:

* [`ai_state`][meadow.frontend.ai_assist.ai_state] — whether the assistant is
  switched on *and* configured. The settings response never carries the API key,
  only `configured` (`crates/pim-core/src/api/system.rs`), so the UI can decide
  without ever seeing the secret.
* [`open_suggestion_dialog`][meadow.frontend.ai_assist.open_suggestion_dialog] —
  the dialog both editors use: a description, a mode, the generated suggestion,
  the policy-check verdict, and an **apply** step.
* [`history_rows`][meadow.frontend.ai_assist.history_rows] — the recorded
  suggestions of one target (audit metadata only, `GET /ai/history`).

Two properties of the server side show up here:

* the suggestion is **never stored**: the dialog hands the fields to the caller,
  which puts them into the open editor, and the user saves through the normal
  path (`plan/detail/ki-assistenz.md` §6.1);
* a refusal (`ok: false` with errors) is a normal answer, not an exception — the
  errors are shown verbatim so the user can rephrase.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable

import httpx
from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.i18n import current_locale, t

#: Modes offered for a suggestion.
MODES = ("create", "edit", "explain")


def offered_modes(
    modes: tuple[str, ...], current_script: Callable[[], str] | None
) -> tuple[str, ...]:
    """Modes that can work with what the open editor holds right now.

    `explain` needs something to explain: the core refuses the request without a
    script, query or configuration (`crates/pim-core/src/api/ai.rs`). A fresh
    editor therefore does not offer it, instead of failing after the click.
    """
    has_text = current_script is not None and bool(str(current_script() or "").strip())
    return modes if has_text else tuple(mode for mode in modes if mode != "explain")


async def stream_request_id(stream_state: dict[str, object], *, timeout_s: float = 2.0) -> str:
    """The running stream's id, waiting briefly if the click came before it.

    The id is the core's **first** stream event, so a „Stopp" pressed immediately
    after „Erzeugen" has nothing to cancel yet — without this it would silently do
    nothing. Returns `""` when the run already ended or no id shows up in time.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        request_id = str(stream_state.get("id") or "")
        if request_id or not stream_state.get("running") or time.monotonic() >= deadline:
            return request_id
        await asyncio.sleep(0.05)


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


def history_rows(entries: list[object]) -> list[dict[str, object]]:
    """Rows for the history dialog from the core's audit projection.

    Returns `when`, `mode`, `verdict` (`accepted` | `cancelled` | `rejected`), `model`,
    `ms` and `user` per entry — the dialog adds the translated labels, so this stays
    testable without a UI context. Only metadata is read: the prompt, the script and
    object values are **never** part of the audit entry (`plan/detail/ki-assistenz.md`
    §6.7), which is what makes a history safe for anyone allowed to ask for suggestions.
    A run the user stopped is not a rejection: the worker marks it `cancelled`.
    """
    rows: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        latency = metadata.get("latency_ms")
        if metadata.get("ok"):
            verdict = "accepted"
        elif metadata.get("cancelled"):
            verdict = "cancelled"
        else:
            verdict = "rejected"
        rows.append(
            {
                "when": _history_time(entry.get("timestamp")),
                "mode": str(metadata.get("mode") or ""),
                "verdict": verdict,
                "model": str(metadata.get("model") or ""),
                "ms": "" if latency is None else str(latency),
                "user": str(entry.get("user_id") or ""),
            }
        )
    return rows


def _history_time(value: object) -> str:
    """`2026-10-17T09:12:44.123Z` → `2026-10-17 09:12` for the table."""
    text = str(value or "")
    return text[:16].replace("T", " ") if len(text) >= 16 else text


def open_suggestion_dialog(
    *,
    client: CoreClient,
    token: str,
    area: str,
    on_apply: Callable[[dict], Awaitable[None] | None],
    title_key: str = "ai.title",
    hint_key: str = "ai.hint",
    object_type: Callable[[], str] | None = None,
    rule_type: Callable[[], str] | None = None,
    current_script: Callable[[], str] | None = None,
    target_attribute: str | None = None,
    declared_computed: Callable[[], list[str]] | None = None,
    config_kind: Callable[[], str] | None = None,
    columns: Callable[[], list[dict]] | None = None,
    statuses: Callable[[], list[str]] | None = None,
    target: Callable[[], str] | None = None,
    modes: tuple[str, ...] = MODES,
) -> None:
    """Opens the suggestion dialog; `on_apply` receives the accepted reply.

    The callables read the *current* editor state when a suggestion is requested,
    so a dialog opened once always works with the latest values. `modes` restricts
    the mode list for an area without an explain target (a group document has no
    prose field to fill); on top of that [`offered_modes`][meadow.frontend.ai_assist.offered_modes]
    drops `explain` while the editor is still empty.

    `target` names **what is being written** (a record id while editing, `new` for
    an unsaved artefact, the attribute name for a computed attribute). It travels
    with the request, lands in the audit as the history key — and unlocks the
    **Verlauf** button, which lists the earlier suggestions for that target. An
    area without a stable identity (the import mapping of an uploaded file) passes
    none and gets no history.
    """
    with ui.dialog() as dialog, ui.card().classes("w-[46rem] max-w-[95vw]"):
        ui.label(t(title_key)).classes("text-h6")
        ui.label(t(hint_key)).classes("text-caption text-gray-500")
        available = offered_modes(modes, current_script) or modes
        with ui.row().classes("w-full gap-2 mt-2"):
            mode_select = (
                ui.select(
                    {mode: t(f"ai.mode_{mode}") for mode in available},
                    label=t("ai.mode"),
                    value=available[0],
                )
                .props("filled dense")
                .classes("w-48")
            )
            description_input = (
                ui.input(label=t("ai.description"))
                .props("filled dense")
                .classes("grow")
                .mark("ai-description")
            )
        if "explain" in modes and "explain" not in available:
            ui.label(t("ai.explain_needs_script")).classes("text-caption text-gray-500")
        preview = (
            ui.codemirror("", language="Python")
            .classes("w-full h-64 font-mono text-sm")
            .props("readonly")
            .mark("ai-preview")
        )
        status = ui.label(t("ai.idle")).classes("text-caption text-gray-500").mark("ai-status")
        note_label = ui.label("").classes("text-caption text-gray-500").mark("ai-note")
        errors_box = ui.column().classes("w-full gap-1")
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            history_button = None
            if target is not None:
                history_button = (
                    ui.button(t("ai.history"), icon="history").props("flat").mark("ai-history")
                )
            close_button = ui.button(t("grid.close")).props("flat")
            apply_button = ui.button(t("ai.apply"), icon="check").props("flat").mark("ai-apply")
            generate_button = (
                ui.button(t("ai.generate"), icon="auto_awesome").props("flat").mark("ai-generate")
            )
            stop_button = ui.button(t("ai.stop"), icon="stop").props("flat").mark("ai-stop")

    #: The last successful reply — what `apply` hands to the caller.
    state: dict[str, object] = {"reply": None}
    #: The running stream: its request id (for a cancel) and whether it runs.
    stream_state: dict[str, object] = {"id": "", "running": False}

    def append_delta(text: str) -> None:
        """Appends one streamed piece to the preview.

        Only prose is streamed (`explain`), so showing the pieces as they arrive
        cannot display anything that would have to pass a check first.
        """
        preview.value = str(preview.value or "") + text

    async def apply() -> None:
        reply = state.get("reply")
        if not isinstance(reply, dict):
            return
        outcome = on_apply(reply)
        if inspect.isawaitable(outcome):  # a caller that has to reload something awaits
            await outcome
        dialog.close()

    async def run() -> None:
        status.classes(remove="text-negative text-positive")
        note_label.set_text("")
        errors_box.clear()
        apply_button.set_enabled(False)
        state["reply"] = None
        stream_state["id"] = ""
        mode = str(mode_select.value or "create")
        streamed = mode == "explain"
        status.text = t("ai.streaming") if streamed else t("ai.running")
        if streamed:
            preview.value = ""
            stream_state["running"] = True
            stop_button.set_visibility(True)
        payload = {
            "area": area,
            "mode": mode,
            "description": description_input.value or "",
            "rule_type": rule_type() if rule_type else None,
            "object_type": (object_type() if object_type else None) or None,
            "current_script": (current_script() if current_script else None) or None,
            "target_attribute": target_attribute,
            "declared_computed": declared_computed() if declared_computed else None,
            "config_kind": config_kind() if config_kind else None,
            "columns": columns() if columns else None,
            "statuses": statuses() if statuses else None,
            "language": current_locale(),
            "target": target() if target else None,
        }
        try:
            if streamed:
                reply = await client.ai_generate_stream(
                    token,
                    payload,
                    on_id=lambda value: stream_state.update({"id": value}),
                    on_delta=append_delta,
                )
            else:
                reply = await client.ai_generate(token, payload)
        except (CoreError, httpx.HTTPError) as error:
            status.text = f"{t('ai.failed')}: {error}"
            status.classes("text-negative")
            return
        finally:
            stream_state["running"] = False
            stop_button.set_visibility(False)
        if not reply.get("ok"):
            messages = [str(item) for item in reply.get("errors") or []]
            if reply.get("cancelled"):
                status.text = t("ai.stopped")
                return
            status.text = t("ai.rejected")
            status.classes("text-negative")
            for message in messages or [t("ai.failed")]:
                with errors_box:
                    ui.label(f"• {message}").classes("text-caption text-negative")
            return
        if mode == "explain":
            preview.value = str(reply.get("description") or "")
        elif area == "search":
            # A query, not a script: show the JSON the core accepted.
            preview.value = json.dumps(reply.get("query") or {}, indent=2, ensure_ascii=False)
        elif area == "connector":
            preview.value = json.dumps(reply.get("config") or {}, indent=2, ensure_ascii=False)
        elif area == "export_profile":
            preview.value = json.dumps(reply.get("profile") or {}, indent=2, ensure_ascii=False)
        elif area == "job":
            preview.value = json.dumps(reply.get("job") or {}, indent=2, ensure_ascii=False)
        elif area == "import_mapping":
            preview.value = json.dumps(reply.get("mapping") or {}, indent=2, ensure_ascii=False)
        elif area == "group_permissions":
            preview.value = json.dumps(reply.get("permissions") or {}, indent=2, ensure_ascii=False)
        else:
            preview.value = str(reply.get("script") or "")
        status.text = t("ai.ok").format(
            attempts=reply.get("attempts") or 1,
            ms=reply.get("latency_ms") or 0,
            model=reply.get("model") or "?",
        )
        status.classes("text-positive")
        if reply.get("note"):
            note_label.set_text(f"{t('ai.note')}: {reply['note']}")
        state["reply"] = reply
        apply_button.set_enabled(True)

    async def show_history() -> None:
        """Lists the earlier suggestions for this target (audit metadata only)."""
        if target is None:
            return
        try:
            answer = await client.ai_history(token, area=area, target=target() or "new")
        except (CoreError, httpx.HTTPError) as exc:
            ui.notify(f"{t('ai.history_failed')}: {exc}", color="negative")
            return
        entries = answer.get("entries")
        rows = [
            {
                **row,
                "result": {
                    "accepted": t("ai.history_accepted"),
                    "cancelled": t("ai.history_cancelled"),
                }.get(str(row["verdict"]), t("ai.history_rejected")),
            }
            for row in history_rows(entries if isinstance(entries, list) else [])
        ]
        with ui.dialog() as history_dialog, ui.card().classes("w-[44rem] max-w-[95vw]"):
            ui.label(t("ai.history_title")).classes("text-h6")
            if rows:
                ui.table(
                    columns=[
                        {
                            "name": "when",
                            "label": t("ai.history_when"),
                            "field": "when",
                            "align": "left",
                        },
                        {
                            "name": "mode",
                            "label": t("ai.mode"),
                            "field": "mode",
                            "align": "left",
                        },
                        {
                            "name": "result",
                            "label": t("ai.history_result"),
                            "field": "result",
                            "align": "left",
                        },
                        {
                            "name": "model",
                            "label": t("ai.history_model"),
                            "field": "model",
                            "align": "left",
                        },
                        {"name": "ms", "label": "ms", "field": "ms", "align": "right"},
                        {
                            "name": "user",
                            "label": t("ai.history_user"),
                            "field": "user",
                            "align": "left",
                        },
                    ],
                    rows=rows,
                ).classes("w-full")
            else:
                ui.label(t("ai.history_empty")).classes("text-caption text-gray-500")
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=history_dialog.close).props("flat")
        history_dialog.open()

    async def stop() -> None:
        """Asks the core to abort the running stream (the worker stops the provider)."""
        request_id = await stream_request_id(stream_state)
        if not request_id:
            return
        try:
            await client.ai_cancel(token, request_id)
        except (CoreError, httpx.HTTPError) as error:
            ui.notify(f"{t('ai.stop_failed')}: {error}", color="negative")

    async def close_dialog() -> None:
        if stream_state["running"]:
            await stop()
        dialog.close()

    stop_button.set_visibility(False)
    apply_button.set_enabled(False)
    apply_button.on_click(apply)
    generate_button.on_click(run)
    stop_button.on_click(stop)
    close_button.on_click(close_dialog)
    if history_button is not None:
        history_button.on_click(show_history)
    dialog.open()

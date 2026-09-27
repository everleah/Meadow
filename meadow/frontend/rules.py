"""Admin page for business rules and the Rule Lab (admin/superadmin only).

- **Rules**: list rules (``GET /rules``) with a create/edit dialog (``POST`` /
  ``PATCH /rules``; edit opens for the selected rule on double-click or via the
  **Edit** button), activate/disable (``POST /rules/{id}/activate`` /
  ``/disable``), a **Delete** action (``DELETE /rules/{id}``, with confirmation)
  and a **Rule Lab** button that opens the interactive lab for the selected rule.
- **Rule Lab**: run a rule read-only against a real object (``POST
  /rules/{id}/dry-run``), a selection of objects (``POST
  /rules/{id}/test-batch``) or a constructed payload (``dry-run`` with
  ``object``). Shows errors/warnings, computed values (before/after), ``log()``
  output and the transition verdict. No persistence.
"""

from __future__ import annotations

import json as _json
from datetime import datetime

import httpx
from nicegui import app, ui

from meadow.ai.context import (
    attribute_facts,
    attribute_names_text,
    contract_block,
    policy_block,
)
from meadow.frontend import auth
from meadow.frontend.ai_assist import ai_state, open_suggestion_dialog
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.display import object_row_label
from meadow.frontend.filters import filter_select
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.multilang import base_lang
from meadow.frontend.object_picker import open_object_picker

#: Known rule types (matches the core's VALID_RULE_TYPES).
RULE_TYPES = ("validation", "computed", "normalization", "workflow_guard", "bulk")


def _state_badge(state: str) -> str:
    """A coloured badge for a rule state."""
    classes = {
        "draft": "bg-neutral text-gray-600",
        "active": "bg-positive text-white",
        "disabled": "bg-negative text-white",
    }.get(state, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{_state_label(state)}</span>'


def _state_label(state: str) -> str:
    """A localized label for a rule state."""
    return {
        "draft": t("rules.state_draft"),
        "active": t("rules.state_active"),
        "disabled": t("rules.state_disabled"),
    }.get(state, state)


def _type_label(rule_type: str) -> str:
    """A localized label for a rule type."""
    return {
        "validation": t("rules.type_validation"),
        "computed": t("rules.type_computed"),
        "normalization": t("rules.type_normalization"),
        "workflow_guard": t("rules.type_workflow_guard"),
        "bulk": t("rules.type_bulk"),
    }.get(rule_type, rule_type)


def _fmt_dt(value: str | None) -> str:
    """Formats a SurrealDB ISO timestamp for display (local time, no tz)."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:  # leave the raw value on parse failure
        return str(value)


def _applies_label(applies: object) -> str:
    """A compact label for a rule's `applies_to` (empty = all)."""
    if isinstance(applies, list) and applies:
        return ", ".join(str(v) for v in applies)
    return "—"


def _json_pretty(value: object) -> str:
    """Pretty-prints a JSON value for the results panel."""
    try:
        return _json.dumps(value, indent=2, ensure_ascii=False)
    except TypeError, ValueError:  # fall back to the raw string
        return str(value)


@ui.page("/rules", response_timeout=PAGE_TIMEOUT)
async def rules_page() -> None:
    await build_shell(active="/rules")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")

    # Editor and lab state shared between the render helpers and their handlers. An
    # attribute on the button object would be invisible to the reader - and to the
    # type checker.
    editing_id: str | None = None
    lab_rule: dict | None = None

    try:
        rules = await client.list_rules(token, limit=1000)
    except CoreError, httpx.HTTPError:  # show a friendly message on failure
        ui.label(t("rules.load_failed")).classes("text-subtitle1 text-negative")
        return

    try:
        object_types = await client.list_object_types(token, limit=1000)
    except CoreError, httpx.HTTPError:  # degrade to default types
        object_types = []
    type_names = sorted({str(ot.get("name")) for ot in object_types if ot.get("name")})
    # The language object labels are resolved in (`GET /objects` returns raw attributes).
    ui_lang = base_lang(current_locale())
    # Whether the AI assistant can be used (opt-in, configured, admin page) — and
    # why not, so the editor can say it instead of hiding the button silently.
    ai = await ai_state(client, token)

    with ui.column().classes("w-full p-6"):
        ui.label(t("rules.title")).classes("text-h4")
        ui.label(t("rules.subtitle")).classes("text-subtitle1")

        filters: dict[str, str] = {"name": "", "type": "", "state": ""}

        def _matches(r: dict) -> bool:
            if filters["name"]:
                q = filters["name"].lower()
                if q not in str(r.get("name") or "").lower():
                    return False
            if filters["type"] and str(r.get("type") or "") != filters["type"]:
                return False
            return not (filters["state"] and str(r.get("state") or "draft") != filters["state"])

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(r.get("id")),
                    "name": str(r.get("name") or ""),
                    "type": _type_label(str(r.get("type") or "")),
                    "state": _state_badge(str(r.get("state") or "draft")),
                    "applies": _applies_label(r.get("applies_to")),
                    "version": str(r.get("version") or 0),
                    "updated": _fmt_dt(r.get("updated_at")),
                    "updated_by": str(r.get("updated_by") or "").split(":")[-1],
                }
                for r in rules
                if _matches(r)
            ]

        # Filter bar (free-text name / type / state).
        with ui.row().classes("w-full items-center gap-2 mt-2"):
            (
                ui.input(t("rules.filter_name"))
                .props("filled dense")
                .classes("w-48")
                .on_value_change(lambda e: _set_filter("name", str(e.value)))
            )
            filter_select(
                {typ: _type_label(typ) for typ in RULE_TYPES},
                label=t("rules.filter_type"),
                classes="w-44",
                on_change=lambda value: _set_filter("type", str(value)),
            )
            filter_select(
                {
                    "draft": t("rules.state_draft"),
                    "active": t("rules.state_active"),
                    "disabled": t("rules.state_disabled"),
                },
                label=t("rules.filter_state"),
                classes="w-44",
                on_change=lambda value: _set_filter("state", str(value)),
            )

        def _apply_filters() -> None:
            grid.run_grid_method("setGridOption", "rowData", _rows())

        def _set_filter(key: str, value: str) -> None:
            filters[key] = value
            _apply_filters()

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("rules.col_name"), "field": "name", "width": 200},
                    {"headerName": t("rules.col_type"), "field": "type", "width": 140},
                    {"headerName": t("rules.col_state"), "field": "state", "width": 110},
                    {"headerName": t("rules.col_applies"), "field": "applies", "width": 170},
                    {"headerName": t("rules.col_version"), "field": "version", "width": 90},
                    {"headerName": t("rules.col_updated"), "field": "updated", "width": 190},
                    {"headerName": t("rules.col_updated_by"), "field": "updated_by", "width": 120},
                ],
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[2],
        ).classes("w-full")

        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(t("rules.new"), icon="add", on_click=lambda: open_editor(None)).props(
                    "flat"
                )
                ui.button(t("rules.edit"), icon="edit", on_click=lambda: edit_selected()).props(
                    "flat"
                )
                ui.button(
                    t("rules.activate"),
                    icon="check_circle",
                    on_click=lambda: change_state("activate"),
                ).props("flat")
                ui.button(
                    t("rules.disable"),
                    icon="block",
                    on_click=lambda: change_state("disable"),
                ).props("flat")
                ui.button(
                    t("rules.delete"), icon="delete", on_click=lambda: delete_selected()
                ).props("flat color=negative")
                ui.button(
                    t("rules.lab"), icon="science", on_click=lambda: open_lab_for_selection()
                ).props("flat")
            ui.button(t("rules.reload"), icon="refresh", on_click=lambda: reload_rules()).props(
                "flat"
            )

        # --- Create/edit dialog --------------------------------------------
        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[42rem] max-w-[94vw]"):
            editor_title = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full gap-2"):
                    name_input = (
                        ui.input(t("rules.field_name")).props("filled dense").classes("grow")
                    )
                    type_select = (
                        ui.select(
                            {typ: _type_label(typ) for typ in RULE_TYPES},
                            label=t("rules.field_type"),
                            value="validation",
                        )
                        .props("filled dense")
                        .classes("w-56")
                    )
                description_input = (
                    ui.input(t("rules.field_description")).props("filled dense").classes("w-full")
                )
                applies_select = (
                    ui.select(
                        {typ: typ for typ in type_names},
                        label=t("rules.field_applies"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                ui.label(t("rules.field_script")).classes("text-subtitle2")
                script_editor = ui.codemirror("", language="Python").classes(
                    "w-full h-64 font-mono text-sm"
                )
                with ui.row().classes("w-full items-center gap-2"):
                    if ai.get("available"):
                        # The suggestion goes into the editor above - never into the
                        # database (`plan/detail/ki-assistenz.md` §6.1).
                        ui.button(
                            t("rules.ai_suggest"),
                            icon="auto_awesome",
                            on_click=lambda: suggest_rule(script_editor),
                        ).props("flat dense")
                        ui.label(t("rules.ai_suggest_hint")).classes("text-caption text-gray-500")
                    else:
                        ui.label(t(str(ai.get("reason") or "ai.unavailable"))).classes(
                            "text-caption text-gray-500"
                        )
                # Contract & policy panel: what a rule may use, taken from the sandbox
                # itself (`meadow.ai`), plus the own attributes of the selected type.
                # The blocks are filled on first expand — the attribute vocabulary is a
                # multi-megabyte call and must not run on every dialog open.
                with ui.expansion(t("rules.contract_title"), icon="school").classes(
                    "w-full"
                ) as contract_panel:
                    ui.label(t("rules.contract_hint")).classes("text-caption text-gray-500")
                    contract_body = ui.column().classes("w-full gap-2")
                    contract_attr_host = ui.column().classes("w-full gap-1")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                ui.button(t("rules.save"), icon="save", on_click=lambda: save_rule()).props("flat")

        #: Panel state: built once (lazy), the vocabulary cached per page render.
        contract_state: dict[str, object] = {"built": False, "vocabulary": None}

        async def _contract_vocabulary() -> list[dict]:
            """The attribute vocabulary (cached) — the projection needs it."""
            cached = contract_state.get("vocabulary")
            if isinstance(cached, list):
                return cached
            vocabulary: list[dict] = await client.list_attribute_vocabulary(token)
            contract_state["vocabulary"] = vocabulary
            return vocabulary

        async def render_contract_attributes() -> None:
            """Render the own attributes of the currently selected object types."""
            if not contract_state["built"]:
                return
            contract_attr_host.clear()
            selected = [str(name) for name in (applies_select.value or [])]
            with contract_attr_host:
                if not selected:
                    ui.label(t("rules.contract_attributes_all")).classes(
                        "text-caption text-gray-500"
                    )
                    return
                try:
                    vocabulary = await _contract_vocabulary()
                except CoreError, httpx.HTTPError:  # degrade to a hint, never a traceback
                    ui.label(t("rules.contract_attributes_failed")).classes(
                        "text-caption text-negative"
                    )
                    return
                facts = {
                    fact.name: fact
                    for object_type in selected
                    for fact in attribute_facts(vocabulary, object_type)
                }
                own = sorted(facts.values(), key=lambda fact: fact.name)
                ui.label(
                    t("rules.contract_attributes").format(types=", ".join(selected))
                    + f" ({len(own)})"
                ).classes("text-caption font-medium")
                ui.label(attribute_names_text(own)).classes(
                    "text-xs font-mono whitespace-pre-wrap w-full max-h-40 overflow-y-auto"
                )
                if any(fact.computed for fact in own):
                    ui.label(
                        t("rules.contract_attributes_note")
                        + " "
                        + ", ".join(fact.name for fact in own if fact.computed)
                    ).classes("text-caption text-gray-500")

        async def build_contract_panel() -> None:
            """Fill the blocks once, on first expand."""
            if contract_state["built"]:
                return
            contract_state["built"] = True
            with (
                contract_body,
                ui.row().classes("w-full gap-4 items-start").style("flex-wrap: wrap"),
            ):
                with ui.column().classes("flex-1 min-w-80 gap-0"):
                    ui.label(t("rules.contract_result")).classes("text-caption font-medium")
                    ui.label(contract_block()).classes(
                        "text-xs font-mono whitespace-pre-wrap w-full"
                    )
                with ui.column().classes("flex-1 min-w-80 gap-0"):
                    ui.label(t("rules.contract_policy")).classes("text-caption font-medium")
                    ui.label(policy_block()).classes(
                        "text-xs font-mono whitespace-pre-wrap w-full max-h-64 overflow-y-auto"
                    )
            await render_contract_attributes()

        contract_panel.on_value_change(
            lambda event: build_contract_panel() if event.value else None
        )
        applies_select.on_value_change(lambda _event: render_contract_attributes())

        def _fill_editor(rule: dict | None) -> None:
            nonlocal editing_id
            editing = rule is not None
            rule = rule or {}  # the reads below only run while editing
            editor_title.set_text(t("rules.edit") if editing else t("rules.new"))
            editing_id = rule.get("id") if rule else None
            name_input.set_value(str(rule.get("name") or "") if editing else "")
            current_type = str(rule.get("type") or "validation") if editing else "validation"
            type_select.set_value(current_type)
            description_input.set_value(str(rule.get("description") or "") if editing else "")
            applies = [str(v) for v in (rule.get("applies_to") or [])] if editing else []
            # Keep any type that is not (yet) in the registry selectable so an
            # existing value is never silently dropped on save.
            missing = [v for v in applies if v not in type_names]
            if missing:
                applies_select.options = {
                    **{typ: typ for typ in type_names},
                    **{v: v for v in missing},
                }
                applies_select.update()
            applies_select.set_value(applies or [])
            script_editor.set_value(str(rule.get("script") or "") if editing else "")

        def open_editor(rule: dict | None) -> None:
            _fill_editor(rule)
            editor_dialog.open()

        def suggest_rule(editor: ui.codemirror) -> None:
            """Opens the AI dialog; applying a suggestion fills the open editor.

            The name and the description are only filled when the field is still
            empty — an edit must not overwrite what the user typed.
            """

            def apply(reply: dict) -> None:
                script = reply.get("script")
                if not script:
                    # Explain mode: the answer is prose for the description field.
                    if not (description_input.value or "").strip():
                        description_input.set_value(str(reply.get("description") or ""))
                    return
                editor.set_value(str(script))
                if not (name_input.value or "").strip() and reply.get("rule_name"):
                    name_input.set_value(str(reply["rule_name"]))
                if not (description_input.value or "").strip() and reply.get("description"):
                    description_input.set_value(str(reply["description"]))
                dependencies = reply.get("dependencies") or []
                if dependencies:
                    # The dependency list belongs to the attribute definition, not to
                    # the rule: say it instead of dropping the information.
                    ui.notify(
                        t("rules.ai_dependencies").format(names=", ".join(map(str, dependencies))),
                        color="info",
                    )

            open_suggestion_dialog(
                client=client,
                token=token,
                area="rule",
                title_key="rules.ai_title",
                object_type=lambda: next(iter(applies_select.value or []), ""),
                rule_type=lambda: str(type_select.value or "validation"),
                current_script=lambda: str(editor.value or ""),
                on_apply=apply,
            )

        async def save_rule() -> None:
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("rules.name_required"), color="warning")
                return
            rule_type = type_select.value or "validation"
            description = (description_input.value or "").strip() or None
            applies_values = [str(v) for v in (applies_select.value or [])]
            script = script_editor.value or ""
            try:
                if editing_id:
                    await client.update_rule(
                        token,
                        editing_id,
                        name=name,
                        script=script,
                        description=description,
                        applies_to=applies_values,
                    )
                else:
                    await client.create_rule(
                        token,
                        name=name,
                        rule_type=rule_type,
                        script=script,
                        description=description,
                        applies_to=applies_values,
                    )
            except CoreError as error:
                ui.notify(f"{t('rules.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload_rules()
            ui.notify(t("rules.saved"), color="positive")

        async def reload_rules() -> None:
            nonlocal rules
            try:
                rules = await client.list_rules(token, limit=1000)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def get_selected() -> dict | None:
            try:
                rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                rows = []
            if not rows:
                return None
            row = rows[0] if isinstance(rows, list) else rows
            if isinstance(row, dict) and row.get("id"):
                return next((r for r in rules if str(r.get("id")) == str(row.get("id"))), row)
            return row if isinstance(row, dict) else None

        async def edit_selected() -> None:
            rule = await get_selected()
            if not rule:
                ui.notify(t("rules.no_selection"), color="warning")
                return
            open_editor(rule)

        async def delete_selected() -> None:
            rule = await get_selected()
            if not rule:
                ui.notify(t("rules.no_selection"), color="warning")
                return
            rule_id = str(rule.get("id"))
            name = str(rule.get("name") or "")
            with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                ui.label(t("rules.delete_confirm").format(name=name)).classes("text-subtitle1")
                ui.label(t("rules.delete_warn")).classes("text-caption text-gray-600")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _go() -> None:
                        confirm.close()
                        try:
                            await client.delete_rule(token, rule_id)
                        except CoreError as error:
                            ui.notify(f"{t('rules.delete_failed')}: {error}", color="negative")
                            return
                        ui.notify(t("rules.deleted"), color="positive")
                        await reload_rules()

                    ui.button(t("rules.delete"), on_click=_go).props("flat color=negative")
            confirm.open()

        def _rule_for(data: object) -> dict | None:
            """Resolves a double-click payload to the full rule record."""
            rid = str(data.get("id") or "") if isinstance(data, dict) else str(data or "")
            if not rid:
                return None
            return next((r for r in rules if str(r.get("id")) == rid), None)

        async def change_state(action: str) -> None:
            rule = await get_selected()
            if not rule:
                ui.notify(t("rules.no_selection"), color="warning")
                return
            rule_id = str(rule.get("id"))
            name = str(rule.get("name") or "")
            state = str(rule.get("state") or "")

            if action == "activate" and state == "active":
                ui.notify(t("rules.activated_failed"), color="warning")
                return
            if action == "disable" and state == "disabled":
                ui.notify(t("rules.disabled_failed"), color="warning")
                return

            async def _apply() -> None:
                label = t("rules.activate") if action == "activate" else t("rules.disable")
                confirm.close()
                try:
                    if action == "activate":
                        await client.activate_rule(token, rule_id)
                        ui.notify(t("rules.activated"), color="positive")
                    else:
                        await client.disable_rule(token, rule_id)
                        ui.notify(t("rules.disabled"), color="positive")
                except CoreError as error:
                    ui.notify(f"{label}: {error}", color="negative")
                    return
                await reload_rules()

            message = (
                t("rules.activate_confirm").format(name=name)
                if action == "activate"
                else f"{t('rules.disable')} — {name}"
            )
            with ui.dialog() as confirm, ui.card().classes("w-[30rem] max-w-[90vw]"):
                ui.label(message).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")
                    ui.button(
                        t("rules.activate") if action == "activate" else t("rules.disable"),
                        on_click=_apply,
                    ).props("color=secondary")
            confirm.open()

        # --- Rule Lab dialog -----------------------------------------------
        lab_dialog = ui.dialog()
        with lab_dialog, ui.card().classes("w-[56rem] max-w-[96vw]"):
            lab_title = ui.label("").classes("text-h6")
            ui.label(t("rules.lab_subtitle")).classes("text-subtitle2 text-gray-600")
            with ui.column().classes("w-full mt-2 gap-3"):
                mode_radio = ui.radio(
                    {
                        "objects": t("rules.lab_mode_objects"),
                        "payload": t("rules.lab_mode_payload"),
                    },
                    value="objects",
                ).props("inline")

                # The object type is a **filter for the list below** (and for the picker):
                # an input would make the user type a name the registry already knows.
                object_type_select = (
                    ui.select(
                        {"": t("rules.lab_all_types"), **{name: name for name in type_names}},
                        label=t("rules.lab_object_type"),
                        value="",
                        with_input=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                    .mark("lab-object-type")
                )
                with ui.row().classes("w-full gap-2").style("flex-wrap: nowrap"):
                    object_select = (
                        ui.select({}, label=t("rules.lab_objects"), multiple=True)
                        .props("filled dense outlined")
                        .classes("grow")
                        .mark("lab-objects")
                    )
                    ui.button(
                        icon="account_tree",
                        on_click=lambda: pick_objects(),
                    ).props("flat").tooltip(t("form.pick_in_structure"))
                    ui.button(
                        t("rules.reload"),
                        icon="refresh",
                        on_click=lambda: load_object_options(),
                    ).props("flat")
                payload_area = (
                    ui.textarea(t("rules.lab_payload"))
                    .props("filled dense")
                    .classes("w-full font-mono text-sm")
                    .mark("lab-payload")
                )
                ui.label(t("rules.lab_payload_hint")).classes("text-caption text-gray-500")
                with ui.row().classes("w-full justify-end gap-2"):
                    run_button = (
                        ui.button(
                            t("rules.lab_run"),
                            icon="play_arrow",
                            on_click=lambda: run_lab(),
                        )
                        .props("color=secondary")
                        .mark("lab-run")
                    )

                results = ui.column().classes("w-full mt-3 gap-2").mark("lab-results")

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=lab_dialog.close).props("flat")

        def _sync_lab_visibility() -> None:
            mode = mode_radio.value
            object_type_select.set_visibility(mode == "objects")
            object_select.set_visibility(mode == "objects")
            payload_area.set_visibility(mode == "payload")

        def _sync_run_label() -> None:
            """The run button names how many objects it will send (no silent truncation)."""
            count = len(object_select.value or [])
            label = t("rules.lab_run")
            run_button.set_text(label if not count else f"{label} ({count})")

        mode_radio.on_value_change(lambda _e: _sync_lab_visibility())
        object_select.on_value_change(lambda _e: _sync_run_label())

        async def load_object_options() -> None:
            """Fills the object list with the objects of the chosen type (a filter, not a rule).

            An empty type means "any type" — the picker and the search are the tools for a
            catalogue that does not fit into a list, this one is the convenience for a few.
            """
            object_type = str(object_type_select.value or "") or None
            try:
                rows = await client.list_objects(token, object_type=object_type, limit=200)
            except CoreError, httpx.HTTPError:  # degrade to no options
                rows = []
            options = {str(row.get("id")): object_row_label(row, ui_lang) for row in rows}
            # A type change must not keep ids that are no longer on offer (a select whose
            # value is not among its options takes the page down).
            kept = [value for value in (object_select.value or []) if value in options]
            object_select.set_options(options, value=kept)
            _sync_run_label()

        object_type_select.on_value_change(lambda _e: load_object_options())

        async def pick_objects() -> None:
            """Picks test objects with the shared picker (search + structure tree)."""
            object_type = str(object_type_select.value or "") or None

            async def on_pick(hits: list[dict]) -> None:
                """Merges the picked objects into the selection.

                The picker starts empty on every open (like the scope card's), so a second
                pick **adds** to the first instead of replacing it.
                """
                merged = {str(hit["id"]): f"{hit.get('label')} · {hit.get('type')}" for hit in hits}
                previous = object_select.options
                if isinstance(previous, dict):  # keep what the list had contributed
                    for value, label in previous.items():
                        merged.setdefault(str(value), str(label))
                chosen = [*(object_select.value or []), *(str(hit["id"]) for hit in hits)]
                object_select.set_options(merged, value=list(dict.fromkeys(chosen)))
                _sync_run_label()

            await open_object_picker(
                client=client,
                token=token,
                locale=current_locale(),
                title=t("rules.lab_objects"),
                on_pick=on_pick,
                target_types=[object_type] if object_type else None,
                multi=True,
            )

        def _edit_lab_state(rule: dict) -> None:
            nonlocal lab_rule
            lab_rule = rule
            lab_title.set_text(f"{t('rules.lab_title')} — {str(rule.get('name') or '')}")
            # The rule usually knows which types it is for: prefill the filter when it names
            # exactly one, so the list is useful without a single click.
            applies = [str(value) for value in (rule.get("applies_to") or [])]
            single = applies[0] if len(applies) == 1 and applies[0] in type_names else ""
            object_type_select.set_value(single)
            object_select.set_options({})
            object_select.set_value([])
            payload_area.set_value("")
            results.clear()
            _sync_run_label()
            _sync_lab_visibility()

        async def open_lab_for_selection() -> None:
            rule = await get_selected()
            if not rule:
                ui.notify(t("rules.no_selection"), color="warning")
                return
            _edit_lab_state(rule)
            lab_dialog.open()

        async def run_lab() -> None:
            rule = lab_rule
            if not rule:
                ui.notify(t("rules.no_selection"), color="warning")
                return
            rule_id = str(rule.get("id"))
            mode = mode_radio.value

            results.clear()
            with results:
                ui.spinner().classes("place-self-center my-4")
            run_button.set_enabled(False)

            try:
                if mode == "payload":
                    payload_text = (payload_area.value or "").strip()
                    if not payload_text:
                        ui.notify(t("rules.lab_no_objects"), color="warning")
                        results.clear()
                        return
                    try:
                        parsed = _json.loads(payload_text)
                    except ValueError as exc:  # a payload that is not JSON shows a warning
                        ui.notify(f"{t('rules.lab_invalid_payload')}: {exc}", color="warning")
                        results.clear()
                        return
                    if not isinstance(parsed, dict):
                        ui.notify(t("rules.lab_invalid_payload"), color="warning")
                        results.clear()
                        return
                    outcome = await client.dry_run_rule(token, rule_id, object_data=parsed)
                    await render_outcomes([(None, parsed, outcome)])
                    return

                # One object or many: the same call, one outcome card per object. A separate
                # "single object" mode used to send only the **first** of the chosen ids and
                # silently dropped the rest.
                chosen = [str(value) for value in (object_select.value or [])]
                if not chosen:
                    ui.notify(t("rules.lab_no_objects"), color="warning")
                    results.clear()
                    return
                # Labels for exactly these objects (the type filter is a list filter, not a
                # statement about which objects may be run).
                try:
                    rows = await client.list_objects(token, ids=chosen, limit=len(chosen))
                except CoreError, httpx.HTTPError:  # degrade to no labels
                    rows = []
                by_id = {str(row.get("id")): row for row in rows}
                batch_outcome = await client.test_batch_rule(token, rule_id, chosen)
                await render_outcomes(
                    [
                        (
                            str(o.get("object_id")),
                            by_id.get(str(o.get("object_id")), {}),
                            o.get("outcome"),
                        )
                        for o in batch_outcome
                    ]
                )
            except CoreError as error:
                ui.notify(f"{t('rules.lab_run_failed')}: {error}", color="negative")
                results.clear()
            finally:
                run_button.set_enabled(True)

        async def render_outcomes(entries: list[tuple]) -> None:
            results.clear()
            with results:
                if not entries:
                    ui.label(t("rules.lab_empty")).classes("text-subtitle2 text-gray-500")
                    return
                for object_id, candidate, outcome in entries:
                    with ui.card().classes("w-full"):
                        header = ""
                        if candidate:
                            header = object_row_label(candidate, ui_lang)
                        if header and object_id:
                            header = f"{header} · {object_id}"
                        ui.label(header or t("rules.lab_object")).classes(
                            "text-subtitle2 font-medium"
                        )
                        render_issues(
                            outcome.get("errors", []),
                            outcome.get("warnings", []),
                            outcome.get("failed", []),
                        )
                        computed = outcome.get("computed")
                        if isinstance(computed, dict) and computed:
                            render_computed(candidate, computed)
                        logs = outcome.get("logs") or []
                        if logs:
                            render_logs(logs)
                        allowed = outcome.get("allowed")
                        if allowed is not None:
                            render_verdict(bool(allowed), outcome.get("deny_reason"))

        def render_issues(errors: list, warnings: list, failed: list) -> None:
            if errors:
                with ui.column().classes("gap-0.5"):
                    ui.label(t("rules.lab_errors")).classes("text-caption text-negative")
                    for issue in errors:
                        ui.label(
                            f"• {issue.get('attr', '')} [{issue.get('code', '')}] "
                            f"{issue.get('message', '')}"
                        ).classes("text-sm text-negative")
            if warnings:
                with ui.column().classes("gap-0.5"):
                    ui.label(t("rules.lab_warnings")).classes("text-caption text-warning")
                    for issue in warnings:
                        ui.label(
                            f"• {issue.get('attr', '')} [{issue.get('code', '')}] "
                            f"{issue.get('message', '')}"
                        ).classes("text-sm text-warning")
            if failed:
                with ui.column().classes("gap-0.5"):
                    ui.label(t("rules.lab_failed")).classes("text-caption text-negative")
                    for message in failed:
                        ui.label(f"• {message}").classes("text-sm text-negative")

        def render_computed(candidate: object, computed: dict) -> None:
            before = candidate.get("attributes", {}) if isinstance(candidate, dict) else {}
            attrs = before if isinstance(before, dict) else {}
            rows = [
                {
                    "attr": str(key),
                    "before": _json_pretty(attrs.get(key)),
                    "after": _json_pretty(value),
                }
                for key, value in computed.items()
            ]
            ui.label(t("rules.lab_computed")).classes("text-caption font-medium")
            ui.table(
                columns=[
                    {"name": "attr", "label": "attribut", "field": "attr", "align": "left"},
                    {
                        "name": "before",
                        "label": t("rules.lab_before"),
                        "field": "before",
                        "align": "left",
                    },
                    {
                        "name": "after",
                        "label": t("rules.lab_after"),
                        "field": "after",
                        "align": "left",
                    },
                ],
                rows=rows,
                row_key="attr",
            ).props("dense flat bordered")

        def render_logs(logs: list) -> None:
            with ui.column().classes("gap-0.5"):
                ui.label(t("rules.lab_logs")).classes("text-caption")
                for entry in logs:
                    ui.label(_json_pretty(entry)).classes("text-xs text-gray-600 font-mono")

        def render_verdict(allowed: bool, reason: str | None) -> None:
            label = t("rules.lab_allowed") if allowed else t("rules.lab_denied")
            color = "text-positive" if allowed else "text-negative"
            ui.label(label).classes(f"text-sm font-medium {color}")
            if reason:
                ui.label(f"{t('rules.lab_deny_reason')}: {reason}").classes("text-sm text-negative")

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(_rule_for(e.args.get("data"))),
            ["data"],
        )

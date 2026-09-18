"""Admin page for attribute definitions (admin/superadmin only).

Lists the attribute catalog (the product template per object type via
``applicable_types``/``required_types``) and allows creating and editing
definitions. Writes go through ``POST``/``PATCH /attribute-definitions``.
"""

from __future__ import annotations

from typing import Any

import httpx
from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.attribute_form import (
    cleaned_payload,
    field_state,
    hides_standard,
    standard_count,
)
from meadow.frontend.filters import filter_select
from meadow.frontend.grid import resolve_value
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.multilang import base_lang, multilingual_value_editor
from meadow.frontend.range_values import NUMERIC_TYPES

#: Supported attribute data types (extensible; existing types are never renamed).
ATTR_TYPES = ("text", "url", "number", "range", "boolean", "date", "enum", "reference")
#: Classification standards an attribute can be bound to (`standard_codes`, see
#: `plan/detail/bmecat-export.md` §11.7). The key is the map key on the record.
STANDARDS = {"etim": "ETIM", "eclass": "ECLASS", "unspsc": "UNSPSC"}


@ui.page("/attribute-definitions", response_timeout=PAGE_TIMEOUT)
async def attribute_definitions_page() -> None:
    await build_shell(active="/attribute-definitions")
    locale = current_locale()
    client = CoreClient(config.CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("attr_def.title")).classes("text-h4")
        ui.label(t("attr_def.subtitle")).classes("text-subtitle1")

        try:
            # The **slim vocabulary** of *all* definitions, not the paged catalog
            # list: that one is capped at 1000 rows, and with an imported standard
            # the first 1000 are the standard's features (`EF…`), so an own
            # attribute was simply not reachable here. The core serves the
            # projection from its cache; the editor loads the full record on open.
            definitions = await client.list_attribute_vocabulary(token)
        except CoreError, httpx.HTTPError:  # show a friendly message on failure
            ui.label(t("attr_def.load_failed")).classes("text-subtitle1 text-negative")
            return
        try:
            units = await client.list_units(token, limit=1000)
        except CoreError, httpx.HTTPError:  # degrade to no units
            units = []

        def _unit_key(u: dict) -> str:
            return str(u.get("id")).split(":")[-1]

        unit_options = {
            _unit_key(u): (
                f"{u.get('symbol')} · {resolve_value(u.get('label'), locale) or u.get('symbol')}"
            )
            for u in units
            if u.get("id")
        }
        unit_key_by_symbol = {
            str(u.get("symbol")): _unit_key(u) for u in units if u.get("symbol") and u.get("id")
        }

        def _resolve_unit_key(value: object) -> str | None:
            """Maps a stored unit reference (record key or symbol) to an option key."""
            v = str(value or "")
            if v in unit_options:
                return v
            return unit_key_by_symbol.get(v)

        # Active data languages drive the multilingual label editor (hybrid).
        try:
            languages = await client.list_languages(token)
        except CoreError, httpx.HTTPError:  # degrade to the UI language only
            languages = []

        # Reusable enum sets drive the `enum_set_id` selector for `enum` types.
        try:
            enum_sets = await client.list_enum_sets(token)
        except CoreError, httpx.HTTPError:  # degrade to no enum sets
            enum_sets = []
        enum_set_options: dict[str, str] = {
            str(s.get("id")): (
                resolve_value(s.get("label"), locale) or str(s.get("name")) or str(s.get("id"))
            )
            for s in enum_sets
            if s.get("id")
        }

        try:
            groups = await client.list_attribute_groups(token)
        except CoreError, httpx.HTTPError:  # degrade to no groups
            groups = []
        # Object types come from the managed registry (`/object-types`), so the
        # `applicable_types`/`required_types` dropdowns show all known types,
        # not just the types of existing objects.
        try:
            object_type_rows = await client.list_object_types(token, limit=1000)
        except CoreError, httpx.HTTPError:  # degrade to the default types
            object_type_rows = []

        object_types: list[str] = sorted(
            {str(r.get("name")) for r in object_type_rows if r.get("name")}
        )
        if not object_types:
            object_types = ["product", "category"]

        groups_by_id = {
            str(g.get("id")): (
                resolve_value(g.get("label"), locale) or str(g.get("name")) or str(g.get("id"))
            )
            for g in groups
        }

        # Business rules drive the `computed_rule` select: its value is a rule **name**
        # and the inherited-computed cascade looks the rule up by exactly that name,
        # so the dialog offers the registry instead of free text (a typed name can
        # point nowhere). Computed + active rules come first, they are the usual pick.
        try:
            rules = await client.list_rules(token, limit=1000)
        except CoreError, httpx.HTTPError:  # degrade to an empty picker
            rules = []
        rule_options: dict[str, str] = {
            str(r.get("name")): " · ".join(
                part
                for part in (
                    str(r.get("name") or ""),
                    str(r.get("type") or ""),
                    str(r.get("state") or ""),
                )
                if part
            )
            for r in sorted(
                rules,
                key=lambda r: (
                    0 if (r.get("type") == "computed" and r.get("state") == "active") else 1,
                    str(r.get("name") or ""),
                ),
            )
            if r.get("name")
        }

        # --- Filter state & predicate (name / group / type / standard) ------
        #: Standard vocabulary stays out of the working list until the switch is on.
        show_state: dict[str, bool] = {"reference": False}
        filters: dict[str, str] = {"name": "", "group": "", "type": ""}

        def _matches(d: dict) -> bool:
            if hides_standard(d, show_reference=show_state["reference"]):
                return False
            if filters["name"]:
                q = filters["name"].lower()
                hay = f"{d.get('name', '')} {resolve_value(d.get('label'), locale)}".lower()
                if q not in hay:
                    return False
            if filters["type"] and str(d.get("type")) != filters["type"]:
                return False
            return not (filters["group"] and filters["group"] not in (d.get("group_ids") or []))

        def _rows() -> list[dict]:
            rows: list[dict] = []
            for d in definitions:
                if not _matches(d):
                    continue
                rows.append(
                    {
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "label": resolve_value(d.get("label"), locale),
                        "type": d.get("type"),
                        "group": ", ".join(
                            groups_by_id.get(str(gid), "") for gid in (d.get("group_ids") or [])
                        ),
                        "key": "✔" if d.get("is_key") else "–",
                        "inheritable": "✔" if d.get("inheritable") else "–",
                        "multilingual": "✔" if d.get("multilingual") else "–",
                        "applicable": ", ".join(d.get("applicable_types") or []),
                        "required": ", ".join(d.get("required_types") or []),
                        "reference": (
                            t("attr_def.reference_badge") if d.get("reference_data") else "–"
                        ),
                    }
                )
            return rows

        # Filter bar (free-text name / group / type).
        with ui.row().classes("w-full items-center gap-2 mt-2"):
            (
                ui.input(t("attr_def.filter_name"))
                .props("filled dense")
                .classes("w-48")
                .on_value_change(lambda e: _set_filter("name", str(e.value)))
            )
            filter_select(
                {str(gid): lbl for gid, lbl in groups_by_id.items()},
                label=t("attr_def.filter_group"),
                classes="w-48",
                on_change=lambda value: _set_filter("group", str(value)),
            )
            filter_select(
                {typ: typ for typ in ATTR_TYPES},
                label=t("attr_def.filter_type"),
                classes="w-40",
                on_change=lambda value: _set_filter("type", str(value)),
            )
            # The standard's vocabulary is hidden by default: it belongs to the
            # catalogue but not into the list an admin works with (the same rule as
            # the product form, the export wizard and the search form).
            ui.switch(
                t("attr_def.show_reference"),
                value=False,
                on_change=lambda e: _set_show_reference(bool(e.value)),
            ).props("dense").classes("ml-2")
        #: "N standard attributes hidden": one label for the grid, **not** the
        #: editor dialog's `reference_hint` further down — both would live in this
        #: one function scope (`tests/test_frontend_page_scopes.py`).
        hidden_hint = ui.label().classes("text-caption text-gray-500")

        state: dict = {"definitions": definitions}

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("attr_def.col_name"), "field": "name", "width": 160},
                    {"headerName": t("attr_def.col_label"), "field": "label", "width": 120},
                    {"headerName": t("attr_def.col_type"), "field": "type", "width": 100},
                    {
                        "headerName": t("attr_def.col_reference"),
                        "field": "reference",
                        "width": 100,
                    },
                    {"headerName": t("attr_def.col_group"), "field": "group", "width": 120},
                    {"headerName": t("attr_def.col_key"), "field": "key", "width": 70},
                    {
                        "headerName": t("attr_def.col_inheritable"),
                        "field": "inheritable",
                        "width": 110,
                    },
                    {
                        "headerName": t("attr_def.col_multilingual"),
                        "field": "multilingual",
                        "width": 110,
                    },
                    {
                        "headerName": t("attr_def.col_applicable"),
                        "field": "applicable",
                        "width": 160,
                    },
                    {"headerName": t("attr_def.col_required"), "field": "required", "width": 160},
                ],
                "rowData": _rows(),
            }
        ).classes("w-full grow")

        def _apply_filters() -> None:
            grid.run_grid_method("setGridOption", "rowData", _rows())
            hidden = 0 if show_state["reference"] else standard_count(definitions)
            hidden_hint.set_text(
                t("attr_def.reference_hidden").format(count=hidden) if hidden else ""
            )
            hidden_hint.set_visibility(hidden > 0)

        def _set_filter(key: str, value: str) -> None:
            filters[key] = value
            _apply_filters()

        def _set_show_reference(show: bool) -> None:
            show_state["reference"] = show
            _apply_filters()

        # Initial render: the grid has its rows already, but the hidden-count hint
        # has to be filled from the loaded catalog.
        _apply_filters()

        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("attr_def.create"), icon="add", on_click=lambda: open_editor(None)).props(
                "flat"
            )

        # --- Group management ------------------------------------------------
        with (
            ui.expansion(t("attr_def.groups_title"), icon="folder")
            .classes("w-full mt-4")
            .props("header-class=bg-green-2 text-green-9")
        ):

            def _group_rows() -> list[dict]:
                return [
                    {
                        "id": str(g.get("id")),
                        "name": str(g.get("name") or ""),
                        "label": resolve_value(g.get("label"), locale),
                        "position": int(g.get("position") or 0),
                        "applicable": ", ".join(g.get("applicable_types") or []),
                    }
                    for g in groups
                ]

            group_col_defs = [
                {"headerName": t("attr_def.groups_name"), "field": "name", "width": 160},
                {"headerName": t("attr_def.groups_label"), "field": "label", "width": 140},
                {"headerName": t("attr_def.groups_position"), "field": "position", "width": 90},
                {"headerName": t("attr_def.col_applicable"), "field": "applicable", "width": 240},
            ]
            #: Persisted per-user column visibility for the groups grid — the same
            #: "saved header" pattern as the product grid's views (`grid.py`).
            group_visible: set[str] = set(
                app.storage.user.get("attr_def_group_columns")
                or ["name", "label", "position", "applicable"]
            )
            for col in group_col_defs:
                col["hide"] = col["field"] not in group_visible

            # Column picker (dialog, not `ui.menu`) so it opens reliably in all
            # browsers — mirrors the product grid's column picker. Defined before
            # the buttons below that open it (the checkbox handlers call the
            # `_toggle_group_column` function defined later in this scope).
            group_columns_dialog = ui.dialog()
            with group_columns_dialog, ui.card().classes("w-[22rem] max-w-[92vw]"):
                ui.label(t("grid.columns")).classes("text-h6")
                with ui.column().classes("w-full gap-1 max-h-80 overflow-y-auto"):
                    for col in group_col_defs:
                        ui.checkbox(
                            str(col["headerName"]), value=col["field"] in group_visible
                        ).on_value_change(
                            lambda e, field=col["field"]: _toggle_group_column(field, e.value)
                        )
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button(t("grid.close"), on_click=group_columns_dialog.close).props("flat")

            group_grid = ui.aggrid(
                {
                    "columnDefs": group_col_defs,
                    "rowData": _group_rows(),
                    "rowSelection": {"mode": "multiRow"},
                }
            ).classes("w-full")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(
                    t("attr_def.groups_create"),
                    icon="add",
                    on_click=lambda: open_group_editor(None),
                ).props("flat")
                ui.button(
                    t("attr_def.groups_edit"),
                    icon="edit",
                    on_click=lambda: edit_selected_group(),
                ).props("flat")
                ui.button(
                    t("attr_def.groups_delete"), icon="delete", on_click=lambda: delete_group()
                ).props("flat color=negative")
                ui.button(icon="view_column").props("flat outline").tooltip(
                    t("grid.columns")
                ).on_click(group_columns_dialog.open)

            group_dialog = ui.dialog()
            with group_dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
                group_title = ui.label("").classes("text-h6")
                with ui.column().classes("w-full mt-2 gap-2"):
                    with ui.row().classes("w-full gap-2"):
                        group_name = (
                            ui.input(t("attr_def.groups_name"))
                            .props("filled dense")
                            .classes("grow")
                        )
                        group_position = (
                            ui.number(t("attr_def.groups_position"), value=0)
                            .props("filled dense")
                            .classes("w-32")
                        )
                    group_store: dict[str, Any] = {"label": {}}
                    group_label_field = multilingual_value_editor(
                        field_label=t("attr_def.groups_label"),
                        locale=locale,
                        languages=languages,
                        value=None,
                        store=group_store,
                        key="label",
                    )
                    group_applicable = (
                        ui.select(
                            {typ: typ for typ in object_types},
                            label=t("attr_def.field_applicable"),
                            value=[],
                            multiple=True,
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=group_dialog.close).props("flat")
                    ui.button(t("attr_def.save"), icon="save", on_click=lambda: save_group()).props(
                        "flat"
                    )

            def open_group_editor(group: dict | None) -> None:
                if isinstance(group, str):
                    group = next((g for g in groups if str(g.get("id")) == group), None)
                editing = group is not None
                group = group or {}  # the reads below only run while editing
                group_title.set_text(
                    t("attr_def.groups_edit") if editing else t("attr_def.groups_create")
                )
                group_name.set_value(str(group.get("name") or "") if editing else "")
                group_position.set_value(int(group.get("position") or 0) if editing else 0)
                group_store["label"] = dict((group.get("label") if editing else {}) or {})
                group_label_field.set_value(str(group_store["label"].get(base_lang(locale)) or ""))
                group_applicable.set_value(
                    list(group.get("applicable_types") or []) if editing else []
                )
                save_group.editing_id = group.get("id") if editing else None  # type: ignore[attr-defined]
                group_dialog.open()

            async def save_group() -> None:
                editing_id = getattr(save_group, "editing_id", None)
                glabel = dict(group_store["label"] or {})
                name = group_name.value or ""
                if not name:
                    ui.notify(t("attr_def.groups_name_required"), color="warning")
                    return
                try:
                    if editing_id:
                        await client.update_attribute_group(
                            token,
                            editing_id,
                            name=name,
                            label=glabel,
                            position=int(group_position.value or 0),
                            applicable_types=list(group_applicable.value or []),
                        )
                    else:
                        await client.create_attribute_group(
                            token,
                            name=name,
                            label=glabel,
                            position=int(group_position.value or 0),
                            applicable_types=list(group_applicable.value or []),
                        )
                except (CoreError, httpx.HTTPError) as exc:  # show a friendly error
                    ui.notify(f"{t('attr_def.save_failed')}: {exc}", color="negative")
                    return
                group_dialog.close()
                await reload_groups()
                ui.notify(t("attr_def.saved"), color="positive")

            async def delete_group() -> None:
                try:
                    selected = await group_grid.get_selected_rows()
                except Exception:  # noqa: BLE001 - degrade to no selection
                    selected = []
                if not selected:
                    ui.notify(t("attr_def.groups_none_selected"), color="warning")
                    return
                for row in selected:
                    oid = row.get("id") if isinstance(row, dict) else None
                    if oid:
                        try:
                            await client.delete_attribute_group(token, oid)
                        except CoreError as error:
                            ui.notify(f"{t('attr_def.delete_failed')}: {error}", color="negative")
                            return
                await reload_groups()
                ui.notify(t("attr_def.deleted"), color="positive")

            async def reload_groups() -> None:
                nonlocal groups, groups_by_id
                try:
                    groups = await client.list_attribute_groups(token)
                except CoreError, httpx.HTTPError:  # keep the cached list on failure
                    return
                groups_by_id = {
                    str(g.get("id")): (
                        resolve_value(g.get("label"), locale)
                        or str(g.get("name"))
                        or str(g.get("id"))
                    )
                    for g in groups
                    if isinstance(g, dict)
                }
                group_grid.run_grid_method("setGridOption", "rowData", _group_rows())
                grid.run_grid_method("setGridOption", "rowData", _rows())

            def _group_for(row: dict | None) -> dict | None:
                """Resolves a rendered row (or its id) to the full group record.

                The grid rows carry resolved display strings (label, comma-joined
                `applicable`), but the editor needs the raw `label` dict and
                `applicable_types` list — so look the group up by id.
                """
                if not isinstance(row, dict):
                    return None
                gid = str(row.get("id") or "")
                return next((g for g in groups if str(g.get("id")) == gid), None)

            def _toggle_group_column(field: str, visible: bool) -> None:
                if visible:
                    group_visible.add(field)
                else:
                    group_visible.discard(field)
                app.storage.user["attr_def_group_columns"] = sorted(group_visible)
                group_grid.run_grid_method("setColumnsVisible", [field], visible)

            async def edit_selected_group() -> None:
                try:
                    selected = await group_grid.get_selected_rows()
                except Exception:  # noqa: BLE001 - degrade to no selection
                    selected = []
                if not selected:
                    ui.notify(t("attr_def.groups_none_selected"), color="warning")
                    return
                open_group_editor(_group_for(selected[0]))

            group_grid.on(
                "cellDoubleClicked",
                lambda e: open_group_editor(_group_for(e.args.get("data"))),
                ["data"],
            )

        # --- editor dialog --------------------------------------------------
        form_dialog = ui.dialog()
        with form_dialog, ui.card().classes("w-[42rem] max-w-[95vw] max-h-[85vh] overflow-y-auto"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full gap-2"):
                    name_input = (
                        ui.input(t("attr_def.field_name")).props("filled dense").classes("grow")
                    )
                    type_select = (
                        ui.select(
                            {typ: typ for typ in ATTR_TYPES},
                            label=t("attr_def.field_type"),
                            value="text",
                        )
                        .props("filled dense")
                        .classes("w-40")
                        .tooltip(t("attr_def.type_change_hint"))
                    )
                def_store: dict[str, Any] = {"label": {}}
                label_field = multilingual_value_editor(
                    field_label=t("attr_def.col_label"),
                    locale=locale,
                    languages=languages,
                    value=None,
                    store=def_store,
                    key="label",
                )
                group_select = (
                    ui.select(
                        {gid: lbl for gid, lbl in groups_by_id.items()},
                        label=t("attr_def.field_group"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                with ui.row().classes("w-full gap-6"):
                    inheritable_check = ui.checkbox(t("attr_def.field_inheritable"))
                    is_key_check = ui.checkbox(t("attr_def.field_key"))
                    multilingual_check = ui.checkbox(t("attr_def.field_multilingual"))
                    computed_check = ui.checkbox(t("attr_def.field_computed"))
                # Names the locked switch and why (`attribute_form.field_state`).
                flag_hint = ui.label().classes("text-caption text-orange-600")
                # --- Computed value: rule, inputs, inherited reads -------------
                # Only meaningful with `computed` (`attribute_form.cleaned_payload`
                # clears the three fields when the flag is off).
                with ui.column().classes("w-full gap-1") as computed_box:
                    computed_rule_select = (
                        ui.select(
                            rule_options,
                            label=t("attr_def.field_computed_rule"),
                            value=None,
                            clearable=True,
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    ui.label(t("attr_def.computed_rule_hint")).classes("text-caption text-gray-500")
                    dependencies_select = (
                        ui.select(
                            [],
                            label=t("attr_def.field_dependencies"),
                            value=[],
                            multiple=True,
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    ui.label(t("attr_def.dependencies_hint")).classes("text-caption text-gray-500")
                    computed_inherited_check = ui.checkbox(t("attr_def.field_computed_inherited"))
                applicable_select = (
                    ui.select(
                        {typ: typ for typ in object_types},
                        label=t("attr_def.field_applicable"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                required_select = (
                    ui.select(
                        {typ: typ for typ in object_types},
                        label=t("attr_def.field_required"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                required_hint = ui.label().classes("text-caption text-gray-500")
                # --- Units (numeric types only) ---
                with ui.row().classes("w-full gap-2") as units_row:
                    unit_input = (
                        ui.select(
                            unit_options,
                            label=t("attr_def.field_unit"),
                            value=None,
                        )
                        .props("filled dense")
                        .classes("grow")
                    )
                    display_input = (
                        ui.select(
                            unit_options,
                            label=t("attr_def.field_display_unit"),
                            value=None,
                        )
                        .props("filled dense")
                        .classes("grow")
                    )
                # --- Enum set selector (Phase 9, Reference Data) ---
                with ui.column().classes("w-full gap-1") as enum_box:
                    enum_set_select = (
                        ui.select(
                            enum_set_options,
                            label=t("attr_def.field_enum_set"),
                            value=None,
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    ui.button(
                        t("attr_def.manage_enum_sets"),
                        icon="list",
                        on_click=lambda: ui.navigate.to("/enum-sets"),
                    ).props("flat dense").classes("w-full")

                description_area = (
                    ui.textarea(t("attr_def.field_description"))
                    .props("filled dense")
                    .classes("w-full")
                )
                # --- Standard binding (classification standards, Phase 9) ---
                # Binds this attribute to a standard's feature code, so a
                # class feature is filled from *this* attribute instead of one
                # named after the code (`plan/detail/bmecat-export.md` §11.7).
                with ui.row().classes("w-full gap-2"):
                    standard_select = (
                        ui.select(STANDARDS, label=t("attr_def.field_standard"), value="etim")
                        .props("filled dense")
                        .classes("w-32")
                    )
                    standard_code_input = (
                        ui.input(t("attr_def.field_standard_code"))
                        .props("filled dense")
                        .classes("grow")
                    )
                ui.label(t("attr_def.standard_code_hint")).classes("text-caption text-gray-500")
                # Reference data of an imported standard: hidden wherever
                # attributes are chosen until it is adopted into the own set
                # (`meadow/frontend/reference_data.py`). Only offered while
                # editing — a standard import creates reference data, so the
                # switch has no meaning on a new attribute.
                reference_check = ui.checkbox(t("attr_def.field_reference"))
                reference_hint = ui.label(t("attr_def.field_reference_hint")).classes(
                    "text-caption text-gray-500"
                )
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("attr_def.delete"), icon="delete", on_click=lambda: delete_definition()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=form_dialog.close).props("flat")
                    ui.button(t("attr_def.save"), icon="save", on_click=lambda: save()).props(
                        "flat"
                    )

        def apply_field_state() -> None:
            """Shows only the fields the current choices make meaningful.

            The decision itself lives in `attribute_form.field_state` (pure,
            tested); this renders it. A switch the current data type or flag
            forbids is **reset** rather than hidden, so a save cannot carry a
            stale choice, and a hint names the reason.
            """
            state = field_state(
                str(type_select.value or "text"),
                is_key=bool(is_key_check.value),
                multilingual=bool(multilingual_check.value),
                computed=bool(computed_check.value),
                editing=bool(getattr(save, "editing_id", None)),
            )
            computed_box.set_visibility(state.computed)
            units_row.set_visibility(state.units)
            enum_box.set_visibility(state.enum_set)
            reference_check.set_visibility(state.reference)
            reference_hint.set_visibility(state.reference)
            is_key_check.set_enabled(state.key)
            if not state.key:
                is_key_check.set_value(False)
            inheritable_check.set_enabled(state.inheritable)
            if not state.inheritable:
                inheritable_check.set_value(False)
            reason = state.key_reason or state.inheritable_reason
            flag_hint.set_text(t(reason) if reason else "")
            flag_hint.set_visibility(bool(reason))
            # Required types are limited to the applicable ones (an empty
            # `applicable_types` means "every type", so all stay allowed).
            chosen = list(applicable_select.value or [])
            allowed = chosen or object_types
            kept = [typ for typ in (required_select.value or []) if typ in set(allowed)]
            required_select.set_options({typ: typ for typ in allowed}, value=kept)
            required_hint.set_text(t("attr_def.required_follows_applicable") if chosen else "")
            required_hint.set_visibility(bool(chosen))

        # Any decision can turn another field meaningless — re-evaluate on change.
        for element in (
            type_select,
            is_key_check,
            multilingual_check,
            computed_check,
            applicable_select,
        ):
            element.on_value_change(lambda _: apply_field_state())

        def _fill_form(definition: dict | None) -> None:
            editing = definition is not None
            definition = definition or {}  # the reads below only run while editing
            title_label.set_text(t("attr_def.edit") if editing else t("attr_def.create"))
            delete_btn.visible = editing
            name_input.set_value(definition["name"] if editing else "")
            type_select.set_value(definition["type"] if editing else "text")
            # The type stays selectable while editing: a change runs through
            # `confirm_type_change` (report + migration), never through the PATCH.
            type_select.enable()
            label = (definition["label"] if editing else {}) or {}
            def_store["label"] = dict(label)
            label_field.set_value(str(def_store["label"].get(base_lang(locale)) or ""))
            group_select.set_value(list(definition.get("group_ids") or []) if editing else [])
            inheritable_check.set_value(bool(definition.get("inheritable")) if editing else False)
            is_key_check.set_value(bool(definition.get("is_key")) if editing else False)
            multilingual_check.set_value(bool(definition.get("multilingual")) if editing else False)
            computed_check.set_value(bool(definition.get("computed")) if editing else False)
            applicable_select.set_value(
                list(definition.get("applicable_types") or []) if editing else []
            )
            required_select.set_value(
                list(definition.get("required_types") or []) if editing else []
            )
            unit_input.set_value(_resolve_unit_key(definition.get("unit_id")) if editing else None)
            display_input.set_value(
                _resolve_unit_key(definition.get("display_unit_id")) if editing else None
            )
            enum_set_value = definition.get("enum_set_id") if editing else None
            enum_set_select.set_value(
                str(enum_set_value) if enum_set_value in enum_set_options else None
            )
            # The binding shows one standard at a time; the others stay on the
            # record (the save merges this field into the loaded map).
            codes = dict(definition.get("standard_codes") or {}) if editing else {}
            first_standard = next((key for key in codes if key in STANDARDS), "etim")
            standard_select.set_value(first_standard)
            standard_code_input.set_value(str(codes.get(first_standard) or ""))
            reference_check.set_value(bool(definition.get("reference_data")) if editing else False)
            description_area.set_value(str(definition.get("description") or "") if editing else "")
            # Dependencies are attribute names — a multi-select over the whole
            # catalog (a stored name that no longer exists stays selectable).
            own_name = str(definition.get("name") or "") if editing else ""
            dependency_values = (
                [str(name) for name in (definition.get("dependencies") or [])] if editing else []
            )
            dependency_choices = [
                str(row.get("name"))
                for row in definitions
                if row.get("name") and str(row.get("name")) != own_name
            ]
            for name in dependency_values:
                if name not in dependency_choices:
                    dependency_choices.append(name)
            dependencies_select.set_options(
                {name: name for name in dependency_choices}, value=dependency_values
            )
            # The stored rule name stays selectable when the rule was renamed or
            # removed, so opening a definition never drops the value silently.
            rule_value = str(definition.get("computed_rule") or "") if editing else ""
            rule_picks = dict(rule_options)
            if rule_value and rule_value not in rule_picks:
                rule_picks[rule_value] = t("attr_def.computed_rule_missing").format(name=rule_value)
            computed_rule_select.set_options(rule_picks, value=rule_value or None)
            computed_inherited_check.set_value(
                bool(definition.get("computed_reads_inherited")) if editing else False
            )
            # Last: hide/lock what the loaded choices make meaningless.
            apply_field_state()

        async def reload() -> None:
            nonlocal definitions
            try:
                # Same projection as the initial load: the list endpoint is capped
                # at 1000 rows and would silently drop late-sorted attributes.
                definitions = await client.list_attribute_vocabulary(token)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                definitions = state["definitions"]
            state["definitions"] = definitions
            _apply_filters()

        async def apply_type_change(
            definition: dict, target: str, fields: dict, *, drop: bool
        ) -> None:
            """Migrates the values to the new type, then applies the other edits."""
            try:
                report = await client.change_attribute_type(
                    token,
                    definition["id"],
                    definition_type=target,
                    enum_set_id=fields.get("enum_set_id") if target == "enum" else None,
                    unit_id=fields.get("unit_id") if target in NUMERIC_TYPES else None,
                    display_unit_id=(
                        fields.get("display_unit_id") if target in NUMERIC_TYPES else None
                    ),
                    drop_incompatible=drop,
                )
                await client.update_attribute_definition(token, definition["id"], **fields)
            except (CoreError, httpx.HTTPError) as exc:  # show a friendly error
                ui.notify(f"{t('attr_def.type_change_failed')}: {exc}", color="negative")
                return
            form_dialog.close()
            await reload()
            ui.notify(
                t("attr_def.type_change_done").format(
                    source=report.get("from"),
                    target=report.get("to"),
                    converted=report.get("convertible", 0),
                    dropped=report.get("dropped", 0),
                ),
                color="positive",
            )

        async def confirm_type_change(definition: dict, target: str, fields: dict) -> None:
            """Shows the conversion report (dry run) and applies it on request.

            The core converts every existing value; values that do not fit the
            new type block the change unless the user explicitly allows removing
            them — that is exactly what the dialog asks about.
            """
            try:
                report = await client.change_attribute_type(
                    token, definition["id"], definition_type=target, dry_run=True
                )
            except (CoreError, httpx.HTTPError) as exc:  # show a friendly error
                ui.notify(f"{t('attr_def.type_change_failed')}: {exc}", color="negative")
                return
            incompatible = int(report.get("incompatible") or 0)
            with ui.dialog() as dialog, ui.card().classes("w-[36rem] max-w-[94vw]"):
                ui.label(
                    t("attr_def.type_change_title").format(name=definition.get("name"))
                ).classes("text-h6")
                ui.label(
                    t("attr_def.type_change_report").format(
                        source=report.get("from"),
                        target=report.get("to"),
                        checked=report.get("checked", 0),
                        convertible=report.get("convertible", 0),
                        incompatible=incompatible,
                    )
                ).classes("text-caption text-gray-600")
                samples = report.get("samples") or []
                if samples:
                    with ui.column().classes("w-full gap-1 max-h-40 overflow-y-auto"):
                        for sample in samples:
                            ui.label(
                                f"{sample.get('object')} · {sample.get('value')} · "
                                f"{sample.get('reason')}"
                            ).classes("text-caption text-warning break-all")
                drop_switch: ui.switch | None = None
                if incompatible:
                    ui.label(t("attr_def.type_change_drop_hint")).classes(
                        "text-caption text-warning"
                    )
                    drop_switch = ui.switch(t("attr_def.type_change_drop"))
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=dialog.close).props("flat")

                    async def _apply() -> None:
                        dialog.close()
                        await apply_type_change(
                            definition,
                            target,
                            fields,
                            drop=bool(drop_switch.value) if drop_switch is not None else False,
                        )

                    ui.button(
                        t("attr_def.type_change_apply"),
                        icon="sync_alt",
                        on_click=_apply,
                    ).props("color=primary")
            dialog.open()

        async def save() -> None:
            editing_id = getattr(save, "editing_id", None)
            name = name_input.value or ""
            definition_type = type_select.value or "text"
            label = dict(def_store["label"] or {})
            group_ids = list(group_select.value or [])
            applicable = list(applicable_select.value or [])

            current = (
                next((d for d in definitions if d.get("id") == editing_id), None)
                if editing_id
                else None
            )
            # Merge into the loaded map so a second standard is not dropped, and
            # an empty input clears the binding of the shown standard. The grid row
            # is the slim projection, so the **full** record loaded by the editor
            # wins over it (otherwise the merge would drop every other binding).
            loaded = getattr(save, "loaded", None) or current or {}
            standard_codes = {
                str(key): str(value)
                for key, value in dict(loaded.get("standard_codes") or {}).items()
                if value
            }
            standard = str(standard_select.value or "etim").strip() or "etim"
            code = str(standard_code_input.value or "").strip()
            if code:
                standard_codes[standard] = code
            else:
                standard_codes.pop(standard, None)

            # The payload is heterogeneous (strings, flags, lists, the cleaned
            # extras), so it is passed as `Any` when unpacked into the core call.
            common: dict[str, Any] = {
                "label": label,
                "group_ids": group_ids,
                "applicable_types": applicable,
                "computed": bool(computed_check.value),
                "multilingual": bool(multilingual_check.value),
                "standard_codes": standard_codes,
                "reference_data": bool(reference_check.value),
                "description": description_area.value or None,
                # The fields that only make sense together with a type/flag:
                # cleared instead of sent stale (`attribute_form.cleaned_payload`).
                **cleaned_payload(
                    definition_type=definition_type,
                    is_key=bool(is_key_check.value),
                    multilingual=bool(multilingual_check.value),
                    computed=bool(computed_check.value),
                    inheritable=bool(inheritable_check.value),
                    unit_id=unit_input.value,
                    display_unit_id=display_input.value,
                    enum_set_id=enum_set_select.value,
                    computed_rule=computed_rule_select.value,
                    dependencies=dependencies_select.value or [],
                    reads_inherited=bool(computed_inherited_check.value),
                    applicable=applicable,
                    required=required_select.value or [],
                    own_name=name,
                ),
            }

            try:
                if editing_id:
                    if current and str(current.get("type") or "") != definition_type:
                        await confirm_type_change(current, definition_type, common)
                        return
                    await client.update_attribute_definition(
                        token,
                        editing_id,
                        **common,
                    )
                else:
                    await client.create_attribute_definition(
                        token,
                        name=name,
                        definition_type=definition_type,
                        **common,
                    )
            except (CoreError, httpx.HTTPError) as exc:  # show a friendly error
                ui.notify(f"{t('attr_def.save_failed')}: {exc}", color="negative")
                return
            form_dialog.close()
            await reload()
            ui.notify(t("attr_def.saved"), color="positive")

        async def do_delete(definition_id: str, *, force: bool) -> None:
            try:
                await client.delete_attribute_definition(token, definition_id, force=force)
            except CoreError as error:
                ui.notify(f"{t('attr_def.delete_failed')}: {error}", color="negative")
                return
            form_dialog.close()
            await reload()
            ui.notify(t("attr_def.deleted"), color="positive")

        async def force_confirm(definition_id: str, name: str) -> None:
            with ui.dialog() as force_dialog, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("attr_def.delete_in_use").format(name=name)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=force_dialog.close).props("flat")

                    async def _force() -> None:
                        force_dialog.close()
                        await do_delete(definition_id, force=True)

                    ui.button(t("attr_def.delete_force"), on_click=_force).props("color=negative")
            force_dialog.open()

        async def delete_definition() -> None:
            editing_id = getattr(save, "editing_id", None)
            if not editing_id:
                return
            name = name_input.value or ""
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("attr_def.delete_confirm").format(name=name)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _confirm() -> None:
                        confirm.close()
                        try:
                            await client.delete_attribute_definition(token, editing_id)
                        except CoreError as error:
                            if error.status == 409:
                                await force_confirm(editing_id, name)
                                return
                            ui.notify(f"{t('attr_def.delete_failed')}: {error}", color="negative")
                            return
                        form_dialog.close()
                        await reload()
                        ui.notify(t("attr_def.deleted"), color="positive")

                    ui.button(t("attr_def.delete"), on_click=_confirm).props("color=negative")
            confirm.open()

        async def open_editor(definition: dict | None) -> None:
            if isinstance(definition, str):
                definition = next((d for d in definitions if str(d.get("id")) == definition), None)
            if definition is None:
                editing_id = None
                full = None
                save.loaded = None  # type: ignore[attr-defined]
            else:
                editing_id = definition.get("id")
                # The grid row is the slim projection; the editor needs the whole
                # record (units, standard binding, computed fields, description) —
                # otherwise saving would write a truncated payload back.
                try:
                    full = await client.get_attribute_definition(token, str(editing_id))
                except CoreError, httpx.HTTPError:  # fall back to the loaded row
                    full = next(
                        (d for d in definitions if d.get("id") == editing_id),
                        definition,
                    )
                save.loaded = full  # type: ignore[attr-defined]
            save.editing_id = editing_id  # type: ignore[attr-defined]
            _fill_form(full)
            form_dialog.open()

        grid.on("cellDoubleClicked", lambda e: open_editor(e.args.get("data")), ["data"])

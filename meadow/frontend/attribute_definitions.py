"""Admin page for attribute definitions (admin/superadmin only).

Lists the attribute catalog (the product template per object type via
``applicable_types``/``required_types``) and allows creating and editing
definitions. Writes go through ``POST``/``PATCH /attribute-definitions``.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.grid import resolve_value
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell

#: Supported attribute data types (extensible; existing types are never renamed).
ATTR_TYPES = ("text", "number", "boolean", "date", "enum", "reference")


@ui.page("/attribute-definitions", response_timeout=15)
async def attribute_definitions_page() -> None:
    await build_shell(active="/attribute-definitions")
    locale = current_locale()
    client = CoreClient(config.CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("attr_def.title")).classes("text-h4")
        ui.label(t("attr_def.subtitle")).classes("text-subtitle1")

        try:
            definitions = await client.list_attribute_definitions(token, limit=1000)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("attr_def.load_failed")).classes("text-subtitle1 text-negative")
            return
        try:
            groups = await client.list_attribute_groups(token)
        except Exception:  # noqa: BLE001 - degrade to no groups
            groups = []
        try:
            objects = await client.list_objects(token, limit=1000, locale=locale)
        except Exception:  # noqa: BLE001 - degrade to the default types
            objects = []

        object_types: list[str] = sorted({str(o.get("type")) for o in objects if o.get("type")})
        if not object_types:
            object_types = ["product", "category"]

        groups_by_id = {
            str(g.get("id")): (
                resolve_value(g.get("label"), locale) or str(g.get("name")) or str(g.get("id"))
            )
            for g in groups
        }

        def _rows() -> list[dict]:
            rows: list[dict] = []
            for d in definitions:
                rows.append(
                    {
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "label": resolve_value(d.get("label"), locale),
                        "type": d.get("type"),
                        "group": ", ".join(
                            groups_by_id.get(str(gid), "") for gid in (d.get("group_ids") or [])
                        ),
                        "key": "✔" if d.get("is_key") else "",
                        "inheritable": "✔" if d.get("inheritable") else "",
                        "multilingual": "✔" if d.get("multilingual") else "",
                        "applicable": ", ".join(d.get("applicable_types") or []),
                        "required": ", ".join(d.get("required_types") or []),
                    }
                )
            return rows

        state: dict = {"definitions": definitions}

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("attr_def.col_name"), "field": "name", "width": 160},
                    {"headerName": t("attr_def.col_label"), "field": "label", "width": 120},
                    {"headerName": t("attr_def.col_type"), "field": "type", "width": 100},
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
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(t("attr_def.create"), icon="add", on_click=lambda: open_editor(None)).props(
                "flat"
            )

        # --- Group management ------------------------------------------------
        with ui.expansion(t("attr_def.groups_title"), icon="folder").classes("w-full mt-4"):

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
                            col["headerName"], value=col["field"] in group_visible
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
                    with ui.row().classes("w-full gap-2"):
                        group_label_en = (
                            ui.input("Label (EN)").props("filled dense").classes("grow")
                        )
                        group_label_de = (
                            ui.input("Label (DE)").props("filled dense").classes("grow")
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
                group_title.set_text(
                    t("attr_def.groups_edit") if editing else t("attr_def.groups_create")
                )
                group_name.set_value(str(group.get("name") or "") if editing else "")
                group_position.set_value(int(group.get("position") or 0) if editing else 0)
                glabel = (group.get("label") if editing else {}) or {}
                group_label_en.set_value(str(glabel.get("en") or ""))
                group_label_de.set_value(str(glabel.get("de") or ""))
                group_applicable.set_value(
                    list(group.get("applicable_types") or []) if editing else []
                )
                save_group.editing_id = group.get("id") if editing else None  # type: ignore[attr-defined]
                group_dialog.open()

            async def save_group() -> None:
                editing_id = getattr(save_group, "editing_id", None)
                glabel = {}
                if group_label_en.value:
                    glabel["en"] = group_label_en.value
                if group_label_de.value:
                    glabel["de"] = group_label_de.value
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
                except Exception as exc:  # noqa: BLE001 - show a friendly error
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
                except Exception:  # noqa: BLE001 - keep the cached list on failure
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
            fields: dict[str, ui.element] = {}
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
                    )
                with ui.row().classes("w-full gap-2"):
                    label_en = ui.input("Label (EN)").props("filled dense").classes("grow")
                    label_de = ui.input("Label (DE)").props("filled dense").classes("grow")
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
                with ui.row().classes("w-full gap-2"):
                    unit_input = (
                        ui.input(t("attr_def.field_unit")).props("filled dense").classes("grow")
                    )
                    display_input = (
                        ui.input(t("attr_def.field_display_unit"))
                        .props("filled dense")
                        .classes("grow")
                    )
                enum_input = (
                    ui.input(t("attr_def.field_enum_values"))
                    .props("filled dense")
                    .classes("w-full")
                )
                description_area = (
                    ui.textarea(t("attr_def.field_description"))
                    .props("filled dense")
                    .classes("w-full")
                )
                computed_rule_input = (
                    ui.input(t("attr_def.field_computed_rule"))
                    .props("filled dense")
                    .classes("w-full")
                )
                dependencies_input = (
                    ui.input(t("attr_def.field_dependencies"))
                    .props("filled dense")
                    .classes("w-full")
                )
                computed_inherited_check = ui.checkbox(t("attr_def.field_computed_inherited"))
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("attr_def.delete"), icon="delete", on_click=lambda: delete_definition()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=form_dialog.close).props("flat")
                    ui.button(t("attr_def.save"), icon="save", on_click=lambda: save()).props(
                        "flat"
                    )

        fields.update(
            {
                "name": name_input,
                "type": type_select,
                "label_en": label_en,
                "label_de": label_de,
                "group": group_select,
                "inheritable": inheritable_check,
                "key": is_key_check,
                "multilingual": multilingual_check,
                "computed": computed_check,
                "applicable": applicable_select,
                "required": required_select,
                "unit": unit_input,
                "display_unit": display_input,
                "enum": enum_input,
                "description": description_area,
                "computed_rule": computed_rule_input,
                "dependencies": dependencies_input,
                "computed_inherited": computed_inherited_check,
            }
        )

        def _split_csv(value: str) -> list[str]:
            return [item.strip() for item in value.split(",") if item.strip()]

        def _fill_form(definition: dict | None) -> None:
            editing = definition is not None
            title_label.set_text(t("attr_def.edit") if editing else t("attr_def.create"))
            delete_btn.visible = editing
            name_input.set_value(definition["name"] if editing else "")
            type_select.set_value(definition["type"] if editing else "text")
            if editing:
                type_select.disable()
            else:
                type_select.enable()
            label = (definition["label"] if editing else {}) or {}
            label_en.set_value(str(label.get("en") or ""))
            label_de.set_value(str(label.get("de") or ""))
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
            unit_input.set_value(str(definition.get("unit_id") or "") if editing else "")
            display_input.set_value(str(definition.get("display_unit_id") or "") if editing else "")
            enum_input.set_value(
                ", ".join((definition.get("constraints") or {}).get("enum_values") or [])
                if editing
                else ""
            )
            description_area.set_value(str(definition.get("description") or "") if editing else "")
            computed_rule_input.set_value(
                str(definition.get("computed_rule") or "") if editing else ""
            )
            dependencies_input.set_value(
                ", ".join(definition.get("dependencies") or []) if editing else ""
            )
            computed_inherited_check.set_value(
                bool(definition.get("computed_reads_inherited")) if editing else False
            )

        async def reload() -> None:
            nonlocal definitions
            try:
                definitions = await client.list_attribute_definitions(token, limit=1000)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                definitions = state["definitions"]
            state["definitions"] = definitions
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def save() -> None:
            editing_id = getattr(save, "editing_id", None)
            name = name_input.value or ""
            definition_type = type_select.value or "text"
            label = {}
            if label_en.value:
                label["en"] = label_en.value
            if label_de.value:
                label["de"] = label_de.value
            group_ids = list(group_select.value or [])
            applicable = list(applicable_select.value or [])
            required = list(required_select.value or [])
            inherited = bool(computed_inherited_check.value)

            constraints: dict | None = None
            if type_select.value == "enum":
                enums = _split_csv(enum_input.value or "")
                constraints = {"enum_values": enums} if enums else None

            common = {
                "label": label,
                "group_ids": group_ids,
                "inheritable": bool(inheritable_check.value),
                "is_key": bool(is_key_check.value),
                "applicable_types": applicable,
                "required_types": required,
                "computed": bool(computed_check.value),
                "computed_rule": computed_rule_input.value or None,
                "dependencies": _split_csv(dependencies_input.value or "") or None,
                "computed_reads_inherited": inherited,
                "multilingual": bool(multilingual_check.value),
                "unit_id": unit_input.value or None,
                "display_unit_id": display_input.value or None,
                "constraints": constraints,
                "description": description_area.value or None,
            }

            try:
                if editing_id:
                    await client.update_attribute_definition(token, editing_id, **common)
                else:
                    await client.create_attribute_definition(
                        token,
                        name=name,
                        definition_type=definition_type,
                        **common,
                    )
            except Exception as exc:  # noqa: BLE001 - show a friendly error
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

        def open_editor(definition: dict | None) -> None:
            if isinstance(definition, str):
                definition = next((d for d in definitions if str(d.get("id")) == definition), None)
            if definition is None:
                editing_id = None
                full = None
            else:
                oid = definition.get("id")
                editing_id = oid
                full = next(
                    (d for d in definitions if d.get("id") == oid),
                    definition,
                )
            save.editing_id = editing_id  # type: ignore[attr-defined]
            _fill_form(full)
            form_dialog.open()

        grid.on("cellDoubleClicked", lambda e: open_editor(e.args.get("data")), ["data"])

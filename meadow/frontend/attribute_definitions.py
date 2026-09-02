"""Admin page for attribute definitions (admin/superadmin only).

Lists the attribute catalog (the product template per object type via
``applicable_types``/``required_types``) and allows creating and editing
definitions. Writes go through ``POST``/``PATCH /attribute-definitions``.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient
from meadow.frontend.grid import resolve_value
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell

#: Supported attribute data types (extensible; existing types are never renamed).
ATTR_TYPES = ("text", "number", "boolean", "date", "enum", "reference")


@ui.page("/attribute-definitions", response_timeout=15)
async def attribute_definitions_page() -> None:
    build_shell(active="/attribute-definitions")
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
                        "group": groups_by_id.get(str(d.get("group_id") or ""), ""),
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
                        {
                            "": t("attr_def.no_group"),
                            **{gid: lbl for gid, lbl in groups_by_id.items()},
                        },
                        label=t("attr_def.field_group"),
                        value="",
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
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=form_dialog.close).props("flat")
                ui.button(t("attr_def.save"), icon="save", on_click=lambda: save()).props("flat")

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
            name_input.set_value(definition["name"] if editing else "")
            type_select.set_value(definition["type"] if editing else "text")
            if editing:
                type_select.disable()
            else:
                type_select.enable()
            label = (definition["label"] if editing else {}) or {}
            label_en.set_value(str(label.get("en") or ""))
            label_de.set_value(str(label.get("de") or ""))
            group_select.set_value(str(definition.get("group_id") or "") if editing else "")
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
            group_id = group_select.value or None
            applicable = list(applicable_select.value or [])
            required = list(required_select.value or [])
            inherited = bool(computed_inherited_check.value)

            constraints: dict | None = None
            if type_select.value == "enum":
                enums = _split_csv(enum_input.value or "")
                constraints = {"enum_values": enums} if enums else None

            common = {
                "label": label,
                "group_id": group_id,
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

        def open_editor(definition: dict | None) -> None:
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

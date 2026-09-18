"""Admin page for the object-type registry (admin/superadmin only).

Object types are the managed business types of a catalogue — a **flat** registry
(no type inheritance), so every object belongs to exactly one type.
Each type may declare `key_attributes` (the unique + mandatory business-key
attribute names for that type; empty = no key) and an optional `label_attribute`
(the attribute used as the display name). Attributes associate with types via
their own `applicable_types`/`required_types` (managed on `/attribute-definitions`).

- **List** (``GET /object-types``), **create/edit** (``POST``/``PATCH
  /object-types``) in a dialog, **delete** (``DELETE /object-types/{name}``,
  rejected with 409 when objects still use the type).
"""

from __future__ import annotations

import contextlib

import httpx
from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.display import declared_key_names, text_attribute_options
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.multilang import base_lang, multilingual_value_editor


def _label(value: object, locale: str) -> str:
    """Resolves a multilingual label map to a display string."""
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("en") or value.get("de") or "")
    return str(value or "")


def _keys_of(row: dict) -> str:
    keys = row.get("key_attributes") or []
    return ", ".join(str(v) for v in keys) if keys else "—"


def _label_attr_of(row: dict) -> str:
    return str(row.get("label_attribute") or "") or "—"


def _system_badge(system: bool) -> str:
    if system:
        return (
            '<span class="px-2 py-0.5 rounded text-xs bg-neutral text-gray-600">'
            + t("object_types.system_default")
            + "</span>"
        )
    return (
        '<span class="px-2 py-0.5 rounded text-xs bg-warning text-white">'
        + t("object_types.system_custom")
        + "</span>"
    )


def _taxonomy_badge(taxonomy: bool) -> str:
    """Badge for the taxonomy node types (categories/classification nodes)."""
    if taxonomy:
        return (
            '<span class="px-2 py-0.5 rounded text-xs bg-primary text-white">'
            + t("object_types.taxonomy_yes")
            + "</span>"
        )
    return "—"


def _standard_badge(standard: dict | None) -> str:
    """Badge for the object type that holds a registered standard's classes."""
    if not standard:
        return "—"
    return (
        '<span class="px-2 py-0.5 rounded text-xs bg-primary text-white">'
        + str(standard.get("feature_system_name") or standard.get("name") or "")
        + "</span>"
    )


@ui.page("/object-types", response_timeout=PAGE_TIMEOUT)
async def object_types_page() -> None:
    await build_shell(active="/object-types")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")
    locale = current_locale()

    try:
        types = await client.list_object_types(token, limit=1000)
    except CoreError, httpx.HTTPError:  # show a friendly message on failure
        ui.label(t("object_types.load_failed")).classes("text-subtitle1 text-negative")
        return

    try:
        languages = await client.list_languages(token)
    except CoreError, httpx.HTTPError:  # degrade to the UI language only
        languages = []

    # Eligible attributes for `key_attributes`/`label_attribute` (own, text, not
    # multilingual). The **whole** vocabulary, not the capped paged list — with a
    # standard installed its first page holds nothing but `EF…` features, so a newly
    # created own text attribute could not be selected. Attribute definitions may be
    # unavailable; degrade gracefully.
    try:
        definitions = await client.list_attribute_vocabulary(token)
    except CoreError, httpx.HTTPError:  # degrade to no attribute names
        definitions = []
    text_attrs = text_attribute_options(definitions, declared_key_names(types))

    # Registered classification standards (ETIM, ECLASS…): they explain which
    # vocabulary is reference data and which object type holds the class nodes.
    # A missing endpoint (older core) degrades to an empty list.
    try:
        standards = await client.list_classification_standards(token, counts=True)
    except CoreError, httpx.HTTPError:  # degrade to no standards
        standards = []
    standards_by_type = {
        str(row.get("node_type")): row
        for row in standards
        if row.get("node_type") and row.get("current")
    }

    with ui.column().classes("w-full p-6"):
        ui.label(t("object_types.title")).classes("text-h4")
        ui.label(t("object_types.subtitle")).classes("text-subtitle1")

        filters: dict[str, str] = {"name": ""}

        def _matches(row: dict) -> bool:
            if filters["name"]:
                q = filters["name"].lower()
                hay = f"{row.get('name', '')} {_label(row.get('label'), locale)}".lower()
                return q in hay
            return True

        def _rows() -> list[dict]:
            return [
                {
                    "name": str(r.get("name")),
                    "label": _label(r.get("label"), locale),
                    "keys": _keys_of(r),
                    "label_attr": _label_attr_of(r),
                    "system": _system_badge(bool(r.get("system"))),
                    "taxonomy": _taxonomy_badge(bool(r.get("taxonomy"))),
                    "standard": _standard_badge(standards_by_type.get(str(r.get("name")))),
                    "updated": str(r.get("updated_at") or ""),
                }
                for r in types
                if _matches(r)
            ]

        with ui.row().classes("w-full items-center gap-2 mt-2"):
            (
                ui.input(t("object_types.filter_name"))
                .props("filled dense")
                .classes("w-56")
                .on_value_change(lambda e: _set_filter(str(e.value)))
            )

        column_defs = [
            {"headerName": t("object_types.col_name"), "field": "name", "width": 160},
            {"headerName": t("object_types.col_label"), "field": "label", "width": 200},
            {"headerName": t("object_types.col_keys"), "field": "keys", "width": 260},
            {
                "headerName": t("object_types.col_label_attr"),
                "field": "label_attr",
                "width": 160,
            },
            {"headerName": t("object_types.col_system"), "field": "system", "width": 140},
            {"headerName": t("object_types.col_taxonomy"), "field": "taxonomy", "width": 130},
            {
                "headerName": t("object_types.col_standard"),
                "field": "standard",
                "width": 190,
            },
            {"headerName": t("object_types.col_updated"), "field": "updated", "width": 200},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[
                i
                for i, col in enumerate(column_defs)
                if col["field"] in ("system", "taxonomy", "standard")
            ],
        ).classes("w-full")

        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(
                    t("object_types.new"), icon="add", on_click=lambda: open_editor(None)
                ).props("flat")
                ui.button(
                    t("object_types.edit"), icon="edit", on_click=lambda: edit_selected()
                ).props("flat")
                ui.button(
                    t("object_types.delete"), icon="delete", on_click=lambda: delete_selected()
                ).props("flat color=negative")
            ui.button(t("object_types.reload"), icon="refresh", on_click=lambda: reload()).props(
                "flat"
            )

        if standards:
            with ui.card().classes("w-full mt-4"):
                ui.label(t("object_types.standards_title")).classes("text-subtitle2")
                ui.label(t("object_types.standards_hint")).classes(
                    "text-caption text-gray-600 mt-1"
                )
                for row in sorted(
                    standards,
                    key=lambda r: (
                        str(r.get("code")),
                        not bool(r.get("current")),
                        str(r.get("version")),
                    ),
                ):
                    system = str(row.get("feature_system_name") or "")
                    with ui.row().classes("items-baseline gap-2 mt-2"):
                        ui.badge(system or str(row.get("name") or "")).props(
                            "color=primary" if row.get("current") else "outline"
                        )
                        if not row.get("current"):
                            ui.badge(t("object_types.standards_older")).props("outline")
                        details = [
                            str(row.get("name") or ""),
                            str(row.get("node_type") or ""),
                        ]
                        if row.get("class_count") is not None:
                            details.append(
                                t("object_types.standards_counts").format(
                                    classes=row.get("class_count"),
                                    features=row.get("feature_count"),
                                )
                            )
                        if row.get("source"):
                            details.append(str(row["source"]))
                        ui.label(" · ".join(part for part in details if part)).classes(
                            "text-caption text-gray-600"
                        )

        def _set_filter(value: str) -> None:
            filters["name"] = value
            grid.run_grid_method("setGridOption", "rowData", _rows())

        # --- Editor dialog ---------------------------------------------------
        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[38rem] max-w-[92vw]"):
            editor_title = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                name_input = (
                    ui.input(t("object_types.field_name")).props("filled dense").classes("w-full")
                )
                type_label_store: dict[str, dict] = {"label": {}}
                type_label_field = multilingual_value_editor(
                    field_label=t("object_types.col_label"),
                    locale=locale,
                    languages=languages,
                    value=None,
                    store=type_label_store,
                    key="label",
                )
                description_area = (
                    ui.textarea(t("object_types.field_description"))
                    .props("filled dense")
                    .classes("w-full")
                )
                key_select = (
                    ui.select(
                        {name: name for name in sorted(text_attrs)},
                        label=t("object_types.field_keys"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                label_attr_select = (
                    ui.select(
                        {name: name for name in sorted(text_attrs)},
                        label=t("object_types.field_label_attr"),
                        value=None,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                ui.label(t("object_types.key_hint")).classes("text-caption text-gray-500")
                taxonomy_switch = ui.switch(t("object_types.field_taxonomy"))
                ui.label(t("object_types.taxonomy_hint")).classes("text-caption text-gray-500")
            with (
                ui.row().classes("w-full justify-between mt-2 gap-2"),
                ui.row().classes("gap-2"),
            ):
                ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                ui.button(t("object_types.save"), icon="save", on_click=lambda: save()).props(
                    "flat"
                )

        _state: dict = {"editing": False, "name": None}

        def _fill_editor(record: dict | None) -> None:
            editing = record is not None
            record = record or {}  # the reads below only run while editing
            _state["editing"] = editing
            _state["name"] = record.get("name") if editing else None
            editor_title.set_text(t("object_types.edit") if editing else t("object_types.new"))
            name_input.set_value(str(record.get("name") or "") if editing else "")
            name_input.disable() if editing else name_input.enable()
            label = (record.get("label") if editing else {}) or {}
            type_label_store["label"] = dict(label)
            type_label_field.set_value(str(type_label_store["label"].get(base_lang(locale)) or ""))
            description_area.set_value(str(record.get("description") or "") if editing else "")
            key_select.set_value(list(record.get("key_attributes") or []) if editing else [])
            label_attr_select.set_value(str(record.get("label_attribute")) if editing else None)
            taxonomy_switch.set_value(bool(record.get("taxonomy")) if editing else False)
            # The built-in types keep the flag the seed owns.
            if editing and record.get("system"):
                taxonomy_switch.disable()
            else:
                taxonomy_switch.enable()

        def open_editor(record: dict | None) -> None:
            _fill_editor(record)
            editor_dialog.open()

        async def save() -> None:
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("object_types.name_required"), color="warning")
                return
            label = dict(type_label_store["label"] or {})
            description = (description_area.value or "").strip() or None
            keys = list(key_select.value or [])
            label_attr = (label_attr_select.value or "").strip() or None
            taxonomy = bool(taxonomy_switch.value)
            try:
                if _state["editing"]:
                    current_type = next(
                        (row for row in types if row.get("name") == _state["name"]), None
                    )
                    await client.update_object_type(
                        token,
                        _state["name"],
                        label=label or None,
                        description=description,
                        key_attributes=keys if keys else None,
                        label_attribute=None if label_attr is None else label_attr,
                        # A system type's flag is fixed; do not send it then.
                        taxonomy=None if bool((current_type or {}).get("system")) else taxonomy,
                    )
                else:
                    await client.create_object_type(
                        token,
                        name=name,
                        label=label or None,
                        description=description,
                        key_attributes=keys if keys else None,
                        label_attribute=label_attr,
                        taxonomy=taxonomy,
                    )
            except CoreError as error:
                ui.notify(f"{t('object_types.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload()
            ui.notify(t("object_types.saved"), color="positive")

        async def reload() -> None:
            nonlocal types, text_attrs
            with contextlib.suppress(Exception):
                types = await client.list_object_types(token, limit=1000)
            try:
                definitions = await client.list_attribute_vocabulary(token)
            except CoreError, httpx.HTTPError:  # keep the cached list on failure
                definitions = []
            text_attrs = text_attribute_options(definitions, declared_key_names(types))
            key_select.set_options({n: n for n in sorted(text_attrs)})
            label_attr_select.set_options({n: n for n in sorted(text_attrs)})
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def get_selected() -> dict | None:
            try:
                rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001 - the AG Grid bridge has no narrow contract
                return None
            return rows[0] if rows else None

        async def edit_selected() -> None:
            row = await get_selected()
            if not row:
                ui.notify(t("object_types.select_first"), color="warning")
                return
            full = next((r for r in types if str(r.get("name")) == str(row.get("name"))), None)
            open_editor(full)

        async def delete_selected() -> None:
            row = await get_selected()
            if not row:
                ui.notify(t("object_types.select_first"), color="warning")
                return
            name = str(row.get("name"))
            full = next((r for r in types if str(r.get("name")) == name), None)
            if bool(full.get("system")) if full else False:
                ui.notify(
                    f"{t('object_types.delete_failed')}: {t('object_types.system_locked')}",
                    color="negative",
                )
                return
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("object_types.delete_confirm").format(name=name)).classes(
                    "text-subtitle1"
                )
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_object_type(token, name)
                        except CoreError as error:
                            ui.notify(
                                f"{t('object_types.delete_failed')}: {error}", color="negative"
                            )
                            return
                        await reload()
                        ui.notify(t("object_types.deleted"), color="positive")

                    ui.button(t("object_types.delete"), on_click=_do).props("color=negative")
            confirm.open()

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(
                next((r for r in types if str(r.get("name")) == _id_of(e.args.get("data"))), None)
            ),
            ["data"],
        )


def _id_of(data: object) -> str:
    if isinstance(data, dict):
        return str(data.get("name") or "")
    return str(data)

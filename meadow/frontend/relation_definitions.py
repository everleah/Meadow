"""Admin page for the relation-type registry (admin/superadmin only).

Relation types come from ``GET /relation-definitions``. Built-in types
(``system: true``: ``depends_on`` composition + ``category_member``/
``collection_member``/``related`` aggregations) are read-only and cannot be
deleted. Custom types are always aggregations (``system: false``) and may
declare ``source_types``/``target_types`` (allowed object types) and
``attributes`` (edge metadata, referencing ``attribute_definition`` names) that
are editable when adding/editing a relation edge in the product form.

Every type carries a **usage** (``role``, see ``relation_roles``): built-in
roles are fixed, custom types may be ``link`` (default) or ``category``. A role
may fix the target type (``category`` ⇒ ``category`` objects).

- **List** (``GET /relation-definitions``), **create/edit** (``POST``/``PATCH
  /relation-definitions``) in a dialog, **delete** (``DELETE
  /relation-definitions/{id}``; 400 for system types, 409 while references use
  the type).
"""

from __future__ import annotations

import contextlib

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.multilang import base_lang, multilingual_value_editor
from meadow.frontend.relation_roles import (
    DEFAULT_ROLE,
    ROLE_CATEGORY,
    ROLE_COLLECTION,
    ROLE_COMPOSITION,
    custom_role_options,
    definition_key,
    effective_target_types,
    role_label,
    role_of,
    role_target_types,
)


def _label(value: object, locale: str) -> str:
    """Resolves a multilingual label map to a display string."""
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("en") or value.get("de") or "")
    return str(value or "")


def _names_of(value: object) -> str:
    items = value or []
    if not isinstance(items, list):
        return "—"
    return ", ".join(str(v) for v in items) if items else "—"


def _kind_badge(kind: str) -> str:
    if kind == "composition":
        return (
            '<span class="px-2 py-0.5 rounded text-xs bg-orange-2 text-orange-9">'
            + t("relation_definitions.kind_composition")
            + "</span>"
        )
    return (
        '<span class="px-2 py-0.5 rounded text-xs bg-blue-2 text-blue-9">'
        + t("relation_definitions.kind_aggregation")
        + "</span>"
    )


def _role_badge(role: str) -> str:
    """Renders the usage (`role`) of a relation type as a badge."""
    styles = {
        ROLE_COMPOSITION: "bg-orange-2 text-orange-9",
        ROLE_CATEGORY: "bg-teal-2 text-teal-9",
        ROLE_COLLECTION: "bg-purple-2 text-purple-9",
    }
    style = styles.get(role, "bg-grey-3 text-grey-8")
    return f'<span class="px-2 py-0.5 rounded text-xs {style}">{role_label(role)}</span>'


def _system_badge(system: bool) -> str:
    if system:
        return (
            '<span class="px-2 py-0.5 rounded text-xs bg-neutral text-gray-600">'
            + t("relation_definitions.system_default")
            + "</span>"
        )
    return (
        '<span class="px-2 py-0.5 rounded text-xs bg-warning text-white">'
        + t("relation_definitions.system_custom")
        + "</span>"
    )


@ui.page("/relation-definitions", response_timeout=15)
async def relation_definitions_page() -> None:
    await build_shell(active="/relation-definitions")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")
    locale = current_locale()

    try:
        defs = await client.list_relation_definitions(token)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("relation_definitions.load_failed")).classes("text-subtitle1 text-negative")
        return
    try:
        languages = await client.list_languages(token)
    except Exception:  # noqa: BLE001 - degrade to the UI language only
        languages = []
    try:
        object_types = await client.list_object_types(token, limit=1000)
    except Exception:  # noqa: BLE001
        object_types = []
    try:
        definitions = await client.list_attribute_definitions(token, limit=1000)
    except Exception:  # noqa: BLE001
        definitions = []

    type_options = {
        str(d.get("name")): str(d.get("name"))
        for d in object_types
        if d.get("name") and str(d.get("name")) != "saved_search"
    }
    attr_options = {str(d.get("name")): str(d.get("name")) for d in definitions if d.get("name")}

    with ui.column().classes("w-full p-6"):
        ui.label(t("relation_definitions.title")).classes("text-h4")
        ui.label(t("relation_definitions.subtitle")).classes("text-subtitle1")

        filters: dict[str, str] = {"name": ""}

        def _matches(row: dict) -> bool:
            if filters["name"]:
                q = filters["name"].lower()
                hay = (
                    f"{row.get('name', '')} {_label(row.get('label'), locale)}"
                    f" {_names_of(row.get('source_types'))} {_names_of(row.get('target_types'))}"
                ).lower()
                return q in hay
            return True

        def _rows() -> list[dict]:
            return [
                {
                    "key": str(r.get("id", "")).split(":", 1)[-1],
                    "name": str(r.get("name")),
                    "label": _label(r.get("label"), locale),
                    "role": _role_badge(role_of(r)),
                    "kind": _kind_badge(str(r.get("kind") or "")),
                    "source": _names_of(r.get("source_types")),
                    "target": _names_of(effective_target_types(r)),
                    "attributes": _names_of(r.get("attributes")),
                    "system": _system_badge(bool(r.get("system"))),
                    "updated": str(r.get("updated_at") or ""),
                }
                for r in defs
                if _matches(r)
            ]

        with ui.row().classes("w-full items-center gap-2 mt-2"):
            (
                ui.input(t("relation_definitions.filter_name"))
                .props("filled dense")
                .classes("w-56")
                .on_value_change(lambda e: _set_filter(str(e.value)))
            )

        column_defs = [
            {"headerName": t("relation_definitions.col_name"), "field": "name", "width": 160},
            {"headerName": t("relation_definitions.col_label"), "field": "label", "width": 180},
            {"headerName": t("relation_definitions.col_role"), "field": "role", "width": 150},
            {"headerName": t("relation_definitions.col_kind"), "field": "kind", "width": 140},
            {"headerName": t("relation_definitions.col_source"), "field": "source", "width": 180},
            {"headerName": t("relation_definitions.col_target"), "field": "target", "width": 180},
            {
                "headerName": t("relation_definitions.col_attributes"),
                "field": "attributes",
                "width": 220,
            },
            {"headerName": t("relation_definitions.col_system"), "field": "system", "width": 140},
            {"headerName": t("relation_definitions.col_updated"), "field": "updated", "width": 200},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[
                i for i, col in enumerate(column_defs) if col["field"] in ("role", "kind", "system")
            ],
        ).classes("w-full")

        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            with ui.row().classes("gap-2"):
                ui.button(
                    t("relation_definitions.new"), icon="add", on_click=lambda: open_editor(None)
                ).props("flat")
                ui.button(
                    t("relation_definitions.edit"), icon="edit", on_click=lambda: edit_selected()
                ).props("flat")
                ui.button(
                    t("relation_definitions.delete"),
                    icon="delete",
                    on_click=lambda: delete_selected(),
                ).props("flat color=negative")
            ui.button(
                t("relation_definitions.reload"),
                icon="refresh",
                on_click=lambda: reload(),
            ).props("flat")

        def _set_filter(value: str) -> None:
            filters["name"] = value
            grid.run_grid_method("setGridOption", "rowData", _rows())

        # --- Editor dialog ---------------------------------------------------
        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[40rem] max-w-[94vw]"):
            editor_title = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                name_input = (
                    ui.input(t("relation_definitions.field_name"))
                    .props("filled dense")
                    .classes("w-full")
                )
                rel_label_store: dict[str, object] = {"label": {}}
                rel_label_field = multilingual_value_editor(
                    field_label=t("relation_definitions.col_label"),
                    locale=locale,
                    languages=languages,
                    value=None,
                    store=rel_label_store,
                    key="label",
                )
                kind_select = (
                    ui.select(
                        {
                            "aggregation": t("relation_definitions.kind_aggregation"),
                            "composition": t("relation_definitions.kind_composition"),
                        },
                        label=t("relation_definitions.field_kind"),
                        value="aggregation",
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                source_select = (
                    ui.select(
                        {name: name for name in sorted(type_options)},
                        label=t("relation_definitions.field_source"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                role_select = (
                    ui.select(
                        custom_role_options(),
                        label=t("relation_definitions.field_role"),
                        value=DEFAULT_ROLE,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                role_hint = ui.label("").classes("text-caption text-gray-500")
                target_select = (
                    ui.select(
                        {name: name for name in sorted(type_options)},
                        label=t("relation_definitions.field_target"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                attr_select = (
                    ui.select(
                        {name: name for name in sorted(attr_options)},
                        label=t("relation_definitions.field_attributes"),
                        value=[],
                        multiple=True,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                ui.label(t("relation_definitions.kind_hint")).classes("text-caption text-gray-500")
            with (
                ui.row().classes("w-full justify-between mt-2 gap-2"),
                ui.row().classes("gap-2"),
            ):
                ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                ui.button(
                    t("relation_definitions.save"), icon="save", on_click=lambda: save()
                ).props("flat")

        def _sync_role() -> None:
            """A usage fixes the target type; free types get the picker back."""
            role = str(role_select.value or DEFAULT_ROLE)
            fixed = role_target_types(role)
            if fixed:
                target_select.set_value(fixed)
                target_select.disable()
            else:
                target_select.enable()
            if role == ROLE_CATEGORY:
                role_hint.set_text(t("relation_definitions.role_category_hint"))
            elif role == ROLE_COLLECTION:
                role_hint.set_text(t("relation_definitions.role_collection_hint"))
            else:
                role_hint.set_text(t("relation_definitions.role_link_hint"))

        role_select.on_value_change(lambda _event: _sync_role())

        _state: dict = {"editing": False, "key": None}

        def _fill_editor(record: dict | None) -> None:
            editing = record is not None
            _state["editing"] = editing
            _state["system"] = bool(record.get("system")) if editing else False
            _state["key"] = definition_key(record) if editing else None
            editor_title.set_text(
                t("relation_definitions.edit") if editing else t("relation_definitions.new")
            )
            name_input.set_value(str(record.get("name") or "") if editing else "")
            name_input.disable() if editing else name_input.enable()
            label = (record.get("label") if editing else {}) or {}
            rel_label_store["label"] = dict(label)
            rel_label_field.set_value(str(rel_label_store["label"].get(base_lang(locale)) or ""))
            kind_select.set_value(
                str(record.get("kind") or "aggregation") if editing else "aggregation"
            )
            kind_select.disable()
            # The usage of the built-in types is fixed (enforced by the core).
            if editing and _state["system"]:
                role_select.set_value(role_of(record))
                role_select.disable()
            else:
                role_select.set_value(role_of(record) if editing else DEFAULT_ROLE)
                role_select.enable()
            _sync_role()
            source_select.set_value(list(record.get("source_types") or []) if editing else [])
            target_select.set_value(list(record.get("target_types") or []) if editing else [])
            _sync_role()
            attr_select.set_value(list(record.get("attributes") or []) if editing else [])

        def open_editor(record: dict | None) -> None:
            _fill_editor(record)
            editor_dialog.open()

        async def save() -> None:
            name = (name_input.value or "").strip()
            if not name:
                ui.notify(t("relation_definitions.name_required"), color="warning")
                return
            label = dict(rel_label_store["label"] or {})
            source = list(source_select.value or [])
            target = list(role_target_types(str(role_select.value or DEFAULT_ROLE))) or list(
                target_select.value or []
            )
            attrs = list(attr_select.value or [])
            role = str(role_select.value or DEFAULT_ROLE)
            # The usage of the built-in types is immutable; only send it for
            # custom types so the core does not reject the request.
            send_role = None if _state["system"] else role
            try:
                if _state["editing"]:
                    await client.update_relation_definition(
                        token,
                        _state["key"],
                        label=label or None,
                        source_types=source if source else None,
                        target_types=target if target else None,
                        attributes=attrs if attrs else None,
                        role=send_role,
                    )
                else:
                    await client.create_relation_definition(
                        token,
                        name=name,
                        label=label or None,
                        source_types=source if source else None,
                        target_types=target if target else None,
                        attributes=attrs if attrs else None,
                        role=role,
                    )
            except CoreError as error:
                ui.notify(f"{t('relation_definitions.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload()
            ui.notify(t("relation_definitions.saved"), color="positive")

        async def reload() -> None:
            nonlocal defs, type_options, attr_options
            with contextlib.suppress(Exception):
                defs = await client.list_relation_definitions(token)
            try:
                object_types = await client.list_object_types(token, limit=1000)
            except Exception:  # noqa: BLE001
                object_types = []
            try:
                definitions = await client.list_attribute_definitions(token, limit=1000)
            except Exception:  # noqa: BLE001
                definitions = []
            type_options = {
                str(d.get("name")): str(d.get("name"))
                for d in object_types
                if d.get("name") and str(d.get("name")) != "saved_search"
            }
            attr_options = {
                str(d.get("name")): str(d.get("name")) for d in definitions if d.get("name")
            }
            source_select.set_options({n: n for n in sorted(type_options)})
            target_select.set_options({n: n for n in sorted(type_options)})
            attr_select.set_options({n: n for n in sorted(attr_options)})
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def get_selected() -> dict | None:
            try:
                rows = await grid.get_selected_rows()
            except Exception:  # noqa: BLE001
                return None
            return rows[0] if rows else None

        async def edit_selected() -> None:
            row = await get_selected()
            if not row:
                ui.notify(t("relation_definitions.select_first"), color="warning")
                return
            full = next((r for r in defs if str(r.get("name")) == str(row.get("name"))), None)
            open_editor(full)

        async def delete_selected() -> None:
            row = await get_selected()
            if not row:
                ui.notify(t("relation_definitions.select_first"), color="warning")
                return
            key = str(row.get("key"))
            full = next((r for r in defs if str(r.get("id", "")).split(":", 1)[-1] == key), None)
            if bool(full.get("system")) if full else False:
                ui.notify(
                    f"{t('relation_definitions.delete_failed')}: "
                    f"{t('relation_definitions.system_locked')}",
                    color="negative",
                )
                return
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("relation_definitions.delete_confirm").format(name=key)).classes(
                    "text-subtitle1"
                )
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_relation_definition(token, key)
                        except CoreError as error:
                            ui.notify(
                                f"{t('relation_definitions.delete_failed')}: {error}",
                                color="negative",
                            )
                            return
                        await reload()
                        ui.notify(t("relation_definitions.deleted"), color="positive")

                    ui.button(t("relation_definitions.delete"), on_click=_do).props(
                        "color=negative"
                    )
            confirm.open()

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(
                next(
                    (
                        r
                        for r in defs
                        if str(r.get("id", "")).split(":", 1)[-1] == _id_of(e.args.get("data"))
                    ),
                    None,
                )
            ),
            ["data"],
        )


def _id_of(data: object) -> str:
    if isinstance(data, dict):
        return str(data.get("key") or "")
    return str(data)

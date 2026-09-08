"""Admin page for reusable enum sets (Reference Data, admin/superadmin only).

Enum options are decoupled from attribute definitions: an ``enum_option`` is a
stable ``code`` with multilingual ``labels``, and an ``enum_set`` bundles an
ordered, reusable list of options. An ``enum`` attribute references a set by
``enum_set_id``. This page manages sets and their options (``/enum-sets`` and
``/enum-options``); the attribute-definition dialog only selects a set.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.multilang import base_lang, multilingual_value_editor


def _label(value: object, locale: str) -> str:
    """Resolves a multilingual label map to a display string."""
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("en") or value.get("de") or "")
    return str(value or "")


@ui.page("/enum-sets", response_timeout=15)
async def enum_sets_page() -> None:
    await build_shell(active="/enum-sets")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")
    locale = current_locale()

    try:
        sets = await client.list_enum_sets(token)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("enum_set.load_failed")).classes("text-subtitle1 text-negative")
        return

    try:
        languages = await client.list_languages(token)
    except Exception:  # noqa: BLE001 - degrade to the UI language only
        languages = []

    filters: dict[str, str] = {"name": ""}

    def _matches(s: dict) -> bool:
        if filters["name"]:
            q = filters["name"].lower()
            hay = f"{s.get('name', '')} {_label(s.get('label'), locale)}".lower()
            if q not in hay:
                return False
        return True

    def _rows() -> list[dict]:
        rows: list[dict] = []
        for s in sets:
            if not _matches(s):
                continue
            rows.append(
                {
                    "id": str(s.get("id")),
                    "name": str(s.get("name") or ""),
                    "label": _label(s.get("label"), locale),
                    "options": len(s.get("options") or []),
                    "position": int(s.get("position") or 0),
                    "updated": str(s.get("updated_at") or ""),
                }
            )
        return rows

    with ui.column().classes("w-full p-6"):
        ui.label(t("enum_set.title")).classes("text-h4")
        ui.label(t("enum_set.subtitle")).classes("text-subtitle1")

        def _set_filter(name: str) -> None:
            filters["name"] = name
            grid.run_grid_method("setGridOption", "rowData", _rows())

        with ui.row().classes("w-full items-center gap-2 mt-2"):
            ui.input(
                placeholder=t("enum_set.filter_name"),
                on_change=lambda e: _set_filter(str(e.value)),
            ).props("filled dense").classes("w-64")

        column_defs = [
            {"headerName": t("enum_set.col_name"), "field": "name", "width": 180},
            {"headerName": t("enum_set.col_label"), "field": "label", "width": 220},
            {"headerName": t("enum_set.col_options"), "field": "options", "width": 110},
            {"headerName": t("enum_set.col_position"), "field": "position", "width": 110},
            {"headerName": t("enum_set.col_updated"), "field": "updated", "width": 220},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
        ).classes("w-full")
        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            ui.button(t("enum_set.new"), icon="add", on_click=lambda: open_editor(None)).props(
                "flat"
            )
            ui.button(t("enum_set.reload"), icon="refresh", on_click=lambda: reload_sets()).props(
                "flat"
            )

        # --- Set editor dialog ----------------------------------------------
        set_dialog = ui.dialog()
        with set_dialog, ui.card().classes("w-[32rem] max-w-[94vw]"):
            set_title = ui.label("").classes("text-h6")
            set_state: dict = {"editing_id": None}
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full gap-2"):
                    name_input = (
                        ui.input(t("enum_set.field_name")).props("filled dense").classes("grow")
                    )
                    position_input = (
                        ui.number(t("enum_set.field_position"), value=0)
                        .props("filled dense")
                        .classes("w-32")
                    )
                set_label_store: dict[str, object] = {"label": {}}
                set_label_field = multilingual_value_editor(
                    field_label=t("enum_set.col_label"),
                    locale=locale,
                    languages=languages,
                    value=None,
                    store=set_label_store,
                    key="label",
                )
                ui.button(
                    t("enum_set.manage_options"),
                    icon="list",
                    on_click=lambda: open_options(current_set()),
                ).props("flat dense")
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("enum_set.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=set_dialog.close).props("flat")
                    ui.button(t("enum_set.save"), icon="save", on_click=lambda: do_save()).props(
                        "flat"
                    )

        def current_set() -> dict | None:
            editing_id = set_state.get("editing_id")
            if not editing_id:
                return None
            return next((s for s in sets if str(s.get("id")) == str(editing_id)), None)

        def _fill_editor(s: dict | None) -> None:
            editing = s is not None
            set_title.set_text(t("enum_set.edit") if editing else t("enum_set.new"))
            set_state["editing_id"] = s.get("id") if editing else None
            delete_btn.visible = editing
            name_input.set_value(str(s.get("name") or "") if editing else "")
            name_input.disable() if editing else name_input.enable()
            position_input.set_value(int(s.get("position") or 0) if editing else 0)
            label = (s.get("label") if editing else {}) or {}
            if not isinstance(label, dict):
                label = {}
            set_label_store["label"] = dict(label)
            set_label_field.set_value(str(set_label_store["label"].get(base_lang(locale)) or ""))

        def open_editor(s: dict | None) -> None:
            _fill_editor(s)
            set_dialog.open()

        async def reload_sets() -> None:
            nonlocal sets
            try:
                sets = await client.list_enum_sets(token)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def do_save() -> None:
            editing_id = set_state.get("editing_id")
            name = (name_input.value or "").strip()
            label = dict(set_label_store["label"] or {})
            try:
                if editing_id:
                    await client.update_enum_set(
                        token,
                        editing_id,
                        label=label,
                        position=int(position_input.value or 0),
                    )
                else:
                    if not name:
                        ui.notify(t("enum_set.name_required"), color="warning")
                        return
                    new_set = await client.create_enum_set(
                        token,
                        name=name,
                        label=label,
                        position=int(position_input.value or 0),
                    )
                    # Open the options editor for the freshly created set so the
                    # admin can add options right away.
                    set_state["editing_id"] = new_set.get("id")
                    open_options_for_id(new_set.get("id"))
            except CoreError as error:
                ui.notify(f"{t('enum_set.save_failed')}: {error}", color="negative")
                return
            set_dialog.close()
            await reload_sets()
            ui.notify(t("enum_set.saved"), color="positive")

        async def do_delete() -> None:
            editing_id = set_state.get("editing_id")
            if not editing_id:
                return
            name = name_input.value or ""
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("enum_set.delete_confirm").format(name=name)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_enum_set(token, editing_id)
                        except CoreError as error:
                            ui.notify(f"{t('enum_set.delete_failed')}: {error}", color="negative")
                            return
                        set_dialog.close()
                        await reload_sets()
                        ui.notify(t("enum_set.deleted"), color="positive")

                    ui.button(t("enum_set.delete"), on_click=_do).props("color=negative")
            confirm.open()

        # --- Options editor dialog ------------------------------------------
        opt_state: dict = {
            "set_id": None,
            "rows": [],
            "remap": {},
            "row_key": 0,
        }

        def _next_row_key() -> int:
            opt_state["row_key"] += 1
            return opt_state["row_key"]

        @ui.refreshable
        def render_options() -> None:
            for row in opt_state["rows"]:
                with ui.row().classes("w-full items-center gap-1"):
                    code_input = (
                        ui.input(
                            placeholder=t("enum_set.option_code"),
                            value=str(row["code"] or ""),
                        )
                        .props("filled dense")
                        .classes("w-28")
                    )
                    multilingual_value_editor(
                        field_label=t("enum_set.option_label"),
                        locale=locale,
                        languages=languages,
                        value=row.get("labels"),
                        store=row,
                        key="labels",
                        in_row=False,
                    )
                    active_check = (
                        ui.checkbox(value=bool(row["active"]))
                        .props("dense")
                        .tooltip(t("enum_set.option_active"))
                    )
                    ui.button(
                        icon="close",
                        on_click=lambda r=row: remove_option(r),
                    ).props("flat dense round").tooltip(t("enum_set.option_remove"))

                    code_input.on_value_change(lambda e, r=row: r.__setitem__("code", str(e.value)))
                    active_check.on_value_change(
                        lambda e, r=row: r.__setitem__("active", bool(e.value))
                    )

        def open_options_for_id(set_id: object) -> None:
            s = next((x for x in sets if str(x.get("id")) == str(set_id)), None)
            if s:
                open_options(s)

        def open_options(s: dict) -> None:
            opt_state["set_id"] = s.get("id")
            opt_state["remap"] = {}
            options = s.get("options") or []
            opt_state["rows"] = []
            for o in options:
                labels = o.get("labels") or {}
                if not isinstance(labels, dict):
                    labels = {}
                opt_state["rows"].append(
                    {
                        "key": _next_row_key(),
                        "id": o.get("id"),
                        "code": str(o.get("code") or ""),
                        "labels": dict(labels),
                        "active": bool(o.get("active", True)),
                    }
                )
            opt_title.set_text(t("enum_set.options_title").format(name=str(s.get("name") or "")))
            render_options.refresh()
            opt_dialog.open()

        def add_option() -> None:
            opt_state["rows"].append(
                {
                    "key": _next_row_key(),
                    "id": None,
                    "code": "",
                    "labels": {},
                    "active": True,
                }
            )
            render_options.refresh()

        async def remove_option(row: dict) -> None:
            if not row.get("id"):
                opt_state["rows"] = [r for r in opt_state["rows"] if r.get("key") != row["key"]]
                render_options.refresh()
                return
            await open_remap(row)

        async def open_remap(row: dict) -> None:
            code = str(row.get("code") or "")
            remaining = [
                str(r.get("code"))
                for r in opt_state["rows"]
                if r.get("key") != row["key"] and r.get("code")
            ]
            with ui.dialog() as remap_dlg, ui.card().classes("w-[30rem] max-w-[90vw]"):
                ui.label(t("enum_set.option_in_use").format(value=code)).classes("text-subtitle1")
                target_select = (
                    ui.select(
                        {o: o for o in remaining},
                        label=t("enum_set.remap_to"),
                        value=None,
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                new_input = (
                    ui.input(t("enum_set.remap_new")).props("filled dense").classes("w-full")
                )
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=remap_dlg.close).props("flat")

                    def do_remap() -> None:
                        target = (target_select.value or new_input.value or "").strip()
                        remap_dlg.close()
                        if target and target != code:
                            opt_state["remap"][code] = target
                            opt_state["rows"] = [
                                r for r in opt_state["rows"] if r.get("key") != row["key"]
                            ]
                            render_options.refresh()
                            return
                        ui.notify(t("enum_set.remap_required"), color="warning")

                    ui.button(t("enum_set.remap"), on_click=do_remap).props("color=warning")
            remap_dlg.open()

        async def save_options() -> None:
            set_id = opt_state.get("set_id")
            if not set_id:
                return
            option_ids: list[str] = []
            try:
                for row in opt_state["rows"]:
                    code = str(row.get("code") or "").strip()
                    labels = dict(row.get("labels") or {})
                    if row.get("id"):
                        await client.update_enum_option(
                            token,
                            row["id"],
                            labels=labels or None,
                            active=bool(row.get("active", True)),
                        )
                        option_ids.append(str(row["id"]))
                    else:
                        if not code:
                            ui.notify(t("enum_set.code_required"), color="warning")
                            return
                        try:
                            created = await client.create_enum_option(
                                token,
                                code=code,
                                labels=labels or None,
                                position=len(option_ids),
                                active=bool(row.get("active", True)),
                            )
                        except CoreError as error:
                            ui.notify(
                                f"{t('enum_set.option_create_failed')}: {error}", color="negative"
                            )
                            return
                        option_ids.append(str(created.get("id")))
                await client.update_enum_set(
                    token,
                    set_id,
                    options=option_ids,
                    option_remap=opt_state["remap"] or None,
                )
            except CoreError as error:
                ui.notify(f"{t('enum_set.save_failed')}: {error}", color="negative")
                return
            opt_dialog.close()
            await reload_sets()
            set_state["editing_id"] = set_id
            ui.notify(t("enum_set.saved"), color="positive")

        opt_dialog = ui.dialog()
        with opt_dialog, ui.card().classes("w-[42rem] max-w-[95vw]"):
            opt_title = ui.label("").classes("text-h6")
            ui.label(t("enum_set.options_hint")).classes("text-caption text-gray-500")
            with ui.column().classes("w-full mt-1 gap-1 overflow-auto"):
                render_options()
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                ui.button(
                    t("enum_set.add_option"), icon="add", on_click=lambda: add_option()
                ).props("flat")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=opt_dialog.close).props("flat")
                    ui.button(
                        t("enum_set.save"), icon="save", on_click=lambda: save_options()
                    ).props("flat")

        def _set_for(data: object) -> dict | None:
            sid = str(data.get("id") or "") if isinstance(data, dict) else str(data or "")
            if not sid:
                return None
            return next((s for s in sets if str(s.get("id")) == sid), None)

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(_set_for(e.args.get("data"))),
            ["data"],
        )

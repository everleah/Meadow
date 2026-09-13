"""Admin page for the unit catalog (admin/superadmin only).

Lists the seeded unit catalog (``GET /units``, SI + imperial/custom) filtered
by dimension, and lets admins create / edit / delete **custom** units
(``POST``/``PATCH``/``DELETE /units``) that map to a canonical SI unit via a
linear factor/offset (``canonical = value * factor + offset``). Seeded
``default`` units are read-only.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import auth
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.config import CORE_URL
from meadow.frontend.filters import filter_select
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.multilang import base_lang, multilingual_value_editor


def _label(value: object, locale: str) -> str:
    """Resolves a multilingual label map to a display string."""
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("en") or value.get("de") or "")
    return str(value or "")


@ui.page("/units", response_timeout=15)
async def units_page() -> None:
    await build_shell(active="/units")
    if not auth.is_admin():
        ui.navigate.to("/")
        return
    client = CoreClient(CORE_URL)
    token = app.storage.user.get("token", "")
    locale = current_locale()

    try:
        units = await client.list_units(token, limit=1000)
    except Exception:  # noqa: BLE001 - show a friendly message on failure
        ui.label(t("units.load_failed")).classes("text-subtitle1 text-negative")
        return

    try:
        languages = await client.list_languages(token)
    except Exception:  # noqa: BLE001 - degrade to the UI language only
        languages = []

    dimensions = sorted({str(u.get("dimension") or "") for u in units if u.get("dimension")})
    by_id: dict[str, dict] = {}
    full_by_key: dict[str, str] = {}
    for _u in units:
        _uid = str(_u.get("id") or "")
        if not _uid:
            continue
        by_id[_uid] = _u
        by_id[_uid.split(":")[-1]] = _u
        full_by_key[_uid] = _uid
        full_by_key[_uid.split(":")[-1]] = _uid

    with ui.column().classes("w-full p-6"):
        ui.label(t("units.title")).classes("text-h4")
        ui.label(t("units.subtitle")).classes("text-subtitle1")

        # Filter bar (dimension).
        filters: dict[str, str] = {"dimension": ""}

        def _matches(u: dict) -> bool:
            dimension = str(u.get("dimension") or "")
            return not (filters["dimension"] and dimension != filters["dimension"])

        def _canonical_symbol(u: dict) -> str:
            cid = u.get("canonical_unit_id")
            if not cid:
                return str(u.get("symbol") or "")
            c = by_id.get(str(cid))
            if c is None:
                return str(cid)
            return str(c.get("symbol") or cid)

        def _rows() -> list[dict]:
            return [
                {
                    "id": str(u.get("id")),
                    "symbol": str(u.get("symbol") or ""),
                    "label": _label(u.get("label"), locale),
                    "dimension": str(u.get("dimension") or ""),
                    "canonical": _canonical_symbol(u),
                    "factor": float(u.get("factor") or 0),
                    "offset": float(u.get("offset") or 0),
                    "system": str(u.get("system") or ""),
                    "readonly": bool(u.get("readonly")),
                }
                for u in units
                if _matches(u)
            ]

        with ui.row().classes("w-full items-center gap-2 mt-2"):
            filter_select(
                {d: d for d in dimensions},
                label=t("units.filter_dimension"),
                classes="w-52",
                on_change=lambda value: _set_filter(str(value)),
            )

        def _set_filter(dimension: str) -> None:
            filters["dimension"] = dimension
            grid.run_grid_method("setGridOption", "rowData", _rows())

        def _system_badge(system: str, readonly: bool) -> str:
            if readonly or system == "default":
                cls = "bg-neutral text-gray-600"
                label = t("units.system_default")
            else:
                cls = "bg-warning text-white"
                label = t("units.system_custom")
            return f'<span class="px-2 py-0.5 rounded text-xs {cls}">{label}</span>'

        column_defs = [
            {"headerName": t("units.col_symbol"), "field": "symbol", "width": 120},
            {"headerName": t("units.col_label"), "field": "label", "width": 200},
            {"headerName": t("units.col_dimension"), "field": "dimension", "width": 150},
            {"headerName": t("units.col_canonical"), "field": "canonical", "width": 130},
            {"headerName": t("units.col_factor"), "field": "factor", "width": 100},
            {"headerName": t("units.col_offset"), "field": "offset", "width": 100},
            {"headerName": t("units.col_system"), "field": "system", "width": 140},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": column_defs,
                "rowData": _rows(),
                "rowSelection": {"mode": "singleRow"},
            },
            html_columns=[i for i, col in enumerate(column_defs) if col["field"] == "system"],
        ).classes("w-full")
        with ui.row().classes("w-full justify-between mt-2 gap-2"):
            ui.button(t("units.new"), icon="add", on_click=lambda: open_editor(None)).props("flat")
            ui.button(t("units.reload"), icon="refresh", on_click=lambda: reload_units()).props(
                "flat"
            )

        editor_dialog = ui.dialog()
        with editor_dialog, ui.card().classes("w-[36rem] max-w-[94vw]"):
            editor_title = ui.label("").classes("text-h6")
            state: dict = {"editing_id": None, "readonly": False}
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full gap-2"):
                    symbol_input = (
                        ui.input(t("units.field_symbol")).props("filled dense").classes("grow")
                    )
                    base_select = (
                        ui.select(
                            {
                                str(u.get("id")): (
                                    f"{u.get('symbol')} · {_label(u.get('label'), locale)}"
                                )
                                for u in units
                            },
                            label=t("units.field_base"),
                            value=None,
                        )
                        .props("filled dense")
                        .classes("grow")
                    )
                unit_label_store: dict[str, object] = {"label": {}}
                unit_label_field = multilingual_value_editor(
                    field_label=t("units.col_label"),
                    locale=locale,
                    languages=languages,
                    value=None,
                    store=unit_label_store,
                    key="label",
                )
                with ui.row().classes("w-full gap-2"):
                    factor_input = (
                        ui.number(t("units.field_factor"), value=1.0)
                        .props("filled dense")
                        .classes("grow")
                    )
                    offset_input = (
                        ui.number(t("units.field_offset"), value=0.0)
                        .props("filled dense")
                        .classes("grow")
                    )
                ui.label(t("units.conversion_hint")).classes("text-caption text-gray-500")
            with ui.row().classes("w-full justify-between mt-2 gap-2"):
                delete_btn = ui.button(
                    t("units.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")
                with ui.row().classes("gap-2"):
                    ui.button(t("grid.close"), on_click=editor_dialog.close).props("flat")
                    ui.button(t("units.save"), icon="save", on_click=lambda: do_save()).props(
                        "flat"
                    )

        def _fill_editor(unit: dict | None) -> None:
            editing = unit is not None
            editor_title.set_text(t("units.edit") if editing else t("units.new"))
            state["editing_id"] = unit.get("id") if editing else None
            state["readonly"] = bool(unit.get("readonly")) if editing else False
            delete_btn.visible = editing and not bool(unit.get("readonly"))
            symbol_input.set_value(str(unit.get("symbol") or "") if editing else "")
            symbol_input.disable() if editing else symbol_input.enable()
            base_select.set_value(
                full_by_key.get(str(unit.get("canonical_unit_id") or ""), str(unit.get("id")))
                if editing
                else None
            )
            base_select.disable() if editing else base_select.enable()
            label = (unit.get("label") if editing else {}) or {}
            if not isinstance(label, dict):
                label = {}
            unit_label_store["label"] = dict(label)
            unit_label_field.set_value(str(unit_label_store["label"].get(base_lang(locale)) or ""))
            factor_input.set_value(float(unit.get("factor") or 1.0) if editing else 1.0)
            offset_input.set_value(float(unit.get("offset") or 0.0) if editing else 0.0)

        def open_editor(unit: dict | None) -> None:
            _fill_editor(unit)
            editor_dialog.open()

        async def reload_units() -> None:
            nonlocal units
            try:
                units = await client.list_units(token, limit=1000)
            except Exception:  # noqa: BLE001 - keep the cached list on failure
                return
            grid.run_grid_method("setGridOption", "rowData", _rows())

        async def do_save() -> None:
            editing_id = state.get("editing_id")
            symbol = (symbol_input.value or "").strip()
            if not symbol:
                ui.notify(t("units.symbol_required"), color="warning")
                return
            base = base_select.value
            if not base:
                ui.notify(t("units.base_required"), color="warning")
                return
            label = dict(unit_label_store["label"] or {})
            try:
                if editing_id:
                    await client.update_unit(
                        token,
                        editing_id,
                        symbol=symbol,
                        factor=float(factor_input.value or 1.0),
                        offset=float(offset_input.value or 0.0),
                        label=label,
                    )
                else:
                    # The backend resolves `canonical_unit_id` via symbol or
                    # record key; send the bare key (e.g. `unit_m`) rather than
                    # the full `unit:unit_m` record id.
                    canonical_ref = str(base).split(":")[-1]
                    await client.create_unit(
                        token,
                        symbol=symbol,
                        canonical_unit_id=canonical_ref,
                        factor=float(factor_input.value or 1.0),
                        offset=float(offset_input.value or 0.0),
                        label=label,
                    )
            except CoreError as error:
                ui.notify(f"{t('units.save_failed')}: {error}", color="negative")
                return
            editor_dialog.close()
            await reload_units()
            ui.notify(t("units.saved"), color="positive")

        async def do_delete() -> None:
            editing_id = state.get("editing_id")
            if not editing_id:
                return
            symbol = symbol_input.value or ""
            with ui.dialog() as confirm, ui.card().classes("w-[28rem] max-w-[90vw]"):
                ui.label(t("units.delete_confirm").format(symbol=symbol)).classes("text-subtitle1")
                with ui.row().classes("w-full justify-end mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=confirm.close).props("flat")

                    async def _do() -> None:
                        confirm.close()
                        try:
                            await client.delete_unit(token, editing_id)
                        except CoreError as error:
                            ui.notify(f"{t('units.delete_failed')}: {error}", color="negative")
                            return
                        editor_dialog.close()
                        await reload_units()
                        ui.notify(t("units.deleted"), color="positive")

                    ui.button(t("units.delete"), on_click=_do).props("color=negative")
            confirm.open()

        def _unit_for(data: object) -> dict | None:
            """Resolves a double-click payload to the full API unit record."""
            uid = str(data.get("id") or "") if isinstance(data, dict) else str(data or "")
            if not uid:
                return None
            return next((u for u in units if str(u.get("id")) == uid), None)

        grid.on(
            "cellDoubleClicked",
            lambda e: open_editor(_unit_for(e.args.get("data"))),
            ["data"],
        )

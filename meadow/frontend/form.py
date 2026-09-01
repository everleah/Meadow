"""Product form (drill-down): deep editing of a single object.

Phase 7, step 6. A single scrollable page with collapsible attribute groups
(``ui.expansion``), type-specific inputs, hybrid-language editing for
multilingual attributes, an inherited-value marker (with "back to inherited"),
a completeness widget in the header and a status selector. Saves via
``PATCH /objects/{id}``.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient
from meadow.frontend.grid import completeness_badge, resolve_value, status_badge
from meadow.frontend.i18n import current_locale, supported_locales, t
from meadow.frontend.layout import build_shell

#: Attribute names that are not declared as ``attribute_definition`` rows but
#: still live on the object (the human-readable display name).
_VIRTUAL_ATTRIBUTES = ("name",)

#: Workflow statuses offered in the status selector. The core validates the
#: actual transition via the workflow guard.
_STATUS_OPTIONS = ("draft", "in_review", "published", "archived")


def _label(defn: dict | None, name: str, locale: str) -> str:
    """Resolves an attribute's display label (definition label or the name)."""
    if defn is not None:
        text = resolve_value(defn.get("label"), locale)
        if text:
            return text
    return name


def _applicable(defn: dict, object_type: str) -> bool:
    """Whether an attribute definition applies to the object's type."""
    types = defn.get("applicable_types") or []
    return not types or object_type in types


def _raw_value(raw_attributes: dict, name: str, locale: str) -> object | None:
    """Returns the raw value of an attribute for a locale (dict value or scalar)."""
    value = raw_attributes.get(name)
    if isinstance(value, dict):
        return value.get(locale)
    return value


@ui.page("/products/{object_id}", response_timeout=15)
async def product_form(object_id: str) -> None:
    """Opens a single object for deep editing."""
    build_shell(active="/products")
    locale = current_locale()
    with ui.column().classes("w-full p-6"):
        ui.button(
            t("form.back"),
            icon="arrow_back",
            on_click=lambda: ui.navigate.to("/products"),
        ).props("flat")

        client = CoreClient(config.CORE_URL)
        token = app.storage.user.get("token", "")
        try:
            raw = await client.get_object(token, object_id, resolve_inheritance=False)
            resolved = await client.get_object(
                token, object_id, locale=locale, resolve_inheritance=True
            )
            definitions = await client.list_attribute_definitions(token)
            groups = await client.list_attribute_groups(token)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("form.load_failed")).classes("text-subtitle1 text-negative")
            return

        object_type = str(resolved.get("type", ""))
        raw_attributes = dict(raw.get("attributes") or {})
        resolved_attributes = dict(resolved.get("attributes") or {})
        name = resolve_value(resolved_attributes.get("name"), locale) or object_type
        missing = resolved.get("missing_required") or []
        completeness = (resolved.get("completeness") or {}).get(locale, 0)

        # --- Header: name, type, status badge, completeness widget. -----------
        with ui.row().classes("items-center gap-3 w-full mt-2"):
            ui.label(name).classes("text-h5")
            ui.label(object_type).classes("text-caption text-gray-500")
            ui.html(status_badge(str(resolved.get("status", "")))).classes("text-sm")
            ui.html(completeness_badge(int(completeness))).classes("text-sm").tooltip(
                t("form.missing_required").format(count=len(missing))
            )

        # Attribute definitions applicable to this object type, grouped.
        defs = [d for d in definitions if d.get("name") and _applicable(d, object_type)]
        group_by_id = {str(g.get("id")): g for g in groups}
        sections: dict[str, list] = {}
        for d in defs:
            gid = str(d.get("group_id") or "")
            sections.setdefault(gid, []).append(d)
        # The virtual `name` attribute always goes into the default section.
        sections.setdefault("", []).insert(0, None)
        ordered_sections = sorted(
            sections.items(),
            key=lambda kv: (
                int(group_by_id[kv[0]].get("position") or 0) if kv[0] in group_by_id else 0,
                kv[0],
            ),
        )

        # Holds the edited value of each rendered field.
        field_ui: dict[tuple[str, str], object] = {}

        def build_widget(name: str, defn: dict | None, value: object, loc: str) -> None:
            atype = (defn or {}).get("type", "text")

            def on_change(e) -> None:
                field_ui[(name, loc)] = e.value

            if atype == "number":
                field: ui.element = (
                    ui.number(value=value).props("outline dense").on_value_change(on_change)
                )
            elif atype == "boolean":
                field = ui.switch(value=bool(value)).props("dense").on_value_change(on_change)
            elif atype == "enum":
                options = ((defn or {}).get("constraints") or {}).get("enum_values") or []
                field = (
                    ui.select(options, value=value)
                    .props("outline dense")
                    .on_value_change(on_change)
                )
            elif atype == "date":
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("outline dense type=date")
                    .on_value_change(on_change)
                )
            else:
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("outline dense")
                    .on_value_change(on_change)
                )
            field.classes("w-full")

        # --- Sections (attribute groups as collapsible accordions). ------------
        with ui.column().classes("w-full gap-2 mt-4"):
            for gid, section_defs in ordered_sections:
                group = group_by_id.get(gid)
                label = _label(group, gid, locale) if group else t("form.general")
                with (
                    ui.expansion(label, value=True).classes("w-full"),
                    ui.column().classes("w-full gap-2"),
                ):
                    for d in section_defs:
                        attr_name = "name" if d is None else str(d.get("name"))
                        is_virtual = d is None
                        is_multilingual = bool(is_virtual or (d or {}).get("multilingual"))
                        attribute_label = _label(d, attr_name, locale)
                        if is_multilingual:
                            for loc in supported_locales():
                                value = _raw_value(raw_attributes, attr_name, loc)
                                if value is None:
                                    value = _raw_value(resolved_attributes, attr_name, loc)
                                with ui.row().classes("items-center gap-2 w-full"):
                                    ui.label(f"{attribute_label} · {loc}").classes(
                                        "w-48 text-caption"
                                    )
                                    build_widget(attr_name, d, value, loc)
                        else:
                            value = _raw_value(resolved_attributes, attr_name, locale)
                            own = _raw_value(raw_attributes, attr_name, locale)
                            with ui.row().classes("items-center gap-2 w-full"):
                                ui.label(attribute_label).classes("w-48 text-caption")
                                if own is None and value is not None:
                                    ui.icon("swap_horiz").classes("text-gray-400").tooltip(
                                        t("form.inherited")
                                    )
                                build_widget(attr_name, d, value, locale)

        # --- Status selector. --------------------------------------------------
        with ui.row().classes("items-center gap-2 w-full mt-4"):
            ui.label(t("form.status")).classes("w-48 text-caption")
            status_select = (
                ui.select(
                    list(_STATUS_OPTIONS),
                    value=str(resolved.get("status", "")),
                )
                .props("outline dense")
                .classes("w-64")
            )

        # --- Save. -------------------------------------------------------------
        async def save() -> None:
            new_attrs: dict = {
                k: (dict(v) if isinstance(v, dict) else v) for k, v in raw_attributes.items()
            }
            changed = False
            for (attr_name, loc), value in field_ui.items():
                current = new_attrs.get(attr_name)
                if isinstance(current, dict):
                    new_current = dict(current)
                    if value in (None, ""):
                        new_current.pop(loc, None)
                    else:
                        new_current[loc] = value
                    if new_current != raw_attributes.get(attr_name):
                        changed = True
                    if new_current:
                        new_attrs[attr_name] = new_current
                    else:
                        new_attrs.pop(attr_name, None)
                else:
                    if value in (None, ""):
                        if attr_name in new_attrs:
                            changed = True
                            new_attrs.pop(attr_name, None)
                    elif value != raw_attributes.get(attr_name):
                        changed = True
                        new_attrs[attr_name] = value
            status = status_select.value if status_select.value else None
            if status is not None and status != resolved.get("status"):
                changed = True
            if not changed:
                ui.notify(t("grid.no_changes"), color="info")
                return
            try:
                await client.update_object(
                    token, object_id, attributes=new_attrs, status=status, locale=locale
                )
                ui.notify(t("form.saved"), color="positive")
                ui.navigate.reload()
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("form.save_failed"), color="negative")

        ui.button(t("grid.save"), icon="save", on_click=save).classes("bg-primary text-white mt-4")

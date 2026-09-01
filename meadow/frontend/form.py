"""Product form (drill-down): deep editing of a single object.

Phase 7, step 6. A single scrollable page with three collapsible sections:

1. **Stammdaten** – object id (read-only), the virtual ``name`` and the
   business-key / required attributes for the object's type.
2. **Attribute ohne Gruppe** – attributes that carry no ``group_id``.
3. **Attributgruppen** – the remaining attributes, grouped and sorted by the
   group's ``position``.

Multilingual editing uses a hybrid model: a "Haupteditiersprache" selector in
the header picks which language is edited inline for every multilingual
attribute, and a per-attribute language-icon button opens a dialog to edit all
data languages (from ``GET /languages``); the dialog reads/writes the session's
working copy so applied values persist on reopen. Attribute rows are rendered
as an aligned, table-like grid (label | value | language icon) inside
``ui.expansion`` sections with coloured headers. Saves via ``PATCH /objects/{id}``
and surfaces the core's validation error (e.g. ``height: Height is required``)
instead of only showing a generic failure message.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.grid import completeness_badge, resolve_value, status_badge
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell

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


def _is_summary(defn: dict | None, object_type: str) -> bool:
    """Whether an attribute belongs to the "Stammdaten" section.

    The virtual ``name`` always does; otherwise it is a summary attribute when
    it is a business key (``is_key``) or required for the object's type
    (``required_types``).
    """
    if defn is None:
        return True
    if defn.get("is_key"):
        return True
    return object_type in (defn.get("required_types") or [])


def _is_required(defn: dict | None, object_type: str) -> bool:
    """Whether an attribute is required for the object's type."""
    if defn is None:
        return False
    return object_type in (defn.get("required_types") or [])


def _base_lang(locale: str) -> str:
    """Returns the base language code of a locale (e.g. ``de-DE`` -> ``de``)."""
    return locale.split("-")[0]


def _raw_value(raw_attributes: dict, name: str, lang: str) -> object | None:
    """Returns the raw own value of an attribute for a language (or scalar)."""
    value = raw_attributes.get(name)
    if isinstance(value, dict):
        return value.get(lang)
    return value


def _coerce(atype: str, raw: object) -> object:
    """Coerces a dialog string to the attribute's native type."""
    if raw in (None, ""):
        return None
    if atype == "number":
        try:
            return float(raw)
        except TypeError, ValueError:
            return raw
    if atype == "boolean":
        return str(raw).strip().lower() in ("true", "1", "on", "yes")
    return str(raw)


@ui.page("/products/{object_id}", response_timeout=15)
async def product_form(object_id: str) -> None:
    """Opens a single object for deep editing."""
    build_shell(active="/products")
    locale = current_locale()
    edit_lang = app.storage.user.get("edit_lang") or _base_lang(locale)

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
            languages = await client.list_languages(token)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("form.load_failed")).classes("text-subtitle1 text-negative")
            return

        object_type = str(resolved.get("type", ""))
        raw_attributes = dict(raw.get("attributes") or {})
        resolved_attributes = dict(resolved.get("attributes") or {})
        name = resolve_value(resolved_attributes.get("name"), locale) or object_type
        missing = resolved.get("missing_required") or []
        completeness = (resolved.get("completeness") or {}).get(locale, 0)

        # Data languages (base languages for the selector, all for the dialog).
        base_langs = [
            lang
            for lang in languages
            if lang.get("parent") == "lang_root" and lang.get("code") != "root"
        ]
        all_langs = [lang for lang in languages if lang.get("code") != "root"]
        data_lang_codes = [str(lang.get("code")) for lang in all_langs]
        if edit_lang not in data_lang_codes:
            edit_lang = _base_lang(locale)

        # --- Header: main-edit-language selector, name, type, badges. --------
        lang_options = {
            str(lang.get("code")): resolve_value(lang.get("label"), locale) for lang in base_langs
        }
        with ui.row().classes("items-center gap-3 w-full mt-2 flex-wrap"):
            ui.select(
                lang_options,
                value=edit_lang,
                on_change=lambda e: on_lang_change(e.value),
            ).props("outline dense").classes("w-40").tooltip(t("form.edit_language"))
            ui.label(name).classes("text-h5")
            ui.label(object_type).classes("text-caption text-gray-500")
            ui.html(status_badge(str(resolved.get("status", "")))).classes("text-sm")
            ui.html(completeness_badge(int(completeness))).classes("text-sm").tooltip(
                t("form.missing_required").format(count=len(missing))
            )

        # Working copy of the raw (own) attribute values. Multilingual values
        # are kept as ``{lang: value}`` dicts.
        edited: dict[str, object] = {}
        for key, value in raw_attributes.items():
            edited[key] = dict(value) if isinstance(value, dict) else value
        main_widgets: dict[str, ui.element] = {}

        def set_value(attr_name: str, lang: str, value: object, *, multilingual: bool) -> None:
            """Updates the working copy for a single attribute + language."""
            if multilingual:
                current = edited.get(attr_name)
                new = dict(current) if isinstance(current, dict) else {}
                if value in (None, ""):
                    new.pop(lang, None)
                else:
                    new[lang] = value
                if new:
                    edited[attr_name] = new
                else:
                    edited.pop(attr_name, None)
            else:
                if value in (None, ""):
                    edited.pop(attr_name, None)
                else:
                    edited[attr_name] = value

        def on_lang_change(new_value: str) -> None:
            if new_value:
                app.storage.user["edit_lang"] = new_value
            ui.navigate.reload()

        def build_value_widget(
            attr_name: str, defn: dict | None, value: object, *, multilingual: bool
        ) -> None:
            """Creates the inline value widget for the main-edit language."""
            atype = (defn or {}).get("type", "text")

            def on_change(e) -> None:
                set_value(attr_name, edit_lang, e.value, multilingual=multilingual)

            if atype == "number":
                field: ui.element = (
                    ui.number(value=value).props("filled dense").on_value_change(on_change)
                )
            elif atype == "boolean":
                field = ui.switch(value=bool(value)).props("dense").on_value_change(on_change)
            elif atype == "enum":
                options = ((defn or {}).get("constraints") or {}).get("enum_values") or []
                field = (
                    ui.select(options, value=value).props("filled dense").on_value_change(on_change)
                )
            elif atype == "date":
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("filled dense type=date")
                    .on_value_change(on_change)
                )
            else:
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("filled dense")
                    .on_value_change(on_change)
                )
            field.classes("flex-1")
            main_widgets[attr_name] = field

        def render_section(
            title: str,
            items: list[tuple[str, dict | None]],
            *,
            open_: bool = True,
            id_label: str | None = None,
        ) -> None:
            """Renders a collapsible section as an aligned attribute table."""
            with (
                ui.expansion(title, value=open_)
                .classes("w-full")
                .props("header-class=bg-green-2 text-green-9"),
                ui.column().classes("w-full gap-0 p-2"),
            ):
                if id_label is not None:
                    with ui.row().classes("items-center gap-2 w-full py-1"):
                        ui.label(t("form.object_id")).classes(
                            "w-56 shrink-0 text-caption text-gray-500"
                        )
                        ui.label(id_label).classes("text-caption text-gray-500")
                # Column header.
                with ui.row().classes("items-center gap-2 w-full py-1 text-caption text-gray-500"):
                    ui.label(t("form.column_attr")).classes("w-56 shrink-0")
                    ui.label(t("form.column_value")).classes("flex-1")
                    ui.label("").classes("w-10 shrink-0")
                for attr_name, defn in items:
                    multilingual = bool(defn is None or (defn or {}).get("multilingual"))
                    label = _label(defn, attr_name, locale)
                    own = _raw_value(raw_attributes, attr_name, edit_lang)
                    required = _is_required(defn, object_type)
                    with ui.row().classes("items-center gap-2 w-full py-1"):
                        # Label cell (+ required marker, inherited marker).
                        with ui.row().classes("items-center gap-1 shrink-0 w-56"):
                            ui.label(label).classes("text-caption")
                            if required:
                                ui.label("*").classes("text-caption text-negative").tooltip(
                                    t("form.required")
                                )
                            if not multilingual and own is None:
                                resolved_val = _raw_value(resolved_attributes, attr_name, edit_lang)
                                if resolved_val is not None:
                                    ui.icon("swap_horiz").classes("text-gray-400 text-xs").tooltip(
                                        t("form.inherited")
                                    )
                        # Value cell in the main-edit language.
                        value = own
                        if value is None:
                            value = _raw_value(resolved_attributes, attr_name, edit_lang)
                        build_value_widget(attr_name, defn, value, multilingual=multilingual)
                        # Language cell (icon only) opens the language editor.
                        if multilingual:
                            ui.button(icon="translate").props("dense flat round").on_click(
                                lambda n=attr_name, d=defn: open_lang_dialog(n, d)
                            ).tooltip(t("form.languages_edit"))

        # --- Language dialog: edit one attribute across all data languages. --
        def open_lang_dialog(attr_name: str, defn: dict | None) -> None:
            atype = (defn or {}).get("type", "text")
            multilingual = bool(defn is None or (defn or {}).get("multilingual"))
            with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-[90vw]"):
                ui.label(f"{_label(defn, attr_name, locale)} · {t('form.languages_edit')}").classes(
                    "text-subtitle1"
                )
                inputs: dict[str, ui.element] = {}
                for lang in all_langs:
                    code = str(lang.get("code"))
                    own = _raw_value(edited, attr_name, code)
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.label(code).classes("w-24 shrink-0 text-caption")
                        inputs[code] = (
                            ui.input(value="" if own is None else str(own))
                            .props("filled dense")
                            .classes("flex-1")
                        )
                with ui.row().classes("justify-end w-full mt-2 gap-2"):
                    ui.button(t("form.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("form.apply")).props("flat").on_click(
                        lambda: apply_languages(attr_name, inputs, atype, multilingual, dialog)
                    )
            dialog.open()

        def apply_languages(
            attr_name: str,
            inputs: dict[str, ui.element],
            atype: str,
            multilingual: bool,
            dialog: ui.dialog,
        ) -> None:
            for code, field in inputs.items():
                set_value(
                    attr_name,
                    code,
                    _coerce(atype, field.value),
                    multilingual=multilingual,
                )
            # Reflect the change in the inline main-edit-language widget.
            widget = main_widgets.get(attr_name)
            if widget is not None:
                widget.value = _raw_value(edited, attr_name, edit_lang)
            dialog.close()

        # Attribute definitions applicable to this object type.
        defs = [d for d in definitions if d.get("name") and _applicable(d, object_type)]
        defs_sorted = sorted(defs, key=lambda d: str(d.get("name", "")))
        group_by_id = {str(g.get("id")): g for g in groups}

        # Partition into summary, ungrouped and grouped.
        summary_items: list[tuple[str, dict | None]] = [("name", None)]
        ungrouped_items: list[tuple[str, dict | None]] = []
        grouped: dict[str, list[tuple[str, dict | None]]] = {}
        for d in defs_sorted:
            name_ = str(d.get("name"))
            if _is_summary(d, object_type):
                summary_items.append((name_, d))
            else:
                gid = str(d.get("group_id") or "")
                if not gid:
                    ungrouped_items.append((name_, d))
                else:
                    grouped.setdefault(gid, []).append((name_, d))
        ordered_groups = sorted(
            grouped.items(),
            key=lambda kv: (
                int(group_by_id[kv[0]].get("position") or 0) if kv[0] in group_by_id else 0,
                kv[0],
            ),
        )

        # --- Render the three sections. --------------------------------------
        with ui.column().classes("w-full gap-2 mt-4"):
            render_section(t("form.summary"), summary_items, open_=True, id_label=object_id)
            if ungrouped_items:
                render_section(t("form.ungrouped"), ungrouped_items, open_=False)
            for gid, items in ordered_groups:
                group = group_by_id.get(gid)
                render_section(
                    _label(group, gid, locale) if group else t("form.general"),
                    items,
                    open_=False,
                )

        # --- Status selector. -------------------------------------------------
        with ui.row().classes("items-center gap-2 w-full mt-4"):
            ui.label(t("form.status")).classes("w-56 text-caption")
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
                k: (dict(v) if isinstance(v, dict) else v) for k, v in edited.items()
            }
            changed = new_attrs != raw_attributes
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
            except CoreError as error:
                ui.notify(t("form.save_failed_reason").format(reason=error), color="negative")
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("form.save_failed"), color="negative")

        ui.button(t("grid.save"), icon="save", on_click=save).classes("bg-primary text-white mt-4")

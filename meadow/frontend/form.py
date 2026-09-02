"""Product form (drill-down): deep editing of a single object.

Phase 7, step 6. A single scrollable page with three collapsible sections:

1. **Stammdaten** – object id (read-only), the virtual ``name`` and the
   business-key / required attributes for the object's type.
2. **Attribute ohne Gruppe** – attributes that carry no ``group_ids``.
3. **Attributgruppen** – the remaining attributes, grouped and sorted by the
   group's ``position``.

Multilingual editing uses a hybrid model: a "Haupteditiersprache" selector in
the header picks which language is edited inline for every multilingual
attribute, and a per-attribute language-icon button opens a dialog to edit all
data languages (from ``GET /languages``); the dialog reads/writes the session's
working copy so applied values persist on reopen. Attribute rows are rendered
as an aligned, table-like grid (label | value | language icon) inside
``ui.expansion`` sections with coloured headers. A fourth section, **Relationen**,
shows the object's graph-edge relations (composition ``depends_on`` + aggregations)
grouped by relation type. Saves via ``PATCH /objects/{id}``
and surfaces the core's validation error (e.g. ``height: Height is required``)
instead of only showing a generic failure message.
"""

from __future__ import annotations

import inspect

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
            relation_definitions = await client.list_relation_definitions(token)
            references = await client.list_object_references(token, object_id, locale=locale)
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
        with ui.row().classes("items-center gap-3 w-full mt-2"):
            ui.space()
            ui.label(name).classes("text-h5")
            ui.label(object_type).classes("text-caption text-gray-500")
            ui.html(status_badge(str(resolved.get("status", "")))).classes("text-sm")
            ui.html(completeness_badge(int(completeness))).classes("text-sm").tooltip(
                t("form.missing_required").format(count=len(missing))
            )
            ui.space()
            ui.select(
                lang_options,
                value=edit_lang,
                on_change=lambda e: on_lang_change(e.value),
            ).props("filled dense").classes("w-40").tooltip(t("form.edit_language"))

        # Working copy of the raw (own) attribute values. Multilingual values
        # are kept as ``{lang: value}`` dicts.
        edited: dict[str, object] = {}
        for key, value in raw_attributes.items():
            edited[key] = dict(value) if isinstance(value, dict) else value
        main_widgets: dict[str, ui.element] = {}
        ref_labels: dict[str, dict] = {}

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

        def open_enum_creation(attr_name: str, defn: dict | None, field: ui.element) -> None:
            """Adds a new enum option to the attribute definition (MVP)."""
            multilingual = bool(defn is not None and (defn or {}).get("multilingual"))

            with ui.dialog() as dialog, ui.card().classes("w-[24rem] max-w-[90vw]"):
                ui.label(t("form.add_option")).classes("text-subtitle1")
                inp = ui.input(label=t("form.new_option")).props("filled dense").classes("w-full")

                async def create() -> None:
                    value = (inp.value or "").strip()
                    if not value:
                        return
                    constraints = dict((defn or {}).get("constraints") or {})
                    current = constraints.get("enum_values") or []
                    if value in current:
                        ui.notify(t("form.option_exists"), color="info")
                        dialog.close()
                        return
                    new_values = list(current) + [value]
                    constraints["enum_values"] = new_values
                    try:
                        await client.update_attribute_definition(
                            token, defn["id"], constraints=constraints
                        )
                    except CoreError as error:
                        ui.notify(
                            t("form.save_failed_reason").format(reason=error), color="negative"
                        )
                        dialog.close()
                        return
                    field.set_options(new_values, value=value)
                    set_value(attr_name, edit_lang, value, multilingual=multilingual)
                    ui.notify(t("form.option_added"), color="positive")
                    dialog.close()

                with ui.row().classes("justify-end w-full mt-2 gap-2"):
                    ui.button(t("form.cancel"), on_click=dialog.close).props("flat")
                    ui.button(t("form.add"), on_click=create).props("flat")
            dialog.open()

        def build_reference_widget(
            attr_name: str, defn: dict | None, value: object, *, multilingual: bool
        ) -> None:
            """Renders a searchable reference picker storing the target object ID."""
            target_types = ((defn or {}).get("constraints") or {}).get("target_types") or None
            existing = ref_labels.get(attr_name)
            if existing is None and value:
                existing = {"id": str(value), "label": str(value), "type": ""}

            container = ui.column().classes("flex-1 gap-1")
            with container:
                search = (
                    ui.input(placeholder=t("form.reference_search"))
                    .props("filled dense")
                    .classes("w-full")
                )
                chip_slot = ui.column().classes("w-full")
                results = ui.column().classes("w-full gap-1")

            def render_chip(ref: dict | None) -> None:
                chip_slot.clear()
                if not ref:
                    return
                with chip_slot, ui.row().classes("items-center gap-2 w-full"):
                    ui.label(f"{ref.get('label')} · {ref.get('type')}").classes(
                        "bg-neutral px-2 py-0.5 rounded text-xs"
                    )
                    ui.button(icon="close").props("flat dense size=sm").on_click(
                        lambda: on_pick(None)
                    ).tooltip(t("grid.ref_remove"))

            def on_pick(hit: dict | None) -> None:
                if hit is None:
                    edited.pop(attr_name, None)
                else:
                    set_value(attr_name, "scalar", hit["id"], multilingual=False)
                render_chip(hit)
                search.value = ""
                results.clear()

            async def on_search(text: str) -> None:
                q = (text or "").strip()
                results.clear()
                if not q:
                    return
                try:
                    hits = await client.picker_search(
                        token, q, types=target_types, locale=locale, limit=10
                    )
                except Exception:  # noqa: BLE001 - degrade to no hits
                    return
                for hit in hits:
                    with results:
                        ui.button(
                            f"{hit.get('label')} · {hit.get('type')}",
                            on_click=lambda h=hit: on_pick(h),
                        ).props("outline dense").classes("w-full justify-start")

            search.on_value_change(lambda e: on_search(e.value))
            render_chip(existing)

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
            elif atype == "reference":
                build_reference_widget(attr_name, defn, value, multilingual=multilingual)
                return
            elif atype == "enum":
                options = ((defn or {}).get("constraints") or {}).get("enum_values") or []
                field = (
                    ui.select(options, value=value).props("filled dense").on_value_change(on_change)
                )
                field.classes("flex-1")
                main_widgets[attr_name] = field
                ui.button(icon="add").props("dense flat round").on_click(
                    lambda: open_enum_creation(attr_name, defn, field)
                ).tooltip(t("form.add_option"))
                return
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
            show_missing_only: bool = False,
        ) -> tuple[ui.expansion, ui.column] | None:
            """Renders a collapsible section as an aligned attribute table."""
            if show_missing_only:
                missing_set = set(missing)
                items = [(n, d) for n, d in items if n in missing_set]
                if not items and id_label is None:
                    return None
            expansion = (
                ui.expansion(title, value=open_)
                .classes("w-full")
                .props("header-class=bg-green-2 text-green-9")
            )
            with expansion:
                content = ui.column().classes("w-full gap-0 p-2")
            with (
                expansion,
                content,
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
            return expansion, content

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

        # --- Relations (graph edges) helpers. --------------------------------
        def rel_label(dk: str) -> str:
            """Resolves a relation-type label (or its short key)."""
            return resolve_value(rel_def_by_key.get(dk, {}).get("label"), locale) or dk

        def rel_kind(dk: str) -> str:
            """Returns the relation kind (`composition` | `aggregation`)."""
            return str(rel_def_by_key.get(dk, {}).get("kind", "aggregation"))

        def kind_badge(dk: str) -> str:
            """Renders a coloured badge for the relation kind."""
            if rel_kind(dk) == "composition":
                cls, text = "bg-orange-2 text-orange-9", t("form.relation_composition")
            else:
                cls, text = "bg-blue-2 text-blue-9", t("form.relation_aggregation")
            return f'<span class="{cls} px-1.5 py-0.5 rounded text-xs">{text}</span>'

        def open_object(obj_id: str) -> None:
            """Navigates to the referenced object's product form."""
            if obj_id:
                ui.navigate.to(f"/products/{obj_id}")

        def build_reference_rows() -> dict[str, list[dict]]:
            """Groups the object's references by relation type (short key)."""
            grouped: dict[str, list[dict]] = {}
            for ref in all_references:
                dk = str(ref.get("definition_id") or "")
                is_out_target = ref.get("out") == object_id
                other = ref.get("in_object") if is_out_target else ref.get("out_object")
                other = other or {}
                role = ""
                if dk == "rel_depends_on":
                    role = "parent" if not is_out_target else "child"
                grouped.setdefault(dk, []).append(
                    {
                        "id": ref.get("id"),
                        "label": other.get("label") or "",
                        "type": other.get("type") or "",
                        "other_id": other.get("id") or "",
                        "role": role,
                    }
                )
            return grouped

        def open_relation_picker(title: str, target_types: list[str] | None, on_pick) -> None:
            """Pick a target object by label search (add aggregation / re-parent)."""
            with ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-[90vw]"):
                ui.label(title).classes("text-subtitle1")
                search = (
                    ui.input(placeholder=t("form.reference_search"))
                    .props("filled dense")
                    .classes("w-full")
                )
                hits = ui.column().classes("w-full gap-1")

                async def choose(hit: dict) -> None:
                    dialog.close()
                    result = on_pick(hit)
                    if inspect.isawaitable(result):
                        await result

                async def do_search(value: str) -> None:
                    q = (value or "").strip()
                    hits.clear()
                    if not q:
                        return
                    try:
                        found = await client.picker_search(
                            token, q, types=target_types, locale=locale, limit=20
                        )
                    except Exception:  # noqa: BLE001 - degrade to no hits
                        return
                    for hit in found:
                        with hits:
                            ui.button(
                                f"{hit.get('label')} · {hit.get('type')}",
                                on_click=lambda h=hit: choose(h),
                            ).props("outline dense").classes("w-full justify-start")

                search.on_value_change(lambda e: do_search(e.value))
                with ui.row().classes("justify-end w-full mt-2"):
                    ui.button(t("form.cancel"), on_click=dialog.close).props("flat")
            dialog.open()

        async def on_add_ref(defn: dict, hit: dict) -> None:
            """Creates an aggregation reference from the current object to a hit."""
            dk = str(defn.get("id", "")).split(":", 1)[-1]
            try:
                await client.create_reference(token, object_id, hit["id"], dk)
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.ref_add_failed"), color="negative")
                return
            ui.notify(t("form.relation_added"), color="positive")
            await reload_relations()

        async def on_remove_ref(reference_id: str) -> None:
            """Deletes an aggregation reference."""
            try:
                await client.delete_reference(token, reference_id)
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.ref_remove_failed"), color="negative")
                return
            await reload_relations()

        async def on_reparent(hit: dict) -> None:
            """Re-parents the object to a new composition parent."""
            try:
                await client.reparent_object(token, object_id, hit["id"])
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("form.relation_reparent_failed"), color="negative")
                return
            ui.notify(t("form.relation_reparented"), color="positive")
            await reload_relations()

        def build_relations_section() -> tuple[ui.expansion, ui.column] | None:
            """Renders the collapsible relations (graph edges) section."""
            if not rel_defs:
                return None
            rows_by_dk = build_reference_rows()
            total = len(all_references)
            expansion = (
                ui.expansion(f"{t('form.relations')} ({total})", value=True)
                .classes("w-full")
                .props("header-class=bg-green-2 text-green-9")
            )
            with expansion:
                content = ui.column().classes("w-full gap-2 p-2")
            with expansion, content:

                def sort_key(dk: str) -> tuple:
                    rank = 0 if dk == "rel_depends_on" else 1
                    return (rank, rel_label(dk).lower())

                for dk in sorted(rel_def_by_key, key=sort_key):
                    defn = rel_def_by_key[dk]
                    kind = rel_kind(dk)
                    rows = rows_by_dk.get(dk, [])
                    target_types = (defn.get("target_types") or []) or None
                    bg = "bg-orange-1" if kind == "composition" else "bg-blue-1"
                    head_bg = "bg-orange-2" if kind == "composition" else "bg-blue-2"
                    with ui.column().classes(f"rounded w-full overflow-hidden mb-1 {bg}"):
                        with ui.row().classes(f"items-center gap-2 w-full px-2 py-1 {head_bg}"):
                            ui.label(rel_label(dk)).classes(
                                "text-caption font-medium text-gray-800"
                            )
                            ui.html(kind_badge(dk))
                            ui.label(str(len(rows))).classes("text-caption text-gray-600")
                            ui.space()
                            if kind == "aggregation":
                                ui.button(
                                    icon="add",
                                    on_click=lambda d=defn: open_relation_picker(
                                        rel_label(str(d.get("id", "")).split(":", 1)[-1]),
                                        (d.get("target_types") or []) or None,
                                        lambda h, dd=d: on_add_ref(dd, h),
                                    ),
                                ).props("dense flat round").tooltip(t("form.relation_add"))
                        with ui.column().classes("w-full gap-1 p-2"):
                            if not rows:
                                ui.label(t("form.relation_none")).classes(
                                    "text-caption text-gray-400 italic"
                                )
                            else:
                                for row in rows:
                                    with ui.row().classes("items-center gap-2 w-full"):
                                        if row["role"] == "parent":
                                            ui.label(t("form.relation_parent")).classes(
                                                "w-16 shrink-0 text-caption text-gray-400"
                                            )
                                        elif row["role"] == "child":
                                            ui.label(t("form.relation_children")).classes(
                                                "w-16 shrink-0 text-caption text-gray-400"
                                            )
                                        else:
                                            ui.label("").classes("w-16 shrink-0")
                                        ui.label(f"{row['label']} · {row['type']}").classes(
                                            "flex-1 text-caption"
                                        )
                                        ui.button(
                                            icon="open_in_new",
                                            on_click=lambda oid=row["other_id"]: open_object(oid),
                                        ).props("flat dense round").tooltip(t("form.relation_open"))
                                        if dk == "rel_depends_on" and row["role"] == "parent":
                                            ui.button(
                                                icon="drive_file_move",
                                                on_click=lambda dk=dk, tt=target_types: (
                                                    open_relation_picker(
                                                        rel_label(dk),
                                                        tt,
                                                        lambda hit: on_reparent(hit),
                                                    )
                                                ),
                                            ).props("flat dense round").tooltip(
                                                t("form.relation_reparent")
                                            )
                                        elif kind == "aggregation":
                                            ui.button(
                                                icon="close",
                                                on_click=lambda rid=row["id"]: on_remove_ref(rid),
                                            ).props("flat dense round").tooltip(
                                                t("grid.ref_remove")
                                            )
            return expansion, content

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
                gids = [str(g) for g in (d.get("group_ids") or []) if g]
                if not gids:
                    ungrouped_items.append((name_, d))
                else:
                    for gid in gids:
                        # An attribute may belong to several groups (same field
                        # on the object) — avoid duplicates within one group.
                        if not any(existing[0] == name_ for existing in grouped.get(gid, [])):
                            grouped.setdefault(gid, []).append((name_, d))
        ordered_groups = sorted(
            grouped.items(),
            key=lambda kv: (
                int(group_by_id[kv[0]].get("position") or 0) if kv[0] in group_by_id else 0,
                kv[0],
            ),
        )

        # Resolve existing reference-attribute values to a display label.
        for d in defs:
            if d.get("type") == "reference":
                name_ = str(d.get("name"))
                val = edited.get(name_)
                if isinstance(val, str) and val:
                    try:
                        target = await client.get_object(token, val, locale=locale)
                        attrs = target.get("attributes") or {}
                        label = (
                            resolve_value(attrs.get("name"), locale)
                            or str(target.get("type", ""))
                            or val
                        )
                        ref_labels[name_] = {
                            "id": val,
                            "label": label,
                            "type": str(target.get("type", "")),
                        }
                    except Exception:  # noqa: BLE001 - fall back to the raw id
                        ref_labels[name_] = {"id": val, "label": val, "type": ""}

        # --- Relations (graph edges): definitions + the object's references. --
        rel_defs: list[dict] = list(relation_definitions)
        all_references: list[dict] = list(references)
        rel_def_by_key: dict[str, dict] = {
            str(d.get("id", "")).split(":", 1)[-1]: d for d in rel_defs
        }

        # --- "Nur fehlende Pflichtfelder" toggle + attribute sections. ------
        show_missing_only = bool(app.storage.user.get("form_show_missing", False))

        @ui.refreshable
        def render_sections() -> None:
            jump_refs: list[tuple[str, ui.expansion, ui.column]] = []

            def go_to(label: str) -> None:
                for lab, exp, content in jump_refs:
                    if lab == label:
                        exp.open()
                        content.run_method("scrollIntoView")
                        break

            with ui.column().classes("w-full gap-2 mt-4"):
                jump = (
                    ui.select([], label=t("form.jump"), on_change=lambda e: go_to(str(e.value)))
                    .props("filled dense")
                    .classes("w-64")
                )
                if show_missing_only and not missing:
                    ui.label(t("form.no_missing")).classes("text-caption text-gray-500")
                else:
                    result = render_section(
                        t("form.summary"),
                        summary_items,
                        open_=True,
                        id_label=object_id,
                        show_missing_only=show_missing_only,
                    )
                    if result is not None:
                        jump_refs.append((t("form.summary"), *result))
                    if ungrouped_items:
                        result = render_section(
                            t("form.ungrouped"),
                            ungrouped_items,
                            open_=False,
                            show_missing_only=show_missing_only,
                        )
                        if result is not None:
                            jump_refs.append((t("form.ungrouped"), *result))
                    for gid, items in ordered_groups:
                        group = group_by_id.get(gid)
                        title = _label(group, gid, locale) if group else t("form.general")
                        result = render_section(
                            title,
                            items,
                            open_=False,
                            show_missing_only=show_missing_only,
                        )
                        if result is not None:
                            jump_refs.append((title, *result))
                # Relations (graph edges) — always visible.
                result = build_relations_section()
                if result is not None:
                    jump_refs.append((t("form.relations"), *result))
                jump.set_options([label for label, _, _ in jump_refs])

        async def reload_relations() -> None:
            """Re-fetches the object's references and re-renders the sections."""
            nonlocal all_references
            try:
                all_references = await client.list_object_references(
                    token, object_id, locale=locale
                )
            except Exception:  # noqa: BLE001 - degrade to an empty list
                all_references = []
            render_sections.refresh()

        def on_missing_toggle(value: bool) -> None:
            nonlocal show_missing_only
            show_missing_only = bool(value)
            app.storage.user["form_show_missing"] = show_missing_only
            render_sections.refresh()

        with ui.row().classes("items-center gap-2 w-full mt-4"):
            ui.switch(
                value=show_missing_only,
                on_change=lambda e: on_missing_toggle(e.value),
            ).props("dense")
            ui.label(t("form.only_missing")).classes("text-caption")

        render_sections()

        # --- Status selector. -------------------------------------------------
        with ui.row().classes("items-center gap-2 w-full mt-4"):
            ui.label(t("form.status")).classes("w-56 text-caption")
            status_select = (
                ui.select(
                    list(_STATUS_OPTIONS),
                    value=str(resolved.get("status", "")),
                )
                .props("filled dense")
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

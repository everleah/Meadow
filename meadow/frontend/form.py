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

import asyncio
import inspect
from functools import partial
from urllib.parse import quote

from nicegui import app, ui

from meadow.frontend import auth, config, edit_queue
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.channels import channel_label
from meadow.frontend.display import (
    RELEASE_STATUSES,
    completeness_badge,
    resolve_value,
    status_badge,
    taxonomy_type_names,
)
from meadow.frontend.enums import enum_options
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell
from meadow.frontend.object_picker import open_object_picker, render_picker_hit
from meadow.frontend.range_values import range_bounds, range_value
from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    ROLE_CATEGORY,
    allows_source,
    category_definitions,
    definition_key,
    effective_target_types,
    role_of,
)
from meadow.frontend.url_values import is_valid_url, open_url

#: Workflow statuses offered in the status selector. The core validates the
#: actual transition via the workflow guard.
_STATUS_OPTIONS = ("draft", "in_review", "published", "archived")

#: Debounce (ms) for the attribute search field: one re-render per typing pause.
_SEARCH_DEBOUNCE_MS = 300

#: Polls of a consolidation job before the "no worker running" hint is shown.
_PENDING_WARN_POLLS = 5

#: Poll interval (s) for a running consolidation job.
_JOB_POLL_SECONDS = 2

#: Session key holding the open/closed state of the form's attribute sections
#: (`{object_type: {section_key: bool}}`), so a reload keeps them as they were.
SECTIONS_STORAGE_KEY = "form_sections"


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


def _num_or_none(value: object) -> float | None:
    """Coerces an edge-attribute value to a number (or None)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _str_or(value: object) -> str:
    """Stringifies an edge-attribute value (empty string for None)."""
    if value is None:
        return ""
    return str(value)


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


def _consolidation_parent(move: dict, locale: str) -> str:
    """Display name of the node a value is pushed up to (id as fallback)."""
    label = resolve_value(move.get("parent_label"), locale)
    return label or str(move.get("parent") or "")


def _consolidation_value(value: object, locale: str) -> str:
    """Display form of a moved value (multilingual maps resolved, empty = dash)."""
    return resolve_value(value, locale) or "–"


@ui.page("/products/{object_id}", response_timeout=15)
async def product_form(object_id: str) -> None:
    """Opens a single object for deep editing."""
    await build_shell(active="/products")
    locale = current_locale()
    # The current UI language is the single "main edit language" for
    # multilingual attributes (no separate selector) — consistent everywhere.
    edit_lang = _base_lang(locale)

    with ui.column().classes("w-full p-6"):
        origin_url = edit_queue.queue_origin()
        in_sequence = edit_queue.is_active(object_id)

        def go_back() -> None:
            """Leaves the form; a running sequence ends here as well."""
            edit_queue.stop_queue()
            ui.navigate.to(origin_url if in_sequence else "/products")

        ui.button(t("form.back"), icon="arrow_back", on_click=go_back).props("flat")

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
            try:
                enum_sets = await client.list_enum_sets(token)
            except Exception:  # noqa: BLE001 - degrade to raw enum codes
                enum_sets = []
            try:
                channels = await client.list_channels(token, active=True)
            except Exception:  # noqa: BLE001 - no channel section without a catalog
                channels = []
            try:
                # Taxonomy node types: the valid targets of a categorisation
                # (built-in `category` plus custom `taxonomy: true` types).
                taxonomy_types = taxonomy_type_names(
                    await client.list_object_types(token, limit=1000)
                )
            except Exception:  # noqa: BLE001 - degrade to the built-in taxonomy
                taxonomy_types = taxonomy_type_names([])
            try:
                # Class features (Stage B): the features of the object's taxonomy
                # classes, incl. the mandatory ones that are still unset.
                class_features = await client.list_object_category_attributes(
                    token, object_id, locale=locale
                )
            except Exception:  # noqa: BLE001 - no class features without classes
                class_features = {}
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("form.load_failed")).classes("text-subtitle1 text-negative")
            return

        object_type = str(resolved.get("type", ""))
        raw_attributes = dict(raw.get("attributes") or {})
        resolved_attributes = dict(resolved.get("attributes") or {})
        # Origin of every inherited value (`{attr: {from, label}}`), reported by
        # the core for `?resolve_inheritance=true`.
        inherited_sources = dict(resolved.get("inherited") or {})
        name = resolve_value(resolved_attributes.get("name"), locale) or object_type
        missing = resolved.get("missing_required") or []
        completeness = (resolved.get("completeness") or {}).get(locale, 0)

        # A leaf has nothing to consolidate, so the action is only offered for
        # nodes that actually have children.
        try:
            has_children = bool(await client.list_object_children(token, object_id, limit=1))
        except Exception:  # noqa: BLE001 - the action is simply not offered
            has_children = False

        # Data languages (base languages for the per-attribute language dialog;
        # only `active` languages are offered for data maintenance).
        all_langs = [
            lang for lang in languages if lang.get("code") != "root" and lang.get("active", True)
        ]
        data_lang_codes = [str(lang.get("code")) for lang in all_langs]
        if edit_lang not in data_lang_codes:
            edit_lang = _base_lang(locale)

        def is_inheritable(defn: dict | None) -> bool:
            """Whether the attribute may inherit (the value-action menu needs that)."""
            return bool((defn or {}).get("inheritable")) and not (defn or {}).get("computed")

        def inherited_source(attr_name: str) -> dict | None:
            """Returns the `{from, label}` origin of an inherited value."""
            source = inherited_sources.get(attr_name)
            return source if isinstance(source, dict) else None

        def attr_state(attr_name: str, defn: dict | None, own: object) -> str:
            """Classifies the value for the marker: own, inherited or interrupted."""
            if own is None and inherited_source(attr_name) is not None:
                return "inherited"
            if attr_name not in edited:
                return "own"
            raw = edited.get(attr_name)
            # The key exists but carries nothing (`null`, empty string or an empty
            # language map) = the value deliberately interrupts inheritance
            # ("leer gesetzt"); a language map filled for *another* language is a
            # normal own value.
            if raw is None or raw == "" or raw == {}:
                return "interrupted"
            return "own"

        def render_value_marker(attr_name: str, defn: dict | None, own: object) -> None:
            """Adds the inheritance/interruption marker next to the label.

            Icon + tooltip are the whole marker: a text badge next to it would
            only cost space without adding information.
            """
            state = attr_state(attr_name, defn, own)
            if state == "inherited":
                source = inherited_source(attr_name) or {}
                origin = str(source.get("label") or source.get("from") or "")
                ui.icon("swap_horiz").classes("text-gray-400 text-xs shrink-0").tooltip(
                    t("form.inherited_from", label=origin)
                )
            elif state == "interrupted":
                ui.icon("link_off").classes("text-warning text-xs shrink-0").tooltip(
                    t("form.interrupted")
                )

        async def apply_value_action(attr_name: str, *, remove: bool) -> None:
            """Removes the own value (back to inherited) or sets it explicitly empty."""
            try:
                if remove:
                    await client.update_object(
                        token, object_id, remove_attributes=[attr_name], locale=locale
                    )
                else:
                    await client.update_object(
                        token, object_id, attributes={attr_name: None}, locale=locale
                    )
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("form.inherit_failed"), color="negative")
                return
            ui.navigate.reload()

        def render_value_actions(attr_name: str, defn: dict | None) -> None:
            """Context menu for inheritance actions of an inheritable attribute."""
            menu_button = ui.button(icon="more_vert").props("dense flat round")
            menu_button.tooltip(t("form.value_actions"))
            with menu_button, ui.menu():
                ui.menu_item(
                    t("form.reset_inherited"),
                    on_click=lambda n=attr_name: apply_value_action(n, remove=True),
                )
                ui.menu_item(
                    t("form.clear_value"),
                    on_click=lambda n=attr_name: apply_value_action(n, remove=False),
                )

        # --- Header: name, type, status, completeness. The current UI language
        # is the main edit language (no separate language selector). ----------
        with ui.row().classes("items-center gap-3 w-full mt-2"):
            ui.label(name).classes("text-h5")
            ui.label(object_type).classes("text-caption text-gray-500")
            ui.html(status_badge(str(resolved.get("status", "")))).classes("text-sm")
            ui.html(completeness_badge(int(completeness))).classes("text-sm").tooltip(
                t("form.missing_required").format(count=len(missing))
            )
            if has_children and auth.is_editor():
                ui.space()
                ui.button(
                    t("form.consolidate"),
                    icon="arrow_upward",
                    on_click=lambda: open_consolidate_dialog(),
                ).props("flat dense").tooltip(t("consolidate.hint"))

        # --- Sequential editing (queue started in the grid): progress + actions.
        # The lambdas defer resolving `save_and_next`/`skip_object`/`stop_sequence`
        # until the click, because the save logic is defined further below.
        queue_ids = edit_queue.queue_ids() if in_sequence else []
        queue_position = edit_queue.position_of(queue_ids, object_id)
        if queue_position is not None:
            with (
                ui.column().classes("w-full gap-1 mt-3"),
                ui.row().classes("items-center gap-3 w-full").style("flex-wrap: nowrap"),
            ):
                ui.label(
                    t("seq.position").format(index=queue_position + 1, total=len(queue_ids))
                ).classes("text-caption text-gray-600 shrink-0")
                ui.linear_progress(value=(queue_position + 1) / len(queue_ids)).classes("grow")
                ui.button(t("seq.save_next"), icon="save", on_click=lambda: save_and_next()).props(
                    "flat dense"
                )
                ui.button(t("seq.skip"), icon="skip_next", on_click=lambda: skip_object()).props(
                    "flat dense"
                )
                ui.button(icon="close", on_click=lambda: stop_sequence()).props(
                    "flat dense"
                ).tooltip(t("seq.stop"))

        # Working copy of the raw (own) attribute values. Multilingual values
        # are kept as ``{lang: value}`` dicts.
        edited: dict[str, object] = {}
        for key, value in raw_attributes.items():
            edited[key] = dict(value) if isinstance(value, dict) else value
        main_widgets: dict[str, ui.element] = {}
        ref_labels: dict[str, dict] = {}
        # Pinned attributes (per object type, per user): the sticky sidebar keeps
        # them visible while scrolling. `pin_buttons` holds the rendered toggles
        # so a pin can restyle every occurrence without re-rendering the sections
        # (which would collapse the current open/closed state).
        pinned_by_type: dict = dict(app.storage.user.get("pinned_attributes") or {})
        pinned: list[str] = list(pinned_by_type.get(object_type) or [])
        pin_buttons: dict[str, list[ui.button]] = {}

        def is_pinned(attr_name: str) -> bool:
            return attr_name in pinned

        def style_pin_button(button: ui.button, attr_name: str) -> None:
            """Marks a pin toggle as active (filled/primary) or inactive."""
            if is_pinned(attr_name):
                button.props("color=primary", remove="flat")
            else:
                button.props("flat", remove="color")

        def toggle_pin(attr_name: str) -> None:
            """Pins/unpins an attribute and persists the set for this object type."""
            if is_pinned(attr_name):
                pinned.remove(attr_name)
            else:
                pinned.append(attr_name)
            pinned_by_type[object_type] = list(pinned)
            app.storage.user["pinned_attributes"] = pinned_by_type
            for button in pin_buttons.get(attr_name, []):
                style_pin_button(button, attr_name)
            render_pinned.refresh()

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
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    search = (
                        ui.input(placeholder=t("form.reference_search"))
                        .props("filled dense")
                        .classes("grow")
                    )
                    # Same picker as the relations (search + structure tree).
                    ui.button(
                        icon="account_tree",
                        on_click=lambda: open_object_picker(
                            client=client,
                            token=token,
                            locale=locale,
                            title=t("form.pick_target"),
                            on_pick=on_pick,
                            target_types=target_types,
                            exclude_ids=[object_id],
                        ),
                    ).props("dense flat round").tooltip(t("form.pick_in_structure"))
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
                        render_picker_hit(hit, on_pick, locale)

            search.on_value_change(lambda e: on_search(e.value))
            render_chip(existing)

        def build_value_widget(
            attr_name: str,
            defn: dict | None,
            value: object,
            *,
            multilingual: bool,
            register_main: bool = True,
            dim: bool = False,
        ) -> None:
            """Creates the inline value widget for the main-edit language.

            ``register_main=False`` is used by the pinned sidebar: the sidebar
            writes to the same working copy but must not own the inline widget
            handle (`main_widgets`), which the language dialog updates.
            ``dim`` greys the field: the value is inherited, not maintained here.
            """
            atype = (defn or {}).get("type", "text")

            def on_change(e) -> None:
                set_value(attr_name, edit_lang, e.value, multilingual=multilingual)

            if atype == "range":
                # Two bounds in one row. Numbers are never multilingual, so the
                # language dialog does not mirror this field (`main_widgets`).
                low, high = range_bounds(value)
                with ui.row().classes("items-center gap-1 flex-1"):
                    from_field = (
                        ui.number(value=low)
                        .props(f'filled dense label="{t("form.range_from")}"')
                        .classes("w-28")
                    )
                    ui.label("–").classes("text-gray-500")
                    to_field = (
                        ui.number(value=high)
                        .props(f'filled dense label="{t("form.range_to")}"')
                        .classes("w-28")
                    )

                    def on_bounds_change(_event=None) -> None:
                        set_value(
                            attr_name,
                            edit_lang,
                            range_value(from_field.value, to_field.value),
                            multilingual=multilingual,
                        )

                    from_field.on_value_change(on_bounds_change)
                    to_field.on_value_change(on_bounds_change)
                if dim:
                    for element in (from_field, to_field):
                        element.style("opacity: 0.65")
                return
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
                opts = enum_options(enum_sets, defn, edit_lang)
                labels = {o["code"]: o["label"] for o in opts}
                cur = "" if value is None else str(value)
                if cur and cur not in labels:
                    # Keep any stored value that is not (yet) an option so the
                    # editor does not lose it (e.g. a code removed from the set).
                    labels[cur] = cur
                field = (
                    ui.select({c: labels[c] for c in labels}, value=cur or None)
                    .props("filled dense")
                    .on_value_change(on_change)
                )
                field.classes("flex-1")
                if register_main:
                    main_widgets[attr_name] = field
                ui.button(icon="add").props("dense flat round").on_click(
                    lambda: ui.navigate.to("/enum-sets")
                ).tooltip(t("form.manage_enum_sets"))
                return
            elif atype == "date":
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("filled dense type=date")
                    .on_value_change(on_change)
                )
            elif atype == "url":
                # A URL is edited as text and opened next to the field: images
                # and videos in a dialog, anything else in a new tab.
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("filled dense type=url")
                    .on_value_change(on_change)
                )
                open_button = (
                    ui.button(icon="open_in_new")
                    .props("dense flat round")
                    .tooltip(t("url.open"))
                    .on_click(lambda: open_url(str(field.value or "")))
                )
                open_button.set_enabled(is_valid_url(value))

                def refresh_open_button(event=None) -> None:
                    open_button.set_enabled(is_valid_url(getattr(event, "value", None)))

                field.on_value_change(refresh_open_button)
            else:
                field = (
                    ui.input(value="" if value is None else str(value))
                    .props("filled dense")
                    .on_value_change(on_change)
                )
            field.classes("flex-1")
            if dim:
                # Dim the whole field: Quasar sets the input text color itself, so
                # a color class would not reach the input element.
                field.style("opacity: 0.65")
            if register_main:
                main_widgets[attr_name] = field

        def render_section(
            title: str,
            items: list[tuple[str, dict | None]],
            *,
            section_key: str,
            open_: bool = True,
            force_open: bool = False,
            id_label: str | None = None,
            show_missing_only: bool = False,
        ) -> tuple[ui.expansion, ui.column] | None:
            """Renders a collapsible section as an aligned attribute table.

            ``section_key`` identifies the section for the persisted open/closed
            state, so a save or an inheritance action (both reload the page) does
            not collapse what the user just had open. ``open_`` is only the
            initial default; ``force_open`` (an active attribute search) wins.
            """
            if show_missing_only:
                missing_set = set(missing)
                items = [(n, d) for n, d in items if n in missing_set]
                if not items and id_label is None:
                    return None
            is_open = force_open or section_state.get(section_key, open_)
            expansion = (
                ui.expansion(title, value=is_open)
                .classes("w-full")
                .props("header-class=bg-green-2 text-green-9")
            )
            expansion.on_value_change(
                lambda event, key=section_key: remember_section(key, bool(event.value))
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
                    ui.label("").classes("w-28 shrink-0")
                for attr_name, defn in items:
                    multilingual = bool(defn is None or (defn or {}).get("multilingual"))
                    label = _label(defn, attr_name, locale)
                    # Display the working copy (own values + edits), not the raw
                    # snapshot — otherwise a refresh (search/missing toggle) would
                    # visually revert unstaged edits.
                    own = _raw_value(edited, attr_name, edit_lang)
                    required = _is_required(defn, object_type)
                    state = attr_state(attr_name, defn, own)
                    with ui.row().classes("items-center gap-2 w-full py-1"):
                        # Label cell (+ required marker, inherited marker).
                        with ui.row().classes("items-center gap-1 shrink-0 w-56"):
                            ui.label(label).classes("text-caption")
                            if required:
                                ui.label("*").classes("text-caption text-negative").tooltip(
                                    t("form.required")
                                )
                            render_value_marker(attr_name, defn, own)
                        # Value cell in the main-edit language.
                        value = own
                        if value is None:
                            value = _raw_value(resolved_attributes, attr_name, edit_lang)
                        build_value_widget(
                            attr_name,
                            defn,
                            value,
                            multilingual=multilingual,
                            dim=state == "inherited",
                        )
                        # Action cell: value actions (inheritable), language editor
                        # (multilingual only) + pin toggle.
                        with ui.row().classes("items-center gap-1 shrink-0 w-28 justify-end"):
                            if is_inheritable(defn):
                                render_value_actions(attr_name, defn)
                            if multilingual:
                                ui.button(icon="translate").props("dense flat round").on_click(
                                    lambda n=attr_name, d=defn: open_lang_dialog(n, d)
                                ).tooltip(t("form.languages_edit"))
                            pin_button = (
                                ui.button(icon="push_pin")
                                .props("dense flat round")
                                .on_click(lambda n=attr_name: toggle_pin(n))
                                .tooltip(t("form.pin"))
                            )
                            style_pin_button(pin_button, attr_name)
                            pin_buttons.setdefault(attr_name, []).append(pin_button)
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
            # Reflect the change in the inline main-edit-language widget and in
            # the pinned sidebar (the attribute may be rendered in both).
            widget = main_widgets.get(attr_name)
            if widget is not None:
                widget.value = _raw_value(edited, attr_name, edit_lang)
            render_pinned.refresh()
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
                        "definition_id": dk,
                        "attributes": ref.get("attributes") or {},
                    }
                )
            return grouped

        async def open_relation_picker(title: str, target_types: list[str] | None, on_pick) -> None:
            """Picks a target object via the shared picker (search or structure tree).

            The shared dialog greys out nodes whose type is not an allowed target
            type (and the object itself); see `meadow.frontend.object_picker`.
            """
            await open_object_picker(
                client=client,
                token=token,
                locale=locale,
                title=title,
                on_pick=on_pick,
                target_types=target_types,
                exclude_ids=[object_id],
            )

        async def on_add_ref(defn: dict, hit: dict) -> None:
            """Creates an aggregation reference from the current object to a hit.

            When the relation type declares edge attributes, a typed editor is
            shown first; the collected values are stored on the edge.
            """
            dk = str(defn.get("id", "")).split(":", 1)[-1]

            async def submit(values: dict) -> None:
                try:
                    await client.create_reference(
                        token, object_id, hit["id"], dk, attributes=values or None
                    )
                except Exception:  # noqa: BLE001 - show a friendly message
                    ui.notify(t("grid.ref_add_failed"), color="negative")
                    return
                ui.notify(t("form.relation_added"), color="positive")
                await reload_relations()

            attr_names = [str(a) for a in (defn.get("attributes") or [])]
            if attr_names:
                open_edge_editor(
                    defn,
                    {},
                    f"{rel_label(dk)}: {_label(hit, 'name', locale) or hit.get('type') or ''}",
                    submit,
                )
            else:
                await submit({})

        def on_edit_ref(reference_id: str, defn: dict, current: dict) -> None:
            """Opens the edge-attribute editor for an existing aggregation reference."""

            async def submit(values: dict) -> None:
                try:
                    await client.update_reference(token, reference_id, values or {})
                except Exception:  # noqa: BLE001 - show a friendly message
                    ui.notify(t("grid.ref_edit_failed"), color="negative")
                    return
                ui.notify(t("form.relation_updated"), color="positive")
                await reload_relations()

            open_edge_editor(
                defn,
                current,
                rel_label(str(defn.get("id", "")).split(":", 1)[-1]),
                submit,
            )

        async def on_remove_ref(reference_id: str) -> None:
            """Deletes an aggregation reference."""
            try:
                await client.delete_reference(token, reference_id)
            except Exception:  # noqa: BLE001 - show a friendly message
                ui.notify(t("grid.ref_remove_failed"), color="negative")
                return
            await reload_relations()

        def open_edge_editor(defn: dict, current: dict, title: str, on_save) -> None:
            """Renders a typed dialog for the edge attributes of a relation type.

            Each attribute referenced by the relation definition is rendered with
            an input matching its attribute-definition type (text/number/boolean/
            enum). The collected values are passed to ``on_save`` as a ``dict``.
            """
            attr_names = [str(a) for a in (defn.get("attributes") or [])]
            if not attr_names:
                return
            widget_by_name: dict[str, tuple[object, str]] = {}
            dialog = ui.dialog()

            async def do_save() -> None:
                values: dict = {}
                for name, (widget, atype) in widget_by_name.items():
                    value = widget.value
                    if atype == "number":
                        value = _num_or_none(value)
                    elif atype == "boolean":
                        value = bool(value)
                    values[name] = value
                dialog.close()
                result = on_save(values)
                if inspect.isawaitable(result):
                    await result

            with dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
                ui.label(title).classes("text-subtitle1")
                with ui.column().classes("w-full gap-2 mt-2"):
                    for name in attr_names:
                        adef = rel_attr_defs.get(name)
                        label = _label(adef, name, locale) if adef else name
                        atype = str(adef.get("type") or "text") if adef else "text"
                        current_val = (current or {}).get(name)
                        if atype == "number":
                            widget = (
                                ui.number(label, value=_num_or_none(current_val))
                                .props("filled dense")
                                .classes("w-full")
                            )
                        elif atype == "boolean":
                            widget = ui.switch(label, value=bool(current_val)).props("dense")
                        elif atype == "enum":
                            opts = enum_options(enum_sets, adef, locale)
                            labels = {o["code"]: o["label"] for o in opts}
                            cur = "" if current_val in (None, "") else str(current_val)
                            if cur and cur not in labels:
                                # Preserve an existing value that is not (yet) an
                                # enum option so the editor does not lose it.
                                labels[cur] = cur
                            if labels:
                                widget = (
                                    ui.select(
                                        {c: labels[c] for c in labels},
                                        label=label,
                                        value=cur or None,
                                    )
                                    .props("filled dense")
                                    .classes("w-full")
                                )
                            else:
                                widget = (
                                    ui.input(label, value=cur)
                                    .props("filled dense")
                                    .classes("w-full")
                                )
                        else:
                            widget = (
                                ui.input(label, value=_str_or(current_val))
                                .props("filled dense")
                                .classes("w-full")
                            )
                        widget_by_name[name] = (widget, atype)
                with ui.row().classes("w-full justify-between mt-2 gap-2"):
                    ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                    ui.button(t("grid.save"), icon="save", on_click=do_save).props("flat")

            dialog.open()

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
            # Categorisation is handled in the dedicated "Categories" sections;
            # hide every category relation type here to avoid duplication.
            total = len(
                [
                    r
                    for r in all_references
                    if role_of(rel_def_by_key.get(str(r.get("definition_id")), {})) != ROLE_CATEGORY
                ]
            )
            expansion = (
                ui.expansion(
                    f"{t('form.relations')} ({total})",
                    value=section_state.get("__relations__", True),
                )
                .classes("w-full")
                .props("header-class=bg-green-2 text-green-9")
            )
            expansion.on_value_change(
                lambda event: remember_section("__relations__", bool(event.value))
            )
            with expansion:
                content = ui.column().classes("w-full gap-2 p-2")
            with expansion, content:

                def sort_key(dk: str) -> tuple:
                    rank = 0 if dk == "rel_depends_on" else 1
                    return (rank, rel_label(dk).lower())

                for dk in sorted(rel_def_by_key, key=sort_key):
                    if role_of(rel_def_by_key[dk]) == ROLE_CATEGORY:
                        continue
                    defn = rel_def_by_key[dk]
                    kind = rel_kind(dk)
                    rows = rows_by_dk.get(dk, [])
                    attr_names = [str(a) for a in (defn.get("attributes") or [])]
                    # A usage may fix the target type (`collection`, `category`);
                    # the picker then greys out everything else instead of letting
                    # the user pick an object the core rejects.
                    target_types = effective_target_types(defn)
                    # The current object must be an allowed source for the type;
                    # a type it cannot use is hidden (existing edges still show).
                    if not rows and not allows_source(defn, object_type):
                        continue
                    can_add = kind == "aggregation" and allows_source(defn, object_type)
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
                            if can_add:
                                ui.button(
                                    icon="add",
                                    on_click=lambda d=defn: open_relation_picker(
                                        rel_label(str(d.get("id", "")).split(":", 1)[-1]),
                                        effective_target_types(d),
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
                                        edge_attrs = row.get("attributes") or {}
                                        if isinstance(edge_attrs, dict) and edge_attrs:
                                            summary = ", ".join(
                                                f"{k}={v}" for k, v in edge_attrs.items()
                                            )
                                            ui.label(summary).classes("text-caption text-gray-400")
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
                                            if attr_names:
                                                ui.button(
                                                    icon="edit",
                                                    on_click=partial(
                                                        on_edit_ref,
                                                        row["id"],
                                                        defn,
                                                        row["attributes"],
                                                    ),
                                                ).props("flat dense round").tooltip(
                                                    t("form.relation_edit")
                                                )
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

        # Catalog of the pin-able attributes in display order (the sidebar renders
        # the pinned names from here, so it stays in sync with the sections).
        pin_catalog: dict[str, dict | None] = {}
        for name_, defn in summary_items:
            pin_catalog[name_] = defn
        for name_, defn in ungrouped_items:
            pin_catalog[name_] = defn
        for _, items in ordered_groups:
            for name_, defn in items:
                pin_catalog[name_] = defn

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
        # Attribute definitions by name (used to render typed edge-attribute inputs).
        rel_attr_defs: dict[str, dict] = {
            str(d.get("name")): d for d in definitions if d.get("name")
        }

        # --- Categories (product-centric assignment, vgl. Akeneo) -----------
        # Assignment uses the shared object picker (multi-select, restricted to
        # `category`); assigned categories render as removable chips and each change
        # creates/removes the category edge immediately. Labels come from
        # the picker hits / `GET /objects/{id}` — no full category-tree load.
        # One section per category relation type (primary first); custom category
        # types (e.g. "Marke") keep their own assignment set.
        category_defs = category_definitions(rel_defs)
        cat_state: dict[str, dict] = {}
        cat_ref_ids: dict[str, dict[str, str]] = {
            definition_key(defn): {
                str(ref.get("out")): ref.get("id")
                for ref in all_references
                if ref.get("definition_id") == definition_key(defn)
                and ref.get("out")
                and ref.get("id")
            }
            for defn in category_defs
        }

        async def load_category_labels() -> None:
            """Resolves the display data of the currently assigned categories."""
            for assigned in cat_ref_ids.values():
                for cat_id in assigned:
                    if cat_id in cat_state:
                        continue
                    try:
                        obj = await client.get_object(token, cat_id, locale=locale)
                    except Exception:  # noqa: BLE001 - fall back to the raw id
                        cat_state[cat_id] = {
                            "id": cat_id,
                            "label": cat_id,
                            "type": taxonomy_types[0],
                        }
                        continue
                    cat_state[cat_id] = {
                        "id": cat_id,
                        "label": resolve_value((obj.get("attributes") or {}).get("name"), locale)
                        or cat_id,
                        "type": str(obj.get("type") or taxonomy_types[0]),
                    }

        async def assign_categories(definition: dict) -> None:
            """Adds category edges for the categories picked in the dialog."""
            def_key = definition_key(definition)
            assigned = cat_ref_ids.setdefault(def_key, {})

            async def on_pick(hits: list[dict]) -> None:
                for hit in hits:
                    cat_id = str(hit["id"])
                    cat_state[cat_id] = hit
                    if cat_id in assigned:
                        continue
                    try:
                        created = await client.create_reference(token, object_id, cat_id, def_key)
                    except Exception as exc:  # noqa: BLE001 - show a friendly message
                        ui.notify(str(exc), color="negative")
                        continue
                    if created and created.get("id"):
                        assigned[cat_id] = created["id"]
                render_sections.refresh()

            await open_object_picker(
                client=client,
                token=token,
                locale=locale,
                title=t("form.categories_pick"),
                on_pick=on_pick,
                target_types=effective_target_types(definition, taxonomy_types),
                exclude_ids=[object_id],
                multi=True,
            )

        async def unassign_category(def_key: str, cat_id: str) -> None:
            """Removes one category edge of a category relation type."""
            reference_id = cat_ref_ids.get(def_key, {}).pop(cat_id, None)
            cat_state.pop(cat_id, None)
            if reference_id:
                try:
                    await client.delete_reference(token, reference_id)
                except Exception as exc:  # noqa: BLE001 - show a friendly message
                    ui.notify(str(exc), color="negative")
            render_sections.refresh()

        # --- Channels ("released for that channel") --------------------------
        # A channel release is a workflow action, not part of the saved attribute
        # payload: like the category edges it is written immediately
        # (`PUT`/`DELETE /objects/{id}/releases`), so `workflow_guard` rules can
        # veto a release. The section is hidden without a channel catalog.
        release_state: dict[str, str] = {str(c.get("code")): "" for c in channels}
        if channels:
            try:
                for row in await client.list_object_releases(token, object_id):
                    code = str(row.get("channel") or "")
                    if code in release_state:
                        release_state[code] = str(row.get("status") or "")
            except Exception:  # noqa: BLE001 - show the section as not released
                pass

        async def set_release(code: str, status: str) -> None:
            """Releases the object for a channel (or changes the channel state)."""
            if not status or status == release_state.get(code, ""):
                return
            try:
                await client.set_object_release(token, object_id, channel=code, status=status)
            except Exception as exc:  # noqa: BLE001 - e.g. a `workflow_guard` denial
                ui.notify(f"{t('form.release_failed')}: {exc}", color="negative")
            else:
                release_state[code] = status
                ui.notify(t("form.release_saved", channel=code, status=status), color="positive")
            render_sections.refresh()

        async def withdraw_release(code: str) -> None:
            """Withdraws the release of one channel."""
            try:
                await client.withdraw_object_release(token, object_id, code)
            except Exception as exc:  # noqa: BLE001 - show a friendly message
                ui.notify(f"{t('form.release_failed')}: {exc}", color="negative")
            else:
                release_state[code] = ""
                ui.notify(t("form.release_withdrawn", channel=code), color="positive")
            render_sections.refresh()

        def build_channels_section() -> tuple[ui.expansion, ui.column]:
            """Renders the channel releases of the object (one row per channel)."""
            released = sum(1 for status in release_state.values() if status)
            state_key = "__channels__"
            expansion = (
                ui.expansion(
                    f"{t('form.channels')} ({released})",
                    value=section_state.get(state_key, False),
                )
                .classes("w-full")
                .props("header-class=bg-blue-2 text-blue-9")
            )
            expansion.on_value_change(
                lambda event, key=state_key: remember_section(key, bool(event.value))
            )
            with expansion:
                content = ui.column().classes("w-full gap-2 p-2")
            with expansion, content:
                ui.label(t("form.channels_hint")).classes("text-caption text-gray-500")
                for channel in channels:
                    code = str(channel.get("code") or "")
                    status = release_state.get(code, "")
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.label(channel_label(channel, locale)).classes("w-48")
                        ui.select(
                            {value: value for value in RELEASE_STATUSES},
                            value=status or None,
                            on_change=lambda event, c=code: set_release(c, str(event.value or "")),
                        ).props(f'filled dense placeholder="{t("form.release_none")}"').classes(
                            "w-48"
                        ).tooltip(t("form.release_status_hint"))
                        if status:
                            ui.button(
                                icon="undo",
                                on_click=lambda c=code: withdraw_release(c),
                            ).props("flat dense size=sm").tooltip(t("form.release_withdraw"))
                        else:
                            ui.icon("block").classes("text-gray-300 text-sm shrink-0")
            return expansion, content

        def build_class_features_section() -> tuple[ui.expansion, ui.column]:
            """Renders the class features of the object (Stage B).

            `class_features` is the resolved list of the object's taxonomy classes
            (`GET /objects/{id}/category-attributes`): every feature with its
            required flag and the classes that contribute it. A missing mandatory
            feature is marked, so the section shows *what the classification
            demands* — the same data a `validation`/`workflow_guard` rule sees as
            `ctx.missing_features`. Each row shows the value of the attribute that
            carries the feature (the bound own attribute, or the feature itself).
            """

            def _feature_value(attribute: str) -> str:
                """Displays the feature's value (read-only; edited in its section)."""
                return resolve_value(
                    (resolved.get("attributes") or {}).get(attribute), locale
                ) or t("form.class_features_unset")

            missing = set(class_features.get("missing") or [])
            count = len(class_features.get("attributes") or [])
            state_key = "__class_features__"
            expansion = (
                ui.expansion(
                    f"{t('form.class_features')} ({count})",
                    value=section_state.get(state_key, False),
                )
                .classes("w-full")
                .props("header-class=bg-teal-2 text-teal-9")
            )
            expansion.on_value_change(
                lambda event, key=state_key: remember_section(key, bool(event.value))
            )
            with expansion:
                content = ui.column().classes("w-full gap-1 p-2")
            with expansion, content:
                ui.label(t("form.class_features_hint")).classes("text-caption text-gray-500")
                for feature in class_features.get("attributes") or []:
                    name = str(feature.get("name") or "")
                    # The value lives in the **bound** attribute when the feature
                    # is mapped to an own attribute (`standard_codes`, see
                    # `plan/detail/bmecat-export.md` §11.7); otherwise the feature
                    # name *is* the attribute name (standard-as-attribute path).
                    attribute = str(feature.get("attribute") or name)
                    nodes = [str(node) for node in (feature.get("nodes") or [])]
                    with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                        ui.label(name).classes("grow text-body2")
                        if attribute != name:
                            ui.label(
                                t("form.class_features_bound").format(attribute=attribute)
                            ).classes("text-caption text-gray-500")
                        ui.label(_feature_value(attribute)).classes("text-body2")
                        ui.html(
                            '<span class="px-2 py-0.5 rounded text-xs bg-primary text-white">'
                            + t("categories.feature_required")
                            + "</span>"
                            if feature.get("required")
                            else '<span class="px-2 py-0.5 rounded text-xs bg-neutral '
                            'text-gray-600">' + t("categories.feature_optional") + "</span>"
                        )
                        if feature.get("missing"):
                            ui.html(
                                '<span class="px-2 py-0.5 rounded text-xs bg-negative text-white">'
                                + t("form.class_features_missing")
                                + "</span>"
                            )
                        tooltip = t("form.class_features_source").format(
                            nodes=", ".join(node.split(":")[-1] for node in nodes)
                            or t("grid.bulk_nothing_picked")
                        )
                        ui.icon("category").classes("text-gray-400 text-sm shrink-0").tooltip(
                            tooltip
                        )
                if missing:
                    ui.label(
                        t("form.class_features_missing_hint").format(
                            count=len(missing), names=", ".join(sorted(missing))
                        )
                    ).classes("text-caption text-negative")
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.button(
                        t("form.class_features_manage"),
                        icon="category",
                        on_click=lambda: ui.navigate.to("/categories"),
                    ).props("flat dense")
            return expansion, content

        def build_category_section(definition: dict) -> tuple[ui.expansion, ui.column]:
            """Renders the collapsible section of one category relation type."""
            def_key = definition_key(definition)
            assigned = cat_ref_ids.setdefault(def_key, {})
            # The primary type keeps the established title; custom types are
            # labelled with their own (multilingual) name.
            title = (
                t("form.categories")
                if def_key == PRIMARY_CATEGORY_DEF
                else (resolve_value(definition.get("label"), locale) or def_key)
            )
            state_key = f"__categories__:{def_key}"
            expansion = (
                ui.expansion(
                    f"{title} ({len(assigned)})",
                    value=section_state.get(state_key, False),
                )
                .classes("w-full")
                .props("header-class=bg-blue-2 text-blue-9")
            )
            expansion.on_value_change(
                lambda event, key=state_key: remember_section(key, bool(event.value))
            )
            with expansion:
                content = ui.column().classes("w-full gap-2 p-2")
            with expansion, content:
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    ui.label(t("form.categories_hint")).classes("grow text-caption text-gray-500")
                    ui.button(
                        t("form.categories_pick"),
                        icon="account_tree",
                        on_click=lambda d=definition: assign_categories(d),
                    ).props("flat")
                if not assigned:
                    ui.label(t("form.categories_none")).classes("text-caption text-gray-500")
                else:
                    for cat_id in assigned:
                        hit = cat_state.get(cat_id) or {"id": cat_id, "label": cat_id}
                        with ui.row().classes("items-center gap-2 w-full"):
                            ui.label(f"{hit.get('label')} · {hit.get('type')}").classes(
                                "bg-neutral px-2 py-0.5 rounded text-xs"
                            )
                            ui.button(
                                icon="close",
                                on_click=lambda c=cat_id, d=def_key: unassign_category(d, c),
                            ).props("flat dense size=sm").tooltip(t("grid.ref_remove"))
            return expansion, content

        # --- "Nur fehlende Pflichtfelder" toggle + attribute search + sections.
        show_missing_only = bool(app.storage.user.get("form_show_missing", False))
        attribute_search = {"term": ""}
        section_state = dict(
            (app.storage.user.get(SECTIONS_STORAGE_KEY) or {}).get(object_type) or {}
        )

        def remember_section(section_key: str, is_open: bool) -> None:
            """Persists a section's open/closed state for the current object type."""
            section_state[section_key] = is_open
            stored = dict(app.storage.user.get(SECTIONS_STORAGE_KEY) or {})
            stored[object_type] = section_state
            app.storage.user[SECTIONS_STORAGE_KEY] = stored

        def on_attribute_search(value: str | None) -> None:
            attribute_search["term"] = (value or "").strip()
            render_sections.refresh()

        @ui.refreshable
        def render_sections() -> None:
            jump_refs: list[tuple[str, ui.expansion, ui.column]] = []
            # Widget registries are rebuilt below — stale handles of removed
            # elements (sections, pin toggles) must not be updated later.
            pin_buttons.clear()
            main_widgets.clear()

            def go_to(label: str) -> None:
                for lab, exp, content in jump_refs:
                    if lab == label:
                        exp.open()
                        content.run_method("scrollIntoView")
                        break

            # Attribute search (left) + "jump to" selector + "only missing" switch.
            with ui.row().classes("items-center gap-2 w-full mt-4"):
                search = (
                    ui.input(
                        placeholder=t("form.search_attributes"),
                        value=attribute_search["term"],
                    )
                    .props(f"outline dense clearable debounce={_SEARCH_DEBOUNCE_MS}")
                    .classes("w-72")
                )
                search.on_value_change(lambda e: on_attribute_search(e.value))
                jump = (
                    ui.select([], label=t("form.jump"), on_change=lambda e: go_to(str(e.value)))
                    .props("filled dense")
                    .classes("w-64")
                )
                ui.space()
                ui.switch(
                    value=show_missing_only,
                    on_change=lambda e: on_missing_toggle(e.value),
                ).props("dense")
                ui.label(t("form.only_missing")).classes("text-caption")

            # Attribute search: filter the rows by (localized) label or name; the
            # matching sections open so every hit is visible right away.
            term = attribute_search["term"].lower()

            def matches(attr_name: str, defn: dict | None) -> bool:
                if not term:
                    return True
                return term in attr_name.lower() or term in _label(defn, attr_name, locale).lower()

            def select(items: list[tuple[str, dict | None]]) -> list[tuple[str, dict | None]]:
                return [item for item in items if matches(*item)]

            summary_view = select(summary_items) if term else summary_items
            ungrouped_view = select(ungrouped_items)
            groups_view = [(gid, select(items)) for gid, items in ordered_groups]
            hits = (
                len(summary_view)
                + len(ungrouped_view)
                + sum(len(items) for _, items in groups_view)
            )

            with ui.column().classes("w-full gap-2 mt-2"):
                if term:
                    ui.label(
                        t("form.search_hits").format(count=hits)
                        if hits
                        else t("form.search_no_hits")
                    ).classes("text-caption text-gray-500")
                if show_missing_only and not missing:
                    ui.label(t("form.no_missing")).classes("text-caption text-gray-500")
                else:
                    if summary_view or not term:
                        result = render_section(
                            t("form.summary"),
                            summary_view,
                            section_key="__summary__",
                            open_=True,
                            id_label=object_id,
                            show_missing_only=show_missing_only,
                        )
                        if result is not None:
                            jump_refs.append((t("form.summary"), *result))
                    if ungrouped_view:
                        result = render_section(
                            t("form.ungrouped"),
                            ungrouped_view,
                            section_key="__ungrouped__",
                            open_=False,
                            force_open=bool(term),
                            show_missing_only=show_missing_only,
                        )
                        if result is not None:
                            jump_refs.append((t("form.ungrouped"), *result))
                    for gid, items in groups_view:
                        if not items:
                            continue
                        group = group_by_id.get(gid)
                        title = _label(group, gid, locale) if group else t("form.general")
                        result = render_section(
                            title,
                            items,
                            section_key=f"group:{gid}",
                            open_=False,
                            force_open=bool(term),
                            show_missing_only=show_missing_only,
                        )
                        if result is not None:
                            jump_refs.append((title, *result))
                # Categories (product-centric assignment) — one section per
                # category relation type, the built-in primary type first.
                for definition in category_defs:
                    def_key = definition_key(definition)
                    title = (
                        t("form.categories")
                        if def_key == PRIMARY_CATEGORY_DEF
                        else (resolve_value(definition.get("label"), locale) or def_key)
                    )
                    result = build_category_section(definition)
                    jump_refs.append((title, *result))
                # Class features (Stage B) — shown when the object's classes
                # define features at all.
                if class_features.get("attributes"):
                    result = build_class_features_section()
                    jump_refs.append((t("form.class_features"), *result))
                # Channels (channel-specific releases) — hidden without a catalog.
                if channels:
                    result = build_channels_section()
                    jump_refs.append((t("form.channels"), *result))
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

        @ui.refreshable
        def render_pinned() -> None:
            """Sticky sidebar with the attributes pinned for this object type."""
            with ui.card().classes("w-full p-3 gap-2"):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.icon("push_pin").classes("text-primary")
                    ui.label(t("form.pinned")).classes("text-subtitle2")
                visible = [name for name in pinned if name in pin_catalog]
                if not visible:
                    ui.label(t("form.pinned_empty")).classes("text-caption text-gray-500")
                    return
                for attr_name in visible:
                    defn = pin_catalog[attr_name]
                    multilingual = bool(defn is None or defn.get("multilingual"))
                    own = _raw_value(edited, attr_name, edit_lang)
                    state = attr_state(attr_name, defn, own)
                    with ui.column().classes("w-full gap-1 border-t pt-1"):
                        with ui.row().classes("items-center gap-1 w-full"):
                            ui.label(_label(defn, attr_name, locale)).classes("text-caption")
                            if _is_required(defn, object_type):
                                ui.label("*").classes("text-caption text-negative").tooltip(
                                    t("form.required")
                                )
                            render_value_marker(attr_name, defn, own)
                            ui.space()
                            if is_inheritable(defn):
                                render_value_actions(attr_name, defn)
                            if multilingual:
                                ui.button(icon="translate").props(
                                    "dense flat round size=sm"
                                ).on_click(
                                    lambda n=attr_name, d=defn: open_lang_dialog(n, d)
                                ).tooltip(t("form.languages_edit"))
                            ui.button(icon="push_pin").props(
                                "dense flat round size=sm color=primary"
                            ).on_click(lambda n=attr_name: toggle_pin(n)).tooltip(t("form.unpin"))
                        value = own
                        if value is None:
                            value = _raw_value(resolved_attributes, attr_name, edit_lang)
                        build_value_widget(
                            attr_name,
                            defn,
                            value,
                            multilingual=multilingual,
                            register_main=False,
                            dim=state == "inherited",
                        )

        def on_missing_toggle(value: bool) -> None:
            nonlocal show_missing_only
            show_missing_only = bool(value)
            app.storage.user["form_show_missing"] = show_missing_only
            render_sections.refresh()

        # Resolve the labels of the already assigned categories once (the chips in
        # the categories section render from it).
        await load_category_labels()

        # Attribute sections (left) + pinned-attributes sidebar (right, sticky so
        # the pinned fields stay visible while the form scrolls). The sidebar is
        # built first so the sections own the inline widgets (`main_widgets`) for
        # the language dialog.
        with ui.row().classes("w-full items-start gap-4").style("flex-wrap: nowrap"):
            sections_column = ui.column().classes("grow min-w-0")
            with (
                ui.column()
                .classes("w-80 shrink-0")
                .style(
                    "position: sticky; top: 0.5rem; "
                    "max-height: calc(100vh - 5rem); overflow-y: auto"
                )
            ):
                render_pinned()
            with sections_column:
                render_sections()

        # --- Consolidate inheritance (Phase 9). ------------------------------
        # Identical values of *all* children are pushed up to this node and
        # removed from the children, so they inherit again instead of keeping
        # data copies (`plan/detail/vererbung-konsolidierung.md`).
        def open_consolidate_dialog() -> None:
            """Previews and applies the consolidation for this object's subtree."""
            options = {
                str(definition.get("name")): _label(definition, str(definition.get("name")), locale)
                for definition in definitions
                if definition.get("name") and is_inheritable(definition)
            }
            dialog = ui.dialog()
            with (
                dialog,
                ui.card().classes("w-[48rem] max-w-[92vw] max-h-[80vh] overflow-y-auto"),
            ):
                ui.label(t("consolidate.title")).classes("text-subtitle1")
                ui.label(t("consolidate.hint")).classes("text-caption text-gray-500")
                with ui.row().classes("items-center gap-3 w-full"):
                    min_children = (
                        ui.number(t("consolidate.min_children"), value=2, min=2, precision=0)
                        .props("outline dense")
                        .classes("w-44")
                    )
                    attribute_select = (
                        ui.select(options, multiple=True, label=t("consolidate.attributes"))
                        .props("outline dense use-chips")
                        .classes("grow")
                    )
                result_area = ui.column().classes("w-full gap-1")

                async def poll_job(run_id: str) -> dict | None:
                    """Waits for the queued job and returns its result (None = failure)."""
                    for _ in range(120):
                        try:
                            runs = await client.list_job_runs(token, ids=[run_id], limit=1)
                        except Exception:  # noqa: BLE001 - retry on transient errors
                            runs = []
                        if not runs:
                            return None
                        status = str(runs[0].get("status") or "")
                        if status == "success":
                            return dict(runs[0].get("result") or {})
                        if status == "failed":
                            return None
                        await asyncio.sleep(_JOB_POLL_SECONDS)
                    return None

                def render_result(result: dict) -> None:
                    """Shows the proposal (preview) or the outcome of the run."""
                    result_area.clear()
                    moves = list(result.get("moves") or [])
                    with result_area:
                        ui.label(
                            t("consolidate.summary").format(
                                promoted=result.get("promoted", 0),
                                removed=result.get("removed", 0),
                                skipped=result.get("skipped", 0),
                                nodes=result.get("nodes", 0),
                            )
                        ).classes("text-caption")
                        if not any(move.get("action") == "promote" for move in moves):
                            ui.label(t("consolidate.empty")).classes("text-caption text-warning")
                        for move in moves:
                            parent = _consolidation_parent(move, locale)
                            attribute = str(move.get("attribute"))
                            if move.get("action") == "promote":
                                ui.label(
                                    t("consolidate.move").format(
                                        parent=parent,
                                        attribute=attribute,
                                        value=_consolidation_value(move.get("value"), locale),
                                        count=len(move.get("children") or []),
                                    )
                                ).classes("text-caption")
                            else:
                                reason = str(move.get("reason") or "")
                                ui.label(
                                    t("consolidate.skip").format(
                                        parent=parent,
                                        attribute=attribute,
                                        reason=t(f"consolidate.reason.{reason}") if reason else "",
                                    )
                                ).classes("text-caption text-gray-500")
                        for row in result.get("results") or []:
                            if row.get("status") == "error":
                                ui.label(
                                    t("consolidate.root_failed").format(
                                        object_id=row.get("object_id"),
                                        reason=row.get("error"),
                                    )
                                ).classes("text-caption text-negative")

                async def run_consolidation(*, dry_run: bool) -> None:
                    """Queues the run (dry run = preview) and renders its report."""
                    parameters: dict = {
                        "dry_run": dry_run,
                        "min_children": int(min_children.value or 2),
                    }
                    selected = [str(entry) for entry in attribute_select.value or []]
                    if selected:
                        parameters["attributes"] = selected
                    try:
                        run = await client.post_bulk(
                            token,
                            op="consolidate_inheritance",
                            object_ids=[object_id],
                            parameters=parameters,
                        )
                    except Exception:  # noqa: BLE001 - show a friendly message
                        ui.notify(t("consolidate.failed"), color="negative")
                        return
                    run_id = str(run.get("id") or "")
                    result_area.clear()
                    with result_area:
                        ui.label(t("consolidate.running")).classes("text-caption")
                    if not run_id:
                        return
                    result = await poll_job(run_id)
                    if result is None:
                        result_area.clear()
                        with result_area:
                            ui.label(t("consolidate.failed")).classes("text-caption text-negative")
                        return
                    render_result(result)
                    if not dry_run:
                        ui.notify(t("consolidate.done"), color="positive")
                        # Show the result on the form itself; section state and
                        # preferences survive the reload.
                        ui.timer(2.0, lambda: ui.navigate.reload(), once=True)

                with ui.row().classes("w-full justify-end"):
                    ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                    ui.button(
                        t("consolidate.preview"),
                        icon="visibility",
                        on_click=lambda: run_consolidation(dry_run=True),
                    ).props("flat")
                    ui.button(
                        t("consolidate.apply"),
                        icon="arrow_upward",
                        on_click=lambda: run_consolidation(dry_run=False),
                    ).props("flat")
            dialog.open()

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
        async def save(*, next_object: bool = False) -> None:
            """Saves the object; ``next_object`` continues the edit queue."""
            new_attrs: dict = {
                k: (dict(v) if isinstance(v, dict) else v) for k, v in edited.items()
            }
            # Keys cleared in the UI must be **removed** on the server: the core
            # merges `attributes`, so sending the shorter map alone would keep the
            # old value (and block inheritance). `remove_attributes` drops them.
            removed = [name for name in raw_attributes if name not in new_attrs]
            changed = new_attrs != raw_attributes
            status = status_select.value if status_select.value else None
            if status is not None and status != resolved.get("status"):
                changed = True
            if changed:
                try:
                    await client.update_object(
                        token,
                        object_id,
                        attributes=new_attrs,
                        status=status,
                        locale=locale,
                        remove_attributes=removed,
                    )
                    ui.notify(t("form.saved"), color="positive")
                except CoreError as error:
                    ui.notify(t("form.save_failed_reason").format(reason=error), color="negative")
                    return  # keep the object open, in particular in a sequence
                except Exception:  # noqa: BLE001 - show a friendly message
                    ui.notify(t("form.save_failed"), color="negative")
                    return
            elif not next_object:
                ui.notify(t("grid.no_changes"), color="info")
                return
            if next_object:
                await advance_queue()
            else:
                ui.navigate.reload()

        async def save_and_next() -> None:
            """Sequential editing: save and open the next queued object."""
            await save(next_object=True)

        async def advance_queue() -> None:
            """Opens the next object of the queue (or finishes the flow)."""
            ids = edit_queue.queue_ids()
            following = edit_queue.next_object_id(ids, object_id)
            if following is None:
                edit_queue.stop_queue()
                ui.notify(t("seq.finished").format(count=len(ids)), color="positive")
                ui.navigate.to(origin_url)
                return
            ui.navigate.to(f"/products/{quote(following, safe='')}")

        async def skip_object() -> None:
            """Sequential editing: continue without saving this object."""
            await advance_queue()

        def stop_sequence() -> None:
            """Leaves sequential editing and returns to the grid view."""
            edit_queue.stop_queue()
            ui.navigate.to(origin_url)

        ui.button(t("grid.save"), icon="save", on_click=save).classes("bg-primary text-white mt-4")

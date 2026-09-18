"""The column picker of the product grid: which columns a user sees.

`visible_fields` is the **single source of truth**. The grid, every checkbox (an attribute
may sit in several groups, so a field can have several boxes), the group toggles and the
language counters are mirrored *from* that set — `sync` writes the widgets, never the other
way round. While the picker is written from code, `syncing` suppresses the change handlers:
NiceGUI fires `on_value_change` when `element.value` is assigned, which would call back in.

The page supplies what the picker must not know: how a column is shown or hidden in the
grid (`apply_set` for a whole set, `set_field` for one field), how the set is persisted
(`persist`), and the catalogs the rows are built from (`PickerCatalogs`).

`group_checked` and `language_columns_state` are the two pure rules the sync relies on.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from meadow.frontend.display import resolve_value
from meadow.frontend.export_fields import group_members, group_order
from meadow.frontend.i18n import t


def group_checked(members: Sequence[str], visible: Collection[str]) -> bool:
    """A group box is ticked when **every** member column is visible."""
    return bool(members) and all(name in visible for name in members)


def language_columns_state(fields: Mapping[str, str], visible: Collection[str]) -> tuple[int, int]:
    """How many of an attribute's language columns are visible (`on`, `total`)."""
    return sum(1 for name in fields.values() if name in visible), len(fields)


@dataclass(frozen=True)
class PickerCatalogs:
    """What the picker needs to build its sections (read off the grid by the page)."""

    #: Every field a column exists for.
    column_fields: frozenset[str]
    #: The field prefix of the display value's language columns (`_name`).
    name_prefix: str
    #: Definitions by name (the label of an attribute row).
    definitions: Mapping[str, Mapping[str, Any]]
    #: The **own** attribute definitions (grouped and ungrouped rows).
    own_definitions: Sequence[Mapping[str, Any]]
    #: Attribute groups by id (their labels).
    groups: Mapping[str, Mapping[str, Any]]
    #: Prefix of an attribute -> `{language: field}` of its language columns.
    language_fields: Mapping[str, Mapping[str, str]]
    #: Language code -> label (for the language dialogs).
    language_labels: Mapping[str, str]
    #: Prefix (field prefix of an attribute) -> its language prefix.
    language_prefixes: Mapping[str, str]
    #: The standard's attributes that carry data (they get a section of their own).
    reference_definitions: Sequence[Mapping[str, Any]]
    #: How many of the standard's attributes are hidden (named in a hint).
    hidden_standard: int = 0


class ColumnPicker:
    """The modal that shows and edits the visible column set."""

    def __init__(
        self,
        *,
        locale: str,
        visible: set[str],
        catalogs: PickerCatalogs,
        apply_set: Callable[[set[str]], None],
        set_field: Callable[[str, bool], None],
        persist: Callable[[set[str]], None],
    ) -> None:
        """
        :param visible: the current column set; the picker takes ownership of it.
        :param apply_set: shows exactly the given set in the grid.
        :param set_field: shows/hides one column in the grid.
        :param persist: stores the set for the current user.
        """
        self.locale = locale
        self.visible_fields = visible
        self.catalogs = catalogs
        self.apply_set = apply_set
        self.set_field = set_field
        self.persist = persist
        self.dialog: ui.dialog | None = None
        #: Field -> every checkbox that shows it (a field may sit in several groups).
        self.checkbox_refs: dict[str, list[ui.checkbox]] = {}
        #: Group id -> the "all" checkbox of that section.
        self.group_boxes: dict[str, ui.checkbox] = {}
        #: Group id -> the column fields of that group.
        self.group_fields: dict[str, list[str]] = {}
        #: Group id -> the language prefixes the group toggle clears when switched off.
        self.group_prefixes: dict[str, list[str]] = {}
        #: Prefix -> the counter label of that attribute's language columns.
        self.language_counters: dict[str, ui.label] = {}
        #: Prefix -> the language dialogs (built on first use).
        self._language_dialogs: dict[str, ui.dialog] = {}
        self._syncing = False

    # --- state -------------------------------------------------------------

    def sync(self) -> None:
        """Mirrors `visible_fields` into the picker (boxes, groups, counters).

        Called after every change, so a field that belongs to several groups stays
        consistent in all of them and a restored personal view shows up in the
        picker as well.
        """
        self._syncing = True
        try:
            for field, boxes in self.checkbox_refs.items():
                state = field in self.visible_fields
                for box in boxes:
                    if bool(box.value) != state:
                        box.value = state
            for group_id, box in self.group_boxes.items():
                state = group_checked(self.group_fields.get(group_id, []), self.visible_fields)
                if bool(box.value) != state:
                    box.value = state
            for prefix, counter in self.language_counters.items():
                on, total = language_columns_state(
                    self.catalogs.language_fields.get(prefix, {}), self.visible_fields
                )
                counter.set_text(t("grid.columns_languages_count").format(on=on, total=total))
        finally:
            self._syncing = False

    def set_visibility(self, fields: set[str]) -> None:
        """Applies a column set, syncs the picker and persists it."""
        self.visible_fields.clear()
        self.visible_fields.update(fields)
        self.apply_set(self.visible_fields)
        self.sync()
        self.persist(self.visible_fields)

    def toggle_field(self, field: str, visible: bool) -> None:
        """Shows/hides one column and brings the whole picker in line."""
        if visible:
            self.visible_fields.add(field)
        else:
            self.visible_fields.discard(field)
        self.set_field(field, visible)
        self.sync()
        self.persist(self.visible_fields)

    def toggle_group(self, group_id: str, visible: bool) -> None:
        """Toggles a whole attribute group.

        Turning a group on enables the group's attributes (their working-language
        column each — the language columns stay a per-attribute choice); turning it
        off also clears those language columns, so a hidden group leaves no orphan
        language column behind.
        """
        for field in self.group_fields.get(group_id, []):
            self.toggle_field(field, visible)
        if not visible:
            for prefix in self.group_prefixes.get(group_id, []):
                for language_column in self.catalogs.language_fields.get(prefix, {}).values():
                    if language_column in self.visible_fields:
                        self.toggle_field(language_column, False)
        self.sync()

    def on_field_change(self, field: str, event: Any) -> None:
        """Checkbox handler: ignored while the picker is written from code."""
        if self._syncing:
            return
        self.toggle_field(field, bool(event.value))

    # --- the dialog --------------------------------------------------------

    def build(self) -> ui.dialog:
        """Builds the picker dialog (and the toolbar button belongs to the page)."""
        catalogs = self.catalogs
        base_labels = {
            "_id": t("grid.id"),
            "_name": t("grid.name"),
            "_type": t("grid.type"),
            "_status": t("grid.status"),
            "_completeness": t("grid.completeness"),
            "_releases": t("grid.releases"),
            "_refs": t("grid.columns_refs"),
        }
        base_fields = [field for field in base_labels if field in catalogs.column_fields]
        grouped_names, ungrouped_names = group_members(catalogs.own_definitions)
        ordered_group_ids = group_order(grouped_names, catalogs.groups, self._group_title)
        # Column picker as a dialog (not a `ui.menu`): Quasar's QMenu auto-open doesn't
        # fire in Firefox/Zen, while `ui.dialog` opens reliably in all browsers.
        with ui.dialog() as dialog, ui.card().classes("w-[26rem] max-w-[94vw]"):
            ui.label(t("grid.columns")).classes("text-h6")
            with ui.column().classes("w-full gap-0 max-h-[70vh] overflow-y-auto"):
                self._section(
                    "_base",
                    t("grid.columns_base"),
                    [
                        (
                            field,
                            base_labels[field],
                            catalogs.name_prefix if field == "_name" else "",
                        )
                        for field in base_fields
                    ],
                    open_=True,
                )
                for group_id in ordered_group_ids:
                    self._section(
                        group_id,
                        self._group_title(group_id),
                        self._attribute_rows(list(grouped_names[group_id])),
                    )
                self._section(
                    "_ungrouped",
                    t("grid.columns_ungrouped"),
                    self._attribute_rows(list(ungrouped_names)),
                )
                if catalogs.reference_definitions:
                    self._section(
                        "_standard",
                        t("grid.columns_reference"),
                        self._attribute_rows(
                            [
                                str(definition.get("name"))
                                for definition in catalogs.reference_definitions
                            ]
                        ),
                    )
                if catalogs.hidden_standard > 0:
                    ui.label(
                        t("grid.columns_reference_hidden").format(count=catalogs.hidden_standard)
                    ).classes("text-caption text-gray-500 mt-2")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat")
            # Initial state: the boxes were built with the current set, the group
            # toggles and language counters are filled from it (a restored personal
            # view must show up here).
            self.sync()
        self.dialog = dialog
        return dialog

    def _group_title(self, group_id: str) -> str:
        group = self.catalogs.groups.get(group_id) or {}
        return (
            resolve_value(group.get("label"), self.locale)
            or str(group.get("name") or "")
            or group_id
        )

    def _attribute_rows(self, names: list[str]) -> list[tuple[str, str, str]]:
        """Picker rows for attribute names (label + name, language prefix)."""
        rows: list[tuple[str, str, str]] = []
        for name in names:
            definition = self.catalogs.definitions.get(name) or {}
            label = resolve_value(definition.get("label"), self.locale) or name
            text = name if label == name else f"{label} ({name})"
            rows.append(
                (
                    f"attr_{name}",
                    text,
                    self.catalogs.language_prefixes.get(name, ""),
                )
            )
        return rows

    def _field_checkbox(self, field: str, text: str) -> ui.checkbox:
        """One picker box for a field (a field may get several)."""
        box = (
            ui.checkbox(text, value=field in self.visible_fields)
            .props("dense")
            .on_value_change(lambda e, f=field: self.on_field_change(f, e))
        )
        self.checkbox_refs.setdefault(field, []).append(box)
        return box

    def _language_row(self, prefix: str, title: str) -> None:
        """The hybrid language control of one attribute (button + counter)."""
        fields = self.catalogs.language_fields.get(prefix) or {}
        if not fields:
            return
        counter = ui.label().classes("text-caption text-gray-500")
        self.language_counters[prefix] = counter
        ui.button(icon="translate").props("flat round dense").on_click(
            lambda p=prefix, title_=title: self._open_language_dialog(p, title_)
        ).tooltip(t("grid.columns_languages_hint"))

    def _open_language_dialog(self, prefix: str, title: str) -> None:
        """Opens the language columns of one attribute (built on first use)."""
        dialog = self._language_dialogs.get(prefix)
        if dialog is None:
            with ui.dialog() as dialog, ui.card().classes("w-[24rem] max-w-[92vw]"):
                ui.label(title).classes("text-h6")
                ui.label(t("grid.columns_languages_hint")).classes("text-caption text-gray-500")
                for language, field in (self.catalogs.language_fields.get(prefix) or {}).items():
                    self._field_checkbox(
                        field,
                        f"{self.catalogs.language_labels.get(language, language)} · "
                        f"{language.upper()}",
                    )
                with ui.row().classes("w-full justify-end"):
                    ui.button(t("grid.close"), on_click=dialog.close).props("flat dense")
            self._language_dialogs[prefix] = dialog
        dialog.open()

    def _section(
        self,
        group_id: str,
        title: str,
        rows: list[tuple[str, str, str]],
        *,
        open_: bool = False,
    ) -> None:
        """One collapsible section: group toggle + `(field, text, prefix)` rows.

        `prefix` is the language prefix of an attribute's language columns (empty when it
        has none), so the group toggle can clear them together with the group.
        """
        if not rows:
            return
        with ui.expansion(f"{title} ({len(rows)})", value=open_).classes("w-full").props("dense"):
            group_box = (
                ui.checkbox(t("grid.columns_group_all"))
                .props("dense")
                .on_value_change(
                    lambda e, g=group_id: (
                        None if self._syncing else self.toggle_group(g, bool(e.value))
                    )
                )
            )
            self.group_boxes[group_id] = group_box
            self.group_fields[group_id] = [field for field, _, _ in rows]
            self.group_prefixes[group_id] = [prefix for _, _, prefix in rows if prefix]
            for field, text, prefix in rows:
                with ui.row().classes("w-full items-center gap-1 pl-4"):
                    self._field_checkbox(field, text)
                    self._language_row(prefix, text)

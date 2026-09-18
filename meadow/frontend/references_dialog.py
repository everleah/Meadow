"""The reference dialog of the product grid: show, add and remove an object's relations.

Opened from a row's `_refs` column it lists the object's references grouped by relation
type — the composition parent first, then the aggregations by their label — and offers two
ways to add one: the server-side picker search (`GET /objects/picker`) or the structure
picker. Only relation types whose `source_types` accept the shown object are offered; the
core rejects every other one with `400`.

`reference_groups` is the pure core of the list: which object a reference points to (the
"other" side), whether it may be removed, and in which order the groups appear. The dialog
around it only renders what that function decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.display import resolve_value
from meadow.frontend.i18n import t
from meadow.frontend.object_picker import open_object_picker, render_picker_hit
from meadow.frontend.relation_roles import (
    definition_key,
    effective_target_types,
    usable_definitions,
)

#: The composition relation: always listed first and never removable here.
COMPOSITION_DEFINITION = "rel_depends_on"


@dataclass(frozen=True)
class ReferenceChip:
    """One reference of the shown object: the "other" side and what it is."""

    #: The reference's own id (what a remove call needs).
    id: object
    #: Label of the object on the other side of the reference.
    label: str
    #: Object type of the other side.
    type: str
    #: The relation type the reference uses.
    definition_id: str
    #: Whether the dialog offers a remove button (the composition edge cannot be cut).
    removable: bool


def relation_options(
    relation_definitions: Mapping[str, Mapping[str, Any]], locale: str
) -> dict[str, str]:
    """The relation types the dialog offers to **add**: the aggregations, labelled.

    The core's `create_reference` expects the definition's short record key (e.g.
    `rel_category_member`, from `relation_definition:rel_category_member`), not the full
    id and not the display `name` — `definition_key` is the one place that decides.
    """
    return {
        key: resolve_value(definition.get("label"), locale) or key
        for key, definition in relation_definitions.items()
    }


def reference_chip(reference: Mapping[str, Any], target: str) -> ReferenceChip:
    """Builds the chip of one reference: the object on the **other** side.

    A reference is directional (`out` -> `in`); which end the shown object is depends on
    the direction, and the chip always describes the far end.
    """
    definition_id = str(reference.get("definition_id") or "")
    other = (
        reference.get("in_object")
        if reference.get("out") == target
        else reference.get("out_object")
    ) or {}
    return ReferenceChip(
        id=reference.get("id"),
        label=str(other.get("label") or ""),
        type=str(other.get("type") or ""),
        definition_id=definition_id,
        removable=definition_id != COMPOSITION_DEFINITION,
    )


def reference_groups(
    references: Sequence[Mapping[str, Any]],
    target: str,
    labels: Mapping[str, str],
) -> list[tuple[str, str, list[ReferenceChip]]]:
    """The relation groups of one object, ready to render: `(key, heading, chips)`.

    Order: the composition first, then the aggregations by their label (a group without a
    resolved label falls back to its key). Inside a group the chips are ordered by label,
    then type, so the list does not jump between reloads.
    """
    groups: dict[str, list[ReferenceChip]] = {}
    for reference in references:
        chip = reference_chip(reference, target)
        groups.setdefault(chip.definition_id, []).append(chip)
    ordered = sorted(
        groups,
        key=lambda key: (0 if key == COMPOSITION_DEFINITION else 1, labels.get(key, key).lower()),
    )
    return [
        (
            key,
            labels.get(key, key),
            sorted(groups[key], key=lambda chip: (chip.label.lower(), chip.type)),
        )
        for key in ordered
    ]


class ReferencesDialog:
    """The modal that displays and edits the references of one object."""

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        locale: str,
        relation_definitions: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """
        :param relation_definitions: the relation types by short record key.
        """
        self.client = client
        self.token = token
        self.locale = locale
        self.relation_definitions = relation_definitions
        self.options = relation_options(relation_definitions, locale)
        self.dialog: ui.dialog | None = None
        self.select: ui.select | None = None
        self.search_input: ui.input | None = None
        self.subject_label: ui.label | None = None
        self.chips: ui.column | None = None
        self.hits: ui.column | None = None
        self._subject: dict[str, str] = {"id": "", "type": ""}

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with ui.dialog() as dialog, ui.card().classes("w-[640px]"):
            ui.label(t("grid.references")).classes("text-h6")
            self.subject_label = ui.label().classes("text-caption text-gray-500")
            self.chips = ui.column()
            with ui.row().classes("items-center gap-2 w-full"):
                self.select = (
                    ui.select(self.options, value=None, label=t("grid.relation_type"))
                    .props("outline dense")
                    .classes("grow")
                )
            self.search_input = (
                ui.input(t("grid.reference_search")).props("outline dense").classes("grow")
            )
            self.search_input.on_value_change(lambda e: self.search(e.value))
            ui.button(
                icon="account_tree",
                on_click=lambda: open_object_picker(
                    client=self.client,
                    token=self.token,
                    locale=self.locale,
                    title=t("form.pick_target"),
                    on_pick=self.add,
                    target_types=self.target_types(),
                    exclude_ids=[self.subject_id],
                ),
            ).props("outline dense").tooltip(t("form.pick_in_structure"))
            self.hits = ui.column()
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat dense")
        self.dialog = dialog
        return dialog

    @property
    def subject_id(self) -> str:
        """Id of the object the dialog currently shows (`""` before the first open)."""
        return self._subject["id"]

    def target_types(self) -> list[str] | None:
        """Target types of the relation type currently chosen in the dialog."""
        key = str(self.select.value or "") if self.select is not None else ""
        return effective_target_types(self.relation_definitions.get(key))

    async def open(self, object_id: str, object_type: str = "") -> None:
        """Opens the dialog for one object: only usable relation types are offered."""
        self._subject = {"id": object_id, "type": object_type}
        if self.subject_label is not None:
            self.subject_label.set_text(object_id)
        self._offer_usable_types(object_type)
        self._clear_hits()
        await self.refresh()
        if self.dialog is not None:
            self.dialog.open()

    async def refresh(self) -> None:
        """Reloads the reference list of the shown object."""
        if self.chips is None:
            return
        self.chips.clear()
        target = self.subject_id
        if not target:
            return
        try:
            references = await self.client.list_object_references(
                self.token, target, locale=self.locale
            )
        except CoreError, httpx.HTTPError:  # degrade to no chips
            references = []
        labels = await self._definition_labels()
        groups = reference_groups(references, target, labels)
        if not groups:
            with self.chips, ui.column().classes("w-full"):
                ui.label(t("grid.ref_empty")).classes("text-caption text-gray-500")
            return
        for _, heading, chips in groups:
            with self.chips, ui.column().classes("w-full gap-1"):
                ui.label(heading).classes("text-caption font-medium text-gray-500")
                for chip in chips:
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.label(f"{chip.label} · {chip.type}").classes(
                            "bg-neutral px-2 py-0.5 rounded text-xs"
                        )
                        if chip.removable:
                            ui.button(
                                icon="close",
                                on_click=lambda rid=chip.id: self.remove(rid),
                            ).props("flat dense size=sm").tooltip(t("grid.ref_remove"))

    async def search(self, value: str) -> None:
        """Searches the target objects of the chosen relation type."""
        if self.hits is not None:
            self.hits.clear()
        query = (value or "").strip()
        if not query or not self.subject_id or self.hits is None:
            return
        try:
            hits = await self.client.picker_search(
                self.token,
                query,
                types=self.target_types(),
                locale=self.locale,
                limit=20,
                # A partial `code` must find the target too; the hits keep the
                # completeness badge.
                code_prefix=True,
            )
        except CoreError, httpx.HTTPError:  # degrade to no hits
            return
        own_id = self.subject_id
        for hit in hits:
            if str(hit.get("id")) == own_id:
                continue
            with self.hits:
                render_picker_hit(hit, self.add, self.locale)

    async def add(self, hit: Mapping[str, Any]) -> None:
        """Creates one reference from the chosen hit (picker or structure)."""
        definition_id = str(self.select.value or "") if self.select is not None else ""
        if not definition_id:
            ui.notify(t("grid.ref_relation_required"), color="warning")
            return
        try:
            await self.client.create_reference(
                self.token, self.subject_id, str(hit.get("id")), definition_id
            )
        except CoreError, httpx.HTTPError:  # show a friendly message
            ui.notify(t("grid.ref_add_failed"), color="negative")
            return
        self._clear_hits()
        await self.refresh()

    async def remove(self, reference_id: str) -> None:
        """Deletes one reference of the shown object."""
        try:
            await self.client.delete_reference(self.token, reference_id)
        except CoreError, httpx.HTTPError:  # show a friendly message
            ui.notify(t("grid.ref_remove_failed"), color="negative")
        await self.refresh()

    def _offer_usable_types(self, object_type: str) -> None:
        """Narrows the relation types to those that accept this object type."""
        if self.select is None:
            return
        usable = usable_definitions(list(self.relation_definitions.values()), object_type)
        if not usable:
            self.select.set_options(self.options)
            self.select.value = None
            return
        offered = {
            definition_key(definition): self.options[definition_key(definition)]
            for definition in usable
        }
        self.select.set_options(offered)
        self.select.value = next(iter(offered))

    def _clear_hits(self) -> None:
        if self.hits is not None:
            self.hits.clear()
        if self.search_input is not None:
            self.search_input.value = ""

    async def _definition_labels(self) -> dict[str, str]:
        """Labels of **all** relation types (the list shows every kind, composition too)."""
        try:
            definitions = await self.client.list_relation_definitions(self.token)
        except CoreError, httpx.HTTPError:  # fall back to raw keys
            return {}
        return {
            str(definition["id"]).split(":", 1)[-1]: resolve_value(
                definition.get("label"), self.locale
            )
            or str(definition["id"]).split(":", 1)[-1]
            for definition in definitions
        }

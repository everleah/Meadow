"""The "create object" dialog of the products grid (`/products`).

Creating an object needs its **composition parent** (root contract), and the type
registry supplies the display attribute (`label_attribute`) and the mandatory
business keys (`key_attributes`). This module owns the dialog and the validation of
its form; the page supplies the type registry, the attribute definitions and the
parent (the structure tree's selection).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx
from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.display import resolve_value
from meadow.frontend.i18n import t


def normalized_name(raw: object) -> object:
    """The name as the core wants it: a `str`, or a `{lang: text}` map when multilingual.

    An empty result means "no name given" — the dialog refuses to create then.
    """
    if isinstance(raw, dict):
        return {str(lang): str(text) for lang, text in raw.items() if str(text or "").strip()}
    return str(raw or "").strip()


def missing_key_names(values: Mapping[str, object]) -> list[str]:
    """The business keys that are still empty (in the order they were passed)."""
    return [name for name, value in values.items() if not str(value or "").strip()]


def build_attributes(
    label_attribute: str, name: object, values: Mapping[str, object]
) -> dict[str, object]:
    """The `attributes` of a new object: its label attribute plus the business keys."""
    attributes: dict[str, object] = {label_attribute: name}
    for key, value in values.items():
        attributes[key] = str(value).strip()
    return attributes


class CreateObjectDialog:
    """The dialog that creates one object of a creatable type.

    :param client: core client (parent lookup, create).
    :param token: bearer token of the session.
    :param locale: working locale (type and attribute labels).
    :param types: the creatable types of the page (`creatable_types`).
    :param definitions: the attribute definitions by name (labels, multilingual).
    :param languages: languages the multilingual name editor offers.
    :param get_parent: the composition parent to write with (tree selection or `None`).
    """

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        locale: str,
        types: Sequence[Mapping[str, object]],
        definitions: Mapping[str, Mapping[str, object]],
        languages: Sequence[Mapping[str, object]],
        get_parent: Callable[[], str | None],
    ) -> None:
        self.client = client
        self.token = token
        self.locale = locale
        self.types = list(types)
        self.definitions = definitions
        self.languages = languages
        self.get_parent = get_parent
        self.type_options = {
            str(item.get("name")): (
                resolve_value(item.get("label"), locale) or str(item.get("name"))
            )
            for item in self.types
        }
        self._by_name = {str(item.get("name")): item for item in self.types}
        self._state: dict[str, Any] = {}
        self._key_inputs: dict[str, ui.element] = {}
        self.dialog: ui.dialog | None = None
        self.type_select: ui.select | None = None
        self.parent_label: ui.label | None = None
        self.form_host: ui.column | None = None

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with ui.dialog() as dialog, ui.card().classes("w-[34rem] max-w-[92vw]"):
            ui.label(t("grid.create_title")).classes("text-h6")
            ui.label(t("grid.create_hint")).classes("text-caption text-gray-600")
            self.type_select = (
                ui.select(self.type_options, label=t("grid.create_type"))
                .props("filled dense")
                .classes("w-full")
                .mark("create-type")
            )
            self.type_select.on_value_change(lambda _: self.render_fields())
            self.parent_label = ui.label().classes("text-caption text-gray-500")
            self.form_host = ui.column().classes("w-full gap-2")
            with self.form_host:
                self.render_fields()
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                ui.button(t("grid.create_save"), icon="add", on_click=self.save).props("flat").mark(
                    "create-save"
                )
        self.dialog = dialog
        return dialog

    async def open(self) -> None:
        """Opens the dialog for the current parent (the tree selection or the root)."""
        parent = self.get_parent()
        self._state.clear()
        self._state["parent"] = parent
        default_type = next(iter(self.type_options), None)
        if self.type_select is not None and self.type_select.value != default_type:
            self.type_select.set_value(default_type)
        self.render_fields()
        if self.parent_label is not None:
            self.parent_label.set_text(
                t("grid.create_parent").format(parent=await self._parent_caption(parent))
            )
        if self.dialog is not None:
            self.dialog.open()

    async def save(self) -> None:
        """Creates the object (with its composition parent) and opens its form page."""
        type_name = str(self.type_select.value or "") if self.type_select is not None else ""
        label_attribute = self._label_attribute(type_name)
        name = normalized_name(self._state.get(label_attribute))
        if not name:
            ui.notify(t("grid.create_name_required"), color="warning")
            return
        values = {key: getattr(field, "value", "") for key, field in self._key_inputs.items()}
        missing = missing_key_names(values)
        if missing:
            ui.notify(t("grid.create_key_required").format(name=missing[0]), color="warning")
            return
        try:
            created = await self.client.create_object(
                self.token,
                object_type=type_name,
                attributes=build_attributes(label_attribute, name, values),
                parent=self._state.get("parent"),
            )
        except CoreError as error:
            ui.notify(f"{t('grid.create_failed')}: {error}", color="negative")
            return
        if self.dialog is not None:
            self.dialog.close()
        type_label = self.type_options.get(type_name, type_name)
        ui.notify(t("grid.created").format(type=type_label), color="positive")
        # Straight into the form: the new object is still empty.
        ui.navigate.to(f"/products/{created['id']}")

    def render_fields(self) -> None:
        """Renders the label attribute and the business keys of the chosen type."""
        if self.form_host is None or self.type_select is None:
            return
        self._key_inputs.clear()
        type_name = str(self.type_select.value or "")
        label_attribute = self._label_attribute(type_name)
        self.form_host.clear()
        with self.form_host:
            if self._is_multilingual(label_attribute):
                # Imported here on purpose: `multilang` reads `resolve_value` from
                # `grid`, so a module-level import would close the cycle as soon as
                # `grid` is imported first (e.g. by a test).
                from meadow.frontend.multilang import multilingual_value_editor

                multilingual_value_editor(
                    field_label=t("grid.create_name"),
                    locale=self.locale,
                    languages=self.languages,
                    value=None,
                    store=self._state,
                    key=label_attribute,
                )
            else:
                (
                    ui.input(t("grid.create_name"))
                    .props("filled dense")
                    .classes("w-full")
                    .mark("create-name")
                    .on_value_change(
                        lambda e, k=label_attribute: self._state.__setitem__(k, str(e.value or ""))
                    )
                )
            declared_keys = (self._by_name.get(type_name) or {}).get("key_attributes")
            for key_name in declared_keys if isinstance(declared_keys, list) else []:
                key = str(key_name)
                definition = self.definitions.get(key) or {}
                label = resolve_value(definition.get("label"), self.locale) or key
                self._key_inputs[key] = (
                    ui.input(label if label == key else f"{label} ({key})")
                    .props("filled dense")
                    .classes("w-full")
                    .mark(f"create-key-{key}")
                )
            if self._key_inputs:
                ui.label(t("grid.create_key_hint")).classes("text-caption text-gray-500")

    def _label_attribute(self, type_name: str) -> str:
        """The type's display attribute (`label_attribute`, default `name`)."""
        return str((self._by_name.get(type_name) or {}).get("label_attribute") or "name")

    def _is_multilingual(self, attribute: str) -> bool:
        """`name` is multilingual by convention, others by definition."""
        if attribute == "name":
            return True
        return bool((self.definitions.get(attribute) or {}).get("multilingual"))

    async def _parent_caption(self, parent: str | None) -> str:
        """`Placement: <name>` of the composition parent (its id as fallback)."""
        if not parent:
            return t("grid.create_parent_root")
        try:
            parent_object = await self.client.get_object(self.token, parent, locale=self.locale)
        except CoreError, httpx.HTTPError:  # show the raw reference
            return str(parent)
        attributes = parent_object.get("attributes") or {}
        label = resolve_value(attributes.get("name"), self.locale)
        return str(label or parent_object.get("id") or parent)

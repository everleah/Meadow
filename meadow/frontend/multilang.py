"""Reusable multilingual value editor (hybrid approach).

A field shows the value for the current UI language (the "main edit language")
plus a language-icon button that opens a dialog to edit the value in every active
data language. Mirrors the product-form / categories / languages multilingual
editing and replaces the former hard-coded ``Label (DE)`` / ``Label (EN)`` pairs.
"""

from __future__ import annotations

from nicegui import ui

from meadow.frontend.grid import resolve_value
from meadow.frontend.i18n import t


def base_lang(locale: str) -> str:
    """Returns the base language code of a locale (e.g. ``de-DE`` -> ``de``)."""
    return locale.split("-")[0]


def active_data_languages(languages: list[dict]) -> list[dict]:
    """Returns the active, non-root data languages from ``GET /languages``."""
    return [
        lang for lang in languages if str(lang.get("code")) != "root" and lang.get("active", True)
    ]


def _lang_list(languages: list[dict], locale: str) -> list[dict]:
    """Active data languages, guaranteed to include the main edit language."""
    main = base_lang(locale)
    langs = active_data_languages(languages)
    if not any(str(lang.get("code")) == main for lang in langs):
        langs = [{"code": main, "label": {main: main}}] + langs
    return langs


def multilingual_value_editor(
    *,
    field_label: str,
    locale: str,
    languages: list[dict],
    value: dict | None,
    store: dict,
    key: str,
    in_row: bool = True,
) -> ui.element:
    """Renders a main-language input + language-icon button (hybrid editor).

    The value is a ``{lang: value}`` map stored in ``store[key]``. The inline
    input edits the current UI language; the icon opens a dialog to edit every
    active data language. Returns the main input (so callers can read it/refresh
    it). With ``in_row=False`` the input + icon are rendered directly into the
    current context (for use inside an existing ``ui.row``).
    """
    main_lang = base_lang(locale)
    langs = _lang_list(languages, locale)
    store[key] = dict(value or {}) if isinstance(value, dict) else {}
    main_input: ui.element

    def on_main(e) -> None:
        store[key][main_lang] = str(e.value) or ""
        if not store[key][main_lang]:
            store[key].pop(main_lang, None)

    def open_dialog() -> None:
        with ui.dialog() as dlg, ui.card().classes("w-[34rem] max-w-[90vw]"):
            ui.label(field_label).classes("text-h6")
            inputs: dict[str, ui.input] = {}
            with ui.column().classes("w-full gap-2 mt-2"):
                for lang in langs:
                    code = str(lang.get("code"))
                    inputs[code] = (
                        ui.input(resolve_value(lang.get("label"), locale) or code)
                        .props("filled dense")
                        .classes("w-full")
                    )
                    inputs[code].set_value(str((store[key] or {}).get(code) or ""))
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=dlg.close).props("flat")

                def apply() -> None:
                    new: dict[str, str] = {}
                    for code, inp in inputs.items():
                        if inp.value:
                            new[code] = inp.value
                    store[key] = new
                    main_input.set_value(str(new.get(main_lang) or ""))
                    dlg.close()

                ui.button(t("form.apply"), icon="save", on_click=apply).props("flat")

        dlg.open()

    def build() -> None:
        nonlocal main_input
        main_input = (
            ui.input(f"{field_label} ({main_lang.upper()})").props("filled dense").classes("flex-1")
        )
        main_input.set_value(str((store[key] or {}).get(main_lang) or ""))
        ui.button(icon="translate").props("flat round dense").on_click(open_dialog).tooltip(
            t("form.languages_edit")
        )
        main_input.on_value_change(on_main)

    if in_row:
        with ui.row().classes("w-full items-center gap-1"):
            build()
    else:
        build()

    return main_input

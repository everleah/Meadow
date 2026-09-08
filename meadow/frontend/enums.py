"""Helpers to resolve reusable enum sets and their multilingual option labels.

An ``enum`` attribute now references an ``enum_set`` by ``enum_set_id`` instead
of a flat ``constraints.enum_values`` string list. These helpers resolve the
set's options and turn a stored option ``code`` into a display label for a
locale, falling back to ``constraints.enum_values`` for any definition that has
not been migrated yet.
"""

from __future__ import annotations


def set_for_definition(sets: list[dict], definition: dict | None) -> dict | None:
    """Returns the enum set referenced by an attribute definition, else ``None``.

    The definition's ``enum_set_id`` holds a full record id (e.g.
    ``enum_set:enum_set_color``); it is matched against the set's ``id``.
    """
    set_id = (definition or {}).get("enum_set_id")
    if not set_id:
        return None
    for s in sets or []:
        if str(s.get("id")) == str(set_id):
            return s
    return None


def _pick_label(labels: object, locale: str) -> str:
    """Picks a multilingual label for a locale (falls back to en, then first)."""
    if isinstance(labels, dict):
        if locale in labels and labels.get(locale):
            return str(labels[locale])
        if labels.get("en"):
            return str(labels["en"])
        for value in labels.values():
            if value:
                return str(value)
        return ""
    return str(labels) if labels else ""


def enum_options(sets: list[dict], definition: dict | None, locale: str) -> list[dict]:
    """Returns ``[{code, label}]`` for the definition's enum set.

    Labels are resolved for ``locale`` (with an en/first fallback). When the
    definition references no set, the legacy ``constraints.enum_values`` string
    list is used unchanged.
    """
    s = set_for_definition(sets, definition)
    if s is None:
        return [
            {"code": str(v), "label": str(v)}
            for v in ((definition or {}).get("constraints") or {}).get("enum_values") or []
        ]
    result: list[dict] = []
    for opt in s.get("options") or []:
        code = str(opt.get("code") or "")
        if not code:
            continue
        result.append({"code": code, "label": _pick_label(opt.get("labels"), locale)})
    return result


def enum_code_label_map(sets: list[dict], definition: dict | None, locale: str) -> dict[str, str]:
    """Returns ``{code: label}`` for the definition's enum set."""
    return {o["code"]: o["label"] for o in enum_options(sets, definition, locale)}


def enum_label_for(sets: list[dict], definition: dict | None, locale: str, value: object) -> str:
    """Resolves a stored enum code to its display label for the locale."""
    if value is None:
        return ""
    code = str(value)
    return enum_code_label_map(sets, definition, locale).get(code, code)

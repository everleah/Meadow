"""Where the UI loads attribute definitions, and which ones it may offer.

Two rules are guarded here, because both failures are silent:

1. **Every** definition has to arrive. `GET /attribute-definitions` is capped at
   1000 rows and sorted by id, so with an installed standard (17 377 `EF…`
   features) its first page holds nothing but standard vocabulary — a freshly
   created own attribute is then maintainable in the grid (which loads the
   vocabulary) but has no field in the product form. Every UI page therefore loads
   `GET /attribute-definitions/vocabulary`, which answers with all definitions
   from the core's cache in one request.

2. Places that let a user **choose** an attribute offer the catalogue's own
   attributes, not the standard's vocabulary (`reference_data`, see
   `meadow/frontend/reference_data.py` and `display.own_attribute_names`).
"""

from __future__ import annotations

import ast
from pathlib import Path

from meadow.frontend.display import (
    declared_key_names,
    own_attribute_names,
    text_attribute_options,
)

FRONTEND = Path(__file__).resolve().parent.parent / "meadow" / "frontend"

#: `api.py` wraps the endpoint (a caller may really want one page); the pages must
#: not use it — the cap would decide which attributes they can see.
CAPPED_CALL = "list_attribute_definitions"

OWN_TEXT = {"name": "sku", "type": "text", "reference_data": False}
OWN_ENUM = {"name": "color", "type": "enum", "reference_data": False}
OWN_MULTILINGUAL = {"name": "name", "type": "text", "multilingual": True}
REFERENCE_TEXT = {"name": "EF000123", "type": "text", "reference_data": True}
DEFINITIONS = [OWN_TEXT, OWN_ENUM, OWN_MULTILINGUAL, REFERENCE_TEXT]

TYPES = [
    {"name": "product", "key_attributes": ["sku"], "label_attribute": "name"},
    {"name": "vehicle_generation", "key_attributes": ["generation_code"]},
    {"name": "part_group", "key_attributes": ["code"], "label_attribute": "name"},
    {"name": "root", "key_attributes": []},
]


def _capped_callers() -> dict[str, list[int]]:
    """Page modules that still call the capped paged list, with the line numbers."""
    callers: dict[str, list[int]] = {}
    for path in sorted(FRONTEND.glob("*.py")):
        if path.name == "api.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == CAPPED_CALL
        ]
        if lines:
            callers[path.name] = lines
    return callers


def test_no_page_loads_the_capped_paged_list() -> None:
    callers = _capped_callers()
    assert not callers, (
        "these modules would only ever see the first 1000 definitions: "
        f"{callers} — load `list_attribute_vocabulary` instead"
    )


def test_the_form_loads_the_whole_vocabulary() -> None:
    source = (FRONTEND / "form.py").read_text(encoding="utf-8")
    assert "list_attribute_vocabulary" in source


def test_own_attribute_names_keeps_the_standards_vocabulary_out() -> None:
    assert own_attribute_names(DEFINITIONS) == ["color", "name", "sku"]
    assert own_attribute_names([]) == []
    assert own_attribute_names([{"type": "text"}, REFERENCE_TEXT]) == []


def test_key_and_label_options_are_own_single_language_texts() -> None:
    options = text_attribute_options(DEFINITIONS)
    assert options == {"sku": "sku"}


def test_declared_names_stay_selectable() -> None:
    """A type's current key/label must never fall out of the options.

    `name` is the conventional label attribute and is not an attribute definition
    at all, so a filter over definitions alone would not offer it; `code` and
    `generation_code` are the declared keys of other types.
    """
    options = text_attribute_options(DEFINITIONS, declared_key_names(TYPES))
    assert options == {
        "sku": "sku",
        "code": "code",
        "generation_code": "generation_code",
        "name": "name",
    }


def test_declared_key_names_collects_keys_and_labels() -> None:
    assert declared_key_names(TYPES) == ["code", "generation_code", "name", "sku"]
    assert declared_key_names([]) == []

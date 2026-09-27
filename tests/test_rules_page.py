"""Unit tests for the rules page helpers.

The Rule Lab itself is opened from the AG Grid's **selection**, which only the browser can
answer (`aggrid.get_selected_rows()` is a JavaScript call) — its behaviour is verified
against the running instance with a browser check. What is a pure function is tested here:
the label of an object row, which the lab's object list shows. It used to read a top-level
`name` that `GET /objects` does not have, so the list showed 200 ULIDs.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.frontend.display import object_row_label  # noqa: E402 - after the path fix


def test_the_object_label_resolves_the_name_for_the_current_language() -> None:
    row = {
        "id": "object:01M2Q60QXGNEFNB3PNGPYAQV13",
        "type": "product",
        "attributes": {"name": {"de": "Radlagersatz, Vorderachse", "en": "Wheel bearing kit"}},
    }
    assert object_row_label(row, "de") == "Radlagersatz, Vorderachse"
    assert object_row_label(row, "en") == "Wheel bearing kit"


def test_the_object_label_falls_back_code_business_key_and_id() -> None:
    # A taxonomy node carries a `code` instead of a name (the picker does the same).
    assert object_row_label({"id": "object:1", "attributes": {"code": "EG0001"}}, "de") == "EG0001"
    assert (
        object_row_label({"id": "object:2", "attributes": {"sku": "MA-WHE-01953"}}, "de")
        == "MA-WHE-01953"
    )
    assert (
        object_row_label({"id": "object:3", "source_id": "demo-product-1"}, "de")
        == "demo-product-1"
    )
    # Nothing usable: the id, never an empty string.
    assert object_row_label({"id": "object:4"}, "de") == "object:4"
    assert object_row_label({"id": "object:5", "attributes": "not a map"}, "de") == "object:5"
    # A constructed payload has no id at all; the caller falls back to its own label.
    assert object_row_label({"type": "product", "attributes": {}}, "de") == ""

"""Tests for creating objects from the product grid (`meadow/frontend/grid.py`).

`creatable_types` decides which of the registry's object types the grid offers for
a **new** object: system types (`root`, `category`, `collection`, `saved_search`,
`asset`) have their own pages and taxonomy node types are maintained in the
categories view — offering them here would file objects where nobody looks for
them. `product` is the usual case and comes first.
"""

from __future__ import annotations

from meadow.frontend.grid import creatable_types


def _type(name: str, **extra: object) -> dict:
    return {"name": name, "label": {"en": name.upper()}, **extra}


def test_system_and_taxonomy_types_are_not_offered() -> None:
    rows = [
        _type("root", system=True),
        _type("category", system=True, taxonomy=True),
        _type("collection", system=True),
        _type("saved_search", system=True),
        _type("asset", system=True),
        _type("etim_class", taxonomy=True),
        _type("part_group", taxonomy=True),
        _type("product"),
        _type("variant"),
    ]
    assert [item["name"] for item in creatable_types(rows)] == ["product", "variant"]


def test_product_comes_first_and_the_rest_stays_sorted() -> None:
    rows = [_type("variant"), _type("oem_number"), _type("product"), _type("bench")]
    assert [item["name"] for item in creatable_types(rows)] == [
        "product",
        "bench",
        "oem_number",
        "variant",
    ]


def test_types_without_a_name_are_skipped() -> None:
    rows = [{"label": {"en": "broken"}}, _type("product")]
    assert [item["name"] for item in creatable_types(rows)] == ["product"]


def test_key_and_label_attributes_survive() -> None:
    rows = [_type("product", key_attributes=["sku"], label_attribute="name")]
    (product,) = creatable_types(rows)
    assert product["key_attributes"] == ["sku"]
    assert product["label_attribute"] == "name"

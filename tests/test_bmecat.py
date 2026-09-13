"""Tests for the BMEcat/ETIM export builder (`meadow/worker/bmecat.py`).

The rules asserted here were verified against the official ETIM BMEcat 5.0 schema
(`resources/etim/bmecat_etim_501.xsd`) — the header order, the mandatory
`BUYER` block, the 32-character `SUPPLIER_PID`, one `PRODUCT_FEATURES` per class
and `FVALUE_DETAILS` for a missing value. `plan/detail/bmecat-export.md` §11.3.
"""

from __future__ import annotations

import httpx
import pytest

from meadow.worker import bmecat
from meadow.worker.exports import _bmecat_export

DEFINITIONS = {
    "EF000007": {"name": "EF000007", "type": "text", "label": {"en": "Colour"}},
    "EF000008": {"name": "EF000008", "type": "number", "label": {"en": "Width"}},
    "EF007219": {"name": "EF007219", "type": "range", "label": {"en": "Busbar width"}},
}
PROFILE = {
    "catalog_id": "MEADOW-TEST",
    "catalog_name": "Test catalogue",
    "language": "eng",
    "supplier": "ACME",
    "buyer": "Buyer Inc",
}


def _product(pid: str = "A-1", **overrides: object) -> dict:
    product = {
        "id": f"object:{pid}",
        "type": "product",
        "pid": pid,
        "attributes": {"name": {"en": "Widget", "de": "Dings"}, "code": pid},
        "classes": ["EC000012"],
        "features": [
            {"name": "EF000007", "attribute": "EF000007", "required": False, "nodes": ["EC000012"]},
            {"name": "EF000008", "attribute": "EF000008", "required": True, "nodes": ["EC000012"]},
        ],
    }
    product.update(overrides)
    return product


def test_the_header_keeps_the_order_the_schema_demands() -> None:
    document, report = bmecat.build_document([_product()], DEFINITIONS, PROFILE)

    assert document.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'version="2005"' in document
    assert bmecat.NS in document
    assert "<CATALOG_ID>MEADOW-TEST</CATALOG_ID>" in document
    assert '<LANGUAGE default="true">eng</LANGUAGE>' in document
    # `BUYER` is mandatory in the ETIM profile and must precede `SUPPLIER`.
    assert document.index("<BUYER>") < document.index("<SUPPLIER>")
    assert "<BUYER_NAME>Buyer Inc</BUYER_NAME>" in document
    # `T_NEW_PRODUCTDATA` is the price-less transaction (prices are V4).
    assert "<T_NEW_PRODUCTDATA>" in document
    assert "<T_NEW_CATALOG>" not in document
    assert report["products"] == 1


def test_every_class_gets_its_own_feature_block() -> None:
    product = _product(
        classes=["EC000012", "EC000013"],
        features=[
            {"name": "EF000007", "attribute": "EF000007", "required": False, "nodes": ["EC000012"]},
            {"name": "EF000008", "attribute": "EF000008", "required": False, "nodes": ["EC000013"]},
        ],
        attributes={"name": "Widget", "EF000007": "red", "EF000008": 12.5},
    )
    document, report = bmecat.build_document([product], DEFINITIONS, PROFILE)

    assert document.count("<PRODUCT_FEATURES>") == 2
    assert document.count("<REFERENCE_FEATURE_GROUP_ID>EC000012") == 1
    assert document.count("<REFERENCE_FEATURE_GROUP_ID>EC000013") == 1
    # Each feature appears exactly once, in the block of its own class.
    assert document.count("<FNAME>EF000007</FNAME>") == 1
    assert document.count("<FNAME>EF000008</FNAME>") == 1
    assert report["classes"] == 2
    assert report["features"] == 2


def test_an_inherited_feature_appears_in_every_block() -> None:
    """A feature defined outside the product's classes belongs to each class."""
    product = _product(
        classes=["EC000012", "EC000013"],
        features=[
            {"name": "EF000007", "attribute": "EF000007", "required": False, "nodes": ["ancestor"]},
        ],
        attributes={"name": "Widget"},
    )
    document, _ = bmecat.build_document([product], DEFINITIONS, PROFILE)

    assert document.count("<FNAME>EF000007</FNAME>") == 2


def test_a_missing_value_becomes_a_dash_with_a_reason() -> None:
    product = _product(attributes={"name": "Widget", "EF000007": "red"})
    document, report = bmecat.build_document([product], DEFINITIONS, PROFILE)

    # EF000008 is mandatory and unset -> `-` + UN; EF000007 is optional but set.
    assert "<FVALUE>red</FVALUE>" in document
    assert "<FVALUE>-</FVALUE>" in document
    assert "<FVALUE_DETAILS>UN</FVALUE_DETAILS>" in document
    assert report["with_value"] == 1
    assert report["dash"] == 1
    assert report["missing_required"] == [
        {"pid": "A-1", "feature": "EF000008", "attribute": "EF000008"}
    ]


def test_an_optional_feature_without_a_value_is_not_applicable() -> None:
    product = _product(attributes={"name": "Widget", "EF000008": 4})
    _document, report = bmecat.build_document([product], DEFINITIONS, PROFILE)

    assert report["by_reason"] == {"NA": 1}
    assert report["missing_required"] == []


def test_a_range_value_becomes_two_values() -> None:
    product = _product(
        features=[
            {"name": "EF007219", "attribute": "EF007219", "required": True, "nodes": ["EC000012"]},
        ],
        attributes={"name": "Widget", "EF007219": {"from": 4.0, "to": 12.5}},
    )
    document, report = bmecat.build_document([product], DEFINITIONS, PROFILE)

    assert "<FVALUE>4</FVALUE>\n          <FVALUE>12.5</FVALUE>" in document
    assert report["with_value"] == 1
    # A number or range never lands in the "raw text" list.
    assert report["text_values"] == []


def test_only_the_catalogue_language_counts() -> None:
    product = _product(attributes={"name": {"en": "Widget"}, "EF000007": {"de": "rot"}})
    document, report = bmecat.build_document([product], DEFINITIONS, PROFILE)

    # The value exists in German only -> missing in an `eng` catalogue, never
    # silently exported in the wrong language.
    assert "rot" not in document
    assert report["dash"] == 2

    german, _ = bmecat.build_document(
        [_product(attributes={"name": {"de": "Dings"}, "EF000007": {"de": "rot"}})],
        DEFINITIONS,
        {**PROFILE, "language": "ger"},
    )
    assert "<FVALUE>rot</FVALUE>" in german
    assert "<DESCRIPTION_SHORT>Dings</DESCRIPTION_SHORT>" in german


def test_a_bound_own_attribute_supplies_the_feature_value() -> None:
    product = _product(
        features=[
            {"name": "EF000008", "attribute": "width", "required": True, "nodes": ["EC000012"]},
        ],
        attributes={"name": "Widget", "width": 42},
    )
    document, report = bmecat.build_document([product], DEFINITIONS, PROFILE)

    assert "<FNAME>EF000008</FNAME>" in document
    assert "<FVALUE>42</FVALUE>" in document
    assert report["bound"] == 1


def test_products_without_a_pid_or_class_are_skipped_and_reported() -> None:
    products = [
        _product(pid="A-1"),
        _product(pid="", attributes={"name": "Nameless"}),
        _product(pid="X" * 33, attributes={"name": "Too long"}),
        _product(pid="C-1", classes=[]),
        _product(pid="K-1", type="etim_class"),
    ]
    document, report = bmecat.build_document(products, DEFINITIONS, PROFILE)

    assert report["products"] == 1
    assert report["skipped"] == {
        "no supplier pid": 1,
        "supplier pid too long": 1,
        "not classified": 1,
        "type etim_class": 1,
    }
    assert report["unclassified"] == ["C-1"]
    assert "X" * 33 not in document
    assert any("supplier PID longer" in issue for issue in report["issues"])


def test_the_supplier_pid_falls_back_to_code_then_sku() -> None:
    assert bmecat.pid_of({"code": "A-1"}, {}) == "A-1"
    assert bmecat.pid_of({"sku": "B-2"}, {}) == "B-2"
    assert bmecat.pid_of({"code": {"de": "C-3"}}, {}) == "C-3"
    assert bmecat.pid_of({"code": "A-1", "sku": "B-2"}, {"pid_attribute": "sku"}) == "B-2"
    assert bmecat.pid_of({}, {}) == ""


def test_the_output_is_deterministic_and_escaped() -> None:
    products = [
        _product(pid="B-2", attributes={"name": "Zweite", "EF000007": "a & b < c"}),
        _product(pid="A-1", attributes={"name": "Erste"}),
    ]
    document, _ = bmecat.build_document(products, DEFINITIONS, PROFILE)

    assert document.index("<SUPPLIER_PID>A-1<") < document.index("<SUPPLIER_PID>B-2<")
    assert "a &amp; b &lt; c" in document


def test_validation_reports_a_missing_schema_instead_of_failing() -> None:
    document, _ = bmecat.build_document([_product()], DEFINITIONS, PROFILE)
    result = bmecat.validate(document)

    # The licensed schema and `lxml` are optional: an unavailable prerequisite is
    # reported (`validated: false` + reason), never raised.
    assert isinstance(result["validated"], bool)
    if not result["validated"]:
        assert result["reason"]
    else:
        assert result["valid"] is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, ["42"]),
        (4.0, ["4"]),
        (True, ["true"]),
        ({"from": 1, "to": 3}, ["1", "3"]),
        ({"from": None, "to": 3}, None),
        ("", None),
        (None, None),
        (" text ", ["text"]),
    ],
)
def test_feature_values_renders_every_stored_shape(value: object, expected: list | None) -> None:
    assert bmecat.feature_values(value) == expected


# --- the resolution layer (`meadow/worker/exports.py`) ------------------------
#
# A `PRODUCT_FEATURES` block must name its class by the business key (`code`,
# the ETIM class `EC…`). The taxonomy the core returns also contains plain shop
# categories without a code, and those are no reference feature groups.


def _transport(
    objects: list[dict],
    resolved: dict[str, dict],
    nodes: dict[str, dict],
    seen: list[str] | None = None,
):
    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/objects":
            return httpx.Response(200, json=objects)
        if path == "/attribute-definitions":
            return httpx.Response(200, json=[])
        if path == "/languages":
            return httpx.Response(200, json=[])
        if path.startswith("/objects/object:"):
            rest = path[len("/objects/object:") :]
            if rest.endswith("/category-attributes"):
                key = rest[: -len("/category-attributes")]
                return httpx.Response(200, json=resolved.get(key, {}))
            if seen is not None:
                seen.append(rest)
            return httpx.Response(200, json=nodes.get(rest, {}))

        return httpx.Response(404)

    return transport


def _export_parameters() -> dict:
    return {
        "source": {"kind": "type", "type": "product"},
        "format": "bmecat",
        "options": {},
        "bmecat": {"catalog_id": "MEADOW-TEST", "supplier": "ACME"},
    }


async def test_the_block_id_is_the_class_code_not_the_node_key() -> None:
    objects = [{"id": "object:1", "type": "product", "attributes": {"sku": "A-1"}}]
    resolved = {
        "1": {
            "classes": ["01M0CLASS"],
            "attributes": [
                {
                    "name": "EF000008",
                    "attribute": "EF000008",
                    "required": True,
                    "nodes": ["01M0CLASS"],
                }
            ],
        }
    }
    nodes = {"01M0CLASS": {"attributes": {"code": "EC000012"}}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_transport(objects, resolved, nodes)))

    result = await _bmecat_export(client, "http://core", _export_parameters())

    assert "<REFERENCE_FEATURE_GROUP_ID>EC000012</REFERENCE_FEATURE_GROUP_ID>" in result["xml"]
    assert "01M0CLASS" not in result["xml"]
    assert result["report"]["products"] == 1
    assert result["report"]["features"] == 1
    assert result["report"]["nodes_without_code"] == []


async def test_a_node_without_a_code_is_no_block() -> None:
    objects = [{"id": "object:1", "type": "product", "attributes": {"sku": "A-1"}}]
    resolved = {"1": {"classes": ["01M0SHOP"], "attributes": []}}
    nodes = {"01M0SHOP": {"type": "category", "attributes": {"name": {"en": "Widgets"}}}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_transport(objects, resolved, nodes)))

    result = await _bmecat_export(client, "http://core", _export_parameters())

    assert result["report"]["products"] == 0
    assert result["report"]["skipped"] == {"not classified": 1}
    # The reason is visible: the node exists, it just cannot be referenced.
    assert result["report"]["nodes_without_code"] == ["01M0SHOP"]
    assert "01M0SHOP" not in result["xml"]


async def test_a_feature_of_an_ancestor_class_is_inherited_into_every_block() -> None:
    objects = [{"id": "object:1", "type": "product", "attributes": {"sku": "A-1", "EF000098": "x"}}]
    resolved = {
        "1": {
            "classes": ["01M0CLASS"],
            "attributes": [
                {
                    "name": "EF000098",
                    "attribute": "EF000098",
                    "required": False,
                    "nodes": ["01M0GROUP"],
                },
                {
                    "name": "EF000008",
                    "attribute": "EF000008",
                    "required": False,
                    "nodes": ["01M0CLASS"],
                },
            ],
        }
    }
    nodes = {
        "01M0CLASS": {"attributes": {"code": "EC000012"}},
        "01M0GROUP": {"attributes": {"code": "EG000010"}},
    }
    seen: list[str] = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_transport(objects, resolved, nodes, seen))
    )

    result = await _bmecat_export(client, "http://core", _export_parameters())

    # Both features are in the class block; the ancestor's code is resolved too.
    assert "<FNAME>EF000098</FNAME>" in result["xml"]
    assert "<FNAME>EF000008</FNAME>" in result["xml"]
    assert result["report"]["features"] == 2
    # Each node is looked up once (cached across products and features).
    assert sorted(seen) == ["01M0CLASS", "01M0GROUP"]

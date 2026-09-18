"""Tests for the BMEcat/ETIM export builder (`meadow/worker/bmecat.py`).

The rules asserted here were verified against the official ETIM BMEcat 5.0 schema
(`resources/etim/bmecat_etim_501.xsd`) — the header order, the mandatory
`BUYER` block, the 32-character `SUPPLIER_PID`, one `PRODUCT_FEATURES` per class
and `FVALUE_DETAILS` for a missing value. `plan/detail/bmecat-export.md` §11.3.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import httpx
import pytest

from meadow.worker import bmecat
from meadow.worker.exports import _bmecat_export, parent_reference_id

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
    product: dict[str, Any] = {
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


# --- the priced catalogue (`transaction: "new_catalog"`) ---------------------
#
# The schema makes `PRODUCT_ORDER_DETAILS` (order + content unit as BMEcat unit
# codes), at least one `PRODUCT_PRICE_DETAILS` and a `USER_DEFINED_EXTENSIONS`
# block mandatory per product in that transaction — verified against the XSD.

PRICED = {**PROFILE, "transaction": "new_catalog", "price_attribute": "net_price"}


def test_the_price_less_transaction_stays_unchanged() -> None:
    document, report = bmecat.build_document([_product()], DEFINITIONS, PROFILE)

    assert report["transaction"] == "new_productdata"
    # The product block stays as in V1: the header *does* carry a UDX block
    # (`UDX.EDXF.VERSION`), the price-less transaction carries none.
    for tag in ("PRODUCT_ORDER_DETAILS", "PRODUCT_PRICE_DETAILS", "<USER_DEFINED_EXTENSIONS/>"):
        assert tag not in document, f"`{tag}` belongs to the priced transaction"
    assert report["prices"] == 0


def test_a_priced_catalogue_writes_the_mandatory_blocks_in_order() -> None:
    product = _product(attributes={"name": "Widget", "net_price": 12.5, "EF000008": 4})
    document, report = bmecat.build_document([product], DEFINITIONS, PRICED)

    assert "<T_NEW_CATALOG>" in document
    assert "<T_NEW_PRODUCTDATA>" not in document
    assert report["transaction"] == "new_catalog"
    assert report["prices"] == 1
    # The order of the product sequence is fixed by the schema.
    positions = [
        document.index("<SUPPLIER_PID>"),
        document.index("<PRODUCT_FEATURES>"),
        document.index("<PRODUCT_ORDER_DETAILS>"),
        document.index("<PRODUCT_PRICE_DETAILS>"),
        document.index("<USER_DEFINED_EXTENSIONS/>"),
    ]
    assert positions == sorted(positions)
    assert "<ORDER_UNIT>C62</ORDER_UNIT>" in document
    assert "<CONTENT_UNIT>C62</CONTENT_UNIT>" in document
    assert '<PRODUCT_PRICE price_type="net_list">' in document
    assert "<PRICE_AMOUNT>12.5</PRICE_AMOUNT>" in document
    assert "<PRICE_CURRENCY>EUR</PRICE_CURRENCY>" in document
    assert '<DATETIME type="valid_start_date">' in document


def test_a_priced_catalogue_reports_a_product_without_a_price() -> None:
    products = [
        _product(pid="A-1", attributes={"name": "With price", "net_price": 5}),
        _product(pid="A-2", attributes={"name": "Without price"}),
    ]
    document, report = bmecat.build_document(products, DEFINITIONS, PRICED)

    assert report["products"] == 1
    assert report["skipped"] == {"no price": 1}
    assert report["unpriced"] == ["A-2"]
    assert "<SUPPLIER_PID>A-2<" not in document, "a mandatory block must not be missing"


def test_the_profile_names_the_price_attribute_and_type() -> None:
    product = _product(attributes={"name": "Widget", "list_price": {"value": 9.99, "unit": "EUR"}})
    profile = {
        **PRICED,
        "price_attribute": "list_price",
        "price_type": "net_customer",
        "order_unit": "MTR",
        "content_unit": "KGM",
    }
    document, report = bmecat.build_document([product], DEFINITIONS, profile)

    # A quantified value contributes its number, not its unit map.
    assert "<PRICE_AMOUNT>9.99</PRICE_AMOUNT>" in document
    assert 'price_type="net_customer"' in document
    assert "<ORDER_UNIT>MTR</ORDER_UNIT>" in document
    assert "<CONTENT_UNIT>KGM</CONTENT_UNIT>" in document
    assert report["price_attribute"] == "list_price"
    assert report["price_type"] == "net_customer"
    assert report["order_unit"] == "MTR"


def test_an_unknown_transaction_or_price_type_falls_back_to_the_default() -> None:
    product = _product(attributes={"name": "Widget", "net_price": 5})
    document, report = bmecat.build_document(
        [product], DEFINITIONS, {**PRICED, "transaction": "nope", "price_type": "gross"}
    )

    assert report["transaction"] == "new_productdata", "an unknown transaction must not guess"
    assert "<PRODUCT_PRICE_DETAILS>" not in document
    priced, _ = bmecat.build_document([product], DEFINITIONS, {**PRICED, "price_type": "gross"})
    assert 'price_type="net_list"' in priced


def test_a_variant_references_its_base_product() -> None:
    product = _product(
        pid="VAR-1",
        type="variant",
        base_pid="BASE-7",
        attributes={"name": "Variant", "net_price": 4},
    )
    document, report = bmecat.build_document([product], DEFINITIONS, PRICED)

    assert '<PRODUCT_REFERENCE type="base_product">' in document
    assert "<PROD_ID_TO>BASE-7</PROD_ID_TO>" in document
    assert report["references"] == 1


def test_a_product_without_a_base_pid_writes_no_reference() -> None:
    document, report = bmecat.build_document([_product()], DEFINITIONS, PRICED)

    assert "PRODUCT_REFERENCE" not in document
    assert report["references"] == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12.5, "12.5"),
        (12.0, "12"),
        ({"value": 7, "unit": "EUR"}, "7"),
        ({"en": 3}, "3"),
        ("12.50", "12.50"),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_amount_of_reads_the_price_from_every_stored_shape(
    value: object, expected: str | None
) -> None:
    assert bmecat.amount_of(value) == expected


# --- BMEcat 1.2 (`ARTICLE`, the legacy delivery format) ---------------------
# Verified against `resources/bmecat/bmecat_new_catalog_1_2.dtd` (which includes
# `bmecat_base_1_2.dtd`): the vocabulary is `ARTICLE`, `SUPPLIER_AID` and
# `ARTICLE_*`, order details + prices are mandatory and the header carries
# `LANGUAGE` exactly once. The DTD and the XSD variant are two namespaces.

DTPLUS = {
    **PROFILE,
    "version": "1.2",
    "price_attribute": "net_price",
    "schema_variant": "dtd",
}


def test_the_12_document_uses_the_article_vocabulary() -> None:
    product = _product(attributes={"name": "Widget", "net_price": 12.5, "EF000008": 4})
    document, report = bmecat.build_document([product], DEFINITIONS, DTPLUS)

    assert report["version"] == "1.2"
    assert report["variant"] == "dtd"
    assert report["transaction"] == "new_catalog", "1.2 has no price-less transaction"
    assert (
        '<BMECAT xmlns="http://www.bmecat.org/bmecat/1.2/bmecat_new_catalog" version="1.2">'
        in document
    )
    assert "<T_NEW_CATALOG>" in document
    assert '<ARTICLE mode="new">' in document
    assert "<SUPPLIER_AID>A-1</SUPPLIER_AID>" in document
    assert "<ARTICLE_FEATURES>" in document
    assert "<ARTICLE_ORDER_DETAILS>" in document
    assert "<ARTICLE_PRICE_DETAILS>" in document
    assert '<ARTICLE_PRICE price_type="net_list">' in document
    # The 1.2 vocabulary never mixes with the 2005 element names.
    for tag in ("<PRODUCT ", "<SUPPLIER_PID>", "<PRODUCT_FEATURES>", "<PRODUCT_PRICE_DETAILS>"):
        assert tag not in document, f"`{tag}` is not part of BMEcat 1.2"
    # `LANGUAGE` carries no `default` attribute in 1.2 and exists exactly once.
    assert document.count("<LANGUAGE>") == 1
    assert report["prices"] == 1


def test_the_12_document_needs_a_price_and_reports_the_missing_one() -> None:
    products = [
        _product(pid="A-1", attributes={"name": "With price", "net_price": 5}),
        _product(pid="A-2", attributes={"name": "Without price"}),
    ]
    document, report = bmecat.build_document(products, DEFINITIONS, DTPLUS)

    assert report["products"] == 1
    assert report["skipped"] == {"no price": 1}
    assert report["unpriced"] == ["A-2"]
    assert "<SUPPLIER_AID>A-2<" not in document


def test_the_12_xsd_variant_switches_the_namespace() -> None:
    product = _product(attributes={"name": "Widget", "net_price": 5})
    document, report = bmecat.build_document(
        [product], DEFINITIONS, {**DTPLUS, "schema_variant": "xsd"}
    )

    assert report["variant"] == "xsd"
    assert 'xmlns="http://www.bmecat.org/XMLSchema/1.2/bmecat_new_catalog"' in document, (
        "a 1.2 document satisfies only one of the two variants"
    )


def test_an_unknown_version_falls_back_to_the_etim_profile() -> None:
    document, report = bmecat.build_document(
        [_product()], DEFINITIONS, {**PROFILE, "version": "1.1", "schema_variant": "nope"}
    )

    assert report["version"] == "2005"
    assert report["variant"] == "dtd"
    assert "<T_NEW_PRODUCTDATA>" in document


def test_the_schema_path_follows_the_version_and_variant() -> None:
    assert bmecat.schema_path("2005", "xsd") == bmecat.DEFAULT_XSD
    assert str(bmecat.schema_path("1.2", "dtd") or "").endswith("bmecat_new_catalog_1_2.dtd")
    assert str(bmecat.schema_path("1.2", "xsd") or "").endswith("bmecat_new_catalog_1_2.xsd")


def test_a_12_variant_cannot_reference_its_base_product_and_says_so() -> None:
    product = _product(
        pid="VAR-1",
        type="variant",
        base_pid="BASE-7",
        attributes={"name": "Variant", "net_price": 4},
    )
    document, report = bmecat.build_document([product], DEFINITIONS, DTPLUS)

    # `REFERENCE_QUALIFIERS` of 1.2 has no `base_product` (2005 only), and a wrong
    # qualifier would corrupt the meaning — so the link is dropped, not guessed.
    assert "ARTICLE_REFERENCE" not in document
    assert report["references"] == 0
    assert any("BASE-7" in issue for issue in report["issues"])


def test_the_12_description_stops_at_eighty_characters() -> None:
    product = _product(attributes={"name": "x" * 120, "net_price": 1})
    document, _ = bmecat.build_document([product], DEFINITIONS, DTPLUS)

    assert f"<DESCRIPTION_SHORT>{'x' * 80}</DESCRIPTION_SHORT>" in document
    assert "x" * 81 not in document


def test_a_12_document_validates_against_the_dtd() -> None:
    """The real licensed DTD (it pulls in `bmecat_base_1_2.dtd` via a SYSTEM entity)."""
    if importlib.util.find_spec("lxml") is None:  # pragma: no cover - optional extra
        pytest.skip("lxml is not installed")
    path = bmecat.schema_path("1.2", "dtd")
    if path is None:  # pragma: no cover - the artefact is gitignored
        pytest.skip("the licensed BMEcat 1.2 DTD is not present")
    product = _product(
        attributes={"name": "Widget", "net_price": 12.5, "code": "A-1", "EF000008": 4}
    )
    product["classes"] = ["EC000012", "EC000013"]
    document, _ = bmecat.build_document([product], DEFINITIONS, DTPLUS)

    result = bmecat.validate(document, path)

    assert result["valid"], result.get("errors")


def test_parent_reference_id_reads_the_composition_parent() -> None:
    """The base product of a variant is its composition parent (`rel_depends_on`)."""
    rows = [
        {"definition_id": "rel_category_member", "out": "object:CLASS"},
        {"definition_id": "rel_depends_on", "out": "object:BASE"},
        {"definition_id": "related", "out": "object:OTHER"},
    ]
    assert parent_reference_id(rows) == "object:BASE"
    # The object root is not a base product.
    assert (
        parent_reference_id([{"definition_id": "rel_depends_on", "out": "object:object_root"}])
        == ""
    )
    # A fully qualified definition id and an absent parent are both fine.
    assert (
        parent_reference_id(
            [{"definition_id": "relation_definition:rel_depends_on", "out": "object:BASE"}]
        )
        == "object:BASE"
    )
    assert parent_reference_id([]) == ""
    assert parent_reference_id([{"definition_id": "rel_depends_on"}]) == ""


async def test_a_variant_carries_its_base_product_pid() -> None:
    """The variant path resolves the base PID through the parent's references."""
    objects = [
        {
            "id": "object:VAR",
            "type": "variant",
            "attributes": {"sku": "VAR-1", "net_price": 5},
        }
    ]
    resolved = {"VAR": {"classes": ["01M0CLASS"], "attributes": []}}
    nodes = {"01M0CLASS": {"type": "etim_class", "attributes": {"code": "EC000012"}}}

    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # The worker passes the id as the core returned it (`object:VAR`).
        if path == "/objects/object:VAR/references":
            return httpx.Response(
                200,
                json=[
                    {"definition_id": "rel_depends_on", "out": "object:object_root"},
                    {"definition_id": "rel_depends_on", "out": "object:BASE"},
                ],
            )
        if path == "/objects/object:BASE":
            return httpx.Response(200, json={"id": "object:BASE", "attributes": {"sku": "BASE-9"}})
        return await _transport(objects, resolved, nodes)(request)

    parameters = _export_parameters()
    parameters["bmecat"] = {
        **parameters["bmecat"],
        "transaction": "new_catalog",
        "price_attribute": "net_price",
    }
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    result = await _bmecat_export(client, "http://core", parameters)

    assert "<PROD_ID_TO>BASE-9</PROD_ID_TO>" in result["xml"]
    assert result["report"]["references"] == 1


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
    standards: list[dict] | None = None,
):
    async def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/objects":
            return httpx.Response(200, json=objects)
        if path == "/attribute-definitions":
            return httpx.Response(200, json=[])
        if path == "/languages":
            return httpx.Response(200, json=[])
        if path == "/classification-standards":
            return httpx.Response(200, json=standards or [])
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


async def test_the_installed_standard_names_the_class_block() -> None:
    """The reference feature system comes from the registry, not from a guess."""
    objects = [{"id": "object:1", "type": "product", "attributes": {"sku": "A-1"}}]
    resolved = {"1": {"classes": ["01M0CLASS"], "attributes": []}}
    nodes = {"01M0CLASS": {"type": "etim_class", "attributes": {"code": "EC000012"}}}
    standards = [
        {
            "code": "etim",
            "version": "11.0",
            "node_type": "etim_class",
            "current": True,
            "feature_system_name": "ETIM-11.0",
        },
        {
            "code": "etim",
            "version": "10.0",
            "node_type": "etim_class",
            "current": False,
            "feature_system_name": "ETIM-10.0",
        },
    ]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_transport(objects, resolved, nodes, standards=standards))
    )

    result = await _bmecat_export(client, "http://core", _export_parameters())

    assert (
        "<REFERENCE_FEATURE_SYSTEM_NAME>ETIM-11.0</REFERENCE_FEATURE_SYSTEM_NAME>" in result["xml"]
    )
    assert result["report"]["feature_systems"] == ["ETIM-11.0"], "the current version wins"
    assert result["report"]["standards"] == {"etim_class": "ETIM-11.0"}


async def test_an_unregistered_class_falls_back_to_the_profile_version() -> None:
    objects = [{"id": "object:1", "type": "product", "attributes": {"sku": "A-1"}}]
    resolved = {"1": {"classes": ["01M0CLASS"], "attributes": []}}
    nodes = {"01M0CLASS": {"type": "shop_category", "attributes": {"code": "CAT-1"}}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_transport(objects, resolved, nodes)))

    result = await _bmecat_export(client, "http://core", _export_parameters())

    # `_export_parameters()` sets no `etim_version`, so the module default applies.
    assert (
        "<REFERENCE_FEATURE_SYSTEM_NAME>ETIM-10.0</REFERENCE_FEATURE_SYSTEM_NAME>" in result["xml"]
    )
    assert result["report"]["standards"] == {}, "nothing is registered for that node type"


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

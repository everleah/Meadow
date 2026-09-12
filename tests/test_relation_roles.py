"""Tests for the relation-role helpers shared by the frontend views.

The views must classify relation types by their usage (`role`) instead of
knowing built-in keys: categorisation types provide their own assignment
section and bulk operation, composition/collection stay system-owned
(``plan/detail/relationen.md``).
"""

from __future__ import annotations

from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    PRIMARY_CATEGORY_TYPE,
    ROLE_CATEGORY,
    ROLE_COLLECTION,
    ROLE_COMPOSITION,
    ROLE_LINK,
    allows_source,
    association_definitions,
    category_definitions,
    definition_key,
    effective_target_types,
    primary_category_key,
    role_of,
    role_target_types,
    usable_definitions,
)

_DEFINITIONS = [
    {
        "id": "relation_definition:rel_related",
        "name": "related",
        "kind": "aggregation",
        "role": "link",
        "system": True,
    },
    {
        "id": "relation_definition:rel_collection_member",
        "name": "collection_member",
        "kind": "aggregation",
        "role": "collection",
        "system": True,
    },
    {
        "id": "relation_definition:rel_category_member",
        "name": "category_member",
        "kind": "aggregation",
        "role": "category",
        "system": True,
    },
    {
        "id": "relation_definition:rel_depends_on",
        "name": "depends_on",
        "kind": "composition",
        "role": "composition",
        "system": True,
    },
    {
        "id": "relation_definition:rel_marke",
        "name": "marke",
        "kind": "aggregation",
        "role": "category",
        "system": False,
    },
]


def test_definition_key_strips_the_record_prefix() -> None:
    assert definition_key(_DEFINITIONS[0]) == "rel_related"
    assert definition_key({"name": "legacy"}) == "legacy"


def test_role_of_defaults_to_link() -> None:
    assert role_of({"role": "category"}) == ROLE_CATEGORY
    assert role_of({"role": "  "}) == ROLE_LINK
    assert role_of({}) == ROLE_LINK


def test_role_target_types_fixes_only_the_collection_target() -> None:
    # A categorisation may point at any taxonomy node type (resolved from the
    # object-type catalog), so the role fixes nothing on its own.
    assert role_target_types(ROLE_CATEGORY) == []
    assert role_target_types(ROLE_COLLECTION) == ["collection"]
    assert role_target_types(ROLE_COMPOSITION) == []
    assert role_target_types(ROLE_LINK) == []


def test_category_definitions_start_with_the_primary_type() -> None:
    keys = [definition_key(d) for d in category_definitions(_DEFINITIONS)]

    assert keys == [PRIMARY_CATEGORY_DEF, "rel_marke"]


def test_primary_category_key_falls_back_to_the_builtin_type() -> None:
    assert primary_category_key(_DEFINITIONS) == PRIMARY_CATEGORY_DEF
    assert primary_category_key([]) == PRIMARY_CATEGORY_DEF


def test_association_definitions_exclude_composition_and_categorisation() -> None:
    keys = {definition_key(d) for d in association_definitions(_DEFINITIONS)}

    # Collections and links stay generically assignable; categorisation and
    # composition have dedicated operations.
    assert keys == {"rel_related", "rel_collection_member"}


def test_effective_target_types_prefers_an_explicit_list() -> None:
    definition = {"role": ROLE_CATEGORY, "target_types": ["product"]}

    assert effective_target_types(definition) == ["product"]


def test_effective_target_types_falls_back_to_the_usage() -> None:
    """The built-in types carry no `target_types`; the usage supplies them."""
    assert effective_target_types({"role": "collection", "target_types": []}) == ["collection"]
    # A categorisation resolves against the taxonomy node types the caller knows.
    assert effective_target_types({"role": ROLE_CATEGORY}, ["category", "etim_class"]) == [
        "category",
        "etim_class",
    ]
    # Without that catalog the built-in taxonomy type is assumed.
    assert effective_target_types({"role": ROLE_CATEGORY}) == [PRIMARY_CATEGORY_TYPE]
    assert effective_target_types({"role": ROLE_CATEGORY}, []) == [PRIMARY_CATEGORY_TYPE]
    # A plain link stays unrestricted (the picker greys nothing out).
    assert effective_target_types({"role": ROLE_LINK}) is None
    assert effective_target_types({}) is None


def test_allows_source_checks_the_source_types() -> None:
    assert allows_source({"source_types": []}, "product")
    assert allows_source({"source_types": ["product"]}, "product")
    assert not allows_source({"source_types": ["category"]}, "product")
    assert allows_source(None, "product")


def test_usable_definitions_filter_by_the_source_type() -> None:
    definitions = [
        _DEFINITIONS[0],
        {"id": "relation_definition:rel_only_category", "source_types": ["category"]},
    ]

    keys = {definition_key(d) for d in usable_definitions(definitions, "product")}

    assert keys == {"rel_related"}

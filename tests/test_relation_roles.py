"""Tests for the relation-role helpers shared by the frontend views.

The views must classify relation types by their usage (`role`) instead of
knowing built-in keys: categorisation types provide their own assignment
section and bulk operation, composition/collection stay system-owned
(``plan/detail/relationen.md``).
"""

from __future__ import annotations

from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    ROLE_CATEGORY,
    ROLE_COMPOSITION,
    ROLE_LINK,
    association_definitions,
    category_definitions,
    definition_key,
    primary_category_key,
    role_of,
    role_target_types,
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


def test_role_target_types_fixes_the_target_for_categorisation() -> None:
    assert role_target_types(ROLE_CATEGORY) == ["category"]
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

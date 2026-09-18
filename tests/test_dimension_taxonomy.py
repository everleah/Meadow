"""A category dimension decides the tree — not the user's guesswork.

The core contract (`plan/detail/kategorisierung.md`): a categorisation
(`relation_definition.role = category`) may name `target_types`. With an explicit
list it is **bound** to those taxonomy node types and the core rejects every other
target with `400` (`check_target_types`); without a list it is **generic** and may
point at any taxonomy type.

The UI has to derive that: a bound dimension fixes the shown tree (grid and
`/categories`), and a picker may only ever walk **one** taxonomy. Loading the
universal root's taxonomy children instead returned the first page of *all* trees —
with an installed classification standard that is 100 ETIM classes, so the
"Assembly group membership" picker showed exactly what belongs to "ETIM class
membership" and offered nothing selectable (2026-09-17).
"""

from __future__ import annotations

from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    bound_dimension_taxonomy,
    category_dimension_bindings,
    effective_target_types,
    taxonomy_choices,
)

TAXONOMIES = ["category", "etim_class", "part_group"]

PRIMARY = {
    "id": "relation_definition:rel_category_member",
    "name": "category_member",
    "role": "category",
    "target_types": [],
}
ETIM = {
    "id": "relation_definition:rel_etim_class_member",
    "name": "etim_class_member",
    "role": "category",
    "target_types": ["etim_class"],
}
PARTS = {
    "id": "relation_definition:rel_part_group_member",
    "name": "part_group_member",
    "role": "category",
    "target_types": ["part_group"],
}
LINK = {
    "id": "relation_definition:rel_related",
    "name": "related",
    "role": "link",
    "target_types": [],
}
DEFINITIONS = [PRIMARY, ETIM, PARTS, LINK]


def test_a_bound_dimension_names_its_taxonomy() -> None:
    assert bound_dimension_taxonomy(PARTS, TAXONOMIES) == "part_group"
    assert bound_dimension_taxonomy(ETIM, TAXONOMIES) == "etim_class"


def test_a_generic_dimension_binds_nothing() -> None:
    assert bound_dimension_taxonomy(PRIMARY, TAXONOMIES) is None
    assert bound_dimension_taxonomy(None, TAXONOMIES) is None
    assert bound_dimension_taxonomy(LINK, TAXONOMIES) is None


def test_a_multi_type_binding_is_not_a_single_tree() -> None:
    both = {**PARTS, "target_types": ["part_group", "etim_class"]}
    assert bound_dimension_taxonomy(both, TAXONOMIES) is None


def test_a_binding_outside_the_catalog_is_ignored() -> None:
    stale = {**PARTS, "target_types": ["retired_taxonomy"]}
    assert bound_dimension_taxonomy(stale, TAXONOMIES) is None
    # Without a catalog there is nothing to contradict the definition.
    assert bound_dimension_taxonomy(stale) == "retired_taxonomy"


def test_dimension_bindings_cover_every_bound_categorisation() -> None:
    bindings = category_dimension_bindings(DEFINITIONS, TAXONOMIES)
    assert bindings == {
        "rel_etim_class_member": "etim_class",
        "rel_part_group_member": "part_group",
    }
    # The generic primary dimension stays the user's choice.
    assert PRIMARY_CATEGORY_DEF not in bindings


def test_the_generic_dimension_still_accepts_every_taxonomy() -> None:
    assert effective_target_types(PRIMARY, TAXONOMIES) == TAXONOMIES
    assert effective_target_types(PARTS, TAXONOMIES) == ["part_group"]


def test_a_picker_walks_one_tree_even_for_a_generic_dimension() -> None:
    # Bound: exactly that tree, no switcher needed.
    assert taxonomy_choices({"part_group"}, TAXONOMIES) == ["part_group"]
    # Generic: every tree is allowed, the built-in one first, and the switcher
    # offers them in catalogue order.
    assert taxonomy_choices(set(TAXONOMIES), TAXONOMIES) == TAXONOMIES
    assert taxonomy_choices({"part_group", "etim_class"}, TAXONOMIES) == [
        "etim_class",
        "part_group",
    ]


def test_a_picker_without_a_taxonomy_target_leaves_the_taxonomies_alone() -> None:
    # Products and structure objects are no taxonomy nodes: no taxonomy tree may be
    # offered for them. Which panel such a picker shows is decided by
    # `picker_tree_mode` (flat object list — see `tests/test_picker_tree.py`).
    assert taxonomy_choices({"product", "variant"}, TAXONOMIES) == []
    assert taxonomy_choices(None, TAXONOMIES) == []
    assert taxonomy_choices(set(), TAXONOMIES) == []

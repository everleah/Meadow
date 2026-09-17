"""Relation-type usages (``role``) shared by admin page, form and grid.

Mirrors the core contract (``plan/detail/relationen.md``): every relation type
carries a usage that decides where it appears and which object type it may
point to.

- ``composition`` – the single object hierarchy (built-in type only).
- ``category`` – categorisation of objects; the built-in primary type plus any
  number of custom types (e.g. "Marke", "Saison").
- ``collection`` – membership in a materialized collection (built-in only).
- ``link`` – plain association, the default for custom types.

Views in the UI derive their behaviour from the usage of a type instead of
knowing individual built-in keys.
"""

from __future__ import annotations

from collections.abc import Iterable

from meadow.frontend.i18n import t

ROLE_COMPOSITION = "composition"
ROLE_CATEGORY = "category"
ROLE_COLLECTION = "collection"
ROLE_LINK = "link"

#: Usage assumed for records created before the classification existed.
DEFAULT_ROLE = ROLE_LINK

#: Record key of the built-in primary categorisation.
PRIMARY_CATEGORY_DEF = "rel_category_member"

#: Built-in **taxonomy** object type: the single system taxonomy (product
#: categories). Additional taxonomy types are custom object types flagged with
#: `taxonomy: true` (e.g. a classification standard's class hierarchy).
PRIMARY_CATEGORY_TYPE = "category"

#: Usages a **custom** relation type may have (`composition`/`collection` are
#: reserved for the built-in types).
CUSTOM_ROLES = (ROLE_LINK, ROLE_CATEGORY)


def definition_key(record: dict) -> str:
    """Bare record key of a relation type (``relation_definition:rel_x`` → ``rel_x``)."""
    raw = str(record.get("id") or "").strip()
    return raw.split(":", 1)[-1] if raw else str(record.get("name") or "")


def role_of(definition: dict) -> str:
    """Usage of a relation type (`link` when the record carries none)."""
    return str(definition.get("role") or "").strip() or DEFAULT_ROLE


def role_label(role: str) -> str:
    return t(f"relation_definitions.role_{role}")


def role_target_types(role: str) -> list[str]:
    """Object types a usage fixes on the target side **without** extra context.

    Only `collection` is fixed to a built-in type. A categorisation points at
    every **taxonomy** object type (`object_type.taxonomy`), which the caller
    knows from the object-type catalog — pass it to `effective_target_types`.
    """
    if role == ROLE_COLLECTION:
        return ["collection"]
    return []


def custom_role_options() -> dict[str, str]:
    """Usages selectable for a custom relation type."""
    return {role: role_label(role) for role in CUSTOM_ROLES}


def effective_target_types(
    definition: dict | None, taxonomy_types: Iterable[str] | None = None
) -> list[str] | None:
    """Object types a relation type accepts on the target side.

    An explicit `target_types` list wins; otherwise the usage decides. For a
    categorisation (`category`) these are the taxonomy node types — passed in by
    the caller (`taxonomy_types`, from `/object-types`); without that context the
    built-in `category` type is assumed. `collection` stays fixed. `None` means
    every object type may be the target — the picker then greys nothing out.
    """
    explicit = [str(name) for name in ((definition or {}).get("target_types") or []) if name]
    if explicit:
        return explicit
    role = role_of(definition or {})
    if role == ROLE_CATEGORY:
        return list(taxonomy_types) if taxonomy_types else [PRIMARY_CATEGORY_TYPE]
    return role_target_types(role) or None


def taxonomy_choices(
    allowed_types: Iterable[str] | None, taxonomy_types: Iterable[str] | None
) -> list[str]:
    """Taxonomy node types a picker may show for these allowed target types.

    Empty when the allowed targets are not **all** taxonomy nodes: such a picker
    walks the composition tree (products, structure objects). More than one entry
    means the dimension is **generic** — it accepts every taxonomy type, so the
    user chooses the tree; the built-in taxonomy (`category`) comes first, the rest
    in catalogue order.

    The list is what the picker needs to load **one** tree instead of the first
    page of the universal root's taxonomy children (which is a random mix of all
    trees — with an installed classification standard, 100 ETIM classes).
    """
    allowed = {str(name) for name in (allowed_types or []) if name}
    taxonomies = [str(name) for name in (taxonomy_types or []) if name]
    if not allowed or not allowed.issubset(set(taxonomies)):
        return []
    ordered = [name for name in taxonomies if name in allowed]
    return sorted(ordered, key=lambda name: (name != PRIMARY_CATEGORY_TYPE, name))


def bound_dimension_taxonomy(
    definition: dict | None, taxonomy_types: Iterable[str] | None = None
) -> str | None:
    """The taxonomy node type a categorisation is **bound** to (`None` = generic).

    An explicit `target_types` list with exactly one type is a binding the core
    enforces (`check_target_types` rejects every other target type with `400`), so
    the UI derives the tree from the dimension instead of asking again. A missing,
    empty or multi-entry list keeps the dimension generic — the user chooses.
    """
    if definition is None or role_of(definition) != ROLE_CATEGORY:
        return None
    targets = [str(name) for name in (definition.get("target_types") or []) if name]
    if len(targets) != 1:
        return None
    known = {str(name) for name in (taxonomy_types or []) if name}
    if known and targets[0] not in known:
        return None
    return targets[0]


def category_dimension_bindings(
    definitions: Iterable[dict], taxonomy_types: Iterable[str] | None = None
) -> dict[str, str]:
    """Taxonomy node type each categorisation is bound to, by record key.

    Used by the grid and `/categories`: choosing a **bound** dimension fixes the
    shown tree, so the taxonomy switcher follows the dimension instead of leaving
    the user with a combination that can only be empty (the core rejects the
    assignment anyway). Generic dimensions are absent from the map — the switcher
    stays theirs.
    """
    bindings: dict[str, str] = {}
    for definition in definitions:
        bound = bound_dimension_taxonomy(definition, taxonomy_types)
        if bound:
            bindings[definition_key(definition)] = bound
    return bindings


def allows_source(definition: dict | None, object_type: str) -> bool:
    """True when `object_type` may be the **source** of the relation.

    An empty `source_types` list allows every type; the picker only renders an
    add action for a relation type the core would accept (it rejects the rest
    with 400).
    """
    source_types = [str(name) for name in ((definition or {}).get("source_types") or []) if name]
    return not source_types or object_type in source_types


def usable_definitions(definitions: Iterable[dict], object_type: str) -> list[dict]:
    """Relation types whose source side accepts `object_type`."""
    return [d for d in definitions if allows_source(d, object_type)]


def category_definitions(definitions: Iterable[dict]) -> list[dict]:
    """Category relation types, the built-in primary type first."""
    categories = [d for d in definitions if role_of(d) == ROLE_CATEGORY]
    return sorted(
        categories,
        key=lambda d: (
            definition_key(d) != PRIMARY_CATEGORY_DEF,
            str(d.get("name") or "").lower(),
        ),
    )


def primary_category_key(definitions: Iterable[dict]) -> str:
    """Key of the category type used when none is chosen explicitly."""
    categories = category_definitions(definitions)
    return definition_key(categories[0]) if categories else PRIMARY_CATEGORY_DEF


def association_definitions(definitions: Iterable[dict]) -> list[dict]:
    """Relation types usable in the generic (non-dedicated) views.

    Composition and categorisation have dedicated UI/operations; everything else
    (collections, links, custom types) is handled generically.
    """
    return [d for d in definitions if role_of(d) not in (ROLE_COMPOSITION, ROLE_CATEGORY)]

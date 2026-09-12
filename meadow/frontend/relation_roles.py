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

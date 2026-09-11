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
    """Object types a usage fixes on the target side (empty = free choice)."""
    if role == ROLE_CATEGORY:
        return ["category"]
    if role == ROLE_COLLECTION:
        return ["collection"]
    return []


def custom_role_options() -> dict[str, str]:
    """Usages selectable for a custom relation type."""
    return {role: role_label(role) for role in CUSTOM_ROLES}


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

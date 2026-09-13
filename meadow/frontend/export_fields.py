"""Pure helpers for the export wizard's field selection (`/export`, editor+).

The wizard offers the base fields plus every attribute definition. A catalog can
carry thousands of attributes (an imported classification standard does), so the
selection is **grouped by attribute group** and limited to the attributes the
chosen object type allows. The decisions live here — pure, no NiceGUI, no core
calls — and `export_page.py` renders them (one `ui.expansion` per group).

Nothing here decides *whether* an export is allowed: the core remains
authoritative, these helpers only shape the offered selection.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping


def attribute_names(definitions: Iterable[Mapping[str, object]]) -> list[str]:
    """The distinct attribute definition names in the order they arrive."""
    names: list[str] = []
    for definition in definitions:
        name = str(definition.get("name") or "")
        if name and name not in names:
            names.append(name)
    return names


def group_members(
    definitions: Iterable[Mapping[str, object]],
) -> tuple[dict[str, list[str]], list[str]]:
    """Splits the definitions into `{group_id: [name, …]}` and the ungrouped names.

    An attribute may belong to several groups: it then appears in each of them
    (the product form does the same) while staying a single field in the export.
    """
    grouped: dict[str, list[str]] = {}
    ungrouped: list[str] = []
    for definition in definitions:
        name = str(definition.get("name") or "")
        if not name:
            continue
        group_ids = [str(group_id) for group_id in (definition.get("group_ids") or []) if group_id]
        if not group_ids:
            ungrouped.append(name)
            continue
        for group_id in group_ids:
            members = grouped.setdefault(group_id, [])
            if name not in members:
                members.append(name)
    return grouped, ungrouped


def applicable(definition: Mapping[str, object] | None, object_type: str | None) -> bool:
    """Whether a definition is offered for the chosen object type.

    An empty `applicable_types` means "every type" (the definition is not
    restricted); without an object type (a source that does not fix one) every
    attribute stays visible.
    """
    if not object_type or definition is None:
        return True
    allowed = [str(entry) for entry in (definition.get("applicable_types") or []) if entry]
    return not allowed or object_type in allowed


def allowed_names(
    names: Iterable[str],
    definitions_by_name: Mapping[str, Mapping[str, object]],
    object_type: str | None,
) -> list[str]:
    """The names whose definitions allow the given object type."""
    return [name for name in names if applicable(definitions_by_name.get(name), object_type)]


def group_order(
    grouped: Mapping[str, list[str]],
    groups_by_id: Mapping[str, Mapping[str, object]],
    title: Callable[[str], str],
) -> list[str]:
    """The group ids in display order (`position` first, then the display title)."""
    return sorted(
        grouped,
        key=lambda group_id: (
            int(groups_by_id.get(group_id, {}).get("position") or 0),
            title(group_id),
        ),
    )


def selected_fields(
    visible: Iterable[str],
    state: Mapping[str, bool],
    base_fields: Iterable[str],
) -> list[str]:
    """The ticked fields in render order.

    `state` carries the user's explicit decisions; a field the user never touched
    falls back to "ticked" for the base fields and "unticked" for attributes.
    """
    base = set(base_fields)
    return [name for name in visible if state.get(name, name in base)]

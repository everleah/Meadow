"""Pure display helpers shared by the frontend pages.

These helpers turn core values into human-readable HTML/text and carry no
NiceGUI or page state, so every page (grid, product form, admin pages) can use
the same rendering. Kept separate from the pages to avoid import cycles.
"""

from __future__ import annotations

import html

from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    PRIMARY_CATEGORY_TYPE,
    category_definitions,
    definition_key,
)

#: Object statuses of the workflow state machine (`plan/detail/workflow-freigaben.md`):
#: `draft → in_review → approved → published → archived`. The transitions are
#: `workflow_guard` business rules — the core itself accepts **any** string for
#: `status`, so a selector must be able to show the stored value as well
#: ([`status_options`][meadow.frontend.display.status_options]).
OBJECT_STATUSES = ("draft", "in_review", "approved", "published", "archived")

#: States offered for a channel release: the same vocabulary (the plan defines the
#: release states as the object statuses).
RELEASE_STATUSES = OBJECT_STATUSES

#: State that counts as "released for the channel" (default of the filters).
DEFAULT_RELEASE_STATUS = "published"


def status_options(current: str | None = None) -> dict[str, str]:
    """Options of a status selector: the state machine plus the stored value.

    The value comes from the data, and the core validates no vocabulary, so it can be
    anything an import, the API, an MCF document or a rule wrote. A `ui.select` whose
    value is not among its options raises `ValueError: Invalid value: …` and turns the
    whole page into a **500** — seen 2026-09-21, when the product form knew four of the
    five states and opening an `approved` object answered `500` instead of the form.
    The stored value is therefore always offered, next to the five states.
    """
    options = {status: status for status in OBJECT_STATUSES}
    if current and current not in options:
        options[current] = current
    return options


def resolve_value(value: object, locale: str) -> str:
    """Resolves a (possibly multilingual) attribute value to a display string."""
    if isinstance(value, dict):
        if locale in value:
            return str(value[locale])
        if value:
            return str(next(iter(value.values())))
        return ""
    return "" if value is None else str(value)


def status_badge(status: str) -> str:
    """Returns a coloured badge for a workflow status."""
    classes = {
        "published": "bg-primary text-white",
        "draft": "bg-neutral text-gray-600",
        "archived": "bg-info text-white",
    }.get(status, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{status}</span>'


def completeness_badge(percent: int) -> str:
    """Returns a coloured badge for a completeness percentage.

    Grey for 0 % (nothing set), orange for 1-99 %, green for 100 %.
    """
    if percent >= 100:
        classes = "bg-positive text-white"
    elif percent >= 1:
        classes = "bg-orange text-white"
    else:
        classes = "bg-grey text-white"
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{percent}%</span>'


def release_badges(releases: list[dict] | None, labels: dict[str, str] | None = None) -> str:
    """Returns one chip per channel release (channel label, state in the tooltip).

    ``releases`` is the ``releases`` field of an object (`[{channel, status}]`,
    `?releases=true`), ``labels`` maps channel codes to display names. The chip is
    coloured like a status badge, so a glance shows *for which channel* an object
    is released and in which state.
    """
    labels = labels or {}
    chips: list[str] = []
    for row in releases or []:
        if not isinstance(row, dict):
            continue
        channel = str(row.get("channel") or "")
        status = str(row.get("status") or "")
        if not channel:
            continue
        classes = {
            "published": "bg-primary text-white",
            "draft": "bg-neutral text-gray-600",
            "archived": "bg-info text-white",
        }.get(status, "bg-secondary text-white")
        name = html.escape(labels.get(channel, channel))
        tip = html.escape(f"{channel}: {status}")
        chips.append(
            f'<span class="px-2 py-0.5 mr-1 rounded text-xs {classes}" title="{tip}">{name}</span>'
        )
    return " ".join(chips)


def channel_label(channel: dict | None, locale: str) -> str:
    """Display label of a channel (falls back to its code)."""
    if not isinstance(channel, dict):
        return ""
    return resolve_value(channel.get("label"), locale) or str(channel.get("code") or "")


def object_row_label(row: dict, locale: str) -> str:
    """A human-readable label for an object row from `GET /objects`.

    The endpoint returns the raw `attributes` (multilingual maps and all) and has **no**
    top-level `name`, so the label attribute is resolved for `locale` first — a list of
    bare ULIDs is unusable. Then a `code` (a taxonomy node, the same order the object
    picker uses), the business key `sku`, the source id and finally the id.
    """
    raw = row.get("attributes")
    attributes = raw if isinstance(raw, dict) else {}
    name = resolve_value(attributes.get("name"), locale)
    if name:
        return name
    for key in ("code", "sku", "source_id"):
        value = attributes.get(key) or row.get(key)
        if isinstance(value, str) and value:
            return value
    return str(row.get("id") or "")


def channel_options(channels: list[dict], locale: str) -> dict[str, str]:
    """Select options `code -> label` for channel filters and pickers."""
    return {
        str(channel.get("code")): channel_label(channel, locale)
        for channel in channels
        if channel.get("code")
    }


def dimension_options(
    definitions: list[dict], locale: str, *, primary_label: str
) -> dict[str, str]:
    """Select options `relation key -> label` for the category dimensions.

    A dimension is the built-in primary categorisation plus every custom relation
    type with the usage `category` ("additional categorisations", e.g. "Brand").
    All dimensions share the same category objects (every categorisation points at
    taxonomy node types); the dimension only decides whose members are
    counted/filtered. The primary type gets the caller's label (the pages pass the
    translated key, keeping this helper free of page/i18n state); without known
    definitions it alone is offered, so the switcher is never empty.
    """
    options: dict[str, str] = {}
    for definition in category_definitions(definitions):
        key = definition_key(definition)
        if key == PRIMARY_CATEGORY_DEF:
            options[key] = primary_label
        else:
            options[key] = resolve_value(definition.get("label"), locale) or str(
                definition.get("name") or key
            )
    return options or {PRIMARY_CATEGORY_DEF: primary_label}


def taxonomy_type_names(object_types: list[dict]) -> list[str]:
    """Names of the object types flagged as **taxonomy** node types, sorted.

    Taxonomy types are the node types of the category/classification trees: the
    built-in `category` plus every custom type with `taxonomy: true`. Without a
    catalog the built-in type alone is returned, so the views never break.
    """
    names = sorted(
        {
            str(entry.get("name"))
            for entry in object_types
            if isinstance(entry, dict) and entry.get("taxonomy") and entry.get("name")
        }
    )
    return names or [PRIMARY_CATEGORY_TYPE]


def taxonomy_options(object_types: list[dict], locale: str) -> dict[str, str]:
    """Select options `type name -> label` for the taxonomy node types."""
    options: dict[str, str] = {}
    for entry in object_types:
        if not isinstance(entry, dict) or not entry.get("taxonomy") or not entry.get("name"):
            continue
        name = str(entry["name"])
        options[name] = resolve_value(entry.get("label"), locale) or name
    return options or {PRIMARY_CATEGORY_TYPE: PRIMARY_CATEGORY_TYPE}


def own_attribute_names(definitions: list[dict]) -> list[str]:
    """Names of the **own** attribute definitions, sorted.

    Own means: not the vocabulary of an installed classification standard
    (`reference_data`). Every place that lets a user *choose* an attribute for a
    new purpose — grid columns, relation attributes, class features, connector
    mapping — takes its names from here, so the 17 377 standard features of a full
    ETIM import neither hide the own attributes nor flood the picker
    (`meadow/frontend/reference_data.py`). Places that read or map **data** pass
    the vocabulary unfiltered instead: a stored value never disappears.
    """
    return sorted(
        {
            str(entry.get("name"))
            for entry in definitions
            if isinstance(entry, dict) and entry.get("name") and not entry.get("reference_data")
        }
    )


def text_attribute_options(definitions: list[dict], declared: object = ()) -> dict[str, str]:
    """Select options `name -> name` for a type's `key_attributes`/`label_attribute`.

    A key or label holds one plain value, so only own, single-language `text`
    attributes are eligible. Names a type already declares are kept in the list even
    when no definition matches them (`name` is the conventional label attribute and
    is not an attribute definition at all, see `meadow/frontend/import_page.py`), so
    opening and saving a type never silently drops its current key or label.
    """
    options = {
        str(entry.get("name")): str(entry.get("name"))
        for entry in definitions
        if isinstance(entry, dict)
        and entry.get("name")
        and str(entry.get("type")) == "text"
        and not entry.get("multilingual")
        and not entry.get("reference_data")
    }
    for name in declared if isinstance(declared, list | tuple | set) else ():
        if name:
            options.setdefault(str(name), str(name))
    return options


def declared_key_names(object_types: list[dict]) -> list[str]:
    """Every attribute name the object types declare as key or label attribute."""
    names: set[str] = set()
    for entry in object_types:
        if not isinstance(entry, dict):
            continue
        names.update(str(name) for name in (entry.get("key_attributes") or []) if name)
        if entry.get("label_attribute"):
            names.add(str(entry["label_attribute"]))
    return sorted(names)

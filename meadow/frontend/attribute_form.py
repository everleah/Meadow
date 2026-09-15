"""Pure field rules of the attribute-definition dialog (admin UI).

The dialog asks for many decisions at once — data type, business key,
multilingual, computed, units, enum set, classification-standard binding. Some
combinations have no meaning (a computed rule without `computed`, a unit for a
`text`, an enum set for a `number`), and a few are rejected by the core with `400`
(`is_key` requires `type: text` and no multilingual, see
`api/keys.rs::validate_key_definition`). The rules live here — pure, no NiceGUI, no
core calls — so the dialog stays a renderer and the rules are unit-tested:

- `field_state` tells the dialog which blocks to offer and which switch is
  locked, including the i18n key of the reason.
- `cleaned_payload` builds the payload parts that depend on those choices: a
  field without meaning for the chosen type/flag is **cleared** rather than sent
  as a leftover. The core stores what it receives, so `computed_rule` next to
  `computed: false` would stay in the record forever.

**Clearing an optional text field is an empty string, not `None`:** the core's
PATCH reads a *missing* key as "leave it as it is" (and `api.update_attribute_definition`
drops `None` values from the payload), so only a value the request really carries
can clear — `""` does it for `unit_id`, `display_unit_id`, `enum_set_id` and
`computed_rule` (`crates/pim-core/src/api/attribute_definitions.rs::patch_text`).

`dependencies` lists the attributes a formula reads. The core stores, exports and
imports the list but does **not** evaluate it today (`plan/detail/api-design.md`,
"Attributkatalog"), so a stale entry is harmless — it is still cleared together
with `computed` so records do not carry meaningless data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from meadow.frontend.range_values import NUMERIC_TYPES

#: The data type a business key requires (`api/keys.rs::validate_key_definition`).
KEY_TYPE = "text"


@dataclass(frozen=True)
class FieldState:
    """What the dialog offers for the current choices.

    `key_reason`/`inheritable_reason` carry the **i18n key** of the explanation
    and are empty while the switch is usable. `reference` marks the
    `reference_data` switch: reference data is created by a standard import, so
    the switch is only offered while editing (adopting an imported attribute);
    on a new attribute it would have no meaning.
    """

    units: bool
    enum_set: bool
    computed: bool
    key: bool
    key_reason: str
    inheritable: bool
    inheritable_reason: str
    reference: bool


def field_state(
    definition_type: str,
    *,
    is_key: bool = False,
    multilingual: bool = False,
    computed: bool = False,
    editing: bool = False,
) -> FieldState:
    """Which fields/blocks the dialog shows, and which switch is locked."""
    if definition_type == KEY_TYPE and not multilingual:
        key, key_reason = True, ""
    elif definition_type != KEY_TYPE:
        key, key_reason = False, "attr_def.key_needs_text"
    else:
        key, key_reason = False, "attr_def.key_not_multilingual"
    if is_key:
        inheritable, inheritable_reason = False, "attr_def.inheritable_blocked_by_key"
    else:
        inheritable, inheritable_reason = True, ""
    return FieldState(
        units=definition_type in NUMERIC_TYPES,
        enum_set=definition_type == "enum",
        computed=bool(computed),
        key=key,
        key_reason=key_reason,
        inheritable=inheritable,
        inheritable_reason=inheritable_reason,
        reference=bool(editing),
    )


def _parts(values: Iterable[object], *, skip: str = "") -> list[str]:
    """Non-empty string values, deduplicated, order preserved (`skip` dropped)."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        item = str(value).strip() if value is not None else ""
        if not item or item == skip or item in seen:
            continue
        seen.add(item)
        kept.append(item)
    return kept


def _first(values: Iterable[object]) -> str | None:
    """The single cleaned value or `None` (for the scalar optional fields)."""
    parts = _parts(values)
    return parts[0] if parts else None


def cleaned_payload(
    *,
    definition_type: str,
    is_key: bool,
    multilingual: bool,
    computed: bool,
    inheritable: bool,
    unit_id: object | None = None,
    display_unit_id: object | None = None,
    enum_set_id: object | None = None,
    computed_rule: object | None = None,
    dependencies: Iterable[object] = (),
    reads_inherited: bool = False,
    applicable: Iterable[object] = (),
    required: Iterable[object] = (),
    own_name: str = "",
) -> dict[str, object]:
    """The type- and flag-consistent part of the `POST`/`PATCH` payload.

    Mirrors the core's own rules (`is_key` forces `inheritable: false`) and drops
    everything the chosen type/flag makes meaningless:

    - units only for the numeric types (`number`, `range`),
    - the enum set only for `enum`,
    - rule/dependencies/reads-inherited only for `computed`,
    - required types only for the types the attribute applies to (an empty
      `applicable_types` means every type, so everything stays allowed) —
      otherwise completeness would demand a value for an attribute the form
      never offers,
    - `dependencies` without the attribute itself (a formula reading its own
      value is not a dependency).

    A cleared optional text field is `""`: the core treats a *missing* key as
    "leave it as it is", so the request must carry the clear.
    """
    is_key = bool(is_key) and definition_type == KEY_TYPE and not multilingual
    numeric = definition_type in NUMERIC_TYPES
    applicable_parts = _parts(applicable)
    required_parts = _parts(required)
    if applicable_parts:
        allowed = set(applicable_parts)
        required_parts = [item for item in required_parts if item in allowed]
    return {
        "inheritable": False if is_key else bool(inheritable),
        "is_key": is_key,
        "unit_id": (_first([unit_id]) or "") if numeric else "",
        "display_unit_id": (_first([display_unit_id]) or "") if numeric else "",
        "enum_set_id": (_first([enum_set_id]) or "") if definition_type == "enum" else "",
        "computed_rule": (_first([computed_rule]) or "") if computed else "",
        "dependencies": _parts(dependencies, skip=own_name) if computed else [],
        "computed_reads_inherited": bool(reads_inherited) if computed else False,
        "required_types": required_parts,
    }

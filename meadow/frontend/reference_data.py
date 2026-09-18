"""Reference data of classification standards (`attribute_definition.reference_data`).

An imported standard (ETIM) brings its **whole vocabulary** as attribute
definitions — 380 features in the shipped ETIM 10.0 bundle, 17 377 in the full
standard. Those definitions are not part of the customer's own attribute set:
they exist so a class can demand a feature (`category_attribute`) and so an
export can transmit the code. Buried between the own attributes they make the
product form and every attribute selection unusable.

The core flags them with `reference_data: true` (`schema.rs`, set by the standard
import). The rule implemented here:

- **Hidden where an attribute is chosen** for a new purpose — the product form's
  field list, the export field selection, the bulk/find-replace target. There the
  question is "which of *my* attributes do I want?".
- **Visible where data is read** — the grid columns, the class-features section,
  search. A stored value never disappears.
- **Visible again as soon as the object carries a value** for it: nothing that
  exists may become unreachable, and the value stays editable.
- **Adopting** an attribute (the action in the attribute catalog,
  `attribute_definitions.py`) clears the flag, so it becomes a normal own
  attribute.

Pure (no NiceGUI, no core calls), so the rules are unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def is_reference(definition: Mapping[str, object] | None) -> bool:
    """Whether a definition is reference data of a classification standard."""
    return bool(definition and definition.get("reference_data"))


def has_value(attributes: Mapping[str, object] | None, name: str) -> bool:
    """Whether an object carries a value for an attribute.

    A language map counts as set when **any** language has a non-empty entry (the
    same reading as `form.value_marker`); `0`/`false` are values, an empty list
    or map is not.
    """
    if not attributes or name not in attributes:
        return False
    value = attributes.get(name)
    if value is None:
        return False
    if isinstance(value, Mapping):
        return any(entry not in (None, "") for entry in value.values())
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def visible_in_form(
    definition: Mapping[str, object] | None,
    attributes: Mapping[str, object] | None,
    name: str,
) -> bool:
    """Whether a definition shows up in an object's form field list.

    Own attributes always do; a reference attribute only when the object already
    carries a value for it (so an adopted/imported value stays visible and
    editable instead of vanishing with the vocabulary).
    """
    if not is_reference(definition):
        return True
    return has_value(attributes, name)


def visible_in_picker(
    definition: Mapping[str, object] | None,
    *,
    include_reference: bool = False,
) -> bool:
    """Whether a definition is offered wherever an attribute is *chosen*.

    The callers that offer a "show standard attributes" switch pass
    `include_reference=True`, which is the escape hatch for exporting the raw
    standard codes as columns.
    """
    return include_reference or not is_reference(definition)


def reference_names(definitions: Iterable[Mapping[str, object]]) -> list[str]:
    """The names of the reference-data definitions (for a count/hint line)."""
    return [str(item.get("name")) for item in definitions if is_reference(item)]

"""The context blocks an assistant (or the panel) shows about a rule.

Three blocks, each generated from its runtime source:

* `contract_block()` — the script contract: the entry point, what `obj` is, the `ctx`
  fields and the `result` helpers **with their parameter lists** (read from
  `meadow.rules.contract` via `inspect`, so a renamed helper or a new parameter shows up
  here without anyone editing a text).
* `policy_block()` — the sandbox facts from [`meadow.ai.policy`][meadow.ai.policy].
* `attribute_facts()` / `attribute_names_text()` — the **projection** of the attribute
  vocabulary onto the *own* attributes of an object type.

Why the projection is not optional, measured 2026-09-19 (`plan/detail/ki-assistenz.md`
§2): the vocabulary holds 17 463 definitions (8.35 MB); for `product` **17 443** of them
are applicable and their names alone are 174 654 characters (~44 k tokens). The own
(non-`reference_data`) attributes of the same type are 54 (1 107 characters). A context
that lists everything is unusable — the names a rule may actually write would drown in
the standard's feature vocabulary.

Nothing here is a prompt: the blocks are the shared material for the contract panel
(stage 0), the generated reference and — later — the provider prompt (stage 1).
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import fields as dataclass_fields
from typing import Any, NamedTuple

from meadow.ai.examples import ExampleRule, examples_for
from meadow.ai.policy import (
    SCRIPT_ARGUMENTS,
    available_imports,
    forbidden_helpers,
    injected_globals,
    limits,
    sandbox_builtins,
)
from meadow.rules.contract import Context, Result


class AttributeFact(NamedTuple):
    """One own attribute of an object type, as the context needs it."""

    name: str
    value_type: str
    computed: bool


def _documented(value: Any) -> str:
    """The first docstring line of a callable, trimmed to one short sentence."""
    doc = inspect.getdoc(value) or ""
    first = doc.split("\n", 1)[0].strip()
    return first


def contract_block() -> str:
    """The script contract as text: entry point, `obj`, `ctx` fields, `result` helpers."""
    entry = f"def run({', '.join(SCRIPT_ARGUMENTS)}): ..."
    lines = [
        "Script contract — one function per rule:",
        f"  {entry}",
        "  obj  : read-only view of the object: obj['id'], obj['type'], obj['status'],",
        "         obj['attributes'] (own + inherited + resolved for the working language).",
        "         Never mutate obj — write through result.",
    ]
    ctx_fields = ", ".join(field.name for field in dataclass_fields(Context))
    lines.append(f"  ctx  : {ctx_fields}")
    lines.append("  result helpers (the only way a rule produces effects):")
    for name, member in inspect.getmembers(Result, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        lines.append(f"    result.{name}{_signature(member)} — {_documented(member)}")
    return "\n".join(lines)


def _signature(member: Any) -> str:
    """The call signature of a `Result` helper, without `self` and without quotes.

    The contract module uses `from __future__ import annotations`, so the annotations
    arrive as strings; `eval_str=True` resolves them, and the fallback keeps the quoted
    form for anything that cannot be resolved.
    """
    try:
        signature = str(inspect.signature(member, eval_str=True))
    except Exception:  # noqa: BLE001 - an unresolvable annotation must not break the block
        signature = str(inspect.signature(member))
    return signature.replace("(self, ", "(").replace("(self)", "()")


def policy_block() -> str:
    """The sandbox policy as text: available names, importable modules, hard limits."""
    facts_limits = limits()
    injected = injected_globals()
    injected_line = (
        f"  additionally injected: {', '.join(injected)}"
        if injected
        else "  additionally injected: (none today)"
    )
    return "\n".join(
        [
            "Sandbox policy (enforced by the runtime):",
            f"  available builtins ({len(sandbox_builtins())}) — use only these:",
            f"    {', '.join(sandbox_builtins())}",
            f"  importable modules — use only these: {', '.join(available_imports())}",
            f"  not available (fails at runtime): {', '.join(forbidden_helpers())}",
            "  note: getattr(value, 'name', None) exists, hasattr does not.",
            "  note: a module must be imported before use (`import re` at the top) —",
            "        writing `re.fullmatch(…)` without the import fails at runtime.",
            "  limits: "
            f"{facts_limits['max_source_bytes']:.0f} bytes script, "
            f"{facts_limits['timeout_seconds']}s wall clock, "
            f"{facts_limits['max_memory_bytes'] / (1024 * 1024):.0f} MiB traced objects",
            injected_line,
        ]
    )


def attribute_facts(vocabulary: list[dict], object_type: str) -> tuple[AttributeFact, ...]:
    """The **own** attributes applicable to `object_type`, sorted by name.

    Own means: no `reference_data` (the imported classification standard's vocabulary),
    which mirrors the rule the UI already follows wherever an attribute is *chosen*
    (`display.own_attribute_names`). Attributes without `applicable_types` apply to every
    type. A rule may only write attributes that are declared `computed`; the flag is part
    of the fact so a caller can say so without asking again.
    """
    facts: list[AttributeFact] = []
    for entry in vocabulary:
        if not isinstance(entry, dict) or entry.get("reference_data"):
            continue
        name = str(entry.get("name") or "")
        if not name:
            continue
        applicable = [str(value) for value in (entry.get("applicable_types") or [])]
        if applicable and object_type and object_type not in applicable:
            continue
        facts.append(
            AttributeFact(
                name=name,
                value_type=str(entry.get("type") or ""),
                computed=bool(entry.get("computed")),
            )
        )
    return tuple(sorted(facts, key=lambda fact: fact.name))


def attribute_names_text(facts: tuple[AttributeFact, ...] | list[AttributeFact]) -> str:
    """`name:type` per attribute, `(computed)` appended — one compact line."""
    return ", ".join(
        f"{fact.name}:{fact.value_type}" + ("(computed)" if fact.computed else "") for fact in facts
    )


def declared_computed(facts: tuple[AttributeFact, ...] | list[AttributeFact]) -> tuple[str, ...]:
    """The names a `computed` rule may write (`result.set_value`) for this type."""
    return tuple(fact.name for fact in facts if fact.computed)


def few_shot_examples(rule_type: str, limit: int = 3) -> list[ExampleRule]:
    """Example rules of one rule type (see [`meadow.ai.examples`][meadow.ai.examples])."""
    return examples_for(rule_type, limit)


# --- search (query DSL, stage 2) -----------------------------------------------


class SearchFieldFact(NamedTuple):
    """One own search field as the DSL prompt needs it."""

    key: str
    value_type: str
    operators: tuple[str, ...]


def search_field_facts(catalog: Mapping[str, Any]) -> tuple[SearchFieldFact, ...]:
    """The **own** search fields of `GET /search-fields`, sorted by key.

    Same rule as the attribute projection: where a field can be *chosen*, the UI
    offers the own ones (the search form hides reference data). Measured
    2026-09-19 on the dev instance: 18 400 fields in the catalog, 6.54 MB raw,
    **92** own.
    """
    fields = catalog.get("fields")
    if not isinstance(fields, list):
        return ()
    facts: list[SearchFieldFact] = []
    for entry in fields:
        if not isinstance(entry, Mapping) or entry.get("reference_data"):
            continue
        key = str(entry.get("key") or "")
        if not key:
            continue
        operators = entry.get("operators")
        facts.append(
            SearchFieldFact(
                key=key,
                value_type=str(entry.get("value_type") or ""),
                operators=(
                    tuple(str(operator) for operator in operators)
                    if isinstance(operators, list)
                    else ()
                ),
            )
        )
    return tuple(sorted(facts, key=lambda fact: fact.key))


def search_fields_text(facts: tuple[SearchFieldFact, ...] | list[SearchFieldFact]) -> str:
    """The fields grouped by their operator set: `key:type` lines under `[ops]` headings.

    Grouping is what makes this affordable. Measured 2026-09-19: the 92 own fields
    *with* their operators are **3 075** characters grouped this way and **7 947**
    as one line per field — the same information, 2.6× smaller, and the operator
    constraint stays visible instead of being reduced to the type.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for fact in facts:
        groups.setdefault(fact.operators, []).append(f"{fact.key}:{fact.value_type}")
    lines: list[str] = []
    for operators, entries in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"[{', '.join(operators) or 'no operators listed'}]")
        lines.append("  " + ", ".join(entries))
    return "\n".join(lines)


def search_operators_text(catalog: Mapping[str, Any]) -> str:
    """The operator table of the catalog: type, arity and whether it takes a list."""
    operators = catalog.get("operators")
    if not isinstance(operators, list) or not operators:
        return ""
    lines = ["Operators (arity = number of values, multi = the value is a list):"]
    for entry in operators:
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("type") or "")
        if not kind:
            continue
        lines.append(
            f"  {kind}: arity {entry.get('arity')}{' (multi)' if entry.get('multi') else ''}"
        )
    return "\n".join(lines)


def search_limits_text(catalog: Mapping[str, Any]) -> str:
    """The DSL limits in words (the core enforces them when it validates a query)."""
    limits_value = catalog.get("limits")
    if not isinstance(limits_value, Mapping) or not limits_value:
        return ""
    page_min = limits_value.get("min")
    page_max = limits_value.get("max")
    page_default = limits_value.get("default")
    parts = [f"`limit` between {page_min} and {page_max} (default {page_default})"]
    if limits_value.get("max_conditions"):
        parts.append(f"at most {limits_value['max_conditions']} conditions per level")
    if limits_value.get("max_depth"):
        parts.append(f"nesting depth at most {limits_value['max_depth']}")
    return "Limits: " + ", ".join(parts) + "."


# --- JSON configuration (connector parameters, stage 2) -------------------------


class ConnectorFieldFact(NamedTuple):
    """One field of a connector type's parameter schema."""

    key: str
    value_type: str
    required: bool
    enum: tuple[str, ...]
    minimum: float | None
    maximum: float | None
    default: str
    secret: bool
    show_if: str
    label: str
    help: str


def _label(labels: Mapping[str, str] | None, key: Any) -> str:
    """The UI text of a `label_key`/`help_key` (empty when unknown).

    The schema carries i18n keys, not text: the words a user sees live in the
    frontend translations. Passing them in keeps exactly one source of truth for
    the wording — and the prompt speaks the same language as the form.
    """
    if not labels or not isinstance(key, str):
        return ""
    return labels.get(key, "")


def _render_default(value: Any) -> str:
    """A default value as a short token (`[]`, `{}` for containers, else the literal)."""
    if isinstance(value, (list, dict)):
        return "[]" if isinstance(value, list) else "{}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def connector_type_facts(
    schema: Any, type_key: str, labels: Mapping[str, str] | None = None
) -> tuple[ConnectorFieldFact, ...]:
    """The fields of one connector type, flattened out of their display groups.

    `GET /connector-types` returns the catalog the connector form renders: types
    with `groups`, each holding `fields`. A prompt needs the fields in one list
    (the groups are display, not meaning), so this flattens them and keeps what
    decides a valid value: type, required flag, enum, range, defaults, `show_if`
    and whether the field is a secret.
    """
    if not isinstance(schema, list):
        return ()
    for entry in schema:
        if not isinstance(entry, Mapping) or entry.get("type") != type_key:
            continue
        facts: list[ConnectorFieldFact] = []
        groups = entry.get("groups")
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, Mapping):
                continue
            for field in group.get("fields") or []:
                if not isinstance(field, Mapping):
                    continue
                key = str(field.get("key") or "")
                if not key:
                    continue
                enum = field.get("enum")
                show_if = field.get("show_if")
                condition = ""
                if isinstance(show_if, Mapping):
                    when = _render_default(show_if.get("equals"))
                    condition = f"only when {show_if.get('key')} = {when}"
                facts.append(
                    ConnectorFieldFact(
                        key=key,
                        value_type=str(field.get("type") or ""),
                        required=bool(field.get("required")),
                        enum=tuple(str(option) for option in enum)
                        if isinstance(enum, list)
                        else (),
                        minimum=field.get("minimum")
                        if isinstance(field.get("minimum"), (int, float))
                        else None,
                        maximum=field.get("maximum")
                        if isinstance(field.get("maximum"), (int, float))
                        else None,
                        default=_render_default(field.get("default")) if "default" in field else "",
                        secret=bool(field.get("secret")),
                        show_if=condition,
                        label=_label(labels, field.get("label_key")),
                        help=_label(labels, field.get("help_key")),
                    )
                )
        return tuple(facts)
    return ()


def connector_types_text(schema: Any, labels: Mapping[str, str] | None = None) -> str:
    """The available connector types with their direction (`inbound`/`outbound`)."""
    if not isinstance(schema, list):
        return ""
    lines = []
    for entry in schema:
        if not isinstance(entry, Mapping):
            continue
        type_key = str(entry.get("type") or "")
        if not type_key:
            continue
        label = _label(labels, entry.get("label_key")) or type_key
        lines.append(f"  {type_key} ({entry.get('direction')}): {label}")
    return "Connector types:\n" + "\n".join(lines) if lines else ""


def connector_fields_text(facts: tuple[ConnectorFieldFact, ...] | list[ConnectorFieldFact]) -> str:
    """One line per field: `key: type`, constraints, and the UI wording.

    A dotted key is a **nested** parameter (`filter.event_types` →
    `{"filter": {"event_types": …}}`); the prompt says so, because that mapping is
    the one thing a model gets wrong here.
    """
    lines: list[str] = []
    for fact in facts:
        parts = [f"{fact.key}: {fact.value_type}"]
        if fact.required:
            parts.append("required")
        if fact.enum:
            parts.append("one of " + " | ".join(fact.enum))
        if fact.minimum is not None or fact.maximum is not None:
            parts.append(f"{fact.minimum:g}–{fact.maximum:g}")
        if fact.default:
            parts.append(f"default {fact.default}")
        if fact.secret:
            parts.append("secret")
        if fact.show_if:
            parts.append(fact.show_if)
        line = ", ".join(parts)
        note = fact.label or fact.help
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines)

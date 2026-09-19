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

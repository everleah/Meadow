"""Prompt assembly per area — one system prompt per artefact, built from the facts.

Two rules govern this module:

* **The facts come from the runtime**, never from prose maintained here:
  [`contract_block`][meadow.ai.context.contract_block] and
  [`policy_block`][meadow.ai.context.policy_block] are generated from
  `meadow.rules.contract` and the live sandbox, the attribute list is the
  projection of the vocabulary.
* **One system prompt per area.** The spike (2026-09-19) showed that a model with
  the *rule* prompt asked for a search query answered with `def run(obj, ctx,
  result)` — the artefact has to be named in the system role
  (`plan/detail/ki-assistenz.md` §8).

Area blocks are short by design: the material a suggestion needs is the contract,
the policy, the own attributes and two or three examples — not the whole model.
"""

from __future__ import annotations

from meadow.ai.context import AttributeFact, attribute_names_text
from meadow.ai.examples import ExampleRule

#: Areas the assistant knows (the core validates the same list).
AREAS = ("rule", "computed_attribute")
#: Modes the assistant knows.
MODES = ("create", "edit", "explain")

_AREA_TITLES = {
    "rule": "business rule",
    "computed_attribute": "computed attribute (a business rule with `rule_type = computed`)",
}

_RULE_SYSTEM = """\
You are the rule assistant of Meadow PIM, a product information management system.
You write the body of a business rule: exactly one function, `def run(obj, ctx, result)`.

Non-negotiable:
- Exactly one function, named `run`, with the parameters (obj, ctx, result).
- Use only the builtins, modules and helpers listed in the sandbox policy below.
- Read values from `obj["attributes"]`; never mutate `obj`.
- Produce every effect through the `result` helpers.
- Be defensive: a missing or unexpected value must not raise.
- English identifiers, English comments. At most a handful of comments.
- Answer with the Python code only: no explanation, no markdown fence.
"""

_COMPUTED_SYSTEM = """\
You are the rule assistant of Meadow PIM, a product information management system.
You write a `computed` business rule `def run(obj, ctx, result)` that fills one
attribute, plus its metadata.

Non-negotiable:
- Exactly one function, named `run`, with the parameters (obj, ctx, result).
- Use only the builtins, modules and helpers listed in the sandbox policy below.
- `result.set_value("attribute", value)` may only write the attributes listed as
  declared computed — any other name is rejected by the core on save.
- Be defensive: a missing or unexpected input value must not raise. If the inputs
  are not sufficient, write nothing at all instead of a wrong number.
- English identifiers, English comments.
- Answer with a single JSON object, no markdown fence, with exactly these keys:
  {"script": "<the Python code>", "rule_name": "<snake_case name for the rule>",
   "dependencies": ["<attribute names the script reads>"],
   "computed_reads_inherited": <true if it depends on inherited values>,
   "description": "<one sentence for the rule description>"}
"""

_SYSTEM_PROMPTS = {"rule": _RULE_SYSTEM, "computed_attribute": _COMPUTED_SYSTEM}

_EXPLAIN_SYSTEM = """\
You are the documentation assistant of Meadow PIM, a product information management
system. You explain an existing business rule to a colleague who maintains product data.

- Answer in prose, at most three sentences, no code, no bullet list, no markdown.
- Say what the rule checks or computes and when it reacts.
- Do not invent behaviour the script does not have.
"""


def system_prompt(area: str, mode: str) -> str:
    """The system prompt for one area and mode."""
    if mode == "explain":
        return _EXPLAIN_SYSTEM
    return _SYSTEM_PROMPTS.get(area, _RULE_SYSTEM)


def _examples_block(examples: list[ExampleRule]) -> str:
    """The few-shot block under its own heading (empty when there is none)."""
    if not examples:
        return ""
    lines = ["Examples of the same kind, in the house style:"]
    for example in examples:
        lines.append(f"--- {example.name} ({example.rule_type})")
        lines.append(example.script.rstrip())
    return "\n".join(lines)


def _attributes_block(facts: tuple[AttributeFact, ...], declared: tuple[str, ...]) -> str:
    """The projected attribute list plus the writable names."""
    if not facts:
        return "Own attributes of the object type: (the list could not be loaded)"
    lines = [
        f"Own attributes of the object type ({len(facts)}), as name:type:",
        "  " + attribute_names_text(facts),
    ]
    if declared:
        lines.append("Declared as computed (the only names set_value may write):")
        lines.append("  " + ", ".join(declared))
    else:
        lines.append("No attribute of this type is declared as computed yet.")
    return "\n".join(lines)


def user_prompt(
    *,
    area: str,
    mode: str,
    description: str,
    rule_type: str | None,
    object_type: str | None,
    facts: tuple[AttributeFact, ...],
    declared: tuple[str, ...],
    examples: list[ExampleRule],
    contract: str,
    policy: str,
    current_script: str | None = None,
    target_attribute: str | None = None,
    language: str | None = None,
) -> str:
    """The user message: the task, the material and the required answer shape."""
    sections: list[str] = []
    heading = "Explain this" if mode == "explain" else "Write this"
    sections.append(f"{heading}: {_AREA_TITLES.get(area, area)}")
    if rule_type:
        sections.append(f"Rule type: {rule_type}")
    sections.append(f"Object type: {object_type}" if object_type else "Object type: all types")
    if target_attribute:
        sections.append(f"Attribute to fill: {target_attribute}")
    if description.strip():
        sections.append(f"Description from the user:\n{description.strip()}")
    if current_script and current_script.strip():
        label = "The script to explain" if mode == "explain" else "The current script to improve"
        sections.append(f"{label}:\n{current_script.strip()}")
    if mode == "edit":
        sections.append("Keep what works and change only what the description asks for.")

    if mode != "explain":
        sections.append(_attributes_block(facts, declared))
        sections.append(contract)
        sections.append(policy)
        block = _examples_block(examples)
        if block:
            sections.append(block)
    if language:
        # The prose follows the user's language, the code stays English — for
        # `explain` the answer *is* the prose, so the sentence applies there too
        # (measured 2026-09-19: without it a German request came back in English).
        what = "the answer" if mode == "explain" else "the description"
        sections.append(f"Write {what} in the language `{language}`; the code stays English.")
    return "\n\n".join(sections)


def retry_prompt(message: str) -> str:
    """The correction message of the single self-correction attempt.

    The failure is quoted verbatim: the model gets one chance to fix exactly what
    the policy check rejected (`plan/detail/ki-assistenz.md` §6.5).
    """
    return (
        "The script was rejected by the policy check with this message:\n"
        f"  {message}\n"
        "Fix exactly that and answer again with the complete artefact."
    )

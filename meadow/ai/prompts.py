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
from meadow.ai.examples import ExampleRule, search_examples

#: Areas the assistant knows (the core validates the same list).
AREAS = (
    "rule",
    "computed_attribute",
    "search",
    "connector",
    "export_profile",
    "job",
    "import_mapping",
    "group_permissions",
)
#: Modes the assistant knows.
MODES = ("create", "edit", "explain")

_AREA_TITLES = {
    "rule": "business rule",
    "computed_attribute": "computed attribute (a business rule with `rule_type = computed`)",
    "search": "saved search (a query in Meadow's search DSL)",
    "connector": "connector configuration (the `parameters` object of a connector)",
    "export_profile": "export profile (an export format plus its `parameters`)",
    "job": "job definition (a `type`, an optional `schedule` and its `parameters`)",
    "import_mapping": "import column mapping (which file column fills which field)",
    "group_permissions": "group document (the permission lists and the data scope)",
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

_SEARCH_SYSTEM = """\
You are the search assistant of Meadow PIM, a product information management system.
You write a saved search: one JSON object in Meadow's search DSL.

Non-negotiable:
- Answer with the JSON object only: no explanation, no markdown fence.
- `where` is a node: `{"op": "and"|"or", "conditions": [...]}`, where every
  condition is `{"field": <field>, "operator": <operator>, "value": <value>}`.
  Nested nodes are allowed inside `conditions`.
- Use only the fields and operators listed below; a field accepts only the
  operators listed with it.
- `value` is a single value, or a list when the operator is marked `multi`.
- A missing value is **not** a match: use `is_null` for "has no value",
  `not_empty` for "has a value".
- `sort` is optional (`[{"field": ..., "order": "asc"|"desc"}]`), `limit` is a number
  inside the documented range.
"""

_CONNECTOR_SYSTEM = """\
You are the configuration assistant of Meadow PIM, a product information management system.
You write the `parameters` object of a connector: one JSON object.

Non-negotiable:
- Answer with the JSON object only: no explanation, no markdown fence.
- Use only the fields listed below, with the listed type and the listed values
  for a field that names its options.
- A dotted field name means nesting: `filter.event_types` becomes
  `{"filter": {"event_types": [...]}}`.
- Leave out fields the user did not ask for unless the field is required or has
  a default that matters. Never invent credentials — use a placeholder like
  "EXAMPLE-TOKEN" and say so in the description field if one is needed.
"""

_EXPORT_PROFILE_SYSTEM = """\
You are the export assistant of Meadow PIM, a product information management system.
You write an export profile: one JSON object with the keys `format` and `parameters`.

Non-negotiable:
- Answer with the JSON object only: no explanation, no markdown fence.
- `format` is one of the allowed formats listed below.
- `parameters` uses only the listed parameter keys with the listed shape; a
  parameter you do not need stays out.
- `fields` lists attribute names (without the `attributes.` prefix); leaving it
  out or empty means the object's own attributes.
- `locales` lists language codes; leaving it out means the working language.
- Never invent attribute or language names that are not in the context you get.
"""

_SYSTEM_PROMPTS = {"rule": _RULE_SYSTEM, "computed_attribute": _COMPUTED_SYSTEM}

_JOB_SYSTEM = """\
You are the automation assistant of Meadow PIM, a product information management system.
You write a job definition: one JSON object with the keys `type` and `parameters`,
plus `schedule` when the job should run on a timetable.

Non-negotiable:
- Answer with the JSON object only: no explanation, no markdown fence.
- `type` is the job type named below; `parameters` uses only the parameter keys
  listed for it, with the listed shape.
- A parameter marked `required` must be set; a parameter that names its allowed
  values must use one of them; leave out what the user did not ask for.
- `schedule` is a cron expression with six fields (seconds first), for example
  "0 0 3 * * *" for daily at 03:00. Leave it out for "only when triggered".
- Object ids look like `object:<key>`, imported documents like
  `import_document:<key>`, config documents like `config_document:<key>`. Never
  invent an id, an attribute or a language that is not in the context you get.
- For the type `bulk_operation`: `op` picks the operation, and that operation's
  own parameters go into `parameters.parameters` — use only the keys listed for
  that operation.
"""

_IMPORT_MAPPING_SYSTEM = """\
You are the import assistant of Meadow PIM, a product information management system.
You map the columns of an uploaded CSV/Excel file to Meadow's fields and attributes.

Non-negotiable:
- Answer with a single JSON object, no explanation, no markdown fence:
  {"mapping": {"<column>": "<target>", ...}}
- Use the column names exactly as they are listed, and list **every** column —
  a column you do not want to import gets `""`.
- A target is exactly one of:
  - `""` — ignore the column,
  - `id`, `type` or `status` — a base field of the object (only `id`/`type`/`status`
    may be written this way, never with a language),
  - `<attribute>` — the value of that attribute,
  - `<attribute>|<language>` — the value of that multilingual attribute in that
    language.
- Use only the attribute names listed as targets; never invent one. If a column
  holds something no listed attribute describes, ignore the column (`""`) instead
  of guessing a name.
- Never map two columns to the same target: only the last one would survive.
- If the object type has a business key, a column that clearly holds it must fill it.
- Prefer the value shape over the header when the header is cryptic, and say so in
  the description field if a decision rests on a guess.
"""

_GROUP_PERMISSIONS_SYSTEM = """\
You are the access assistant of Meadow PIM, a product information management system.
You write the permission lists and the data scope of a user group: one JSON object.

Non-negotiable:
- Answer with the JSON object only: no explanation, no markdown fence, with exactly
  these keys: {"grant_permissions": [...], "deny_permissions": [...],
  "scope": {"rules": [...]}}
- The permission lists use only the permission keys listed below; never invent one.
  A key must not appear in both lists.
- Every permission a role holds by default is already available to the group's
  members, so the lists are **deviations**: `deny` takes one away, `grant` adds one
  the default does not have. A group that should behave like the plain role of its
  members gets both lists empty.
- `scope.rules` uses only the two rule shapes below, with the listed values, and a
  rule's `value` list is never empty. No rules means no restriction.
- Return the **complete** state, not a diff: in the `edit` mode you are given the
  current state, and every permission and every rule the description does not ask to
  change must appear again **unchanged** — what you leave out is removed (a permission
  falls back to its role default, a missing type/status rule no longer restricts).
- If a wish cannot be expressed with permissions and a type/status scope ("only the
  objects this user created", "only the category X"), leave it out and name it in
  `note` — one sentence, never a guess.
- English identifiers; answer with the JSON object only.
"""

_EXPLAIN_SYSTEM = """\
You are the documentation assistant of Meadow PIM, a product information management
system. You explain an existing business rule to a colleague who maintains product data.

- Answer in prose, at most three sentences, no code, no bullet list, no markdown.
- Say what the rule checks or computes and when it reacts.
- Do not invent behaviour the script does not have.
"""

_SEARCH_EXPLAIN_SYSTEM = """\
You are the documentation assistant of Meadow PIM, a product information management
system. You explain an existing saved search to a colleague who works with product data.

- Answer in prose, at most three sentences, no code, no bullet list, no markdown.
- Say which objects it selects (fields, operators, values) and what the result is
  limited or sorted by.
- Mention that a missing value never matches, when the query uses `is_null` or
  `not_empty`.
- Do not invent conditions the query does not contain.
"""

_JOB_EXPLAIN_SYSTEM = """\
You are the documentation assistant of Meadow PIM, a product information management
system. You explain an existing job definition to a colleague who maintains product data.

- Answer in prose, at most three sentences, no code, no bullet list, no markdown.
- Say which job type it is, what one run does and when it runs (the schedule, or
  only on demand).
- Do not invent parameters or effects the definition does not have.
"""

_MAPPING_EXPLAIN_SYSTEM = """\
You are the documentation assistant of Meadow PIM, a product information management
system. You explain an existing import column mapping to a colleague who works with
product data.

- Answer in prose, at most three sentences, no code, no bullet list, no markdown.
- Say which columns are imported into which fields, which columns are ignored, and
  name the business key when the mapping fills it.
- Do not invent columns or targets the mapping does not have.
"""


def system_prompt(area: str, mode: str) -> str:
    """The system prompt for one area and mode.

    Per area on purpose: the spike showed that a prompt naming the wrong artefact
    makes the model answer with it (`plan/detail/ki-assistenz.md` §8).
    """
    if mode == "explain":
        if area == "search":
            return _SEARCH_EXPLAIN_SYSTEM
        if area == "job":
            return _JOB_EXPLAIN_SYSTEM
        if area == "import_mapping":
            return _MAPPING_EXPLAIN_SYSTEM
        return _EXPLAIN_SYSTEM
    if area == "search":
        return _SEARCH_SYSTEM
    if area == "connector":
        return _CONNECTOR_SYSTEM
    if area == "export_profile":
        return _EXPORT_PROFILE_SYSTEM
    if area == "job":
        return _JOB_SYSTEM
    if area == "import_mapping":
        return _IMPORT_MAPPING_SYSTEM
    if area == "group_permissions":
        return _GROUP_PERMISSIONS_SYSTEM
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


def search_user_prompt(
    *,
    mode: str,
    description: str,
    fields: str,
    operators: str,
    limits: str,
    current_query: str | None = None,
    language: str | None = None,
) -> str:
    """The user message for a search request: task, field catalogue, limits.

    The catalogue is the **projection** of `GET /search-fields`
    (`meadow.ai.context.search_fields_text`); the core validates the answer, so the
    field list here is guidance, not the guard.
    """
    sections: list[str] = []
    heading = "Explain this" if mode == "explain" else "Write this"
    sections.append(f"{heading}: {_AREA_TITLES['search']}")
    if description.strip():
        sections.append(f"Description from the user:\n{description.strip()}")
    if current_query and current_query.strip():
        label = "The query to explain" if mode == "explain" else "The current query to improve"
        sections.append(f"{label}:\n{current_query.strip()}")
    if mode == "edit":
        sections.append("Keep what works and change only what the description asks for.")
    if mode != "explain":
        sections.append("Search fields, grouped by the operators they accept:\n" + fields)
        if operators:
            sections.append(operators)
        if limits:
            sections.append(limits)
        examples = search_examples()
        if examples:
            sections.append("Examples of the same kind:\n" + "\n".join(examples))
    if language:
        what = "the answer" if mode == "explain" else "the search name and description"
        sections.append(f"Write {what} in the language `{language}`; field names stay as they are.")
    return "\n\n".join(sections)


def config_user_prompt(
    *,
    area: str,
    mode: str,
    description: str,
    subject: str,
    fields: str,
    current_config: str | None = None,
    language: str | None = None,
) -> str:
    """The user message for a JSON-parameter request (connectors, profiles, jobs).

    `subject` names the concrete target in one line (connector type and name, job
    type and description), `fields` is the schema projection of that target.
    """
    sections: list[str] = []
    heading = "Explain this" if mode == "explain" else "Write this"
    sections.append(f"{heading}: {_AREA_TITLES.get(area, area)}")
    sections.append(subject)
    if description.strip():
        sections.append(f"Description from the user:\n{description.strip()}")
    if current_config and current_config.strip():
        label = (
            "The configuration to explain"
            if mode == "explain"
            else "The current configuration to improve"
        )
        sections.append(f"{label}:\n{current_config.strip()}")
    if mode == "edit":
        sections.append("Keep what works and change only what the description asks for.")
    if mode != "explain":
        sections.append("Fields of this configuration:\n" + fields)
    if language:
        what = "the answer" if mode == "explain" else "any prose you add"
        sections.append(f"Write {what} in the language `{language}`; field names stay as they are.")
    return "\n\n".join(sections)


def mapping_user_prompt(
    *,
    mode: str,
    description: str,
    subject: str,
    targets: str,
    columns: str,
    current_mapping: str | None = None,
    language: str | None = None,
) -> str:
    """The user message for an import column mapping.

    `subject` names the concrete target (object type, business key, data languages),
    `targets` is the attribute list a mapping may write and `columns` the file's
    headers with their value shapes — the columns come last because they are what
    the answer is built from, right before the required shape.
    """
    sections: list[str] = []
    heading = "Explain this" if mode == "explain" else "Write this"
    sections.append(f"{heading}: {_AREA_TITLES['import_mapping']}")
    sections.append(subject)
    if description.strip():
        sections.append(f"Description from the user:\n{description.strip()}")
    if current_mapping and current_mapping.strip():
        label = "The mapping to explain" if mode == "explain" else "The current mapping to improve"
        sections.append(f"{label}:\n{current_mapping.strip()}")
    if mode == "edit":
        sections.append("Keep what works and change only what the description asks for.")
    if mode != "explain":
        sections.append("Targets available (nothing else may be written):\n" + targets)
        sections.append(
            "Columns of the file (header and what the values look like, no values):\n" + columns
        )
        sections.append('Answer shape: {"mapping": {"<column>": "<target>"}}')
    if language:
        what = "the answer" if mode == "explain" else "any prose you add"
        sections.append(
            f"Write {what} in the language `{language}`; column names and targets stay as they are."
        )
    return "\n\n".join(sections)


def group_permissions_user_prompt(
    *,
    mode: str,
    description: str,
    permissions: str,
    scope_schema: str,
    current_state: str | None = None,
    language: str | None = None,
) -> str:
    """The user message for a group document (permission lists and scope).

    `permissions` is the catalog projection — the keys the write path accepts, each with
    the roles that hold it by default; `scope_schema` describes the two rule shapes the
    assistant may write. The current state arrives as JSON (the editor serialises its
    matrix and its scope card), so the `edit` mode has a baseline to keep.
    """
    sections: list[str] = []
    heading = "Explain this" if mode == "explain" else "Write this"
    sections.append(f"{heading}: {_AREA_TITLES['group_permissions']}")
    if description.strip():
        sections.append(f"Description from the user:\n{description.strip()}")
    if current_state and current_state.strip():
        label = (
            "The group document to explain" if mode == "explain" else "The current state to change"
        )
        sections.append(f"{label} (JSON, as the editor shows it):\n{current_state.strip()}")
    if mode == "edit":
        sections.append("Keep everything the description does not ask to change.")
    if mode != "explain":
        sections.append("Permissions available (nothing else may be named):\n" + permissions)
        sections.append("Scope:\n" + scope_schema)
        sections.append(
            'Answer shape: {"grant_permissions": [...], "deny_permissions": [...], '
            '"scope": {"rules": [...]}}'
        )
    if language:
        sections.append(f"Write any prose you add in the language `{language}`.")
    return "\n\n".join(sections)


def retry_prompt(message: str) -> str:
    """The correction message of the single self-correction attempt.

    The failure is quoted verbatim: the model gets one chance to fix exactly what
    the check rejected (`plan/detail/ki-assistenz.md` §6.5) — the sandbox policy
    for a script, the core's query validation for a search, the catalog and the
    scope validation for a group document.
    """
    return (
        "The answer was rejected with this message:\n"
        f"  {message}\n"
        "Fix exactly that and answer again with the complete artefact."
    )

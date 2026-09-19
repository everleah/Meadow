"""Everything an assistant needs to know about Meadow — read from the code, not copied.

The package holds the *knowledge* an AI assistant (and the contract panel in the rule
editor) needs to write artefacts this system accepts, plus the machinery that turns a
description into a suggestion:

* [`policy`][meadow.ai.policy] — what the rule sandbox really allows: the builtins that
  exist, the modules that may be imported, the hard limits, and the names that are not
  available although the compiler accepts them. Read from the running sandbox
  (`meadow.rules`), so it cannot drift.
* [`context`][meadow.ai.context] — the blocks built from those facts and from the
  runtime metadata: the script contract (generated from `meadow.rules.contract`), the
  policy text and the **projection** of the attribute vocabulary onto the own
  attributes of one object type.
* [`examples`][meadow.ai.examples] — the curated few-shot example rules, covered by a
  compile test.
* [`prompts`][meadow.ai.prompts] — one system prompt per area plus the user message
  assembled from those blocks. The spike showed why the prompt is per area: with the
  *rule* prompt a search request came back as `def run(obj, ctx, result)`
  (`plan/detail/ki-assistenz.md` §8).
* [`provider`][meadow.ai.provider] — the OpenAI-compatible chat call (`httpx` only, no
  provider SDK per vendor). Holds no state and no secret.
* [`service`][meadow.ai.service] — the whole loop: context, one model call, the policy
  check, and **one** self-correction attempt. It stores nothing.

The calls run in the worker process (`meadow/worker/ai_service.py`); the core sends the
resolved configuration with every request, so the API key never reaches the browser
(stage 1 of `plan/detail/ki-assistenz.md`).
"""

from __future__ import annotations

from meadow.ai.context import (
    AttributeFact,
    attribute_facts,
    attribute_names_text,
    contract_block,
    few_shot_examples,
    policy_block,
)
from meadow.ai.policy import check_script, policy_facts

__all__ = [
    "AttributeFact",
    "attribute_facts",
    "attribute_names_text",
    "check_script",
    "contract_block",
    "few_shot_examples",
    "policy_block",
    "policy_facts",
]

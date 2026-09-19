"""Everything an assistant needs to know about Meadow — read from the code, not copied.

The package holds the *knowledge* an AI assistant (and later a capability panel)
needs to write artefacts this system accepts:

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

Deliberately **not** part of this package yet: the provider client and the generation
service (stage 1 of `plan/detail/ki-assistenz.md`). Nothing here performs I/O, holds a
secret or needs a model — it is the part that can be built and verified without one.
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

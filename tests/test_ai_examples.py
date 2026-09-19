"""The shipped example rules must be usable — they are the prompt's few-shot set.

An example that does not survive the sandbox is worse than no example: it teaches a style
the runtime rejects. `tests/test_ai_policy.py` covers the checker; this file holds the
examples themselves to it (`plan/detail/ki-assistenz.md` §10).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.ai.examples import EXAMPLES, examples_for  # noqa: E402
from meadow.ai.policy import check_script  # noqa: E402


def test_every_example_compiles_under_the_sandbox_policy() -> None:
    for example in EXAMPLES:
        ok, message = check_script(example.script)
        assert ok, f"{example.name}: {message}"


def test_the_examples_cover_the_rule_types_that_have_one() -> None:
    covered = {example.rule_type for example in EXAMPLES}
    assert {"computed", "validation", "workflow_guard", "normalization"} <= covered
    # A type without an example is fine — the caller has to say so, see
    # `context.few_shot_examples("bulk") == []`.
    assert "bulk" not in covered


def test_examples_are_defensive_against_missing_values() -> None:
    """The style rule the prompt states: a missing value must not raise.

    Checked by running each example against an object without any attributes — a rule
    that assumes a value exists fails here (the sandbox reports it as an error, not as
    an exception, so the assertion is on the outcome being a *result*, not a crash).
    """
    from meadow.rules import Context, execute_rule

    empty: dict[str, object] = {
        "id": "object:empty",
        "type": "product",
        "status": "draft",
        "attributes": {},
    }
    for example in EXAMPLES:
        result = execute_rule(
            example.script,
            empty,
            Context(rule_type=example.rule_type, object_type="product", actor="system"),
        )
        assert result is not None, example.name


def test_examples_for_limits_the_selection() -> None:
    assert len(examples_for("computed", limit=1)) == 1
    assert examples_for("computed", limit=1)[0].rule_type == "computed"

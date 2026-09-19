"""The rule sandbox's policy, as an assistant (and the editor panel) must see it.

These tests pin the two facts the AI-assist plan is built on
(`plan/detail/ki-assistenz.md` §2, measured 2026-09-19):

* the policy is **read from the running sandbox**, so it cannot describe something the
  runtime does not do — the test asks both sides and compares them;
* `hasattr` and friends pass the compiler and fail only at runtime, so `check_script`
  needs the AST pre-check **in addition** to the real compile policy.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meadow.ai.policy import (  # noqa: E402 - after the path fix
    CONTRACT_NAMES,
    SCRIPT_ARGUMENTS,
    available_imports,
    check_script,
    forbidden_helpers,
    injected_globals,
    limits,
    policy_facts,
    sandbox_builtins,
)
from meadow.rules.contract import RULE_TYPES, Context, Result  # noqa: E402
from meadow.rules.sandbox import build_globals  # noqa: E402
from meadow.rules.whitelist import DEFAULT_WHITELIST  # noqa: E402

VALID = 'def run(obj, ctx, result):\n    result.set_value("gross_price", 1)\n'
WITH_HASATTR = (
    "def run(obj, ctx, result):\n"
    '    if hasattr(obj, "attributes"):\n'
    '        result.set_value("gross_price", 1)\n'
)
WITH_IMPORT = 'import os\n\n\ndef run(obj, ctx, result):\n    result.set_value("a", os.getcwd())\n'
WITH_PRIVATE = 'def run(obj, ctx, result):\n    result.set_value("a", obj.__class__)\n'


def _real_namespace() -> dict[str, Any]:
    """The namespace the sandbox really builds for one rule execution."""
    return build_globals(
        obj=MappingProxyType({"id": "object:test", "type": "product", "attributes": {}}),
        ctx=Context(rule_type="computed", object_type="product"),
        result=Result(),
        log=lambda *_: None,
        whitelist=DEFAULT_WHITELIST,
        bindings=None,
    )


def test_the_policy_is_read_from_the_running_sandbox() -> None:
    namespace = _real_namespace()
    assert set(sandbox_builtins()) == set(namespace["__builtins__"])


def test_the_contract_names_are_the_injected_script_globals() -> None:
    namespace = _real_namespace()
    for name in CONTRACT_NAMES:
        assert namespace[name] is not None or name == "obj"
    assert SCRIPT_ARGUMENTS == ("obj", "ctx", "result")


def test_injected_globals_lists_only_extras() -> None:
    # Today the sandbox injects nothing beyond the contract; the tuple must not repeat
    # the contract names, and the rule types are the core's five.
    assert set(injected_globals()).isdisjoint(set(CONTRACT_NAMES))
    assert len(RULE_TYPES) == 5


def test_available_imports_are_the_whitelist() -> None:
    assert available_imports() == tuple(sorted(DEFAULT_WHITELIST))
    assert "json" in available_imports()
    assert "os" not in available_imports()


def test_hasattr_is_reported_as_missing_and_getattr_is_not() -> None:
    forbidden = forbidden_helpers()
    assert "hasattr" in forbidden
    assert "getattr" not in forbidden  # the sandbox provides the guarded getattr
    assert "eval" in forbidden and "open" in forbidden


def test_limits_mirror_the_sandbox_defaults() -> None:
    facts = limits()
    assert facts["max_source_bytes"] == 65536
    assert facts["timeout_seconds"] == 1.5
    assert facts["max_memory_bytes"] == 256 * 1024 * 1024


def test_check_script_accepts_a_plain_rule() -> None:
    ok, message = check_script(VALID)
    assert ok, message
    assert "sandbox policy" in message


def test_check_script_accepts_a_rule_using_available_helpers() -> None:
    source = (
        "import json\n"
        "import math\n"
        "\n"
        "\n"
        "def run(obj, ctx, result):\n"
        "    attrs = obj['attributes']\n"
        "    values = sorted(str(key) for key in attrs)\n"
        "    rate = getattr(obj, 'rate', None) or math.pi\n"
        "    result.log(json.dumps({'keys': values, 'rate': rate}))\n"
    )
    ok, message = check_script(source)
    assert ok, message


def test_check_script_requires_exactly_one_run_function() -> None:
    ok, message = check_script(
        "def helper():\n    pass\n\n\ndef run(obj, ctx, result):\n    pass\n"
    )
    assert not ok
    assert "exactly one function `run`" in message

    ok, message = check_script("def process(obj, ctx, result):\n    pass\n")
    assert not ok
    assert "exactly one function `run`" in message


def test_check_script_requires_the_contract_signature() -> None:
    ok, message = check_script("def run(a, b):\n    pass\n")
    assert not ok
    assert "obj, ctx, result" in message


def test_check_script_reports_syntax_errors() -> None:
    ok, message = check_script("def run(obj, ctx, result)\n    pass\n")
    assert not ok
    assert "not valid Python" in message


def test_check_script_catches_names_the_compiler_accepts() -> None:
    """The gap the plan measured: `hasattr` compiles, the call fails at runtime."""
    ok, message = check_script(WITH_HASATTR)
    assert not ok
    assert "does not provide" in message and "hasattr" in message
    assert "getattr" in message  # the message points at the working alternative


def test_check_script_defers_imports_and_private_attributes_to_the_compiler() -> None:
    ok, message = check_script(WITH_IMPORT)
    assert not ok and "not allowed" in message

    ok, message = check_script(WITH_PRIVATE)
    assert not ok and "invalid attribute name" in message


def test_policy_facts_are_plain_data() -> None:
    facts = policy_facts()
    assert facts["builtins"] == list(sandbox_builtins())
    assert facts["limits"] == limits()
    assert facts["forbidden_helpers"] == list(forbidden_helpers())
    assert isinstance(facts["contract_names"], list)

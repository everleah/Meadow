"""Tests for the business-rules sandbox (Phase 4, step 1)."""

from __future__ import annotations

import pytest

from meadow.rules import (
    Context,
    RuleCompileError,
    RuleMemoryError,
    RuleTimeoutError,
    compile_rule,
    execute_rule,
)


def _ctx(rule_type: str = "validation", object_type: str = "product") -> Context:
    return Context(
        rule_type=rule_type,
        object_type=object_type,
        mode="save",
        actor="user:admin",
    )


class _Binding:
    def __init__(self) -> None:
        self._secret = "shh"
        self.public = "ok"


def test_validation_rule_adds_errors() -> None:
    source = (
        "def run(obj, ctx, result):\n"
        '    if not obj["attributes"].get("price"):\n'
        '        result.add_error("price", "required", "price is required")\n'
    )
    result = execute_rule(source, {"id": "object:1", "type": "product", "attributes": {}}, _ctx())
    assert len(result.errors) == 1
    assert result.errors[0].attr == "price"
    assert result.errors[0].code == "required"


def test_computed_rule_sets_value() -> None:
    source = (
        "def run(obj, ctx, result):\n"
        '    net = obj["attributes"]["net_price"]\n'
        '    result.set_value("gross_price", round(net * 1.19, 2))\n'
    )
    result = execute_rule(source, {"attributes": {"net_price": 100}}, _ctx("computed"))
    assert result.computed["gross_price"] == 119.0


def test_workflow_guard_verdict() -> None:
    source = (
        "def run(obj, ctx, result):\n"
        '    result.allow(obj["status"] == "approved", "must be approved")\n'
    )
    result = execute_rule(source, {"status": "draft"}, _ctx("workflow_guard"))
    assert result.allowed is False
    assert result.deny_reason == "must be approved"


def test_log_is_captured() -> None:
    source = 'def run(obj, ctx, result):\n    log("hello", "WARN")\n'
    result = execute_rule(source, {"attributes": {}}, _ctx())
    assert result.logs[0].level == "WARN"
    assert result.logs[0].message == "hello"


def test_missing_run_is_rejected() -> None:
    with pytest.raises(RuleCompileError):
        execute_rule("x = 1\n", {"attributes": {}}, _ctx())


def test_syntax_error_is_rejected() -> None:
    source = "def run(obj, ctx, result):\n    if True\n"
    with pytest.raises(RuleCompileError):
        execute_rule(source, {"attributes": {}}, _ctx())


def test_non_whitelisted_import_is_rejected_at_compile() -> None:
    source = "import os\ndef run(obj, ctx, result):\n    pass\n"
    with pytest.raises(RuleCompileError):
        compile_rule(source)


def test_whitelisted_import_works() -> None:
    source = (
        'import json\ndef run(obj, ctx, result):\n    result.set_value("x", json.dumps({"a": 1}))\n'
    )
    result = execute_rule(source, {"attributes": {}}, _ctx("computed"))
    assert result.computed["x"] == '{"a": 1}'


def test_math_binding_is_available() -> None:
    source = 'def run(obj, ctx, result):\n    result.set_value("sqrt", math.sqrt(9))\n'
    result = execute_rule(source, {"attributes": {}}, _ctx("computed"))
    assert result.computed["sqrt"] == 3.0


def test_obj_is_read_only() -> None:
    source = 'def run(obj, ctx, result):\n    obj["x"] = 1\n'
    with pytest.raises(TypeError):
        execute_rule(source, {"attributes": {}}, _ctx())


def test_private_attribute_access_is_blocked() -> None:
    # RestrictedPython rejects `_private` access at compile time.
    source = 'def run(obj, ctx, result):\n    result.set_value("x", helper._secret)\n'
    with pytest.raises(RuleCompileError):
        execute_rule(
            source,
            {"attributes": {}},
            _ctx("computed"),
            bindings={"helper": _Binding()},
        )


def test_timeout_interrupts_infinite_loop() -> None:
    source = "def run(obj, ctx, result):\n    i = 0\n    while True:\n        i += 1\n"
    with pytest.raises(RuleTimeoutError):
        execute_rule(source, {"attributes": {}}, _ctx(), timeout_s=0.1)


def test_memory_limit_aborts() -> None:
    source = (
        "def run(obj, ctx, result):\n"
        "    x = []\n"
        "    while True:\n"
        '        x.append("x" * 1024 * 1024)\n'
    )
    with pytest.raises(RuleMemoryError):
        execute_rule(
            source,
            {"attributes": {}},
            _ctx(),
            timeout_s=None,
            max_memory_bytes=1024 * 1024,
        )

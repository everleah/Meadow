"""Tests for the worker's rule-evaluation logic (Phase 4, step 2)."""

from __future__ import annotations

from meadow.worker.protocol import (
    EvaluationCompileRequest,
    EvaluationObject,
    EvaluationRequest,
    EvaluationRule,
)
from meadow.worker.rules_service import compile_script, evaluate


def _obj() -> EvaluationObject:
    return EvaluationObject(
        id="object:1",
        type="product",
        status="draft",
        attributes={"net_price": 100},
    )


def test_evaluate_validation_and_computed() -> None:
    validation = EvaluationRule(
        name="require_price",
        type="validation",
        script=(
            "def run(obj, ctx, result):\n"
            '    if not obj["attributes"].get("price"):\n'
            '        result.add_error("price", "required", "price is required")\n'
        ),
    )
    computed = EvaluationRule(
        name="gross_from_net",
        type="computed",
        script=(
            "def run(obj, ctx, result):\n"
            '    result.set_value("gross_price", round(obj["attributes"]["net_price"] * 1.19, 2))\n'
        ),
    )
    request = EvaluationRequest(rules=[validation, computed], object=_obj())

    response = evaluate(request)

    assert response.failed == []
    assert len(response.errors) == 1
    assert response.errors[0].attr == "price"
    assert response.computed["gross_price"] == 119.0


def test_evaluate_workflow_guard_denies_transition() -> None:
    guard = EvaluationRule(
        name="must_approve",
        type="workflow_guard",
        script=(
            "def run(obj, ctx, result):\n"
            '    result.allow(obj["status"] == "approved", "must be approved")\n'
        ),
    )
    request = EvaluationRequest(rules=[guard], object=_obj(), mode="transition")

    response = evaluate(request)

    assert response.allowed is False
    assert response.deny_reason == "must be approved"


def test_evaluate_exposes_the_unset_class_features_to_rules() -> None:
    """Stage B: `ctx.missing_features` carries the mandatory class features."""
    guard = EvaluationRule(
        name="class_complete",
        type="workflow_guard",
        script=(
            "def run(obj, ctx, result):\n"
            "    missing = list(ctx.missing_features)\n"
            "    result.allow(not missing, 'class features missing: ' + ', '.join(missing))\n"
        ),
    )
    denied = evaluate(
        EvaluationRequest(
            rules=[guard],
            object=_obj(),
            mode="transition",
            missing_features=["power", "voltage"],
        )
    )
    allowed = evaluate(
        EvaluationRequest(rules=[guard], object=_obj(), mode="transition", missing_features=[])
    )

    assert denied.allowed is False
    assert denied.deny_reason == "class features missing: power, voltage"
    assert allowed.allowed is True
    # Without the field (older core) the tuple is simply empty.
    assert evaluate(EvaluationRequest(rules=[guard], object=_obj(), mode="transition")).allowed


def test_evaluate_reports_syntax_error_as_failed() -> None:
    broken = EvaluationRule(
        name="broken",
        type="validation",
        script="def run(obj, ctx, result):\n    if True\n",
    )
    request = EvaluationRequest(rules=[broken], object=_obj())

    response = evaluate(request)

    assert response.failed == ["broken"]
    assert response.logs[0].level == "ERROR"


def test_evaluate_reports_timeout_as_failed() -> None:
    infinite = EvaluationRule(
        name="infinite",
        type="validation",
        script="def run(obj, ctx, result):\n    i = 0\n    while True:\n        i += 1\n",
    )
    request = EvaluationRequest(rules=[infinite], object=_obj())

    response = evaluate(request, timeout_s=0.1)

    assert response.failed == ["infinite"]


def test_evaluate_merges_computed_values_from_multiple_rules() -> None:
    first = EvaluationRule(
        name="set_a",
        type="computed",
        script='def run(obj, ctx, result):\n    result.set_value("a", 1)\n',
    )
    second = EvaluationRule(
        name="set_b",
        type="computed",
        script='def run(obj, ctx, result):\n    result.set_value("b", 2)\n',
    )
    request = EvaluationRequest(rules=[first, second], object=_obj())

    response = evaluate(request)

    assert response.computed == {"a": 1, "b": 2}


def test_compile_script_valid() -> None:
    response = compile_script(
        EvaluationCompileRequest(
            script="def run(obj, ctx, result):\n    result.set_value('a', 1)\n"
        )
    )

    assert response.ok is True
    assert response.errors == []


def test_compile_script_reports_syntax_error() -> None:
    response = compile_script(
        EvaluationCompileRequest(script="def run(obj, ctx, result):\n    if True\n")
    )

    assert response.ok is False
    assert response.errors


def test_compile_script_rejects_unwhitelisted_import() -> None:
    response = compile_script(EvaluationCompileRequest(script="import os\n"))

    assert response.ok is False
    assert any("os" in error for error in response.errors)

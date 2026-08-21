"""NATS request/reply protocol for rule evaluation (internal, not public API).

The Rust core sends an `EvaluationRequest` to the subject `rules.evaluate` and
expects an `EvaluationResponse` as the reply. See
`plan/detail/business-rules.md`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: Default NATS subject for the synchronous rule-evaluation request/reply.
RULES_EVALUATE_SUBJECT = "rules.evaluate"


class EvaluationRule(BaseModel):
    """A single rule to evaluate: the script source plus its metadata."""

    name: str
    type: str  # validation | computed | normalization | workflow_guard | bulk
    script: str


class EvaluationObject(BaseModel):
    """The resolved view of the candidate object (own + inherited + language)."""

    id: str
    type: str
    status: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class EvaluationRequest(BaseModel):
    """Request from the Rust core: rules to run plus the candidate object."""

    rules: list[EvaluationRule]
    object: EvaluationObject
    mode: str = "save"  # save | import | transition | bulk
    language: str | None = None
    actor: str | None = None


class EvaluationIssue(BaseModel):
    """A validation error or warning."""

    attr: str
    code: str
    message: str


class EvaluationLog(BaseModel):
    """A single `log(...)` call inside a rule."""

    level: str
    message: str


class EvaluationResponse(BaseModel):
    """Reply to the Rust core (fail-closed: `failed` is non-empty on errors)."""

    errors: list[EvaluationIssue] = Field(default_factory=list)
    warnings: list[EvaluationIssue] = Field(default_factory=list)
    computed: dict[str, Any] = Field(default_factory=dict)
    allowed: bool | None = None
    deny_reason: str | None = None
    failed: list[str] = Field(default_factory=list)
    logs: list[EvaluationLog] = Field(default_factory=list)

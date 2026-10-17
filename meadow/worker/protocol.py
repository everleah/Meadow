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

#: Default NATS subject for the synchronous rule-script compile validation.
RULES_COMPILE_SUBJECT = "rules.compile"

#: Default NATS subject for AI generation (context + provider call).
AI_GENERATE_SUBJECT = "ai.generate"

#: Default NATS subject for the AI connection test (`POST /ai/test`).
AI_TEST_SUBJECT = "ai.test"


class AiConfig(BaseModel):
    """Provider configuration for one AI call, resolved by the core.

    The API key is part of this payload — it travels on the internal subject and
    nowhere else (`plan/detail/ki-assistenz.md` §4). The worker keeps it for the
    duration of the call only.
    """

    provider: str = "generic"
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_output_tokens: int = 2048
    timeout_ms: int = 30_000
    max_requests_per_hour: int = 60


class AiGenerateEnvelope(BaseModel):
    """Request to `ai.generate`: the configuration plus the task.

    Both halves stay raw mappings — the task is parsed tolerantly by
    `meadow.ai.service.GenerationRequest.from_payload` and the configuration by
    `meadow.ai.provider.ProviderConfig.from_payload`. A missing optional field is
    normal here and must not be a validation error.
    """

    config: dict[str, Any] = Field(default_factory=dict)
    request: dict[str, Any] = Field(default_factory=dict)
    #: Set by the core for a streamed `explain` call: the NATS subject the text
    #: pieces go to, and the id the cancel message (`ai.cancel.<id>`) names.
    #: Both absent for a blocking call (`plan/detail/ki-assistenz.md` §13).
    request_id: str | None = None
    stream_subject: str | None = None


class AiTestEnvelope(BaseModel):
    """Request to `ai.test`: the configuration only."""

    config: dict[str, Any] = Field(default_factory=dict)


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
    # Channel-specific release transition (mode `transition`): see `Context`.
    channel: str | None = None
    release_status: str | None = None
    # Unset mandatory class features of the object's taxonomy classes (Stage B).
    missing_features: list[str] = Field(default_factory=list)


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


class EvaluationCompileRequest(BaseModel):
    """Request from the Rust core: validate a script before persisting it."""

    script: str
    whitelist: list[str] | None = None


class EvaluationCompileResponse(BaseModel):
    """Reply to the Rust core: whether the script compiles under the sandbox."""

    ok: bool
    errors: list[str] = Field(default_factory=list)

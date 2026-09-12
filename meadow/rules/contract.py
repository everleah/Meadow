"""Script contract for business rules: `run(obj, ctx, result)`.

The sandbox injects `obj`, `ctx`, `result` and `log` into the rule namespace.
Rules interact with the world exclusively through the typed `result` helpers;
`obj` is a read-only view and must never be mutated (computed/normalization
rules write via `result.set_value`).

See `plan/detail/business-rules.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Rule types from plan/detail/business-rules.md.
RULE_TYPES = ("validation", "computed", "normalization", "workflow_guard", "bulk")

# Evaluation modes from plan/detail/business-rules.md.
MODES = ("save", "import", "transition", "bulk")


@dataclass(frozen=True)
class Context:
    """Injected as `ctx`: static context of the current evaluation."""

    rule_type: str
    object_type: str
    language: str | None = None
    mode: str = "save"
    actor: str | None = None
    # Set for a channel-specific release transition (`release` row): the channel
    # code and the target state of the release. `obj.status` keeps the **global**
    # object status, so a rule can require the global release before a channel.
    channel: str | None = None
    release_status: str | None = None
    # Set for object writes (Stage B, class features): the **mandatory features of
    # the object's taxonomy classes that are not set**. A validation or
    # workflow_guard rule can block a transition on it, e.g. "publish only with a
    # complete classification class".
    missing_features: tuple[str, ...] = ()


@dataclass
class Issue:
    """A single validation error or warning."""

    attr: str
    code: str
    message: str


@dataclass
class LogEntry:
    """A single `log(...)` call inside a rule."""

    level: str
    message: str


@dataclass
class Result:
    """Injected as `result`: the typed outcome of a rule execution."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    computed: dict[str, Any] = field(default_factory=dict)
    allowed: bool | None = None
    deny_reason: str | None = None
    logs: list[LogEntry] = field(default_factory=list)

    # --- validation ---
    def add_error(self, attr: str, code: str, message: str) -> None:
        """Records a blocking validation error."""
        self.errors.append(Issue(attr=attr, code=code, message=message))

    def add_warning(self, attr: str, code: str, message: str) -> None:
        """Records a non-blocking validation warning."""
        self.warnings.append(Issue(attr=attr, code=code, message=message))

    # --- computed / normalization ---
    def set_value(self, attr: str, value: Any) -> None:
        """Sets a computed (or normalized) attribute value."""
        self.computed[attr] = value

    # --- workflow_guard ---
    def allow(self, allowed: bool, reason: str | None = None) -> None:
        """Verdict for a status transition."""
        self.allowed = allowed
        self.deny_reason = None if allowed else reason

    # --- logging ---
    def log(self, message: str, level: str = "INFO") -> None:
        """Appends a log line to the evaluation result."""
        self.logs.append(LogEntry(level=level, message=message))

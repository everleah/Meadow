"""Exception types raised by the business-rules sandbox."""

from __future__ import annotations


class RuleError(Exception):
    """Base class for all business-rule errors."""


class RuleCompileError(RuleError):
    """The script is syntactically invalid or violates sandbox rules."""


class RuleTimeoutError(RuleError):
    """The script exceeded its execution budget."""


class RuleMemoryError(RuleError):
    """The script exceeded its memory budget."""

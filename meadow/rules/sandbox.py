"""RestrictedPython execution sandbox for business rules (Phase 4).

Implements the script contract `run(obj, ctx, result)` with:

- in-process RestrictedPython compilation (safe policy) — **cached per script**, because the
  blocking write path evaluates every applicable rule on every save and
  `compile_restricted_exec` parses, rewrites and compiles each time,
- a configurable import whitelist (guarded ``__import__``),
- hard limits: source size, execution budget (wall-clock timeout via
  ``sys.settrace``), and memory (``tracemalloc``, soft limit).

See `plan/detail/business-rules.md`.
"""

from __future__ import annotations

import builtins
import operator
import sys
import time
import tracemalloc
from collections.abc import Callable
from functools import lru_cache
from types import CodeType, MappingProxyType
from typing import Any

from RestrictedPython import (
    compile_restricted_exec,
    limited_builtins,
    safe_builtins,
    safe_globals,
    utility_builtins,
)
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    full_write_guard,
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)

from meadow.rules.contract import Context, Result
from meadow.rules.errors import RuleCompileError, RuleMemoryError, RuleTimeoutError
from meadow.rules.whitelist import DEFAULT_WHITELIST, validate_imports

DEFAULT_MAX_SOURCE_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 1.5
DEFAULT_MAX_MEMORY_BYTES = 256 * 1024 * 1024  # 256 MiB of traced Python objects

# Wall-clock and memory are sampled every N line events.
_TRACE_CHECK_INTERVAL = 10

_INPLACE_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "+=": operator.iadd,
    "-=": operator.isub,
    "*=": operator.imul,
    "/=": operator.itruediv,
    "//=": operator.ifloordiv,
    "%=": operator.imod,
    "**=": operator.ipow,
    "<<=": operator.ilshift,
    ">>=": operator.irshift,
    "&=": operator.iand,
    "|=": operator.ior,
    "^=": operator.ixor,
}

# Builtins RestrictedPython omits although they are pure helpers with no side
# effects. The shipped demo rules need `enumerate` (GTIN check digits) and
# `getattr`; without them the sandbox raised a `NameError` **at runtime**, long
#: after `compile_rule` had accepted the script (found 2026-09-16: two of the six
#: catalogue rules never ran).
_EXTRA_BUILTINS = (
    "dict",
    "sum",
    "min",
    "max",
    "any",
    "all",
    "enumerate",
    "reversed",
)


def _inplacevar_(op: str, var: Any, expr: Any) -> Any:
    """Implementation of the `_inplacevar_` guard for augmented assignment."""
    try:
        apply = _INPLACE_OPS[op]
    except KeyError as err:
        raise SyntaxError(f"unsupported augmented assignment operator: {op}") from err
    return apply(var, expr)


def _make_guarded_import(whitelist: frozenset[str]) -> Callable[..., Any]:
    def guarded_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        root = name.split(".")[0]
        if root not in whitelist:
            raise ImportError(f"import of {name!r} is not allowed")
        return builtins.__import__(name, globals, locals, fromlist, level)

    return guarded_import


def build_globals(
    *,
    obj: MappingProxyType,
    ctx: Context,
    result: Result,
    log: Callable[..., None],
    whitelist: frozenset[str],
    bindings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Builds the restricted globals for a rule execution."""
    builtins_: dict[str, Any] = dict(safe_builtins)
    builtins_.update(limited_builtins)
    builtins_.update(utility_builtins)
    for name in _EXTRA_BUILTINS:
        builtins_[name] = getattr(builtins, name)
    # `getattr` is RestrictedPython's **guarded** variant (`safer_getattr`): it
    # behaves like the builtin including the default value, but refuses every name
    # starting with `_`, so a rule cannot reach `__class__`, `__dict__` or any
    # other internal through it.
    builtins_["getattr"] = safer_getattr
    builtins_["__import__"] = _make_guarded_import(whitelist)

    globals_: dict[str, Any] = dict(safe_globals)
    globals_["__builtins__"] = builtins_
    globals_["__name__"] = "meadow_rule"
    globals_["__metaclass__"] = type
    # Guards referenced by the restricted bytecode.
    globals_["_getiter_"] = default_guarded_getiter
    globals_["_getitem_"] = default_guarded_getitem
    globals_["_unpack_sequence_"] = guarded_unpack_sequence
    globals_["_iter_unpack_sequence_"] = guarded_iter_unpack_sequence
    globals_["_write_"] = full_write_guard
    globals_["_inplacevar_"] = _inplacevar_
    # Script contract.
    globals_["obj"] = obj
    globals_["ctx"] = ctx
    globals_["result"] = result
    globals_["log"] = log
    if bindings:
        globals_.update(bindings)
    return globals_


def compile_rule(
    source: str,
    *,
    whitelist: frozenset[str] = DEFAULT_WHITELIST,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> CodeType:
    """Compiles and validates a rule script (save-time check).

    Raises `RuleCompileError` for oversized scripts, non-whitelisted imports,
    syntax errors, and sandbox-policy violations.
    """
    if len(source.encode("utf-8")) > max_source_bytes:
        raise RuleCompileError(f"script exceeds {max_source_bytes} bytes")

    # Compile first: RestrictedPython reports syntax errors and sandbox-policy
    # violations (e.g. private attribute access) in `errors`.
    compiled = compile_restricted_exec(source)
    if compiled.errors:
        raise RuleCompileError("; ".join(compiled.errors))
    if compiled.code is None:
        raise RuleCompileError("script did not compile")

    disallowed = validate_imports(source, whitelist)
    if disallowed:
        raise RuleCompileError(f"import of {disallowed[0]!r} is not allowed")
    return compiled.code


@lru_cache(maxsize=256)
def _compiled(
    source: str,
    whitelist: frozenset[str],
    max_source_bytes: int,
) -> CodeType:
    """Compiles a rule script once per (script, policy) pair.

    `compile_restricted_exec` parses the script, rewrites its AST and compiles the
    result for **every** rule of **every** write (the save path evaluates all
    applicable rules synchronously); warm, that is ~0.1–0.5 ms per rule, so the
    cache is a small but free win. The result depends only on the script text and
    the policy (`whitelist`, `max_source_bytes`), and a `CodeType` is immutable,
    so reusing it with fresh globals per execution is exactly what `exec`
    supports. Errors are *not* cached (lru_cache stores successful returns only),
    so an invalid script keeps failing loudly on every attempt.

    The cache is bounded (256 scripts); the rule registry holds far fewer, and a
    changed script has a new key and compiles fresh.
    """
    return compile_rule(source, whitelist=whitelist, max_source_bytes=max_source_bytes)


def execute_rule(
    source: str,
    obj: dict[str, Any],
    ctx: Context,
    *,
    whitelist: frozenset[str] = DEFAULT_WHITELIST,
    bindings: dict[str, Any] | None = None,
    timeout_s: float | None = DEFAULT_TIMEOUT_SECONDS,
    max_memory_bytes: int | None = DEFAULT_MAX_MEMORY_BYTES,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> Result:
    """Executes a rule and returns its typed `Result`.

    `obj` is exposed read-only (top-level); `bindings` injects additional
    capabilities by name. Limits default to the blocking write path
    (~1.5 s, 256 MiB) and can be loosened for bulk jobs.
    """
    code = _compiled(source, whitelist, max_source_bytes)
    result = Result()

    def log(message: str, level: str = "INFO") -> None:
        result.log(message, level)

    globals_ = build_globals(
        obj=MappingProxyType(obj),
        ctx=ctx,
        result=result,
        log=log,
        whitelist=whitelist,
        bindings=bindings,
    )
    _exec(code, globals_, timeout_s=timeout_s, max_memory_bytes=max_memory_bytes)
    return result


def _exec(
    code: CodeType,
    globals_: dict[str, Any],
    *,
    timeout_s: float | None,
    max_memory_bytes: int | None,
) -> None:
    started = time.monotonic()
    events = 0
    if max_memory_bytes is not None:
        tracemalloc.start()

    def trace(frame: Any, event: str, arg: Any) -> Any:
        nonlocal events
        events += 1
        if events % _TRACE_CHECK_INTERVAL == 0:
            if timeout_s is not None and time.monotonic() - started > timeout_s:
                raise RuleTimeoutError(f"rule exceeded {timeout_s}s execution budget")
            if max_memory_bytes is not None:
                current, _ = tracemalloc.get_traced_memory()
                if current > max_memory_bytes:
                    raise RuleMemoryError(f"rule exceeded {max_memory_bytes} bytes of memory")
        return trace

    sys.settrace(trace)
    try:
        exec(code, globals_, globals_)
        run = globals_.get("run")
        if not callable(run):
            raise RuleCompileError("script must define `run(obj, ctx, result)`")
        run(globals_["obj"], globals_["ctx"], globals_["result"])
    finally:
        sys.settrace(None)
        if max_memory_bytes is not None:
            tracemalloc.stop()

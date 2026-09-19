"""The rule sandbox's policy — read from the sandbox itself, never kept in a second list.

A rule author (a human at the editor, later a model) has to know three things that the
sandbox decides: **which names exist**, **which modules may be imported** and **how far
a rule may go** (size, wall clock, memory). This module asks the running sandbox
(`meadow.rules.sandbox.build_globals`) instead of documenting a copy of it, so a change
in the sandbox policy shows up here automatically.

`check_script` runs the two checks a generated or hand-written script has to survive:

1. an **AST pre-check** for structure and for names the sandbox does not provide, and
2. the **real compile policy** (`compile_rule`: import whitelist, private attributes,
   size limit).

Measured 2026-09-19 (`plan/detail/ki-assistenz.md` §2): `compile_restricted_exec` happily
accepts `hasattr(value, "x")` and the call then fails at runtime with `NameError`, while a
forbidden import and a private attribute (`obj.__class__`) are rejected at compile time.
The pre-check therefore exists for exactly that gap — it is the half the compiler cannot
see.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from meadow.rules.contract import Context, Result
from meadow.rules.errors import RuleError
from meadow.rules.sandbox import (
    DEFAULT_MAX_MEMORY_BYTES,
    DEFAULT_MAX_SOURCE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    build_globals,
    compile_rule,
)
from meadow.rules.whitelist import DEFAULT_WHITELIST

#: The four names the script contract injects (`meadow.rules.contract`).
CONTRACT_NAMES = ("obj", "ctx", "result", "log")

#: The parameter list of the rule entry point, in order.
SCRIPT_ARGUMENTS = ("obj", "ctx", "result")

#: Helpers a rule author reaches for although the sandbox does not expose them: the
#: dangerous half (`eval`, `exec`, `open`, …) and the harmless-looking half that still
#: fails at runtime (`hasattr`, `dir`, `vars`). The *effective* list is derived below —
#: a name that the sandbox really provides is never reported as forbidden.
_HELPER_CANDIDATES = (
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "globals",
    "hasattr",
    "help",
    "input",
    "locals",
    "memoryview",
    "open",
    "vars",
    "__import__",
)


@lru_cache(maxsize=1)
def _sandbox_globals() -> dict[str, Any]:
    """The globals the sandbox hands to a rule — built once, from the real builder.

    The rule values are stand-ins: only the *names* of the namespace matter here, and
    building them must not need a database or a candidate object.
    """
    return build_globals(
        obj=MappingProxyType({"id": "object:policy", "type": "", "attributes": {}}),
        ctx=Context(rule_type="validation", object_type=""),
        result=Result(),
        log=lambda *_: None,
        whitelist=DEFAULT_WHITELIST,
        bindings=None,
    )


def sandbox_builtins() -> tuple[str, ...]:
    """Sorted names of the builtins a rule may use (from the running sandbox)."""
    return tuple(sorted(_sandbox_globals()["__builtins__"]))


def injected_globals() -> tuple[str, ...]:
    """Sorted names injected in addition to the contract (today: none).

    The plan allows later bindings (unit conversion, string helpers); when they are
    added, they appear here automatically — and in the panel and the generated
    reference with them.
    """
    reserved = set(CONTRACT_NAMES)
    return tuple(
        sorted(
            name for name in _sandbox_globals() if not name.startswith("_") and name not in reserved
        )
    )


def available_imports() -> tuple[str, ...]:
    """Sorted modules a rule may import (the sandbox's import whitelist)."""
    return tuple(sorted(DEFAULT_WHITELIST))


def limits() -> dict[str, float]:
    """The hard limits of the sandbox, as documented in `plan/detail/business-rules.md`."""
    return {
        "max_source_bytes": DEFAULT_MAX_SOURCE_BYTES,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_memory_bytes": DEFAULT_MAX_MEMORY_BYTES,
    }


def forbidden_helpers() -> tuple[str, ...]:
    """Names that look like helpers but are **not** available to a rule.

    Derived from [`_HELPER_CANDIDATES`][meadow.ai.policy._HELPER_CANDIDATES] minus
    everything the sandbox actually provides (builtins, injected globals, contract
    names) — so the day the sandbox gains `hasattr`, the pre-check stops flagging it
    instead of contradicting the runtime.
    """
    provided = set(sandbox_builtins()) | set(injected_globals()) | set(CONTRACT_NAMES)
    return tuple(name for name in sorted(_HELPER_CANDIDATES) if name not in provided)


def check_script(source: str) -> tuple[bool, str]:
    """Whether a rule script would pass the sandbox (AST pre-check + compile policy).

    Returns `(ok, message)`; the message is the reason on failure and a short
    confirmation on success. The check is pure: it compiles, it never executes.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return False, f"not valid Python: {error.msg}"

    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    defined = [node.name for node in functions]
    if len(functions) != 1 or functions[0].name != "run":
        return False, (
            "the script must define exactly one function `run` "
            f"(found: {', '.join(defined) if defined else 'none'})"
        )
    arguments = [argument.arg for argument in functions[0].args.args]
    if tuple(arguments) != SCRIPT_ARGUMENTS:
        return False, (
            f"`run` must take ({', '.join(SCRIPT_ARGUMENTS)}), "
            f"found ({', '.join(arguments) or 'nothing'})"
        )

    forbidden = set(forbidden_helpers())
    used = sorted(
        {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in forbidden}
    )
    if used:
        return False, (
            f"names the sandbox does not provide: {', '.join(used)} "
            "(use getattr(value, 'name', None) instead of hasattr, for example)"
        )

    try:
        compile_rule(source)
    except (RuleError, SyntaxError) as error:
        return False, f"the sandbox policy rejects the script: {error}"
    return True, "matches the sandbox policy"


def policy_facts() -> dict[str, Any]:
    """All policy facts as a plain structure (for the reference generator and tests)."""
    return {
        "contract_names": list(CONTRACT_NAMES),
        "script_arguments": list(SCRIPT_ARGUMENTS),
        "builtins": list(sandbox_builtins()),
        "injected_globals": list(injected_globals()),
        "available_imports": list(available_imports()),
        "forbidden_helpers": list(forbidden_helpers()),
        "limits": limits(),
    }

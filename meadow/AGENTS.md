# meadow — Python Source Code

## Purpose

Owns the Python package `meadow`: the shared business-rules engine (`meadow.rules`), and later the worker and frontend services. Python is the home of flexible business logic and the UI (see root AGENTS.md).

## Ownership

- Owns all files under `meadow/`.
- `meadow/rules/` — RestrictedPython sandbox and the script contract `run(obj, ctx, result)`.

## Local Contracts

- Implementation stays consistent with `plan/detail/business-rules.md` and the root AGENTS.md (package manager `uv`, `.venv/`, `pyproject.toml`).
- The rules engine is a shared library; its consumers are the Rust core (synchronous evaluation endpoint, Phase 4) and the Python worker/frontend.
- `meadow.rules` public API: `execute_rule`, `compile_rule`, `Context`, `Result`, `DEFAULT_WHITELIST`, and the `RuleError` hierarchy (`RuleCompileError`, `RuleTimeoutError`, `RuleMemoryError`).
- Rule scripts use `def run(obj, ctx, result)`; injected globals are `obj` (read-only view), `ctx`, `result`, `log`, plus whitelisted modules (default: `json`, `datetime`, `math`, `re`, `statistics`, `decimal`, `typing`, `uuid`, `collections`).

## Work Guidance

- Code, comments, and docstrings are written in English (root Coding Guidelines).
- Sandbox limits default to the blocking write path (~1.5 s budget, 256 MiB, 64 KB source); bulk jobs may pass looser limits.

## Verification

- `ruff check meadow tests conftest.py`
- `pytest` (asyncio mode auto via `pyproject.toml`)

## Child DOX Index

_(none yet)_

# tests — Automated Tests

## Purpose

Central home for all automated tests of Meadow PIM. Keeps the repository root uncluttered by collecting test code in one place.

## Ownership

- Owns all files under `tests/`.
- Repo-wide rules and user preferences are owned by the root `AGENTS.md`.

## Local Contracts

- All new tests are created under `tests/`, not in the repository root (root preference).
- Test code is source code: identifiers, comments, and documentation in English (root Coding Guidelines).

## Work Guidance

- Python tests live here as `test_*.py`, run with pytest (asyncio mode auto, configured in `pyproject.toml`).
- Rust unit tests live in `#[cfg(test)]` modules inside the crate's source files (`crates/pim-core/src/…`); Rust integration tests may be added under `tests/`.
- `test_smoke.py` verifies the core Python dependencies import; keep it as an environment sanity check.

## Verification

- Python: `pytest` (dev dependencies via uv).
- Rust: `cargo test` (workspace root).

## Child DOX Index

- (none yet)

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
- `fixtures/deslop/` holds intentionally sloppy samples for the Deslop scan
  (`plan/detail/ai-code-quality.md`). The directory is excluded from Ruff and from the
  repository's own scan; `test_deslop.py` scans a copy from a neutral path, because the
  scan rules skip paths that look like test code.
- **Page tests** (`test_products_page.py`, the net for the `products_page` refactor,
  `plan/detail/ai-code-quality.md` §4.11) boot the real frontend app through NiceGUI's
  user simulation (`user_simulation(main_file=…)`, plugin registered in the root
  `conftest.py` for a session-temp storage path). Three rules: the fixture must drop
  `meadow.frontend*` from `sys.modules` *before* the boot (otherwise the cached
  `@ui.page` decorators never run and every page answers `404`), the core client and
  the auth middleware are replaced (that is the point of a page test), and modules of
  the frontend are imported inside the fixture, never at module level.

## Verification

- Python: `pytest` (dev dependencies via uv).
- Rust: `cargo test` (workspace root).

## Child DOX Index

- (none yet)

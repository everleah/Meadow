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
  `plan/detail/ai-code-quality.md` §4.11; `test_users_page.py`, the net for the group
  editor's permission matrix and scope card) boot the real frontend app through NiceGUI's
  user simulation (`user_simulation(main_file=…)`, plugin registered in the root
  `conftest.py` for a session-temp storage path). Three rules: the fixture must drop
  `meadow.frontend*` from `sys.modules` *before* the boot (otherwise the cached
  `@ui.page` decorators never run and every page answers `404`), the core client and
  the auth middleware are replaced (that is the point of a page test), and modules of
  the frontend are imported inside the fixture, never at module level. `FakeCore.data`
  takes an **`Exception`** for a method as well: the call then raises it, which is how
  a failing catalog request is tested (a rejected token must degrade the page instead of
  answering `500`, `test_a_failing_catalog_call_degrades_instead_of_killing_the_page`).
  They also carry
  the **shell** contract (`test_shell_drawer_entries_are_plain_links`): the drawer
  entries must stay plain links with an `href` — navigation that travels through the
  websocket dies with the socket (root `AGENTS.md`, `plan/detail/ai-code-quality.md`
  §4.11).
- Widgets a page test has to *find* carry a marker (`ui.button(...).mark("bulk-start")`,
  NiceGUI 3.x; the old `marker()` name is gone). Four rules learned the hard way: a click on
  an **async** handler only schedules it (wait for the effect, e.g. with a small
  `_wait_until(...)` helper), `UserInteraction` types into inputs but cannot operate a
  select (set `.value` inside the client context instead — that is what `type()` does),
  `User.find(marker=…)` **raises** when nothing matches (so waiting through
  `bool(user.find(...).elements)` is no wait at all — use `await user.should_see(marker=…)`
  when the wait is about visibility, and `_wait_until` for recorded state such as core
  calls), and `user.notify` is how a test pins the message a handler shows
  (`notify.contains(...)`/`notify.messages`). **Client-rendered widgets are reachable through
  their event:** the AG Grid draws its cells in the browser, so a test cannot *click* one —
  but the click arrives in Python as an event, and `find(marker="objects-grid").trigger(
  "cellClicked", {"data": {...}, "colId": "_refs"})` fires exactly that (marker on the grid
  element, payload as the handler's `e.args`). That is how the reference dialog is tested.

## Verification

- Python: `pytest` (dev dependencies via uv).
- Rust: `cargo test` (workspace root).

## Child DOX Index

- (none yet)

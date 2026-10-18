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
- `test_diagrams.py` checks that each architecture/detail plan has one documented diagram source and a generated PNG/SVG pair, that SVG icons are embedded, and that the renderer can list its sources independently of the working directory. These artifact checks need no Graphviz; rendering itself is verified with `docs/diagrams/render.py`.
- `fixtures/deslop/` holds intentionally sloppy samples for the Deslop scan
  (`plan/detail/ai-code-quality.md`). The directory is excluded from Ruff and from the
  repository's own scan; `test_deslop.py` scans a copy from a neutral path, because the
  scan rules skip paths that look like test code.
- `test_deployment.py` holds the **silent contracts of the production files** (root
  `AGENTS.md`, `plan/detail/betrieb-deployment.md`): every variable the compose reads is
  documented in `.env.vps.example` and every documented one is read, each service rotates
  its logs, the external Traefik network is reached by its `traefik` alias and named through
  `TRAEFIK_NETWORK` (so no internal network name sits in the file), migrations are numbered
  and applied **before** the application containers,
  scripts that load `.env.vps` strip carriage returns, and the restore replays chunks
  through `POST /sql` (never `surreal import` — it fails on real export sizes). The bash
  scripts themselves are checked with `bash -n` plus a run against the running dev instance
  (`run-migration.sh --status`, `restore-vps-db.sh <backup> --force`).
- **Page tests** (`test_products_page.py`, the net for the `products_page` refactor,
  `plan/detail/ai-code-quality.md` §4.11; `test_users_page.py`, the net for the group
  editor's permission matrix and scope card; `test_settings_page.py`, the net for the AI
  commissioning preset, which must fill empty fields only and never replace a configured
  endpoint; `test_connector_types_page.py`, the net for the connector-type registry:
  built-ins filtered out of the list, template schema, create, missing name, broken JSON,
  delete without a selection, failing catalog) boot the real frontend app through NiceGUI's
  user simulation (`user_simulation(main_file=…)`, plugin registered in the root
  `conftest.py` for a session-temp storage path, which also supplies the frontend's required
  session secret). Three rules: the fixture must drop
  `meadow.frontend*` from `sys.modules` *before* the boot (otherwise the cached
  `@ui.page` decorators never run and every page answers `404`), the core client and
  the auth middleware are replaced (that is the point of a page test), and modules of
  the frontend are imported inside the fixture, never at module level. `FakeCore.data`
  takes an **`Exception`** for a method as well: the call then raises it, which is how
  a failing catalog request is tested (a rejected token must degrade the page instead of
  answering `500`, `test_a_failing_catalog_call_degrades_instead_of_killing_the_page`).
  Two traps of the re-imported frontend: an exception class imported at module level is a
  **different** class than the one the page uses after `_drop_frontend_modules()` (import it
  inside the test, otherwise the page does not catch it), and a textarea's value is written
  through the typed element (`_schema_area(user).set_value(...)`) because `find(...).elements`
  hands out `Element`, not `ui.textarea`.
  They also carry
  the **shell** contract (`test_shell_drawer_entries_are_plain_links`): the drawer
  entries must stay plain links with an `href` — navigation that travels through the
  websocket dies with the socket (root `AGENTS.md`, `plan/detail/ai-code-quality.md`
  §4.11).
- The **shared AI dialog** (`frontend/ai_assist.py`) carries markers for every part
  (`ai-description`, `ai-generate`, `ai-apply`, `ai-preview`, `ai-status`, `ai-note`),
  because it is built *by* the click that opens it — a page test waits for the dialog by
  a marker it contains, and a running assistant needs the availability settings in
  `FakeCore.data["list_system_settings"]` (`ai_enabled`/`ai_api_key`/`ai_endpoint`/
  `ai_model`; without them `ai_state` says "unavailable" and the button does not exist,
  which is itself worth a test). `test_users_page.py::test_an_ai_suggestion_fills_the_form_and_saves_nothing` drives it end to end and asserts that `POST`/`PATCH /groups` is **not** called.
  What does not need a page is tested without one: `tests/test_ai_assist.py` holds the
  dialog's mode list (`offered_modes` — "Explain" only once the editor has something to
  explain, and a caller's own restriction still wins) and the stop-before-the-first-event
  wait (`stream_request_id`); both were found by clicking the dialog in a browser on
  2026-10-17, which no simulated user could have reported.
- Two traps of the simulated user, found while testing the permission matrix's state
  buttons: `UserInteraction.elements` is a **set**, so the order of equal-looking widgets
  can only come from the element tree (`element.default_slot.children`) — never from the
  order of `find(...)`; and `click()` picks the element with the lowest id, so a control
  that appears once per row needs a **unique marker per row**
  (`perm-state-<key>-<state>`), otherwise the test clicks the wrong row. A widget with a
  `value` property behaves like the `ui.toggle` it replaced: `find(marker=...).elements`
  can be read and written by the existing helpers.
- **Not everything in a page can be driven by the simulation:** the Rule Lab opens from the
  AG Grid's *selection* (`aggrid.get_selected_rows()` is a JavaScript call the simulation
  cannot answer), so `tests/test_rules_page.py` covers the **pure** helper it depends on
  (`display.object_row_label`) and the lab itself is verified in the browser against the
  running instance. Say so in the test module instead of writing a test that only looks
  like it exercises the UI.
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

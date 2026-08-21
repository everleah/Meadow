# crates — Rust Source Code

## Purpose

Owns the Rust workspace (root `Cargo.toml`) and all Rust crates. The core service `pim-core` is the headless REST API (Axum + utoipa OpenAPI): sole write access to SurrealDB, JWT authentication/authorization, scheduler, and CRUD-event publishing to NATS JetStream.

## Ownership

- Root `Cargo.toml` (workspace) and every crate under `crates/`.
- `crates/pim-core/` — the PIM core service.

## Local Contracts

- Implementation stays consistent with `plan/meadow-pim-architektur.md` (sections 4–7, 9) and its detail documents; otherwise the plan is updated in the same change.
- Rust Edition 2024; MSRV 1.90 (`surrealdb` → `roaring`). `jsonwebtoken` requires a crypto backend feature (`rust_crypto` is used; `aws_lc_rs` is faster but needs NASM + CMake on Windows).
- `pim-core` module layout: `src/lib.rs` + `src/main.rs` (thin entry); top-level modules `config`, `db`, `error`, `audit`, `auth`, `cache`, `language`, `inheritance`, `rules`, `schema`, `seed`; API handlers in `src/api/{health, auth, objects, attribute_definitions, attribute_groups, keys, references, search, system, users, groups}`.
- `cache::MetaCache` (in-memory) caches the multilingual/inheritable attribute names and the language fallback chains; attribute-definition writes invalidate it. Redis remains optional for multi-instance (plan Phase 3).
- JWT auth (`auth.rs`): HS256 via `jsonwebtoken`; config through `JWT_SECRET`/`JWT_TTL_SECONDS`. Roles: `superadmin`, `admin`, `editor`, `viewer`. A default `superadmin` (`admin`/`admin`) is seeded in dev when no `user:admin` exists.
- `POST /system/logs` accepts a machine-to-machine `X-Api-Key` (`LOG_INGEST_API_KEY`) or, as a dev fallback, an admin JWT. Full-text search (`POST /search`) uses analyzer `meadow_std` and index `idx_object_attributes_fulltext` over `object.attributes`.
- Synchronous rule evaluation (`rules.rs`): the core loads `active` `business_rule` records, builds the candidate view, and calls the Python worker over NATS request/reply (subject `rules.evaluate`) in the object write path. Fail-closed: validation errors reject with 400, a rule that fails (exception/timeout) rejects with 500, an unreachable worker rejects with 503; `RULES_DEGRADE=true` degrades to "skip rules" on transport failure. Config: `RULES_NATS_URL`, `RULES_SUBJECT`, `RULES_TIMEOUT_MS`, `RULES_DEGRADE`.

## Work Guidance

- Identifiers, comments, and documentation are written in English (see root AGENTS.md, Coding Guidelines).
- Keep `pim-core` a single service; extract shared crates only when a clear boundary emerges.
- The SurrealDB client connects to `host:port` (no `ws://` scheme).

## Verification

- `cargo fmt --check`
- `cargo clippy --all-targets -- -D warnings`
- `cargo test`

## Child DOX Index

_(none yet)_

# crates — Rust Source Code

## Purpose

Owns the Rust workspace (root `Cargo.toml`) and all Rust crates. The core service `pim-core` is the headless REST API (Axum + utoipa OpenAPI): sole write access to SurrealDB, JWT authentication/authorization, scheduler, and CRUD-event publishing to NATS JetStream.

## Ownership

- Root `Cargo.toml` (workspace) and every crate under `crates/`.
- `crates/pim-core/` — the PIM core service.

## Local Contracts

- Implementation stays consistent with `plan/meadow-pim-architektur.md` (sections 4–7, 9) and its detail documents; otherwise the plan is updated in the same change.
- Rust Edition 2024; MSRV 1.90 (`surrealdb` → `roaring`). `jsonwebtoken` requires a crypto backend feature (`rust_crypto` is used; `aws_lc_rs` is faster but needs NASM + CMake on Windows).
- `pim-core` module layout: `src/lib.rs` + `src/main.rs` (thin entry); `config`, `db`, `error`, `auth`, `schema`, `seed`; `src/api/{health, auth, objects, attribute_definitions, users, groups}`.
- JWT auth (`auth.rs`): HS256 via `jsonwebtoken`; config through `JWT_SECRET`/`JWT_TTL_SECONDS`. Roles: `superadmin`, `admin`, `editor`, `viewer`. A default `superadmin` (`admin`/`admin`) is seeded in dev when no `user:admin` exists.

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

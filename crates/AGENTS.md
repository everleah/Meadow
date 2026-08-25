# crates — Rust Source Code

## Purpose

Owns the Rust workspace (root `Cargo.toml`) and all Rust crates. The core service `pim-core` is the headless REST API (Axum + utoipa OpenAPI): sole write access to SurrealDB, JWT authentication/authorization, scheduler, and CRUD-event publishing to NATS JetStream.

## Ownership

- Root `Cargo.toml` (workspace) and every crate under `crates/`.
- `crates/pim-core/` — the PIM core service.

## Local Contracts

- Implementation stays consistent with `plan/meadow-pim-architektur.md` (sections 4–7, 9) and its detail documents; otherwise the plan is updated in the same change.
- Rust Edition 2024; MSRV 1.90 (`surrealdb` → `roaring`). `jsonwebtoken` requires a crypto backend feature (`rust_crypto` is used; `aws_lc_rs` is faster but needs NASM + CMake on Windows).
- `pim-core` module layout: `src/lib.rs` + `src/main.rs` (thin entry); top-level modules `config`, `db`, `error`, `event`, `audit`, `auth`, `cache`, `language`, `inheritance`, `jobqueue`, `rules`, `scheduler`, `schema`, `seed`; API handlers in `src/api/{health, auth, objects, attribute_definitions, attribute_groups, jobs, keys, references, rules, search, system, users, groups}`.
- `cache::MetaCache` (in-memory) caches the multilingual/inheritable attribute names and the language fallback chains; attribute-definition writes invalidate it. Redis remains optional for multi-instance (plan Phase 3).
- JWT auth (`auth.rs`): HS256 via `jsonwebtoken`; config through `JWT_SECRET`/`JWT_TTL_SECONDS`. Roles: `superadmin`, `admin`, `editor`, `viewer`. A default `superadmin` (`admin`/`admin`) is seeded in dev when no `user:admin` exists.
- `POST /system/logs` accepts a machine-to-machine `X-Api-Key` (`LOG_INGEST_API_KEY`) or, as a dev fallback, an admin JWT. Full-text search (`POST /search`) uses analyzer `meadow_std` and index `idx_object_attributes_fulltext` over `object.attributes`. `GET /system/settings` also accepts the API key (machine-to-machine config read for the worker). Curated log categories are `app|job|rule|auth|integration`; the core's own rule logs use `rule` (singular).
- Synchronous rule evaluation (`rules.rs`): the core loads `active` `business_rule` records, builds the candidate view, and calls the Python worker over NATS request/reply (subject `rules.evaluate`) in the object write path. Fail-closed: validation errors reject with 400, a rule that fails (exception/timeout) rejects with 500, an unreachable worker rejects with 503; `RULES_DEGRADE=true` degrades to "skip rules" on transport failure. Circuit breaker: a `failure_count` on the rule auto-disables it after `RULES_BREAKER_THRESHOLD` (default 3) consecutive failures and writes an audit entry + system log; success resets the counter. Config: `RULES_NATS_URL`, `RULES_SUBJECT`, `RULES_COMPILE_SUBJECT`, `RULES_TIMEOUT_MS`, `RULES_DEGRADE`, `RULES_BREAKER_THRESHOLD`.
- Business-rule CRUD (`src/api/rules.rs`, endpoints `/rules`): list/get are open to all authenticated roles; create/update/activate/disable require `admin`/`superadmin`. Create/update/activate compile-check the script via the worker (subject `rules.compile`); invalid scripts reject with 400, an unreachable worker with 503. New rules start as `draft`; `name` is unique (409); every change bumps `version` and is audited. `POST /rules/{id}/dry-run` (admin/superadmin) evaluates a single rule against a real object (`object_id`) or a constructed `object`, read-only and audited. `POST /rules/{id}/recompute` (admin/superadmin) queues a `business_rule` recompute job over the rule's object set (only `active` `computed`/`bulk` rules; see job management).
- Computed-attribute enforcement (`rules.rs::resolve_computed`): the write path validates that every value a `computed` rule produces is declared as `computed: true` in `attribute_definition`; undeclared or non-computed writes reject the save with 400 (fail-closed).
- Resolved view in the write path: `create_object`/`update_object` evaluate rules against own + inherited (`resolve_inheritance`) + language-resolved (`resolve_attributes`) values via `?locale=`; only own + computed values are persisted (inherited values never become own values).
- Rule warnings and `log()` output from evaluations are persisted as curated `log_entry` rows (`service: pim-core`, `category: rule`; warnings as WARN, `log()` with its script-provided level), best-effort.
- Event groundwork (`event.rs`, table `outbox_event`): Phase 6 writes CRUD events atomically with each operation into the outbox and relays them to NATS JetStream (`events.crud`). The schema, the event-type constants, and `enqueue_outbox_event` are prepared; the relay and the transactional wiring follow in Phase 6.
- Job management (`src/api/jobs.rs`, tables `job_definition`/`job_run`): admin-only CRUD for definitions (`GET/POST /jobs`, `GET/PATCH /jobs/{id}`), editor+ read of runs (`GET /jobs/{id}/runs`, `GET /jobs/runs?ids=&status=`), and `PATCH /jobs/runs/{run_id}` (worker status update; `X-Api-Key` or admin). Job types `outbound_export|inbound_import|business_rule|bulk_operation`; run statuses `pending|running|success|failed|retry`. `job_run.job_definition_id` is `option<string>`: `None` for ad-hoc jobs (rule recompute). `running`/`retry` clear a stale `error`/`result`; `success` clears `error`.
- Manual job run (`src/api/jobs.rs::run_job`, `POST /jobs/{id}/run`, editor+): creates a `pending` `job_run` (`trigger: manual`) and publishes it to NATS JetStream via `src/jobqueue.rs` (`JOBS` stream, subjects `jobs.*`, WorkQueue retention, `max_age` from `JOBS_STREAM_MAX_AGE_MINUTES`). On queue failure the run is marked `failed` and the call returns 503. Config: `JOBS_NATS_URL`, `JOBS_STREAM_MAX_AGE_MINUTES`.
- Rule recompute (`src/api/rules.rs::recompute_rule`, `POST /rules/{id}/recompute`, admin/superadmin): queues a `business_rule` `job_run` (no definition) whose parameters embed the rule `{name, type, script}`, `applies_to`, and optional `object_ids`. The worker executes and reports per object.
- Computed write-back (`src/api/objects.rs::update_computed`, `PATCH /objects/{id}/computed`, `X-Api-Key` or admin): machine-to-machine path that merges only declared-`computed` values (`rules.rs::resolve_computed`) into an object without re-running rules (used by the recompute worker).
- Cron scheduler (`src/scheduler.rs`): a background task (`tokio::spawn` in `run()`) keeps `tokio_cron_scheduler` in sync with enabled scheduled `job_definition` records (6-field seconds-first cron) and, on each fire, creates a `job_run` (`trigger: schedule`) and publishes it to JetStream via `queue_run`. Sync interval from `SCHEDULER_INTERVAL_S` (default 60).
- Dead-letter monitor (`src/jobqueue.rs::monitor_dlq`, background task): ensures the `JOBS_DLQ` stream (`jobs.dlq.*`, Limits retention, manual cleanup) and, for each dead-lettered message, marks the corresponding `job_run` as finally `failed` via `jobs::fail_run` („dead lettered after N attempts“).
- SurrealDB `option<T>` fields reject JSON `null` (the distinct `NULL` value, not `NONE`): omit `None` option fields when building `CREATE ... CONTENT`, and clear them with `UPDATE ... SET field = NONE;`.

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

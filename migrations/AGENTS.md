# migrations — DOX Contract

## Purpose

Numbered SurrealQL steps for a database that already runs. `scripts/run-migration.sh` applies every pending file before the services start (the deploy script calls it between the infrastructure and the application containers).

## Ownership

- This folder owns the migration files and the rules below.
- The **base schema** is owned by `crates/pim-core/src/db/schema.rs` (all statements are `DEFINE ... IF NOT EXISTS`, applied when `pim-core` starts) — not by this folder.
- The runner `scripts/run-migration.sh` owns the bookkeeping table `schema_migrations` (`name`, `applied_at`) and the baseline behaviour.

## Local Contracts

- **Name:** `NNN_snake_case.surql`, three-digit number, applied in lexicographic order (`001_baseline.surql` is the starting point).
- **One change per file.** Files are applied once and then booked; the runner stops at the first failure and does not book it.
- **Never edit, rename or renumber an applied file** — its effects are not replayed. Add a new one.
- **A file must be harmless on a fresh database**, because a fresh deployment runs the whole history against an empty one. It must not assume that the core schema exists yet: migrations run **before** `pim-core` starts.
- **Do not repeat the core schema.** Tables/fields the core defines do not belong in a migration; only changes that cannot be expressed as a `DEFINE ... IF NOT EXISTS` in `schema.rs` do (data backfills, one-off repairs, renames).
- **Use `DEFINE ... IF NOT EXISTS` / `UPSERT`** so a re-run after a failed batch stays safe.
- **`OPTION IMPORT;` is not usable here** — it disables field processing and events. The runner talks to `POST /sql`, which reports one status per statement and answers HTTP 400 for a batch that does not parse.

## Work Guidance

- Add a file, then check the state: `bash scripts/run-migration.sh --status`.
- Against a local database: `SURREALDB_HTTP_URL=http://127.0.0.1:8000 SURREALDB_USER=root SURREALDB_PASS=root bash scripts/run-migration.sh`.
- `--baseline` books every pending file as applied without executing it; the runner does that automatically when `schema_migrations` is empty but the core's marker table `system_setting` already exists (`AUTO_BASELINE_DB_MIGRATIONS=false` switches it off).

## Verification

- `--status` lists applied and pending files; applying twice reports `skipped ... (applied)`.
- The migration runs on the real path in `scripts/deploy-vps-meadow.sh` (step "Apply migrations") before `pim-core` and `pim-frontend` start.

## Child DOX Index

None.

// 001_baseline.surql - the starting point of the migration history.
//
// This file carries no statements on purpose. The base schema (every table, field
// and index) is owned by the core: `crates/pim-core/src/db/schema.rs` defines it
// with `DEFINE ... IF NOT EXISTS` and applies it when `pim-core` starts, so a
// duplicate schema definition here would be a second source of truth that
// silently drifts.
//
// What the file does is book the starting point: a fresh database runs it and
// records it in `schema_migrations`, a database that already carries the core
// schema is baselined against it (see `scripts/run-migration.sh`). Every later
// file `NNN_*.surql` holds one change of its own - contract: AGENTS.md.
//
// A batch without statements is valid: SurrealDB answers HTTP 200 with an empty
// result list.

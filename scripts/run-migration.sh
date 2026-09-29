#!/usr/bin/env bash
# Meadow PIM - apply the SurrealQL migrations to the database.
#
# Usage:
#   ./run-migration.sh --status     # what is applied, what is pending (changes nothing)
#   ./run-migration.sh              # apply every pending file, in name order
#   ./run-migration.sh --baseline   # book every pending file as applied, run nothing
#   ./run-migration.sh --file 002_x.surql
#
# The runner talks to the SurrealDB HTTP endpoint `POST /sql`. That endpoint
# answers **per statement** (`{"status":"OK"|"ERR", ...}`) and refuses a batch
# that does not parse with HTTP 400, so a migration can only be booked once
# every statement in it succeeded. `surreal import` is deliberately not used:
# it demands `OPTION IMPORT;` as the first statement, which *disables* field
# processing and events - exactly what schema work needs.
#
# The base schema is owned by the core: `crates/pim-core/src/db/schema.rs`
# defines every table with `DEFINE ... IF NOT EXISTS` and applies it on start.
# A database that already carries that schema is therefore *baselined* (the
# files are booked without running) when `schema_migrations` is still empty -
# see `AUTO_BASELINE_DB_MIGRATIONS` below.
#
# Files are applied in lexicographic order, each one booked in the table
# `schema_migrations` (name, applied_at). Never edit an applied file; add a new
# one. Contract: migrations/AGENTS.md.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . <(tr -d '\r' <./.env.vps); set +a; fi
: "${SURREALDB_NAMESPACE:=meadow}"
: "${SURREALDB_DATABASE:=meadow}"
: "${SURREALDB_USER:?SURREALDB_USER required}"
: "${SURREALDB_PASS:?SURREALDB_PASS required}"
: "${SURREALDB_HTTP_URL:=http://127.0.0.1:8000}"
: "${MIGRATIONS_DIR:=migrations}"
# Book an already-schemad database instead of replaying the files (a fresh
# container that was ever started has the core schema already).
: "${AUTO_BASELINE_DB_MIGRATIONS:=true}"

command -v curl >/dev/null 2>&1 || {
  echo "curl is required (it talks to $SURREALDB_HTTP_URL/sql)" >&2
  exit 1
}
[ -d "$MIGRATIONS_DIR" ] || { echo "no directory $MIGRATIONS_DIR" >&2; exit 1; }

MODE=apply
ONLY_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --status) MODE=status ;;
    --baseline) MODE=baseline ;;
    --file)
      shift
      ONLY_FILE="${1:?--file needs a file name}"
      ;;
    *) echo "usage: $0 [--status | --baseline | --file <name>]" >&2; exit 2 ;;
  esac
  shift
done

# Runs one batch of SurrealQL and fails loudly on a statement error or a parse
# error. The body is passed via stdin so the file needs no escaping, and the
# answer is captured in one call (curl appends the HTTP code as the last line).
run_sql() {
  local body="$1" answer http payload
  answer="$(printf '%s' "$body" | curl -sS -w '\n%{http_code}' \
    -X POST "${SURREALDB_HTTP_URL%/}/sql" \
    -H "surreal-ns: $SURREALDB_NAMESPACE" \
    -H "surreal-db: $SURREALDB_DATABASE" \
    -H 'Content-Type: application/surrealql' \
    -u "$SURREALDB_USER:$SURREALDB_PASS" \
    --data-binary @-)" || {
    echo "cannot reach ${SURREALDB_HTTP_URL%/}/sql (is surrealdb running?)" >&2
    return 1
  }
  http="${answer##*$'\n'}"
  payload="${answer%$'\n'*}"
  if [ "$http" != "200" ]; then
    echo "SurrealDB answered HTTP $http:" >&2
    echo "$payload" >&2
    return 1
  fi
  if printf '%s' "$payload" | grep -q '"status":"ERR"'; then
    echo "a statement failed:" >&2
    echo "$payload" >&2
    return 1
  fi
  printf '%s\n' "$payload"
}

# The namespace and the database are the runner's own prerequisite: SurrealDB
# refuses every write into a database that was never defined (`The database 'x'
# does not exist`), and on a fresh volume neither exists — that is exactly the
# first-deployment path. `--status` works with either.
run_sql "
DEFINE NAMESPACE IF NOT EXISTS $SURREALDB_NAMESPACE;
USE NS $SURREALDB_NAMESPACE;
DEFINE DATABASE IF NOT EXISTS $SURREALDB_DATABASE;
" >/dev/null

run_sql "
DEFINE TABLE IF NOT EXISTS schema_migrations SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS name ON schema_migrations TYPE string;
DEFINE FIELD IF NOT EXISTS applied_at ON schema_migrations TYPE datetime;
DEFINE INDEX IF NOT EXISTS schema_migrations_name ON schema_migrations FIELDS name UNIQUE;
" >/dev/null

applied_names() {
  run_sql "SELECT VALUE name FROM schema_migrations ORDER BY name;" |
    grep -o '[0-9][0-9A-Za-z_]*\.surql' | sort -u || true
}

# The core's own marker table: if it exists, `schema.rs` already ran and the
# database is not fresh.
schema_present() {
  run_sql 'INFO FOR DB;' | grep -q '"system_setting"'
}

book_applied() {
  local name="$1"
  run_sql "CREATE schema_migrations SET name = '$name', applied_at = time::now();" >/dev/null
  echo "    booked $name"
}

book_missing_files() {
  local name applied="$1" pending=0
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if printf '%s\n' "$applied" | grep -qx "$name"; then continue; fi
    book_applied "$name"
    pending=$((pending + 1))
  done < <(list_files)
  echo "==> baselined $pending file(s) (the core schema is already in the database)"
}

list_files() {
  if [ -n "$ONLY_FILE" ]; then
    [ -f "$MIGRATIONS_DIR/$ONLY_FILE" ] || {
      echo "no such migration: $MIGRATIONS_DIR/$ONLY_FILE" >&2
      exit 1
    }
    echo "$ONLY_FILE"
    return
  fi
  for path in "$MIGRATIONS_DIR"/*.surql; do
    [ -e "$path" ] || continue
    basename "$path"
  done | sort
}

APPLIED="$(applied_names)"

if [ "$MODE" = status ]; then
  echo "==> migrations in $MIGRATIONS_DIR against $SURREALDB_HTTP_URL (namespace $SURREALDB_NAMESPACE, database $SURREALDB_DATABASE)"
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if printf '%s\n' "$APPLIED" | grep -qx "$name"; then
      echo "    applied  $name"
    else
      echo "    pending  $name"
    fi
  done < <(list_files)
  exit 0
fi

if [ "$MODE" = baseline ]; then
  echo "==> baseline (nothing is executed)"
  book_missing_files "$APPLIED"
  exit 0
fi

if [ -z "$ONLY_FILE" ] && [ -z "$APPLIED" ] && schema_present &&
  [ "$AUTO_BASELINE_DB_MIGRATIONS" != "false" ]; then
  echo "==> the core schema is already in the database; baselining instead of replaying"
  book_missing_files ""
  echo "==> nothing to apply"
  exit 0
fi

echo "==> applying migrations to $SURREALDB_HTTP_URL (namespace $SURREALDB_NAMESPACE, database $SURREALDB_DATABASE)"
APPLIED="$(applied_names)"
count=0
while IFS= read -r name; do
  [ -n "$name" ] || continue
  if printf '%s\n' "$APPLIED" | grep -qx "$name"; then
    echo "    skipped  $name (applied)"
    continue
  fi
  echo "    applying $name"
  run_sql "$(cat "$MIGRATIONS_DIR/$name")" >/dev/null
  book_applied "$name"
  APPLIED="$APPLIED
$name"
  count=$((count + 1))
done < <(list_files)

echo "==> $count migration(s) applied"

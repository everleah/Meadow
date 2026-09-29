#!/usr/bin/env bash
# Meadow PIM - restore the production SurrealDB from a backup file.
#
# Usage: ./restore-vps-db.sh ./backups/db/meadow-20260101-120000.surql [--force]
#
# The export is replayed in chunks through `POST /sql`, deliberately **not** with
# `surreal import`:
#
#   * `import` fails on any real export size with `error sending request for url
#     (.../import)` — measured against this image: a 100-statement batch succeeds
#     in 3 s, a 0.6 MB batch (9 500 statements) already fails after 32 s, and the
#     production-sized file never got through.
#   * the endpoint refuses bodies above roughly 4 MB anyway (`HTTP 413 length
#     limit exceeded`).
#
# `/sql` answers **per statement** (`{"status":"OK"|"ERR"}`) and answers HTTP 400
# for a batch that does not parse, so every chunk is checked and the restore stops
# at the first failure instead of reporting success on a half-written database.
#
# A restore merges by record id, it does not wipe the database first: take a fresh
# backup before you restore.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  echo "usage: $0 <backup.surql> [--force]" >&2
  exit 2
}

FILE="${1:-}"
[ -n "$FILE" ] || usage
[ -f "$FILE" ] || {
  echo "backup file not found: $FILE" >&2
  exit 1
}
shift

force=false
[ "${1:-}" = "--force" ] && force=true

if [ "$force" != true ]; then
  read -r -p "This writes the backup into the live database. Type 'yes' to continue: " confirm
  [ "$confirm" = "yes" ] || {
    echo "aborted" >&2
    exit 1
  }
fi

if [ -f .env.vps ]; then set -a; . <(tr -d '\r' <./.env.vps); set +a; else
  echo ".env.vps not found - this script only runs on the server" >&2
  exit 1
fi
: "${SURREALDB_NAMESPACE:=meadow}"
: "${SURREALDB_DATABASE:=meadow}"
: "${SURREALDB_USER:?SURREALDB_USER required}"
: "${SURREALDB_PASS:?SURREALDB_PASS required}"
: "${SURREALDB_HTTP_URL:=http://127.0.0.1:8000}"
: "${RESTORE_CHUNK_BYTES:=2097152}"

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
command -v split >/dev/null 2>&1 || { echo "split (coreutils) is required" >&2; exit 1; }
command -v awk >/dev/null 2>&1 || { echo "awk is required" >&2; exit 1; }

sql() {
  local body_file="$1" answer http payload
  if ! answer="$(curl -sS -w '\n%{http_code}' -X POST "${SURREALDB_HTTP_URL%/}/sql" \
    -H "surreal-ns: $SURREALDB_NAMESPACE" \
    -H "surreal-db: $SURREALDB_DATABASE" \
    -H 'Content-Type: application/surrealql' \
    -u "$SURREALDB_USER:$SURREALDB_PASS" \
    --data-binary "@$body_file")"; then
    echo "curl did not reach ${SURREALDB_HTTP_URL%/}/sql (see its message above)" >&2
    return 1
  fi
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

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# `mktemp` honours TMPDIR; the chunk files are written next to each other there.
work="$(cd "$work" && pwd)"

sql_query() {
  printf '%s\n' "$1" >"$work/query.surql"
  sql "$work/query.surql"
}

# A database that was never defined rejects every write (`The database 'x' does
# not exist`), so a restore into a fresh volume has to create the scope first.
echo "==> Ensuring namespace $SURREALDB_NAMESPACE and database $SURREALDB_DATABASE exist"
sql_query "
DEFINE NAMESPACE IF NOT EXISTS $SURREALDB_NAMESPACE;
USE NS $SURREALDB_NAMESPACE;
DEFINE DATABASE IF NOT EXISTS $SURREALDB_DATABASE;
" >/dev/null

echo "==> Splitting $FILE into chunks of at most ${RESTORE_CHUNK_BYTES} bytes"
# `-C` cuts at line boundaries — but only while a line fits: a statement longer
# than the chunk size would be sliced in half and rejected by the parser. So the
# chunk size is widened to the longest line of the export, and a line above the
# server's payload limit stops the run instead of half-applying it.
limit=3670016   # ~3.5 MiB: below the ~4 MB the endpoint accepts
longest="$(LC_ALL=C awk '{ if (length($0) > m) m = length($0) } END { print m + 0 }' "$FILE")"
if [ "$longest" -ge "$limit" ]; then
  echo "a single statement in $FILE is $longest bytes long, above the ~4 MB the" >&2
  echo "endpoint accepts - split the export by hand or raise the server limit" >&2
  exit 1
fi
if [ "$longest" -ge "$RESTORE_CHUNK_BYTES" ]; then
  RESTORE_CHUNK_BYTES=$((longest + 1024))
  echo "    widened to $RESTORE_CHUNK_BYTES bytes (longest line: $longest)"
fi
split -C "$RESTORE_CHUNK_BYTES" -d -a 4 "$FILE" "$work/chunk-"
total="$(find "$work" -name 'chunk-*' | wc -l | tr -d ' ')"
echo "    $total chunk(s)"

index=0
started="$(date +%s)"
for chunk in "$work"/chunk-*; do
  index=$((index + 1))
  {
    printf 'OPTION IMPORT;\n'
    cat "$chunk"
  } >"$work/prepared.surql"
  printf '    chunk %s/%s ... ' "$index" "$total"
  if ! sql "$work/prepared.surql" >/dev/null; then
    echo "failed" >&2
    echo "restore aborted in chunk $index of $total - the database is partially written" >&2
    exit 1
  fi
  echo "ok"
done
echo "==> $total chunk(s) replayed in $(( $(date +%s) - started ))s"

echo "==> Row counts (sanity check, not a guarantee)"
for table in object object_type attribute_definition system_setting user; do
  count="$(sql_query "SELECT count() FROM $table GROUP ALL;" 2>/dev/null |
    grep -o '"count":[0-9]*' | head -1 | cut -d: -f2 || true)"
  printf '    %-22s %s\n' "$table" "${count:-n/a}"
done

echo "==> Restored from $FILE"

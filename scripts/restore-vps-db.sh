#!/usr/bin/env bash
# Meadow PIM — restore the production SurrealDB from a backup file.
# Usage: ./restore-vps-db.sh ./backups/db/meadow-20260101-120000.surql [--force]
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-}"
[ -n "$FILE" ] || { echo "usage: $0 <backup.surql> [--force]" >&2; exit 1; }
[ -f "$FILE" ] || { echo "backup file not found: $FILE" >&2; exit 1; }

if [ "${2:-}" != "--force" ]; then
  read -r -p "This will overwrite the live database. Type 'yes' to continue: " confirm
  [ "$confirm" = "yes" ] || { echo "aborted" >&2; exit 1; }
fi

if [ -f .env.vps ]; then set -a; . ./.env.vps; set +a; fi
: "${SURREALDB_NAMESPACE:=meadow}"
: "${SURREALDB_DATABASE:=meadow}"
: "${SURREALDB_USER:?SURREALDB_USER required}"
: "${SURREALDB_PASS:?SURREALDB_PASS required}"

CONTAINER="$(docker compose -f docker-compose.vps.yml ps -q surrealdb)"
[ -n "$CONTAINER" ] || { echo "surrealdb container is not running" >&2; exit 1; }

# Copy the backup into the container, then import (the image has no shell, so
# the surrealdb binary is invoked directly with the file path).
docker cp "$FILE" "$CONTAINER:/tmp/meadow-restore.surql"
docker exec "$CONTAINER" /surreal import \
  --namespace "$SURREALDB_NAMESPACE" --database "$SURREALDB_DATABASE" \
  --endpoint http://localhost:8000 --username "$SURREALDB_USER" --password "$SURREALDB_PASS" \
  /tmp/meadow-restore.surql

echo "Restored from $FILE"

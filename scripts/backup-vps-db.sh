#!/usr/bin/env bash
# Meadow PIM — export the production SurrealDB into $BACKUP_DIR.
# Cron:  0 2 * * * /opt/meadow/scripts/backup-vps-db.sh >> /var/log/meadow-backup.log 2>&1
#
# The database is authoritative; export artefacts in the MinIO bucket can be
# regenerated from it (plan/detail/betrieb-deployment.md §10).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . <(tr -d '\r' <./.env.vps); set +a; else
  echo ".env.vps not found - this script only runs on the server" >&2
  exit 1
fi
: "${SURREALDB_NAMESPACE:=meadow}"
: "${SURREALDB_DATABASE:=meadow}"
: "${SURREALDB_USER:?SURREALDB_USER required}"
: "${SURREALDB_PASS:?SURREALDB_PASS required}"

OUT_DIR="${BACKUP_DIR:-backups/db}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$OUT_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
FILE="$OUT_DIR/meadow-${STAMP}.surql"

CONTAINER="$(docker compose -f docker-compose.vps.yml ps -q surrealdb)"
[ -n "$CONTAINER" ] || { echo "surrealdb container is not running" >&2; exit 1; }

# Export inside the container (the binary is /surreal; this image's CLI uses
# --namespace/--database, not --conn/--ns/--db), then copy the file out.
docker exec "$CONTAINER" /surreal export \
  --namespace "$SURREALDB_NAMESPACE" --database "$SURREALDB_DATABASE" \
  --endpoint http://localhost:8000 --username "$SURREALDB_USER" --password "$SURREALDB_PASS" \
  /tmp/meadow-backup.surql
docker cp "$CONTAINER:/tmp/meadow-backup.surql" "$FILE"

echo "Backup written to $FILE"
find "$OUT_DIR" -name 'meadow-*.surql' -mtime +"$RETENTION" -delete || true

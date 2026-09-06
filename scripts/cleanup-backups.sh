#!/usr/bin/env bash
# Meadow PIM — delete backups older than $BACKUP_RETENTION_DAYS.
# Cron:  30 2 * * 0 /opt/meadow/cleanup-backups.sh >> /var/log/meadow-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . ./.env.vps; set +a; fi
OUT_DIR="${BACKUP_DIR:-backups/db}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"

[ -d "$OUT_DIR" ] || { echo "no backup dir $OUT_DIR"; exit 0; }
find "$OUT_DIR" -name 'meadow-*.surql' -mtime +"$RETENTION" -delete
echo "cleaned backups older than $RETENTION days in $OUT_DIR"

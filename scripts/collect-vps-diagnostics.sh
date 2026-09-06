#!/usr/bin/env bash
# Meadow PIM — collect a timestamped diagnostics bundle for triage.
# Usage: ./collect-vps-diagnostics.sh
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.vps.yml"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="diagnostics-${STAMP}"
mkdir -p "$OUT"

$COMPOSE config > "$OUT/compose-config.txt" 2>&1 || true
$COMPOSE ps > "$OUT/ps.txt" 2>&1 || true
for svc in pim-core pim-frontend pim-worker surrealdb nats object-storage; do
  $COMPOSE inspect "$svc" > "$OUT/inspect-${svc}.txt" 2>&1 || true
  $COMPOSE logs --tail 200 "$svc" > "$OUT/logs-${svc}.txt" 2>&1 || true
done
{
  echo "=== host ==="
  uname -a
  df -h
  free -h
} > "$OUT/host.txt"

echo "Diagnostics written to $OUT"

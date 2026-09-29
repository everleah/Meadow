#!/usr/bin/env bash
# Meadow PIM — follow error-level lines across the service logs.
# Usage: ./tail-vps-errors.sh [--follow]
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.vps ] || {
  echo ".env.vps not found - copy .env.vps.example and fill in the secrets" >&2
  exit 1
}
set -a
. <(tr -d '\r' <./.env.vps)
set +a

COMPOSE="docker compose -f docker-compose.vps.yml"
FOLLOW="${1:-}"

for svc in pim-core pim-frontend pim-worker; do
  if [ "$FOLLOW" = "--follow" ]; then
    $COMPOSE logs -f --tail 0 "$svc" 2>&1 | { grep --line-buffered -iE 'error|panic|fatal|exception' || true; } &
  else
    $COMPOSE logs --tail 200 "$svc" 2>&1 | grep -iE 'error|panic|fatal|exception' || true
  fi
done

if [ "$FOLLOW" = "--follow" ]; then
  wait
fi

#!/usr/bin/env bash
# Meadow PIM — wait for healthy containers and check internal endpoints.
# Usage: ./healthcheck-vps.sh
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.vps.yml"

echo "==> Wait for containers (max 120s)"
for i in $(seq 1 60); do
  STATUS="$($COMPOSE ps --format '{{.Name}}: {{.Status}}')"
  if echo "$STATUS" | grep -qE 'unhealthy|exited'; then
    echo "$STATUS"
    echo "one or more containers are unhealthy/exited" >&2
    exit 1
  fi
  echo "$STATUS"
  # Stop polling once every container is "Up" (healthy or running).
  if ! echo "$STATUS" | grep -qE 'starting|created'; then
    break
  fi
  sleep 2
done

echo "==> Internal endpoint checks"
curl -fsS http://127.0.0.1:8080/health >/dev/null && echo "  pim-core  ok"
curl -fsS -o /dev/null http://127.0.0.1:8081/ && echo "  pim-ui    ok"

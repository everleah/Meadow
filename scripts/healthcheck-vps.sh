#!/usr/bin/env bash
# Meadow PIM - wait for healthy containers and check the internal endpoints.
#
# Usage: ./healthcheck-vps.sh
#
# Containers with a compose healthcheck (surrealdb, pim-core, pim-frontend,
# object-storage, pim-worker) are judged by their status. The frontend and the
# API have no published port for the UI, and nats has no shell to run a check in,
# so those three are asked from where they can be reached.
set -euo pipefail
cd "$(dirname "$0")/.."

# Without the variables compose interpolates empty values and warns on every
# call; the file is mandatory on the server anyway.
[ -f .env.vps ] || {
  echo ".env.vps not found - copy .env.vps.example and fill in the secrets" >&2
  exit 1
}
set -a
. <(tr -d '\r' <./.env.vps)
set +a

COMPOSE="docker compose -f docker-compose.vps.yml"

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }

echo "==> Wait for containers (max 120s)"
status=""
for _ in $(seq 1 60); do
  status="$($COMPOSE ps --format '{{.Name}}: {{.Status}}')"
  if echo "$status" | grep -qE 'unhealthy|exited|restarting'; then
    echo "$status"
    echo "one or more containers are unhealthy/exited" >&2
    exit 1
  fi
  # Done once nothing is "starting" or still "created".
  if ! echo "$status" | grep -qE 'starting|created|Waiting'; then
    break
  fi
  sleep 2
done
echo "$status"

echo "==> Internal endpoint checks"
curl -fsS http://127.0.0.1:8080/health >/dev/null && echo "  pim-core       ok (GET /health)"
curl -fsS http://127.0.0.1:8222/healthz >/dev/null && echo "  nats           ok (monitoring /healthz)"
$COMPOSE exec -T pim-frontend python -c \
  "import urllib.request as u; u.urlopen('http://127.0.0.1:8081/', timeout=5)" >/dev/null &&
  echo "  pim-frontend   ok (port 8081 in the container)"

echo "==> All checks passed"

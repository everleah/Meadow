#!/usr/bin/env bash
# Meadow PIM - deploy on a VPS (behind a Traefik reverse proxy).
#
# Usage: ./deploy-vps-meadow.sh
#
# Order: preflight -> registry login -> compose validation -> network -> pull ->
# infrastructure -> migrations -> application -> health. `docker compose up -d`
# keeps the named volumes, so a redeploy never touches the data.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env.vps ] || {
  echo ".env.vps not found - copy .env.vps.example and fill in the secrets" >&2
  exit 1
}
# `.env.vps` may have been written on Windows; a stray \r turns every value into a
# parser error on the server, so carriage returns are stripped while loading.
set -a
. <(tr -d '\r' <./.env.vps)
set +a

: "${PIM_REGISTRY:?PIM_REGISTRY required}"
: "${TRAEFIK_NETWORK:?TRAEFIK_NETWORK required}"
: "${AUTO_RUN_DB_MIGRATIONS:=true}"
: "${RUN_POST_DEPLOY_HEALTHCHECK:=true}"

# Preflight: an empty or placeholder value here becomes a broken Traefik route
# or a container that starts with a placeholder secret.
missing=""
for name in PIM_UI_HOST PIM_API_HOST JWT_SECRET UI_STORAGE_SECRET LOG_INGEST_API_KEY \
  SURREALDB_USER SURREALDB_PASS S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_BUCKET; do
  value="$(printenv "$name" || true)"
  if [ -z "$value" ]; then
    echo "preflight: $name is empty in .env.vps" >&2
    missing="yes"
  elif [ "$value" = "CHANGE_ME" ]; then
    echo "preflight: $name still holds CHANGE_ME" >&2
    missing="yes"
  fi
done
[ -z "$missing" ] || exit 1

COMPOSE="docker compose -f docker-compose.vps.yml"
INFRA="surrealdb nats object-storage"

echo "==> Registry login ($PIM_REGISTRY)"
if [ -n "${DOCKER_REGISTRY_USER:-}" ] && [ -n "${DOCKER_REGISTRY_PASSWORD:-}" ]; then
  printf '%s' "$DOCKER_REGISTRY_PASSWORD" |
    docker login "$PIM_REGISTRY" -u "$DOCKER_REGISTRY_USER" --password-stdin
else
  docker login "$PIM_REGISTRY"
fi

echo "==> Validate compose"
$COMPOSE config --quiet

echo "==> Check external Traefik network"
if ! docker network inspect "$TRAEFIK_NETWORK" >/dev/null 2>&1; then
  echo "    creating network $TRAEFIK_NETWORK"
  docker network create "$TRAEFIK_NETWORK"
fi

echo "==> Pull images"
$COMPOSE pull

echo "==> Start infrastructure ($INFRA)"
$COMPOSE up -d $INFRA
health=""
for _ in $(seq 1 30); do
  health="$($COMPOSE ps --status running --format '{{.Health}}' surrealdb 2>/dev/null || true)"
  [ "$health" = "healthy" ] && break
  sleep 2
done
if [ "$health" != "healthy" ]; then
  echo "surrealdb did not become healthy:" >&2
  $COMPOSE ps >&2
  exit 1
fi

if [ "$AUTO_RUN_DB_MIGRATIONS" != "false" ]; then
  echo "==> Apply migrations"
  ./scripts/run-migration.sh
else
  echo "==> Migrations skipped (AUTO_RUN_DB_MIGRATIONS=false)"
fi

echo "==> Start the application (the core applies its own schema on startup, see migrations/AGENTS.md)"
$COMPOSE up -d --remove-orphans

echo "==> Status"
$COMPOSE ps

if [ "$RUN_POST_DEPLOY_HEALTHCHECK" != "false" ]; then
  ./scripts/healthcheck-vps.sh
fi

echo "==> Deployed. UI: https://$PIM_UI_HOST  API: https://$PIM_API_HOST"

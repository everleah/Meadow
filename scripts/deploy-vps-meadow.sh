#!/usr/bin/env bash
# Meadow PIM — deploy on the VPS (behind Traefik).
# Usage: ./deploy-vps-meadow.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . ./.env.vps; set +a; fi
: "${PIM_REGISTRY:?PIM_REGISTRY required}"
: "${TRAEFIK_NETWORK:=traefik-net}"

COMPOSE="docker compose -f docker-compose.vps.yml"

echo "==> Registry login"
docker login "$PIM_REGISTRY"

echo "==> Validate compose"
$COMPOSE config --quiet

echo "==> Check external Traefik network"
if ! docker network inspect "$TRAEFIK_NETWORK" >/dev/null 2>&1; then
  echo "    creating network $TRAEFIK_NETWORK"
  docker network create "$TRAEFIK_NETWORK"
fi

echo "==> Pull images"
$COMPOSE pull

echo "==> Start (dependencies first via depends_on; the core applies schema on startup)"
$COMPOSE up -d --remove-orphans

echo "==> Status"
$COMPOSE ps

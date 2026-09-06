#!/usr/bin/env bash
# Meadow PIM — build and (optionally) push the service images.
# Usage: ./build-images.sh [--push]
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . ./.env.vps; set +a; fi
: "${PIM_REGISTRY:?PIM_REGISTRY required}"
: "${PIM_CORE_TAG:=latest}"
: "${PIM_FRONTEND_TAG:=latest}"
: "${PIM_WORKER_TAG:=latest}"

docker build -f docker/Dockerfile.core     -t "${PIM_REGISTRY}/pim-core:${PIM_CORE_TAG}" .
docker build -f docker/Dockerfile.frontend -t "${PIM_REGISTRY}/pim-frontend:${PIM_FRONTEND_TAG}" .
docker build -f docker/Dockerfile.worker   -t "${PIM_REGISTRY}/pim-worker:${PIM_WORKER_TAG}" .

if [ "${1:-}" = "--push" ]; then
  docker push "${PIM_REGISTRY}/pim-core:${PIM_CORE_TAG}"
  docker push "${PIM_REGISTRY}/pim-frontend:${PIM_FRONTEND_TAG}"
  docker push "${PIM_REGISTRY}/pim-worker:${PIM_WORKER_TAG}"
else
  echo "Images built locally; use --push to push to ${PIM_REGISTRY}"
fi

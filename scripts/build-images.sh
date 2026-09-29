#!/usr/bin/env bash
# Meadow PIM - build the service images (and optionally push them).
#
# Usage: ./build-images.sh [--push] [--no-cache] [--tag <tag>] [--registry <host/path>]
#
# Every image is tagged twice: with the immutable tag (the short git SHA by
# default, `-dirty` appended when the worktree is not clean) and with `latest`.
# Pin the immutable tag in `.env.vps` on the server — that is what makes a deploy
# reproducible and a rollback a one-line change.
#
# The registry comes from the environment, from `.env.vps` if it exists (on the
# server), or from `--registry` — the workstation has no `.env.vps`.
#
# Windows twin: scripts/build-images.ps1.
set -euo pipefail
cd "$(dirname "$0")/.."

push=false
no_cache=false
tag=""
registry_arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --push) push=true ;;
    --no-cache) no_cache=true ;;
    --tag)
      shift
      tag="${1:?--tag needs a value}"
      ;;
    --registry)
      shift
      registry_arg="${1:?--registry needs a value}"
      ;;
    *)
      echo "usage: $0 [--push] [--no-cache] [--tag <tag>] [--registry <host/path>]" >&2
      exit 2
      ;;
  esac
  shift
done

if [ -f .env.vps ]; then set -a; . <(tr -d '\r' <./.env.vps); set +a; fi
[ -n "$registry_arg" ] && PIM_REGISTRY="$registry_arg"
: "${PIM_REGISTRY:?PIM_REGISTRY required (--registry, the environment or .env.vps)}"

if [ -z "$tag" ]; then
  tag="$(git rev-parse --short HEAD)"
  [ -z "$(git status --porcelain)" ] || tag="${tag}-dirty"
fi

build_args=()
[ "$no_cache" = true ] && build_args+=(--no-cache)

echo "==> Registry $PIM_REGISTRY, tag $tag (plus latest)"
for service in core frontend worker; do
  image="${PIM_REGISTRY}/pim-${service}"
  echo "==> Building $image:$tag"
  docker build "${build_args[@]+"${build_args[@]}"}" -f "docker/Dockerfile.${service}" \
    -t "${image}:${tag}" -t "${image}:latest" .
done

if [ "$push" = true ]; then
  echo "==> Pushing (docker login $PIM_REGISTRY must have happened)"
  for service in core frontend worker; do
    image="${PIM_REGISTRY}/pim-${service}"
    docker push "${image}:${tag}"
    docker push "${image}:latest"
  done
  echo "==> Pin this tag on the server (.env.vps):"
  echo "    PIM_CORE_TAG=$tag"
  echo "    PIM_FRONTEND_TAG=$tag"
  echo "    PIM_WORKER_TAG=$tag"
else
  echo "==> Images built locally; use --push to push them to $PIM_REGISTRY"
fi

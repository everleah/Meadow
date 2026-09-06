#!/usr/bin/env bash
# Meadow PIM — one-time admin bootstrap.
#
# The core seeds a default `superadmin` user (username `admin`, password
# `admin`) on first start for development. On production you MUST change that
# password immediately. There is no password-change API yet, so this script
# verifies the login works and prints a reminder. A secure password-set / rotate
# flow is a Phase-9 hardening item.
#
# Usage: ./bootstrap-vps-admin.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . ./.env.vps; set +a; fi
: "${PIM_API_HOST:?PIM_API_HOST required}"

URL="https://${PIM_API_HOST}/auth/login"
echo "==> Verifying default admin login at $URL (first start only)"
if curl -fsS -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  -o /tmp/meadow-admin-token.json 2>/dev/null; then
  echo "    login OK — CHANGE the default password now (no change-password API yet; see plan)."
else
  echo "    login failed — the default admin may already be secured or the core is not ready." >&2
  exit 1
fi

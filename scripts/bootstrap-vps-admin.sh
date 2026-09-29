#!/usr/bin/env bash
# Meadow PIM - one-time admin bootstrap on the VPS.
#
# The core seeds the user `admin` with the password `admin`. This script replaces
# that password through the core's own flow (`POST /auth/password`, the same call
# the key button in the UI header makes) and verifies the new login. Nothing is
# written behind the core's back, so the password policy and the session
# invalidation of the core both apply.
#
# Usage:
#   ./bootstrap-vps-admin.sh                        # asks for the new password
#   ./bootstrap-vps-admin.sh --new-password <value> # non-interactive (shows up in the shell history)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env.vps ]; then set -a; . <(tr -d '\r' <./.env.vps); set +a; else
  echo ".env.vps not found - this script only runs on the server" >&2
  exit 1
fi
: "${PIM_API_HOST:?PIM_API_HOST required}"
BASE="https://${PIM_API_HOST}"

new_password=""
while [ $# -gt 0 ]; do
  case "$1" in
    --new-password)
      shift
      new_password="${1:?--new-password needs a value}"
      ;;
    *)
      echo "usage: $0 [--new-password <value>]" >&2
      exit 2
      ;;
  esac
  shift
done

if [ -z "$new_password" ]; then
  read -r -s -p "new password for the user 'admin': " new_password
  echo
  read -r -s -p "repeat: " again
  echo
  [ "$new_password" = "$again" ] || {
    echo "the two entries differ - nothing changed" >&2
    exit 1
  }
fi
[ -n "$new_password" ] || {
  echo "empty password - nothing changed" >&2
  exit 1
}

# The password travels inside JSON; only `\` and `"` would break the document.
json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

echo "==> Verifying the seeded login at $BASE/auth/login"
token="$(curl -fsS -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' |
  sed -n 's/.*"token":"\([^"]*\)".*/\1/p')"
if [ -z "$token" ]; then
  echo "the login with 'admin'/'admin' failed - the password was already changed," >&2
  echo "the user does not exist any more, or the core is not reachable. Nothing changed." >&2
  exit 1
fi

echo "==> Changing the password (POST /auth/password)"
answer="$(curl -sS -X POST "$BASE/auth/password" \
  -H "Authorization: Bearer $token" \
  -H 'Content-Type: application/json' \
  -d "{\"current_password\":\"admin\",\"new_password\":\"$(json_escape "$new_password")\"}")"
case "$answer" in
  *'"token"'*) ;;
  *)
    echo "the core refused the new password:" >&2
    echo "$answer" >&2
    exit 1
    ;;
esac

echo "==> Verifying the new login"
curl -fsS -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$(json_escape "$new_password")\"}" >/dev/null

echo "    ok - the seeded password is replaced."
echo "==> Next: create a personal superadmin account on the users page and keep"
echo "    'admin' as an emergency access only."

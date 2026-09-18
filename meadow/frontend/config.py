"""Frontend configuration (environment-driven)."""

from __future__ import annotations

import os

#: URL of the PIM core REST API. `127.0.0.1` (not `localhost`) avoids the
#: Windows IPv6-first resolution stall that adds ~2 s to every new connection
#: (`CoreClient` also normalises `localhost` -> `127.0.0.1` as a safety net).
CORE_URL = os.environ.get("PIM_CORE_URL", "http://127.0.0.1:8080")
#: Bind address and port for the frontend dev server.
HOST = os.environ.get("PIM_FRONTEND_HOST", "0.0.0.0")
PORT = int(os.environ.get("PIM_FRONTEND_PORT", "8081"))
#: Secret used to sign the browser session cookie (required by NiceGUI storage).
STORAGE_SECRET = os.environ.get("UI_STORAGE_SECRET", "meadow-dev-storage-secret")
#: How long a client keeps its state after its websocket dropped (seconds).
#: NiceGUI deletes it after **3 s** by default: a short outage (network hiccup,
#: suspended tab) then orphans a perfectly good page, and the tab either reloads
#: itself ("implicit handshake failed" — the socket endpoint answers `401`) or, if
#: the browser does not, keeps a dead socket. Longer window = reconnection instead
#: of reload. A *server restart* is unaffected: a new process knows no clients at
#: all, and there NiceGUI's own reload-on-refused-handshake is the cure.
RECONNECT_TIMEOUT = float(os.environ.get("PIM_FRONTEND_RECONNECT_S", "60"))

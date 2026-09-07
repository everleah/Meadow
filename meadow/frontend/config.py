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

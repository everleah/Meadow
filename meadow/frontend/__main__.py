"""Entry point: `python -m meadow.frontend` starts the NiceGUI web app."""

from __future__ import annotations

import logging

import engineio
from nicegui import ui

from meadow.frontend import auth, config, views
from meadow.frontend.theme import STATIC_DIR, apply_branding, register_static

logging.basicConfig(level=logging.INFO)


def _patch_engineio_disconnect_guard() -> None:
    """Guard engineio against a benign client-disconnect during polling.

    ``engineio``'s async ``translate_request`` returns an *empty* ``environ``
    when the first ASGI ``receive()`` event is an ``http.disconnect`` (the client
    vanished before sending a request body). ``AsyncServer.handle_request`` then
    reads ``environ['REQUEST_METHOD']`` and raises ``KeyError``, producing a
    noisy 500 traceback that is never delivered (the client is already gone).
    This is a known upstream gap — engineio's own tests assert ``environ == {}``
    for unknown request types, and neither 4.13.x nor 4.14.x guards the caller.

    We patch ``AsyncServer.handle_request`` to swallow that specific
    ``KeyError``: uvicorn skips the "returned without starting response" error
    for disconnected clients (``httptools_impl.run_asgi`` checks
    ``not self.disconnected``), so returning ``None`` is safe and clean.
    """

    original = engineio.AsyncServer.handle_request

    async def guarded(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except KeyError as exc:
            if exc.args == ("REQUEST_METHOD",):
                return None
            raise

    engineio.AsyncServer.handle_request = guarded


def main() -> None:
    """Applies branding, registers pages/middleware and starts the server."""
    _patch_engineio_disconnect_guard()
    apply_branding()
    register_static()
    auth.register()
    views.register_pages()
    ui.run(
        host=config.HOST,
        port=config.PORT,
        title="Meadow PIM",
        favicon=str(STATIC_DIR / "logos" / "favicon.ico"),
        storage_secret=config.STORAGE_SECRET,
        reload=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()

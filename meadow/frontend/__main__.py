"""Entry point: `python -m meadow.frontend` starts the NiceGUI web app."""

from __future__ import annotations

import logging

from nicegui import ui

from meadow.frontend import auth, config, views
from meadow.frontend.theme import STATIC_DIR, apply_branding, register_static

logging.basicConfig(level=logging.INFO)


def main() -> None:
    """Applies branding, registers pages/middleware and starts the server."""
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

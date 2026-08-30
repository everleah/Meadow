"""Meadow branding/CI: colors and the NiceGUI Quasar theme mapping.

The palette reflects the Meadow branding (see plan/detail/frontend.md):
a green meadow in a valley with a river, surrounded by forests and mountains.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import app

#: Directory with static brand assets (served under `/static`).
STATIC_DIR = Path(__file__).parent / "static"

#: Brand asset URLs (relative to the served `/static` prefix).
MEADOW_LOGO = "/static/logos/meadow_logo.svg"
LOGIN_BG_WEBP = "/static/images/meadow_login_bg.webp"
LOGIN_BG_PNG = "/static/images/meadow_login_bg.png"

#: Primary: the meadow (brand color).
MEADOW_GREEN = "#16a34a"
#: Secondary: the river.
RIVER_BLUE = "#0ea5e9"
#: Accent: sunlight / warm tone.
SUNLIGHT_AMBER = "#d97706"
#: Dark: mountains / forests (contrast).
MOUNTAIN_SLATE = "#1e293b"
#: Neutral: the valley (light background).
VALLEY_MIST = "#f1f5f9"
#: Positive: success / status.
MEADOW_LIGHT = "#22c55e"
#: Negative: errors.
ERROR_RED = "#dc2626"
#: Info.
RIVER_DEEP = "#0891b2"
#: Warning.
WARNING_AMBER = "#f59e0b"


def apply_branding() -> None:
    """Sets the app-wide Quasar theme to the Meadow palette."""
    app.colors(
        primary=MEADOW_GREEN,
        secondary=RIVER_BLUE,
        accent=SUNLIGHT_AMBER,
        dark=MOUNTAIN_SLATE,
        positive=MEADOW_LIGHT,
        negative=ERROR_RED,
        info=RIVER_DEEP,
        warning=WARNING_AMBER,
    )


def register_static() -> None:
    """Mounts the frontend static asset directory under `/static`."""
    app.add_static_files("/static", str(STATIC_DIR))

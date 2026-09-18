"""Pytest configuration: make the repository root importable."""

import sys
from pathlib import Path

#: NiceGUI's user simulation plugin. Besides the `user`/`screen` fixtures it moves
#: NiceGUI's file storage into a session temp dir, so the page tests
#: (`tests/test_products_page.py`) never touch the real `.nicegui` storage.
pytest_plugins = ["nicegui.testing.user_plugin"]

sys.path.insert(0, str(Path(__file__).resolve().parent))

"""Pytest configuration: make the repository root importable."""

import os
import sys
from pathlib import Path

#: NiceGUI's user simulation plugin. Besides the `user`/`screen` fixtures it moves
#: NiceGUI's file storage into a session temp dir, so the page tests
#: (`tests/test_products_page.py`) never touch the real `.nicegui` storage.
pytest_plugins = ["nicegui.testing.user_plugin"]

#: The frontend has no default for the cookie-signing secret (a published one
#: would be forgeable) and it must be at least 32 characters; the page tests
#: boot the real app, so they need a value.
os.environ.setdefault("UI_STORAGE_SECRET", "test-storage-secret-for-the-page-tests")

sys.path.insert(0, str(Path(__file__).resolve().parent))

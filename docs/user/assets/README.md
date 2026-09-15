# Handbook screenshots

Images referenced by the handbook chapters (`/help-assets/<file>.png`).

They are **generated**, not hand-made: `scripts/docs_screenshots.py` drives a running
dev instance with Playwright and writes the PNGs here. The frontend mounts this
folder at `/help-assets` (`meadow/frontend/help_page.py`), so a chapter references
an image with an absolute path:

```markdown
![Die Objektliste](/help-assets/product-grid.png)
```

Regenerate them whenever the UI of the shown area changes:

```bash
uv run python scripts/docs_screenshots.py            # all shots
uv run python scripts/docs_screenshots.py --list     # available shots
```

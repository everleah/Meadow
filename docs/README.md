# Meadow PIM — Documentation

Non-planning documentation for users, developers and operators. Planning documents live in `plan/`.

## User handbook

The handbook is written as Markdown in `docs/user/<language>/` and rendered by the frontend at `/help` (chapter list, full-text search, no build step). German and English chapters ship together and share the same chapter ids, so a link like `/help?page=product-form` works in either language.

- [Erste Schritte](user/de/01-getting-started.md) · [Objektliste (Grid)](user/de/02-product-grid.md) · [Objekt bearbeiten](user/de/03-product-form.md) · [Daten importieren](user/de/04-import.md) · [Daten exportieren](user/de/05-export.md)
- [Getting started](user/en/01-getting-started.md) · [Object list (grid)](user/en/02-product-grid.md) · [Editing an object](user/en/03-product-form.md) · [Importing data](user/en/04-import.md) · [Exporting data](user/en/05-export.md)

## Integration

- [MEF & Event System Integration](integration/mef-and-events.md) — the Meadow Exchange Format (import/export JSON) and the NATS/outbox event contract for external systems.

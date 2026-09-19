# docs — Project Documentation (non-planning)

## Purpose

Owns all durable project documentation that does not belong to planning: user-facing, developer, operational, and other documentation, ideally organized hierarchically. Planning documents stay in `plan/`.

## Ownership

- Owns all files under `docs/`.
- Does not own planning documents — those belong in `plan/` (owned by `plan/AGENTS.md`).

## Local Contracts

- Non-planning documentation goes under `docs/` (root preference), ideally in hierarchical subfolders per topic or audience.
- Planning documents (architecture, technical planning) stay in `plan/`; do not move them here.
- Documentation language follows audience and context; the planning docs are German (root rule), other docs have no fixed rule yet.
- `docs/user/<language>/` is the **user handbook** and has a fixed shape: one chapter per file, `NN-<slug>.md` (number = reading order, `<slug>` = the chapter id in `/help?page=<slug>`), first `#` heading = chapter title. Chapter ids are **identical in every language** (`de/` and `en/` ship the same file names) — only the text is translated, so a deep link works in any language.
- The handbook is rendered by the UI **without a build step** (`/help`, `meadow/frontend/help_content.py`); section anchors come from the Markdown renderer, never from hand-written slugs. The catalogue is cached per process, so a content change needs a frontend restart in dev.
- Chapter images live in `docs/user/assets/` and are referenced as `/help-assets/<file>.png` (mounted by `meadow/frontend/help_page.py`). They are **generated**: `uv run python scripts/docs_screenshots.py` photographs a running dev instance with Playwright (fixed viewport, locale from the browser context, marker waits). Do not add hand-made screenshots — they drift.

## Work Guidance

- Structure `docs/` hierarchically by topic or audience (e.g. user, developer, operations).
- Split documents into subfolders when they outgrow a single page; keep an index where useful.
- Refine conventions once the first real documents are added.
- Current content: `README.md` (index), `user/` (in-app handbook, de/en), `integration/mef-and-events.md` (MEF & event system integration reference) and `developer/` (engineering references; `rule-context.md`/`.json` are **generated** by `scripts/ai_context.py`, never edited by hand). The authoritative spec stays in `plan/`; these docs are the audience-facing contract.
- **Help texts follow one rule:** a tooltip answers in one sentence, the handbook chapter answers in full — a concept is explained at exactly **one** place and linked from there (`info_button` for a concept, `chapter_button` for the page itself). Do not repeat the same explanation in several dialogs.
- New handbook chapters are added in `docs/user/de/` **and** `docs/user/en/` (same ids, same order — `tests/test_help.py` enforces it); cross-references inside the handbook are absolute links `/help?page=<id>`.

## Verification

- `pytest tests/test_help.py` — handbook invariants: same chapter ids per language, every search anchor exists exactly once in the rendered HTML, every `/help?page=…` cross-reference points at a real chapter.

## Child DOX Index

- (none yet)

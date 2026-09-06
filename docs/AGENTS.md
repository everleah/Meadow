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

## Work Guidance

- Structure `docs/` hierarchically by topic or audience (e.g. user, developer, operations).
- Split documents into subfolders when they outgrow a single page; keep an index where useful.
- Refine conventions once the first real documents are added.
- Current content: `README.md` (index) and `integration/mef-and-events.md` (MEF & event system integration reference). The authoritative spec stays in `plan/`; this doc is the audience-facing contract.

## Verification

## Child DOX Index

- (none yet)

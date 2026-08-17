# plan — Architecture & Technical Planning

## Purpose

Owns the architecture and technical planning documents for Meadow PIM. This folder is the single source of truth for the agreed architecture and technical plan of the system.

## Ownership

- Owns all files under `plan/`.
- `plan/meadow-pim-architektur.md` — main architecture and technical plan.
- `plan/detail/` — owned by `plan/detail/AGENTS.md` (detail documents deepening main-document sections 4–13).

## Local Contracts

- `meadow-pim-architektur.md` is the authoritative architecture and technical plan. Implementation decisions must stay consistent with it, or the plan must be updated in the same change.
- The document is the single source of truth for:
  - overall architecture (Rust core, Python worker and frontend, NATS JetStream, SurrealDB, object storage)
  - final technology stack (sections 2–3)
  - data model (generic `object`, `attribute_definition`, `attribute_group`, `unit`, `reference`, `relation_definition`, `language_tree`, `business_rule`, `job_definition`, `job_run`, `audit_log`)
  - REST API design (section 5)
  - jobs, events, and webhook integration (sections 6–7)
  - workflows and RBAC/ABAC (sections 8–9)
  - frontend scope (section 11)
  - deployment and operations (section 12)
  - development phases 1–9 (section 14)
- The document is written in German; keep new planning documents in German unless otherwise directed.
- Detail documents under `plan/detail/` deepen main-document sections 4–13. Each detail document is the single source of truth for its topic; the main document keeps a summary per section plus an inline link and must stay consistent with the detail document.

## Work Guidance

- When the architecture changes, update `meadow-pim-architektur.md` in the same change and keep the existing section numbering and headings intact.
- Record the change date at the end of the document when a substantive change is made.
- When a main-document section outgrows a summary, move the detail into the matching file under `plan/detail/` and leave a summary plus inline link in the main document.
- Keep each detail document's header block (`Hauptdokument`, `Relevante Phasen`, `Stand`) up to date.

## Verification

## Child DOX Index

- `detail/` — detail documents deepening main-document sections 4–13; owned by `detail/AGENTS.md`.

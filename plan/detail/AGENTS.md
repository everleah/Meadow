# plan/detail — Detail Documents

## Purpose

Owns the detailed planning documents that deepen sections 4–13 of the main architecture document (`plan/meadow-pim-architektur.md`). Each file here is the single source of truth for its topic; the main document keeps a summary per section plus an inline link.

## Ownership

- Owns all files under `plan/detail/`.
- Owned documents, one per main-document section:
  - `datenmodell.md` — data model (section 4)
  - `relationen.md` — relations model: composition & aggregation (section 4)
  - `einheiten.md` — units & measurement units (section 4)
  - `sprachbaum-mehrsprachigkeit.md` — language tree & multilingual data (section 4.4)
  - `datenwertvererbung.md` — data value inheritance & conflict resolution (sections 4.5, 4.7)
  - `kategorisierung.md` — categorization & grouping (section 4.6)
  - `api-design.md` — REST API design (section 5)
  - `jobs-scheduler.md` — background jobs & scheduler (section 6)
  - `integration-eventsystem.md` — integration & event system (section 7)
  - `workflow-freigaben.md` — workflow & releases (section 8)
  - `business-rules.md` — Business Rules: Python scripting, sandbox, execution & lifecycle (section 8)
  - `benutzer-gruppen-rechte.md` — users, groups & permissions (section 9)
  - `gespeicherte-suchen-collections.md` — saved searches & collections (section 10)
  - `frontend.md` — frontend: architecture, i18n & module overview (section 11)
  - `frontend-arbeitsflaechen.md` — frontend working areas: grid, form, mass maintenance, completeness & dashboard (section 11)
  - `betrieb-deployment.md` — operations & deployment (section 12)
  - `versionierung-audit.md` — versioning & audit (section 13)

## Local Contracts

- Every detail document follows the same header block:
  - `# <Title>` matching the main-document section title
  - `**Hauptdokument:**` link to the corresponding main-document section
  - `**Relevante Phasen:**` development phases (1–9)
  - `**Stand:**` maturity of the document (e.g. `Entwurf`)
- Detail documents are written in German, consistent with the main document.
- The main document remains authoritative for the overall architecture; a detail document must not contradict it.

## Work Guidance

- When a main-document section outgrows a summary, move the detail into the matching file here and leave a summary plus inline link in the main document.
- Keep the header block up to date when content changes, especially `Stand`.

## Verification

## Child DOX Index

- No child AGENTS.md files are needed within `plan/detail/`.

# plan/detail — Detail Documents

## Purpose

Owns the detailed planning documents that deepen sections 4–13 of the main architecture document (`plan/meadow-pim-architektur.md`). Each file here is the single source of truth for its topic; the main document keeps a summary per section plus an inline link.

## Ownership

- Owns all files under `plan/detail/`.
- Owned documents, one per main-document section:
  - `datenmodell.md` — data model (section 4)
  - `relationen.md` — relations model: composition & aggregation, plus the usage classification (`role`) and additional categorisations (section 4)
  - `einheiten.md` — units & measurement units (section 4)
  - `sprachbaum-mehrsprachigkeit.md` — language tree & multilingual data (section 4)
  - `datenwertvererbung.md` — data value inheritance & conflict resolution (section 4)
  - `vererbung-konsolidierung.md` — consolidating inheritance (pushing identical child values up to the parent, implemented Phase 9)
  - `kategorisierung.md` — categorization & grouping, including the category dimensions (per-relation-type counters & filters) (section 4)
  - `api-design.md` — REST API design (section 5)
  - `jobs-scheduler.md` — background jobs & scheduler (section 6)
  - `integration-eventsystem.md` — integration & event system (section 7)
  - `konnektor-konfiguration.md` — schema-driven connector configuration forms (extension of section 7 & 11; stages 1–2 implemented, stage 3 sketched in §14)
  - `konfiguration-export-import.md` — configuration export & import (MCF, extension of the data export/import, section 7)
  - `datenexport-import-ui.md` — UI-driven data export & import for normal users (extension of sections 7 & 11)
  - `workflow-freigaben.md` — workflow, approvals & channel-specific releases (section 8)
  - `business-rules.md` — Business Rules: Python scripting, sandbox, execution & lifecycle (section 8)
  - `benutzer-gruppen-rechte.md` — users, groups & permissions (section 9)
  - `gespeicherte-suchen-collections.md` — saved searches & collections (section 10)
  - `suche-formular.md` — schema-driven search form (query builder) and the shared query DSL for saved searches *and* the grid (extension of sections 10 & 11; stages A–C implemented, per-field full text sketched in §12)
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

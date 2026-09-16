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
  - `kategorisierung.md` — categorization & grouping: category dimensions (per-relation-type counters & filters), taxonomy node types (`object_type.taxonomy`), class features (`category_attribute`) and the classification-standard plan (section 4)
  - `api-design.md` — REST API design (section 5)
  - `jobs-scheduler.md` — background jobs & scheduler (section 6)
  - `integration-eventsystem.md` — integration & event system (section 7)
  - `bmecat-export.md` — BMEcat export/import and external classification standards (ETIM): mapping model, data conversion, export profiles, phases (extension of sections 7 & 4; export implemented 2026-09-13: V1 §11.10, V4 prices/variants §11.11, V1b BMEcat 1.2 DTD/XSD §11.12, saved export profiles §11.13)
  - `konnektor-konfiguration.md` — schema-driven connector configuration forms (extension of section 7 & 11; stages 1–2 implemented, stage 3 sketched in §14)
  - `konfiguration-export-import.md` — configuration export & import (MCF, extension of the data export/import, section 7)
  - `datenexport-import-ui.md` — UI-driven data export & import for normal users (extension of sections 7 & 11; saved export profiles + admin page `/export-profiles` in §5.7)
  - `demodaten-generator.md` — synthetic automotive demo & load data: data sources & licensing, the demo catalog model (vehicle master data, parts, three taxonomy axes, class features, relations), the MCF/MEF import path with measured throughput, scale profiles, verification. **Stages 1–3 implemented 2026-09-15** (`scripts/demo_data.py` with `--import`/`--cleanup`, `scripts/demo_data_catalog.json`, `scripts/validate_demo.py`, `tests/test_demo_data.py`), including the shared-object pass (`masters.mef.json`), the assembly-group chunk clustering and a cleanup that lists the instance by source (finds duplicate rows and orphans) plus an extras cleanup by the `source` field of collections/saved searches; §4.3 also carries the demo-scale API latency footprint (write path incl. the **2026-09-16 sharpening** — index-backed key lookup, per-write caches, level-wise descendant walk: `POST` 203→107 ms, `PATCH` 372→100 ms —, `DELETE`, `GET /objects/stats` phases, its versioned cache, plus the batch-delete endpoint) and §9 the resulting performance/open points (2026-09-16). **Rule for large imports:** parallel chunks do *not* scale on one core + one SurrealDB (measured 2026-09-16: 4 chunks in flight → `POST /objects` 413 ms vs 107 ms idle, whole `perf` profile ≈ 15–20 h) — import large sets **sequentially** on a **release** core; `JOBS_CONCURRENCY` is not the lever, a core-side batch upsert would be (§4.3/§5/§9). §4.3 also carries the **read-path benchmark** of the frozen 16 166-object load dataset (`scripts/load_test.py`, release core: 116 req/s, mean 86 ms, p99 392 ms vs 23 req/s in the debug build) and the **single-flight** of the `stats` cache miss (ten concurrent misses: 8,2 s mean → 2,09 s, `stats` p99 11 216 ms → 69 ms). The findings from the live imports (composition references, re-parent auth, non-atomic key uniqueness — closed in the core via `src/key_lock.rs` —, missing `409` on duplicate edges, untraceable extras, delete latency, `stats` cost) are documented there (extension of section 7; tooling for Phase 8)
  - `workflow-freigaben.md` — workflow, approvals & channel-specific releases (section 8)
  - `business-rules.md` — Business Rules: Python scripting, sandbox, execution & lifecycle (section 8)
  - `benutzer-gruppen-rechte.md` — users, groups & permissions (section 9)
  - `gespeicherte-suchen-collections.md` — saved searches & collections (section 10)
  - `suche-formular.md` — schema-driven search form (query builder) and the shared query DSL for saved searches *and* the grid (extension of sections 10 & 11; stages A–C implemented, per-field full text sketched in §12)
  - `frontend.md` — frontend: architecture, i18n & module overview (section 11)
  - `dokumentation-und-hilfe.md` — in-app user handbook & help (`/help`, Markdown chapters in `docs/user/`, info-button convention, generated screenshots; first stage implemented 2026-09-15, extension points in §6)
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

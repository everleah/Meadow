# Meadow PIM — Root DOX Contract

- DOX is a highly performant AGENTS.md hierarchy installed in this repository.
- Agents must follow DOX instructions across any edits.

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## Purpose

Meadow PIM is a modern, performant Product Information Management (PIM) system for small and medium-sized enterprises (SMEs/KMUs). The high-performance core is written in Rust; flexible business logic and the user interface are written in Python. Design principles: simple, performant, and maintainable.

## Ownership

- The root AGENTS.md is the DOX rail for the whole repository.
- Root-owned files: `README.md`, `LICENSE`, `.gitignore`, `.editorconfig`, `rustfmt.toml`, `.vscode/`, `Cargo.toml`, `pyproject.toml`, `uv.lock`, `docker-compose.dev.yml`, `docker-compose.vps.yml`, `.env.vps.example`, `docker/` (production Dockerfiles), `scripts/`, and root-level project documentation.
- `plan/` is owned by `plan/AGENTS.md` (architecture and technical planning).
- `crates/` is owned by `crates/AGENTS.md` (Rust source code).
- `meadow/` is owned by `meadow/AGENTS.md` (Python source code: rules engine, later worker/frontend).
- `tests/` is owned by `tests/AGENTS.md` (automated tests).
- `docs/` is owned by `docs/AGENTS.md` (non-planning documentation).
- `resources/` is owned by `resources/AGENTS.md` (local, non-versioned vendor standard data).

## Local Contracts

- `plan/meadow-pim-architektur.md` is the authoritative architecture and technical plan. Implementation decisions must stay consistent with it, or the plan must be updated in the same change.
- Final technology stack (see plan sections 2–3):
  - Core: Rust (Edition 2024), Axum + utoipa (OpenAPI), serde/serde_json, jsonwebtoken + argon2, surrealdb client, async-nats, tokio-cron-scheduler.
  - Data & messaging: SurrealDB (documents + graph), NATS JetStream (queue + event bus), S3-compatible object storage, optional Redis cache.
  - Python: Python 3.14, NiceGUI (frontend), asyncio + nats-py + httpx (worker), Pydantic + RestrictedPython (Business Rules, Abschnitt 8).
  - Operations: Docker + Docker Compose (MVP), optional Kubernetes (k3s).
- SurrealDB server core is BSL 1.1 licensed (change date 2030-01-01 → Apache 2.0). Self-hosted production use for Meadow PIM is allowed; offering SurrealDB as a "Database Service" is not.
- jsonwebtoken v10+/v11 requires a crypto backend feature (`aws_lc_rs` or `rust_crypto`); crate MSRV is 1.88.
- **The composition parent is part of the write:** an object is created *with* its composition parent (`POST /objects` / `POST /objects/batch-upsert`, field `parent`; the import worker writes a document parents-first in breadth-first levels and re-attaches legacy edgeless objects). If the parent is neither written in the document nor present in the system, the object must **not** be created — the job reports an error. The composition edge cannot be deleted via `DELETE /references/{id}`; re-parenting goes through `PATCH /objects/{id}/parent`. Types without a composition edge: `collection`, `saved_search`.
- **A MEF export is a restore artifact, not just a value dump (2026-09-17):** the JSON document carries the objects, their **relations** (`references`, read in bulk via `POST /references/query`), and their **channel releases** (`releases`); the source kind `all` is the complete export, and `options.include_ancestors` (default on) keeps a partial source importable. A full restore is three steps in this order: **MCF** (`GET /config/export` → `POST /config/import`) for the model, **MEF** (`POST /exports` kind `all` → `POST /imports`) for the data, then the MCF section `category_attributes` (the class features of an installed standard) — those reference taxonomy nodes that travel as data, so they can only be applied after the data import. Users/groups, connectors, job definitions and system settings are environment configuration and are not part of either document. Contract details: `docs/integration/mef-and-events.md` §1.6.

## Work Guidance

- The repository contains the Rust workspace scaffold (`crates/pim-core/`) and Python packaging (`pyproject.toml` + `uv.lock`); keep all implementation work consistent with `plan/meadow-pim-architektur.md`.
- **Benutzerdokumentation:** Das Handbuch ist Markdown unter `docs/user/<sprache>/` (de/en, **gleiche Kapitel-IDs je Sprache**, Deep-Link `/help?page=<id>`) und wird von der UI **ohne Build-Schritt** unter `/help` gelesen (`meadow/frontend/help_content.py`, pro Prozess gecacht — Inhaltsänderungen brauchen im Dev einen Frontend-Neustart); das Frontend-Image kopiert `docs/user` (`docker/Dockerfile.frontend`). Die Kapitelbilder in `docs/user/assets/` sind **erzeugt**, nicht gemalt: `uv run python scripts/docs_screenshots.py` fotografiert die laufende Dev-Instanz mit Playwright (feste Viewport-Größe, Locale aus dem Browser-Kontext, Marker-Waits; **kein** Seeding — Demo-Daten müssen vorhanden sein), eingebunden als `/help-assets/<datei>.png`. Regel: Tooltip = Kurzantwort, Handbuch = Langantwort, ein Konzept nur an einer Stelle — Details in `docs/AGENTS.md`.
- When the plan changes, update `plan/meadow-pim-architektur.md` and `plan/AGENTS.md` together, and run the DOX pass.
- The architecture and planning documents are written in German; keep new planning documents in German unless otherwise directed.
- Local dev infrastructure helpers live in `scripts/`: `check.sh`/`check.ps1` (alle Verifikations-Gates, siehe Verification), `backup-db.ps1` (SurrealDB-Export nach `backups/`, Retention `-RetentionDays`), `restore-db.ps1` (`-File`), `run-dev.ps1`, `setup.ps1`, `load_test.py` (HTTP-Load-Test für die heißen Read-Pfade gegen `pim-core`: `--duration/--concurrency/--queries`, optional `--seed N --cleanup`), `docs_screenshots.py` (Handbuchbilder: fotografiert eine laufende Dev-Instanz mit Playwright, `--list`/`--only`/`--out`/`--locale`, Marker-Waits statt Sleeps), `grid_structure.py` (Strukturmaß einer Seitenfunktion für den `products_page`-Umbau: Zeilen, verschachtelte Funktionen, linearer Widget-Code; `uv run python scripts/grid_structure.py`), `inspect_etim_ixf.py` (Struktur-Erkundung eines ETIM-IXF-Exports: Sektionen/Felder/Zählungen + `_ixf_structure.txt`), `etim_ixf_to_mef.py` (ETIM-IXF → MCF/MEF für einen Klassenausschnitt: `model.mcf.json`/`classes.mef.json`/`features.mcf.json` + Report; `--classes/--group/--limit/--with-units`; die ETIM-**Feature-**Gruppen `EFG…` werden als `attribute_groups` mitgeschrieben und jedes Merkmal trägt seine `group_ids` — die Klassen-Gruppen `EG…` sind davon getrennt), `etim_export_spike.py` (ein Produkt als ETIM-BMEcat exportieren und gegen `resources/etim/bmecat_etim_501.xsd` validieren), `install_standard_bundle.py` (Standardpaket installieren: Manifest + Modell → Knoten → Klassen-Merkmale, idempotent, mit Einheiten-Vorprüfung – nur die wichtigsten fehlenden Einheiten als Warnung – und `--dry-run`; `--all` installiert alle Pakete aus `resources/standards/`, `--bulk` queued den Knoten-Import als `inbound_import`-Job **mit `match_by_key`** (echtes Upsert) und überspringt die Merkmals-Sektion bis zum zweiten Lauf; beide CLIs stellen die Konsole auf UTF-8 um, weil Einheiten-Symbole sonst unter cp1252 den Lauf abbrechen). SurrealDB-Export/-Import erfolgen im Container (Binary `/surreal`, Flags `--namespace`/`--database`) und werden per `docker cp` geholt – eine PowerShell-`>`-Umleitung kodiert UTF-16 und erzeugt einen ungültigen Import. In der verwendeten `surrealdb/surrealdb:latest`-Version ist der Healthcheck `surreal isready --conn …` ungültig; korrekt ist `/surreal is-ready` (siehe `docker-compose.dev.yml`).
- **Demo-/Lastdaten (Automotive):** `scripts/demo_data.py` erzeugt deterministisch (Seed) synthetische Katalogdaten aus der gepflegten Stammdatenliste `scripts/demo_data_catalog.json` und schreibt nach `--out-dir` (Default `.tmp/demo-data`, gitignored): `model.mcf.json` (`POST /config/import?mode=merge`, Zusatz-Kategorie-Typen über `relation_definitions`), `class-features.mcf.json` (**nach** dem Datenimport, `?sections=category_attributes`), `masters.mef.json` (nur Objekte, die in mehreren Chunks vorkommen; wird **vor** den Chunks eingespielt und abgewartet — sonst legen zwei parallele Chunks dasselbe Objekt an: `409` oder Duplikat), `data-<nn>.mef.json` (geschlossene Chunks, parallel per `POST /imports?emit_events=false`), `extras.json` (Freigaben/Collections/Suchen/Jobdefinitionen/Standard-Zuordnungen/Regel-Aktivierung) und `report.json`. Profile `tiny|demo|perf` (`--profile`, Default `demo`), `--seed`, `--chunks`, `--check`. Stufen 1–3 sind umgesetzt (Dateien + `tests/test_demo_data.py`; **`--import`** spielt Modell → geteilte Objekte → Daten-Chunks → Klassen-Merkmale → Extras ein und prüft gegen `GET /objects/stats` — die Objekt-Writes laufen seit 2026-09-16 **gebündelt** über `POST /objects/batch-upsert` (bis 500 je Transaktion, Worker-Chargen von 200; Spitzen ~73 Writes/s statt 11–25 obj/s; Details in `plan/detail/demodaten-generator.md` §4.3/§4.4 Punkt 10), **`--cleanup`** entfernt den Bestand wieder (Objekte über ein Quellen-Listing pro Typ, **gebündelt** über `POST /objects/batch-delete` mit 100 Ids je Aufruf: der komplette `demo`-Bestand in ~0,9 min statt ~20–27 min; danach die generierten Collections und Suchen über ihr `source`-Feld `?source=meadow-demo`; Collections **vor** ihren Filter-Suchen, weil der Kern eine referenzierte Suche mit `409` schützt), beides idempotent: gleicher Seed ⇒ zweiter Lauf `updated` statt `created`). Abnahme: **`scripts/validate_demo.py`** (rein lesend, Exit ≠ 0 bei einem Fehlschlag) prüft `report.json` gegen `GET /objects/stats`, doppelte Business-Keys (über die `key_attributes` der Typen), Kanalfreigaben, Regeln, Collections samt Mitgliedern, gespeicherte Suchen (und dass alle Collections/Suchen über `?source=` auffindbar sind — sonst würde das Cleanup sie nie löschen), den Kompositionspfad, die Klassen-Merkmale und exportiert eine Collection als BMEcat — mit Validierung gegen `resources/etim/bmecat_etim_501.xsd`, sofern vorhanden (`--skip-export` lässt den Export weg). Details und die dabei gefundenen Lücken im Import-Pfad: **`plan/detail/demodaten-generator.md`**.
- **Deslop-Scan (AI-Code-Qualität, drei Reparaturpasses umgesetzt 2026-09-18):** `scripts/deslop.ps1`/`deslop.sh` scannen Quelltext, Kommentare, Strings und Dokumentation mit dem gepinnten `stopslop` 0.5.1 (`all|changed|staged`, `--fix-plan` = zusätzlich `ai-slop-detector` über uv; Berichte gitignoriert unter `.tmp/deslop/`). Konfiguration `stopslop.toml` (`SLOP018` Gedankenstrich aus, Stilgruppen nur für Markdown aus, Artefakt-/Strukturregeln an, **keine Baseline** – der Scan ist scharf), Fixtures `tests/fixtures/deslop/` (für Ruff und den Repo-Scan ausgeschlossen) und `tests/test_deslop.py`. Stand: **null Befunde**; die Passes machten sechs verschluckte Frontend-Fehler sichtbar, entfernten einen Weiterschleifer, lösten die Platzhalterwerte auf und entfernten acht Typ-Ausweichungen. `--check-imports` (`SLOP010`) bleibt bewusst **aus** (gemessen 271 Befunde, davon 256 für unser eigenes Paket `meadow`; Begründung §4.3). Der Scan **ersetzt keine Gates** und repariert nichts automatisch; die README des Werkzeugs beschreibt die Hauptentwicklung, geprüft wird immer die installierte Version. Details: `plan/detail/ai-code-quality.md`.
- Production deployment (VPS, `plan/detail/betrieb-deployment.md` §3–8): `docker-compose.vps.yml` (Services `pim-core`/`pim-frontend`/`pim-worker`/`surrealdb`/`nats`/`object-storage`, optionale `redis`; Traefik-Labels nur für Core/Frontend; interne Netze `meadow-backend` + external `traefik-net`), `.env.vps.example` (Kopie → `.env.vps`, nie in Git), Dockerfiles unter `docker/` (`Dockerfile.core`/`.frontend`/`.worker`), und VPS-Bash-Skripte in `scripts/`: `build-images.sh` (Build/Push der Images), `deploy-vps-meadow.sh` (Registry-Login, Compose-Validierung, Netz-Check, Pull, `up -d --remove-orphans`), `backup-vps-db.sh`/`cleanup-backups.sh`/`restore-vps-db.sh` (SurrealDB-Backup/Restore mit korrekter CLI), `healthcheck-vps.sh`, `collect-vps-diagnostics.sh`, `tail-vps-errors.sh`, `bootstrap-vps-admin.sh`. `backups/`, `.env.vps`, `diagnostics-*/` sind gitignored. Hinweis: Der Kern sät derzeit `admin`/`admin` (MVP); der Passwort-Änderungs-Flow ist implementiert (`POST /auth/password`, Schlüssel-Button im UI-Kopf, Hinweis-Banner solange das Passwort nie geändert wurde) — die Seed-Zugangsdaten sollten nach dem ersten Deployment geändert werden.

## Coding Guidelines

- All source code is written in English: identifiers (variables, functions, methods, classes, modules, types), comments, and documentation.
- Code documentation is written in English, including Python docstrings and Rust doc comments (`///`, `//!`).
- Choose self-documenting names for variables, functions, methods, and classes so code reads clearly without requiring excessive comments.

## Verification

- All gates in one run: `bash scripts/check.sh` (Linux/CI/VPS) or `powershell -File scripts/check.ps1` (Windows); `rust` / `python` selects a subset. The core must not be running — on Windows a running `pim-core.exe` locks the binary while relinking.
- **Der Deslop-Scan ist bewusst kein Gate (2026-09-18):** `scripts/deslop.ps1`/`deslop.sh` laufen manuell, nicht in `check.*` und nicht in CI — ein Befund ist ein Qualitätssignal, kein Korrektheitsbeweis. Nur Artefaktbefunde der Stufe A lassen den Lauf scheitern; der Bestand ist über `.stopslop-baseline.json` grün. Details: `plan/detail/ai-code-quality.md`.
- **`mypy` ist ein Gate über den gesamten Python-Baum (2026-09-18):** `[tool.mypy]` in `pyproject.toml` prüft 131 Dateien (`meadow/` vollständig inkl. Frontend, `tests/`, `scripts/`) mit `check_untyped_defs = true` und läuft in `scripts/check.*`; nur `tests/fixtures/` ist über `exclude` ausgenommen (der absichtlich kaputte Deslop-Sample). `follow_imports = "silent"` war die Voraussetzung, dass der Scope modulweise wachsen konnte: nur die in `[tool.mypy] files` gelisteten Dateien werden gemeldet, ein noch nicht aufgenommenes Modul bleibt ungeprüft, statt das Gate zu blockieren. `warn_unused_ignores` macht überflüssige `# type: ignore` sichtbar — der Anlass war genau das: 33 Ausweichungen ohne Prüfer, heute sind es 11 (alle notwendig). Achtung: mypy und Pylance widersprechen sich stellenweise (z. B. `lambda n=hit:` — mypy rät, Pylance braucht das Ignore; beim Entfernen beide ansehen, Pylance fand dabei einen echten `await`-Fehler). NiceGUIs Element-Typing wird **geprüft**: Element-Variablen konkret typisieren (`ui.input`, `ui.checkbox`), `ui.element` ist nur der Rahmen; die wiederkehrenden Muster und die zwei Lehren („dieselbe Meldung 86-mal ⇒ Signatur prüfen", „ein Test-Double macht die schmale Schnittstelle sichtbar") stehen in `plan/detail/ai-code-quality.md` §4.7–§4.9.
- **Die Gates bauen kein Binary (2026-09-17):** `check.ps1`/`check.sh` prüfen nur (fmt, clippy, `cargo test`, ruff, pytest) — `target/debug/pim-core.exe` bleibt also **alt**, bis jemand `cargo build -p pim-core` (oder `cargo run`) aufruft. Wer den Kern nach einer Rust-Änderung neu startet, ohne vorher zu bauen, bedient die Oberfläche still mit einem **veralteten** Kern (genau das passierte hier: ein Debug-Binary vom Vortag ignorierte das `parent`-Feld, während der Release-Build es schon konnte). Vor jedem Neustart nach Kern-Änderungen also **bauen, dann starten**.
- Rust: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test` (workspace root).
- Python: `ruff check`, `ruff format --check`, `mypy`, `pytest` (dev dependencies via uv); die Ruff-Gates prüfen `meadow`, `tests` und `scripts`, der mypy-Scope steht in `[tool.mypy] files` (`pyproject.toml`).
- **Seitentests (2026-09-18):** `tests/test_products_page.py` bootet die **echte** Frontend-App über NiceGUIs User-Simulation (Plugin im Root-`conftest.py`, NiceGUI-Storage im Temp-Verzeichnis) und prüft Seitenverhalten statt Helfer – das Netz für den `products_page`-Umbau; Regeln in `tests/AGENTS.md`, Details in `plan/detail/ai-code-quality.md` §4.11.
- CI: **Forgejo Actions** on `registry.example.com` (`.forgejo/workflows/ci.yml`) runs the same gates on push to `main` and on pull requests (the job installs the Rust toolchain and uv/Python 3.14). Requires a registered Forgejo runner and a resolvable `actions/checkout@v4` (GitHub-compatible actions or a mirror). **Status:** no runner is registered yet (deliberately deferred), so the pipeline does not run — verify locally with the scripts above until then.
- The translation files must carry the same key set and no empty values — enforced by `tests/test_frontend.py::test_translation_files_share_the_same_keys` (part of the pytest gate).

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

- **Python-Paketmanager:** `uv` (statt pip). Projekt-venv unter `.venv/`; Abhängigkeiten in `pyproject.toml` pflegen (`uv add`/`uv sync`, Lockfile `uv.lock`).
- **Test-Ablage:** Neue Tests möglichst im `tests/`-Verzeichnis anlegen, um das Wurzelverzeichnis übersichtlich zu halten.
- **Dokumentations-Ablage:** Dokumentation, die nicht zur Planung gehört, (idealerweise hierarchisch) im `docs/`-Verzeichnis anlegen.
- **Keine Fallbacks für Versionsversatz (bis es eine produktive VPS-Umgebung gibt):** Kern und Frontend/Worker laufen immer zusammen, also gibt es keine Kompatibilitätspfade für ältere Kern-Versionen (z. B. „neuer Endpunkt, sonst seitenweise laden", doppelte Payload-Formate, Feature-Detection). Ein neuer Vertrag wird direkt genutzt; fehlt er, ist das ein Fehler und wird sichtbar gemeldet. Das betrifft **nicht** das Degradieren bei fehlenden Daten/Infrastruktur (leere Liste statt Absturz, `RULES_DEGRADE`), und nicht die bewusst doppelt geführten Konventionen (MEF/MCF-Importe mehrerer Versionen).
- **Keine Fremdprodukt-Namen in der Oberfläche (2026-09-17):** UI-Texte — Übersetzungen (`meadow/frontend/locales/*.json`), Handbuchkapitel (`docs/user/`), Tooltips, Hinweise, Meldungen — beschreiben die Funktion in eigenen Worten und nennen **keine** anderen PIM-/Shop-Systeme; die Nennung von Mitbewerbern in Kundentexten ist rechtlich riskant. Marktvergleiche stehen nur in internen Plan-/Vertragsdokumenten (`plan/`, `*/AGENTS.md`). Details: `meadow/AGENTS.md` (Work Guidance).

## Child DOX Index

- `plan/` — architecture & technical planning; owned by `plan/AGENTS.md`.
- `crates/` — Rust source code; owned by `crates/AGENTS.md`.
- `meadow/` — Python source code; owned by `meadow/AGENTS.md`.
- `tests/` — automated tests; owned by `tests/AGENTS.md`.
- `docs/` — non-planning documentation; owned by `docs/AGENTS.md`.
- `resources/` — local vendor standard data (BMEcat/ETIM artefacts), gitignored; owned by `resources/AGENTS.md`.

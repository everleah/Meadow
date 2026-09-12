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

## Local Contracts

- `plan/meadow-pim-architektur.md` is the authoritative architecture and technical plan. Implementation decisions must stay consistent with it, or the plan must be updated in the same change.
- Final technology stack (see plan sections 2–3):
  - Core: Rust (Edition 2024), Axum + utoipa (OpenAPI), serde/serde_json, jsonwebtoken + argon2, surrealdb client, async-nats, tokio-cron-scheduler.
  - Data & messaging: SurrealDB (documents + graph), NATS JetStream (queue + event bus), S3-compatible object storage, optional Redis cache.
  - Python: Python 3.14, NiceGUI (frontend), asyncio + nats-py + httpx (worker), Pydantic + RestrictedPython (Business Rules, Abschnitt 8).
  - Operations: Docker + Docker Compose (MVP), optional Kubernetes (k3s).
- SurrealDB server core is BSL 1.1 licensed (change date 2030-01-01 → Apache 2.0). Self-hosted production use for Meadow PIM is allowed; offering SurrealDB as a "Database Service" is not.
- jsonwebtoken v10+/v11 requires a crypto backend feature (`aws_lc_rs` or `rust_crypto`); crate MSRV is 1.88.

## Work Guidance

- The repository contains the Rust workspace scaffold (`crates/pim-core/`) and Python packaging (`pyproject.toml` + `uv.lock`); keep all implementation work consistent with `plan/meadow-pim-architektur.md`.
- When the plan changes, update `plan/meadow-pim-architektur.md` and `plan/AGENTS.md` together, and run the DOX pass.
- The architecture and planning documents are written in German; keep new planning documents in German unless otherwise directed.
- Local dev infrastructure helpers live in `scripts/`: `check.sh`/`check.ps1` (alle Verifikations-Gates, siehe Verification), `backup-db.ps1` (SurrealDB-Export nach `backups/`, Retention `-RetentionDays`), `restore-db.ps1` (`-File`), `run-dev.ps1`, `setup.ps1`, `load_test.py` (HTTP-Load-Test für die heißen Read-Pfade gegen `pim-core`: `--duration/--concurrency/--queries`, optional `--seed N --cleanup`). SurrealDB-Export/-Import erfolgen im Container (Binary `/surreal`, Flags `--namespace`/`--database`) und werden per `docker cp` geholt – eine PowerShell-`>`-Umleitung kodiert UTF-16 und erzeugt einen ungültigen Import. In der verwendeten `surrealdb/surrealdb:latest`-Version ist der Healthcheck `surreal isready --conn …` ungültig; korrekt ist `/surreal is-ready` (siehe `docker-compose.dev.yml`).
- Production deployment (VPS, `plan/detail/betrieb-deployment.md` §3–8): `docker-compose.vps.yml` (Services `pim-core`/`pim-frontend`/`pim-worker`/`surrealdb`/`nats`/`object-storage`, optionale `redis`; Traefik-Labels nur für Core/Frontend; interne Netze `meadow-backend` + external `traefik-net`), `.env.vps.example` (Kopie → `.env.vps`, nie in Git), Dockerfiles unter `docker/` (`Dockerfile.core`/`.frontend`/`.worker`), und VPS-Bash-Skripte in `scripts/`: `build-images.sh` (Build/Push der Images), `deploy-vps-meadow.sh` (Registry-Login, Compose-Validierung, Netz-Check, Pull, `up -d --remove-orphans`), `backup-vps-db.sh`/`cleanup-backups.sh`/`restore-vps-db.sh` (SurrealDB-Backup/Restore mit korrekter CLI), `healthcheck-vps.sh`, `collect-vps-diagnostics.sh`, `tail-vps-errors.sh`, `bootstrap-vps-admin.sh`. `backups/`, `.env.vps`, `diagnostics-*/` sind gitignored. Hinweis: Der Kern sät derzeit `admin`/`admin` (MVP); der Passwort-Änderungs-Flow ist implementiert (`POST /auth/password`, Schlüssel-Button im UI-Kopf, Hinweis-Banner solange das Passwort nie geändert wurde) — die Seed-Zugangsdaten sollten nach dem ersten Deployment geändert werden.

## Coding Guidelines

- All source code is written in English: identifiers (variables, functions, methods, classes, modules, types), comments, and documentation.
- Code documentation is written in English, including Python docstrings and Rust doc comments (`///`, `//!`).
- Choose self-documenting names for variables, functions, methods, and classes so code reads clearly without requiring excessive comments.

## Verification

- All gates in one run: `bash scripts/check.sh` (Linux/CI/VPS) or `powershell -File scripts/check.ps1` (Windows); `rust` / `python` selects a subset. The core must not be running — on Windows a running `pim-core.exe` locks the binary while relinking.
- Rust: `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test` (workspace root).
- Python: `ruff check`, `ruff format --check`, `pytest` (dev dependencies via uv).
- CI: **Forgejo Actions** on `registry.example.com` (`.forgejo/workflows/ci.yml`) runs the same gates on push to `main` and on pull requests (the job installs the Rust toolchain and uv/Python 3.14). Requires a registered Forgejo runner and a resolvable `actions/checkout@v4` (GitHub-compatible actions or a mirror).
- The translation files must carry the same key set and no empty values — enforced by `tests/test_frontend.py::test_translation_files_share_the_same_keys` (part of the pytest gate).

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

- **Python-Paketmanager:** `uv` (statt pip). Projekt-venv unter `.venv/`; Abhängigkeiten in `pyproject.toml` pflegen (`uv add`/`uv sync`, Lockfile `uv.lock`).
- **Test-Ablage:** Neue Tests möglichst im `tests/`-Verzeichnis anlegen, um das Wurzelverzeichnis übersichtlich zu halten.
- **Dokumentations-Ablage:** Dokumentation, die nicht zur Planung gehört, (idealerweise hierarchisch) im `docs/`-Verzeichnis anlegen.

## Child DOX Index

- `plan/` — architecture & technical planning; owned by `plan/AGENTS.md`.
- `crates/` — Rust source code; owned by `crates/AGENTS.md`.
- `meadow/` — Python source code; owned by `meadow/AGENTS.md`.
- `tests/` — automated tests; owned by `tests/AGENTS.md`.
- `docs/` — non-planning documentation; owned by `docs/AGENTS.md`.

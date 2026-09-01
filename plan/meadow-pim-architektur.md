# Meadow PIM – Architektur & Technischer Plan

## 1. Einleitung

Dieses Dokument beschreibt die vereinbarte Architektur und den technischen Plan für ein modernes, performantes Product-Information-Management-System (PIM) für mittelständische Unternehmen (KMUs). Die Entwicklung folgt der Prämisse **einfach, performant und wartbar** zu sein. Der Kern ist in Rust geschrieben, flexible Geschäftslogik und die Benutzeroberfläche in Python.

Der Projektname **Meadow PIM** steht für den Beginn auf der grünen Wiese: ein System, das Ordnung in wachsende Datenbestände bringt, flexible Strukturen ermöglicht und Informationen organisch miteinander vernetzt.

---

## 2. Gesamtarchitektur

Das System besteht aus mehreren Diensten, die lose gekoppelt über REST und eine Message Queue kommunizieren. Die folgende Darstellung zeigt die Komponenten und ihre Verbindungen.

```text
                        ┌─────────────────────────────────────────────┐
                        │            Web-Browser (Client)             │
                        └──────────────────────┬──────────────────────┘
                                               │ HTTPS / WebSocket
                        ┌──────────────────────▼──────────────────────┐
                        │        Frontend-Service (Python + NiceGUI)  │
                        │  – UI-Rendering, Benutzerinteraktion        │
                        │  – Session-Management, Login, Rollen        │
                        │  – Aufruf der Rust-Kern-API (REST)          │
                        └──────────────────────┬──────────────────────┘
                                               │ REST (OpenAPI, JWT)
                        ┌──────────────────────▼──────────────────────┐
                        │          PIM Core Service (Rust, headless)  │
                        │  – Objektverwaltung, Suche, Referenzen      │
                        │  – Sprachbaum & Datenwertvererbung          │
                        │  – Scheduler (tokio-cron-scheduler)         │
                        │  – Job-Verwaltung (job_definition, job_run) │
                        │  – Eventsystem (publiziert CRUD-Events)     │
                        │  – Authentifizierung & Autorisierung (JWT)  │
                        │  – REST-API (Axum, OpenAPI)                 │
                        └───┬──────────────┬──────────────┬───────────┘
                            │              │              │
                            ▼              ▼              ▼
                 ┌──────────────┐ ┌───────────────┐ ┌───────────────┐
                 │   SurrealDB  │ │ NATS JetStream│ │ Objektstorage │
                 │ (Dokumente + │ │ (Message Queue│ │(S3-kompatibel)│
                 │   Graph)     │ │  + Event-Bus) │ │               │
                 └──────────────┘ └────────┬──────┘ └───────────────┘
                                           │
                                           │ Subscribe (Jobs, Events)
                                           │
                        ┌──────────────────▼──────────────────────────┐
                        │           Python Worker Service             │
                        │  – Abonniert Job- und Event-Streams (NATS)  │
                        │  – Führt Business Rules aus                 │
                        │  – Inbound-/Outbound-Konnektoren            │
                        │  – Webhook-Outbound zur Middleware          │
                        │  – Status-Updates an Core (REST)            │
                        └───────┬──────────────────────┬──────────────┘
                                │                      │
                                │ HTTP (Webhook)       │ REST (Datenabruf/-update)
                                ▼                      ▼
                     ┌────────────────────┐   ┌──────────────────────┐
                     │ Externe Middleware │   │   PIM Core (REST)    │
                     │ (bestehendes System│   │   (für Datenzugriff) │
                     │   des Anwenders)   │   └──────────────────────┘
                     └────────────────────┘
```

**Erläuterungen zu den Komponenten:**

- **Rust-Kern (PIM Core):** Headless REST-API, einzige Instanz mit direktem Schreibzugriff auf die Datenbank. Erzeugt bei CRUD-Operationen Events und publiziert sie in NATS JetStream.
- **Frontend-Service (Python + NiceGUI):** Einziger Dienst, der direkt vom Benutzer angesprochen wird. Kommuniziert ausschließlich per REST mit dem Kern. Keine direkte NATS-Anbindung (Updates über Polling oder später über Server-Sent Events).
- **Python Worker Service:** Verarbeitet asynchrone Aufgaben aus NATS (Jobs und Events). Führt Business Rules aus, importiert/exportiert Daten und sendet gefilterte Webhooks an die externe Middleware. Greift für Datenabrufe und -änderungen auf die REST-API des Kerns zu.
- **NATS JetStream:** Dient als Message Queue für Jobs und als Event-Bus für CRUD-Events. Persistente Streams sorgen für zuverlässige Zustellung.
- **SurrealDB:** Multi-Model-Datenbank (Dokumente + Graph). Kann als separater Dienst oder embedded im Kern betrieben werden.
- **Objektstorage:** Ablage für Binärdaten (Bilder, Exportdateien). Wird vom Kern und vom Worker verwendet.
- **Externe Middleware:** Vom Anwender betriebenes System, das über Webhooks Änderungen in Echtzeit erhält und für komplexe Transformationsaufgaben zuständig ist.

**Wichtige Kommunikationswege:**

1. **Frontend → Kern:** REST/OpenAPI mit JWT für alle Lese- und Schreiboperationen.
2. **Kern → NATS:** Publiziert Job-Messages und CRUD-Events.
3. **Worker → NATS:** Abonniert Job- und Event-Streams (WorkQueue).
4. **Worker → Kern:** REST für Datenabrufe (z. B. Objekte lesen) und Status-Updates (z. B. Job-Run erfolgreich/fehlgeschlagen).
5. **Worker → Objektstorage:** Speichert Exportdateien, liest Importdateien.
6. **Worker → Middleware:** HTTP-Webhook mit konfigurierbaren Filtern und Retry-Logik.

**Abgrenzung zur Pflege- und Kategorienstruktur:**

Die Architektur bildet die **technische Infrastruktur** ab. Fachliche Strukturen wie Komposition/Aggregation (Abschnitt 4), Sprachbaum, Datenwertvererbung und Kategorisierung sind **logische Modelle innerhalb des Kerns** und erfordern keine zusätzlichen Dienste. Sie werden in Abschnitt 4 und den zugehörigen Detaildokumenten beschrieben.

---

## 3. Technologie-Stack (final, erweitert)

### 3.1 Kerntechnologien

| Komponente                | Technologie                                     | Zweck                                                              |
|---------------------------|-------------------------------------------------|--------------------------------------------------------------------|
| Kernsprache               | Rust (Edition 2024)                             | Hochperformante API, Scheduler, Datenzugriff, Eventsystem          |
| Web-Framework             | Axum                                            | REST-API mit OpenAPI-Unterstützung                                 |
| OpenAPI-Dokumentation     | utoipa                                          | Generierung der OpenAPI-Spezifikation aus dem Rust-Code            |
| JSON-Serialisierung       | serde + serde_json                              | Strukturierte Verarbeitung aller JSON-Daten (Objekte, Events, MEF) |
| Datenbank                 | SurrealDB                                       | Dokumente + Graph, flexible Metadaten, Suche, Vererbung            |
| Datenbank-Treiber         | surrealdb (offizieller Rust-Client)             | Kommunikation zwischen Rust-Kern und SurrealDB                     |
| Message Queue / Event Bus | NATS JetStream                                  | Zuverlässige Hintergrundverarbeitung (Jobs) und CRUD-Events        |
| NATS-Client (Rust)        | async-nats                                      | Asynchrone Kommunikation des Rust-Kerns mit NATS                   |
| NATS-Client (Python)      | nats-py                                         | Asynchrone Kommunikation des Python Workers mit NATS               |
| Objektstorage             | S3-kompatibel (z. B. MinIO, AWS S3)             | Ablage von Binärdaten (Bilder, Exportdateien)                      |
| Authentifizierung         | JWT (jsonwebtoken) + argon2 (Passwort-Hashing)  | API-Zugriff, Benutzerrollen, sichere Passwortspeicherung           |
| Caching (optional)        | Redis                                           | Performance-Steigerung bei Vererbungs-/Sprachauflösung und Suche   |

### 3.2 Python-Dienste (Business Logic, Worker, Frontend)

| Komponente                  | Technologie                          | Zweck                                                                 |
|-----------------------------|--------------------------------------|-----------------------------------------------------------------------|
| Python-Basis                | Python 3.14                          | Gemeinsame Laufzeit für Worker und Frontend                           |
| Paketmanager                | uv                                   | Verwaltung der Python-Abhängigkeiten (venv, Lockfile)                 |
| Business Logic              | Pydantic (Validierung), ggf. FastAPI | Business Rules, berechnete Attribute, Validierung von MEF-Dokumenten  |
| Rule-Engine / Sandbox       | RestrictedPython                     | Sichere Ausführung von Business-Rule-Skripten (Python)                |
| Worker                      | asyncio, nats-py, httpx              | Ausführung von Jobs (Import/Export), Webhooks, Status-Updates         |
| Frontend                    | NiceGUI                              | Rollenbasierte Web-Oberfläche                                         |
| Frontend i18n               | gettext + Babel                      | UI-Übersetzungen, Datums-/Zahlen-/Währungsformatierung                |
| HTTP-Client (Worker)        | httpx                                | Aufrufe an den Rust-Kern und an die externe Middleware                |
| PDF/Excel-Export (optional) | ReportLab / openpyxl                 | Spätere Outbound-Konnektoren für PDF, Excel usw.                      |

### 3.3 Infrastruktur & Betrieb

| Komponente                | Technologie                                | Zweck                                                |
|---------------------------|--------------------------------------------|------------------------------------------------------|
| Containerisierung         | Docker                                     | Einheitliche Auslieferung aller Dienste              |
| Orchestrierung (MVP)      | Docker Compose                             | Lokaler Betrieb und KMU-Deployment                   |
| Orchestrierung (optional) | Kubernetes (k3s)                           | Skalierung bei wachsenden Anforderungen              |
| Observability             | tracing (Rust), structlog (Python)         | Strukturiertes Logging (JSON) auf stdout             |
| Metriken (optional)       | Prometheus + Grafana                       | Überwachung von Jobs, Events, API-Performance        |
| Logging (UI-Sicht)        | SurrealDB (`log_entry` + `system_setting`) | Kuratierte System-Logs, in der Admin-UI einsehbar    |
| Logging (optional, später)| Loki + Grafana                             | Zentrale Log-Aggregation/Volltextsuche (Phase 9)     |
| Backup                    | SurrealDB-Export + S3-Sync                 | Datensicherung für Objektdatenbank und Objektstorage |

### 3.4 Abgrenzung & Hinweise

- **FastAPI** ist nur dann erforderlich, wenn der Python-Dienst eigene REST-Endpunkte bereitstellen muss. Für die erste Version kommuniziert der Worker ausschließlich über NATS (inkl. Rule-Evaluation per Request/Reply) und ruft den Rust-Kern per REST auf; FastAPI kann daher optional bleiben. Ein minimaler NATS-Server ist bereits in Phase 4 für die Rule-Evaluation erforderlich; die JetStream-Streams/Jobs folgen in Phase 5.
- **Redis** ist im MVP nicht zwingend, da die Vererbungs- und Sprachauflösung bei geringen Hierarchietiefen auch ohne Cache performant ist. Es wird als optionale Komponente geführt.
- **Meilisearch/Elasticsearch** sind nicht vorgesehen, da SurrealDB über integrierte Volltextsuche verfügt. Erst bei sehr komplexen Suchanforderungen (z. B. Facetten, Synonyme) könnte eine externe Suchmaschine ergänzt werden.
- **ETIM/Klassifizierungsstandards** benötigen keine eigene Technologie, sondern werden später als Datenmodell-Erweiterung im Kern abgebildet.
- **SurrealDB-Lizenz (BSL 1.1):** Der SurrealDB-Serverkern (v3.x) ist unter der **Business Source License 1.1** lizenziert – „source-available“, kein OSI-Open-Source; SDKs/Clients sind dagegen unter Apache/MIT lizenziert. Die BSL erlaubt:
  - **Entwicklung, Tests, Demo & Evaluierung kostenlos** (ausdrücklich erlaubt),
  - **Produktivbetrieb kostenlos**, solange SurrealDB **nicht als „Database Service“** angeboten wird (d. h. Dritte dürfen über das Angebot keine Schemas/Tabellen erstellen oder verwalten),
  - **Change Date 2030-01-01** → danach vollständig unter **Apache 2.0** (Open Source).
  - Für Meadow PIM (selbst gehostet bei Kunden, normale Anwendungsnutzung) ist die erlaubte Produktionsnutzung damit abgedeckt. Erst bei einem Angebot mit direktem Datenbank-Zugriff für Dritte (DB-as-a-Service) wäre eine kommerzielle Lizenz erforderlich.
- **jsonwebtoken (v10+/v11):** Bei der Dependency-Einrichtung muss eines der Crypto-Backend-Features gewählt werden (`aws_lc_rs` oder `rust_crypto`), sonst schlägt der Build fehl. Zudem liegt die MSRV des Crates bei 1.88.

### 3.5 Lokale Entwicklungsumgebung

Für die lokale Entwicklung wird folgende Toolchain vorausgesetzt:

- **Python:** Python 3.14 mit Projekt-venv (`.venv/`), verwaltet mit **uv** (Python-Paketmanager in Rust).
- **Rust:** Toolchain über **rustup** (stable), Edition 2024; effektive MSRV ≥ 1.90 (`surrealdb` → `roaring`; `jsonwebtoken` selbst ≥ 1.88). Stand beim Aufsetzen: rustc 1.97.1.
- **Windows-Linker:** MSVC Build Tools (Visual Studio Community mit Workload „Desktopentwicklung mit C++“) als Linker für `rustc`.
- **Python-Abhängigkeiten (MVP):** `nicegui`, `pydantic`, `nats-py`, `httpx`, `structlog`, `babel` – Installation in das venv mit `uv pip install --python .venv\Scripts\python.exe nicegui pydantic nats-py httpx structlog babel`.

### 3.6 Repository-Struktur (Ausgangslage)

- `Cargo.toml` (Workspace) + `crates/pim-core/` — Rust-Kern; weitere Rust-Crates unter `crates/`.
- `pyproject.toml` + `uv.lock` — gemeinsame Python-Abhängigkeiten (Worker + Frontend) mit uv; Dienste später unter `services/`.
- `docker-compose.dev.yml` — lokale Infrastruktur (SurrealDB, NATS JetStream, MinIO, optional Redis).
- `scripts/` — Hilfsskripte (`setup.ps1` Dev-Setup, `run-dev.ps1` lokale Infrastruktur).
- `.vscode/`, `.editorconfig`, `rustfmt.toml` — Editor- und Formatierungskonfiguration.

---

## 4. Datenmodell

Alle fachlichen Entitäten (Produkte, Kategorien, Assets, gespeicherte Suchen, Collections, …) werden als **generische Objekte** mit flexiblen Metadaten gespeichert; Unterschiede entstehen durch den Typ. Technische Tabellen (Benutzer, Gruppen, Attributdefinitionen, Sprachbaum, Business Rules, Jobs, Audit-Log) ergänzen das Kernmodell. Details zu den Tabellen sowie zu Indizierung, Volltextsuche und Graph-Traversierung stehen im Detaildokument.

> **Details:**
>
> - [datenmodell.md](detail/datenmodell.md) — Tabellen, Indizes & Suche
> - [relationen.md](detail/relationen.md) — Relationenmodell: Komposition & Aggregation
> - [einheiten.md](detail/einheiten.md) — Einheiten & Umrechnung für quantifizierte Werte
> - [sprachbaum-mehrsprachigkeit.md](detail/sprachbaum-mehrsprachigkeit.md) — Sprachbaum für sprachabhängige Daten
> - [datenwertvererbung.md](detail/datenwertvererbung.md) — Vererbung & Konfliktlösung
> - [kategorisierung.md](detail/kategorisierung.md) — Kategorisierung und Gruppierung

**Datumswerte-Konvention (verbindlich):**

1. **Speicherung:** Alle Datums- und Zeitwerte werden **immer als UTC** in SurrealDB gespeichert (`created_at`, `updated_at`, `started_at`, `finished_at`, Audit-`timestamp`, …). Lokale Zeitzonen sind keine Sache der Speicherung.
2. **Austausch:** REST-API, Inbound und Outbound verwenden **immer ISO-8601-Strings** mit Zeitzonenangabe (z. B. `2026-08-14T10:00:00Z` bzw. `…+02:00`); Werte mit Offset werden beim Empfang nach UTC normalisiert.
3. **Darstellung:** Die GUI zeigt Datums-/Zeitwerte **lokalisiert** (Zeitzone + Format des Benutzers) an; der gespeicherte Wert bleibt unverändert UTC.

> **Details:** [datenmodell.md](detail/datenmodell.md#datumswerte) — Datumswerte-Konvention (Speicherung & Austausch); [frontend.md](detail/frontend.md) — lokalisierte Darstellung.

**Einheiten-Konvention (verbindlich):**

1. **Speicherung:** Werte mit Einheit werden **in der kanonischen SI-Einheit** der jeweiligen Dimension gespeichert (z. B. Länge in `m`, Spannung in `V`, Leistung in `W`, Temperatur in `K`). Gespeichert wird die reine Zahl; die Einheit ergibt sich aus der Attributdefinition (`unit_id`).
2. **Austausch:** REST-API, MEF und Events übertragen quantifizierte Werte **immer mit Einheit** als `{"value": …, "unit": …}`; beim Empfang wird in die kanonische SI-Einheit umgerechnet, beim Senden in die angeforderte bzw. Standard-Einheit.
3. **Darstellung:** Die GUI zeigt den Wert in der gewählten Einheit an (Umrechnung on-the-fly); Eingaben werden vor dem Speichern in die kanonische SI-Einheit umgerechnet. Alle SI-Einheiten, übliche Darstellungen und anglo-amerikanische Einheiten (imperial/US customary) werden per Default mitgeliefert; eigene Einheiten (Zeichen + Umrechnung) sind auf Basis einer SI-Einheit definierbar.

> **Details:** [einheiten.md](detail/einheiten.md) — Einheiten-Katalog, Umrechnung & eigene Einheiten; [datenmodell.md](detail/datenmodell.md) — Tabellen; [api-design.md](detail/api-design.md) — API; [frontend.md](detail/frontend.md) — Darstellung; [integration-eventsystem.md](detail/integration-eventsystem.md) — MEF.

**Relationen-Konvention (verbindlich):**

1. **Komposition vs. Aggregation:** Relationen werden in `relation_definition` als `composition` (1:n inkl. 1:1) oder `aggregation` (n:m) definiert. Es gibt **genau einen** Kompositionstyp `depends_on`.
2. **Ein-Eltern-Invariante:** Jedes Objekt hat **genau eine** `depends_on`-Kante („ich bin abhängig von“). Die Kompositionen bilden einen strikten, zyklenfreien Baum mit dem **Universal-Root** als Wurzel; Top-Level-Objekte hängen direkt am Root. Vererbung und Löschkaskade laufen ausschließlich über diese Kette.
3. **Aggregationen:** `category_member`, `collection_member`, `related` und benutzerdefinierte Typen sind n:m-Verknüpfungen ohne Vererbungswirkung und ohne Kaskade.
4. **Universal-Root:** Reales Objekt (`type: "root"`, feste ID), sichtbar, strukturell unveränderlich (nicht löschbar, nicht umhängbar); seine Attribute (globale Defaults) sind durch Admin/Superadmin editierbar.
5. **Typrestriktionen:** Eine Relation kann optional nur bestimmte Quell- und Ziel-Objekttypen verbinden (`source_types`/`target_types`; leer = alle); die Prüfung erfolgt beim Anlegen jeder Relation.
6. **Löschen & Umhängen:** Soft Delete kaskadiert über den Teilbaum (UI-Warnung mit Anzahl, API ohne Bestätigung); `depends_on` ist umhängbar (Re-Parenting) mit Zyklenschutz, Typrestriktionsprüfung und Audit-Eintrag.

> **Details:** [relationen.md](detail/relationen.md) — Komposition, Aggregation, Universal-Root & Relationstypen; [datenwertvererbung.md](detail/datenwertvererbung.md) — Vererbung; [datenmodell.md](detail/datenmodell.md) — Tabellen; [api-design.md](detail/api-design.md) — API.

**Business Rules & berechnete Attribute (verbindlich):**

1. **Definition:** Business Rules sind Python-Skripte (Sandbox: RestrictedPython) in der Tabelle `business_rule` mit fünf Typen (`validation`, `computed`, `normalization`, `workflow_guard`, `bulk`); sie werden im Admin-UI gepflegt und in definierten Ausführungskontexten ausgeführt (Abschnitt 8).
2. **Berechnete Attribute (stored-first):** Als `computed` markierte Attribute werden **gespeichert**; ihr Wert wird **beim Schreiben synchron** durch die zugehörige `computed`-Regel berechnet und atomar mit dem Save persistiert. Es gibt **keinen** On-Read-/Virtuell-Modus; Lese-Pfade (Grid, Export, Suche) lesen nur die Datenbank. Formeln sind **pure Funktionen** der aufgelösten Attributmenge (eigene + vererbte + sprachlich aufgelöste Werte) und deklarieren ihre Eingaben (`dependencies`).
3. **Kaskade:** Vererbt-abhängige berechnete Werte von Nachfahren werden **asynchron per Recompute-Job** aktualisiert (eventual consistency); Änderungen an Regeln/Definitionen lösen einen Bulk-Recompute-Job aus (R5).

> **Details:** [business-rules.md](detail/business-rules.md) — Regeln, Sandbox, Ausführung, Lebenszyklus & Rule Lab; [datenmodell.md](detail/datenmodell.md) — Tabellen; [api-design.md](detail/api-design.md) — API; [jobs-scheduler.md](detail/jobs-scheduler.md) — Recompute-Jobs.

**Schlüssel-Konvention (verbindlich): Business-Schlüssel (z. B. SKU):**

1. **Definition:** Ein Business-Schlüssel ist eine markierte Attributdefinition (`is_key: true`) und dient dem Anwender als fachliche Kennung – für ihn äquivalent zum technischen Primärschlüssel (`id`). Der Wert liegt normal im `attributes`-Feld; es gibt kein separates Feld und keine eigene Tabelle.
2. **Eindeutigkeit:** Gilt pro Objekttyp. Da Attributnamen dynamisch im flexiblen `attributes`-Objekt liegen, lässt sich die Eindeutigkeit nicht als statischer kompositer Schema-Index abbilden – der Kern erzwingt sie **beim Schreiben anwendungsseitig** (Abfrage `(type, attributes.<name>)`, exakt/case-sensitiv, getrimmt). Leere/fehlende Werte kollidieren nicht (mehrere Objekte dürfen keinen Schlüssel haben).
3. **Mehrere erlaubt:** Pro Objekt können mehrere Schlüssel definiert sein (z. B. `sku`, `ean`), jeweils mit eigener Eindeutigkeit pro Typ.
4. **Optional:** Schlüssel sind optional; Objekte dürfen ohne Schlüssel existieren (Migration und Erstbefüllung ungehindert).
5. **Datentyp & Form:** Nur `text`, nicht lokalisiert (kein `multilingual`); Vergleich exakt (case-sensitive) mit Trim.
6. **Keine Vererbung:** Schlüssel sind nie vererbbar (`is_key: true` erzwingt `inheritable: false`).
7. **Änderbar:** Schlüsselwerte sind änderbar; der Kern prüft die Eindeutigkeit bei jeder Änderung und protokolliert sie im Audit-Log.
8. **Adressierung & Import:** Die API bietet einen Key-Lookup-Endpunkt; der MEF-Import kann per Schlüssel abgleichen (Upsert).

> **Details:** [datenmodell.md](detail/datenmodell.md#schluessel-business-schluessel) — Datenmodell & Eindeutigkeit; [api-design.md](detail/api-design.md) — Key-Lookup; [integration-eventsystem.md](detail/integration-eventsystem.md) — MEF & Import-Abgleich.

**Attribut-Datentypen & Datentypwechsel (vorgesehen, nicht im MVP):**

1. **Typ liegt in der Definition:** Der Datentyp eines Attributs (`type` in `attribute_definition`: `text`, `number`, `boolean`, `date`, `enum`, `reference`) plus optionale Modifikatoren (`multilingual`, `unit_id`/`display_unit_id`) bestimmen Speicherung und Validierung. Die Werte werden **generisch als JSON** im `attributes`-Feld gespeichert – es gibt keine typgebundene Spaltenstruktur.
2. **Wechsel später möglich:** Ein Wechsel des Datentyps ist eine **spätere Ausbaustufe** (Phase 9) und in der ersten Version **nicht** verfügbar. Datenmodell und API werden so angelegt, dass ein späterer Wechsel inkl. Umstellung vorhandener Werte **möglich bleibt** (keine typgebundenen Speicherformate, Typ als reine Definitionseigenschaft, Werte roh/umkehrbar im JSON).
3. **Nicht jeder Wechsel ist möglich oder verlustfrei:** Zulässige bzw. verlustfreie Übergänge werden über eine Kompatibilitätsmatrix geregelt (z. B. `number → text` verlustfrei, `text → number` nur bei parsebaren Werten, `reference → text` id-basiert). Ein Wechsel erfordert eine **Migration der vorhandenen Werte** und einen **Audit-Log-Eintrag**.

> **Details:** [datenmodell.md](detail/datenmodell.md#datentypwechsel) — Kompatibilitätsmatrix, Migration & Entwurfsregeln.

---

## 5. API-Design (OpenAPI REST)

Der Rust-Kern stellt eine vollständige REST-API bereit, dokumentiert mit OpenAPI 3.x. Die vollständige Endpunktliste sowie Authentifizierungs-, Rollen- und Paginationsdetails stehen im Detaildokument.

> **Details:** [detail/api-design.md](detail/api-design.md)

---

## 6. Hintergrundprozesse & Scheduler

Der Scheduler und die Job-Verarbeitung sind für **geplante oder manuell gestartete Aufgaben** zuständig. Der Rust-Kern erzeugt `job_run`-Einträge und publiziert sie an NATS JetStream; der Python-Worker führt die Jobs aus und meldet den Status per REST zurück. Architektur, Ablauf, Job-Typen und Fehlerbehandlung stehen im Detaildokument.

Der Job-Typ `business_rule` (Bulk-/Recompute-Jobs über Objektmengen) sowie **Recompute-Jobs für vererbt-abhängige berechnete Attribute** nutzen die Business-Rules-Infrastruktur (Abschnitt 8).

Die Verarbeitung ist **asynchron und blockiert das System nicht**: Der Worker verarbeitet Job- und Event-Streams parallel (asyncio, konfigurierbare Parallelität, Importe begrenzt); Import-/Export-/Bulk-Jobs sind **idempotent** (Upsert per Business-Schlüssel bzw. Quell-Referenz); NATS-Streams haben **explizite Retention-Limits**. Event-getriggerte Jobs (`trigger: event`) sind für Phase 5+ **reserviert**.

> **Details:** [detail/jobs-scheduler.md](detail/jobs-scheduler.md)

---

## 7. Integration & Eventsystem

Meadow PIM kommuniziert mit externen Systemen über **Inbound- und Outbound-Prozesse**: das einheitliche Austauschformat MEF für Import/Export, ein CRUD-Eventsystem über NATS JetStream und ein Webhook-Outbound an die Middleware. Details zu Format, Event-Typen und Konnektoren stehen im Detaildokument.

Beim Inbound normalisieren **`normalization`-Regeln** (R3, Abschnitt 8) die MEF-Werte vor dem Schreiben.

Der Inbound erfolgt **asynchron über `POST /imports`** (MEF als JSON oder Datei-Upload; sofortige `job_run`-ID, nie blockierend). CRUD-Events werden per **Transactional Outbox** im selben Schreibvorgang wie die Daten persistiert und asynchron über ein Relay an NATS JetStream publiziert – die API ist damit nie von NATS abhängig und es gehen keine Events verloren. Die Event-Zustellung ist **at-least-once** (Deduplizierung über `event_id`), die Reihenfolge **pro Objekt garantiert**; der Webhook-Outbound kann **coalescen** (letztes Event pro Objekt+Typ im Fenster) und Bulk-Importe können auf ein Summary-Event umgestellt werden (`emit_events`). Konnektoren (Webhook/Import/Export) sind **persistierte Datensätze** (Tabelle `connector`, Verwaltung über `/connectors`); der Worker wendet Änderungen per **Live-Reload** ohne Neustart an – Env-Variablen dienen nur als Bootstrap.

> **Details:** [detail/integration-eventsystem.md](detail/integration-eventsystem.md)

---

## 8. Workflow, Freigaben & Business Rules

Es wird **keine schwere Workflow-Engine** eingesetzt. Die Statuslogik ist eine einfache Zustandsmaschine, die im Python Business-Rules-Service implementiert ist; der Rust-Kern speichert nur das Statusfeld (`status`) und protokolliert Änderungen im Audit-Log. Details zu Übergängen und Freigaben stehen im Detaildokument.

> **Details:** [detail/workflow-freigaben.md](detail/workflow-freigaben.md)

### 8.1 Business Rules (Python-Skripte)

**Business Rules** sind anpassbare Geschäftslogik in **Python** als Skriptsprache: gespeichert in der Tabelle `business_rule`, bearbeitet im Admin-UI, ausgeführt in einer **gesicherten Sandbox** (RestrictedPython). Es gibt **einen** Autoren-Mechanismus; fünf Regeltypen unterscheiden sich nur durch Ausführungskontext und Ergebnisinterpretation:

| Typ              | Zweck                                                                   | Ausführung                     |
|------------------|-------------------------------------------------------------------------|--------------------------------|
| `validation`     | Prüfungen beim Schreiben (blockierend: `error`; annotierend: `warning`) | **synchron** im Schreibpfad    |
| `computed`       | Berechnete/abgeleitete Attribute (gespeichert)                          | **synchron** im Schreibpfad    |
| `normalization`  | Bereinigung/Transformation beim MEF-Import                              | Import-Pipeline (Worker)       |
| `workflow_guard` | Erlaubte Statusübergänge / Publish-Hürden                               | **synchron** bei Statuswechsel |
| `bulk`           | Regel über gefilterte Objektmenge (Recompute/Anreicherung)              | Job (`business_rule`, async)   |

**Kernentscheidungen (verbindlich):**

- **Schreibpfad:** Der Rust-Kern bleibt die DB-Autorität, ruft aber für `validation`/`computed`/`workflow_guard` **synchron per NATS Request/Reply** den Python-Service auf (Subject `rules.evaluate`); das Ergebnis (Fehler/Warnungen, berechnete Werte, Übergangs-Verdikt) wird atomar im selben Schreibvorgang erzwungen bzw. gespeichert.
- **Berechnete Attribute (R2, stored-first):** gespeicherte Werte, beim Schreiben **synchron** berechnet – kein On-Read-/Virtuell-Modus im MVP, Lese-Pfade bleiben reine DB-Reads; pure Funktionen der aufgelösten Attributmenge; deklarierte `dependencies`; vererbt-abhängige Werte von Nachfahren werden **async per Recompute-Job** aktualisiert (eventual consistency); Definitionsänderung → Bulk-Recompute-Job.
- **Sandbox:** RestrictedPython in-process; Import-Whitelist administrativ konfigurierbar; harte Limits (Timeout ~1–2 s blockierend / ~30 s Bulk, Speicher, Skriptgröße); keine externe I/O.
- **Lebenszyklus & Sicherheit:** `draft`/`active`/`disabled`; Compile-Check bei jedem Speichern; Dry-Run + **Rule Lab** (interaktives Testmodus gegen reale Objekte, read-only, keine Persistenz) vor Aktivierung; Versionierung + Audit; **fail-closed** bei Regel-Fehlern + **Circuit Breaker** (Auto-`disabled` nach N Fehlern + Alert); RBAC: nur `admin`/`superadmin` dürfen Regeln pflegen/aktivieren.
- **Abgrenzung:** Regeln rufen keine Webhooks/Events und keine externen Systeme auf (Aufgabe des Eventsystems, Abschnitt 7); keine schwere Workflow-Engine, keine externe Rule-Engine.

> **Details:** [detail/business-rules.md](detail/business-rules.md)

---

## 9. Benutzer, Gruppen & Rechte (RBAC + ABAC-Scopes)

Die Rechteprüfung erfolgt **ausschließlich im Rust-Kern** (autoritative Quelle). Kombiniert werden **RBAC** (Rollen & Gruppen) mit **ABAC-Scopes** (welche Daten sichtbar/bearbeitbar sind); SSO (OIDC) ist vorbereitet. Details zu Rollen, Berechtigungen, Datenmodell und JWT stehen im Detaildokument.

> **Details:** [detail/benutzer-gruppen-rechte.md](detail/benutzer-gruppen-rechte.md)

---

## 10. Gespeicherte Suchen & Materialisierte Collections

Komplexe Suchanfragen können als Objekt vom Typ `saved_search` gespeichert und ihre Ergebnisse als refreshbare `collection` materialisiert werden. Dies ist nicht Teil der ersten Version, wird aber von Anfang an im Datenmodell und in der Architektur berücksichtigt. Details stehen im Detaildokument.

> **Details:** [detail/gespeicherte-suchen-collections.md](detail/gespeicherte-suchen-collections.md)

---

## 11. Benutzeroberfläche (Frontend)

Die Weboberfläche läuft als separater Docker-Container mit Python + NiceGUI und kommuniziert ausschließlich über die REST-API des Rust-Kerns. Ein Dienst für alle Benutzer; Menüs und Aktionen werden rollenbasiert ein-/ausgeblendet. Die Oberfläche ist **arbeitsflächen-orientiert**: Das **Grid** (`ui.aggrid`) ist die Hauptarbeitsfläche mit Inline-Editing und Bulk-Aktionen, das **Produkt-Formular** ein Drill-down, die **Massendatenpflege** (Bulk-Wizard) läuft als Hintergrundjob. Eine berechnete **Vollständigkeit** (pro Typ × Sprache) steuert Pflegeschwerpunkte. Details zu Architektur, Mehrsprachigkeit und Modulen stehen in den Detaildokumenten.

Das **Rule Lab** (interaktives Testmodus für Business Rules, read-only, keine Persistenz) ist Teil der Admin-Oberfläche (Abschnitt 8).

Hintergrundjobs (Bulk, Import/Export) werden per **Polling** der `job_run`s über die REST-API beobachtet (Status, Fortschritt, Report); der Grid-Batch-Save (`batch-update`) ist **gedeckelt** (max. 500 Zeilen/Aufruf) – größere Auswahlen laufen über den Bulk-Wizard.

Das **Default-Branding (CI)** startet mit einer Farbskala passend zum Leitbild „grüne Wiese im Tal mit Fluss, umrandet mit Wäldern und Bergen“ (Primär `Meadow Green #16a34a`, Sekundär `River Blue #0ea5e9`, Akzent `Sunlight Amber #d97706`); Logos/Grafiken folgen später. Details in [frontend.md](detail/frontend.md).

**Stand Phase 7:** Frontend-Grundgerüst (Schritt 1), Oberflächen-i18n (Schritt 2), das Objekt-Grid als Basis (Schritt 3), Inline-Editing/Batch-Save (Schritt 3b), der Spalten-Picker (Schritt 4), persönliche Ansichten (Schritt 4b), der Strukturbaum (Schritt 5), der Kategorien-Baum, der Referenz-Picker und das **Produkt-Formular** (Schritt 6, inzwischen überarbeitet: drei Abschnitte mit Stammdaten/Attribute-ohne-Gruppe/Attributgruppen, Hybrid-Mehrsprachigkeit über „Haupteditiersprache“ + „Sprachen (n)“-Dialog mit `GET /languages`, Fehlergrund-Anzeige beim Speichern) sind umgesetzt; Bulk-Wizard, Dashboard und Admin-Seiten folgen.

> **Details:** [detail/frontend.md](detail/frontend.md) – Architektur, Mehrsprachigkeit & Modulübersicht; [detail/frontend-arbeitsflaechen.md](detail/frontend-arbeitsflaechen.md) – Grid, Formular, Massendatenpflege, Vollständigkeit & Dashboard.

---

## 12. Betrieb & Deployment

Alle Komponenten werden als Docker-Container betrieben; die Standardauslieferung erfolgt als **Docker-Compose-Setup**, Kubernetes (k3s) ist vorbereitet. Details zu Containern, Netzwerk, Konfiguration, Skalierung, Backup und Monitoring stehen im Detaildokument. Konkretes Ziel für das erste Produktions-Deployment ist der VPS unter `example.com` mit dem dort laufenden Traefik (externes Docker-Netz `traefik-net`); die Detail-Spezifikation inkl. Runbook steht im Detaildokument.

**Logging-Konvention (verbindlich):**

1. **Strukturiert auf `stdout`:** Jede Komponente loggt strukturiert (Rust `tracing`, Python `structlog`) als Event-Stream auf `stdout` (12-Factor-Prinzip); Sammlung über den Docker-Logging-Treiber. DEBUG/TRACE bleiben ausschließlich hier (Diagnose-Skripte).
2. **Kuratierte System-Logs (`log_entry`):** Jede Komponente sendet einen kuratierten Teil – Kategorien `app` ab WARN sowie `job`, `rule`, `auth`, `integration` auch auf INFO – asynchron per REST (`POST /system/logs`, Batch, non-blocking, bounded Buffer) an den Kern; dieser persistiert sie in der Tabelle `log_entry`. Anzeige in der Admin-UI („System-Logs“), nur `admin`/`superadmin`.
3. **Schema:** `created_at` (UTC, ISO-8601), `service`, `level`, `category`, `message`, `trace_id`/`request_id` (Korrelation über Dienste), `context` (JSON, optional). Keine Volltextsuche im MVP (Filter über Felder + `CONTAINS`).
4. **Aufbewahrung:** `log_entry` ist **flüchtig** – Standard 30 Tage (konfigurierbar), Cleanup als periodischer Job; Rate-Limit-Cap gegen Log-Fluten.
5. **Datenschutz (verbindlich):** keine Passwörter, Tokens, JWT-Secrets oder API-Keys in Meldungen oder `context`; Benutzer als `user_id` statt Klarname/E-Mail; IP-Adressen nur bei Bedarf (z. B. wiederholte fehlgeschlagene Logins).
6. **Abgrenzung:** `audit_log` = fachliche, dauerhafte Änderungshistorie (wer/was/wann); `log_entry` = technische/operative Einträge (flüchtig); NATS-Events = Nachrichten für asynchrone Verarbeitung. Fachliche Änderungen landen nur in `audit_log`, technische Fehler/Ops nur in `log_entry`.
7. **Konfiguration:** Logging-Einstellungen sind im Betrieb per Admin-UI änderbar und persistiert (Tabelle `system_setting`; Auflösung Default < ENV < DB, „Auf Default zurücksetzen“); `LOG_LEVEL` (stdout) bleibt ENV-only.

> **Details:** [detail/betrieb-deployment.md](detail/betrieb-deployment.md#11-monitoring--logging) – Logging-Spezifikation; [detail/datenmodell.md](detail/datenmodell.md) – `log_entry`/`system_setting`; [detail/api-design.md](detail/api-design.md) – `POST/GET /system/logs`, `GET/PUT /system/settings`; [detail/frontend.md](detail/frontend.md) – Admin-Seiten.

---

## 13. Versionierung & Audit

Meadow PIM setzt auf eine **schlanke Versionierung** (`version`, `created_at`, `updated_at`, `updated_by`) und ein einfaches **Audit-Log**; auf eine vollständige Revisionshistorie wird bewusst verzichtet. Details zu Versionierung, Audit-Feldern und der Abgrenzung zum Eventsystem stehen im Detaildokument.

> **Details:** [detail/versionierung-audit.md](detail/versionierung-audit.md)

---

## 14. Entwicklungsplan

### Phase 1 – Grundgerüst & Datenmodell-Basis

- Rust-Projekt aufsetzen (Axum, SurrealDB-Anbindung)
- Basis-CRUD für Objekte und Attributdefinitionen
- OpenAPI-Dokumentation generieren
- **Datenmodell für Sprachbaum vorbereiten** (Tabelle `language_tree`, Fallback-Mechanismus in der API-Struktur)
- **Einheiten-Katalog (`unit`-Tabelle) mit allen SI-Einheiten und üblichen Darstellungen vorbereiten; Attributdefinition um `unit_id`/`display_unit_id` erweitern**
- **Attributdefinition um `inheritable`-Flag erweitern**
- **Attributdefinition um `applicable_types`/`required_types` erweitern (Produktvorlage pro Typ; Grundlage für Formular-Sichtbarkeit und Vollständigkeit)**
- **Attributdefinition um `is_key`-Flag für Business-Schlüssel erweitern (Eindeutigkeit pro Objekttyp, anwendungsseitig beim Schreiben)**
- **Business-Rules-Basis vorbereiten: Tabelle `business_rule`; Attributdefinition um `computed`/`computed_rule`/`dependencies`/`computed_reads_inherited` erweitern (berechnete Attribute, stored-first, Abschnitt 8)**
- **Relationenmodell festlegen: Komposition (`depends_on`) vs. Aggregation (`category_member`, `collection_member`, `related`); Tabelle `relation_definition`; Universal-Root als reales Objekt**
- **Tabellen `user` und `group` sowie JWT-Authentifizierung mit den Rollen `superadmin`, `admin`, `editor`, `viewer` vorbereiten**

### Phase 2 – Kernfunktionen: Metadaten, Suche, Vererbung

- Flexible Attributgruppen und -definitionen vollständig implementieren
- Indizes und Volltextsuche konfigurieren
- Referenzen mit Metadaten umsetzen
- **Sprachabhängige Attributauflösung gemäß Sprachbaum implementieren (inkl. `fallback_order`)**
- **Datenwertvererbung über `depends_on` implementieren (Ein-Eltern-Auflösung, leere Werte als Unterbrechung)**
- **Kategorisierung als normale Objekte mit `category_member`-Aggregationen umsetzen (ohne Vererbungswirkung)**
- **Typrestriktionen der `relation_definition` beim Anlegen jeder Relation erzwingen; Re-Parenting mit Zyklenschutz**
- **Business-Schlüssel implementieren: Eindeutigkeit pro Typ beim Schreiben erzwingen, Validierung (`type: text`, nicht lokalisiert, nicht vererbbar), Key-Lookup-Endpunkt, Schlüssel in Suche/Filterung**
- **Tabelle `audit_log` und Versionierung (`version`, `created_at`, `updated_at`, `updated_by`) implementieren (Abschnitt 13)**
- **Logging-Basis im Kern: Tabelle `log_entry` mit Indizes, Endpunkte `POST/GET /system/logs`; Tabelle `system_setting` mit `GET/PUT /system/settings`; Logging-Konvention (Schema, No-Secrets) verankern**

### Phase 3 – Graph & Performance

- Graph-Traversierung optimieren, Benchmarks durchführen
- Caching einführen (Redis optional)
- **Performance der Vererbungs- und Sprachauflösung messen und optimieren**
- **Indizes für Vererbungsrelationen und Kategorieabfragen sicherstellen**
- **Such-Performance verfeinern: Fulltext-Per-Feld-Indizes und ggf. dynamische Unique-Indizes für Business-Schlüssel (Phase-2-MVP: ein Fulltext-Index über `attributes`, Eindeutigkeit anwendungsseitig)**

### Phase 4 – Python-Integration & Business Rules

- **Python-Service für Business Rules aufsetzen (Sandbox: RestrictedPython, konfigurierbare Import-Whitelist, Timeout-/Speicher-/Größen-Limits, Script-Kontrakt `run(obj, ctx, result)`); Statusmaschine (`workflow_guard`) und Validierungen (`validation`) als Regeln**
- **Synchronen Rule-Evaluation-Endpunkt für den Schreibpfad des Rust-Kerns implementieren (NATS Request/Reply, Subject `rules.evaluate`; fail-closed, atomares Erzwingen von Fehlern/berechneten Werten/Übergangs-Verdikt; enge Timeouts; optionaler Degrade-Modus „Speichern + als unberechnet markieren“)**
- **Berechnete Attribute (`computed`, stored-first) umsetzen: Berechnung beim Schreiben, pure Funktionen, deklarierte `dependencies`, async Recompute-Kaskade für vererbt-abhängige Werte, Bulk-Recompute bei Definitionsänderung**
- **`normalization`-Regeln (R3) in die Import-Pipeline integrieren (MEF-Werte vor dem Schreiben bereinigen/transformieren)**
- **Regel-Lebenszyklus implementieren: `draft`/`active`/`disabled`, Compile-Check bei jedem Speichern, Versionierung + Audit, Circuit Breaker (Auto-`disabled` + Alert), RBAC `admin`/`superadmin`**
- **Rule Lab / Dry-Run umsetzen (interaktives Testmodus gegen reale/konstruierte Objekte, read-only, keine Persistenz, auditierte Lab-Läufe)**
- **Event-basierte Kommunikation vorbereiten (Outbox-Grundlage: Tabelle `outbox_event` + Event-Modul; Relay + atomare Verdrahtung folgen in Phase 6)**
- **Business Rules mit Zugriff auf vererbte und sprachlich aufgelöste Werte ausstatten**
- **Schnittstelle für benutzerdefinierte Regeln definieren, die auf dem generischen Objektmodell arbeiten (eine Skriptsprache, ein Mechanismus)**
- **Log-Sender im Worker: kuratierte Job-/Rule-Logs als Batch über `POST /system/logs` an den Kern senden (non-blocking, bounded Buffer, drop-oldest); Logging-Konfiguration periodisch vom Kern laden (`system_setting`)**

**Hinweis zur Phasenabgrenzung:** Autoren, Compile-Check und Dry-Run für `normalization`- und `bulk`-Regeln sind in Phase 4 umgesetzt (ein Kontrakt, fünf Regeltypen). Die **Ausführung** dieser Typen – `normalization` in der MEF-Import-Pipeline (Phase 6) sowie `bulk`/Recompute-Kaskade als `business_rule`-Jobs (Phase 5) – entsteht in den späteren Phasen; der Event-Trigger `event` bleibt reserviert (Phase 5+). Details: [jobs-scheduler.md](detail/jobs-scheduler.md), [integration-eventsystem.md](detail/integration-eventsystem.md).

### Phase 5 – Hintergrundverarbeitung & Scheduler

- NATS JetStream in die Infrastruktur aufnehmen
- Rust-Kern um Scheduler und Job-Verwaltung erweitern
- Python Worker Service implementieren (Basisframework)
- **Job-Typen für Business Rules vorsehen (inkl. `recompute` für vererbt-abhängige berechnete Attribute und Bulk-Recompute bei Definitionsänderung), Inbound/Outbound und später für Collection-Refresh**
- **Worker mit paralleler Verarbeitung (asyncio): getrennte Konsumenten für Job- und Event-Streams, konfigurierbare Parallelität, Importe begrenzt; Idempotenz-Regeln für schreibende Jobs**
- **Retention-Limits für NATS-Streams festlegen (Jobs kurzlebig, DLQ manuell); `trigger: event` als reservierten Trigger dokumentieren (Phase 5+)**
- **Log-Retention: periodischer Cleanup-Job für `log_entry` (Standard 30 Tage, konfigurierbar über `system_setting`); Rate-Limit-Cap gegen Log-Fluten**

### Phase 6 – Integration & Eventsystem

- **Meadow Exchange Format (MEF) als Standard-Import/Export-Format implementieren**
- **Quantifizierte Werte im MEF (`{"value": …, "unit": …}`) mit Normalisierung auf die kanonische SI-Einheit unterstützen**
- **Inbound-Konnektor `json_import` und Outbound-Konnektor `json_export` umsetzen**
- **MEF-Import mit `normalization`-Regeln (R3) verknüpfen (Werte vor dem Schreiben bereinigen/transformieren; gleiche Sandbox wie Schreibpfad-Regeln)**
- **Dedizierter Inbound-Endpunkt `POST /imports` (MEF als JSON oder Datei-Upload, asynchron mit `job_run`-ID)**
- **CRUD-Events per Transactional Outbox (atomar mit dem DB-Write) erzeugen und über ein Relay asynchron nach NATS JetStream publizieren**
- **Webhook-Outbound-Konnektor mit Filterung, Retry-Logik, at-least-once-Zustellvertrag (`event_id`-Dedup) und Coalescing zur Middleware entwickeln; konfigurierbares `emit_events` für Import-Jobs (Summary-Event)**
- **Konnektor-Konfiguration persistieren (Tabelle `connector` + `/connectors`-API) und im Worker per Live-Reload anwenden (Multi-Konnektor-Fanout; Env nur als Bootstrap)**
- **Import-Abgleich per Business-Schlüssel (Upsert) im `json_import`-Konnektor; Idempotenz zusätzlich über Quell-Referenz für Objekte ohne Schlüssel; Schlüssel im MEF-Format und in Events abdecken**
- **Events für Objekt-, Referenz- und Attributdefinitionsänderungen abdecken; Event-Reihenfolge pro Objekt garantieren**

### Phase 7 – Frontend (NiceGUI)

- NiceGUI-Anwendung mit Login, Benutzerverwaltung und rollenbasierten Menüs
- **Grid-first-Arbeitsfläche (`ui.aggrid`):** Kategorien-/Strukturbaum, konfigurierbare/pinnbare Spalten, persönliche Ansichten, Inline-Editing mit Batch-Save, Modal-Editoren
- **Produkt-Formular:** scrollbare Seite mit einklappbaren Attributgruppen, Hybrid-Sprache (Arbeitssprache + editierbare Spalten pro Sprache), Vererbungs-UX (Override, „Zurück auf vererbt“), typspezifische Wertedarstellung (Tags, Toggles, Einheiten, lokalisierte Daten)
- **Vollständigkeit (Completeness):** Berechnung im Kern (Typ × Sprache), Anzeige in Grid, Formular und Dashboard, Filter „unvollständige Produkte“ / „fehlende Pflichtfelder“
- **Massendatenpflege:** Bulk-Edit-Wizard (Attributwert setzen/leeren, Status, Kategorie, Re-Parenting, Massen-Löschen) als Hintergrundjob mit Report
- **Job-Status per Polling über die REST-API (Status, Fortschritt, Report); Batch-Save gedeckelt (max. 500 Zeilen/Aufruf), größere Auswahlen über den Bulk-Wizard**
- **Dashboard als Arbeitsübersicht** mit Vollständigkeits-Shortcuts, letzte Änderungen und Job-Status
- Admin-Seiten für Attributdefinitionen (inkl. `applicable_types`/`required_types`), Jobs, Benutzer und Gruppen
- **Admin-Bereich „Business Rules“: Regel-Editor mit Compile-Check, Zustände (`draft`/`active`/`disabled`), Dry-Run und **Rule Lab** (Regel gegen Objektauswahl ausführen, read-only, keine Persistenz)**
- **Mehrsprachigkeit der Oberfläche (i18n) mit Übersetzungs-Keys umsetzen (Startsprache Englisch)**
- **UI-Unterstützung für Sprachpflege (Sprachbaum) und Kategorisierung (mehrere Kategorie-Bäume)**
- **Admin-Seite „System-Logs“ (nur `admin`/`superadmin`):** Tabelle (Zeit lokalisiert, Dienst, Kategorie, Level, Message, neueste zuerst), Filter (Dienst, Kategorie, Level, Zeitraum, Textsuche), Polling (≈5 s), Detailansicht mit `context`/`trace_id` und Kontext-Links zu Job/Objekt/Rule; CSV-Export optional (Phase 8)
- **Admin-Seite „System-Einstellungen“ (nur `admin`/`superadmin`):** Logging-Einstellungen im Betrieb änderbar und persistiert (`system_setting`), „Auf Default zurücksetzen“, Änderungen im Audit-Log

### Phase 8 – Produktionsreife & MVP-Abschluss

- Sicherheit, Monitoring, Logging, Backup
- Load-Testing und Optimierung
- **Dokumentation für MEF und Eventsystem**
- **Request-/Upload-Limits und Rate-Limits an der API; NATS-Stream-Retention und Queue-Tiefen-Monitoring (Port 8222)**
- **Backpressure-/Ausfalltests: NATS- und Worker-Ausfall ohne Blockade des Kerns (Outbox-Nachlieferung)**
- **Fehlerfälle der Business Rules testen: Circuit Breaker (Auto-`disabled` nach N Fehlern + Alert) und Degrade-Modus (Speichern ohne Rules-Service) verifizieren**
- **Bereitstellung als Docker-Compose-Setup für KMUs**

### Phase 9 – Spätere Ausbaustufen (nicht im MVP)

- **Gespeicherte Suchen (`saved_search`) und Materialisierte Collections (`collection_member`) vollständig umsetzen**
- **Benutzerdefinierte Relationstypen (immer Aggregationen) über die API anlegen und Typrestriktionen verwalten**
- **Kanalspezifische Freigaben (`release`-Tabelle)**
- **Unterstützung für externe Klassifizierungsstandards (z. B. ETIM)**
- **Datentypwechsel für Attributdefinitionen inkl. Migration vorhandener Werte (Kompatibilitätsmatrix, verlustfreie vs. verlustbehaftete Übergänge)**
- **Weitere Sprachen für die Oberfläche (Deutsch, Französisch, …)**
- **Frontend-Ausbau: Pinned Attributes, Attribut-Suchleiste im Formular, Find & Replace, sequenzielles Editieren, Send-for-approval bei Bulk-Änderungen, per-Kanal-Vollständigkeit**
- **SSO-Anbindung (OIDC) inkl. Gruppen-/Claim-Mapping**
- **Optionale zentrale Log-Aggregation (Loki + Grafana) für Volltextsuche und Dashboards – dokumentiert, im MVP nicht umgesetzt**

---

## 15. Zusammenfassung

Meadow PIM kombiniert einen hochperformanten Rust-Kern (headless) mit SurrealDB für flexible Dokumente und Graphen, NATS JetStream für zuverlässige Hintergrundverarbeitung und Python für Business Rules sowie die Benutzeroberfläche (NiceGUI). Durch die lose Kopplung der Dienste bleibt das System einfach, wartbar und skalierbar – exakt passend für die Anforderungen eines KMUs.

---

*Dokument erstellt am 2026-08-13 – Projektname: Meadow PIM.*

*Zuletzt geändert: 2026-08-28 – Phase-7, Schritt 5 (Abschnitt 11): Strukturbaum im Objekt-Grid (`meadow/frontend/grid.py`, `crates/pim-core/src/api/references.rs`). Neuer Endpunkt `GET /objects/tree` liefert den Kompositionsbaum ab Universal-Root als verschachtelte `{id, children}`-Knoten in einem Call (Zyklenschutz im Core); das Frontend rendert ihn als linkes `ui.tree` und filtert das Grid bei Knoten-Auswahl auf den Teilbaum (Eltern + Nachfahren via `collect_subtree`). Darstellung: **resizable linkes Panel** (`ui.splitter` mit `w-full`, Start 25 %, `limits=(0, 60)`, Breite per Drag-Handle verstellbar; Höhe `h-[calc(100vh-210px)]`, damit das Grid den verfügbaren Bildschirmplatz füllt) mit Filterfeld, **zwei Buttons „Alle einklappen“/„Alle aufklappen“** (`unfold_less`/`unfold_more` → `collapse_all()`/`expand_all()`, ohne Umbruch ans Filterfeld gebunden via `flex-wrap: nowrap`) und Labels „Name · Typ“; der **Universal-Root-Knoten wird verborgen** (Container-Knoten; seine Kinder werden auf Top-Level angehoben, keine „Struktur“-Überschrift); ein `view_sidebar`-Button klappt das Panel ein/aus (`toggle_panel`, `splitter.value` 0/25, volle Grid-Breite beim Einklappen); die **Toolbar** ist rechtsbündig („Speichern“ mit Text, „Verwerfen/Ansichten/Spalten“ als Icon+Tooltip), der Panel-Toggle und der Änderungszähler sitzen links; `response_timeout=10` für die datenlastige Seite. `frontend-arbeitsflaechen.md`, `api-design.md`, `crates/AGENTS.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-29 – Phase-7, Konzept Lazy Loading verfeinert & verifiziert (Abschnitt 11): Variante (a) gewählt — eine `LazyTree`-Unterklasse von `ui.tree`, die Quasar-`@lazy-load` per `element.on(..., js_handler=...)` verdrahtet (clientseitiger `done`-Callback wird global abgelegt und via `Client.run_javascript()` bedient); Knoten mit `lazy: true`, `virtual-scroll`. Recherche-bestätigt: `@lazy-load`/`lazy`/`virtual-scroll` (Quasar-Doku), `js_handler`/`emit()`/`run_javascript` (NiceGUI-Doku/Source). Konzept in `frontend-arbeitsflaechen.md` (1.1) und `api-design.md` notiert.*

*Zuletzt geändert: 2026-08-29 – Phase-7, Lazy Loading des Strukturbaums umgesetzt (Abschnitt 11): `meadow/frontend/lazy_tree.py` (`LazyTree`-Unterklasse von `ui.tree`) lädt Kinder on demand über `GET /objects/{id}/children` (paginiert, mit `hasChildren`, überspringt defekte `depends_on`-Verweise); Knoten mit `hasChildren` werden `lazy: true` markiert, Quasar-`@lazy-load` per `js_handler` + `Client.run_javascript()` verdrahtet, `virtual-scroll` aktiviert. Die Teilbaum-Filterung bei Knoten-Auswahl ist serverseitig: `GET /objects?ancestor=<id>` (Core `subtree_keys`, BFS über `rel_depends_on`, BARE Keys; `id IN array::map($ancestor_ids, |$k| type::record('object', $k))`) ersetzt das frühere clientseitige `collect_subtree`. `frontend-arbeitsflaechen.md` (1.1), `api-design.md`, `crates/AGENTS.md` und `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, Darstellungs-Fix Strukturbaum (Abschnitt 11): Der Baum lief über die Kartenhöhe hinaus (Inhalt 1304px in 870px), wodurch das linke Splitter-Panel extern scrollte und eine „mitwandernde“ Kante entstand. LazyTree ist jetzt auf die Kartenhöhe begrenzt (`min-height: 0`) und scrollt intern (`overflow-y: auto`), sodass kein Überlauf mehr entsteht. `virtual-scroll` greift in dieser NiceGUI/Quasar-Version nicht (gerendert wird `q-tree--standard`, kein `.q-tree__scroll-area`); `frontend-arbeitsflaechen.md` und `meadow/AGENTS.md` entsprechend angepasst.*

*Zuletzt geändert: 2026-08-30 – Phase-7, Vollständigkeits-Spalte im Objekt-Grid (Abschnitt 11 & 4): Berechnung der Vollständigkeit im Rust-Kern (`crates/pim-core/src/completeness.rs`, `compute`/`split_set`): Anteil der Pflichtattribute des Typs (`required_types`), die gesetzt sind (eigener Schlüssel vorhanden – auch leer, Vererbungsunterbrechung – oder vererbt & nicht `null`). `GET /objects?locale=<code>` liefert `completeness: { "<code>": 0–100 }` pro Objekt, `GET /objects/{id}?locale=<code>` zusätzlich `missing_required` (Liste fehlender Pflichtattribute, für das Formular-Widget). Frontend: Grid-Spalte „Completeness“ (`_completeness`, `completeness_badge`: grau 0 %, orange 1–99 %, grün 100 %), `CoreClient.list_objects(..., locale=...)` übergibt `?locale=`; `_status`/`_completeness` als `html_columns`, damit Badges gerendert werden. Filter `?completeness_lt`/`?missing_required` noch offen (folgt mit der Filterleiste). `api-design.md`, `frontend-arbeitsflaechen.md`, `crates/AGENTS.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, Kategorien-Baum im Objekt-Grid (Abschnitt 11 & 4): **Icon-Umschalter „Struktur/Kategorien“** im linken Panel (`ui.button_group`, `account_tree`/`category`, aktiver Button `color=primary`, Tooltip; zweizeiliger Panel-Kopf mit Filter/Einklapp-Buttons in eigener Zeile). Die Kategorien-Sicht lädt nur `category`-Knoten (Universal-Root identifiziert die Kategorie-Bäume; `LazyTree`, Kinder via `GET /objects/{id}/children` gefiltert auf `category`) und filtert bei Knoten-Auswahl das Grid über **`GET /objects?category=<id>`** — serverseitige Aggregations-Filterung (`category_member_keys` in `references.rs`, `category_member` über die Kompositions-Unterkategorien), bewusst **nicht** über `?ancestor=` (Komposition). Zähler/Baum-Auswahl/„Nicht kategorisiert“ folgen. `api-design.md`, `frontend-arbeitsflaechen.md`, `crates/AGENTS.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, Kategorien-Baum: **Zähler** an Kategorie-Knoten (Abschnitt 11 & 4): `GET /objects/{id}/children?count_members=true` liefert für `category`-Kinder ein **`memberCount`** (Anzahl zugeordneter Objekte via `category_member`, inkl. Unterkategorien; `category_member_keys`); die Kategorien-Sicht zeigt den Zähler im Knoten-Label. Baum-Auswahl / „Nicht kategorisiert“ folgen. `api-design.md`, `frontend-arbeitsflaechen.md`, `crates/AGENTS.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, Kategorien-Baum: **Baum-Auswahl** (Abschnitt 11 & 4): ein `ui.select` im Kategorien-Header („Alle Bäume“ + jeder Kategorie-Root) wählt, welcher unabhängige Kategorie-Baum angezeigt wird; „Alle Bäume“ zeigt alle Roots, ein einzelner Baum nur seinen Root (`selected_tree`, `render_tree`). „Nicht kategorisiert“ folgt. `frontend-arbeitsflaechen.md` und `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, Kategorien-Baum: **„Nicht kategorisiert“-Eintrag** (Abschnitt 11 & 4): ein vorangestellter Baum-Knoten (`uncategorized_node`, `id __uncategorized__`) listet Objekte **ohne Kategoriezuordnung** über `GET /objects?uncategorized=true` (`id NOT IN (SELECT VALUE in FROM reference WHERE definition_id='rel_category_member') AND type NOT IN ['category','root']`). `api-design.md`, `frontend-arbeitsflaechen.md`, `crates/AGENTS.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, **Referenz-Picker** (Abschnitt 11 & 4): Objekt-Grid zeigt eine 🔗-Spalte (`_refs`); Klick öffnet ein Modal mit den Referenzen als **Chips** (aufgelöste Labels via `GET /objects/{id}/references?locale=`, Entfernen über `DELETE /references/{id}`, ohne Komposition `rel_depends_on`), Relationstyp-Dropdown (aus `GET /relation-definitions`, gefiltert auf `kind=='aggregation'`) und Suchfeld (→ `GET /objects/picker`), Treffer werden per `POST /references` angelegt. `create_reference` erwartet den **kurzen Record-Key** als `definition_id` (z. B. `rel_category_member` aus `relation_definition:rel_category_member`), nicht die volle Record-ID oder den Anzeigenamen (`name`). `api-design.md`, `frontend-arbeitsflaechen.md`, `crates/AGENTS.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, **Referenz-Picker Klarheit** (Abschnitt 11 & 4): Die Chips im Picker-Modal werden **nach Relationstyp gruppiert und sortiert** (Komposition `rel_depends_on` zuerst, danach Aggregationen nach Label) mit Abschnittsüberschriften aus `GET /relation-definitions`, plus **Leerzustand** (`grid.ref_empty`). Kanten-Metadaten werden bewusst nicht eingeblendet; Bearbeitung der Kanten erfolgt später im Produktformular. `frontend-arbeitsflaechen.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-30 – Phase-7, **Strukturbaum: Einklappen bei Auswahl behoben** (Abschnitt 11 & 1.1): NiceGUI sendet bei jeder Prop-Änderung (z. B. `selected`) das komplette `nodes`-Prop erneut, wodurch die clientseitig lazy geladenen Kinder verworfen und der Baum nach Auswahl eines Unterknotens eingeklappt wurde. `LazyTree.update()` ist jetzt ein **No-op**; Knoten-Änderungen (Suchfilter) laufen über **`LazyTree.set_nodes()`**. Bei Auswahl eines Unterknotens bleibt der Baum aufgeklappt und das gewählte Produkt wird dargestellt. `meadow/AGENTS.md`, `frontend-arbeitsflaechen.md` aktualisiert.*

*Zuletzt geändert: 2026-08-31 – Phase-7, **Produkt-Formular** (Abschnitt 11 & 2): `meadow/frontend/form.py` (Route `/products/{object_id}`, erreichbar per Doppelklick auf eine Grid-Zeile) lädt das Objekt (`get_object` mit `resolve_inheritance`) plus Attributdefinitionen/-gruppen und rendert **einklappbare Gruppen** (`ui.expansion`) mit **typspezifischen Controls**, **Hybrid-Sprach-Spalten** für mehrsprachige Attribute, **Vererbungs-Marker**, **Vollständigkeits-Widget** und **Status-Auswahl**; Speichern via `PATCH /objects/{id}` (mergt in mehrsprachige Dicts, erhält übrige Attribute). `meadow/AGENTS.md`, `frontend-arbeitsflaechen.md` aktualisiert.*

*Zuletzt geändert: 2026-09-01 – Phase-7, **Produkt-Formular überarbeitet + `GET /languages`** (Abschnitt 11 & 2): Formular zu **drei Abschnitten** umgebaut (① **Stammdaten** mit read-only Objekt-ID, `name` und Business-Key-/Pflicht-Attributen, ② **Attribute ohne Gruppe**, ③ **Attributgruppen** nach `position`), **Hybrid-Mehrsprachigkeit** („Haupteditiersprache“-Selektor für die Inline-Bearbeitung + „Sprachen (n)“-Dialog für alle Datensprachen), **Fehlergrund-Anzeige** beim Speichern (Core-`error`-Meldung via `CoreClient.update_object` → `CoreError`); Abweichung vom Entwurf: Attribute werden als Zeilen in `ui.expansion` gerendert, nicht als Nutzung von `ui.table`-Zell-Slots (NiceGUI-Tabellen-Slots hosten keine zuverlässig pro Zeile unabhängigen Eingabewidgets). Neu: `GET /languages` (`crates/pim-core/src/api/languages.rs`) liefert die Daten-Sprachen aus `language_tree` (code, parent, label, fallback_order). `api-design.md`, `frontend-arbeitsflaechen.md`, `meadow/AGENTS.md`, `crates/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-09-01 – Phase-7, **Produkt-Formular UX-Optimierung** (Abschnitt 11 & 2): „Haupteditiersprache“-Selektor in die **Kopfzeile** verlegt (statt eigener Zeile), „Sprachen (n)“-Zählertext entfernt → **Sprach-Icon-Button** je Attribut. Attributzeilen als **tabellenartige, ausgerichtete Zeilen** (`Label | Wert | Sprach-Icon`) mit **Kopfzeile**, farbige **Expansion-Header** (`header-class`), **gefüllte** Inputs (`filled`) statt Unterstreichung, **Pflichtfeld-Sternchen** (`required_types`). Sprach-Dialog **verbreitert** (`w-[34rem]`) und liest/schreibt die **Arbeitskopie** (`edited`), sodass angewendete Werte beim erneuten Öffnen erhalten bleiben und in das Inline-Feld zurückfließen. `frontend-arbeitsflaechen.md`, `meadow/AGENTS.md` aktualisiert.*

*Zuletzt geändert: 2026-08-28 – Phase-7, Schritt 4b (Abschnitt 11): Persönliche Ansichten im Objekt-Grid (`meadow/frontend/grid.py`). „Ansichten“-Dialog speichert die aktuelle Spaltenauswahl pro Benutzer (`app.storage.user`), listet gespeicherte Ansichten und stellt sie per Klick wieder her (inkl. Löschen); Wiederherstellung über AG-Grid-`setColumnsVisible`. `frontend-arbeitsflaechen.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-28 – Phase-7, Schritt 4 (Abschnitt 11): Spalten-Picker im Objekt-Grid (`meadow/frontend/grid.py`). „Spalten“-Button öffnet ein Menü mit Checkboxen je Attributspalte (aus `GET /attribute-definitions` via `CoreClient.list_attribute_definitions`); Attributspalten sind standardmäßig ausgeblendet, Sichtbarkeit wird per AG-Grid-`setColumnsVisible` getoggelt, die ID-Spalte ist links gepinnt. `frontend-arbeitsflaechen.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-28 – Phase-7, Schritt 3b (Abschnitt 11): Inline-Editing & Batch-Save im Objekt-Grid (`meadow/frontend/grid.py`). Zell-Editing über `ui.aggrid`, Gestaging der Änderungen („Ungespeicherte Änderungen“-Zähler), Batch-Save über `POST /objects/batch-update` (max. 500 Zeilen, `is_session_expired` erkennt abgelaufene JWTs und leitet zur Login-Seite um; `grid.session_expired`). `api-design.md` (Batch-Endpunkt inkl. Auth/Fehler-Semantik: ungültiger Token → HTTP 200 mit `success: false`), `frontend-arbeitsflaechen.md`, `crates/AGENTS.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-28 – Phase-7, Schritt 3 (Abschnitt 11): Objekt-Grid `/products` mit `ui.aggrid` (`meadow/frontend/grid.py`) – lesbare Objektliste, Sprachwert-Auflösung, Status-Badge, i18n-Spaltenüberschriften, Mehrfachauswahl; Daten aus `GET /objects`. `frontend-arbeitsflaechen.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-28 – Phase-7, Schritt 2 (Abschnitt 11): Oberflächen-i18n mit schlanker JSON-basierter Übersetzungsverwaltung (`meadow/frontend/locales/<locale>.json` + `i18n.t(key, locale)`; en/de/fr), Sprachumschalter im Drawer, Locale pro Benutzer in der NiceGUI-Session (Default aus `Accept-Language`, Persistenz im Benutzerprofil später). `frontend.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-28 – Phase-7-Start (Abschnitt 11 & Phase 7): Frontend-Grundgerüst `meadow/frontend/` (NiceGUI, `python -m meadow.frontend`, Dev-Port 8081) mit App-Shell, Login/Session (JWT in `app.storage.user`), rollenbasierten Menüs und Anwendung der CI-Farbskala (`app.colors()`); Grid, Produkt-Formular, Bulk-Wizard, Dashboard und Admin-Seiten folgen. `frontend.md` und `meadow/AGENTS.md` ergänzt.*

*Zuletzt geändert: 2026-08-25 – Default-Branding/CI festgelegt (Abschnitt 11 & Phase 7): Farbskala passend zu „Meadow“ (Primär Meadow Green #16a34a, Sekundär River Blue #0ea5e9, Akzent Sunlight Amber #d97706; neutrale Töne Slate); Umsetzung über NiceGUI-Quasar-Theme (`app.colors()`/`ui.colors()`, Tailwind-Klassen mit `!`-Präfix für Element-Overrides); Logos/Grafiken später. `frontend.md` um Abschnitt „Branding & CI“ ergänzt.*

*Zuletzt geändert: 2026-08-25 – Phase-6-Implementierung nachgezogen (Abschnitte 4, 5, 6, 7 & Phase 6): MEF-Import (`POST /imports`, JSON oder Datei-Upload; Dokument inline in `import_document`, Objektstorage-Ablage der Rohdatei als späterer Ausbau), quantifizierte Werte (SI-Normalisierung, `/units` + `/units/convert`), `json_export`, Webhook-Outbound mit Filterung/Retry/Coalescing und Multi-Konnektor-Fanout, Quell-Referenz-Dedup (`source_id`/`source`, `GET /objects/by-source`), `emit_events` (Summary-Event `import.completed`) und persistierte Konnektor-Konfiguration (Tabelle `connector`, `/connectors`-API, Live-Reload im Worker; Env nur als Bootstrap). `datenmodell.md`, `api-design.md`, `integration-eventsystem.md` und `betrieb-deployment.md` entsprechend ergänzt.*

*Zuletzt geändert: 2026-08-19 – Phase-3-Performance nachgezogen (Abschnitt 4 & Phase 3): In-Process-Metadaten-Cache im Kern (Attributnamen `multilingual`/`inheritable` und Sprach-Fallback-Ketten; Invalidierung bei Attributdefinitions-Schreibvorgängen); Vererbungsauflösung als schrittweises Aufsteigen mit einer Query pro Ebene (`SELECT out, out.attributes FROM reference WHERE …`); Graph-Indizes für Vererbung/Kategorisierung per EXPLAIN bestätigt; Benchmark `cargo run --release -p pim-core --example bench`. `datenwertvererbung.md` und `sprachbaum-mehrsprachigkeit.md` entsprechend präzisiert.*

*Zuletzt geändert: 2026-08-19 – Phase-2-Implementierung nachgezogen (Abschnitte 4 & 12, Phase 3): Business-Schlüssel-Eindeutigkeit anwendungsseitig statt statischem Unique-Index (dynamische Attributnamen im flexiblen `attributes`-Objekt); Volltextsuche im MVP über einen Index auf `attributes` (Analyzer `meadow_std`) statt Per-Feld-Indizes; Re-Parenting als `DELETE` + `RELATE` (Richtung einer RELATION-Kante ist nicht per `UPDATE` änderbar); `POST /search`-Payload sowie `POST/GET /system/logs` (API-Key + Admin-JWT-Dev-Fallback, Offset-Pagination, Batchgrößen-Cap) präzisiert; `datenmodell.md`, `relationen.md` und `api-design.md` entsprechend angepasst.*

*Zuletzt geändert: 2026-08-17 – Querverlinkungen & Phasenzuordnung bereinigt: Phantom-Unterabschnittsverweise (4.4–4.7, 8.2, 10.1/10.2) entfernt und durch Detaildokument-Links ersetzt; Anker in den „Hauptdokument“-Links der Detaildokumente ergänzt; Enum-Korrekturen (`bulk` in `business_rule`, `bulk_operation` in `job_definition`) und `multilingual`-Verortung in `datenmodell.md`; Rechte-Korrektur Attributdefinitionen (nur `admin`/`superadmin`) in `frontend.md`; `audit_log` + Versionierung als Phase-2-Punkt ergänzt; Header-Phasen in `business-rules.md` (Phase 1 statt 2), `workflow-freigaben.md` (4/7/9) und `versionierung-audit.md` (2/7) korrigiert.*

*Zuletzt geändert: 2026-08-17 – Logging spezifiziert (Abschnitte 3.3, 12 & Phasen 2, 4–5, 7, 9): strukturierte stdout-Logs (tracing/structlog) als Basis; kuratierte System-Logs in SurrealDB (`log_entry`-Tabelle: Kategorien `app` ab WARN, `job`/`rule`/`auth`/`integration` auch INFO; Schema mit `trace_id`; Retention 30 Tage + Cleanup-Job; Rate-Limit) über `POST /system/logs` (Batch, non-blocking); Anzeige in der Admin-UI „System-Logs“ (nur `admin`/`superadmin`); No-Secrets-/Datenschutz-Konvention; Abgrenzung `audit_log` (fachlich, dauerhaft) vs. `log_entry` (technisch, flüchtig) vs. NATS-Events; Runtime-Konfiguration über `system_setting` (Default < ENV < DB, Admin-UI „System-Einstellungen“); Loki/Grafana als optionale Ausbaustufe (Phase 9). `betrieb-deployment.md` (§11), `datenmodell.md`, `api-design.md`, `frontend.md` und `benutzer-gruppen-rechte.md` entsprechend angepasst.*

*Zuletzt geändert: 2026-08-17 – Business Rules (Abschnitt 8.1 & Phasen 1, 2, 4–8): Business Rules mit Python als Skriptsprache definiert – fünf Regeltypen (`validation`, `computed`, `normalization`, `workflow_guard`, `bulk`), ein Autoren-Mechanismus (`business_rule`-Tabelle, Admin-UI, RestrictedPython-Sandbox). Schreibpfad: synchroner interner Rule-Evaluation-Aufruf des Rust-Kerns an den Python-Service für `validation`/`computed`/`workflow_guard` (fail-closed, atomar, enge Timeouts, optionaler Degrade-Modus). Berechnete Attribute (R2) stored-first: gespeicherte Werte, synchron beim Schreiben berechnet, pure Funktionen, deklarierte `dependencies`, async Recompute-Kaskade für vererbt-abhängige Werte (eventual consistency), Bulk-Recompute bei Definitionsänderung. Import-Whitelist administrativ konfigurierbar; Lebenszyklus `draft`/`active`/`disabled` mit Compile-Check, Versionierung + Audit, Dry-Run und Rule Lab (interaktives Testmodus, read-only), Circuit Breaker + Alert; RBAC `admin`/`superadmin`. Detaildokument `business-rules.md` neu; `workflow-freigaben.md`, `jobs-scheduler.md`, `api-design.md`, `datenmodell.md`, `benutzer-gruppen-rechte.md` und `frontend.md` entsprechend angepasst.*

*Zuletzt geändert: 2026-08-17 – Asynchrone Verarbeitung präzisiert (Abschnitte 6, 7, 11 & Phasen 5–8): Transactional Outbox für CRUD-Events (API nie von NATS blockiert, kein Event-Verlust); dedizierter Inbound-Endpunkt `POST /imports` (MEF asynchron, `job_run`-ID); konfigurierbares `emit_events` + Webhook-Coalescing; Worker mit paralleler Verarbeitung (getrennte Consumer für Jobs/Events); Idempotenz (Upsert per Business-Schlüssel bzw. Quell-Referenz) und at-least-once-Zustellvertrag (`event_id`-Dedup); Event-Reihenfolge pro Objekt garantiert; Retention-Limits für NATS-Streams + API-Request-/Rate-Limits; `batch-update` gedeckelt (max. 500 Zeilen), größere Auswahlen über Bulk; UI-Job-Status per Polling; `trigger: event` als Phase-5+-Trigger reserviert. Detaildokumente `integration-eventsystem.md`, `jobs-scheduler.md`, `api-design.md`, `datenmodell.md`, `betrieb-deployment.md` und `frontend-arbeitsflaechen.md` entsprechend angepasst.*

*Zuletzt geändert: 2026-08-17 – Frontend-Detailplanung (Abschnitt 11 & Phase 7): arbeitsflächen-orientierte UI (Grid-first mit `ui.aggrid`), Produkt-Formular mit Hybrid-Sprache und Vererbungs-UX, Vollständigkeit (Typ × Sprache), Massendatenpflege (Bulk-Wizard), Dashboard als Arbeitsübersicht; Attributdefinition um `applicable_types`/`required_types` erweitert (Produktvorlage); Detaildokument `frontend-arbeitsflaechen.md` neu; `frontend.md`, `datenmodell.md`, `api-design.md` und `jobs-scheduler.md` entsprechend angepasst.*

*Zuletzt geändert: 2026-08-17 – Schlüssel-Konvention (Abschnitt 4): Business-Schlüssel (z. B. SKU) als markierte Attributdefinition (`is_key: true`), Eindeutigkeit pro Objekttyp via Unique-Index `(type, attributes.<name>)`, optional, nur `text`, nicht lokalisiert, nicht vererbbar, änderbar mit Audit, Key-Lookup-Endpunkt und Import-Abgleich (Upsert); `datenmodell.md`, `api-design.md` und `integration-eventsystem.md` entsprechend ergänzt; Phasen 1, 2 und 6 angepasst.*

*Zuletzt geändert: 2026-08-17 – Relationenmodell spezifiziert (Abschnitt 4): Komposition (`depends_on`) vs. Aggregation, Universal-Root als reales Objekt, Tabelle `relation_definition` mit Typrestriktionen, Kaskaden-Soft-Delete und Re-Parenting mit Zyklenschutz; Detaildokument `relationen.md` neu, `datenwertvererbung.md`, `datenmodell.md`, `kategorisierung.md` und `api-design.md` entsprechend angepasst (Multi-Parent, `scope`, `priority` entfernt).*

*Zuletzt geändert: 2026-08-16 – Repository-Ausgangslage für die Implementierung ergänzt (Abschnitt 3.6): Rust-Workspace (`Cargo.toml` + `crates/pim-core/`), Python-Paketierung (`pyproject.toml` + `uv.lock`), lokale Infrastruktur (`docker-compose.dev.yml`), Hilfsskripte (`scripts/`); Editor-Konfiguration (`.vscode/`, `.editorconfig`, `rustfmt.toml`).*

*Zuvor geändert: 2026-08-16 – Lokale Entwicklungsumgebung ergänzt (Abschnitt 3.5) und Paketmanager `uv` in den Python-Stack aufgenommen (Abschnitt 3.2): Python-3.14-venv (`.venv/`) über uv verwaltet, Rust-Toolchain via rustup (stable, Edition 2024, MSRV ≥ 1.88), MSVC Build Tools als Windows-Linker; Python-MVP-Abhängigkeiten `nicegui`, `pydantic`, `nats-py`, `httpx`, `structlog`, `babel`.*

*Zuvor geändert: 2026-08-16 – Datentypwechsel-Konvention ergänzt (Abschnitt 4): Datentyp liegt in der Attributdefinition, Werte generisch als JSON; Wechsel als spätere Ausbaustufe (Phase 9) vorgesehen, Kompatibilitätsmatrix & Migrationsregeln im Detaildokument `datenmodell.md`; Entwicklungsplan Phase 9 entsprechend ergänzt.*

*Zuvor geändert: 2026-08-16 – Einheiten-Konvention ergänzt (Abschnitt 4): Speicherung in kanonischer SI-Einheit, Austausch als `{"value", "unit"}`, Umrechnung in der Darstellung; Detaildokument `einheiten.md` neu und um anglo-amerikanische Einheiten (imperial/US customary) ergänzt; `datenmodell.md`, `api-design.md`, `frontend.md`, `integration-eventsystem.md` und Entwicklungsplan (Phase 1, 6, 7) entsprechend ergänzt.*

*Zuvor geändert: 2026-08-16 – Datumswerte-Konvention ergänzt (Abschnitt 4): UTC-Speicherung in SurrealDB, ISO-8601-Austausch über API/Inbound/Outbound, lokalisierte Darstellung in der GUI; Detaildokumente `datenmodell.md`, `api-design.md`, `integration-eventsystem.md`, `frontend.md` entsprechend ergänzt.*

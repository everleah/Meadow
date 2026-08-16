# Meadow PIM – Architektur & Technischer Plan

## 1. Einleitung

Dieses Dokument beschreibt die vereinbarte Architektur und den technischen Plan für ein modernes, performantes Product-Information-Management-System (PIM) für mittelständische Unternehmen (KMUs). Die Entwicklung folgt der Prämisse **einfach, performant und wartbar** zu sein. Der Kern ist in Rust geschrieben, flexible Geschäftslogik und die Benutzeroberfläche in Python.

Der Projektname **Meadow PIM** steht für den Beginn auf der grünen Wiese: ein System, das Ordnung in wachsende Datenbestände bringt, flexible Strukturen ermöglicht und Informationen organisch miteinander vernetzt.

---

## 2. Gesamtarchitektur

Das System besteht aus mehreren Diensten, die lose gekoppelt über REST und eine Message Queue kommunizieren. Die folgende Darstellung zeigt die Komponenten und ihre Verbindungen.

```
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

Die Architektur bildet die **technische Infrastruktur** ab. Fachliche Strukturen wie Sprachbaum, Datenwertvererbung und Kategorisierung sind **logische Modelle innerhalb des Kerns** und erfordern keine zusätzlichen Dienste. Sie werden in den Abschnitten 4.4 bis 4.7 detailliert beschrieben.
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
| Business Logic              | Pydantic (Validierung), ggf. FastAPI | Business Rules, berechnete Attribute, Validierung von MEF-Dokumenten  |
| Worker                      | asyncio, nats-py, httpx              | Ausführung von Jobs (Import/Export), Webhooks, Status-Updates         |
| Frontend                    | NiceGUI                              | Rollenbasierte Web-Oberfläche                                         |
| Frontend i18n               | gettext + Babel                      | UI-Übersetzungen, Datums-/Zahlen-/Währungsformatierung                |
| HTTP-Client (Worker)        | httpx                                | Aufrufe an den Rust-Kern und an die externe Middleware                |
| PDF/Excel-Export (optional) | ReportLab / openpyxl                 | Spätere Outbound-Konnektoren für PDF, Excel usw.                      |

### 3.3 Infrastruktur & Betrieb

| Komponente                | Technologie                        | Zweck                                                |
|---------------------------|------------------------------------|------------------------------------------------------|
| Containerisierung         | Docker                             | Einheitliche Auslieferung aller Dienste              |
| Orchestrierung (MVP)      | Docker Compose                     | Lokaler Betrieb und KMU-Deployment                   |
| Orchestrierung (optional) | Kubernetes (k3s)                   | Skalierung bei wachsenden Anforderungen              |
| Observability             | tracing (Rust), structlog (Python) | Strukturiertes Logging und Tracing                   |
| Metriken (optional)       | Prometheus + Grafana               | Überwachung von Jobs, Events, API-Performance        |
| Logging (optional)        | Loki / ELK                         | Zentrale Log-Aggregation                             |
| Backup                    | SurrealDB-Export + S3-Sync         | Datensicherung für Objektdatenbank und Objektstorage |

### 3.4 Abgrenzung & Hinweise

- **FastAPI** ist nur dann erforderlich, wenn der Python-Dienst eigene REST-Endpunkte bereitstellen muss. Für die erste Version kommuniziert der Worker ausschließlich über NATS und ruft den Rust-Kern per REST auf; FastAPI kann daher optional bleiben.
- **Redis** ist im MVP nicht zwingend, da die Vererbungs- und Sprachauflösung bei geringen Hierarchietiefen auch ohne Cache performant ist. Es wird als optionale Komponente geführt.
- **Meilisearch/Elasticsearch** sind nicht vorgesehen, da SurrealDB über integrierte Volltextsuche verfügt. Erst bei sehr komplexen Suchanforderungen (z. B. Facetten, Synonyme) könnte eine externe Suchmaschine ergänzt werden.
- **ETIM/Klassifizierungsstandards** benötigen keine eigene Technologie, sondern werden später als Datenmodell-Erweiterung im Kern abgebildet.
- **SurrealDB-Lizenz (BSL 1.1):** Der SurrealDB-Serverkern (v3.x) ist unter der **Business Source License 1.1** lizenziert – „source-available“, kein OSI-Open-Source; SDKs/Clients sind dagegen unter Apache/MIT lizenziert. Die BSL erlaubt:
  - **Entwicklung, Tests, Demo & Evaluierung kostenlos** (ausdrücklich erlaubt),
  - **Produktivbetrieb kostenlos**, solange SurrealDB **nicht als „Database Service“** angeboten wird (d. h. Dritte dürfen über das Angebot keine Schemas/Tabellen erstellen oder verwalten),
  - **Change Date 2030-01-01** → danach vollständig unter **Apache 2.0** (Open Source).
  - Für Meadow PIM (selbst gehostet bei Kunden, normale Anwendungsnutzung) ist die erlaubte Produktionsnutzung damit abgedeckt. Erst bei einem Angebot mit direktem Datenbank-Zugriff für Dritte (DB-as-a-Service) wäre eine kommerzielle Lizenz erforderlich.
- **jsonwebtoken (v10+/v11):** Bei der Dependency-Einrichtung muss eines der Crypto-Backend-Features gewählt werden (`aws_lc_rs` oder `rust_crypto`), sonst schlägt der Build fehl. Zudem liegt die MSRV des Crates bei 1.88.

---

## 4. Datenmodell

Alle fachlichen Entitäten (Produkte, Kategorien, Assets, gespeicherte Suchen, Collections, …) werden als **generische Objekte** mit flexiblen Metadaten gespeichert; Unterschiede entstehen durch den Typ. Technische Tabellen (Benutzer, Gruppen, Attributdefinitionen, Sprachbaum, Jobs, Audit-Log) ergänzen das Kernmodell. Details zu den Tabellen sowie zu Indizierung, Volltextsuche und Graph-Traversierung stehen im Detaildokument.

> **Details:**
> - [datenmodell.md](detail/datenmodell.md) — Tabellen, Indizes & Suche (4.1–4.3)
> - [sprachbaum-mehrsprachigkeit.md](detail/sprachbaum-mehrsprachigkeit.md) — Sprachbaum für sprachabhängige Daten (4.4)
> - [datenwertvererbung.md](detail/datenwertvererbung.md) — Vererbung & Konfliktlösung (4.5, 4.7)
> - [kategorisierung.md](detail/kategorisierung.md) — Kategorisierung und Gruppierung (4.6)

---

## 5. API-Design (OpenAPI REST)

Der Rust-Kern stellt eine vollständige REST-API bereit, dokumentiert mit OpenAPI 3.x. Die vollständige Endpunktliste sowie Authentifizierungs-, Rollen- und Paginationsdetails stehen im Detaildokument.

> **Details:** [detail/api-design.md](detail/api-design.md)

---

## 6. Hintergrundprozesse & Scheduler

Der Scheduler und die Job-Verarbeitung sind für **geplante oder manuell gestartete Aufgaben** zuständig. Der Rust-Kern erzeugt `job_run`-Einträge und publiziert sie an NATS JetStream; der Python-Worker führt die Jobs aus und meldet den Status per REST zurück. Architektur, Ablauf, Job-Typen und Fehlerbehandlung stehen im Detaildokument.

> **Details:** [detail/jobs-scheduler.md](detail/jobs-scheduler.md)

---

## 7. Integration & Eventsystem

Meadow PIM kommuniziert mit externen Systemen über **Inbound- und Outbound-Prozesse**: das einheitliche Austauschformat MEF für Import/Export, ein CRUD-Eventsystem über NATS JetStream und ein Webhook-Outbound an die Middleware. Details zu Format, Event-Typen und Konnektoren stehen im Detaildokument.

> **Details:** [detail/integration-eventsystem.md](detail/integration-eventsystem.md)

---

## 8. Workflow & Freigaben

Es wird **keine schwere Workflow-Engine** eingesetzt. Die Statuslogik ist eine einfache Zustandsmaschine, die im Python Business-Rules-Service implementiert ist; der Rust-Kern speichert nur das Statusfeld (`status`) und protokolliert Änderungen im Audit-Log. Details zu Übergängen und Freigaben stehen im Detaildokument.

> **Details:** [detail/workflow-freigaben.md](detail/workflow-freigaben.md)

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

Die Weboberfläche läuft als separater Docker-Container mit Python + NiceGUI und kommuniziert ausschließlich über die REST-API des Rust-Kerns. Ein Dienst für alle Benutzer; Menüs und Aktionen werden rollenbasiert ein-/ausgeblendet. Details zu Architektur, Mehrsprachigkeit und Kernfunktionen stehen im Detaildokument.

> **Details:** [detail/frontend.md](detail/frontend.md)

---

## 12. Betrieb & Deployment

Alle Komponenten werden als Docker-Container betrieben; die Standardauslieferung erfolgt als **Docker-Compose-Setup**, Kubernetes (k3s) ist vorbereitet. Details zu Containern, Netzwerk, Konfiguration, Skalierung, Backup und Monitoring stehen im Detaildokument. Konkretes Ziel für das erste Produktions-Deployment ist der VPS unter `example.com` mit dem dort laufenden Traefik (externes Docker-Netz `traefik-net`); die Detail-Spezifikation inkl. Runbook steht im Detaildokument.

> **Details:** [detail/betrieb-deployment.md](detail/betrieb-deployment.md)

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
- **Attributdefinition um `inheritable`-Flag erweitern**
- **Referenztypen für Vererbung (`inherits_from`), Kategorisierung (`category_member`) und spätere Collections (`collection_member`) als Konzept festlegen**
- **Tabellen `user` und `group` sowie JWT-Authentifizierung mit den Rollen `superadmin`, `admin`, `editor`, `viewer` vorbereiten**

### Phase 2 – Kernfunktionen: Metadaten, Suche, Vererbung
- Flexible Attributgruppen und -definitionen vollständig implementieren
- Indizes und Volltextsuche konfigurieren
- Referenzen mit Metadaten umsetzen
- **Sprachabhängige Attributauflösung gemäß Sprachbaum implementieren (inkl. `fallback_order`)**
- **Datenwertvererbung über `inherits_from` implementieren (Auflösungslogik, leere Werte als Unterbrechung)**
- **Kategorisierung als normale Objekte mit `category_member`-Relationen umsetzen**
- **Grundlegende Konfliktlösung bei mehrfacher Vererbung (Priorität/Scope)**

### Phase 3 – Graph & Performance
- Graph-Traversierung optimieren, Benchmarks durchführen
- Caching einführen (Redis optional)
- **Performance der Vererbungs- und Sprachauflösung messen und optimieren**
- **Indizes für Vererbungsrelationen und Kategorieabfragen sicherstellen**

### Phase 4 – Python-Integration & Business Rules
- Python-Service für Business Rules (Statusmaschine, Validierungen) aufsetzen
- Event-basierte Kommunikation vorbereiten
- **Business Rules mit Zugriff auf vererbte und sprachlich aufgelöste Werte ausstatten**
- **Schnittstelle für benutzerdefinierte Regeln definieren, die auf dem generischen Objektmodell arbeiten**

### Phase 5 – Hintergrundverarbeitung & Scheduler
- NATS JetStream in die Infrastruktur aufnehmen
- Rust-Kern um Scheduler und Job-Verwaltung erweitern
- Python Worker Service implementieren (Basisframework)
- **Job-Typen für Business Rules, Inbound/Outbound und später für Collection-Refresh vorsehen**

### Phase 6 – Integration & Eventsystem
- **Meadow Exchange Format (MEF) als Standard-Import/Export-Format implementieren**
- **Inbound-Konnektor `json_import` und Outbound-Konnektor `json_export` umsetzen**
- **CRUD-Events im Rust-Kern erzeugen und in NATS JetStream publizieren**
- **Webhook-Outbound-Konnektor mit Filterung und Retry-Logik zur Middleware entwickeln**
- **Events für Objekt-, Referenz- und Attributdefinitionsänderungen abdecken**

### Phase 7 – Frontend (NiceGUI)
- NiceGUI-Anwendung mit Login, Objektliste, Objektbearbeitung
- Admin-Seiten für Attributdefinitionen, Jobs, Benutzer und Gruppen
- **Mehrsprachigkeit der Oberfläche (i18n) mit Übersetzungs-Keys umsetzen (Startsprache Englisch)**
- **Anzeige von vererbten Werten und Herkunft in der Objektbearbeitung**
- **UI-Unterstützung für Sprachpflege (Sprachbaum) und Kategorisierung**

### Phase 8 – Produktionsreife & MVP-Abschluss
- Sicherheit, Monitoring, Logging, Backup
- Load-Testing und Optimierung
- **Dokumentation für MEF und Eventsystem**
- **Bereitstellung als Docker-Compose-Setup für KMUs**

### Phase 9 – Spätere Ausbaustufen (nicht im MVP)
- **Gespeicherte Suchen (`saved_search`) und Materialisierte Collections (`collection_member`) vollständig umsetzen**
- **Erweiterte Konfliktbehandlung in der UI (bevorzugte Quelle, visuelle Markierung)**
- **Kanalspezifische Freigaben (`release`-Tabelle)**
- **Unterstützung für externe Klassifizierungsstandards (z. B. ETIM)**
- **Weitere Sprachen für die Oberfläche (Deutsch, Französisch, …)**
- **SSO-Anbindung (OIDC) inkl. Gruppen-/Claim-Mapping**

---

## 15. Zusammenfassung

Meadow PIM kombiniert einen hochperformanten Rust-Kern (headless) mit SurrealDB für flexible Dokumente und Graphen, NATS JetStream für zuverlässige Hintergrundverarbeitung und Python für Business Rules sowie die Benutzeroberfläche (NiceGUI). Durch die lose Kopplung der Dienste bleibt das System einfach, wartbar und skalierbar – exakt passend für die Anforderungen eines KMUs.

---

*Dokument erstellt am 2026-08-13 – Projektname: Meadow PIM.*

*Zuletzt geändert: 2026-08-15 – Detaildokumente ausgelagert: `plan/detail/` (13 Dokumente); Abschnitte 4–13 im Hauptdokument auf Zusammenfassung + Inline-Link gekürzt.*
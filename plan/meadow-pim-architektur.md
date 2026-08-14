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
- **SurrealDB-Lizenz:** Der SurrealDB-Serverkern ist unter der **Business Source License 1.1 (BSL)** lizenziert (SDKs/Clients unter Apache/MIT). Für den produktiven Betrieb bei Kunden sollte die BSL kurz rechtlich geprüft werden – für KMU-Szenarien in der Regel unkritisch, aber dokumentierenswert.
- **jsonwebtoken (v10+/v11):** Bei der Dependency-Einrichtung muss eines der Crypto-Backend-Features gewählt werden (`aws_lc_rs` oder `rust_crypto`), sonst schlägt der Build fehl. Zudem liegt die MSRV des Crates bei 1.88.

---

## 4. Datenmodell

### 4.1 Grundprinzip

Alle fachlichen Entitäten (Produkte, Kategorien, Assets, gespeicherte Suchen, Collections, …) werden als **generische Objekte** gespeichert. Unterschiede ergeben sich durch den Typ und flexible Metadaten. Technische Tabellen (Attributdefinitionen, Sprachbaum, Jobs, Audit-Log) ergänzen das Kernmodell.

### 4.2 Tabellen in SurrealDB

#### Tabelle `object`

```json
{
    "id": "object_123",
    "type": "product",               // z. B. "product", "category", "asset", "saved_search", "collection"
    "attributes": {                  // flexible Metadaten als JSON
        "name": {
            "de": "Beispielprodukt",
            "en": "Example product"
        },
        "price": 19.99,
        "color": "red"
    },
    "status": "draft",               // draft | in_review | approved | published | archived
    "version": 3,
    "created_at": "...",
    "updated_at": "...",
    "updated_by": "user_1"
}
```

- `type` ist frei wählbar und bestimmt die fachliche Rolle des Objekts.
- Sprachabhängige Werte werden als verschachtelte JSON-Objekte mit Sprachschlüsseln gemäß Sprachbaum (Abschnitt 4.4) gespeichert.
- `status` dient dem einfachen Workflow (Abschnitt 8).

#### Tabelle `attribute_definition`

Definiert Metadaten-Attribute (Name, Typ, Gruppe, Validierung, Wertelisten, Mehrsprachigkeit, Vererbbarkeit).

```json
{
    "id": "attr_def_001",
    "name": "price",
    "label": { "de": "Preis", "en": "Price" },
    "type": "number",                // text, number, boolean, date, enum, reference, ...
    "group_id": "attr_group_01",
    "inheritable": true,             // true: Wert kann über Vererbung bezogen werden
    "constraints": {
        "min": 0,
        "max": 999999,
        "enum_values": ["red", "green", "blue"],   // optional für geschlossene Wertelisten
        "multilingual": true                        // optional, kennzeichnet sprachabhängige Felder
    }
}
```

- `inheritable` steuert, ob das Attribut in die Datenwertvererbung (Abschnitt 4.5) einbezogen wird.
- Weitere optionale Felder: `multilingual`, `enum_values`, `default_value`, `unit`, `description`.

#### Tabelle `attribute_group`

Gruppiert Attributdefinitionen für die UI.

```json
{
    "id": "attr_group_01",
    "name": "pricing",
    "label": { "de": "Preise", "en": "Pricing" },
    "position": 2
}
```

#### Tabelle `reference`

Referenzen zwischen Objekten (Graph-Kanten) mit eigenen Metadaten. Der `type` ist frei definierbar; bestimmte Typen aktivieren spezielle Verhaltensweisen:

- `inherits_from` → Datenwertvererbung (Abschnitt 4.5)
- `category_member` → Kategorisierung (Abschnitt 4.6)
- `collection_member` → materialisierte Collections (Abschnitt 9.2)
- weitere Typen (z. B. `related`, `parent`, `variant`) sind reine Verknüpfungen ohne Sonderlogik.

```json
{
    "id": "ref_001",
    "from": "object_123",            // Quellobjekt-ID
    "to": "object_456",              // Zielobjekt-ID
    "type": "inherits_from",         // oder related, category_member, collection_member, ...
    "attributes": {                  // Metadaten an der Kante
        "priority": 1,               // z. B. bei Vererbung: niedriger Wert = höhere Priorität
        "position": 3                // z. B. bei Kategorien: Sortierung
    }
}
```

#### Tabelle `language_tree`

Definiert den Sprachbaum für sprachabhängige Attributwerte (Details in Abschnitt 4.4).

```json
{
    "id": "lang_en-GB",
    "code": "en-GB",
    "parent": "lang_en",
    "label": { "en": "English (United Kingdom)", "de": "Englisch (UK)" },
    "fallback_order": ["en", "root"]
}
```

#### Tabelle `job_definition`

Definiert wiederkehrende oder manuell ausführbare Aufgaben (Details in Abschnitt 6).

```json
{
    "id": "job_def_123",
    "name": "Täglicher Produktexport",
    "type": "outbound_export",       // outbound_export | inbound_import | business_rule
    "schedule": "0 0 3 * * *",       // Cron (6 Felder, Sekunden zuerst), null = nur manuell
    "parameters": {
        "connector_id": "conn_amazon",
        "filter": { "type": "product", "active": true }
    },
    "enabled": true,
    "created_at": "...",
    "updated_at": "..."
}
```

#### Tabelle `job_run`

Protokolliert jeden Ausführungsversuch (Details in Abschnitt 6).

```json
{
    "id": "job_run_456",
    "job_definition_id": "job_def_123",
    "trigger": "schedule",           // schedule | manual | event
    "status": "success",             // pending | running | success | failed | retry
    "attempt": 2,
    "started_at": "...",
    "finished_at": "...",
    "result": { "exported_objects": 150 },
    "error": null,
    "worker_id": "worker-1"
}
```

#### Tabelle `audit_log`

Protokolliert Änderungen und Statuswechsel (Details in Abschnitt 12).

```json
{
    "id": "audit_001",
    "object_id": "object_123",
    "user_id": "user_1",
    "action": "status_changed",
    "from": "in_review",
    "to": "approved",
    "timestamp": "...",
    "metadata": {
        "request_id": "req_456"
    }
}
```

#### Zukünftige Tabellen (nicht im MVP, aber konzeptionell vorgesehen)

- **`saved_search`**: Speichert komplexe Suchanfragen (Abschnitt 9.1).
- **`collection`**: Materialisierte Ergebnislisten auf Basis gespeicherter Suchen (Abschnitt 9.2).
- **`release`**: Kanalspezifische Freigaben (Abschnitt 8.2, spätere Ausbaustufe).

### 4.3 Indizes & Suche

- **Objekt-Indizes:**
  - auf `type`, `status`, `created_at`, `updated_at`
  - auf häufig gefilterte Attributfelder, z. B. `attributes.name.de`, `attributes.price`
  - auf sprachabhängige Felder je nach Konfiguration (z. B. alle Sprachen oder nur `root`/`en`)
- **Referenz-Indizes:**
  - auf `type`, `from`, `to`
  - kombinierte Indizes für `from` + `type` und `to` + `type` (z. B. für Kategorieabfragen)
  - speziell für `inherits_from` und `category_member`
- **Volltextsuche:**
  - SurrealDB-eigene Volltextindizes auf ausgewählte Attributfelder (Syntax `FULLTEXT ANALYZER …`, seit SurrealDB 3.0; zuvor `SEARCH ANALYZER`)
  - Wichtig: Ein `FULLTEXT`-Index deckt **immer nur ein Feld** ab – pro Feld (bzw. pro Sprachfeld wie `attributes.description.de`) ist ein eigener `DEFINE INDEX` nötig
  - sprachspezifische Suche unter Berücksichtigung des Sprachbaums
- **Graph-Traversierung:**
  - Indizes auf Relationen ermöglichen performante Traversierung in beide Richtungen
  - Da alle Relationen in einer generischen `reference`-Tabelle mit `type`-Feld liegen, erfolgt die Traversierung über typisierte Edge-Queries, z. B. `->(reference WHERE type = 'inherits_from')->object` bzw. `<-()<-object` in Gegenrichtung
- **Weitere Indizes (vorbereitet):**
  - auf `saved_search.status`, `collection.status` für spätere Funktionen

### 4.4 Sprachbaum für sprachabhängige Daten

Für sprachabhängige Attributwerte (z. B. `name`, `description`) wird ein **hierarchischer Sprachbaum** verwendet. Er bildet sowohl Sprachen als auch regionale Varianten ab und erlaubt die **Vererbung von Werten** von allgemeineren Knoten zu spezifischeren.

**Aufbau des Sprachbaums:**

- **Wurzel (`root`)**: Der globale Ausgangspunkt, z. B. `root` oder ein universeller Fallback.
- **Sprachknoten (ISO 639-1)**: z. B. `en`, `de`, `fr`.
- **Regionale Varianten (ISO 3166-1 Alpha-2)**: z. B. `en-GB`, `en-US`, `de-DE`, `de-AT`, `fr-FR`, `fr-CA`.

**Hierarchie:**

```
root
├── en
│   ├── en-GB
│   └── en-US
├── de
│   ├── de-DE
│   └── de-AT
└── fr
    ├── fr-FR
    └── fr-CA
```

**Vererbungsregeln:**

1. Wenn ein Attributwert für den exakten Sprachcode (z. B. `en-US`) vorhanden ist, wird dieser verwendet.
2. Ist kein Wert für den exakten Code gesetzt, wird im **direkten Elternknoten** (z. B. `en`) nachgesehen.
3. Ist auch dort kein Wert vorhanden, wird weiter bis zur Wurzel (`root`) aufgestiegen.
4. Erst wenn auch die Wurzel keinen Wert enthält, gilt der Wert als **nicht vorhanden** (null).

**Beispiel:**

- Attribut `name` hat Werte:

  ```json
  {
    "root": "Produkt",
    "en": "Product",
    "en-GB": "Product (UK)",
    "de": "Produkt"
  }
  ```

- Anfrage mit Sprache `en-US` → kein exakter Treffer → erbt von `en` → ergibt `"Product"`.
- Anfrage mit Sprache `en-GB` → exakter Treffer → ergibt `"Product (UK)"`.
- Anfrage mit Sprache `fr-FR` → kein Treffer unter `fr` → erbt von `root` → ergibt `"Produkt"`.

**Datenhaltung in SurrealDB:**

- Die sprachabhängigen Werte werden weiterhin als **JSON-Objekt im `attributes`-Feld** des Objekts gespeichert, wobei die Schlüssel den Knoten des Sprachbaums entsprechen (z. B. `"name": { "root": "...", "en": "...", "en-GB": "..." }`).
- Die Struktur des Sprachbaums selbst wird in einer eigenen Tabelle `language_tree` abgelegt, um die Vererbung flexibel konfigurieren zu können:

  ```json
  {
    "id": "lang_en-GB",
    "code": "en-GB",
    "parent": "lang_en",
    "label": { "en": "English (United Kingdom)", "de": "Englisch (UK)" },
    "fallback_order": ["en", "root"]
  }
  ```

- Der `fallback_order` wird beim Anlegen/Ändern des Baums berechnet und gespeichert, um die Auflösung zur Lesezeit zu beschleunigen (kein rekursives Traversieren in Echtzeit).

**Auflösung in der API:**

- Beim Lesen eines Objekts kann über den Query-Parameter `?locale=en-US` die gewünschte Sprache angegeben werden.
- Der Rust-Kern (oder vorgelagerte Python-Service) löst die sprachabhängigen Felder anhand des Sprachbaums auf und liefert die **effektiven Werte** im Antwort-JSON zurück.
- In der Standardausgabe bleibt das vollständige Sprachobjekt erhalten (optional), damit alle Varianten sichtbar bleiben. Für die UI und Exporte wird die aufgelöste Variante verwendet.

**Behandlung von Änderungen:**

- Wird ein Wert in einem übergeordneten Knoten geändert (z. B. `en`), erben alle untergeordneten Knoten ohne eigenen Wert automatisch die Änderung.
- Wird ein Wert in einem spezifischen Knoten gesetzt, überschreibt er den geerbten Wert für diesen Knoten.
- Das Löschen eines Wertes in einem Knoten entfernt nur diesen expliziten Eintrag – die Vererbung greift danach wieder für alle Kindknoten ohne eigenen Wert.

**Performance-Aspekte:**

- Durch die gespeicherte `fallback_order` ist die Auflösung O(depth) mit sehr geringer Tiefe (max. 3–4 Ebenen). Dies ist vernachlässigbar gegenüber der Datenbankabfrage.
- Optional kann ein Cache für aufgelöste Werte (z. B. Redis) eingesetzt werden, wenn sehr viele Lesevorgänge mit unterschiedlichen Sprachen auftreten. Für KMU-Szenarien ist das in der Regel nicht erforderlich.

**Mehrsprachigkeit der Oberfläche vs. Daten:**

- Die Sprachbaum-Logik betrifft ausschließlich **fachliche Attributwerte** (Produktname, Beschreibung, etc.).
- Die Übersetzung der Benutzeroberfläche (Menü, Buttons) bleibt davon getrennt und folgt dem in Abschnitt 8.3 beschriebenen Ansatz.

### 4.5 Datenwertvererbung über Objektrelationen

Meadow PIM unterstützt die **Vererbung von Attributwerten** entlang hierarchischer Objektbeziehungen. Dies ist besonders relevant für Gruppierungsobjekte (z. B. ein Produkt) und darunterliegende Varianten (z. B. Produktvarianten). Dabei wird ein Wert **nicht kopiert**, sondern dynamisch aufgelöst – Änderungen am übergeordneten Objekt wirken sich sofort auf alle erbenden Kindobjekte aus, sofern diese keinen eigenen Wert gesetzt haben.

**Zielsetzung:**

- Gemeinsame Attribute zwischen Eltern- und Kindobjekten müssen nur an einer Stelle gepflegt werden.
- Die Vererbung ist **echt**: Es wird zur Lesezeit der effektive Wert berechnet, nicht beim Schreiben dupliziert.
- Es muss möglich sein, die Vererbung gezielt zu unterbrechen, indem am Kind ein **leerer Wert** gesetzt wird (Unterscheidung zwischen „nicht gepflegt“ und „leer“).

**Modellierung der Vererbungsrelation:**

- Die bestehende, leichtgewichtige `reference`-Tabelle bleibt für allgemeine Verknüpfungen erhalten.
- Für die Vererbung wird ein eigener Relationstyp `inherits_from` verwendet. Die Richtung ist definiert als: **Kind verweist auf Elternteil**.

  ```json
  {
    "id": "ref_inh_001",
    "from": "variant_1",             // Kindobjekt
    "to": "product_1",               // Elternobjekt
    "type": "inherits_from",         // spezieller Vererbungstyp
    "attributes": {
      "priority": 1                  // optional: Reihenfolge bei mehreren Eltern
    }
  }
  ```

- Über SurrealDB-Graphfunktionen kann entlang dieser Relationen effizient traversiert werden. Da die Relationen in der generischen `reference`-Tabelle liegen, wird der Typ als Bedingung angegeben: `->(reference WHERE type = 'inherits_from')->object`.
- Ein Objekt kann mehrere `inherits_from`-Relationen besitzen. In diesem Fall gilt die durch `priority` definierte Reihenfolge (niedriger Wert = höhere Priorität). Ist keine Priorität gesetzt, wird die zuerst gefundene Relation verwendet.

**Attributkategorien:**

- In der `attribute_definition` wird ein Feld `inheritable` ergänzt:
  - `true`: Das Attribut ist vererbungsfähig und wird bei der Auflösung berücksichtigt.
  - `false` (Standard): Das Attribut ist **nicht vererbbar** – es wird ausschließlich am Objekt selbst ausgelesen.

  ```json
  {
    "id": "attr_def_001",
    "name": "price",
    "type": "number",
    "group_id": "attr_group_01",
    "inheritable": true
  }
  ```

- Dadurch kann die Performance gezielt gesteuert werden: Nur als `inheritable` markierte Attribute erfordern eine Graph-Traversierung.

**Auflösungslogik (Lesen eines Objekts):**

1. Für jedes Attribut wird zuerst der **eigene Wert** des Objekts geprüft.
2. Ist der Schlüssel im `attributes`-Objekt **vorhanden** (auch mit `null` oder leerem String), gilt er als gesetzt – die Vererbung greift **nicht**. Ein leerer Wert unterbricht also bewusst die Vererbung.
3. Fehlt der Schlüssel („nicht gepflegt“), wird entlang der `inherits_from`-Kette aufwärts gesucht. Der nächstgelegene Elternknoten, der das Attribut gesetzt hat, liefert den Wert.
4. Wird kein gesetzter Wert in der gesamten Kette gefunden, bleibt das Attribut für das Kind **leer/nicht vorhanden**.

**Beispiel:**

- Objekt `product_1` (Eltern) hat:
  ```json
  {
    "attributes": {
      "brand": "Acme",
      "material": "Edelstahl"
    }
  }
  ```
- Objekt `variant_1` (Kind) hat:
  ```json
  {
    "attributes": {
      "material": null          // leer gesetzt => unterbricht Vererbung
    }
  }
  ```
- Ergebnis für `variant_1`:
  - `brand` wird vererbt → `"Acme"`
  - `material` ist explizit leer → bleibt `null`
  - Andere Attribute des Kindes werden unverändert übernommen.

**Kombination mit dem Sprachbaum (4.4):**

- Die sprachliche Auflösung (z. B. `name` mit `en-GB`-Fallback) erfolgt **zuerst innerhalb jedes einzelnen Objekts**.
- Anschließend wird die Objektvererbung angewendet: Wenn ein Kind keinen sprachabhängigen Wert besitzt, erbt es den bereits sprachlich aufgelösten Wert des Elternobjekts.
- Dadurch sind beide Mechanismen entkoppelt und bleiben verständlich.

**Technische Umsetzung in SurrealDB:**

- Indizes auf `reference` (insbesondere `type` und `from`) beschleunigen die Traversierung.
- Die Vererbung wird über SurrealQL-Abfragen realisiert, z. B.:
  ```sql
  SELECT ->(reference WHERE type = 'inherits_from')->object.attributes.brand AS inherited_brand
  FROM object:variant_1
  ```
  oder durch schrittweises Aufsteigen in der Applikationslogik.
- Optional kann ein Cache (z. B. Redis) die aufgelösten Werte für häufig gelesene Objekte zwischenspeichern. Für die meisten KMU-Szenarien ist das jedoch nicht erforderlich, da die Hierarchietiefen gering sind.

**API-Verhalten:**

- Beim Abruf eines Objekts über `GET /objects/{id}` kann der Client mit dem Query-Parameter `?resolve_inheritance=true` angeben, dass die effektiven Werte (inkl. Vererbung) geliefert werden sollen.
- Ohne diesen Parameter werden nur die am Objekt direkt gespeicherten Werte zurückgegeben (Rohdaten).
- Für die UI und Exporte wird standardmäßig die aufgelöste Sicht verwendet.

**Vorteile:**

- Reduzierte Datenpflege: Gemeinsame Werte werden zentral am Elternobjekt gepflegt.
- Konsistenz: Änderungen am Elternobjekt propagieren sofort zu allen Kindobjekten.
- Flexibilität: Durch explizites Setzen eines leeren Wertes kann die Vererbung punktuell ausgehebelt werden.
- Performance: Nur vererbungsfähige Attribute werden in die Graph-Traversierung einbezogen.

**Abgrenzung zu allgemeinen Relationen:**

- Normale `reference`-Relationen (z. B. `related`, `parent`, `variant`) dienen rein der Verknüpfung und haben **keine** Vererbungswirkung.
- Nur Relationen vom Typ `inherits_from` aktivieren die beschriebene Wertvererbung.

### 4.6 Kategorisierung und Gruppierung

Meadow PIM unterstützt die **Kategorisierung und Gruppierung von Objekten**, ohne dafür neue technische Entitäten einzuführen. Kategorien sind ganz normale Objekte, die über Relationen mit den zugeordneten Objekten verbunden werden. Dadurch bleibt das Datenmodell schlank und nutzt die bereits vorhandenen Graph-Fähigkeiten.

**Grundprinzip:**

- Eine **Kategorie** wird als Objekt mit `type: "category"` (oder einem frei wählbaren Typ wie `grouping`, `folder`, `collection`) angelegt.
- Die Zuordnung eines Objekts zu einer Kategorie erfolgt über eine Relation vom Typ `category_member`.

  ```json
  {
    "id": "ref_cat_001",
    "from": "object_123",          // das zugeordnete Objekt (z. B. Produkt)
    "to": "category_5",            // die Kategorie
    "type": "category_member",
    "attributes": {
      "position": 3,               // optionale Sortierung innerhalb der Kategorie
      "context": "webshop"         // optional: nur für bestimmten Kanal relevant
    }
  }
  ```

- Objekte können **mehreren Kategorien** gleichzeitig zugeordnet sein (Mehrfachkategorisierung). Die `attributes` an der Relation erlauben eine kontextabhängige Sortierung oder Gültigkeit.

**Kategorien als normale Objekte:**

- Kategorien besitzen selbst flexible Attribute und können über `inherits_from`-Relationen hierarchisch aufgebaut sein (z. B. `Elektronik` → `Audio` → `Kopfhörer`).
- Da sie reguläre Objekte sind, profitieren sie automatisch von:
  - Mehrsprachigkeit (z. B. Kategoriename in mehreren Sprachen über den Sprachbaum)
  - Datenwertvererbung (z. B. Standardbeschreibung von übergeordneten Kategorien)
  - Status/Workflow (z. B. Kategorie erst nach Freigabe sichtbar)
  - Audit-Log und Versionierung

**Abgrenzung zur Pflegestruktur:**

- Die **Pflegestruktur** (z. B. Produkt → Varianten über `inherits_from`) dient der Datenhaltung und Wertvererbung.
- Die **Kategorisierung** ordnet Objekte unabhängig von der Pflegestruktur neu an – etwa für Navigation, Marketing oder externe Klassifizierung.
- Beide Strukturen können parallel existieren und sich überlagern. Ein Objekt kann gleichzeitig in einer Vererbungshierarchie und in mehreren Kategoriebäumen hängen.

**Typische Anwendungsfälle:**

1. **Navigationsstruktur** für den Webshop: Kategorien wie „Neuheiten“, „Sale“, „Zubehör“.
2. **Fachliche Gliederung** unabhängig von der technischen Ablage: z. B. Produkte nach Zielgruppe oder Saison.
3. **Vorbereitung für Standards (z. B. ETIM):** Kategorien können später um ein Attribut `standard_code` ergänzt werden, um externe Klassifikationssysteme abzubilden. Die Zuordnung erfolgt dann weiterhin über Relationen, zusätzlich könnte ein Mapping auf Standardklassen entstehen. Dies ist bewusst nicht Teil der ersten Version, aber das Modell ist dafür offen.

**Anforderungen an die Implementierung:**

- **Indizes** auf `reference.type` und `reference.to` sorgen für schnelle Abfragen der Kategorien eines Objekts sowie aller Objekte einer Kategorie.
- **Rekursive Abfragen** müssen effizient sein: z. B. alle Produkte inklusive Unterkategorien. SurrealDB bietet dafür Graph-Traversierung über die generische `reference`-Tabelle (z. B. `->(reference WHERE type = 'category_member')->object` in beide Richtungen).
- **Sortierung:** Die optionale `position` an der Relation wird beim Lesen ausgewertet, um eine konfigurierbare Reihenfolge innerhalb der Kategorie zu ermöglichen.
- **Caching:** Für häufig gelesene Kategoriebäume (Navigation) kann ein Cache eingesetzt werden. Für KMU-Größenordnungen ist das meist nicht erforderlich, da die Baumtiefen gering sind.

**Ausblick auf Klassifizierungsstandards:**

- In späteren Versionen können Kategorien mit externen Klassifikationssystemen verknüpft werden, z. B. durch ein eigenes Objekt `classification_system` und Relationen `maps_to`.
- Für ETIM ließe sich die Kategorie um ein Attribut `etim_class` erweitern und die Zuordnung automatisiert überprüfen.
- Die Architektur ist so gewählt, dass solche Erweiterungen ohne Bruch des Kernmodells möglich sind.

### 4.7 Konfliktlösung bei mehrfacher Vererbung

Durch die parallele Einordnung von Objekten in mehrere Strukturen (Pflegehierarchie, Kategorien, spätere Klassifizierung) kann der Fall auftreten, dass für dasselbe Attribut **mehrere potenzielle Erbquellen** existieren. Diese liefern dann möglicherweise unterschiedliche Werte. Meadow PIM löst solche Konflikte mit einer Kombination aus **deterministischen Regeln** und **manuellen Eingriffsmöglichkeiten**.

**Grundsatz:**

- Konflikte werden **nicht verhindert**, sondern kontrolliert aufgelöst.
- Die Auflösung erfolgt **zur Lesezeit**, ohne Werte zu duplizieren.
- Der Benutzer kann jederzeit einen **expliziten Wert am Kind** setzen, um die Vererbung gezielt zu überschreiben – das ist die mächtigste und üblichste Methode.

**1. Deterministische Prioritätsregeln**

Jede `inherits_from`-Relation kann eine Priorität erhalten:

```json
{
  "id": "ref_inh_001",
  "from": "variant_1",
  "to": "product_1",
  "type": "inherits_from",
  "attributes": {
    "priority": 10,
    "scope": "maintenance"
  }
}
```

- **`priority`** (niedriger Wert = höhere Priorität) legt die Reihenfolge fest, wenn mehrere Relationen desselben Geltungsbereichs existieren.
- **`scope`** unterscheidet die Art der Beziehung, z. B. `maintenance` (Pflegestruktur), `category` (Kategorisierung) oder `classification` (spätere Standards).
- Bei der Auflösung wird zuerst die Relation mit dem **kleinsten `priority`-Wert** im **höchstpriorisierten `scope`** verwendet. Die `scope`-Reihenfolge ist systemweit definiert (z. B. `maintenance` vor `category` vor `classification`).
- Sind mehrere Relationen gleich priorisiert, entscheidet die zuerst gefundene (stabile Reihenfolge, z. B. nach `id`).

**2. Manuelle Konfliktbehandlung in der UI**

Auch mit Prioritätsregeln kann es für Benutzer wichtig sein, die tatsächliche Quelle eines geerbten Wertes zu verstehen und bei Bedarf zu übersteuern.

- Die Oberfläche zeigt bei vererbten Werten an, **von welchem Elternobjekt** der Wert stammt.
- Besteht ein Konflikt (mehrere Quellen liefern unterschiedliche Werte), wird dies visuell markiert (z. B. Warnsymbol).
- Der Benutzer hat dann zwei Möglichkeiten:
  1. **Expliziten Wert am Kind setzen** – damit greift die Vererbung für dieses Attribut nicht mehr (Standard-Override).
  2. **Bevorzugte Quelle wählen** – dies kann als dauerhafte Einstellung am Kind gespeichert werden, z. B. in einem Attribut `preferred_inheritance_source`, das die Relation-ID oder das Quellobjekt angibt. Zukünftige Lesevorgänge berücksichtigen diese Präferenz.

**3. Vererbungsunterbrechung auf Relationsebene**

Falls eine bestimmte `inherits_from`-Relation für einzelne Attribute oder generell nicht vererben soll, kann das über ein Flag an der Relation gesteuert werden:

```json
{
  "attributes": {
    "priority": 5,
    "disabled_for": ["price", "description"]   // optional: Liste von Attributen, die nicht vererbt werden
  }
}
```

- `disabled_for` verhindert die Vererbung der genannten Attribute über diese Relation.
- Ist die Liste leer oder nicht vorhanden, werden alle vererbbaren Attribute berücksichtigt.

**4. Transparenz und Audit**

- Jede Änderung an der Vererbungskonfiguration (Priorität, Deaktivierung, bevorzugte Quelle) wird im `audit_log` protokolliert.
- In der API-Antwort kann das aufgelöste Attribut um Metadaten ergänzt werden, z. B.:

  ```json
  {
    "brand": {
      "value": "Acme",
      "source": "inherited",
      "from_object": "product_1",
      "resolved_locale": "en"
    }
  }
  ```

  Dadurch bleibt die Herkunft nachvollziehbar.

**Empfehlung für Meadow PIM:**

- In der **ersten Version** genügt eine einfache Prioritätsregel (`priority` und `scope`) sowie die Möglichkeit, am Kind explizite Werte zu setzen. Das deckt die meisten KMU-Szenarien ab.
- Die **UI-Konfliktanzeige** wird früh mitgeplant, kann aber in einer zweiten Ausbaustufe verfeinert werden.
- Die Speicherung einer bevorzugten Quelle (`preferred_inheritance_source`) ist optional und kann später ergänzt werden, wenn die Praxis es erfordert.

Durch diesen Ansatz bleibt die Vererbung flexibel, transparent und beherrschbar – ohne die Komplexität unnötig zu erhöhen.

---

## 5. API-Design (OpenAPI REST)

Der Rust-Kern stellt eine vollständige REST-API bereit, dokumentiert mit OpenAPI 3.x.

### 5.1 Wichtige Endpunkte (Auszug)

| Methode | Pfad                              | Beschreibung                                    |
|---------|-----------------------------------|-------------------------------------------------|
| POST    | `/auth/login`                     | JWT-Token erhalten                              |
| POST    | `/objects`                        | Neues Objekt anlegen                            |
| GET     | `/objects/{id}`                   | Objekt inkl. Metadaten und Referenzen laden     |
| PATCH   | `/objects/{id}`                   | Teilaktualisierung der Attribute/Status         |
| DELETE  | `/objects/{id}`                   | Objekt löschen (soft delete)                    |
| GET     | `/objects?type=product&filter=...`| Suche & Filterung mit Pagination                |
| POST    | `/references`                     | Referenz zwischen zwei Objekten erstellen       |
| GET     | `/objects/{id}/references`        | Alle Referenzen eines Objekts (mit Metadaten)   |
| GET     | `/objects/{id}/graph?depth=3`     | Graph-Traversierung ab Objekt                   |
| GET     | `/attribute-groups`               | Attributgruppen verwalten                       |
| POST    | `/attribute-definitions`          | Neue Attributdefinition anlegen                 |
| POST    | `/jobs/{job_def_id}/run`          | Job manuell starten                             |
| GET     | `/jobs/{job_def_id}/runs`         | Job-Historie abrufen                            |
| PATCH   | `/jobs/runs/{run_id}`             | Status eines Job-Runs aktualisieren (Worker)    |
| POST    | `/search`                         | Volltextsuche über Objekte                      |

- **Authentifizierung**: JWT-Bearer-Token im Authorization-Header.
- **Rollen**: `admin`, `editor`, `viewer` – werden im JWT abgebildet.
- **Pagination**: Cursor-basiert für große Listen.

---

## 6. Hintergrundprozesse & Scheduler

### 6.1 Architektur

Der Scheduler und die Job-Verarbeitung sind für **geplante oder manuell gestartete Aufgaben** zuständig. CRUD-Events werden separat über das Eventsystem (Abschnitt 7) verarbeitet und sind nicht Teil dieses Abschnitts.

```
┌──────────────────────────────────────────────────────────────┐
│                     Rust-Kern (Scheduler)                    │
│                                                              │
│  – Lädt aktive job_definition aus SurrealDB                  │
│  – Prüft Cron-Ausdrücke (tokio-cron-scheduler)               │
│  – Erzeugt job_run und publiziert Message an NATS            │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
               │ Publish Job-Messages          │ Read/Write Job-Runs
               ▼                               ▼
       ┌────────────────┐               ┌───────────────┐
       │  NATS JetStream│               │   SurrealDB   │
       │  (WorkQueue)   │               │ (Job Tabellen)│
       └───────┬────────┘               └───────────────┘
               │ Subscribe
               ▼
┌──────────────────────────────────────────────────────────────┐
│                     Python Worker Service                    │
│                                                              │
│  – NATS-Client (nats-py, asyncio)                            │
│  – Führt Jobs aus (Business Rules, Import/Export)            │
│  – Sendet Status-Updates an Rust-Kern (REST)                 │
└──────────────────────────────┬───────────────────────────────┘
                               │ REST (Status-Updates, Datenabruf)
                               ▼
                     ┌─────────────────────┐
                     │    PIM Core (REST)  │
                     └─────────────────────┘
```

### 6.2 Ablauf eines Jobs

> **Cron-Format:** `tokio-cron-scheduler` nutzt ein **6-Felder-Format mit Sekunden an erster Stelle** (`sec min hour day month dow`). „Täglich um 03:00 Uhr“ lautet also `0 0 3 * * *` (nicht das übliche 5-Felder-Format `0 3 * * *`).

1. Der Scheduler (oder ein manueller API-Aufruf) erzeugt ein `job_run` mit Status `pending`.
2. Der Rust-Kern publiziert eine Message in den entsprechenden NATS-JetStream-Stream (Subject: `jobs.<type>`).
3. Ein Python-Worker empfängt die Message, bestätigt sie (Ack) und setzt den Status des `job_run` **per REST-Aufruf** auf `running`.
4. Der Worker führt die eigentliche Aufgabe aus.
5. Nach Abschluss aktualisiert der Worker den Status **per REST-Aufruf** (`PATCH /jobs/runs/{run_id}`) auf `success` oder `failed`.
6. Bei Fehlern wird die Message nicht geackt; JetStream liefert sie nach einem Timeout erneut zu (Retry). Nach maximal 5 Versuchen wird die Message in den Dead-Letter-Stream verschoben. Der Rust-Kern überwacht diesen Stream und markiert den zugehörigen `job_run` als endgültig `failed`.

### 6.3 Job-Typen

- **outbound_export**: Exportiert Daten über einen Konnektor (CSV, JSON, API, SFTP).
- **inbound_import**: Importiert Daten aus externen Quellen.
- **business_rule**: Führt eine Business Rule auf gefilterten Objekten aus (z. B. Berechnung von Attributen).

Für spätere Ausbaustufen sind weitere Job-Typen vorgesehen (z. B. `collection_refresh` für materialisierte Collections). Der Mechanismus bleibt identisch.

### 6.4 Fehlerbehandlung & Wiederholung

- **Retries**: NATS JetStream wiederholt die Zustellung bei nicht erfolgtem Acknowledgment. Der Worker kann zusätzlich bei bestimmten Fehlern selbst entscheiden, die Nachricht abzulehnen, um eine sofortige Wiederholung zu vermeiden.
- **Dead-Letter-Queue (DLQ)**: Nach Erreichen der maximalen Zustellversuche landet die Nachricht automatisch im DLQ-Stream. Der Rust-Kern abonniert diesen Stream und markiert die betroffenen `job_run`-Einträge als `failed`.
- **Timeout**: Wird ein Worker nicht innerhalb der konfigurierten `AckWait`-Zeit aktiv, stellt JetStream die Nachricht erneut zu.
- **Monitoring**: Alle Job-Runs und Dead-Letter-Ereignisse werden in SurrealDB gespeichert und können über die API ausgelesen werden.
---

## 7. Integration & Eventsystem

Meadow PIM kommuniziert mit externen Systemen über **Inbound- und Outbound-Prozesse**. In der ersten Version genügt die Anbindung an eine bestehende Middleware, die komplexe Transformations- und Verteilungsaufgaben übernimmt. Meadow PIM liefert dafür ein einheitliches JSON-Austauschformat und ein Eventsystem, das Änderungen zuverlässig an die Middleware weiterleitet.

### 7.1 Standard-Import/Export-Format (Meadow Exchange Format, MEF)

Für den Import und Export wird ein generisches JSON-Format verwendet, das alle Kernentitäten (Objekte, Referenzen, ggf. Attributdefinitionen) abbildet. Es dient als **einziges Austauschformat** für die Inbound-/Outbound-Konnektoren der ersten Version.

**Struktur eines MEF-Dokuments:**

```json
{
  "format_version": "1.0",
  "source": "middleware",
  "created_at": "2026-08-14T10:00:00Z",
  "objects": [
    {
      "id": "object_123",
      "type": "product",
      "attributes": {
        "name": {
          "en": "Example product",
          "de": "Beispielprodukt"
        },
        "price": 19.99,
        "status": "draft"
      },
      "status": "draft",
      "references": [
        {
          "to": "category_5",
          "type": "category_member",
          "attributes": { "position": 3 }
        }
      ]
    }
  ],
  "references": [
    {
      "from": "object_123",
      "to": "object_456",
      "type": "related",
      "attributes": { "relation_strength": 0.8 }
    }
  ]
}
```

**Regeln:**

- `objects` enthält die zu importierenden Objekte mit ihren Attributen und optionalen Referenzen.
- `references` enthält Referenzen, die unabhängig von Objekten verarbeitet werden (z. B. nachträgliche Verknüpfungen).
- Sprachabhängige Werte werden als verschachtelte JSON-Objekte mit Sprachschlüsseln gemäß dem Sprachbaum (Abschnitt 4.4) abgebildet.
- `status` am Objekt kann gesetzt werden, um Workflow-Status zu importieren. Fehlt es, gilt der Standard `draft`.
- Nicht übergebene Attribute bleiben unverändert (bei Update) bzw. leer (bei Neuanlage).
- Die ID ist optional: Fehlt sie, wird eine neue vergeben. Vorhandene IDs ermöglichen Upserts.

**Verwendung im Inbound:**

- Der Inbound-Konnektor `json_import` akzeptiert MEF-Dokumente (z. B. aus Datei oder HTTP).
- Der Python-Worker validiert die Struktur, führt Business Rules aus und übergibt die Objekte/Referenzen an den Rust-Kern.

**Verwendung im Outbound:**

- Der Outbound-Konnektor `json_export` erzeugt MEF-Dokumente aus ausgewählten Objekten (Filter wie Typ, Status, Kategorie).
- Die Exportdatei kann per Datei, HTTP oder Messaging an die Middleware übergeben werden.

### 7.2 Eventsystem

Jede CRUD-Operation im Rust-Kern erzeugt ein **Event**, das Änderungen am Datenbestand beschreibt. Events werden intern über NATS JetStream veröffentlicht und können von Outbound-Konnektoren konsumiert werden.

**Event-Struktur:**

```json
{
  "event_id": "evt_001",
  "timestamp": "2026-08-14T10:00:00Z",
  "event_type": "object.updated",
  "object_type": "product",
  "object_id": "object_123",
  "changes": {
    "attributes.price": {
      "old": 19.99,
      "new": 24.99
    }
  },
  "metadata": {
    "user_id": "user_1",
    "request_id": "req_456"
  }
}
```

**Event-Typen:**

- `object.created` – neues Objekt angelegt
- `object.updated` – Attribute oder Status geändert
- `object.deleted` – Objekt (soft) gelöscht
- `reference.created` – neue Referenz erstellt
- `reference.deleted` – Referenz entfernt
- `attribute_definition.created` / `updated` / `deleted` – Änderungen an Attributdefinitionen

**Veröffentlichung:**

- Der Rust-Kern publiziert Events auf einem eigenen JetStream-Subject (`events.crud`).
- Events werden **persistent** gespeichert und erst nach erfolgreichem Acknowledgment durch den Consumer entfernt (WorkQueue-Prinzip).
- Dadurch gehen keine Änderungen verloren, auch wenn die Middleware kurzfristig nicht erreichbar ist.

**Filterung:**

- Für den Webhook-Outbound werden Events anhand konfigurierbarer Filter selektiert:
  - Nach `event_type` (z. B. nur `object.updated`)
  - Nach `object_type` (z. B. nur `product`, nicht `category`)
  - Nach bestimmten Attributen (z. B. nur wenn `price` geändert wurde)
  - Nach Statusübergängen (z. B. nur Freigaben)

### 7.3 Webhook-Outbound zur Middleware

Der Webhook-Outbound-Konnektor ist der primäre Kanal, um Änderungen an die bestehende Middleware zu liefern. Er kombiniert das Eventsystem mit einem zuverlässigen HTTP-Versand.

**Konfiguration (als `job_definition` oder Konnektor-Konfiguration):**

```json
{
  "id": "conn_webhook_middleware",
  "type": "webhook",
  "name": "Middleware Webhook",
  "parameters": {
    "url": "https://middleware.example.com/api/meadow-events",
    "method": "POST",
    "headers": {
      "Authorization": "Bearer <token>",
      "Content-Type": "application/json"
    },
    "filter": {
      "event_types": ["object.created", "object.updated", "object.deleted"],
      "object_types": ["product", "category"],
      "attribute_changes": ["price", "status"]
    },
    "retry_policy": {
      "max_attempts": 5,
      "backoff_seconds": [30, 60, 120, 300]
    }
  }
}
```

**Verarbeitungsablauf:**

1. Der Rust-Kern publiziert jedes CRUD-Event in NATS JetStream.
2. Ein Python-Worker abonniert den Event-Stream und prüft die Filterregeln des Webhook-Konnektors.
3. Bei Übereinstimmung baut der Worker die HTTP-Anfrage an die Middleware zusammen:
   - Body: das gefilterte Event oder das vollständige Objekt (konfigurierbar)
   - ggf. zusätzliche Informationen wie `resolved_attributes` (nach Vererbung/Sprachbaum)
4. Der Worker sendet die Anfrage mit Wiederholungslogik.
5. Bei dauerhaftem Fehler wird das Event in eine Dead-Letter-Queue verschoben und kann später manuell verarbeitet werden.

**Vorteile dieser Architektur:**

- **Entkopplung:** Änderungen werden sofort erfasst, ohne die Kern-Performance zu beeinträchtigen.
- **Zuverlässigkeit:** Persistente Events und Retry-Mechanismen verhindern Datenverlust.
- **Flexibilität:** Filter können pro Middleware-Kanal unterschiedlich konfiguriert werden.
- **Wiederverwendbarkeit:** Derselbe Mechanismus kann später für weitere Ziele (z. B. Suchmaschinen, Analytics) genutzt werden.

**Abgrenzung zu Standard-Outbounds:**

- Der `json_export` dient dem **Batch-Export** ganzer Datenbestände (z. B. für nächtliche Abgleiche).
- Der `webhook` dient dem **ereignisgetriebenen Push** einzelner Änderungen in Echtzeit.
- Beide nutzen dieselbe Infrastruktur (NATS, Python-Worker), aber unterschiedliche Trigger und Verarbeitung.

---

## 8. Workflow & Freigaben

### 8.1 Workflows (Zustandsmaschine)

- Es wird **keine schwere Workflow-Engine** eingesetzt.
- Die Statuslogik ist eine einfache Zustandsmaschine, implementiert im Python Business-Rules-Service.
- Erlaubte Übergänge werden zentral definiert, z. B.:

```
draft → in_review → approved → published
                     ↓
                  rejected → draft
```

- Der Rust-Kern speichert nur das Statusfeld am Objekt und protokolliert Änderungen im Audit-Log.

### 8.2 Freigaben

- **Grundprinzip**: Jedes Objekt besitzt ein Statusfeld (`status`). Der Wert `published` bedeutet global freigegeben.
- Outbound-Prozesse filtern standardmäßig nach `status: "published"`.
- **Kanalspezifische Freigaben** (z. B. „freigegeben für Webshop“) werden bei Bedarf später über eine separate Tabelle `release` umgesetzt (object_id, channel, status). Für den Start reicht das einfache Statusfeld.

---

## 9. Gespeicherte Suchen & Materialisierte Collections

Meadow PIM sieht die Möglichkeit vor, komplexe Suchanfragen zu speichern und deren Ergebnisse als refreshbare Collections zu materialisieren. Dies ist nicht Teil der ersten Version, wird aber von Anfang an im Datenmodell und in der Architektur berücksichtigt.

### 9.1 Gespeicherte Suchen (Advanced Search)

Eine **gespeicherte Suche** ist ein Objekt vom Typ `saved_search`, das die Kriterien einer komplexen Suche in strukturierter Form enthält. Sie kann jederzeit erneut ausgeführt werden und dient als Grundlage für Business Rules, Exporte und die Materialisierung.

**Beispielobjekt:**

```json
{
  "id": "search_001",
  "type": "saved_search",
  "attributes": {
    "name": "Aktive Produkte mit Preisfehler",
    "query": {
      "filters": [
        { "field": "type", "operator": "eq", "value": "product" },
        { "field": "status", "operator": "in", "value": ["published", "approved"] },
        { "field": "attributes.price", "operator": "lt", "value": 0 },
        { "field": "attributes.name.de", "operator": "not_empty", "value": null }
      ],
      "fulltext": {
        "fields": ["attributes.description.*"],
        "query": "Edelstahl",
        "language": "de"
      },
      "references": [
        {
          "type": "category_member",
          "to": "category_5",
          "direction": "incoming"
        }
      ],
      "sort": [
        { "field": "attributes.price", "order": "asc" }
      ],
      "limit": 500
    },
    "refresh_interval": "0 0 2 * * *",   // Cron (6 Felder, Sekunden zuerst)
    "last_executed_at": "...",
    "last_result_count": 42
  },
  "status": "active"
}
```

**Suchkriterien (Query-DSL):**

- **`filters`**: Beliebige Bedingungen auf Objektfelder und Attributwerte, inkl. Vergleichsoperatoren (`eq`, `ne`, `gt`, `lt`, `in`, `contains`, `not_empty`, …).
- **`fulltext`**: Volltextsuche über sprachabhängige Attributwerte, mit optionaler Sprachauswahl gemäß Sprachbaum (Abschnitt 4.4).
- **`references`**: Einschränkung über Relationen, z. B. nur Objekte, die in einer bestimmten Kategorie hängen oder eine bestimmte Vererbungsquelle haben.
- **`sort`** und **`limit`**: Sortierung und Begrenzung der Ergebnisliste.

**Ausführung:**

- Gespeicherte Suchen werden über den Rust-Kern ausgeführt (synchron) oder als Job über den Scheduler (asynchron).
- Die Ergebnisse können als Liste von Objekt-IDs zurückgegeben werden.
- Business Rules können eine `saved_search` als Filter verwenden, z. B. „Wende Preisregel auf alle Objekte aus Suche X an“.

### 9.2 Materialisierte Collections

Eine **Collection** ist ein eigenständiges Objekt (z. B. `type: "collection"`), das die Ergebnisliste einer gespeicherten Suche als relationierte Objektmenge abbildet. Dadurch wird das Suchergebnis zu einem **refreshbaren Cache**, der wie eine normale Gruppe/Kategorie verwendet werden kann.

**Modellierung:**

- Das Collection-Objekt besitzt eine Referenz auf die zugrunde liegende `saved_search`.
- Die Ergebnisse werden über Relationen des Typs `collection_member` mit dem Collection-Objekt verbunden:

  ```json
  {
    "id": "ref_coll_001",
    "from": "collection_10",
    "to": "object_123",
    "type": "collection_member",
    "attributes": {
      "rank": 1,
      "added_at": "2026-08-14T10:00:00Z"
    }
  }
  ```

- Die Relationen enthalten Metadaten (z. B. Position/Rank), sodass die Collection wie eine sortierte Liste verwendet werden kann.

**Refresh-Mechanismus:**

- Beim Refresh (manuell oder geplant über `refresh_interval`) werden die bisherigen `collection_member`-Relationen der Collection entfernt und durch die aktuellen Treffer der Suche ersetzt.
- Der Refresh läuft als Hintergrundjob im Python-Worker oder direkt im Rust-Kern, um die Oberfläche nicht zu blockieren.
- Während des Refreshs bleibt die alte Collection konsistent lesbar; nach Abschluss wird atomar auf die neue Ergebnisliste umgestellt.

**Verwendung materialisierter Collections:**

- Als **vorgefilterte Arbeitslisten** in der Oberfläche, z. B. „Preisfehler“, „Neuheiten“, „Unvollständige Produkte“.
- Als **Basis für Outbound-Exporte**, ohne bei jedem Export die Suche neu ausführen zu müssen.
- Als **Kategorien für die Middleware** – die Middleware erhält nur noch die Objekt-IDs der Collection und kann gezielt abgleichen.

**Abgrenzung zu normalen Kategorien (Abschnitt 4.6):**

- Kategorien werden manuell oder über feste Regeln befüllt; Collections basieren auf dynamischen Suchkriterien.
- Collections können selbst Teil von Kategorien, Vererbungsstrukturen oder weiteren Suchen sein, da sie normale Objekte sind.

**Vorbereitung in der ersten Version:**

- Das Datenmodell für `saved_search` und `collection` sowie die Relationstypen werden bereits vorgesehen.
- Die Query-DSL wird konzeptionell definiert, auch wenn die vollständige Advanced Search erst später umgesetzt wird.
- Inbound-/Outbound-Konnektoren und Eventsystem berücksichtigen von Anfang an, dass Objekte vom Typ `saved_search` oder `collection` existieren können.

**Performance-Aspekte:**

- Für sehr große Ergebnismengen kann der Refresh der Collection in Batches erfolgen, um die Datenbanklast zu begrenzen.
- Indizes auf `collection_member.from` und `collection_member.to` sorgen für schnelle Abfragen.
- Optional werden die Collection-Relationen in einem separaten Index gehalten, um die Suche nicht durch unnötige Relationen zu belasten.


---

## 10. Benutzeroberfläche (Frontend)

### 10.1 Architektur

- **Separater Docker-Container** mit Python + NiceGUI.
- Der NiceGUI-Dienst kommuniziert ausschließlich über die REST-API des Rust-Kerns.
- **Ein Dienst für alle Benutzer**: Es wird keine getrennte Admin-Oberfläche aufgebaut. Stattdessen werden Menüpunkte, Seiten und Buttons abhängig von der Benutzerrolle (`admin`, `editor`, `viewer`) ein-/ausgeblendet.
- Authentifizierung erfolgt über JWT: NiceGUI nimmt Login-Daten entgegen, fordert ein Token vom Kern an und speichert es in der Session.
- **Asynchrone Updates** (z. B. Job-Status, Änderungsbenachrichtigungen) werden in der ersten Version über **REST-Polling** realisiert. Eine direkte NATS-Anbindung des Frontends ist für spätere Versionen möglich, wird im MVP aber bewusst vermieden, um die Architektur einfach zu halten.

### 10.2 Vorteile

- Einheitliche Codebasis, schnelle Entwicklung.
- Keine doppelte UI-Pflege.
- Rollenbasierte Zugriffskontrolle einfach umsetzbar (NiceGUI `roles`-Parameter).

### 10.3 Mehrsprachigkeit der Oberfläche

Die Weboberfläche von Meadow PIM wird von Beginn an **mehrsprachig konzipiert**. Dies betrifft ausschließlich die **UI-Texte** (Navigation, Buttons, Meldungen, Hilfe) – nicht die fachlichen Daten. Die Mehrsprachigkeit der Datenobjekte ist bereits über die flexiblen Metadaten gelöst und bleibt davon unabhängig.

**Geplante Sprachen:**

1. **Englisch** (Startsprache, internationaler Standard)
2. **Deutsch** (zweite Sprache, für den DACH-Markt)
3. **Französisch** (dritte Sprache, für den frankophonen Markt)
4. Weitere Sprachen nach Bedarf (z. B. Spanisch, Italienisch, Niederländisch)

**Technische Umsetzung in NiceGUI:**

- Verwendung einer internationalisierungsfähigen Struktur: Alle UI-Texte werden über **Übersetzungs-Keys** referenziert, nicht hart im Code verdrahtet.
- Empfohlene Bibliothek: **`gettext`** (Standard in Python) oder eine schlanke JSON-basierte Übersetzungsverwaltung.
  - `gettext` bietet ausgereifte Werkzeuge (`.po`/`.mo`-Dateien, Pluralformen, Kontext).
  - JSON-Dateien sind einfacher zu pflegen, aber weniger mächtig bei Pluralisierung.
- Die Übersetzungsdateien werden im Frontend-Container ausgeliefert und können ohne Änderung am Code ergänzt werden.

**Sprachauswahl & Benutzereinstellung:**

- Die Sprache wird **pro Benutzer** festgelegt und im Benutzerprofil gespeichert (z. B. in SurrealDB).
- Beim ersten Login wird die Sprache automatisch aus den Browser-Einstellungen (`Accept-Language`) übernommen, sofern für den Benutzer noch keine Präferenz hinterlegt ist.
- Die Auswahl kann jederzeit über einen Sprachumschalter in der Oberfläche geändert werden.
- Die gewählte Sprache wird in der Session gehalten und an alle nachfolgenden Seiten/Elemente weitergegeben.

**Umsetzung in NiceGUI:**

- NiceGUI bietet keine eingebaute i18n-Funktion, daher wird eine eigene kleine Abstraktionsschicht implementiert, z. B. eine Funktion `t(key, locale)`, die den passenden Text aus der Übersetzungsdatei liefert.
- Alle UI-Elemente (Labels, Buttons, Dialoge) verwenden diese Funktion.
- Für Datums-, Zahlen- und Währungsformatierung werden die länderspezifischen Einstellungen der gewählten Sprache berücksichtigt (z. B. mit `babel`).

**Vorteile dieses Ansatzes:**

- Neue Sprachen können später durch einfaches Hinzufügen einer Übersetzungsdatei ergänzt werden.
- Die fachlichen Daten bleiben unberührt – Mehrsprachigkeit der Oberfläche ist strikt von der Datenmehrsprachigkeit getrennt.
- Das System ist von Anfang an auf internationale Nutzer vorbereitet, ohne den Kern zu beeinträchtigen.

### 10.4 Kernfunktionen der Oberfläche (MVP)

Die Oberfläche fokussiert sich in der ersten Version auf die wesentlichen Arbeitsabläufe. Folgende Module sind vorgesehen:

1. **Login & Benutzerverwaltung**
   - Anmeldung mit JWT, Passwort-Reset (optional)
   - Anzeige und Bearbeitung von Benutzern und Rollen (nur `admin`)

2. **Objektliste & Suche**
   - Listenansicht aller Objekte, filterbar nach Typ, Status, Kategorie
   - Einfache Volltextsuche
   - Cursor-basierte Pagination

3. **Objektbearbeitung**
   - Formular zur Anzeige und Bearbeitung der flexiblen Attribute
   - Mehrsprachige Eingabefelder gemäß Sprachbaum (z. B. Reiter pro Sprache)
   - Anzeige **vererbter Werte** inkl. Herkunft (Quelle) und Warnhinweis bei Konflikten
   - Setzen von leeren Werten zur Vererbungsunterbrechung
   - Statuswechsel gemäß Zustandsmaschine (Workflow)
   - Anzeige von Referenzen und Kategorien des Objekts

4. **Attributdefinitionen & -gruppen**
   - Verwaltung von Attributgruppen und -definitionen (nur `admin` oder `editor`)
   - Festlegung von Datentyp, Wertelisten, Mehrsprachigkeit und `inheritable`-Flag

5. **Kategorien & Relationen**
   - Anlegen und Hierarchisieren von Kategorien als normale Objekte
   - Zuordnung von Objekten zu Kategorien per Drag-and-Drop oder Auswahl

6. **Jobs & Scheduler**
   - Anzeige laufender und abgeschlossener Jobs (Historie)
   - Manuelles Starten von Job-Definitionen
   - Anzeige von Dead-Letter-Einträgen (nur `admin`)

7. **Sprachbaum-Administration**
   - Anzeige und Pflege des Sprachbaums (Knoten, Eltern, Fallback-Order)
   - Aktivieren/Deaktivieren von Sprachen für die Oberfläche und Datenpflege

8. **Einfache Dashboard-Ansicht**
   - Überblick über Anzahl Objekte pro Typ/Status
   - Letzte Änderungen (Audit-Log Auszug)

**Spätere Ausbaustufen (nicht im MVP):**

- Erweiterte Suche mit Query-Builder („Advanced Search“)
- Verwaltung gespeicherter Suchen und materialisierter Collections
- Kanalspezifische Freigaben
- Echtzeit-Updates über Websockets/NATS

---

## 11. Betrieb & Deployment

Alle Komponenten werden als Docker-Container betrieben. Die Standardauslieferung erfolgt als **Docker-Compose-Setup**, das für KMU-Umgebungen ausreicht. Eine spätere Orchestrierung über Kubernetes (z. B. k3s) ist vorbereitet, aber nicht Bestandteil des MVP.

### 11.1 Container-Übersicht

| Dienst               | Image                                | Beschreibung                                           |
|----------------------|--------------------------------------|--------------------------------------------------------|
| `pim-core`           | Rust (eigenes Image, multi-stage)    | Headless REST-API + Scheduler + Eventsystem            |
| `pim-frontend`       | Python + NiceGUI                     | Web-Oberfläche                                         |
| `pim-worker`         | Python + nats-py                     | Hintergrundverarbeitung (Jobs, Webhooks)               |
| `surrealdb`          | Offizielles SurrealDB-Image          | Datenbank (kann auch embedded im Kern laufen)          |
| `nats`               | Offizielles NATS-Image mit JetStream | Message Queue / Event-Bus                              |
| `object-storage`     | MinIO oder S3-kompatibel             | Binärdaten (Bilder, Exportdateien)                     |
| `redis` (optional)   | Offizielles Redis-Image              | Cache für Vererbungs-/Sprachauflösung und Suche        |

### 11.2 Netzwerk & Sicherheit

- **Öffentlicher Zugang:** Nur der `pim-frontend`-Container ist über HTTPS erreichbar (Reverse Proxy, z. B. Traefik, Caddy oder Nginx).
- **Internes Netz:** `pim-core`, `surrealdb`, `nats`, `object-storage`, `redis` und `pim-worker` befinden sich in einem privaten Docker-Netzwerk und sind nicht direkt aus dem Internet erreichbar.
- **Kommunikation:**
  - Frontend → Core: REST (JWT)
  - Worker → Core: REST (JWT)
  - Core → NATS: Publish (intern)
  - Worker → NATS: Subscribe (intern)
  - Core → SurrealDB: interner Treiber / Netzwerk
- **Secrets:** Zugangsdaten (Datenbank, NATS, Objektstorage, JWT-Secret) werden über Umgebungsvariablen oder Docker Secrets injiziert und nicht in Images eingebacken.
- **TLS:** Die externe Kommunikation läuft ausschließlich über HTTPS (TLS-Terminierung am Reverse Proxy).

### 11.3 Persistenz & Volumes

- **SurrealDB:** Datenbankdatei in einem benannten Volume oder Host-Pfad (`/data`).
- **NATS JetStream:** Persistenter Store in einem Volume (`/data/jetstream`).
- **Objektstorage:** Datenverzeichnis in einem Volume (bei MinIO: `/data`).
- **Redis (optional):** Persistenz konfigurierbar; für Cache-Nutzung kann AOF/RDB aktiviert oder deaktiviert werden.

### 11.4 Konfiguration

Jeder Dienst wird über eine eigene Konfigurationsdatei (z. B. `config.yaml` oder Umgebungsvariablen) parametrisiert. Zentrale Einstellungen im Compose-File:

- `PIM_CORE_URL` (für Frontend und Worker)
- `SURREALDB_URL`, `SURREALDB_USER`, `SURREALDB_PASS`
- `NATS_URL`
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`
- `JWT_SECRET`, `JWT_EXPIRATION`
- `LOG_LEVEL` (für alle Dienste)
- `WEBHOOK_RETRY_MAX`, `WEBHOOK_RETRY_BACKOFF` (Worker)

### 11.5 Skalierung

- **Rust-Kern:** Kann horizontal skaliert werden (mehrere Instanzen hinter Load Balancer). Achtung: Scheduler darf nur in einer Instanz aktiv sein (Leader-Election oder dedizierte Instanz mit `scheduler.enabled=true`).
- **Python-Worker:** Skalierung über mehrere Container, die sich eine NATS-Queue-Group teilen (automatische Lastverteilung).
- **Frontend:** Bei Bedarf mehrere Instanzen hinter Load Balancer (sticky sessions erforderlich, da NiceGUI zustandsbehaftet ist).
- **SurrealDB:** Im MVP als Einzelinstanz (embedded oder separater Dienst). Cluster-Modus ist möglich, wird aber erst bei größerem Datenaufkommen relevant.

### 11.6 Backup & Wiederherstellung

- **SurrealDB:** Regelmäßige Exporte (`SURREAL EXPORT`) oder Dateisicherung des Volumes. Zeitplan über Host-Cron oder externes Tool.
- **Objektstorage:** Synchronisation der Buckets auf einen zweiten Speicherort (z. B. S3-Replikation, rsync bei MinIO).
- **NATS JetStream:** Snapshots des Streams optional; für den MVP genügt die Wiederherstellung aus der Quelle (z. B. erneuter Export), da Events rekonstruierbar sind.
- **Wiederherstellungstests:** Mindestens vierteljährlich durchführen.

### 11.7 Monitoring & Logging

- **Logging:**
  - Rust-Kern: strukturierte Logs mit `tracing` (JSON-Format optional)
  - Python-Dienste: `structlog` mit einheitlichem Format
  - Logs werden auf `stdout` geschrieben und können über Docker-Logging-Treiber oder zentrale Systeme (z. B. Loki, ELK) gesammelt werden.
- **Health-Checks:**
  - Jeder Container stellt einen Health-Endpoint bereit (z. B. `GET /health` im Kern, einfache HTTP-Checks für NATS/SurrealDB).
  - Docker Compose überwacht die Container und startet sie bei Ausfall neu.
- **Metriken (optional, ab Phase 8):**
  - Prometheus-Endpunkte im Kern und Worker (z. B. Anzahl Jobs, Event-Durchsatz, API-Latenz)
  - Grafana-Dashboards für Betriebsübersicht

### 11.8 Deployment-Varianten

- **MVP (Standard):** Docker Compose auf einer einzelnen VM oder einem kleinen Server. Alle Dienste laufen gemeinsam, getrennt durch interne Netzwerke.
- **Erweiterte Variante:** Aufteilung auf mehrere VMs (z. B. Datenbank auf separatem Server, Worker auf zweitem Server) mittels Docker Swarm oder manuellem Setup.
- **Zukunft:** Migration zu Kubernetes (k3s) für automatische Skalierung und Self-Healing.

### 11.9 Ressourcenbedarf (Richtwerte für KMU)

| Dienst          | CPU     | RAM   | Speicher (Volumen) |
|-----------------|---------|-------|--------------------|
| pim-core        | 1–2 vCPU| 512 MB| –                  |
| pim-frontend    | 1 vCPU  | 512 MB| –                  |
| pim-worker      | 1 vCPU  | 512 MB| –                  |
| surrealdb       | 1–2 vCPU| 1 GB  | 10–50 GB           |
| nats            | 0.5 vCPU| 256 MB| 5–20 GB            |
| object-storage  | 1 vCPU  | 512 MB| 50–500 GB          |
| redis (opt.)    | 0.5 vCPU| 256 MB| 1–5 GB             |

Diese Werte sind als Ausgangspunkt gedacht und hängen stark von Objektanzahl, Suchlast und Exportvolumen ab.

---

## 12. Versionierung & Audit

Meadow PIM setzt auf eine **schlanke Versionierung** und ein **einfaches Audit-Log**, um Änderungen nachvollziehbar zu machen, ohne die Komplexität einer vollständigen Revisionshistorie zu tragen. Die Prämisse „einfach und performant“ bleibt dabei erhalten.

### 12.1 Versionierung von Objekten

- Jedes Objekt besitzt ein `version`-Feld (Integer), das bei **jeder Änderung** automatisch inkrementiert wird.
- Zusätzlich werden `created_at`, `updated_at` und `updated_by` gepflegt:
  - `created_at`: Zeitpunkt der Erstellung (unveränderlich)
  - `updated_at`: Zeitpunkt der letzten Änderung
  - `updated_by`: Benutzer- oder Systemkennung, die die Änderung ausgelöst hat
- Die Versionierung gilt für **alle Objekttypen**, einschließlich Attributdefinitionen, Kategorien, gespeicherter Suchen und Collections.

**Beispiel:**

```json
{
  "id": "object_123",
  "type": "product",
  "version": 7,
  "created_at": "2026-08-14T08:00:00Z",
  "updated_at": "2026-08-14T11:30:00Z",
  "updated_by": "user_42"
}
```

### 12.2 Audit-Log

Für die Nachvollziehbarkeit von Änderungen wird eine separate Tabelle `audit_log` geführt. Sie protokolliert **wesentliche Ereignisse**, insbesondere:

- Änderungen an Objektattributen (optional mit alten und neuen Werten)
- Statuswechsel (z. B. `draft` → `in_review` → `approved`)
- Anlegen und Löschen von Objekten
- Änderungen an Referenzen (z. B. Kategoriezuordnung, Vererbungsrelationen)
- Konfigurationsänderungen (z. B. Priorität von Vererbungsrelationen)

**Beispiel eines Audit-Eintrags:**

```json
{
  "id": "audit_001",
  "object_id": "object_123",
  "user_id": "user_42",
  "action": "status_changed",
  "from": "in_review",
  "to": "approved",
  "timestamp": "2026-08-14T11:30:00Z",
  "metadata": {
    "request_id": "req_456"
  }
}
```

**Felder im Audit-Log:**

- `object_id`: betroffenes Objekt (falls vorhanden)
- `user_id`: auslösender Benutzer oder Systemkennung
- `action`: Art der Änderung (z. B. `object_created`, `attribute_updated`, `status_changed`, `reference_created`)
- `from` / `to`: alte und neue Werte (optional, je nach Aktion)
- `timestamp`: Zeitpunkt der Änderung
- `metadata`: zusätzliche Informationen (Request-ID, Quelle des Ereignisses)

### 12.3 Beziehung zum Eventsystem

Das Eventsystem (Abschnitt 7.2) und das Audit-Log ergänzen sich:

- **Events** sind flüchtige, technische Nachrichten für asynchrone Verarbeitung (z. B. Webhooks an die Middleware).
- **Audit-Log** ist die persistente, fachliche Aufzeichnung von Änderungen für Benutzer und Administratoren.

Der Rust-Kern kann das Audit-Log direkt aus den internen CRUD-Operationen befüllen – unabhängig von den publizierten Events. Dadurch ist das Audit-Log auch dann vollständig, wenn Events nicht verarbeitet werden.

### 12.4 Bewusster Verzicht auf vollständige Revisionshistorie

Eine **vollständige Revisionshistorie** (Speicherung jedes alten Standes) ist **bewusst nicht** Teil des MVP. Sie würde:

- den Speicherbedarf erhöhen,
- die Schreiboperationen verlangsamen,
- die Datenbankstruktur verkomplizieren.

Stattdessen ermöglicht die Kombination aus `version`, `updated_at`/`updated_by` und Audit-Log eine ausreichende Rückverfolgbarkeit für die meisten KMU-Szenarien.

**Spätere Erweiterung (optional):**

Falls gesetzliche oder fachliche Anforderungen eine Revisionshistorie erfordern, kann später eine Tabelle `object_version` eingeführt werden, die bei jeder Änderung eine Kopie des vorherigen Zustands speichert. Das Datenmodell und die API-Struktur sind so gewählt, dass eine solche Erweiterung ohne Bruch möglich ist.

---

## 13. Entwicklungsplan

### Phase 1 – Grundgerüst & Datenmodell-Basis
- Rust-Projekt aufsetzen (Axum, SurrealDB-Anbindung)
- Basis-CRUD für Objekte und Attributdefinitionen
- OpenAPI-Dokumentation generieren
- **Datenmodell für Sprachbaum vorbereiten** (Tabelle `language_tree`, Fallback-Mechanismus in der API-Struktur)
- **Attributdefinition um `inheritable`-Flag erweitern**
- **Referenztypen für Vererbung (`inherits_from`), Kategorisierung (`category_member`) und spätere Collections (`collection_member`) als Konzept festlegen**

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
- Admin-Seiten für Attributdefinitionen, Jobs, Benutzer
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

---

## 14. Zusammenfassung

Meadow PIM kombiniert einen hochperformanten Rust-Kern (headless) mit SurrealDB für flexible Dokumente und Graphen, NATS JetStream für zuverlässige Hintergrundverarbeitung und Python für Business Rules sowie die Benutzeroberfläche (NiceGUI). Durch die lose Kopplung der Dienste bleibt das System einfach, wartbar und skalierbar – exakt passend für die Anforderungen eines KMUs.

---

*Dokument erstellt am 2026-08-13 – Projektname: Meadow PIM.*
# Datenmodell

- **Hauptdokument:** Abschnitt [4. Datenmodell](../meadow-pim-architektur.md#4-datenmodell)
- **Relevante Phasen:** Phase 1–3
- **Stand:** Entwurf

## Grundprinzip

Alle fachlichen Entitäten (Produkte, Kategorien, Assets, gespeicherte Suchen, Collections, …) werden als **generische Objekte** gespeichert. Unterschiede ergeben sich durch den Typ und flexible Metadaten. Technische Tabellen (Benutzer, Gruppen, Attributdefinitionen, Sprachbaum, Jobs, Audit-Log) ergänzen das Kernmodell.

## Datumswerte

Alle Datums- und Zeitwerte folgen einer einheitlichen, verbindlichen Konvention (Abschnitt 4 des Hauptdokuments). Speicherung, Austausch und Darstellung sind strikt getrennt:

1. **Speicherung (Datenbank):** Alle Datums- und Zeitwerte werden **immer als UTC** in SurrealDB gespeichert – ohne lokale Zeitzone. Betroffen sind u. a. `created_at`, `updated_at`, `started_at`, `finished_at`, Audit-`timestamp` und alle weiteren Zeitfelder (Datentyp `datetime` bzw. ISO-8601-String mit `Z`).
2. **Austausch (API / Inbound / Outbound):** Zeitwerte werden über die REST-API, im MEF-Format und in Event-Nachrichten **immer als ISO-8601-String mit Zeitzonenangabe** übertragen, z. B. `2026-08-14T10:00:00Z` (UTC) oder `2026-08-14T12:00:00+02:00` (mit Offset). Strings **ohne** Zeitzonenangabe werden abgelehnt. Beim Empfang wird nach UTC normalisiert, bevor gespeichert wird; beim Senden wird UTC als `…Z` ausgegeben.
3. **Darstellung (Frontend):** Die GUI zeigt Datums-/Zeitwerte **lokalisiert** an – in der Zeitzone und im Format des Benutzers (z. B. `14.08.2026, 12:00` für `de-DE`). Formatierung über `babel` (Details in [frontend.md](frontend.md)); der gespeicherte Wert bleibt unverändert UTC.

> **Hinweis:** Alle Zeitfelder in den Beispielen dieses Dokuments sind als ISO-8601-UTC-Strings dargestellt.

## Werte mit Einheiten

Numerische technische Werte (z. B. Breite, Spannung, Leistung) können eine Einheit tragen. Der zugehörige **Einheiten-Katalog** liegt in der Tabelle `unit`; Speicher-Konvention (kanonische SI-Einheit), Umrechnung und benutzerdefinierte Einheiten sind im Detaildokument [einheiten.md](einheiten.md) beschrieben. In der Tabelle `object` werden solche Werte als **reine Zahlen in der kanonischen SI-Einheit** gespeichert; die Einheit ergibt sich aus der Attributdefinition (`unit_id`, optional `display_unit_id`).

## Tabellen in SurrealDB

### Tabelle `object`

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
    "status": "draft",               // draft | in_review | approved | rejected | published | archived
    "version": 3,
    "created_at": "2026-08-14T08:00:00Z",
    "updated_at": "2026-08-14T11:30:00Z",
    "updated_by": "user_1"
}
```

- `type` ist frei wählbar und bestimmt die fachliche Rolle des Objekts.
- Sprachabhängige Werte werden als verschachtelte JSON-Objekte mit Sprachschlüsseln gemäß Sprachbaum (Abschnitt 4.4) gespeichert.
- `status` dient dem einfachen Workflow (Abschnitt 8).

### Tabelle `attribute_definition`

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
    },
    "version": 1,
    "created_at": "2026-08-14T08:00:00Z",
    "updated_at": "2026-08-14T11:30:00Z",
    "updated_by": "user_1"
}
```

- `inheritable` steuert, ob das Attribut in die Datenwertvererbung (Abschnitt 4.5) einbezogen wird.
- Weitere optionale Felder: `multilingual`, `enum_values`, `default_value`, `unit_id`, `display_unit_id`, `description`. Die Felder `unit_id`/`display_unit_id` gelten für numerische Werte mit Einheit (siehe [einheiten.md](einheiten.md)).

### Tabelle `attribute_group`

Gruppiert Attributdefinitionen für die UI.

```json
{
    "id": "attr_group_01",
    "name": "pricing",
    "label": { "de": "Preise", "en": "Pricing" },
    "position": 2
}
```

### Tabelle `reference`

Referenzen zwischen Objekten (Graph-Kanten) mit eigenen Metadaten. Der `type` ist frei definierbar; bestimmte Typen aktivieren spezielle Verhaltensweisen:

- `inherits_from` → Datenwertvererbung (Abschnitt 4.5)
- `category_member` → Kategorisierung (Abschnitt 4.6)
- `collection_member` → materialisierte Collections (Abschnitt 10.2)
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

### Tabelle `language_tree`

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

### Tabelle `job_definition`

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
    "created_at": "2026-08-14T08:00:00Z",
    "updated_at": "2026-08-14T09:15:00Z"
}
```

### Tabelle `job_run`

Protokolliert jeden Ausführungsversuch (Details in Abschnitt 6).

```json
{
    "id": "job_run_456",
    "job_definition_id": "job_def_123",
    "trigger": "schedule",           // schedule | manual | event
    "status": "success",             // pending | running | success | failed | retry
    "attempt": 2,
    "started_at": "2026-08-14T09:00:00Z",
    "finished_at": "2026-08-14T09:04:12Z",
    "result": { "exported_objects": 150 },
    "error": null,
    "worker_id": "worker-1"
}
```

### Tabelle `audit_log`

Protokolliert Änderungen und Statuswechsel (Details in Abschnitt 13).

```json
{
    "id": "audit_001",
    "object_id": "object_123",
    "user_id": "user_1",
    "action": "status_changed",
    "from": "in_review",
    "to": "approved",
    "timestamp": "2026-08-14T11:30:00Z",
    "metadata": {
        "request_id": "req_456"
    }
}
```

### Zukünftige Objekttypen & Tabellen (nicht im MVP, aber konzeptionell vorgesehen)

- **`saved_search`**: Objekttyp (`type: "saved_search"`) für komplexe Suchanfragen (Abschnitt 10.1); wird in der Tabelle `object` gespeichert.
- **`collection`**: Objekttyp (`type: "collection"`) für materialisierte Ergebnislisten auf Basis gespeicherter Suchen (Abschnitt 10.2); wird in der Tabelle `object` gespeichert.
- **`release`**: Eigene Tabelle für kanalspezifische Freigaben (Abschnitt 8.2, spätere Ausbaustufe).

## Indizes & Suche

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

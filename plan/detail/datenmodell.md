# Datenmodell

- **Hauptdokument:** Abschnitt [4. Datenmodell](../meadow-pim-architektur.md#4-datenmodell)
- **Relevante Phasen:** Phase 1–3, 5–7, 9
- **Stand:** Entwurf

## Grundprinzip

Alle fachlichen Entitäten (Produkte, Kategorien, Assets, gespeicherte Suchen, Collections, …) werden als **generische Objekte** gespeichert. Unterschiede ergeben sich durch den Typ und flexible Metadaten. Technische Tabellen (Benutzer, Gruppen, Attributdefinitionen, Sprachbaum, Jobs, Audit-Log) ergänzen das Kernmodell.

## Record-IDs & Querverweise (verbindlich)

1. **Record-IDs:** Jeder Datensatz hat eine SurrealDB-Record-ID `tabelle:key`. Beim Anlegen über die API vergibt der Kern **ULID-Keys** (z. B. `object:01M0…`, `user:<ulid>`). Feste Seed-Datensätze verwenden explizite Keys (`object:object_root`, `relation_definition:rel_depends_on`, `unit:unit_m`, `user:admin`).
2. **Querverweis-Felder:** String-Felder, die auf einen anderen Datensatz verweisen (`definition_id`, `unit_id`, `display_unit_id`, `canonical_unit_id`, `group_id`, `parent`), speichern den **Record-Key** (z. B. `unit_m`, `rel_depends_on`) – das entspricht den Kurz-IDs in den Beispielen dieses Dokuments.
3. **Graph-Kanten (`reference`):** `reference` ist eine **native SurrealDB-RELATION-Tabelle**; die Endpunktfelder heißen `in` (Quelle, früher `from`) und `out` (Ziel, früher `to`) und tragen volle Record-IDs (`object:<ulid>`). Traversierung über Edge-Queries (`->reference->object`).
4. **JWT `sub`:** trägt die volle Record-ID des Benutzers (z. B. `user:admin`).

## System-Log (`log_entry`) & Systemeinstellungen (`system_setting`)

Zwei technische Tabellen ergänzen das Kernmodell für Betrieb und Administration (Konvention gemäß Abschnitt 12 des Hauptdokuments; Detail [betrieb-deployment.md](betrieb-deployment.md#11-monitoring--logging)).

**`log_entry` – kuratierte System-Logs (flüchtig, nur Admin-UI):**

| Feld         | Typ            | Beschreibung                                                                |
| ------------ | -------------- | --------------------------------------------------------------------------- |
| `id`         | string         | Eindeutige ID                                                               |
| `created_at` | datetime (UTC) | Zeitpunkt (ISO-8601, Datumswerte-Konvention)                                |
| `service`    | string         | Herkunft:`pim-core`, `pim-worker`, `pim-frontend`                           |
| `level`      | string         | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` \| `CRITICAL`                        |
| `category`   | string         | `app` \| `job` \| `rule` \| `auth` \| `integration`                         |
| `message`    | string         | Klartext-Meldung (Pflicht)                                                  |
| `trace_id`   | string?        | Korrelations-ID über Dienste hinweg (`trace_id`/`request_id`)               |
| `context`    | object?        | Optional:`object_id`, `job_run_id`, `rule_id`, `user_id`, `status_code`, …  |

- **Charakter:** reine Append-Tabelle, kein Update, kein Soft-Delete; Einträge schreibt der Kern (Empfang über `POST /system/logs` von allen Komponenten).
- **Indizes:** `(service, level, created_at)` für die UI-Filter; `trace_id` für Korrelationssuche. Keine Volltextsuche im MVP.
- **Aufbewahrung:** flüchtig, Standard 30 Tage (konfigurierbar über `system_setting`); Cleanup als periodischer Job; Rate-Limit-Cap gegen Log-Fluten.
- **Datenschutz:** keine Secrets/PII – Regeln in [betrieb-deployment.md](betrieb-deployment.md#11-monitoring--logging).

**`system_setting` – persistierte Runtime-Einstellungen:**

| Feld         | Typ            | Beschreibung                                 |
| ------------ | -------------- | -------------------------------------------- |
| `key`        | string         | Einstellungsname (z. B.`log_retention_days`) |
| `value`      | any (JSON)     | Wert                                         |
| `updated_at` | datetime (UTC) | Letzte Änderung                              |
| `updated_by` | string         | Auslöser (`user_id` oder Systemkennung)      |

- **Auflösung:** Code-Default < ENV < DB (Runtime). Ein fehlender Eintrag bedeutet „Default/ENV gilt“.
- **Verwendung (MVP):** Logging-Einstellungen (`log_persist_level`, `log_persist_categories`, `log_retention_days`, `log_rate_limit`); das Schema ist generisch und für weitere Laufzeit-Einstellungen erweiterbar.
- **Zugriff:** `GET/PUT /system/settings` (nur `admin`/`superadmin`); Änderungen im Audit-Log.

## Datumswerte

Alle Datums- und Zeitwerte folgen einer einheitlichen, verbindlichen Konvention (Abschnitt 4 des Hauptdokuments). Speicherung, Austausch und Darstellung sind strikt getrennt:

1. **Speicherung (Datenbank):** Alle Datums- und Zeitwerte werden **immer als UTC** in SurrealDB gespeichert – ohne lokale Zeitzone. Betroffen sind u. a. `created_at`, `updated_at`, `started_at`, `finished_at`, Audit-`timestamp` und alle weiteren Zeitfelder (Datentyp `datetime` bzw. ISO-8601-String mit `Z`).
2. **Austausch (API / Inbound / Outbound):** Zeitwerte werden über die REST-API, im MEF-Format und in Event-Nachrichten **immer als ISO-8601-String mit Zeitzonenangabe** übertragen, z. B. `2026-08-14T10:00:00Z` (UTC) oder `2026-08-14T12:00:00+02:00` (mit Offset). Strings **ohne** Zeitzonenangabe werden abgelehnt. Beim Empfang wird nach UTC normalisiert, bevor gespeichert wird; beim Senden wird UTC als `…Z` ausgegeben.
3. **Darstellung (Frontend):** Die GUI zeigt Datums-/Zeitwerte **lokalisiert** an – in der Zeitzone und im Format des Benutzers (z. B. `14.08.2026, 12:00` für `de-DE`). Formatierung über `babel` (Details in [frontend.md](frontend.md)); der gespeicherte Wert bleibt unverändert UTC.

> **Hinweis:** Alle Zeitfelder in den Beispielen dieses Dokuments sind als ISO-8601-UTC-Strings dargestellt.

## Werte mit Einheiten

Numerische technische Werte (z. B. Breite, Spannung, Leistung) können eine Einheit tragen. Der zugehörige **Einheiten-Katalog** liegt in der Tabelle `unit`; Speicher-Konvention (kanonische SI-Einheit), Umrechnung und benutzerdefinierte Einheiten sind im Detaildokument [einheiten.md](einheiten.md) beschrieben. In der Tabelle `object` werden solche Werte als **reine Zahlen in der kanonischen SI-Einheit** gespeichert; die Einheit ergibt sich aus der Attributdefinition (`unit_id`, optional `display_unit_id`).

## Schlüssel (Business-Schlüssel)

Ein **Business-Schlüssel** (z. B. SKU) ist eine markierte Attributdefinition und dient dem Anwender als fachliche Kennung – für ihn äquivalent zum technischen Primärschlüssel (`id`). Der Wert liegt normal im `attributes`-Feld; es gibt kein separates Feld und keine eigene Tabelle.

**Regeln (verbindlich, Konvention gemäß Abschnitt 4 des Hauptdokuments):**

1. **Markierung:** Ein Attribut wird durch `is_key: true` in der `attribute_definition` zum Business-Schlüssel.
2. **Eindeutigkeit pro Typ:** Der Kern erzwingt Eindeutigkeit pro Objekttyp **beim Schreiben anwendungsseitig** (Abfrage `(type, attributes.<name>)`, exakt/case-sensitiv, getrimmt) – ein statischer kompositer Unique-Index ist wegen der dynamischen Attributnamen im flexiblen `attributes`-Objekt nicht möglich. Leere/fehlende Werte kollidieren nicht (mehrere Objekte dürfen keinen Schlüssel haben).
3. **Mehrere erlaubt:** Pro Objekt können mehrere Schlüssel definiert sein (z. B. `sku`, `ean`), jeweils mit eigener Eindeutigkeit pro Typ.
4. **Optional:** Schlüssel sind optional; Objekte dürfen ohne Schlüssel existieren (Migration und Erstbefüllung ungehindert).
5. **Datentyp & Form:** Nur `text`, nicht lokalisiert (kein `multilingual`); Vergleich exakt (case-sensitive), beim Speichern wird getrimmt.
6. **Keine Vererbung:** Schlüssel sind nie vererbbar – `is_key: true` erzwingt `inheritable: false` (eine Vererbung würde Identitäten duplizieren).
7. **Änderbar:** Schlüsselwerte sind änderbar; bei jeder Änderung prüft der Kern die Eindeutigkeit neu und protokolliert die Änderung im Audit-Log.
8. **Adressierung & Import:** Die API bietet einen Key-Lookup-Endpunkt; der MEF-Import kann per Schlüssel abgleichen (Upsert) – Details in [api-design.md](api-design.md) und [integration-eventsystem.md](integration-eventsystem.md).

> **Hinweis:** Der Datentypwechsel-Abschnitt unten gilt auch für Schlüsselattribute: Ein Wechsel von `is_key` oder des Typs ist eine spätere Ausbaustufe (Phase 9) und erfordert eine Migration vorhandener Werte.

## Datentypwechsel

Der Datentyp eines Attributs wird über `type` in der `attribute_definition` festgelegt (`text`, `number`, `boolean`, `date`, `enum`, `reference`) und optional über Modifikatoren (`multilingual`, `unit_id`/`display_unit_id`) präzisiert. Die Werte selbst werden **generisch als JSON** im `attributes`-Feld der Objekte gespeichert – es gibt keine typgebundenen Spalten und keine typspezifischen, verlustbehafteten Speicherformate.

**Grundsatz:** Der Datentyp ist eine **Eigenschaft der Definition, nicht der gespeicherten Werte**. Ein Wechsel des Datentyps (inkl. Umstellung aller vorhandenen Werte) ist daher konzeptionell möglich, wird aber in der ersten Version **nicht** unterstützt und ist als spätere Ausbaustufe (Phase 9) vorgesehen.

**Entwurfsregeln (verbindlich – damit ein späterer Wechsel möglich und nicht erschwert wird):**

1. Werte werden **roh und umkehrbar** als JSON gespeichert: Zahl als Zahl, Text als String, Mehrsprachig als Sprachobjekt, Einheitenbehaftet als kanonische SI-Zahl. Keine Stringifizierung von Zahlen, keine Auflösung des Sprachobjekts auf eine Einzelsprache beim Speichern.
2. Der Typ wird **ausschließlich** in der `attribute_definition` geführt und nirgends redundant am Wert „festgeschrieben“.
3. Validierung und Umrechnung erfolgen **zur Lese-/Schreibzeit** anhand der Definition; die gespeicherten Rohwerte bleiben dabei erhalten bzw. normalisiert, aber umkehrbar.
4. Die `type`-Werteliste ist **offen und erweiterbar**: Neue Typen dürfen ergänzt, bestehende Typen nicht umbenannt werden (Stabilität für bestehende Daten, Exporte und Events).

**Kompatibilitätsmatrix:** Nicht jeder Wechsel ist möglich oder verlustfrei. Ein späterer Wechsel (Phase 9) umfasst eine **Migration aller vorhandenen Werte**, einen **Audit-Log-Eintrag** und die Prüfung gegen die folgende Matrix:

| von \ nach  | `text`                         | `number`               | `boolean`                             | `date`                            | `enum`                            | `reference`                  |
| ----------- | ------------------------------ | ---------------------- | ------------------------------------- | --------------------------------- | --------------------------------- | ---------------------------- |
| `text`      | —                              | ✅ nur parsebare Werte | ⚠️ nur`true`/`false`-konforme Werte   | ⚠️ nur ISO-8601-parsebare Werte   | ⚠️ nur Werte aus der Werteliste   | ⚠️ nur gültige Objekt-IDs    |
| `number`    | ✅ verlustfrei                 | —                      | ❌                                    | ❌                                | ⚠️ nur Werte aus der Werteliste   | ❌                           |
| `boolean`   | ✅ verlustfrei                 | ❌                     | —                                     | ❌                                | ⚠️ nur Werte aus der Werteliste   | ❌                           |
| `date`      | ✅ verlustfrei (ISO-8601)      | ❌                     | ❌                                    | —                                 | ⚠️ nur Werte aus der Werteliste   | ❌                           |
| `enum`      | ✅ verlustfrei                 | ❌                     | ❌                                    | ❌                                | —                                 | ❌                           |
| `reference` | ✅ verlustfrei (als ID-String) | ❌                     | ❌                                    | ❌                                | ❌                                | —                            |

- ✅ = verlustfrei möglich; ⚠️ = möglich, aber abhängig von den vorhandenen Werten (bei nicht konformen Werten Ablehnung oder Teilmigration mit Löschung); ❌ = nicht sinnvoll / nicht unterstützt.

**Modifikatoren gelten als Teil des Typs:**

- **Mehrsprachigkeit** (`multilingual`): An- bzw. Abschalten ändert die **Wertstruktur** (String ↔ Sprachobjekt) und zählt daher als Datentypwechsel. `text → text (multilingual)` erfordert das Einpacken der Werte in ein Sprachobjekt (z. B. unter `root`); der Rückweg erfordert die Auswahl genau einer Sprache und ist für die übrigen Sprachen verlustbehaftet.
- **Einheit** (`unit_id`/`display_unit_id`): `number → number (mit Einheit)` interpretiert die vorhandenen Zahlen als kanonische SI-Werte der gewählten Dimension (verlustfrei, sofern die Dimension passt); `number (mit Einheit) → number` entfernt die Einheits-Information aus der Definition, die Werte bleiben als kanonische SI-Zahlen erhalten.

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
- Sprachabhängige Werte werden als verschachtelte JSON-Objekte mit Sprachschlüsseln gemäß Sprachbaum (siehe [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md)) gespeichert.
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
    "is_key": false,                 // true: Business-Schlüssel (z. B. SKU), eindeutig pro Typ
    "applicable_types": [],          // optional: Objekttypen, für die das Attribut gilt (leer = alle)
    "required_types": ["product"],   // optional: Objekttypen, für die das Attribut Pflicht ist (Vollständigkeit)
    "computed": false,                // true: berechnetes Attribut – Wert wird von einer compute-Regel geschrieben
    "computed_rule": "",             // Name der business_rule, die dieses Attribut setzt (nur bei computed)
    "dependencies": [],               // Eingabeattribute, die die Formel liest (für Recompute-Kaskade)
    "computed_reads_inherited": false,// true: Formel liest vererbte Werte -> async Kaskade auf Nachfahren
    "multilingual": false,            // optional: kennzeichnet sprachabhängige Felder
    "constraints": {
        "min": 0,
        "max": 999999,
        "enum_values": ["red", "green", "blue"]    // optional für geschlossene Wertelisten
    },
    "version": 1,
    "created_at": "2026-08-14T08:00:00Z",
    "updated_at": "2026-08-14T11:30:00Z",
    "updated_by": "user_1"
}
```

- `type` bestimmt den Datentyp (`text`, `number`, `boolean`, `date`, `enum`, `reference`). Die Werteliste ist erweiterbar; bestehende Typen werden nicht umbenannt. Ein späterer Wechsel des Typs ist in [Datentypwechsel](#datentypwechsel) beschrieben.
- `inheritable` steuert, ob das Attribut in die Datenwertvererbung einbezogen wird (siehe [datenwertvererbung.md](datenwertvererbung.md)).
- `is_key` kennzeichnet ein Attribut als **Business-Schlüssel** (z. B. SKU): nur `text`, nicht lokalisiert, nicht vererbbar; eindeutig pro Objekttyp (anwendungsseitig beim Schreiben erzwungen, konzeptionell `(type, attributes.<name>)`). Details siehe [Schlüssel (Business-Schlüssel)](#schlüssel-business-schlüssel).
- `applicable_types` (leer = alle Typen) bestimmt, für welche Objekttypen das Attribut im Formular/Grid erscheint – die **Produktvorlage** pro Typ (siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md)).
- `required_types` bestimmt, für welche Objekttypen das Attribut ein Pflichtfeld ist und damit in die **Vollständigkeit** (Completeness, pro Typ × Sprache) eingeht (siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#4-vollständigkeit-completeness)).
- `computed`/`computed_rule`/`dependencies`/`computed_reads_inherited` kennzeichnen **berechnete Attribute** (Abschnitt 8 / [business-rules.md](business-rules.md)): Der Wert wird beim Schreiben **synchron** von einer `computed`-Regel berechnet und gespeichert (stored-first, kein On-Read-Modus); `dependencies` deklarieren die Eingaben der Formel (Grundlage der Recompute-Kaskade; async bei `computed_reads_inherited`).
- Weitere optionale Felder: `multilingual`, `default_value`, `unit_id`, `display_unit_id`, `description`; die geschlossene Werteliste `enum_values` liegt optional in `constraints` (siehe Beispiel). Die Felder `unit_id`/`display_unit_id` gelten für numerische Werte mit Einheit (siehe [einheiten.md](einheiten.md)).

### Tabelle `business_rule`

Definiert Business Rules (Python-Skripte). Details zu Script-Kontrakt, Sandbox, Ausführung, Lebenszyklus und Rule Lab in [business-rules.md](business-rules.md) (Abschnitt 8).

```json
{
    "id": "rule_001",
    "name": "gross_from_net",
    "type": "computed",              // validation | computed | normalization | workflow_guard | bulk
    "applies_to": ["product"],       // optional: Objekttypen (leer = alle)
    "script": "def run(obj, ctx, result):\n    ...",
    "state": "active",               // draft | active | disabled
    "version": 3,
    "created_at": "2026-08-17T08:00:00Z",
    "updated_at": "2026-08-17T11:30:00Z",
    "updated_by": "user_1"
}
```

- `type` bestimmt, wie das Skriptergebnis interpretiert wird (`validation`/`computed`/`normalization`/`workflow_guard`/`bulk`); nur `active`-Regeln werden ausgeführt.
- `applies_to` schränkt die Objekttypen ein (leer = alle); die Prüfung erfolgt bei der Auswertung.
- `script` wird in einer RestrictedPython-Sandbox ausgeführt (Import-Whitelist, Timeout-/Speicher-/Größen-Limits, keine externe I/O); ein **Compile-Check bei jedem Speichern** lehnt unzulässige Skripte ab.
- Seed-Regeln werden als Code ausgeliefert und beim Setup geseedet; danach werden sie als Daten im Admin-UI gepflegt.

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

Referenzen zwischen Objekten (Graph-Kanten) mit eigenen Metadaten. Der Relationstyp wird über `definition_id` aus der Tabelle `relation_definition` referenziert; `type` bleibt als denormalisierter Anzeigename erhalten. Das Relationenmodell ist in [relationen.md](relationen.md) spezifiziert:

- `depends_on` (Komposition) → Datenwertvererbung (siehe [datenwertvererbung.md](datenwertvererbung.md)); genau eine Kante pro Objekt
- `category_member` (Aggregation) → Kategorisierung (siehe [kategorisierung.md](kategorisierung.md))
- `collection_member` (Aggregation) → materialisierte Collections (siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md))
- `related` (Aggregation) → lose Verknüpfung ohne Sonderlogik

```json
{
    "id": "reference:01M0…",
    "in": "object:01M0…",              // Quelle (früher `from`); volle Record-ID
    "out": "object:01M0…",             // Ziel (früher `to`); volle Record-ID
    "definition_id": "rel_depends_on", // Record-Key der relation_definition
    "type": "depends_on",            // Anzeigename (denormalisiert)
    "attributes": {                  // Metadaten an der Kante
        "position": 3                // z. B. bei Kategorien: Sortierung
    }
}
```

### Tabelle `relation_definition`

Definiert die Relationstypen des Systems (Details in [relationen.md](relationen.md)).

```json
{
    "id": "rel_depends_on",
    "name": "depends_on",            // einziger Kompositionstyp
    "kind": "composition",           // composition | aggregation
    "source_types": [],              // optional: erlaubte Quell-Objekttypen (leer = alle)
    "target_types": [],              // optional: erlaubte Ziel-Objekttypen (leer = alle)
    "label": { "de": "Abhängig von", "en": "Depends on" },
    "system": true                   // eingebaut (true) oder benutzerdefiniert (false)
}
```

- `kind` bestimmt die Semantik: `composition` (1:n inkl. 1:1, genau eine Kante pro Objekt, Vererbung & Kaskade) oder `aggregation` (n:m, lose Verknüpfung).
- `source_types`/`target_types` schränken die erlaubten Objekttypen ein (leer = alle Typen erlaubt); die Prüfung erfolgt beim Anlegen jeder Relation.
- Eingebaute Typen (`system: true`) werden in Phase 1–2 geseedet: `depends_on` (Komposition) sowie `category_member`, `collection_member`, `related` (Aggregation). Benutzerdefinierte Typen (Phase 9) sind immer Aggregationen.

### Tabelle `language_tree`

Definiert den Sprachbaum für sprachabhängige Attributwerte (Details in [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md)).

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
    "type": "outbound_export",       // outbound_export | inbound_import | business_rule | bulk_operation
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

- Der Triggerwert `event` (Event-getriggerte Jobs) ist **reserviert** für Phase 5+; im MVP sind nur `schedule` und `manual` aktiv (Details [jobs-scheduler.md](jobs-scheduler.md)).

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

### Tabelle `outbox_event`

**Transactional Outbox** für CRUD-Events (Details in [integration-eventsystem.md](integration-eventsystem.md)): Events werden **atomar mit der CRUD-Operation** in dieser Tabelle geschrieben und anschließend asynchron durch ein **Relay im Kern** nach NATS JetStream publiziert. Dadurch ist die API nie von NATS abhängig; es geht kein Event verloren.

```json
{
    "id": "outbox_001",
    "event_type": "object.updated",
    "object_type": "product",
    "object_id": "object_123",
    "changes": { "attributes.price": { "old": 19.99, "new": 24.99 } },
    "metadata": { "user_id": "user_1", "request_id": "req_456" },
    "status": "pending",           // pending | published
    "created_at": "2026-08-14T11:30:00Z",
    "published_at": null            // gesetzt, sobald das Relay publiziert hat
}
```

- Das Relay liefert die ältesten `pending`-Einträge zuerst aus (Index `status` + `created_at`) und setzt nach erfolgreichem Publish `published_at`.
- Publizierte Einträge werden nach Ablauf einer Haltefrist vom Relay aufgeräumt (Cleanup).

### Zukünftige Objekttypen & Tabellen (nicht im MVP, aber konzeptionell vorgesehen)

- **`saved_search`**: Objekttyp (`type: "saved_search"`) für komplexe Suchanfragen (siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md)); wird in der Tabelle `object` gespeichert.
- **`collection`**: Objekttyp (`type: "collection"`) für materialisierte Ergebnislisten auf Basis gespeicherter Suchen (siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md)); wird in der Tabelle `object` gespeichert.
- **`release`**: Eigene Tabelle für kanalspezifische Freigaben (Phase 9, siehe [workflow-freigaben.md](workflow-freigaben.md)).

## Indizes & Suche

- **Objekt-Indizes:**
  - auf `type`, `status`, `created_at`, `updated_at`
  - auf häufig gefilterte Attributfelder, z. B. `attributes.name.de`, `attributes.price`
  - auf sprachabhängige Felder je nach Konfiguration (z. B. alle Sprachen oder nur `root`/`en`)
  - **Eindeutigkeit pro Business-Schlüssel** (`is_key: true`): konzeptionell komposit `(type, attributes.<name>)`, z. B. `(type, attributes.sku)` – technisch **anwendungsseitig beim Schreiben** erzwungen (dynamische Attributnamen können nicht statisch indiziert werden); ein dynamischer Unique-Index ist eine spätere Ausbaustufe (Phase 3/9)
- **Referenz-Indizes:**
  - auf `definition_id` (bzw. `type`), `in`, `out`
  - kombinierte Indizes für `in` + `definition_id` und `out` + `definition_id` (z. B. für Kategorieabfragen)
  - speziell für `depends_on` und `category_member`
- **Volltextsuche:**
  - SurrealDB-eigene Volltextindizes (Syntax `FULLTEXT ANALYZER …`, seit SurrealDB 3.0; zuvor `SEARCH ANALYZER`). **Analyzer existieren nicht vordefiniert** – zuerst `DEFINE ANALYZER … TOKENIZERS … FILTERS …` anlegen.
  - **MVP (Phase 2):** ein einziger Fulltext-Index über das gesamte `attributes`-Objekt (`DEFINE INDEX … FIELDS attributes FULLTEXT ANALYZER meadow_std BM25`); Suche per `attributes @@ $q` mit `search::score`. Deckt alle Attributwerte/Sprachen ab, ohne die Attributnamen kennen zu müssen.
  - Ein `FULLTEXT`-Index deckt **immer nur ein Feld** ab – pro Feld (bzw. pro Sprachfeld wie `attributes.description.de`) ist ein eigener `DEFINE INDEX` nötig. Per-Feld-Indizes und sprachspezifische Suche (Sprachbaum) sind eine spätere Verfeinerung (Phase 3/9).
- **Graph-Traversierung:**
  - Indizes auf Relationen ermöglichen performante Traversierung in beide Richtungen
  - Da alle Relationen in einer generischen `reference`-Tabelle liegen, erfolgt die Traversierung über typisierte Edge-Queries, z. B. `->(reference WHERE definition_id = 'rel_depends_on')->object` bzw. `<-()<-object` in Gegenrichtung
- **Weitere Indizes (vorbereitet):**
  - auf `saved_search.status`, `collection.status` für spätere Funktionen

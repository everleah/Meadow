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
- **Verwendung (MVP):** Logging-Einstellungen (`log_persist_level`, `log_persist_categories`, `log_retention_days`, `log_rate_limit`) und Regel-Einstellungen (`rules_degrade`, `rules_breaker_threshold`, `rules_timeout_ms`); das Schema ist generisch und für weitere Laufzeit-Einstellungen erweiterbar.
- **Zugriff:** `GET/PUT /system/settings` (nur `admin`/`superadmin`); Änderungen im Audit-Log.

## Datumswerte

Alle Datums- und Zeitwerte folgen einer einheitlichen, verbindlichen Konvention (Abschnitt 4 des Hauptdokuments). Speicherung, Austausch und Darstellung sind strikt getrennt:

1. **Speicherung (Datenbank):** Alle Datums- und Zeitwerte werden **immer als UTC** in SurrealDB gespeichert – ohne lokale Zeitzone. Betroffen sind u. a. `created_at`, `updated_at`, `started_at`, `finished_at`, Audit-`timestamp` und alle weiteren Zeitfelder (Datentyp `datetime` bzw. ISO-8601-String mit `Z`).
2. **Austausch (API / Inbound / Outbound):** Zeitwerte werden über die REST-API, im MEF-Format und in Event-Nachrichten **immer als ISO-8601-String mit Zeitzonenangabe** übertragen, z. B. `2026-08-14T10:00:00Z` (UTC) oder `2026-08-14T12:00:00+02:00` (mit Offset). Strings **ohne** Zeitzonenangabe werden abgelehnt. Beim Empfang wird nach UTC normalisiert, bevor gespeichert wird; beim Senden wird UTC als `…Z` ausgegeben.
3. **Darstellung (Frontend):** Die GUI zeigt Datums-/Zeitwerte **lokalisiert** an – in der Zeitzone und im Format des Benutzers (z. B. `14.08.2026, 12:00` für `de-DE`). Formatierung über `babel` (Details in [frontend.md](frontend.md)); der gespeicherte Wert bleibt unverändert UTC.

> **Hinweis:** Alle Zeitfelder in den Beispielen dieses Dokuments sind als ISO-8601-UTC-Strings dargestellt.

## Werte mit Einheiten

Numerische technische Werte (z. B. Breite, Spannung, Leistung) können eine Einheit tragen. Der zugehörige **Einheiten-Katalog** liegt in der Tabelle `unit`; Speicher-Konvention (kanonische SI-Einheit), Umrechnung und benutzerdefinierte Einheiten sind im Detaildokument [einheiten.md](einheiten.md) beschrieben. In der Tabelle `object` werden solche Werte als **reine Zahlen in der kanonischen SI-Einheit** gespeichert; die Einheit ergibt sich aus der Attributdefinition (`unit_id`, optional `display_unit_id`).

## Objekttypen (Registry)

Objekttypen sind **verwaltete** Geschäftstypen (STIBO STEP / Akeneo Familien-Modell, **flach** – keine Typvererbung). Sie stehen in der Tabelle `object_type`:

```json
{
    "id": "object_type:product",
    "name": "product",
    "label": { "de": "Produkt", "en": "Product" },
    "description": "",
    "key_attributes": ["sku"],
    "label_attribute": "name",
    "system": false
}
```

- `name` ist der Objekttyp-Code (gleich dem `type`-Feld am Objekt), eindeutig.
- `key_attributes` sind die **eindeutigen + Pflicht-Schlüssel** für diesen Typ (leer = kein Schlüssel). Ein Typ, der einen Schlüssel deklariert, behandelt ihn als **Pflicht** (Completeness/Formular).
- `label_attribute` ist das Attribut, das als Anzeigename dient (optional, Akeneo „Label-Attribut“).
- `system` kennzeichnet **vom System geseedete/Standard-Typen** (`true`); diese sind unveränderbar geschützt und können **nicht gelöscht** werden (400). Benutzerdefinierte Typen (`system: false`) sind löschbar, solange keine Objekte sie verwenden (409).
- `key_attributes`/`label_attribute` dürfen nur auf `text`-Attribute (nicht mehrsprachig) verweisen.
- Unbekannte Typen werden beim ersten Anlegen eines Objekts automatisch registriert (`ensure_registered`, `system: false`).
- **Abgrenzung:** Die **Wert-Vererbung** (`depends_on`) ist objekt-ebene Vererbung von Werten entlang der Komposition – **kein** Typ-Konzept. Gemeinsame Attribute über Typen hinweg entstehen über Attributdefinitionen mit `applicable_types: []` (leer = alle Typen). Verwaltung über `GET/POST/PATCH/DELETE /object-types` (Read editor+, Write admin) und die Admin-Seite `/object-types`.

## Schlüssel (Business-Schlüssel)

Ein **Business-Schlüssel** (z. B. SKU) dient dem Anwender als fachliche Kennung – für ihn äquivalent zum technischen Primärschlüssel (`id`). Der Wert liegt normal im `attributes`-Feld; es gibt kein separates Feld und keine eigene Tabelle. **Autoritativ ist die Zuordnung pro Objekttyp** (`object_type.key_attributes`); das `is_key`-Flag an der Attributdefinition ist eine zusätzliche Markierung/Validierung.

**Regeln (verbindlich, Konvention gemäß Abschnitt 4 des Hauptdokuments):**

1. **Zuordnung pro Typ:** Welches Attribut Schlüssel ist, legt der **Objekttyp** fest (`object_type.key_attributes`). Ein Typ kann **ohne** Schlüssel existieren (dann ist nichts schlüssel-pflichtig). Deklariert er einen Schlüssel, zählt das Attribut für diesen Typ als **Pflicht**.
2. **Eindeutigkeit pro Typ:** Der Kern erzwingt Eindeutigkeit pro Objekttyp **beim Schreiben anwendungsseitig** (Abfrage `(type, attributes.<name>)`, exakt/case-sensitiv, getrimmt) – ein statischer kompositer Unique-Index ist wegen der dynamischen Attributnamen im flexiblen `attributes`-Objekt nicht möglich. Leere/fehlende Werte kollidieren nicht (mehrere Objekte dürfen keinen Schlüssel haben).
3. **Mehrere erlaubt:** Ein Typ kann mehrere Schlüssel führen (z. B. `sku`, `ean`), jeweils mit eigener Eindeutigkeit pro Typ.
4. **Optional auf Typ-Ebene:** Ein Objekttyp muss keinen Schlüssel haben; Objekte dürfen ohne Schlüssel existieren (Migration und Erstbefüllung ungehindert).
5. **Datentyp & Form:** Nur `text`, nicht lokalisiert (kein `multilingual`); Vergleich exakt (case-sensitive), beim Speichern wird getrimmt.
6. **Keine Vererbung:** Schlüssel sind nie vererbbar – ein Schlüssel-Attribut erzwingt `inheritable: false` (eine Vererbung würde Identitäten duplizieren).
7. **Änderbar:** Schlüsselwerte sind änderbar; bei jeder Änderung prüft der Kern die Eindeutigkeit neu und protokolliert die Änderung im Audit-Log.
8. **Adressierung & Import:** Die API bietet einen Key-Lookup-Endpunkt; der MEF-Import kann per Schlüssel abgleichen (Upsert) – Details in [api-design.md](api-design.md) und [integration-eventsystem.md](integration-eventsystem.md).

> **Hinweis:** Für Schlüsselattribute gilt der Datentypwechsel nur eingeschränkt: Ein Schlüssel muss `text` bleiben, deshalb wird ein Typwechsel mit **409** abgelehnt, solange `is_key` gesetzt ist (erst `is_key` entfernen). Die `is_key`-Markierung selbst wird weiterhin per `PATCH /attribute-definitions/{id}` gepflegt.

## URL-Attribute

> **Stand:** umgesetzt (Phase 9) — Typ `url` in `attribute_definition.type`, Validierung im Kern (`attribute_values.rs`), Link-Darstellung in Grid und Formular (`meadow/frontend/url_values.py`).

Ein `url`-Attribut speichert eine **Webadresse** — Website, Produktseite, Datenblatt, Bild- oder Videolink. Es verhält sich wie `text` (auch `multilingual`, `inheritable`, `default_value` und `constraints` sind erlaubt), wird aber **validiert** und in der UI als **Link** dargestellt.

**Regeln (Kern, verbindlich):**

1. **Nur echte URLs:** Akzeptiert wird ausschließlich eine absolute Adresse mit Schema `http` oder `https` **und** Host — geprüft mit dem WHATWG-URL-Parser (`url`-Crate, `attribute_values::is_valid_url`). Abgelehnt werden u. a. `example.com` (relativ), `ftp:`/`mailto:`/`javascript:`/`data:` (anderes Schema), `https://` (ohne Host) und unparsbarer Text.
2. **Geprüft wird beim Schreiben:** `POST /objects`, `PATCH /objects/{id}` und `POST /objects/batch-update` antworten mit **400** und Pfad im Attribut, z. B. `` `attributes.homepage`: must be an absolute http(s) URL `` (mehrsprachig: `` `attributes.homepage.de`: … ``). Die Prüfung sitzt vor den Business Rules und deckt damit auch Worker-Schreibpfade (Bulk, Import) ab.
3. **Kein Wert ist erlaubt:** `null` und der leere String löschen den Wert bzw. brechen die Vererbung (wie bei jedem anderen Typ); `{ "de": null }` bleibt gültig.
4. **Werte in Maps:** Bei `multilingual` liegt `{ sprache: URL }` vor; jede Sprache wird einzeln geprüft, Nicht-Strings werden abgelehnt.
5. **Kein Schlüssel:** `is_key` bleibt `text` vorbehalten (unverändert).
6. **Speicherung unverändert:** Der Wert bleibt der rohe String (kein Normalisieren, kein Umschreiben), damit Import/Export und MEF-Roundtrips verlustfrei bleiben.

**Darstellung (UI):**

- **Grid:** Die Spalte eines `url`-Attributs (auch je Sprache) rendert den Wert als **Link** (`<a target="_blank" rel="noopener noreferrer">`); die Zelle bleibt inline editierbar (Text).
- **Bild/Video:** Endet die Adresse auf eine Bild- bzw. Video-Endung, wird sie **nicht** verlinkt, sondern als klickbare Zelle dargestellt — ein Klick öffnet den **NiceGUI-Vorschau-Dialog** (Bild eingebettet, Video mit Player, plus „In neuem Tab öffnen“). Die Entscheidung trifft Python (`url_values.media_kind`), das Zell-Rendering erhält sie über die Zeilenmarkierung `_media` — eine Quelle der Wahrheit.
- **Formular:** Feld mit `type=url` und Button „Öffnen“ (Bild/Video → Dialog, sonst neuer Tab); der Button ist deaktiviert, solange kein gültiger Wert im Feld steht.
- **Suche:** `url`-Felder sind filterbar wie Text (`GET /search-fields` meldet `value_type: "url"` mit den Text-Operatoren plus `in`/`not_in`).
- **Massenpflege:** `url` zählt als **stringwertiger** Typ und ist damit im **Find & Replace** wählbar (Domain-/CDN-Präfix umschreiben); der Kern validiert das Ergebnis, ein kaputter Ersatz wird pro Objekt abgelehnt (Report) statt still geschrieben.
- **Export:** In **XLSX** werden Zellen von `url`-Spalten (auch je Sprache) als **Hyperlink** geschrieben (blau/unterstrichen) — ein Nicht-`url`-Attribut mit URL-Text bleibt Text. CSV/JSON bleiben unverändert (Wert = URL).

## Datentypwechsel

> **Stand:** umgesetzt (Phase 9) — Endpunkt `POST /attribute-definitions/{id}/type-change` (Dry-Run + Anwendung), Migration der Werte in einer Transaktion, Audit + Event, Dialog im Frontend.

Der Datentyp eines Attributs wird über `type` in der `attribute_definition` festgelegt (`text`, `url`, `number`, `boolean`, `date`, `enum`, `reference`) und optional über Modifikatoren (`multilingual`, `unit_id`/`display_unit_id`) präzisiert. Die Werte selbst werden **generisch als JSON** im `attributes`-Feld der Objekte gespeichert – es gibt keine typgebundenen Spalten und keine typspezifischen, verlustbehafteten Speicherformate. **`url`** ist die erste Ausnahme bei der Schreibvalidierung: der Kern akzeptiert nur absolute `http(s)`-URLs (siehe [URL-Attribute](#url-attribute)).

**Grundsatz:** Der Datentyp ist eine **Eigenschaft der Definition, nicht der gespeicherten Werte**. Genau deshalb ist ein Wechsel möglich — aber nur **mit Migration aller vorhandenen Werte**, geprüft gegen die Kompatibilitätsmatrix.

**Entwurfsregeln (verbindlich):**

1. Werte werden **roh und umkehrbar** als JSON gespeichert: Zahl als Zahl, Text als String, Mehrsprachig als Sprachobjekt, Einheitenbehaftet als kanonische SI-Zahl. Keine Stringifizierung von Zahlen, keine Auflösung des Sprachobjekts auf eine Einzelsprache beim Speichern.
2. Der Typ wird **ausschließlich** in der `attribute_definition` geführt und nirgends redundant am Wert „festgeschrieben“.
3. Validierung und Umrechnung erfolgen **zur Lese-/Schreibzeit** anhand der Definition; die gespeicherten Rohwerte bleiben dabei erhalten bzw. normalisiert, aber umkehrbar.
4. Die `type`-Werteliste ist **offen und erweiterbar**: Neue Typen dürfen ergänzt, bestehende Typen nicht umbenannt werden (Stabilität für bestehende Daten, Exporte und Events).

**Kompatibilitätsmatrix:** Nicht jeder Wechsel ist möglich oder verlustfrei. Umgesetzt ist die folgende Matrix; jeder Wechsel umfasst eine **Migration aller vorhandenen Werte**, einen **Audit-Log-Eintrag** und ein Event.

| von \ nach  | `text`                         | `url`                                  | `number`               | `boolean`                             | `date`                            | `enum`                            | `reference`                  |
| ----------- | ------------------------------ | -------------------------------------- | ---------------------- | ------------------------------------- | --------------------------------- | --------------------------------- | ---------------------------- |
| `text`      | —                              | ⚠️ nur gültige `http(s)`-URLs          | ⚠️ nur parsebare Zahlen | ⚠️ nur`true`/`false`-konforme Werte   | ⚠️ nur ISO-8601-Datum (`JJJJ-MM-TT`) | ⚠️ nur Codes der Optionsliste   | ⚠️ nur `object:<key>`-IDs    |
| `url`       | ✅ verlustfrei                 | —                                      | ❌                     | ❌                                    | ❌                                | ❌                                | ❌                           |
| `number`    | ✅ verlustfrei (kanonisch)     | ❌                                     | —                      | ❌                                    | ❌                                | ❌                                | ❌                           |
| `boolean`   | ✅ verlustfrei (`true`/`false`) | ❌                                     | ❌                     | —                                     | ❌                                | ❌                                | ❌                           |
| `date`      | ✅ verlustfrei (ISO-8601)      | ❌                                     | ❌                     | ❌                                    | —                                 | ❌                                | ❌                           |
| `enum`      | ✅ verlustfrei (Code)          | ❌                                     | ❌                     | ❌                                    | ❌                                | —                                 | ❌                           |
| `reference` | ✅ verlustfrei (als ID-String) | ❌                                     | ❌                     | ❌                                    | ❌                                | ❌                                | —                            |

- ✅ = verlustfrei möglich; ⚠️ = möglich, aber abhängig von den vorhandenen Werten (nicht konforme Werte → Ablehnung oder, mit `drop_incompatible`, Löschung dieser Werte); ❌ = nicht sinnvoll/unzulässig (bleibt die Antwort auf den Wechsel eine `400` mit Liste der erlaubten Ziel-Typen).
- **Nur Text-Ziele sind Sammelziele** (`text`): alles ist vertextbar, weil nur die Darstellung wechselt. Zahlen sind bewusst **nicht** aus `boolean`/`date`/`enum` ableitbar (keine stillen Interpretationen).
- **Mehrsprachigkeit** (`multilingual`) ist **nicht** Teil des Wechsels: sie ändert die Wertstruktur (String ↔ Sprachobjekt) und braucht eine eigene, verlustentscheidende Migration (Auswahl einer Ausgangs-/Zielsprache). Sie bleibt eine eigene Ausbaustufe; der Wechselendpunkt lehnt eine gleichzeitige Änderung nicht ab, weil er sie gar nicht annimmt (nur `type` + Ziel-Modifikatoren).
- **Modifikatoren:** Beim Verlassen von `enum` wird `enum_set_id` (und die Legacy-Liste `constraints.enum_values`) **geleert**; beim Verlassen von `number` werden `unit_id`/`display_unit_id` **geleert**. Beim Wechsel **nach** `enum` ist ein `enum_set_id` Pflicht (Auswahl im Request), nach `number` dürfen Einheiten mitgegeben werden.

### Ablauf & API

`POST /attribute-definitions/{id}/type-change` (`admin`/`superadmin`), Query `?dry_run=true|false`, Body:

```json
{
  "type": "number",
  "enum_set_id": "enum_set:colors",
  "unit_id": "unit_m",
  "display_unit_id": "unit_cm",
  "drop_incompatible": false
}
```

1. **Vorbedingungen:** Das Attribut darf nicht `computed` sein (der Wert gehört einer Regel – erst die Regel anpassen, dann der Typ; `409`). Ein `is_key`-Attribut bleibt nur dann Schlüssel, wenn das Ziel `text` ist, sonst `409` („erst `is_key` entfernen“). Ziel-Typ muss in der Matrix stehen (`400`); ein Ziel-`enum` braucht ein **existierendes** `enum_set_id` (Auswahl im Request), Einheiten werden wie beim Anlegen durchgereicht.
2. **Prüfen (immer, auch beim Dry-Run):** Alle Objekte mit einem Wert für das Attribut werden geladen und **jeder** Wert mit `convert_value(from, to, value)` in den Ziel-Typ überführt. Nicht konforme Werte werden gesammelt. Das Ergebnis ist ein **Report** `{name, from, to, checked, convertible, incompatible, samples[], dropped, dry_run}` (`samples` = bis zu 10 `{object, value, reason}`).
3. **Anwenden:** Nur wenn `incompatible == 0` **oder** `drop_incompatible: true` gesetzt ist. Dann passieren Definition-Update und Wertmigration in **einer Transaktion**: `UPDATE $def MERGE $changes` plus eine Schleife über die konvertierten Werte (`FOR $row IN $rows { UPDATE … SET attributes[$name] = $row.value; }`, wie beim Collection-Refresh). Nicht konforme Werte werden dabei **entfernt** (`attributes[$name] = NONE`), gezählt als `dropped`.
4. **Nach dem Schreiben:** Audit `attribute_definition_type_changed` (`{name, from, to, converted, dropped}`), Outbox-Event `attribute_definition.updated`, Cache-Invalidierung (Feldkatalog, `url`- und `multilingual`-Caches) – damit ziehen Suche, Formular und Grid sofort nach.
5. **Fehlerantworten:** `400` unzulässiger Ziel-Typ/fehlende Ziel-Modifikatoren, `404` unbekanntes Attribut, `409` `computed`/`is_key`-Konflikt oder nicht konforme Werte (mit `checked`/`convertible`/`incompatible` und Beispielen).

**Konvertierung (rein, unit-getestet in `attribute_definition_types.rs`):**

- **nach `text`:** Zahl → kanonische Dezimaldarstellung, Boolean → `true`/`false`, `date`/`enum`/`reference`/`url` → unverändert (sind schon Strings).
- **`text` → `url`:** muss `is_valid_url` erfüllen (derselbe Prüfer wie beim Schreiben).
- **`text` → `number`:** getrimmt parsebar als Zahl (Ganzzahl bleibt Ganzzahl, sonst Fließkomma).
- **`text` → `boolean`:** exakt `true`/`false` (kein `1`/`0`/`yes`).
- **`text` → `date`:** exaktes `JJJJ-MM-TT` mit gültigen Monats-/Tageswerten (gespeichert wird weiter der ISO-String, wie beim Typ `date` üblich).
- **`text` → `enum`:** Wert muss ein Code der Optionsliste des Ziel-Sets sein.
- **`text` → `reference`:** muss die Form `object:<key>` haben (Formprüfung; die referenzierte Existenz prüft der Wechsel bewusst nicht — Referenzen sind Aggregationen und dürfen temporär ins Leere zeigen).
- **Mehrsprachige Attribute:** Die Konvertierung läuft **je Sprache** (jeder Eintrag der Sprachkarte einzeln); ein nicht konformer Eintrag macht nur diesen Eintrag zum Problem.

**Beispiele (verifiziert):**

| Ausgangswert | Wechsel | Ergebnis |
|---|---|---|
| `"12.5"`, `"7"` | `text → number` | `12.5`, `7` |
| `"true"` | `text → boolean` | `true` |
| `"2026-09-12"` | `text → date` | `"2026-09-12"` (unverändert) |
| `"red"` | `text → enum` (Set mit `red`) | `"red"` |
| `"example.com"` | `text → url` | `409` (kein absolutes `http(s)`-Ziel) |
| `12.5` | `number → text` | `"12.5"` |

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
    "updated_by": "user_1",
    "source_id": "ext-123",           // optionale Quell-Referenz (MEF-`id`), für Import-Dedup ohne Schlüssel
    "source": "middleware"            // optionales Quellsystem (MEF-`source`)
}
```

- `type` ist frei wählbar und bestimmt die fachliche Rolle des Objekts.
- Sprachabhängige Werte werden als verschachtelte JSON-Objekte mit Sprachschlüsseln gemäß Sprachbaum (siehe [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md)) gespeichert.
- `status` dient dem einfachen Workflow (Abschnitt 8).
- **Quell-Referenz (`source_id` + `source`, optional):** externe Identität des Objekts; der MEF-Import nutzt sie für den idempotenten Abgleich von Objekten **ohne** Business-Schlüssel (`GET /objects/by-source`), Details [integration-eventsystem.md](integration-eventsystem.md).

### Tabelle `attribute_definition`

Definiert Metadaten-Attribute (Name, Typ, Gruppe, Validierung, Wertelisten, Mehrsprachigkeit, Vererbbarkeit).

```json
{
    "id": "attr_def_001",
    "name": "price",
    "label": { "de": "Preis", "en": "Price" },
    "type": "number",                // text, url, number, boolean, date, enum, reference, ...
    "group_ids": ["attr_group_01"],  // viele-zu-viele: Attribut kann in mehreren Gruppen sein
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

- `type` bestimmt den Datentyp (`text`, `url`, `number`, `boolean`, `date`, `enum`, `reference`). Die Werteliste ist erweiterbar; bestehende Typen werden nicht umbenannt. Ein Wechsel des Typs ist über einen eigenen Endpunkt möglich (siehe [Datentypwechsel](#datentypwechsel)).
- `group_ids` (viele-zu-viele): Ein Attribut kann in **mehreren** Attributgruppen erscheinen; die Zuordnung wird als **Inline-Array der Group-Keys** direkt am `attribute_definition`-Datensatz gespeichert (keine separate Join-Tabelle). Am Objekt instanziert ist es **dasselbe** Attribut (ein Feld, mehrere UI-Gruppen). Ist `group_ids` leer/`[]`, gehört das Attribut keiner Gruppe („Attribute ohne Gruppe“). *Schema-Detail:* der Feldtyp ist `option<array>` – Datensätze, die vor der Gruppen-Erzwingung angelegt wurden, können das Feld noch nicht tragen (im Code als `group_ids ?? []` behandelt, d. h. fehlend == `[]`).*
- `inheritable` steuert, ob das Attribut in die Datenwertvererbung einbezogen wird (siehe [datenwertvererbung.md](datenwertvererbung.md)).
- `is_key` kennzeichnet ein Attribut als **Business-Schlüssel** (z. B. SKU): nur `text`, nicht lokalisiert, nicht vererbbar; eindeutig pro Objekttyp (anwendungsseitig beim Schreiben erzwungen, konzeptionell `(type, attributes.<name>)`). Details siehe [Schlüssel (Business-Schlüssel)](#schlüssel-business-schlüssel).
- `applicable_types` (leer = alle Typen) bestimmt, für welche Objekttypen das Attribut im Formular/Grid erscheint – die **Produktvorlage** pro Typ (siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md)). **Dies ist der zentrale Mechanismus, damit nicht jedes Attribut überall auftaucht.**
- `required_types` bestimmt, für welche Objekttypen das Attribut ein Pflichtfeld ist und damit in die **Vollständigkeit** (Completeness, pro Typ × Sprache) eingeht (siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#4-vollständigkeit-completeness)).
- `computed`/`computed_rule`/`dependencies`/`computed_reads_inherited` kennzeichnen **berechnete Attribute** (Abschnitt 8 / [business-rules.md](business-rules.md)): Der Wert wird beim Schreiben **synchron** von einer `computed`-Regel berechnet und gespeichert (stored-first, kein On-Read-Modus); `dependencies` deklarieren die Eingaben der Formel (Grundlage der Recompute-Kaskade; async bei `computed_reads_inherited`).
- Weitere optionale Felder: `multilingual`, `default_value`, `unit_id`, `display_unit_id`, `description`; die geschlossene Werteliste `enum_values` liegt optional in `constraints` (siehe Beispiel). Die Felder `unit_id`/`display_unit_id` gelten für numerische Werte mit Einheit (siehe [einheiten.md](einheiten.md)).
- **Löschen:** `DELETE /attribute-definitions/{id}` entfernt die Definition und ihre `attribute_group_member`-Links. Sind bereits Objekt-Attributwerte **aktiver** Objekte vorhanden, wird die Löschung mit HTTP 409 verweigert (soft-gelöschte Objekte zählen nicht — sie sind überall unsichtbar); `?force=true` entfernt die Werte asynchron als Hintergrundjob (Massenoperation, [jobs-scheduler.md](jobs-scheduler.md)).

### Enum-Sets (Reference Data)

> **Stand:** Die ursprüngliche String-Liste `constraints.enum_values` war eine **MVP/Übergangs-Repräsentation** (einsprachig). **Implementiert (Phase 9)** ist die Entkopplung von der Attributdefinition zu wiederverwendbaren, **mehrsprachigen** Enum-Entitäten im Akeneo-„Reference-Data“-Stil:

```json
// enum_set
{
    "id": "enum_set:01M0…",
    "name": "color",                     // eindeutig, wiederverwendbar
    "label": { "de": "Farbe", "en": "Color" },
    "options": ["enum_option:01M0…", "enum_option:01M1…"],
    "position": 0
}

// enum_option
{
    "id": "enum_option:01M0…",
    "code": "red",                       // stabil, sprachunabhängig, eindeutig im Set
    "labels": { "de": "Rot", "en": "Red", "fr": "Rouge" },
    "position": 0,
    "active": true
}

// attribute_definition (enum-Attribut)
{
    "type": "enum",
    "enum_set_id": "enum_set:01M0…",     // statt constraints.enum_values
    "constraints": {}                     // ggf. andere Restriktionen
}
```

- **`attribute_definition.enum_set_id`** referenziert ein `enum_set` (statt einer String-Liste). `constraints.enum_values` entfällt für neue Definitionen.
- **Objekte speichern weiterhin den `code`** (`attributes.color = "red"`) — sprachunabhängig, stabil; keine Datenspeicher-Migration der Werte.
- **Anzeige/Auswahl** löst das Label über die Sprach-Fallback-Kette der `language_tree` auf (`language::resolve_label`/`resolve_value`); Formular-Selakte und Farbige Tags zeigen das lokalisierte Label.
- **Verwaltung:** eigene CRUD-Endpunkte für `enum_set`/`enum_option` (inkl. Nutzungs-/Referenz-Check: Option nur entfernen, wenn weder Attribut noch Objekt sie nutzt bzw. mit Remap). Wiederverwendung: mehrere Attribute können dasselbe Set nutzen.
- **Migration:** vorhandene `constraints.enum_values`-Strings → `enum_option`-Datensätze (Code = Wert, `labels` vorerst = Wert) + `enum_set` erzeugen; `attribute_definition.enum_set_id` setzen.

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
    "failure_count": 0,              // Circuit-Breaker-Zähler (aufeinanderfolgende Fehler)
    "created_at": "2026-08-17T08:00:00Z",
    "updated_at": "2026-08-17T11:30:00Z",
    "updated_by": "user_1"
}
```

- `type` bestimmt, wie das Skriptergebnis interpretiert wird (`validation`/`computed`/`normalization`/`workflow_guard`/`bulk`); nur `active`-Regeln werden ausgeführt.
- `applies_to` schränkt die Objekttypen ein (leer = alle); die Prüfung erfolgt bei der Auswertung.
- `script` wird in einer RestrictedPython-Sandbox ausgeführt (Import-Whitelist, Timeout-/Speicher-/Größen-Limits, keine externe I/O); ein **Compile-Check bei jedem Speichern** lehnt unzulässige Skripte ab.
- `failure_count` zählt aufeinanderfolgende Regel-Fehler; ab dem Schwellwert (Standard 3) wird die Regel automatisch auf `disabled` gesetzt (Circuit Breaker, siehe [business-rules.md](business-rules.md)). Erfolgreiche Ausführung setzt den Zähler zurück.
- Seed-Regeln werden als Code ausgeliefert und beim Setup geseedet; danach werden sie als Daten im Admin-UI gepflegt.

### Tabelle `attribute_group`

Gruppiert Attributdefinitionen für die UI.

```json
{
    "id": "attr_group_01",
    "name": "pricing",
    "label": { "de": "Preise", "en": "Pricing" },
    "position": 2,
    "applicable_types": []           // optional: Objekttypen, für die die Gruppe erscheint (leer = aus Mitgliedern abgeleitet)
}
```

- `applicable_types` (optional, leer = **aus den Mitgliedern abgeleitet**): Eine Gruppe erscheint für einen Objekttyp, wenn `applicable_types` leer ist **oder** der Typ enthalten ist; bei leer wird die Sichtbarkeit aus den `applicable_types` ihrer Mitglieder bestimmt („Nur Gruppen mit mindestens einem für den Typ gültigen Attribut erscheinen“, ~frontend-arbeitsflaechen.md). So lässt sich die Gruppe pro Typ explizit einschränken, ohne die Attribut-Typen zu duplizieren.
- **Löschen:** `DELETE /attribute-groups/{id}` entfernt die Gruppe; dabei wird die Gruppen-Referenz aus dem `group_ids`-Array aller betroffenen Attributdefinitionen entfernt (Dangling-Referenzen werden aufgeräumt). Die Attribute bleiben erhalten (und liegen ggf. weiter in anderen Gruppen bzw. werden zu „ohne Gruppe“).

### Tabelle `attribute_group_member` *(nicht erforderlich — Inline `group_ids`)*

Ursprünglich als Viele-zu-viele-Zuordnung Attribut ↔ Gruppe geplant. **In der aktuellen Implementierung nicht verwendet:** Die Zuordnung wird als Inline-Array `group_ids` direkt am `attribute_definition`-Datensatz gespeichert (Typ `array`), sodass eine separate Join-Tabelle entfällt.

```json
{
    "id": "attr_group_member_001",
    "attribute_definition_id": "attr_def_001",   // record<attribute_definition>
    "attribute_group_id": "attr_group_01",       // record<attribute_group>
    "position": 1                                 // Reihenfolge innerhalb der Gruppe
}
```

- Eindeutigkeit über `(attribute_definition_id, attribute_group_id)`; bei Nutzung der Tabelle würde `group_ids` als Abkürzung serialisiert und intern auf diese Zeilen gemappt.
- Attribute ohne Gruppe hätten keinen Eintrag (`group_ids = []`).

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
    "attributes": [],                // Edge-Attribut-Namen (Phase 9)
    "system": true                   // eingebaut (true) oder benutzerdefiniert (false)
}
```

- `kind` bestimmt die Semantik: `composition` (1:n inkl. 1:1, genau eine Kante pro Objekt, Vererbung & Kaskade) oder `aggregation` (n:m, lose Verknüpfung).
- `source_types`/`target_types` schränken die erlaubten Objekttypen ein (leer = alle Typen erlaubt); die Prüfung erfolgt beim Anlegen jeder Relation.
- `attributes` (Phase 9) ist die Liste der Edge-Attribut-Namen (referenzieren `attribute_definition`); die Werte liegen auf der Kante in `reference.attributes[name]` und werden im Produkt-Formular typspezifisch gepflegt.
- Eingebaute Typen (`system: true`) werden in Phase 1–2 geseedet: `depends_on` (Komposition) sowie `category_member`, `collection_member`, `related` (Aggregation). Benutzerdefinierte Typen (Phase 9) sind immer Aggregationen und werden über `POST /relation-definitions` angelegt (`PATCH`/`DELETE /relation-definitions/{id}` verwalten/entfernen sie).

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
- `job_definition_id` ist **optional** (`option<string>`): Ad-hoc-Jobs ohne Definition (z. B. `POST /rules/{id}/recompute`) haben kein `job_definition_id`; im JSON wird das Feld weggelassen bzw. als `null` geliefert.

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

### Tabelle `import_document`

Speichert ein über `POST /imports` eingereichtes MEF-Dokument, bis es vom Worker-Konnektor verarbeitet wurde (Details [integration-eventsystem.md](integration-eventsystem.md)).

```json
{
    "id": "import_document_123",
    "document": { "format_version": "1.0", "objects": [ … ] },
    "format_version": "1.0",
    "source": "middleware",
    "created_at": "2026-08-14T10:00:00Z",
    "created_by": "user_admin"
}
```

- `document` trägt das rohe MEF-JSON (Objekt); der Job-Message-Payload referenziert nur `document_id`, damit auch große Dokumente (bis zum Body-Limit) nicht durch NATS übertragen werden müssen.

### Tabelle `connector`

Persistierte Konnektor-Konfiguration: die Datenbank ist die dauerhafte Quelle der Wahrheit für Inbound-/Outbound-Konnektoren; Umgebungsvariablen setzen nur den Initialwert (Details [integration-eventsystem.md](integration-eventsystem.md)).

```json
{
    "id": "connector:conn_webhook_middleware",
    "name": "middleware-webhook",
    "type": "webhook",           // webhook | json_import | json_export
    "enabled": true,
    "parameters": {
        "url": "https://middleware.example.com/api/meadow-events",
        "method": "POST",
        "headers": { "Authorization": "Bearer <token>" },
        "filter": { "event_types": ["object.updated"], "object_types": ["product"] },
        "retry_policy": { "max_attempts": 5, "backoff_seconds": [30, 60, 120, 300] },
        "coalesce": { "enabled": true, "window_seconds": 30 }
    },
    "created_at": "2026-08-14T10:00:00Z",
    "updated_at": "2026-08-14T10:00:00Z",
    "updated_by": "user_admin"
}
```

- `name` ist eindeutig und dient zugleich als Konnektor-Schlüssel des Workers; `parameters` ist ein freies JSON-Objekt (je Konnektor-Typ anders belegt).
- Verwaltung über `/connectors` (Admin) bzw. `GET /connectors` (Worker, `X-Api-Key`). Der Worker lädt Änderungen per **Live-Reload** nach (`serve_webhooks_dynamic`, Intervall `WEBHOOK_RELOAD_INTERVAL_S`, Standard 30 s) – ein Worker-Neustart ist nicht erforderlich.

### Tabelle `channel` (Kanal-Katalog)

Ein **Kanal** ist eine Ausgabestrecke (Webshop, Printkatalog, Marktplatz) und eine **Konfigurationsentität** wie Einheiten oder Enum-Sets — kein Fachobjekt. Er ist die Bezugsgröße für kanalspezifische Freigaben und (später) für kanalspezifische Vollständigkeit; für Exporte wirkt er als Filter. Details: [workflow-freigaben.md](workflow-freigaben.md).

```json
{
    "id": "channel:webshop",
    "code": "webshop",            // stabiler Schlüssel (Record-Key), eindeutig, unveränderlich
    "label": { "de": "Webshop", "en": "Web shop" },
    "description": "Hauptkanal",   // optional
    "active": true,
    "position": 10,
    "created_at": "2026-09-12T10:00:00Z",
    "updated_at": "2026-09-12T10:00:00Z",
    "updated_by": "user_admin"
}
```

- Der **Record-Key ist der `code`** (wie bei `object_type`); Referenzen (z. B. in `release`) speichern den Code, damit Konfigurationsdokumente und Filter portabel bleiben.
- `label` ist mehrsprachig (Sprach-Fallback-Kette); `code` ist unveränderlich — für Exporte und den MCF-Import der stabile Schlüssel.
- Unique-Index auf `code`, Sortier-Index auf `position`; die Sektion `channels` gehört zum [MCF-Export/-Import](konfiguration-export-import.md).

### Tabelle `release` (kanalspezifische Freigaben)

Die kanalspezifische Freigabe eines Objekts: **eine Zeile pro `(object, channel)`** mit dem Kanalzustand. Die Existenz der Zeile bedeutet „dieser Kanal ist bewertet/gepflegt“, `status: "published"` heißt „für den Kanal veröffentlicht“.

```json
{
    "id": "release:01M2AXFGPMZS2ASEPC9M50CYS4",
    "object_id": "object:01M2AXFGPMZS2ASEPC9M50CYS4",  // nativer Record (record<object>)
    "channel": "webshop",                              // Code des Kanals
    "status": "published",                             // Zustandswert des Workflows (nicht validiert)
    "released_at": "2026-09-12T13:39:00Z",
    "released_by": "user:admin"
}
```

- **Eindeutigkeit** über `(object_id, channel)` (Unique-Index); ein erneutes Setzen **ersetzt** die Zeile (Delete + Create in einer Transaktion), damit der Index immer hält.
- **Zurückziehen** = Zeile löschen; beim **Löschen des Objekts** werden seine Freigaben mitentfernt (Kaskade wie bei den `reference`-Kanten).
- `object_id` ist ein **nativer Record**, damit der Filter `GET /objects?released_for=<code>` eine Subquery (`SELECT VALUE object_id FROM release …`) ist; `channel` ist der **Code** (kein Record), damit Kanäle portabel referenziert werden.
- Indizes: `(object_id, channel)` unique und `(channel, status)` (Filter „für Kanal X veröffentlicht“). Details, Regeln und API: [workflow-freigaben.md](workflow-freigaben.md).

### Tabelle `category_attribute` (Klassen-Merkmale, Stufe B)

Die Merkmale einer Taxonomie-Klasse: **eine Zeile pro `(Klasse, Attribut)`**. Die Existenz der Zeile heißt „diese Klasse kennt das Merkmal“, `required: true` heißt „Pflicht-Merkmal der Klasse“. Merkmale gelten für alle der Klasse zugeordneten Objekte **und** für deren Unterklassen (Vorfahren-Kette über `depends_on`).

```json
{
    "id": "category_attribute:vwphwrbrprcw56drkrbf",
    "category_id": "object:01M2AXFGPMZS2ASEPC9M50CYS4",  // nativer Record (record<object>)
    "attribute": "power",                               // Name der Attributdefinition (portabel)
    "required": true,                                   // Pflicht-Merkmal dieser Klasse
    "position": 0
}
```

- **Eindeutigkeit** über `(category_id, attribute)` (Unique-Index); `attribute` ist der **Name** der Attributdefinition (wie der Kanal-`code` portabel für Konfigurationsdokumente).
- **Referenzielle Pflege:** beim **Löschen** einer Attributdefinition werden ihre Merkmals-Zeilen entfernt, beim **Umbenennen** mitgezogen; beim **Löschen** eines Klassenknotens kaskadieren seine Zeilen (wie die `release`-Zeilen).
- **Wirkung:** Pflicht-Merkmale zählen in die Vollständigkeit (`completeness`) und stehen Regeln als `ctx.missing_features` zur Verfügung; die Objektseite löst sie über `GET /objects/{id}/category-attributes` auf. Details: [kategorisierung.md](kategorisierung.md).

### Zukünftige Objekttypen & Tabellen (nicht im MVP, aber konzeptionell vorgesehen)

- **`saved_search`**: Objekttyp (`type: "saved_search"`) für komplexe Suchanfragen (siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md)); wird in der Tabelle `object` gespeichert.
- **`collection`**: Objekttyp (`type: "collection"`) für materialisierte Ergebnislisten auf Basis gespeicherter Suchen (siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md)); wird in der Tabelle `object` gespeichert.

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

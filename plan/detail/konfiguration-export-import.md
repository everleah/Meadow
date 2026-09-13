# Konfigurations-Export & Import (Meadow Configuration Format, MCF)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) – Erweiterung des Daten-Export/Imports (MEF) um die Übertragung des Datenmodells / der Referenzdaten.
- **Relevante Phasen:** Phase 9 (spätere Ausbaustufen), ergänzend zum MVP-Import/Export
- **Stand:** Umsetzungsreif (Implementierungsvorschlag). **Umsetzungsstatus:** `GET /config/export` und `POST /config/import?mode=dry_run|merge|replace` sind implementiert und E2E-verifiziert (`crates/pim-core/src/api/config.rs`): `merge` ist idempotent, `replace` löscht nur **target-only** Nicht-System-Einträge je im Dokument enthaltener Sektion (umgekehrte Abhängigkeitsreihenfolge; benutzte/system-Einträge bleiben erhalten und werden als Fehler gemeldet); abwesende Sektionen bleiben unberührt, Voll-Roundtrip-`replace` ist ein No-Op. **`?async=true`** (Jobtyp `config_import`, `import_document.kind` = `mcf`, Worker `meadow/worker/config_import.py`) ist ebenfalls implementiert und E2E-verifiziert. **Frontend `/config`** (Admin-Seite, `meadow/frontend/config_page.py`, Nav „Configuration") ist implementiert und im Browser verifiziert (Export-Download, `dry_run`/`merge`-Report-Dialog). Die Zweiphasen-Auflösung für alle Forward-Referenzen und der Schema-Adapter sind noch **Ausbaustufe**.

## 1. Zielsetzung

Konfigurationen eines Meadow-PIM-Systems auf ein anderes System übertragen (Staging → Prod, Prod → Staging, Test → QA), sowie als **Vorbereitung einer Datenmigration** aus einem Fremdsystem. Konkret:

1. **Umgebungs-Übertragung:** Objekttypen, Attributdefinitionen, Attributgruppen, Enum-Sets, Einheiten, Relationstypen, Sprachbaum und ggf. Business Rules von einer Umgebung in eine andere spiegeln (z. B. Staging → Prod, damit neue Felder vor dem Datenimport existieren).
2. **Migrations-Vorbereitung:** Vor dem eigentlichen Datenimport (MEF) wird zuerst das **Ziel-Datenmodell** aufgebaut. So kann ein Fremdsystem (oder ein Modelldaten-Export) erst die Struktur liefern und anschließend die Objekte.
3. **Datenmodell-Import aus einem Werkzeug:** Eine Struktur aus einem Modellierungswerkzeug (ERD, CSV, JSON-Schema, Excel-Listen) wird als Konfiguration übernommen – über einen **Schema-Adapter** (Abschnitt 9).

## 2. Abgrenzung: Daten vs. Konfiguration

| | Datenexport/Import (MEF) | **Konfigurations-Export/Import (MCF)** |
|---|---|---|
| Inhalt | Instanzdaten: `object` (Produkte, Kategorien, Assets), `reference` | Metadaten/Referenzdaten: Datenmodell & Kataloge |
| Zweck | Datenmenge übertragen (Stammdaten) | Struktur/Modell übertragen (Felddefinitionen, Typen, Kataloge) |
| Reihenfolge | **nach** der Konfiguration | **vor** dem Datenimport |

**Erlaubte Überschneidung:** Ein MEF-Dokument darf optional die benötigten Konfigurations-Sektionen mitführen („self-contained“), damit ein Datenexport auf ein **leeres** Ziel direkt importierbar ist. Die Trennung bleibt logisch: MCF = Modell, MEF = Daten. Ein MEF-Import kann fehlende Konfiguration ablehnen (Fehler) oder im `self-contained`-Fall mit anlegen.

## 3. Umfang – welche Entitäten gehören zur Konfiguration?

Jede Entität hat einen **stabilen Schlüssel** (nicht die lokale ULID), über den sie systemübergreifend identifiziert wird:

| Entität (Tabelle) | Stabiler Schlüssel | Abhängigkeiten (Import-Reihenfolge) |
|---|---|---|
| `language_tree` | `code` | – (Basis) |
| `unit` (eigene Einheiten) | `symbol` | – (Basis) |
| `attribute_group` | `name` | – |
| `enum_set` / `enum_option` | Set `name` + Option `code` | – |
| `object_type` | `name` | `key_attributes`/`label_attribute` → Attribut (forward) |
| `attribute_definition` | `name` | `unit`, `display_unit`, `enum_set`, `group_ids`, `applicable_types`/`required_types` |
| `relation_definition` | `name` (Key `rel_<name>`) | `attributes` (edge attributes), `source_types`/`target_types` |
| `business_rule` | `name` | `applies_to` (Objekttypen), `dependencies` (Attribute) |

**Nicht Teil der Konfiguration** (Instanz-/Betriebsbezug):
- `object` (Instanzdaten per MEF); Kategorie-/Collection-*Bäume* sind Objekte und damit Daten.
- `reference` (Instanz-Verknüpfungen), `job_run`, `audit_log`, `log_entry`, `outbox_event`.
- `users` / `groups` (Rechte & Berechtigungen) – separat (Detail [benutzer-gruppen-rechte.md](benutzer-gruppen-rechte.md)).
- `system_setting` (Laufzeit-Konfiguration) und `connector` (Konnektor-Parameter) – **optional** als Ausbaustufe, nicht im MVP-Umfang.

## 4. Dateiformat: Meadow Configuration Format (MCF)

JSON, passend zum Datenexport (MEF), mit eigenem Kopf und Sektionen.

**Dokument-Kopf:**

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `format_version` | string | ja | Schema-Version (`"1.0"`); Import lehnt unbekannte Versionen ab (400). |
| `kind` | string | ja | `"configuration"` (Abgrenzung zu MEF). |
| `source` | string | nein | Quellsystem/-umgebung (z. B. `staging`), dient als Kennung/Audit. |
| `created_at` | string | nein | Erzeugungszeitpunkt (ISO-8601). |
| `sections` | object | ja | Die eigentlichen Konfigurations-Sektionen. |

**Grundregel:** Referenzen zwischen Konfig-Einträgen werden über **stabile Schlüssel** ausgedrückt – nicht über lokale Record-IDs (z. B. `attribute_definition.unit = "mm"`, `enum_set = "colors"`, `group_ids = ["technical"]`, `applicable_types = ["product"]`).

**Beispiel:**

```json
{
  "format_version": "1.0",
  "kind": "configuration",
  "source": "staging",
  "created_at": "2026-09-09T10:00:00Z",
  "sections": {
    "languages": [
      { "code": "de", "parent": null, "label": { "de": "Deutsch", "en": "German" }, "active": true, "is_ui": true }
    ],
    "channels": [
      { "code": "webshop", "label": { "de": "Webshop", "en": "Web shop" }, "description": "Hauptkanal", "active": true, "position": 10 }
    ],
    "units": [
      { "symbol": "mm", "dimension": "length", "canonical_unit": "m", "factor": 0.001, "offset": 0, "label": { "de": "Millimeter", "en": "Millimetre" }, "system": "default" }
    ],
    "attribute_groups": [
      { "name": "technical", "label": { "de": "Technik", "en": "Technical" }, "position": 1, "applicable_types": ["product"] }
    ],
    "enum_sets": [
      { "name": "colors", "label": { "de": "Farbe", "en": "Color" }, "position": 0, "options": [ { "code": "red", "labels": { "de": "Rot", "en": "Red" }, "active": true, "position": 0 } ] }
    ],
    "object_types": [
      { "name": "product", "label": { "de": "Produkt", "en": "Product" }, "description": "", "key_attributes": ["sku"], "label_attribute": "name", "taxonomy": false, "system": false },
      { "name": "etim_class", "label": { "de": "ETIM-Klasse", "en": "ETIM class" }, "description": "", "key_attributes": ["etim_code"], "label_attribute": "name", "taxonomy": true, "system": false }
    ],
    "attribute_definitions": [
      { "name": "sku", "label": { "de": "SKU", "en": "SKU" }, "type": "text", "group_ids": ["technical"], "multilingual": false, "inheritable": false, "is_key": true, "applicable_types": ["product"], "required_types": ["product"], "computed": false }
    ],
    "relation_definitions": [
      { "name": "related", "kind": "aggregation", "label": { "de": "Verwandt", "en": "Related" }, "source_types": ["product"], "target_types": ["product"], "attributes": ["relation_strength"], "system": false }
    ],
    "business_rules": [
      { "name": "calc_gross", "type": "computed", "state": "disabled", "applies_to": ["product"], "script": "def run(obj, ctx, result):\n    ..." }
    ],
    "category_attributes": [
      { "class": "EC000123", "class_key": "etim_code", "class_id": "object:01M...", "features": [ { "attribute": "power", "required": true, "position": 0 } ] }
    ]
  }
}
```

### 4.1 Sektion `languages` (Sprachbaum)

| Feld | Typ | Beschreibung |
|---|---|---|
| `code` | string | Sprachcode (stabiler Schlüssel, `id = lang_<code>`) |
| `parent` | string? | Sprachcode des Elternknotens (fehlt = Wurzel) |
| `label` | object | Mehrsprachiges Label |
| `active` | bool | Für Datenpflege sichtbar |
| `is_ui` | bool | Für UI-Sprachumschalter |

`fallback_order` wird bei der Auflösung aus der Struktur abgeleitet (nicht aus dem Dokument übernommen).

### 4.1a Sektion `channels` (Kanal-Katalog)

| Feld | Typ | Beschreibung |
|---|---|---|
| `code` | string | Kanalcode (stabiler Schlüssel und Record-Key, z. B. `webshop`) |
| `label` | object | Mehrsprachiges Label |
| `description` | string? | Optionale Beschreibung |
| `active` | bool | Im UI angeboten (Default `true`) |
| `position` | number | Sortierposition |

Kanal-**Freigaben** (`release`) sind Daten und werden **nicht** übertragen. Beim Löschen/Ersetzen blockiert eine Sektion `replace` einen Kanal, solange Freigaben auf ihn verweisen (wie bei `units` über `attribute_definition`).

### 4.2 Sektion `units` (Einheiten-Katalog)

| Feld | Typ | Beschreibung |
|---|---|---|
| `symbol` | string | Einheitenzeichen (stabiler Schlüssel) |
| `dimension` | string | Dimension (z. B. `length`) |
| `canonical_unit` | string? | Symbol der kanonischen SI-Einheit (fehlt bei der kanonischen Einheit) |
| `factor` | number | Linearer Faktor zur kanonischen Einheit |
| `offset` | number | Offset zur kanonischen Einheit |
| `label` | object | Mehrsprachiges Label |
| `system` | string | `default` (mitgeliefert, readonly) oder `custom` |

`system = default`-Einheiten werden **validiert** (Ziel muss sie mit gleicher Umrechnung haben) und **nicht** überschrieben.

### 4.3 Sektion `attribute_groups`

| Feld | Typ | Beschreibung |
|---|---|---|
| `name` | string | Gruppenname (stabiler Schlüssel) |
| `label` | object | Mehrsprachiges Label |
| `position` | number | Reihenfolge |
| `applicable_types` | string[] | Objekttyp-Namen (leer = aus Mitgliedern abgeleitet) |

### 4.4 Sektion `enum_sets` (Reference Data)

| Feld | Typ | Beschreibung |
|---|---|---|
| `name` | string | Set-Name (stabiler Schlüssel) |
| `label` | object | Mehrsprachiges Label |
| `position` | number | Reihenfolge |
| `options` | array | Eingebettete Enum-Optionen |

Je Option: `code` (stabil, sprachunabhängig), `labels` (object), `position`, `active`.

### 4.5 Sektion `object_types` (Objekttypen / Familien)

| Feld | Typ | Beschreibung |
|---|---|---|
| `name` | string | Objekttyp-Code (stabiler Schlüssel, gleich `object.type`) |
| `label` | object | Mehrsprachiges Label |
| `description` | string? | Beschreibung |
| `key_attributes` | string[] | Business-Schlüssel-Attributnamen |
| `label_attribute` | string? | Anzeigename-Attribut |
| `taxonomy` | bool | **Taxonomie-Knotentyp** (`object_type.taxonomy`, s. [kategorisierung.md](kategorisierung.md), Stufe A): Objekte dieses Typs sind Kategorie-/Klassifikationsknoten. Wird nur gesetzt, wenn das Dokument den Schlüssel enthält — eine ältere Datei ohne `taxonomy` **löscht** das Flag eines bestehenden Typs nicht. |
| `system` | bool | Systemtyp (nicht überschreibbar/löschbar) |

### 4.6 Sektion `attribute_definitions`

| Feld | Typ | Beschreibung |
|---|---|---|
| `name` | string | Attributname (stabiler Schlüssel) |
| `label` | object | Mehrsprachiges Label |
| `type` | string | `text`\|`url`\|`number`\|`boolean`\|`date`\|`enum`\|`reference` |
| `group_ids` | string[] | Gruppen-Namen |
| `inheritable` | bool | Vererbbar |
| `is_key` | bool | Marker (autoritativ: `object_type.key_attributes`) |
| `multilingual` | bool | Sprachabhängig |
| `unit` | string? | Einheiten-Symbol (für numerische Werte) |
| `display_unit` | string? | Anzeige-Einheiten-Symbol |
| `enum_set` | string? | Enum-Set-Name |
| `applicable_types` | string[] | Objekttyp-Namen (leer = alle) |
| `required_types` | string[] | Objekttyp-Namen (Pflicht/Vollständigkeit) |
| `computed` | bool | Berechnet |
| `computed_rule` | string? | Business-Rule-Name (nur bei `computed`) |
| `dependencies` | string[] | Eingabe-Attributnamen |
| `computed_reads_inherited` | bool | Regel liest vererbte Werte |
| `constraints` | object? | Weitere Restriktionen (`min`/`max`/…; `enum_values` entfällt zugunsten `enum_set`) |
| `standard_codes` | object? | Bindung an Klassifikationsstandards, `{ "<standard>": "<Merkmal-Code>" }` (z. B. `{"etim": "EF000040"}`) — wandert mit, damit ein Umzug die Merkmalsbindung nicht verliert |
| `reference_data` | bool | `true`: Vokabular eines Klassifizierungsstandards (im Formular/Picker ausgeblendet, s. [kategorisierung.md](kategorisierung.md)) — wandert mit, damit ein Umzug die Absicht „Referenzdaten“ bzw. „uebernommen“ nicht verliert |
| `default_value` | any? | Default |
| `description` | string? | Beschreibung |

### 4.7 Sektion `relation_definitions`

| Feld | Typ | Beschreibung |
|---|---|---|
| `name` | string | Name (stabiler Schlüssel, Record-Key `rel_<name>`) |
| `kind` | string | `composition`\|`aggregation` |
| `source_types` | string[] | Erlaubte Quell-Objekttyp-Namen (leer = alle) |
| `target_types` | string[] | Erlaubte Ziel-Objekttyp-Namen (leer = alle) |
| `label` | object | Mehrsprachiges Label |
| `attributes` | string[] | Edge-Attribut-Namen |
| `system` | bool | Eingebaut (nicht überschreibbar) |

### 4.8 Sektion `business_rules`

| Feld | Typ | Beschreibung |
|---|---|---|
| `name` | string | Regel-Name (stabiler Schlüssel) |
| `type` | string | `validation`\|`computed`\|`normalization`\|`workflow_guard`\|`bulk` |
| `applies_to` | string[] | Objekttyp-Namen (leer = alle) |
| `script` | string | Python-Quelltext |
| `state` | string | `draft`\|`active`\|`disabled` |

### 4.9 Sektion `category_attributes` (Klassen-Merkmale)

Die Merkmale der Taxonomie-Klassen ([kategorisierung.md](kategorisierung.md), Stufe B) – **Konfiguration an Daten**: die Merkmals-Zeilen hängen an Kategorie-Objekten, und Objekte sind (MEF-)Daten und **nicht** Teil des MCF. Deshalb referenziert die Sektion eine Klasse über ihren **stabilen Business-Key** und nicht über eine lokale ULID.

| Feld | Typ | Beschreibung |
|---|---|---|
| `class` | string | Stabiler Key-Wert des Klassenknotens (z. B. die ETIM-Klassennummer) — Wert des **ersten** `key_attributes`-Eintrags des Knoten-Objekttyps (Business-Keys sind `text` und nicht mehrsprachig). Leer, wenn der Knoten keinen Key hat. |
| `class_key` | string? | Attributname, der den Key geliefert hat (für die Auflösung im Ziel) |
| `class_id` | string | Lokale Knoten-ID (`object:…`) — **Fallback** für dieselbe Umgebung, nicht portabel |
| `features` | object[] | Je Merkmal `{attribute, required, position}`; `attribute` ist der Name der Attributdefinition |

- **Auflösung beim Import:** zuerst über `class` + `class_key` (Suche über alle Typen mit `taxonomy: true`), dann über `class_id`. Nicht auflösbare Klassen werden als **Fehler** im Report geführt („import the taxonomy data first") — die Taxonomie-**Daten** müssen also vorher importiert werden (MEF/`references` oder der geplante Standard-Import).
- **Semantik:** Die `features`-Liste einer **genannten** Klasse ist maßgeblich (nicht genannte Merkmale der Klasse werden beim Anwenden entfernt); Klassen, die das Dokument nicht nennt, bleiben unverändert. `required` speist die Vollständigkeit und `ctx.missing_features`.
- **Reihenfolge:** bewusst **am Ende** der Abhängigkeitskette — die Sektion setzt Attributdefinitionen (Konfiguration) **und** die Klassenknoten (Daten) voraus.
- Unbekannte Attributnamen werden als Fehler gemeldet und nicht angewendet (kein stiller Dangling-Verweis).

## 5. API-Kontrakt

### `GET /config/export` (admin/superadmin)

- Query:
  - `sections` (string, optional): kommagetrennt, z. B. `object_types,attribute_definitions,enum_sets` (Default: alle).
  - `include_system` (bool, optional, Default `false`): ob `system`-Einträge (Systemtypen/-einheiten/-relationen, Seeds) mit-exportiert werden.
- Antwort `200`: MCF-Dokument (JSON); `Content-Disposition: attachment; filename="meadow-config-<date>.json"`.
- Fehler: `401` (nicht authentifiziert), `403` (nicht admin), `400` (unbekannte Sektion).

### `POST /config/import` (admin/superadmin)

- Body: MCF als JSON (`Content-Type: application/json`) **oder** `multipart/form-data` (Feld `file`), analog `POST /imports`.
- Query:
  - `mode` (string, Default `merge`): `dry_run` | `merge` | `replace`.
  - `dry_run` (bool, Alias): `?dry_run=true` == `mode=dry_run`.
  - `async` (bool, optional): `?async=true` → `202` + `job_run` (Typ `config_import`), Worker wendet an.
- Antwort:
  - synchron (`dry_run`/`merge`/`replace`): `200` mit `Report`.
  - `?async=true`: `202` `{ "job_run_id": "…" }`.
- Validierung: unbekannte `format_version` → `400`; ungültige Dokumentstruktur/doppelte stabile Schlüssel → `400`; nicht-admin → `403`.
- **Report** (am Response bzw. `job_run.result`):

```json
{
  "mode": "merge",
  "summary": { "created": 0, "updated": 0, "skipped": 0, "errors": 0, "warnings": 0 },
  "sections": {
    "object_types": { "created": [], "updated": [], "skipped": [], "errors": [] },
    "attribute_definitions": { "created": [], "updated": [], "skipped": [], "errors": [] }
  },
  "errors": [ { "section": "attribute_definitions", "key": "price", "message": "…" } ],
  "warnings": [ { "section": "object_types", "key": "product", "message": "…" } ]
}
```

## 6. Export-Logik (Kern, `config_export`)

- Liest je Sektion die Einträge und serialisiert sie mit **stabilen Schlüsseln** statt ULIDs; Querverweise werden auf stabile Schlüssel abgebildet.
- `system`-Einträge nur bei `include_system=true`.
- Sektionen ohne Einträge: im `sections`-Objekt weglassen oder leeres Array (konsistent: leeres Array weglassen, wenn keine Treffer).
- Der Export ist rein lesend (keine Nebenwirkungen).

## 7. Import-Algorithmus (Kern, `apply_config`)

**Vorbedingungen:** admin/superadmin; `format_version` unterstützt; Dokument-Validierung ok.

1. **Abhängigkeits-Reihenfolge:** `languages → channels → units → attribute_groups → enum_sets → object_types → attribute_definitions → relation_definitions → business_rules → category_attributes` (die Klassen-Merkmale zuletzt: sie setzen Attributdefinitionen **und** die Klassenknoten = Daten voraus). Die **Kanäle** stehen wie die Sprachen früh, weil sie keine Abhängigkeiten haben; die **Freigaben selbst sind Daten** (`release`) und gehören nicht ins MCF.
2. **Zweiphasig:**
   - **Phase 1 (Erzeugen/Aktualisieren nach stabilem Schlüssel):** je Sektion je Eintrag den stabilen Schlüssel im Ziel auflösen → `find` oder `create`. Referenzen auf noch nicht angelegte Entitäten (Forward-Referenzen: `object_type.key_attributes`/`label_attribute`, `attribute_definition.computed_rule`, `relation_definition.attributes`, `business_rule.dependencies`) werden als **ausstehend** registriert.
   - **Phase 2 (Querverweis-Auflösung):** Nachdem alle Entitäten existieren, werden die ausstehenden Referenzen auf die Ziel-IDs gemappt und an den Datensätzen aktualisiert.
3. **Schlüssel-Auflösung je Sektion:**

| Sektion | find/create per | Referenzen |
|---|---|---|
| `languages` | `code` | `parent` → Sprachcode |
| `channels` | `code` | – (Freigaben sind Daten) |
| `units` | `symbol` | `canonical_unit` → Symbol |
| `attribute_groups` | `name` | `applicable_types` → Objekttyp-Name |
| `enum_sets` | Set `name`; Optionen per `code` (im Set) | – |
| `object_types` | `name` | `key_attributes`/`label_attribute` → Attributname (Phase 2) |
| `attribute_definitions` | `name` | `group_ids`→Gruppenname, `unit`/`display_unit`→Symbol, `enum_set`→Set-Name, `applicable_types`/`required_types`→Objekttyp-Name, `computed_rule`→Regel-Name (Phase 2) |
| `relation_definitions` | `name` (`rel_<name>`) | `source_types`/`target_types`→Objekttyp-Name, `attributes`→Attributname (Phase 2) |
| `business_rules` | `name` | `applies_to`→Objekttyp-Name, `dependencies`→Attributname (Phase 2) |

**Konflikt-/Sicherheitsregeln (verbindlich):**

| Situation | Verhalten |
|---|---|
| Eintrag ist `system` (`object_type.system=true`, `unit.system=default`, `relation_definition.system=true`, Seeds) | **Nicht** überschreiben; bei Abweichung → Fehler/Warnung, sonst `skipped`. |
| Ziel existiert mit gleichem stabilen Schlüssel | Merge: Update der **eigenen** Felder (nicht `system`, nicht schützende Felder). |
| `attribute_definition.type` weicht ab (Ziel `number`, Quelle `text`) | **Blockieren** (Fehler) gemäß [Datentypwechsel-Matrix](datenmodell.md#datentypwechsel) – keine stille Migration. |
| Definition in Benutzung (Objekte/Werte existieren) | Update erlaubt, wenn kompatibel; sonst Report-Warnung + `?force` nach expliziter Freigabe. |
| `object_type.key_attributes` und `attribute_definition.is_key` inkonsistent | Reconcile: `key_attributes` autoritativ; `is_key` wird gesetzt/gewaschen; Warnung bei Konflikt. |
| Doppelter stabiler Schlüssel im Dokument | `400` (Dokument ungültig). |
| `business_rule`-Skript | Compile-Check (Reuse `rules.compile`); `state=disabled` beim Import, Aktivierung manuell. |

**Idempotenz:** `merge` ist idempotent – erneuter Import derselben MCF erzeugt `created=0`/`updated=0`. `dry_run` erzeugt nur den Plan, keine Änderung.

**`replace`-Modus (optional):** löscht zuerst nur **eigene** Einträge der jeweiligen Sektion (nicht `system`), dann Neuaufbau. Nur sinnvoll, wenn keine Objekte die Definitionen verwenden (sonst `409`/blockiert). Empfehlung: Default = `merge`.

## 8. Referenz-/ID-Mapping über Umgebungen

- ULIDs unterscheiden sich pro System. Die Konfiguration definiert sich über **stabile Schlüssel**; der Import erzeugt im Ziel neue ULIDs und remappt Querverweise auf die Ziel-IDs.
- **Kollisionen:** Existiert im Ziel bereits ein Eintrag mit demselben stabilen Schlüssel, wird er als „dieselbe Entität“ behandelt (Merge), nicht dupliziert.
- **Forward-Referenzen** werden über die Zweiphasen-Auflösung abgedeckt (Phase 2).

## 9. Job-Integration (`config_import`) & Datenmodell-Änderungen

- **Optionaler asynchroner Pfad** für sehr große Dokumente: Jobtyp `config_import`; Worker (`meadow/worker/config_import.py`) lädt das gespeicherte MCF und ruft den Kern-Endpunkt (`POST /config/import` mit `mode`) auf; Report an `job_run.result`.
- **Empfehlung (MVP):** **synchroner Kern-Apply** – die Konfiguration ist klein; `?async=true` ist Ausbaustufe.
- **Dokument-Ablage:** `import_document` um ein Feld `kind` erweitern (`mef` | `mcf`, Default `mef`); das MCF-JSON liegt im bestehenden `document`-Feld. (Alternativ eigene Tabelle `config_import_document`; kein Breaking Change.)
- **Audit:** Jede angewendete Änderung als Audit-Eintrag (Kategorie `config_import`); der Gesamt-Import zusätzlich als Summary-Audit. Bestehende `audit_log` wiederverwendet.
- **System-Schutz:** Die bestehenden CRUD-Endpunkte liefern bei `system`-Einträgen bereits `400`; `apply_config` erzwingt das zusätzlich.
- **Keine neuen Felder an Konfig-Entitäten nötig** (stabile Schlüssel machen die Konfiguration selbst-identifizierend). Optional: `config_source`/`imported_at` für Nachvollziehbarkeit (Ausbaustufe).

## 10. Schema-Adapter (Datenmodell aus einem Werkzeug importieren)

- **Eingabe:** strukturiertes Modell, z. B. CSV/JSON/XLSX mit Zeilen `{ object_type, attribute, type, required, multilingual, unit, enum, group }`. Alternativ JSON-Schema.
- **Adapter-Logik (`meadow/worker/schema_adapter.py`):** gruppiert Attribute je Objekttyp → `object_types`; dedupliziert `attribute_definitions` (via `applicable_types` erweitert); erzeugt `attribute_groups`, `enum_sets`, `unit`-Referenzen; übersetzt in ein gültiges MCF.
- **Fremd-Datentyp-Mapping:** `string`→`text`, `integer`/`float`/`decimal`→`number`, `bool`/`boolean`→`boolean`, `date`/`datetime`→`date`, Liste/Code→`enum` (+ ggf. `enum_set`).
- **Ausgabe:** gültiges MCF → normaler `POST /config/import`.
- **Feld-Mapping (Fremdfeld → Meadow-Attribut)** bleibt getrennt und liegt beim **Datenimport** (`normalization`-Regeln, Konnektor-Parameter `match_by_key`, Quell-Referenz). Die Konfiguration legt nur das Ziel-Datenmodell fest; das Mapping ist ein nachgelagerter, transformativer Schritt.

## 11. UI (`/config`, admin/superadmin)

- **Export:** Sektionen (Checkboxen, Default alle), `include_system`-Schalter, „Exportieren“ → Download MCF.
- **Import:** Datei-Upload (oder JSON einfügen), Modus-Auswahl (`dry_run` als Vorschau, `merge` anwenden), „Vorschau“ → Report-Tabelle (created/updated/skipped/errors/warnings je Sektion), „Anwenden“ → Report.
- `api.py`-Methoden: `export_config`, `import_config`; `views.py` registriert `/config`.
- i18n-Keys `config.*` in en/de/fr.

## 12. Entscheidungen & offene Punkte

| Entscheidung | Empfehlung |
|---|---|
| System-Seeds exportieren? | Ja, gekennzeichnet (`include_system`); Import validiert/überspringt. |
| Merge vs. Replace | Default `merge` (idempotent); `replace` als explizite Option für leere/neue Zielmodelle. |
| Datentyp-Abweichung | Blockieren (Fehler) gemäß [Kompatibilitätsmatrix](datenmodell.md#datentypwechsel). |
| Business Rules mitnehmen? | Ja, aber `state=disabled` beim Import; Aktivierung manuell (Review). |
| Self-contained MEF | Optional; MEF kann `sections` mitführen. |
| `connector`/`system_setting` exportieren? | Optional/Ausbaustufe (nicht im MVP-Umfang). |
| Async-Job? | MVP synchron; `?async=true` als Ausbaustufe. |

## 13. Implementierung (Schritte)

1. **Rust-Kern – Export:** `config_export` (`crates/pim-core/src/api/config.rs`, Modul in `api/mod.rs`, Router in `app.rs`): alle Sektionen lesen, MCF mit stabilen Schlüsseln serialisieren; `GET /config/export`.
2. **Rust-Kern – Import:** `apply_config` + `POST /config/import`: Zweiphasen-Algorithmus (Erzeugen/Update, dann Querverweis-Auflösung), Konflikt-/Systemschutz, Report. Wiederverwendet bestehende CRUD-Validatoren (`object_types`, `attribute_definitions`, `units`, `enum_sets`, `relation_definitions`, `languages`, `rules`).
3. **Datenmodell:** `import_document.kind` (`mef`/`mcf`) ergänzen.
4. **Kern-Tests:** Export (alle Sektionen, `include_system`, stabile Schlüssel); Import (idempotentes `merge`, `dry_run`, Systemschutz, Datentyp-Abweichung, Forward-Refs, `replace`).
5. **Python-Worker (optional, Ausbaustufe):** `meadow/worker/config_import.py` (async-Apply) + Jobtyp `config_import`.
6. **Frontend:** `meadow/frontend/config.py` (`/config`), `api.py` (`export_config`/`import_config`), i18n-Keys, `views.py`-Registrierung.
7. **Schema-Adapter (separat):** `meadow/worker/schema_adapter.py` + Datentyp-Mapping.
8. **Doku:** `api-design.md` (Endpunkte bereits ergänzt), `datenmodell.md`, `integration-eventsystem.md`.

## 14. Testfälle

- Export erzeugt gültiges MCF; alle Sektionen; `include_system`; stabile Schlüssel statt ULIDs.
- Import `dry_run` ändert nichts; Report korrekt.
- Import `merge` idempotent (zweiter Lauf `created=0`/`updated=0`).
- System-Einträge werden nicht überschrieben.
- Datentyp-Abweichung (`number` → `text`) wird blockiert.
- Forward-Referenzen (`object_type.key_attributes` → Attribut) werden aufgelöst.
- `replace` löscht nur eigene, nicht `system`.
- **Sektion `category_attributes`** (Kern-Unit-Tests in `config.rs`/`config/category_attributes.rs`): der stabile Klassen-Schlüssel gewinnt gegen die lokale `class_id` (leerer Schlüssel ⇒ Fallback), Report-Label fällt auf `class_id`/Platzhalter zurück, die Merkmalsliste ist deterministisch sortiert (`position`, dann Attribut), `key_field` = `class`, und die Sektion steht **zuletzt** (nach `object_types`/`attribute_definitions`).
- Unbekannte `format_version` → 400; nicht-admin → 403.
- `business_rule`-Skript wird compile-geprüft and importiert mit `state=disabled`.


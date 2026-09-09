# Konfigurations-Export & Import (Meadow Configuration Format, MCF)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) – Erweiterung des Daten-Export/Imports (MEF) um die Übertragung des Datenmodells / der Referenzdaten.
- **Relevante Phasen:** Phase 9 (spätere Ausbaustufen), ergänzend zum MVP-Import/Export
- **Stand:** Entwurf

## Zielsetzung

Konfigurationen eines Meadow-PIM-Systems auf ein anderes System übertragen (Staging → Prod, Prod → Staging, Test → QA), sowie als **Vorbereitung einer Datenmigration** aus einem Fremdsystem. Konkret:

1. **Umgebungs-Übertragung:** Objekttypen, Attributdefinitionen, Attributgruppen, Enum-Sets, Einheiten, Relationstypen, Sprachbaum und ggf. Business Rules von einer Umgebung in eine andere spiegeln (z. B. Staging → Prod, damit neue Felder vor dem Datenimport existieren).
2. **Migrations-Vorbereitung:** Vor dem eigentlichen Datenimport (MEF) wird zuerst das **Ziel-Datenmodell** aufgebaut. So kann ein Fremdsystem (oder ein Modelldaten-Export) erst die Struktur liefern und anschließend die Objekte.
3. **Datenmodell-Import aus einem Werkzeug:** Eine Struktur aus einem Modellierungswerkzeug (ERD, CSV, JSON-Schema, Excel-Listen) wird als Konfiguration übernommen – über einen **Schema-Adapter** (siehe unten).

## Abgrenzung: Daten vs. Konfiguration

| | Datenexport/Import (MEF) | **Konfigurations-Export/Import (MCF)** |
|---|---|---|
| Inhalt | Instanzdaten: `object` (Produkte, Kategorien, Assets), `reference` | Metadaten/Referenzdaten: Datenmodell & Kataloge |
| Zweck | Datenmenge übertragen (Stammdaten) | Struktur/Modell übertragen (Felddefinitionen, Typen, Kataloge) |
| Reihenfolge | **nach** der Konfiguration | **vor** dem Datenimport |

**Erlaubte Überschneidung:** Ein MEF-Dokument darf optional die benötigten Konfigurations-Sektionen mitführen („self-contained“), damit ein Datenexport auf ein **leeres** Ziel direkt importierbar ist. Die Trennung bleibt logisch: MCF = Modell, MEF = Daten. Ein MEF-Import kann fehlende Konfiguration entweder ablehnen (Fehler) oder im `self-contained`-Fall mit anlegen.

## Umfang – welche Entitäten gehören zur Konfiguration?

Jede Entität hat einen **stabilen Schlüssel** (nicht die lokale ULID), über den sie systemübergreifend identifiziert wird:

| Entität (Tabelle) | Stabiler Schlüssel | Abhängigkeiten (Import-Reihenfolge) |
|---|---|---|
| `language_tree` | `code` | – (Basis) |
| `unit` (eigene Einheiten) | `symbol` | – (Basis) |
| `object_type` | `name` | attributverweisend: `key_attributes`/`label_attribute` (forward) |
| `attribute_group` | `name` | – |
| `enum_set` / `enum_option` | Set `name` + Option `code` | – |
| `attribute_definition` | `name` | `unit`, `display_unit`, `enum_set`, `group_ids`, `applicable_types`/`required_types` |
| `relation_definition` | `name` (Key `rel_<name>`) | `attributes` (edge attributes), `source_types`/`target_types` |
| `business_rule` | `name` | `applies_to` (Objekttypen), `dependencies` (Attribute), `enum_set`, … |

**Nicht Teil der Konfiguration** (bleiben beim Datenimport bzw. sind instanz-/betriebsbezogen):
- `object` (Instanzdaten, kommen per MEF); Kategorie-/Collection-*Bäume* sind Objekte und damit Daten.
- `reference` (Instanz-Verknüpfungen), `job_run`, `audit_log`, `log_entry`, `outbox_event`.
- `users` / `groups` (Rechte & Berechtigungen) – separat zu behandeln (Detail [benutzer-gruppen-rechte.md](benutzer-gruppen-rechte.md)).
- `system_setting` (Laufzeit-Konfiguration) – primär Betrieb/Umgebung, nicht Teil des Modells (optional als Sektion „Runtime“).

## Format: Meadow Configuration Format (MCF)

JSON, passend zum Datenexport (MEF), mit eigenem Kopf und Sektionen:

```json
{
  "format_version": "1.0",
  "kind": "configuration",
  "source": "staging",
  "created_at": "2026-09-09T10:00:00Z",
  "sections": {
    "languages": [
      { "code": "de", "parent": null, "label": { "de": "Deutsch", "en": "German" }, "fallback_order": [], "active": true, "is_ui": true }
    ],
    "units": [
      { "symbol": "mm", "label": { "de": "Millimeter", "en": "Millimetre" }, "dimension": "length", "factor": 0.001, "offset": 0, "system": false }
    ],
    "object_types": [
      { "name": "product", "label": { "de": "Produkt", "en": "Product" }, "key_attributes": ["sku"], "label_attribute": "name", "system": false }
    ],
    "attribute_groups": [
      { "name": "technical", "label": { "de": "Technik", "en": "Technical" }, "applicable_types": ["product"] }
    ],
    "enum_sets": [
      { "name": "colors", "options": [ { "code": "red", "labels": { "de": "Rot", "en": "Red" }, "active": true } ] }
    ],
    "attribute_definitions": [
      { "name": "sku", "type": "text", "label": { "de": "SKU" }, "group_ids": ["technical"], "multilingual": false, "unit": "mm", "display_unit": "mm", "enum_set": "colors", "applicable_types": ["product"], "required_types": ["product"], "inheritable": false, "computed": false }
    ],
    "relation_definitions": [
      { "name": "related", "kind": "aggregation", "label": { "de": "Verwandt", "en": "Related" }, "source_types": ["product"], "target_types": ["product"], "attributes": ["relation_strength"], "system": false }
    ],
    "business_rules": [
      { "name": "calc_gross", "type": "computed", "state": "disabled", "applies_to": ["product"], "script": "def run(obj, ctx, result): ..." }
    ]
  }
}
```

**Regeln:**
- **Stabile Schlüssel statt ULIDs:** Referenzen zwischen Konfig-Einträgen (z. B. `unit`, `enum_set`, `group_ids`, `key_attributes`, `applies_to`) werden über die oben genannten Schlüssel ausgedrückt – nicht über lokale Record-IDs.
- **`format_version`** für Abwärts-/Vorwärtskompatibilität und gezielte Migration alter Dokumente.
- **`system`-Markierung:** vom System geseedete Einträge (z. B. `unit:unit_m`, `relation_definition:rel_depends_on`, Standard-Objekttypen) werden mit `system: true` bzw. als `meta.seeds` gekennzeichnet; der Import behandelt sie besonders (überspringen bzw. Gleichheit validieren).
- Andere Entitäten (`job_run`, `audit_log`, …) tauchen bewusst **nicht** auf.

## Export

- **Endpunkt:** `GET /config/export` (admin/superadmin), optional `?sections=object_types,attribute_definitions,…` und `?include_system=true|false`.
- **Ausgabe:** MCF-Dokument als JSON (Download). Optional als `outbound_export`-Job (`job_run.result`), analog zum Datenexport.
- **Optionen:**
  - Nur **eigene** (nicht-system) Einträge, oder **alle inkl. System-Seeds** (Systemeinträge sind über Umgebungen gleich; Empfehlung: mit-exportieren und kennzeichnen).
  - **Selektiv:** nur Sektionen, die für eine Aufgabe nötig sind (z. B. nur `object_types` + `attribute_definitions`).

## Import

- **Endpunkt:** `POST /config/import` (admin/superadmin), JSON-Body oder `multipart/form-data`-Upload (analog `POST /imports`); Antwort `202` + `job_run`-ID (Typ `config_import`).
- **Verarbeitung (Worker oder Kern, geordnet nach Abhängigkeiten):**
  1. `languages` → 2. `units` → 3. `object_types` → 4. `attribute_groups` → 5. `enum_sets` → 6. `attribute_definitions` → 7. `relation_definitions` → 8. `business_rules`.
- **Auflösung:** Jede Entität wird per stabilem Schlüssel im Ziel gesucht (find), sonst neu angelegt (create). Querverweise werden auf die Ziel-ULIDs umgemappt.
- **Modus:**
  - `merge` (Default): existiert der Schlüssel → Update; sonst create. **Idempotent.**
  - `replace` (optional): löscht zuerst nur **eigene** Einträge der Sektion (nicht `system`), dann Neuaufbau. Vorsicht: nur wenn keine Objekte die Definitionen verwenden.
  - `dry_run` (verbindlich als Vorschau): erzeugt nur einen Bericht, **keine** Änderung.
- **Konflikt- & Sicherheitsverhalten:**
  - `system`-Einträge werden **nicht** überschrieben (übersprungen bzw. Gleichheits-Validierung, sonst Fehler).
  - Nicht-kompatible Änderungen (z. B. `attribute_definition.type` bereits `number` → Ziel `text`; Datentypwechsel-Restriktionen gemäß [datenmodell.md](datenmodell.md#datentypwechsel)) werden **blockiert** (Bericht-Fehler) statt stillschweigend migriert.
  - Definitionen, die **in Benutzung** sind (Objekte/Werte existieren), können aktualisiert werden, wenn kompatibel; sonst Meldung im Report, optional `?force` für die Umgebung nach expliziter Freigabe.
  - **Audit** (`config_import`) und **Versions-Bump** (`version`-Feld) bei jeder Änderung.
- **Bericht:** `{ created, updated, skipped, errors[], warnings[] }` am `job_run.result` – die UI zeigt den `dry_run`-Bericht vor dem Anwenden.

## Referenz-/ID-Mapping über Umgebungen

- ULIDs unterscheiden sich pro System. Die Konfiguration definiert sich über **stabile Schlüssel**; der Import erzeugt im Ziel neue ULIDs und remappt Querverweise auf die Ziel-IDs.
- **Kollisionen:** Existiert im Ziel bereits ein Eintrag mit demselben stabilen Schlüssel, wird er als „dieselbe Entität“ behandelt (Merge), nicht dupliziert.
- **Forward-Referenzen:** `object_type.key_attributes`/`label_attribute` dürfen auf noch nicht importierte Attribute verweisen; die Auflösung erfolgt **nach** dem Attribut-Import (zweiphasig: erst alle Entitäten anlegen, dann Referenzen mappen) bzw. forward-tolerant.

## Datenmigration aus einem Fremdsystem & Schema-Adapter

- **Schema-Adapter (Import aus Modellierungswerkzeug):** Ein vereinfachtes Eingabeformat (Liste von Objekttypen + Attributen mit `type`/`enum`/`unit`/`group`) wird über eine Adapter-Logik in eine MCF übersetzt und angewendet. Eingabe z. B. als CSV/JSON/XLSX über einen Konnektor oder einen UI-Import-Dialog.
- **Feld-Mapping (Fremdfeld → Meadow-Attribut):** bleibt getrennt und liegt beim **Datenimport**: über `normalization`-Regeln (R3) und Konnektor-Parameter (`match_by_key`, Quell-Referenz). Die Konfiguration legt nur das Ziel-Datenmodell fest; das Mapping ist ein nachgelagerter, transformativer Schritt.

## UI

- **Admin-Seite `/config`** (admin/superadmin):
  - **Export:** Sektionen auswählbar, `include_system`-Option, Download des MCF-Dokuments.
  - **Import:** Datei-Upload, `dry_run`-Vorschau (Bericht), dann **Anwenden** (Merge). Konflikt-/Fehleranzeige aus dem Report.
- **Optional:** Konfigurations-Export/-Import als Konnektor (`json_export`/`json_import`-Variante) für automatisierte Umgebungs-Synchronisation.

## Offene Entscheidungen

1. **System-Seeds exportieren?** Empfehlung: ja, gekennzeichnet; Import überspringt/validiert.
2. **Merge bei abweichender Definition:** bei inkompatiblem Datentyp → Blockade (Empfehlung), sonst Warnung + `?force`.
3. **Business Rules (Python-Skripte) mitnehmen?** Empfehlung: ja, aber separat review-/aktivierungsfähig (`state: disabled` beim Import; Aktivierung manuell) – Skripte sind ausführbarer Code, kein reines Datenmodell.
4. **Self-contained MEF:** Soll der Datenexport standardmäßig die benötigte Konfiguration mitliefern (für leere Ziele) oder nur optional?

## Bewertung / Aufwand

- Der Export ist überwiegend Lesen + Serialisieren vorhandener Entitäten (gering).
- Der Import erfordert die **Abhängigkeits-Ordnung**, **Schlüssel-Auflösung/Mapping** und **Konflikt-/Sicherheitsprüfung** (mittel).
- Der **Schema-Adapter** (Werkzeug-Import) ist eine separates, zusätzliches Feature (höher, optional).

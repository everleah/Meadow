# Daten-Export & -Import über die Oberfläche (UI-geführt)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) und Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), ergänzend zum bestehenden MEF-Export/Import (Phase 6)
- **Stand:** Umsetzungsreif — Core-API & Worker-Export (CSV/XLSX/JSON, Locale-Auflösung) implementiert & E2E-verifiziert; `/export`-Wizard (`editor+`) implementiert & im Browser verifiziert (Vorschau + Export + Download). **Collection-Bulk-Add** (`POST /collections/{id}/members`, idempotent) implementiert & verifiziert; die Collections-Seite bietet einen **„Add members"-Dialog** und einen **„Export"-Button** (führt mit vorausgewählter Collection (`?collection=<id>`) in den Wizard). **Grid-Auswahl-/Filter-Export** im `/products`-Grid implementiert & verifiziert (Toolbar-Download-Button → Dialog mit Scope „Selected rows" / „All filter matches" + Format; nutzt die sichtbaren Grid-Spalten als Felder über die `selection`-Quelle). **Geplante Exporte** implementiert & E2E-verifiziert: ein Export wird als `job_definition` (`type = outbound_export`, Cron im 6-Feld-Format) gespeichert und vom bestehenden Scheduler periodisch als `outbound_export`-Job ausgeführt; im `/export`-Wizard (Admin) über „Schedule…" anlegbar, mit Liste (Run-now/Aktiv/Löschen) und **„Recent exports"**-Liste (Download pro Lauf, Ablage im `job_run.result`). **E-Mail-Versand** implementiert & E2E-verifiziert: `options.email_to` → der Worker versendet den Export als Anhang (`meadow/worker/mail.py`, SMTP per Env; Dev-Catcher `mailhog` in `docker-compose.dev.yml`, SMTP 1025 / UI 8025). **Objektstorage-Ablage** implementiert & E2E-verifiziert: bei konfiguriertem S3/MinIO archiviert der Worker die Datei (`result.archive` mit `{bucket, key, size, url}`, Presigned-URL) und entfernt ab `EXPORT_INLINE_MAX_BYTES` (Standard 1 MB) die Inline-Daten; `GET /exports/{run_id}/file` leitet dann per **302** auf die Presigned-URL um (`inline_present`), sonst wird wie zuvor inline serialisiert. **Tabellarischer Import (CSV und XLSX)** implementiert & im Browser verifiziert: gemeinsamer Adapter `meadow/tabular.py` (CSV/XLSX → MEF) und die Seite `/import` (`editor+`) mit Upload, **Arbeitsblatt-Auswahl** (Mehrblatt-XLSX), automatischem Spalten-Mapping (inkl. Freitext-Attributen und Sprache `attribut|<lang>`), Vorschau und Import über die bestehende `POST /imports`-Pipeline (idempotent per Business-Schlüssel). **MEF-JSON-Import** implementiert & E2E-verifiziert: die Seite erkennt MEF-Dokumente (`meadow/mef.py`), zeigt statt des Mappings eine Dokument-Zusammenfassung + Objekt-Vorschau und bietet den Abgleich **per Objekt-ID** (`?match_by_id=true`, im Core/Worker neu) — ein Export→Import-Roundtrip von Meadow-MEF (`source: "meadow"`, `id` = Core-Record-ID) **aktualisiert** die Objekte (`updated: N`, `created: 0`, keine Duplikate) statt sie zu duplizieren.

**BMEcat/ETIM-Export (V1, V4, V1b)** implementiert & E2E-verifiziert (2026-09-13): viertes Format `bmecat`, eigenes Profil im Wizard, Schema-validiertes Dokument mit Bericht — ETIM-Profil 2005 mit Preisen/Varianten sowie **BMEcat 1.2** (DTD/XSD) über dieselben Profilfelder, Details und Grenzen in [bmecat-export.md](bmecat-export.md) §11.10–§11.12.

**Getroffene Entscheidungen:**

- **Dateiablage:** `job_run.result` — die strukturierten Daten (`columns` + `rows` bzw. MEF-Dokument) liegen im Job-Ergebnis; die Datei wird beim Download daraus serialisiert (CSV/XLSX/JSON) — kein binäres Blob im Ergebnis, keine Objektstorage-Ablage im MVP.
- **XLSX:** im MVP enthalten (openpyxl). Formate `csv | xlsx | json` — **`bmecat`** ergänzt (2026-09-13) den Klassifikations-/Katalogpfad: ein XML-Dokument im ETIM-Profil statt einer Tabelle, siehe §5.6.
- **Rechte:** Export `editor+`; Import `editor+`; Collection-Verwaltung `admin`.
- **Mehrere Locale-Spalten:** ja — pro gewählter Sprache eine Spalte `<attribut>_<code>` (z. B. `title_de`, `title_en`); Standardsprache zusätzlich als `<attribut>`/`<attribut>_<std>`.

## 1. Zielsetzung & Abgrenzung

Der technische Datenexport läuft heute über den `json_export`-Connector + `outbound_export`-Job: Konfiguration als freies JSON in `/connectors` (admin-only), Ergebnis als MEF-JSON in `job_run.result`. Das ist für technisch versierte Anwender sinnvoll und **bleibt erhalten**. Damit auch nicht-technische Admins Konnektoren konfigurieren können, ist zusätzlich ein **schema-getriebenes Konfigurationsformular** geplant ([konnektor-konfiguration.md](konnektor-konfiguration.md)); die hier beschriebenen Wizards bleiben der **Anwenderpfad** für Daten, die Konnektor-Formulare konfigurieren den **technischen Pfad** (Defaults/Profile).

Für **normale Anwender** fehlt etwas, das sich direkt über die Oberfläche konfigurieren lässt — analog zum MCF-Konfigurations-Export/Import (`/config`), aber für **Instanzdaten** (Objekte/Produkte):

- **Export** geführt über Formulare / Auswahltabellen statt JSON.
- **Import** geführt (Upload, Vorschau, Dry-Run, Anwenden).

**Konkrete Use-Cases (Startpunkt):**

1. **Mitglieder einer Collection exportieren** — z. B. „Preisfehler", „Neuheiten" als CSV/Excel für Tabellenkalkulation, Backup oder Fremdsystem.
2. **Aktuelle Grid-Auswahl / Filter exportieren** — im Produkt-Grid markierte Objekte oder das gefilterte Suchergebnis.
3. **Objekttyp / Kategorie / gespeicherte Suche exportieren** — mit kontrollierter Felder-/Spaltenauswahl und Sprache.
4. **Daten importieren** (CSV/MEF) — inkl. **Mitglieder in eine Collection aufnehmen** (Bulk-Add).

## 2. Typische Lösungen (Recherche)

- **Shopify (Produkte):** Export-Dialog mit Quell-Auswahl (aktuelle Seite / alle / ausgewählte / **Treffer der Suche & Filter**) und Format (CSV für Excel/Numbers vs. Plain CSV); Filter vor dem Export; große Exporte per E-Mail statt Browser-Download.
- **Akeneo (PIM):** Export-Profile mit Datensatzquelle, **Feld-/Spaltenauswahl**, Format (CSV/XLSX/JSON), **Sprache/Locale**, Kategorie-/Completeness-Filter, **geplante Exporte**; Import mit Mapping/Preview.
- **Gemeinsame Muster:** Datensatzquelle → Spaltenauswahl → Format → Sprache → Optionen → Download (klein) oder Hintergrundjob (groß). Import: Upload → Vorschau → Dry-Run → Anwenden (Upsert).

## 3. Umfang

**MVP (UI-Export, `editor+`):**

| Schritt | Auswahl |
|---|---|
| Quelle | **Gesamter Bestand (Komplett-Export)** · Objekttyp · Kategorie(-baum) · **Collection** · gespeicherte Suche · **Kanal (für den Kanal freigegebene Objekte)** · **aktuelle Grid-Auswahl/Filter** |
| Felder | Basis-Felder (`id`, `type`, `status`, `updated_at`, …) + Attributdefinitionen des Typs; **gruppiert nach Attributgruppe** (je Gruppe eine `ui.expansion` mit Sammel-Checkbox), nur Attribute, die der gewählten Objekttyp **erlaubt** (`applicable_types`); „ohne Gruppe" als eigener Abschnitt, „alle auswählen" über die sichtbaren Felder |
| Format | **CSV** (UTF-8, Excel/Plain) · **XLSX** (openpyxl) · JSON (MEF) · **BMEcat/ETIM** (XML, eigenes Profil statt Feldauswahl, §5.6) |
| Sprache | Eine oder **mehrere** Locales (Fallback-Kette); pro Sprache eine Spalte `<attribut>_<code>` |
| Optionen | Header-Zeile, Trennzeichen (`;`/`,`), quantifizierte Werte als `{value, unit}` in Anzeigeeinheit, `include_system`, **`resolve_inheritance` („Auch vererbte Werte exportieren", umgesetzt 2026-09-11)** |

**Ausgabe:** Vorschau (Beispielzeilen) + Download (klein) oder Hintergrundjob + Report/Download-Link (groß).

**Ausbau:** optionaler synchroner Export kleiner Mengen (Grid-Auswahl); automatische Retention (30 Tage, §7).

## 4. Collection-Mitglieder (Use-Case)

- **Export:** `GET /collections/{id}/members` liefert die Mitglieder. Im Export-Wizard als Quelle „Collection" wählbar; zusätzlich ein **„Als CSV/JSON exportieren"-Button** auf der Collections-Verwaltungsseite (`/saved-searches`, Tab Collections) und im Grid (Collections-Ansicht).
- **Import / Mitglieder aufnehmen:** Bulk-Add von Objekten in eine Collection als `collection_member`-Relationen (`attributes.rank`, `added_at`). Quelle: Grid-Auswahl oder Import-Datei. **Idempotent** (bereits vorhandene Mitglieder überspringen), analog zum bestehenden `collection_member`-Handling.

## 5. Architektur & API

### 5.1 Geführter Export (`POST /exports`)

Neuer Endpunkt `POST /exports` (`editor+`, JWT) mit **strukturierten** Parametern (statt freiem JSON):

```json
{
  "source": {
    "kind": "collection" | "type" | "category" | "saved_search" | "channel" | "selection",
    "id": "<collection|saved_search id>",
    "type": "product",
    "category": "<category id>",
    "object_ids": ["object:…"]
  },
  "fields": ["sku", "title", "price", "status"],
  "format": "csv" | "xlsx" | "json" | "bmecat",
  "locales": ["de", "en"],
  "options": { "header": true, "separator": ",", "include_system": false, "resolve_inheritance": false },
  "bmecat": { "catalog_id": "MEADOW-2026", "language": "eng", "supplier": "ACME" }
}
```

- **Vorgaben aus dem `json_export`-Konnektor (Stage 2b, implementiert):** `format`, `fields`, `locales` und `options.email_to` sind optional — fehlen sie in der Anfrage, zieht der Kern die Werte des ersten aktivierten `json_export`-Konnektors (Profil). Ohne Konnektor bleibt `format` bei `json` und der Worker leitet die Felder wie bisher ab. Analog füllt `POST /imports` `match_by_key`/`match_by_id`/`emit_events`/`source` aus dem `json_import`-Profil.

- **`source.kind`:** Bestimmt die Auflösung der Objektmenge. `all` = **jeder lebende Bestand** (ohne Universal-Wurzel) — die Datenhälfte einer Wiederherstellung; `selection` = Grid-Auswahl (explizite `object_ids`); `channel` = Objekte, die für `source.channel` freigegeben sind (optional `source.release_status`, Default `published`; der Kern filtert über `?released_for=`). Jede Quelle wird **seitenweise vollständig** gelesen (`limit` ist auf 1000 gedeckelt; die Kern-Sortierung `created_at DESC, id` hält die Seitengrenzen stabil) — vorher brach ein Export nach 1000 Objekten still ab.
- **`options.include_ancestors` (Default `true`):** Bei einer Teilmenge werden die **Kompositions-Vorfahren** ins Dokument geholt. Ein Objekt wird beim Import nur **mit** seinem Elternteil angelegt (`relationen.md`), ein Teil-Export ohne Vorfahren wäre also nicht importierbar. Beim Komplett-Export wirkungslos.
- **Auflösung:**
  - `collection` → `GET /collections/{id}/members` (nur Mitglieder).
  - `type` → `GET /objects?type=<type>`.
  - `category` → `GET /objects?category=<id>` (via `category_member`).
  - `saved_search` → `POST /saved-searches/{id}/execute` → IDs, dann Objekte.
  - `selection` → IDs aus `object_ids`.
- **`fields`:** Basis-Feldnamen + Attributnamen. Für mehrsprachige Attribute wird je `locale` eine Spalte `<attribut>_<code>` erzeugt (Standard-Locale zusätzlich als `<attribut>`/`<attribut>_<std>`).
- **Antwort:** `202 { job_run_id }` (Jobtyp `outbound_export`). Der Job läuft asynchron; das Ergebnis steht in `job_run.result`.

**Kleine Mengen synchron?** MVP: immer als Job (einheitlich, robust). Ein synchroner Shortcut für die Grid-Auswahl ist Ausbaustufe.

### 5.2 Worker (`meadow/worker/exports.py`)

Erweitert den `outbound_export`-Handler um die strukturierte Quelle und Formate:

1. Quelle auflösen → Objekt-IDs (siehe oben), Objekte über `GET /objects[...]` mit `?locale=<std>&resolve_inheritance=true` laden. **Umgesetzt (2026-09-11):** `options.resolve_inheritance` steuert dies – ohne die Option werden nur die **eigenen** Werte exportiert (Standard), mit der Option die **effektiven** Werte inkl. vererbter Attribute.
2. **Spalten** bauen: `["id", "type", "status", …]` + Attributnamen (je Locale erweitert); Reihenfolge = `fields`.
3. **Zeilen** bauen: je Objekt je Spalte den Wert extrahieren — Basis-Felder direkt, Attributwerte via Locale-Fallback (`language::resolve`-Konvention), mehrsprachig je Spalte. Quantifizierte Werte → `{"value","unit"}` in der Anzeigeeinheit über `GET /units/convert`.
4. **Ergebnis** in `job_run.result`:
   - `json`: `{ "format": "json", "exported": N, "references": N, "skipped_references": N, "added_ancestors": N, "columns": […], "document": {…MEF…} }`
   - `csv`/`xlsx`: `{ "format": "csv"|"xlsx", "exported": N, "columns": […], "rows": [{col: val, …}] }`
   - `bmecat`: `{ "format": "bmecat", "exported": N, "catalog": "…", "xml": "…", "report": {…} }` (§5.6)
5. Datei wird beim Download aus `columns`/`rows` serialisiert (siehe 5.3) — kein binäres Blob im Ergebnis.

**Das JSON-Dokument trägt die Beziehungen (2026-09-17, ersetzt `“references”: []`):** Nach den Objekten lädt der Worker die Kanten in Massen (`POST /references/query`, 500 IDs je Aufruf, seitenweise) und schreibt sie als MEF-`references` (`in`, `out`, `type` = Definitionsname, Kanten-`attributes`). Kanten, deren Endpunkt nicht im Dokument liegt, werden weggelassen und als `skipped_references` **gezählt**; Kanten zur Universal-Wurzel entfallen still (die Wurzel existiert in jeder Instanz, eine Baumspitze hängt der Kern selbst an). Mit `?releases=true` liefert der Kern je Objekt `[{channel, status}]`, das Dokument schreibt sie als `releases` mit. Zusammen mit `source.kind='all'` ist das Dokument damit **wiederherstellbar**: MCF zuerst (Modell), dann MEF (Daten) — Details: [integration-eventsystem.md](integration-eventsystem.md), Nachweis: [demodaten-generator.md](demodaten-generator.md) §4.4 Punkt 13.

### 5.3 Download-Endpunkt (`GET /exports/{run_id}/file`)

`GET /exports/{run_id}/file?format=csv|xlsx|json|bmecat` (`editor+`, JWT):

- Liest `job_run.result`.
- `csv` → CSV (UTF-8, `;`/`,` Trenner, Header) via `csv`/`StringIO`.
- `xlsx` → openpyxl-Workbook (`columns` als Header, `rows`), Bytes in den Response.
- `json` → MEF-Dokument (`document`) als JSON-Download.
- `bmecat` → XML-Dokument (`xml`) als `application/xml; charset=utf-8` (Dateiname `meadow-export-bmecat.xml`).
- **Objektstorage:** Sind die Inline-Daten (nach Archivierung über `EXPORT_INLINE_MAX_BYTES`) entfernt, antwortet der Endpunkt mit **302** auf `result.archive.url` (Presigned-URL des Objektstorage-Objekts); sonst wie oben inline.
- Antwort mit `Content-Disposition: attachment; filename="meadow-export.<ext>"`.

### 5.4 CSV/XLSX-Konventionen

- UTF-8 (CSV), `\r\n` Zeilenende, Header-Zeile optional.
- Leere Werte = leere Zelle; mehrsprachig = eine Spalte je Locale.
- Objekt-/Attributwerte JSON-serialisiert (z. B. `{"value":…,"unit":…}`); Struktur/Objekte als JSON-String, wenn nicht tabellarisch.
- **`url`-Spalten als Hyperlink (XLSX, Phase 9):** Zellen von `url`-Attributen (auch die Sprach-Spalten `<name>_<locale>`) bekommen zusätzlich zum Wert einen **Hyperlink** plus Link-Optik (blau/unterstrichen) — der Export springt direkt auf Website/Datenblatt. Nur `url`-typisierte Spalten werden verlinkt, und nur bei gültiger `http(s)`-Adresse (`is_valid_url`), sonst bleibt der Text unverändert; CSV/JSON exportieren weiterhin den reinen Wert. Der Worker lädt dafür die Typ-Flags einmal pro Lauf (`_load_attribute_flags` → `multilingual`/`quantified`/`urls`) und bestimmt die Spalten mit `_url_columns`.

### 5.5 Import & Collection-Bulk-Add

- **MEF-Import:** bestehendes `POST /imports` unverändert (Upload/JSON, `inbound_import`-Job) — in der UI über den **MEF-Modus** der Seite `/import` (siehe unten).
- **MEF-Modus der Import-Seite (implementiert, `editor+`):** Eine hochgeladene `.json`/`.mef`-Datei (bzw. ein Body, der mit `{` beginnt) wird als MEF-Dokument erkannt (`meadow/mef.py`: `parse_mef` + `summarize`) und **ohne Spalten-Mapping** importiert. Die Seite zeigt eine Zusammenfassung (Format-Version, Quelle, Objekte, Referenzen, Objekte mit `id`, Typ-Verteilung), eine Vorschau der ersten 20 Objekte (`id`/Typ/Status/Attribute) und blendet den Default-Objekttyp aus.
  - **Abgleich:** `match_by_key` (Business-Schlüssel) wie im Tabellen-Modus; zusätzlich ein Schalter **„Abgleich per Objekt-ID“** (`?match_by_id=true`) — automatisch aktiviert, wenn das Dokument `source: "meadow"` trägt, weil der Meadow-Export die Core-Record-ID in `id` schreibt. Damit ist **Export → Import ein Update** derselben Objekte (E2E verifiziert: `updated: 2`, `created: 0`, identische IDs, keine Duplikate); ohne den Schalter greift die Quell-Referenz-Deduplizierung laut MEF-Spezifikation. Die `source`-Eingabe wird aus dem Dokument vorbelegt und kann überschrieben werden (leer = Dokument-Quelle).
- **Tabellarischer Import (implementiert, `editor+`):** gemeinsamer, UI-unabhängiger Adapter `meadow/tabular.py` für **CSV und XLSX** → MEF:
  - **Parsen:** `parse_csv` (Delimiter-Erkennung `, ; \t |`, UTF-8/-BOM/CP1252/Latin-1) bzw. `parse_xlsx` (openpyxl, `sheet_names` für die Arbeitsblatt-Auswahl); `parse_table(name, content, …)` wählt per Dateiendung (`.xlsx`/`.xlsm` → XLSX, sonst CSV).
  - **Mapping-Zielsyntax:** `""` = Spalte ignorieren, `id`/`type`/`status` = MEF-Basisfelder, `<attribut>` = Attributwert, `<attribut>|<lang>` = mehrsprachiger Wert (`{lang: wert}`, z. B. `title|de`).
  - **MEF-Bau:** `build_mef(table, mapping, default_type=…, source=…)` erzeugt `{format_version, source, created_at, objects, references}`; leere `type`/`status`-Zellen fallen auf den Default-Typ bzw. `draft` zurück.
  - **Import:** `POST /imports` (`match_by_key`, `source`, `emit_events`) → `inbound_import`-Job; Upsert per Business-Schlüssel bzw. Source-Referenz, damit wiederholte Importe **idempotent** sind.
  - **Konventions-Attribute:** Die Objekttypen benennen ein `label_attribute` (hier überall `name`) und optionale `key_attributes`, für die **keine Attributdefinition** existiert — der Grid-Export schreibt `name` aber mit. Damit ein Export→Import-Roundtrip ohne Handarbeit funktioniert, werden diese Namen als Mapping-Ziele angeboten (ohne Sprache: `name (aktuelle Sprache)`) und **in der aktuellen UI-Sprache** als `{sprache: wert}` geschrieben (entspricht der Ablage-Konvention des Bestands, vgl. Kategorien).
- **UI:** Seite **`/import`** (`editor+`) — Upload, Parse-Status, Arbeitsblatt-Auswahl (nur bei mehrblättrigen Arbeitsmappen), Spalten-Mapping (Auto-Mapping bei Namensgleichheit, Freitext-Attribute möglich), Default-Objekttyp, Business-Schlüssel, Quelle, `emit_events`-Schalter, **Vorschau** (Dry-Run ohne Persistenz) und **Import** mit Report (`processed/created/updated/failed`).
- **KI-Vorschlag für das Spalten-Mapping (implementiert 2026-09-21, `plan/detail/ki-assistenz.md`):** Knopf **„Zuordnung vorschlagen“** über der Mapping-Zeile; der Vorschlag füllt die Auswahlfelder der Spalten, gespeichert/importiert wird wie immer von Hand. Der Bereich heißt `import_mapping`, sein Prüfer ist **`meadow/tabular.check_mapping`** — dasselbe Modul, das die Zuordnung ausführt (`build_mef`), mit dem Modellwissen des Kerns (Attribut-Vokabular, Objekttypen mit Business-Key und Label-Attribut, Datensprachen). Geprüft wird: unbekannte Spalte (**Fehler** — die Antwort hat die Datei nicht verstanden), zwei Spalten auf dasselbe Ziel (**Fehler** — `build_mef` behält still die letzte), Sprache außerhalb der Datensprachen, Sprache auf `id`/`type`/`status`, gar nichts zugeordnet, kein Objekttyp (jeweils Fehler); als **Hinweis**: Ziel ohne Attributdefinition (der Kern schreibt jeden Namen, das Merkmal bleibt nur untypisiert), mehrsprachiges Merkmal ohne Sprache, Spalte ohne Kopfzeile, Business-Key von keiner Spalte gefüllt. Die Spalten gehen als **Kopfzeile plus wertfreie Form** (`tabular.describe_columns`: Art, Länge, leere Zeilen, Eindeutigkeit) an das Modell — **kein Zellwert** verlässt die Instanz Richtung Modell, und die Zuordnung selbst trägt keine Objektwerte. **Offen:** eine echte Import-Vorschau im Kern (MEF-Dry-Run gegen das Modell) würde die Prüfung auf die Zeilen ausdehnen; heute prüft sie die Zuordnung gegen Datei und Modell.
- **Roundtrip-Hinweis:** Das MEF-`id` ist eine **Source-Referenz**, nicht die Core-Objekt-ID. Ein Export→Import ohne Business-Schlüssel legt daher **neue** Objekte an (Source-Ref = exportierte `id`); für ein Update muss derselbe **Business-Schlüssel** wie im Export gesetzt werden (z. B. `sku`).
- **Collection-Bulk-Add:** neuer Endpunkt `POST /collections/{id}/members` (`editor+`, JWT) mit `{ "object_ids": ["…"] }` — legt `collection_member`-Relationen an (`attributes.rank` aufsteigend, `added_at`); **idempotent** (bereits vorhandene Mitglieder überspringen), Antwort `{ added, skipped }`.

### 5.6 BMEcat-/ETIM-Ausgabe (`format: "bmecat"`, umgesetzt 2026-09-13)

Ein Katalogdokument ist **keine Tabelle**: Spaltenauswahl, Trennzeichen und Header-Zeile sind hier bedeutungslos. Deshalb bringt das Format ein **eigenes Profil** mit, und der Wizard tauscht die Karten: Feldauswahl und CSV-Optionen verschwinden, die Karte **„BMEcat-Katalogdaten"** erscheint (Quelle, Sprache und Optionen des allgemeinen Teils bleiben nutzbar). Die Regeln, das Mapping und die Grenzen stehen in [bmecat-export.md](bmecat-export.md) — hier nur die UI-Sicht.

| Profilfeld | Bedeutung |
|---|---|
| `catalog_id`, `catalog_name`, `catalog_version` | Kopf des Dokuments (`CATALOG`); `CATALOG_ID` ist der Abnehmer-Schlüssel |
| `language` | Katalogsprache (`eng`/`ger`/…, BMEcat-Codes) — **eine Sprache je Datei** |
| `currency` | `CURRENCY` (ISO-4217, Default `EUR`) |
| `supplier`, `buyer` | `SUPPLIER_NAME`/`BUYER_NAME` — Pflicht im ETIM-Profil, wird **nicht** erfunden || `pid_attribute` | Attribut, dessen Wert `SUPPLIER_PID` wird (Fallback `code`, dann `sku`; Schema-Limit 32 Zeichen) |
| `etim_version` | Versionsteil von `REFERENCE_FEATURE_SYSTEM_NAME` — **Rückfall** für eine Klasse ohne registrierten Standard (sonst gewinnt das Standard-Register, [kategorisierung.md](kategorisierung.md)) |
| `transaction` | `new_productdata` (preislos, V1) oder `new_catalog` (Kataloglieferung mit Preisen) |
| `price_attribute`, `price_type` | Attribut für `PRICE_AMOUNT` und Preisart (`net_list`/`net_customer`/`nrp`) — nur bei `new_catalog` |
| `order_unit`, `content_unit` | BMEcat-Einheitencodes (UN/ECE Rec 20, Default `C62`) — nur bei `new_catalog` (und immer bei 1.2) |
| `version` | `2005` (Default, ETIM-Profil) oder `1.2` (klassische `ARTICLE`-Datei) |
| `schema_variant` | nur bei 1.2: `dtd` (Default, Rev. A) oder `xsd` — wählt Namespace und Validierungsartefakt |

- **Vorgaben/Zwänge:** der Wizard schickt kein `fields`-Array (die Spaltenauswahl ist ausgeblendet) und die Formatprüfung entfällt — `bmecat` exportiert die Klasse, nicht die gewählten Felder. Bei `transaction = Kataloglieferung` erscheinen zusätzlich Preis-Attribut, Preisart und die beiden Einheitencodes (BMEcat-Codes, nicht Meadow-Symbole); der Standard verlangt dort Bestelldaten, einen Preis und einen UDX-Block je Produkt, deshalb wird ein Produkt ohne Preis übersprungen und im Bericht genannt. **BMEcat 1.2 (V1b, 2026-09-13)** hängt an denselben Feldern: die Wahl **„1.2"** blendet die **Schema-Variante** ein, **deaktiviert** die Transaktionswahl (1.2 kennt keine preislose Lieferung) und zeigt die Preiszeile immer; das vertikale Layout der Karte bleibt dabei erhalten (kein nacktes Control). Alles andere (Quelle inkl. Kanal/Kollektion, `email_to`, geplante Exporte) läuft über denselben Job.
- **Bericht statt Download-Überraschung:** der Report-Dialog zeigt zuerst den **Dialekt** („BMEcat 1.2 (DTD), Transaktion new_catalog") und dann Produkte/Klassen/Merkmale/„mit Wert"/„als `-`", die übersprungenen Objekte mit Grund (inkl. „kein Preis"), Pflichtmerkmale ohne Wert, Klartext-Merkmale sowie das **Schema-Verdikt** der gewählten Version — inklusive der Zeile **„zugeordnete Taxonomieknoten ohne `code`"**, wenn Produkte deshalb nicht im Katalog landen (§11.10 in [bmecat-export.md](bmecat-export.md)), dem **Referenz-Merkmalsystem** samt Hinweis, wenn keines registriert ist, und den **Hinweisen** (`issues`) zu Inhalten, die der Dialekt nicht ausdrücken kann — bei 1.2 die nicht schreibbare Basisprodukt-Referenz (§11.12).
- **Noch offen:** mehrere Sprachen/Kanäle je Lauf, 1.2-`VARIANTS`, Streaming für sehr große Kataloge.

### 5.7 Gespeicherte Export-Profile (`/export-profiles`, umgesetzt 2026-09-14)

Ein Export wiederholt sich: dieselben Spalten, dieselbe Sprache, dieselben Optionen, derselbe Katalogkopf. Deshalb ist die Konfiguration eine **eigene Entität** — `export_profile`, der `ExportRequest` **ohne `source`**: `format` plus `parameters` (`fields`, `locales`, `options`, `bmecat`). Das **Was** (Quelle) bleibt eine Entscheidung pro Lauf, das **Wie** ist das Wiederverwendbare.

| Endpunkt | Rolle | Zweck |
|---|---|---|
| `GET /export-profiles` | `editor+` | Liste (optional `?format=`), gefüllt das Auswahlfeld des Wizards |
| `GET /export-profiles/{id}` | `editor+` | Ein Profil — per Record-Key, `id` oder **Name** |
| `POST /export-profiles` | `admin` | Anlegen (`name` ist der Schlüssel, unveränderlich) |
| `PATCH /export-profiles/{id}` | `admin` | Format, Notiz, Parameter (`notes: ""` löscht) |
| `DELETE /export-profiles/{id}` | `admin` | Löschen (keine Exportdaten betroffen) |

- **Admin-Seite `/export-profiles`** (neu, `admin`, Nav-Eintrag „Export-Profile"): Tabelle (Name, Format, Parameter-Zusammenfassung, Notiz, geändert), Dialog mit Name/Format/Notiz und den **Parametern als JSON** — bewusst als JSON, weil der Assistent genau den Rumpf schreibt, den `POST /exports` erwartet, und ein Operator ihn so herauskopieren kann. Ungültiges JSON und eine falsche Wurzel werden vor dem Senden gemeldet, die Kernfehler (unbekanntes Format, unbekannter Parameter-Schlüssel, doppelter Name) kommen als Meldung zurück.
- **Wizard `/export`:** Karte **„Gespeichertes Profil"** ganz oben. Die Auswahl **füllt das Formular** (Format, Locale-Mehrfachauswahl, Optionen inkl. „Vererbte Werte", Spaltenauswahl, BMEcat-Block inkl. Dialekt und Schema-Variante — auch die Sichtbarkeiten wie Preiszeile/Schema-Variante werden nachgezogen); **„Als Profil speichern"** (`admin`) legt den Ist-Stand ab und wählt das neue Profil aus. Die E-Mail-Empfänger bleiben außen vor (pro Lieferung), und Felder, die die aktuelle Quelle nicht anbietet, werden **gezählt und gemeldet** statt still zu verschwinden.
- **Portabel:** die MCF-Sektion `export_profiles` nimmt die Profile mit ([konfiguration-export-import.md](konfiguration-export-import.md) §4.11) — ein Umzug Staging → Prod hätte sonst die Daten, aber nicht die Art zu exportieren.
- **Noch offen:** mehrere Profile je Kanal verwalten/umschalten, Historie/Versionierung, `source` als Bestandteil („gespeicherter Lauf").

## 6. Frontend (NiceGUI)

- Neue Seite **`/export`** (Wizard, `editor+`-Rolle):
  0. **Gespeichertes Profil** (§5.7) — Auswahl lädt Format, Sprachen, Optionen, Spalten und BMEcat-Block; „Als Profil speichern" (admin) legt den Ist-Stand ab. Die **Quelle** ist bewusst nicht Teil eines Profils.
  1. **Quelle** (Select: **Gesamter Bestand** / Typ / Kategorie-Baum / Collection / gespeicherte Suche / Grid-Auswahl; abhängig davon Detailselektor — bei „Gesamter Bestand“ keiner).
  2. **Felder** — **gruppiert nach Attributgruppe** (Basis-Felder zuerst, je Gruppe eine `ui.expansion` mit Anzahl und Sammel-Checkbox, „Attribute ohne Gruppe" zuletzt); gezeigt werden **nur die Attribute, die der gewählte Objekttyp erlaubt** (`applicable_types`, leere Liste = alle Typen — die Quellen Collection/Suche/Kanal legen keinen Typ fest, dort bleiben alle sichtbar, mit Hinweiszeile). Die Checkbox zeigt `Label (name)`, weil der Spaltenname bei einem importierten Standard der Merkmalscode ist. Die Auswahl bleibt beim Umschalten der Quelle erhalten (Zustand außerhalb des Renderers); gerendert wird von `meadow/frontend/export_fields.py` (rein, getestet).
  3. **Format + Sprache + Optionen** — Format-Select (CSV/XLSX/JSON/**BMEcat**), **Locale-Mehrfachauswahl**, Optionen (Header, Trenner, quantifizierte Werte, `include_system`). Bei Format **BMEcat** entfallen Feldauswahl und CSV-Optionen; stattdessen erscheint die Profilkarte (§5.6).
  4. **„Vorschau"** (Beispielzeilen für die gewählten Spalten) und **„Exportieren"** → Report (Anzahl, Download via `GET /exports/{run_id}/file`).
- Im **Product-Grid** (`/products`): „Export-Auswahl / Export-Filter" Aktion.
- Auf der **Collections-Seite** (`/saved-searches`): „Exportieren"-Button + „Mitglieder hinzufügen"-Import-Dialog.
- Neue Seite **`/import`** (tabellarischer Import, `editor+`): Upload (CSV/XLSX/**MEF-JSON**) → Parse-Status → je nach Datei **Spalten-Mapping** (CSV/XLSX, mit Arbeitsblatt-Auswahl bei Mehrblatt) oder **MEF-Zusammenfassung** → Default-Objekttyp / Business-Schlüssel / `match_by_id` / Quelle / `emit_events` → **Vorschau** und **Import** mit Report-Dialog. i18n-Keys `import.*`.
- Neue Seite **`/export-profiles`** (Profile, `admin`, §5.7): Tabelle + Dialog (Parameter als JSON) + Löschen mit Rückfrage. i18n-Keys `export_profiles.*` und `export.profile_*`.
- In der Karte **„Recent exports"** (auf `/export`): Download pro Lauf; für Admins zusätzlich **Löschen** pro Zeile und **„Alle löschen"** (`DELETE /jobs/runs/{run_id}`, admin).
- **„Recent exports" liest die Kurzfassung des Laufs (2026-09-18):** die Liste zeigt Zeitpunkt, Format, Anzahl und Trigger und holt dafür `list_job_runs(limit=100, include_result=False)` — die Klassifizierung läuft über die reine Funktion `recent_exports(runs)` und `result_summary` (Top-Level-Felder ≤ 2 KB, Vertrag in [jobs-scheduler.md](jobs-scheduler.md)), **nicht** über `result`. Vorher stand dort `include_result=true&limit=100`: das Export-Dokument liegt im `result`, die Antwort war **20 MB** und die nächste Anfrage riss die SurrealDB-Verbindung ab (`500 Connection reset`, danach fielen `/languages` und `/system/logs` mit aus). Läufe ohne Kurzfassung (vor der Änderung) erscheinen nicht in der Liste. Der **Download** und der **Report** nach einem Lauf bleiben unverändert: sie holen den einzelnen Lauf samt Result.
- **Fallback-Werte in Auswahlen** (2026-09-18): die Sprachauswahlen fielen bei fehlgeschlagener Referenzladung auf ein Literal zurück (`"de"`/`"en"`) — bei leerer Optionsliste wirft `ui.select` `Invalid value` und die Seite antwortet **500** statt degradiert zu laden. Jetzt: `None` bzw. der erste Optionsschlüssel.
- i18n-Keys (`export.*`, `import.*`, `collection.import_members`) in en/de/fr.

## 7. Jobs & Scheduler

- **Implementiert:** Geplante Exporte als `job_definition` (`type = outbound_export`, Cron im 6-Feld-Format, `parameters = {source, fields, format, locales, options, email_to?}`); der Scheduler führt sie periodisch aus. Ergebnis in `job_run.result`; Download über `GET /exports/{run_id}/file` (bei Objektstorage-Ablage per 302 auf die Presigned-URL).
- **Ausbau / zurückgestellt — automatische Retention (30 Tage):** `job_run`-Datensätze **und** archivierte S3-Objekte nach **30 Tagen** automatisch löschen. Noch **nicht** umgesetzt, weil zuvor ein **Papierkorb-Konzept** (Soft-Delete/Wiederherstellung) geplant ist, mit dem die Retention abgestimmt werden muss. Bis dahin nur **manuelles** Löschen: „Löschen"/„Alle löschen" in der Karte „Recent exports" bzw. Admin-Seite `/jobs` (Runs-Tab).

## 8. Datenmodell & Entscheidungen

| Entscheidung | Entscheidung |
|---|---|
| Connector `json_export` behalten? | **Ja** — als **Profil** für die Vorgaben des technischen Pfads (`format`/`fields`/`locales`/`options.email_to`), implementiert in Stage 2b; der UI-Export nutzt strukturierte Job-Parameter und überschreibt das Profil bei Bedarf. |
| Dateiablage | **`job_run.result`** — strukturierte `columns`/`rows` (bzw. MEF) dort; Datei beim Download serialisiert. |
| Format | **CSV + XLSX (openpyxl) + JSON** im MVP. |
| Sprache | **Mehrere Locale-Spalten** (`<attribut>_<code>`); Fallback-Kette. |
| Rechte | **Export `editor+`**; Import `editor+`; Collection-Verwaltung `admin`. |
| Import-Ziel | MEF wiederverwenden — CSV **und** XLSX laufen über den gemeinsamen Tabellen-Adapter (`meadow/tabular.py`), kein separater Importpfad; Collection-Bulk-Add separat. |
| Feldauswahl | **Gruppiert** nach `attribute_group` und **typtreu** (nur `applicable_types` des gewählten Objekttyps) — ein importierter Klassifikationsstandard bringt Tausende Merkmale mit, eine flache Liste ist nicht bedienbar ([bmecat-export.md](bmecat-export.md) §11.9). |

## 9. Implementierungsschritte

1. **Kern:** `POST /exports` (strukturierte Parameter, `editor+`) + `GET /exports/{run_id}/file` (Download) + `POST /collections/{id}/members` (Bulk-Add); Validierung Quelle/Felder/Format. Registrierung in `api/mod.rs`; `job_type = outbound_export` wiederverwendet.
2. **Worker:** `exports.py` erweitern — Quell-Auflösung (collection/type/category/saved_search/selection), Spalten-/Zeilenbau, Locale-Mehrsprachigkeit, quantifizierte Werte, Ergebnis in `job_run.result`. `openpyxl` als Abhängigkeit.
3. **Frontend:** `/export`-Wizard (`editor+`), Schnell-Aktionen im Grid & auf der Collections-Seite, `api.py`-Methoden (`create_export`, `get_export_file`, `add_collection_members`), i18n `export.*`.
4. **Tests:** Export Collection/Typ/Selection; CSV/XLSX-Serialisierung; Locale-Spalten; Bulk-Add idempotent; Rechte; 400 bei ungültigem Format/Quelle.

## 10. Testfälle

- Export einer Collection: Mitgliederzahl, gewählte Felder, Locale-Spalten korrekt.
- CSV/XLSX: Header-Zeile, Trenner, leere Werte, quantifizierte Werte in Anzeigeeinheit.
- Mehrsprachig: `title_de`/`title_en` Spalten mit Fallback.
- CSV-Roundtrip (Export → Import) idempotent (Upsert per Business-Schlüssel).
- **Tabellarischer Import:** CSV und XLSX mit Auto-Mapping; Mehrblatt-XLSX (Arbeitsblatt-Wechsel, leeres Blatt → Hinweis); mehrsprachiges Ziel `attribut|<lang>` schreibt `{lang: wert}`; **Roundtrip Grid-Export → Import** inkl. der Konventions-Spalte `name` (schreibt `{sprache: wert}` in der UI-Sprache); leere `type`/`status`-Zellen → Default-Typ/`draft`; Wiederholungsimport aktualisiert statt zu duplizieren (`created: 0`, `updated: N`).
- **MEF-Roundtrip:** Export (Format `json`) → Download → Upload auf `/import` → Zusammenfassung/Vorschau korrekt; mit `match_by_id` `created: 0`, `updated: N` und unveränderten Objekt-IDs; ohne `match_by_id` Anlage als neue Objekte (Quell-Referenz = MEF-`id`).
- Große Menge → Job mit Report (`job_run.result`).
- Collection-Bulk-Add überspringt vorhandene Mitglieder (idempotent).
- `viewer` → 403; unbekanntes Format/Quelle → 400; `POST /exports` mit leerer Quelle → 400.

## 11. Offene Punkte

- **Automatische Retention (30 Tage)** für `job_run` **und** archivierte S3-Objekte — **zurückgestellt**; muss mit dem geplanten **Papierkorb-Konzept** (Soft-Delete/Wiederherstellung) abgestimmt werden. Aktuell nur manuelles Löschen (UI `/export` bzw. Admin `/jobs`).
- **Dauerhafte Download-Route** ohne Presigned-URL-Ablauf (7 Tage): Core mit eigenem S3-Client (Streaming) statt 302 auf eine ablaufende Signatur.
- Optionaler synchroner Export kleiner Mengen (Grid-Auswahl).
- **Werte-Normalisierung beim tabellarischen Import** (2026-09-21 im Live-Test aufgefallen, nicht geändert): `build_mef` reicht jede Zelle als **String** weiter, und der Kern prüft auf dem Schreibpfad außer `url` keinen Datentyp. Ein Lieferanten-CSV mit deutschem Dezimalkomma landet damit als `weight_kg: "1,25"` im Objekt — sichtbar in der Vorschau, aber jede Regel, die damit rechnet (`float(...)`), scheitert. Sauber wäre eine Normalisierung nach dem **Ziel-Datentyp** (`number`/`date`, inkl. Trennzeichen) im tabellarischen Pfad; heute ist das eine stille Falle.

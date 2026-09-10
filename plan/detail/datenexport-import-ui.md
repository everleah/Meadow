# Daten-Export & -Import über die Oberfläche (UI-geführt)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) und Abschnitt 11 ([Frontend](../meadow-pim-architektur.md#11-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), ergänzend zum bestehenden MEF-Export/Import (Phase 6)
- **Stand:** Umsetzungsreif — Core-API & Worker-Export (CSV/XLSX/JSON, Locale-Auflösung) implementiert & E2E-verifiziert; `/export`-Wizard (`editor+`) implementiert & im Browser verifiziert (Vorschau + Export + Download). **Collection-Bulk-Add** (`POST /collections/{id}/members`, idempotent) implementiert & verifiziert; die Collections-Seite bietet einen **„Add members"-Dialog** und einen **„Export"-Button** (führt mit vorausgewählter Collection (`?collection=<id>`) in den Wizard). **Grid-Auswahl-/Filter-Export** im `/products`-Grid implementiert & verifiziert (Toolbar-Download-Button → Dialog mit Scope „Selected rows" / „All filter matches" + Format; nutzt die sichtbaren Grid-Spalten als Felder über die `selection`-Quelle). **Geplante Exporte** implementiert & E2E-verifiziert: ein Export wird als `job_definition` (`type = outbound_export`, Cron im 6-Feld-Format) gespeichert und vom bestehenden Scheduler periodisch als `outbound_export`-Job ausgeführt; im `/export`-Wizard (Admin) über „Schedule…" anlegbar, mit Liste (Run-now/Aktiv/Löschen) und **„Recent exports"**-Liste (Download pro Lauf, Ablage im `job_run.result`). **E-Mail-Versand** implementiert & E2E-verifiziert: `options.email_to` → der Worker versendet den Export als Anhang (`meadow/worker/mail.py`, SMTP per Env; Dev-Catcher `mailhog` in `docker-compose.dev.yml`, SMTP 1025 / UI 8025). **Objektstorage-Ablage** implementiert & E2E-verifiziert: bei konfiguriertem S3/MinIO archiviert der Worker die Datei (`result.archive` mit `{bucket, key, size, url}`, Presigned-URL) und entfernt ab `EXPORT_INLINE_MAX_BYTES` (Standard 1 MB) die Inline-Daten; `GET /exports/{run_id}/file` leitet dann per **302** auf die Presigned-URL um (`inline_present`), sonst wird wie zuvor inline serialisiert. Ausstehend: CSV-Import mit Spalten-Mapping.

**Getroffene Entscheidungen:**

- **Dateiablage:** `job_run.result` — die strukturierten Daten (`columns` + `rows` bzw. MEF-Dokument) liegen im Job-Ergebnis; die Datei wird beim Download daraus serialisiert (CSV/XLSX/JSON) — kein binäres Blob im Ergebnis, keine Objektstorage-Ablage im MVP.
- **XLSX:** im MVP enthalten (openpyxl). Formate `csv | xlsx | json`.
- **Rechte:** Export `editor+`; Import `editor+`; Collection-Verwaltung `admin`.
- **Mehrere Locale-Spalten:** ja — pro gewählter Sprache eine Spalte `<attribut>_<code>` (z. B. `title_de`, `title_en`); Standardsprache zusätzlich als `<attribut>`/`<attribut>_<std>`.

## 1. Zielsetzung & Abgrenzung

Der technische Datenexport läuft heute über den `json_export`-Connector + `outbound_export`-Job: Konfiguration als freies JSON in `/connectors` (admin-only), Ergebnis als MEF-JSON in `job_run.result`. Das ist für technisch versierte Anwender sinnvoll und **bleibt erhalten**.

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
| Quelle | Objekttyp · Kategorie(-baum) · **Collection** · gespeicherte Suche · **aktuelle Grid-Auswahl/Filter** |
| Felder | Basis-Felder (`id`, `type`, `status`, `updated_at`, …) + Attributdefinitionen des Typs; Checkbox-Auswahltabelle mit Spalten-Reihenfolge / „alle auswählen" |
| Format | **CSV** (UTF-8, Excel/Plain) · **XLSX** (openpyxl) · JSON (MEF) |
| Sprache | Eine oder **mehrere** Locales (Fallback-Kette); pro Sprache eine Spalte `<attribut>_<code>` |
| Optionen | Header-Zeile, Trennzeichen (`;`/`,`), quantifizierte Werte als `{value, unit}` in Anzeigeeinheit, `include_system` |

**Ausgabe:** Vorschau (Beispielzeilen) + Download (klein) oder Hintergrundjob + Report/Download-Link (groß).

**Ausbau:** geplante Exporte (cron), E-Mail-Versand, Objektstorage-Ablage, UI-Import (CSV/MEF mit Mapping/Preview/Dry-Run).

## 4. Collection-Mitglieder (Use-Case)

- **Export:** `GET /collections/{id}/members` liefert die Mitglieder. Im Export-Wizard als Quelle „Collection" wählbar; zusätzlich ein **„Als CSV/JSON exportieren"-Button** auf der Collections-Verwaltungsseite (`/saved-searches`, Tab Collections) und im Grid (Collections-Ansicht).
- **Import / Mitglieder aufnehmen:** Bulk-Add von Objekten in eine Collection als `collection_member`-Relationen (`attributes.rank`, `added_at`). Quelle: Grid-Auswahl oder Import-Datei. **Idempotent** (bereits vorhandene Mitglieder überspringen), analog zum bestehenden `collection_member`-Handling.

## 5. Architektur & API

### 5.1 Geführter Export (`POST /exports`)

Neuer Endpunkt `POST /exports` (`editor+`, JWT) mit **strukturierten** Parametern (statt freiem JSON):

```json
{
  "source": {
    "kind": "collection" | "type" | "category" | "saved_search" | "selection",
    "id": "<collection|saved_search id>",
    "type": "product",
    "category": "<category id>",
    "object_ids": ["object:…"]
  },
  "fields": ["sku", "title", "price", "status"],
  "format": "csv" | "xlsx" | "json",
  "locales": ["de", "en"],
  "options": { "header": true, "separator": ",", "include_system": false }
}
```

- **`source.kind`:** Bestimmt die Auflösung der Objektmenge. `selection` = Grid-Auswahl (explizite `object_ids`).
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

1. Quelle auflösen → Objekt-IDs (siehe oben), Objekte über `GET /objects[...]` mit `?locale=<std>&resolve_inheritance=true` laden.
2. **Spalten** bauen: `["id", "type", "status", …]` + Attributnamen (je Locale erweitert); Reihenfolge = `fields`.
3. **Zeilen** bauen: je Objekt je Spalte den Wert extrahieren — Basis-Felder direkt, Attributwerte via Locale-Fallback (`language::resolve`-Konvention), mehrsprachig je Spalte. Quantifizierte Werte → `{"value","unit"}` in der Anzeigeeinheit über `GET /units/convert`.
4. **Ergebnis** in `job_run.result`:
   - `json`: `{ "format": "json", "exported": N, "columns": […], "document": {…MEF…} }`
   - `csv`/`xlsx`: `{ "format": "csv"|"xlsx", "exported": N, "columns": […], "rows": [{col: val, …}] }`
5. Datei wird beim Download aus `columns`/`rows` serialisiert (siehe 5.3) — kein binäres Blob im Ergebnis.

### 5.3 Download-Endpunkt (`GET /exports/{run_id}/file`)

`GET /exports/{run_id}/file?format=csv|xlsx|json` (`editor+`, JWT):

- Liest `job_run.result`.
- `csv` → CSV (UTF-8, `;`/`,` Trenner, Header) via `csv`/`StringIO`.
- `xlsx` → openpyxl-Workbook (`columns` als Header, `rows`), Bytes in den Response.
- `json` → MEF-Dokument (`document`) als JSON-Download.
- **Objektstorage:** Sind die Inline-Daten (nach Archivierung über `EXPORT_INLINE_MAX_BYTES`) entfernt, antwortet der Endpunkt mit **302** auf `result.archive.url` (Presigned-URL des Objektstorage-Objekts); sonst wie oben inline.
- Antwort mit `Content-Disposition: attachment; filename="meadow-export.<ext>"`.

### 5.4 CSV/XLSX-Konventionen

- UTF-8 (CSV), `\r\n` Zeilenende, Header-Zeile optional.
- Leere Werte = leere Zelle; mehrsprachig = eine Spalte je Locale.
- Objekt-/Attributwerte JSON-serialisiert (z. B. `{"value":…,"unit":…}`); Struktur/Objekte als JSON-String, wenn nicht tabellarisch.

### 5.5 Import & Collection-Bulk-Add

- **MEF-Import:** bestehendes `POST /imports` unverändert (Upload/JSON, `inbound_import`-Job).
- **CSV-Import (Ausbau):** Worker-Adapter (CSV → MEF) mit Spalten-Mapping/Preview/Dry-Run.
- **Collection-Bulk-Add:** neuer Endpunkt `POST /collections/{id}/members` (`editor+`, JWT) mit `{ "object_ids": ["…"] }` — legt `collection_member`-Relationen an (`attributes.rank` aufsteigend, `added_at`); **idempotent** (bereits vorhandene Mitglieder überspringen), Antwort `{ added, skipped }`.

## 6. Frontend (NiceGUI)

- Neue Seite **`/export`** (Wizard, `editor+`-Rolle):
  1. **Quelle** (Select: Typ / Kategorie-Baum / Collection / gespeicherte Suche / Grid-Auswahl; abhängig davon Detailselektor).
  2. **Felder** (Auswahltabelle mit Spalten-Reihenfolge, „alle auswählen", Vorschau der verfügbaren Attributdefinitionen).
  3. **Format + Sprache + Optionen** — Format-Select (CSV/XLSX/JSON), **Locale-Mehrfachauswahl**, Optionen (Header, Trenner, quantifizierte Werte, `include_system`).
  4. **„Vorschau"** (Beispielzeilen für die gewählten Spalten) und **„Exportieren"** → Report (Anzahl, Download via `GET /exports/{run_id}/file`).
- Im **Product-Grid** (`/products`): „Export-Auswahl / Export-Filter" Aktion.
- Auf der **Collections-Seite** (`/saved-searches`): „Exportieren"-Button + „Mitglieder hinzufügen"-Import-Dialog.
- In der Karte **„Recent exports"** (auf `/export`): Download pro Lauf; für Admins zusätzlich **Löschen** pro Zeile und **„Alle löschen"** (`DELETE /jobs/runs/{run_id}`, admin).
- i18n-Keys (`export.*`, `collection.import_members`) in en/de/fr.

## 7. Jobs & Scheduler

- **Implementiert:** Geplante Exporte als `job_definition` (`type = outbound_export`, Cron im 6-Feld-Format, `parameters = {source, fields, format, locales, options, email_to?}`); der Scheduler führt sie periodisch aus. Ergebnis in `job_run.result`; Download über `GET /exports/{run_id}/file` (bei Objektstorage-Ablage per 302 auf die Presigned-URL).
- **Ausbau / zurückgestellt — automatische Retention (30 Tage):** `job_run`-Datensätze **und** archivierte S3-Objekte nach **30 Tagen** automatisch löschen. Noch **nicht** umgesetzt, weil zuvor ein **Papierkorb-Konzept** (Soft-Delete/Wiederherstellung) geplant ist, mit dem die Retention abgestimmt werden muss. Bis dahin nur **manuelles** Löschen: „Löschen"/„Alle löschen" in der Karte „Recent exports" bzw. Admin-Seite `/jobs` (Runs-Tab).

## 8. Datenmodell & Entscheidungen

| Entscheidung | Entscheidung |
|---|---|
| Connector `json_export` behalten? | **Ja** — technischer Pfad bleibt; UI-Export nutzt strukturierte Job-Parameter. |
| Dateiablage | **`job_run.result`** — strukturierte `columns`/`rows` (bzw. MEF) dort; Datei beim Download serialisiert. |
| Format | **CSV + XLSX (openpyxl) + JSON** im MVP. |
| Sprache | **Mehrere Locale-Spalten** (`<attribut>_<code>`); Fallback-Kette. |
| Rechte | **Export `editor+`**; Import `editor+`; Collection-Verwaltung `admin`. |
| Import-Ziel | MEF wiederverwenden; CSV als Adapter (Ausbau); Collection-Bulk-Add separat. |

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
- Große Menge → Job mit Report (`job_run.result`).
- Collection-Bulk-Add überspringt vorhandene Mitglieder (idempotent).
- `viewer` → 403; unbekanntes Format/Quelle → 400; `POST /exports` mit leerer Quelle → 400.

## 11. Offene Punkte

- **Automatische Retention (30 Tage)** für `job_run` **und** archivierte S3-Objekte — **zurückgestellt**; muss mit dem geplanten **Papierkorb-Konzept** (Soft-Delete/Wiederherstellung) abgestimmt werden. Aktuell nur manuelles Löschen (UI `/export` bzw. Admin `/jobs`).
- **Dauerhafte Download-Route** ohne Presigned-URL-Ablauf (7 Tage): Core mit eigenem S3-Client (Streaming) statt 302 auf eine ablaufende Signatur.
- CSV-Spalten-Mapping beim Import (Ausbau); optionaler synchroner Export kleiner Mengen.
- Optional: Anzeige/Verwaltung der Export-Schedules auch im Admin-`/jobs`-UI (Job-Typen-Liste dort veraltet).

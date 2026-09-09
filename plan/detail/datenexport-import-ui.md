# Daten-Export & -Import über die Oberfläche (UI-geführt)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) und Abschnitt 11 ([Frontend](../meadow-pim-architektur.md#11-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), ergänzend zum bestehenden MEF-Export/Import (Phase 6)
- **Stand:** Entwurf

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

**MVP (UI-Export):**

| Schritt | Auswahl |
|---|---|
| Quelle | Objekttyp · Kategorie(-baum) · **Collection** · gespeicherte Suche · **aktuelle Grid-Auswahl/Filter** |
| Felder | Basis-Felder (`id`, `type`, `status`, …) + Attributdefinitionen des Typs; Checkbox-Auswahltabelle mit Reihenfolge / „alle auswählen" |
| Format | CSV (UTF-8, Excel/Plain) und JSON (MEF) |
| Sprache | Locale (Fallback-Kette) für mehrsprachige Attribute |
| Optionen | Header-Zeile, Trennzeichen, quantifizierte Werte als `{value, unit}` in Anzeigeeinheit, `include_system` |

**Ausgabe:** Vorschau (Beispielzeilen) + Download (klein) oder Hintergrundjob + Download-Link/Report (groß).

**Ausbau:** XLSX, geplante Exporte (cron), E-Mail-Versand, Objektstorage-Ablage, UI-Import (CSV/MEF mit Mapping/Preview/Dry-Run).

## 4. Collection-Mitglieder (Use-Case)

- **Export:** `GET /collections/{id}/members` liefert die Mitglieder. Im Export-Wizard als Quelle „Collection" wählbar; zusätzlich ein **„Als CSV/JSON exportieren"-Button** auf der Collections-Verwaltungsseite (`/saved-searches`, Tab Collections) und im Grid (Collections-Ansicht).
- **Import / Mitglieder aufnehmen:** Bulk-Add von Objekten in eine Collection als `collection_member`-Relationen (`attributes.rank`, `added_at`). Quelle: Grid-Auswahl oder Import-Datei. **Idempotent** (bereits vorhandene Mitglieder überspringen), analog zum bestehenden `collection_member`-Handling.

## 5. Architektur & API

- **Geführter Export:** neuer Endpunkt `POST /exports` mit **strukturierten** Parametern (statt freiem JSON):
  ```json
  {
    "source": { "kind": "collection" | "type" | "category" | "saved_search" | "selection",
                "id": "…", "type": "product", "category": "…", "object_ids": ["…"] },
    "fields": ["sku", "title", "price", "status"],
    "format": "csv" | "json",
    "locale": "de",
    "options": { "header": true, "separator": ",", "include_system": false }
  }
  ```
  Antwort `202 { job_run_id }` (Jobtyp `outbound_export`) oder bei kleinen Mengen synchron `200` + Download.
- **Worker:** `meadow/worker/exports.py` erweitern — Auflösung der Quelle (Objekte über `/objects` mit `type`/`ancestor`/`collection`/`by-ids`, Collection-Mitglieder über `/collections/{id}/members`), Felder-/Spaltenauswahl, Locale-Auflösung, CSV- oder MEF-Serialisierung. Ergebnis in `job_run.result` (später Objektstorage + Download-Endpunkt `GET /exports/{run_id}/file`).
- **CSV-Serialisierung** als eigene Worker-Funktion (UTF-8, `;`/`,` Trenner, Header) bzw. im Kern für synchrone Fälle.
- **Import:** bestehendes `POST /imports` (MEF) wiederverwenden; **CSV-Import** als Worker-Adapter (CSV → MEF) mit Spalten-Mapping/Preview/Dry-Run; **Collection-Bulk-Add** über neuen Endpunkt `POST /collections/{id}/members` (oder Wiederverwendung `POST /references` mit `definition_id = rel_collection_member`).

## 6. Frontend (NiceGUI)

- Neue Seite **`/export`** (Wizard, `editor+`-Rolle):
  1. **Quelle** (Select: Typ / Kategorie-Baum / Collection / gespeicherte Suche / Grid-Auswahl; abhängig davon Detailselektor).
  2. **Felder** (Auswahltabelle mit Spalten-Reihenfolge, „alle auswählen", Vorschau der verfügbaren Attributdefinitionen).
  3. **Format + Sprache + Optionen**.
  4. **„Vorschau"** (Beispielzeilen) und **„Exportieren"** → Report (Anzahl, Download-Link).
- Im **Product-Grid** (`/products`): „Export-Auswahl / Export-Filter" Aktion.
- Auf der **Collections-Seite** (`/saved-searches`): „Exportieren"-Button + „Mitglieder hinzufügen"-Import-Dialog.
- i18n-Keys (`export.*`, `collection.import_members`) in en/de/fr.

## 7. Jobs & Scheduler

- Kleine Mengen synchron; größere als `outbound_export`-Job (strukturierte statt JSON-Parameter).
- **Geplante Exporte** (Ausbau): `job_definition` mit 6-Feld-Cron, Ergebnis-Ablage (Objektstorage) + Download-Link.

## 8. Datenmodell & Entscheidungen

| Entscheidung | Empfehlung |
|---|---|
| Connector `json_export` behalten? | Ja — technischer Pfad bleibt; UI-Export nutzt strukturierte Job-Parameter. |
| Dateiablage | MVP in `job_run.result` (JSON inline) bzw. Base64/Inline; große Dateien → Objektstorage (Ausbau). |
| CSV vs. XLSX | CSV (UTF-8) als MVP; XLSX optional (openpyxl). |
| Sprache | Standard-Locale (Fallback-Kette); optional mehrere Sprachen als Spalten (`title_de`, `title_en`). |
| Rechte | Export `editor+`; Import `editor+`; Collection-Verwaltung `admin`. |
| Import-Ziel | MEF wiederverwenden; CSV als Adapter; Collection-Bulk-Add separat. |

## 9. Implementierungsschritte

1. **Kern:** `POST /exports` (strukturierte Parameter) + CSV/JSON-Serialisierung + optionaler Download-Endpunkt; Validierung der Quelle/Felder/Format.
2. **Worker:** `exports.py` erweitern (Quell-Auflösung, Feld-/Spaltenauswahl, Locale, CSV/JSON); ggf. `imports.py` um CSV-Adapter; `collections`-Worker um Bulk-Member-Add.
3. **Frontend:** `/export`-Wizard + Grid-/Collections-Aktionen + `api.py`-Methoden (`create_export`, `get_export_file`, `bulk_add_collection_members`) + i18n.
4. **Tests:** Export Collection/Grid/Typ; CSV-Roundtrip; Feldauswahl/Locale/Format; Bulk-Add idempotent; Rechte.

## 10. Testfälle

- Export einer Collection liefert eine korrekte Mitgliederzahl, die gewählten Felder und die gewählte Sprache.
- CSV-Roundtrip (Export → Import) ist idempotent (Upsert per Business-Schlüssel).
- Große Menge → Hintergrundjob mit Download-Link/Report.
- Collection-Mitglieder-Bulk-Add überspringt bereits vorhandene Mitglieder.
- Nicht-`editor` erhält 403; unbekanntes Format/Quelle → 400.
- Quantifizierte Werte werden in der Anzeigeeinheit exportiert (kein SI-Wert).

## 11. Offene Punkte

- Objektstorage-Ablage für große Dateien vs. `job_run.result`.
- XLSX, geplante Exporte, E-Mail-Versand, mehrere Sprachen pro Spalte.
- Soll der Export im Grid als „Spalten = Sicht" (gespeicherte Ansicht) wiederverwendet werden? (Nice-to-have.)
- Mappings (CSV-Spalten → Attributnamen) beim Import konfigurierbar machen.

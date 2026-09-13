# BMEcat-Export & -Import / externe Klassifikationsstandards

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)); ergänzt Abschnitt 4 ([Datenmodell](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 9 (Ausbau), aufbauend auf dem MEF/MCF-Export/Import (Phase 6/9)
- **Stand:** Entwurf (Planung, **keine** Implementierung); Recherche **verifiziert** am 2026-09-12 gegen die amtlichen Artefakte (§11.1, `resources/etim/`); offen: BMEcat-1.2-DTD, vollständige IXF-Abschnitte, Syntax Binding zu EuroCat (EN 17015-2)
- **Verwandt:** [kategorisierung.md](kategorisierung.md) (Taxonomie-Typen, Klassen-Merkmale), [datenexport-import-ui.md](datenexport-import-ui.md) (Export-Wizard), [konfiguration-export-import.md](konfiguration-export-import.md) (MCF), [einheiten.md](einheiten.md)
- **Artefakte:** BMEcat-/ETIM-Schema, Guideline, Beispielkataloge und der ETIM-10.0-IXF-Export liegen lokal in `resources/etim/` (nicht versioniert, Lizenz — s. §11.1)

## 1. Ziel & Nicht-Ziele

**Ziel.** Meadow soll Produktdaten in **BMEcat** (XML) ausgeben und einlesen können — mit **Mapping bis auf die Produktattribute** und **Konvertierung** (Einheiten, Zahlen, Datum, Sprache, Codes) — und dabei Klassifikationsstandards wie **ETIM** korrekt bedienen (Klassen + Merkmale), weil im deutschsprachigen Raum genau für diesen Zweck BMEcat verwendet wird. Das ist die praktische Fortsetzung von Stufe A/B ([kategorisierung.md](kategorisierung.md)): Klassen und Pflicht-Merkmale existieren, jetzt müssen sie *ausgeleitet* werden.

**Nicht-Ziele (dieses Dokuments).** Kein Bestellprozess (das wäre openTRANS), kein vollständiger BMEcat-2005.1-Umfang (Preisstufen/Varianten/Konfigurationen sind Ausbaustufen), keine Ablösung des MEF/MCF-Formats.

## 2. Recherche (verifiziert)

**BMEcat (Quelle: de.wikipedia.org/wiki/BMEcat, abgerufen 2026-09-12)**

- Historie: **1.0 (1999)**, **1.2 (2001)**, **2005 (Nov 2005)**; 1.2 war Mitte 2005 die am weitesten verbreitete Version.
- In **Version 2005** wurde `ARTICLE` durch **`PRODUCT`** ersetzt; neu waren u. a. **Mehrsprachigkeit von Katalogen**, Multilieferantenkataloge, externe Kataloge (OCI/PunchOut/RoundTrip), konfigurierbare Produkte und logistische Produktinformationen.
- Ein Katalog hat **genau einen** `SUPPLIER`, **höchstens einen** `BUYER` — **Korrektur (s. §11.2):** ab BMEcat 2005 sind **mehrsprachige Kataloge in *einer* Datei** möglich (`lang`-Attribut); nur **BMEcat 1.2** verlangt ein Dokument je Sprache.
- **Klassifikationssysteme** werden als Element **vollständig** übertragen — „inklusive Merkmalsdefinitionen und Schlagwörtern".
- `CATALOG_GROUP_SYSTEM` ist der (aus 1.0 stammende) **Navigationsbaum** („Baumstrukturen … Navigation im Zielsystem") — das Gegenstück zu Meadows eigener Kategorie­taxonomie.
- Merkmale: `ARTICLE_FEATURE` (ab 2005 `PRODUCT_FEATURE`) transportiert **vordefinierte Gruppenmerkmale** (über ein Referenz-Klassifikationssystem) **und produktindividuelle Merkmale**.
- Weiter vorhanden: `PRODUCT_PRICE_DETAILS` (Staffel-/Länder-/Währungs-/Gültigkeitspreise), `MIME_INFO` (Bilder, Datenblätter, URLs), `PRODUCT_REFERENCE`, `USER_DEFINED_EXTENSION` (UDX) für Daten außerhalb des Standards, `AGREEMENT`.
- **Struktur-Beispiel (verifiziert aus dem Artikel, Namespace `http://www.bmecat.org/bmecat/2005fd`, `version="2005"`):** `HEADER` mit `CATALOG` (`LANGUAGE`, `CATALOG_ID`, `CATALOG_VERSION`, `CATALOG_NAME`, `DATETIME type="generation_date"` mit `DATE`/`TIME`/`TIMEZONE`, `CURRENCY`), `BUYER`/`SUPPLIER`; dann `T_NEW_CATALOG` mit `PRODUCT mode="new"` → `SUPPLIER_PID type="supplier_specific"`, `PRODUCT_DETAILS` (`DESCRIPTION_SHORT`, `DESCRIPTION_LONG`, `INTERNATIONAL_PID type="ean"`, `MANUFACTURER_NAME`), `PRODUCT_FEATURES` (dort im Beispiel `REFERENCE_FEATURE_SYSTEM_NAME` + `REFERENCE_FEATURE_GROUP_ID`, z. B. UNSPSC), `PRODUCT_ORDER_DETAILS` (`ORDER_UNIT`, `CONTENT_UNIT`), `PRODUCT_PRICE_DETAILS`/`PRODUCT_PRICE price_type="net_list"` (`PRICE_AMOUNT`, `PRICE_CURRENCY`, `LOWER_BOUND`), `MIME_INFO`/`MIME` (`MIME_TYPE`, `MIME_SOURCE`, `MIME_PURPOSE`, `MIME_ORDER`).

**ETIM (Quelle: de.wikipedia.org/wiki/ETIM, abgerufen 2026-09-12; frühere Recherche: etim-international.com, ketenstandaard.nl)**

- ETIM ist ein Standard für den elektronischen Austausch technischer Produktdaten. Produkte werden **Artikelklassen** zugeordnet — ausdrücklich **hierarchiefrei**; die übergeordneten Produktgruppen fassen Klassen nur **organisatorisch** zusammen (werden aber mitveröffentlicht).
- **Jeder Klasse werden technische Merkmale zugeordnet**, die das Produkt beschreiben — das ist Meadows `category_attribute` (Stufe B) in der Sprache des Standards.
- Die Übertragung erfolgt **per XML in einem erweiterten BMEcat-Format** („ETIM xChange").
- Versionen: … **9.0 (2022-12-05)**, **10.0 (2024-12-05)** (119 neue Klassen, neu: „ETIM Feature Grouping (EFG)"); es existieren **Mapping-Tabellen** zwischen ETIM-Versionen und zu ECLASS.
- Das Datenmodell ist **kostenfrei** nutzbar (keine Lizenzgebühren); Verteilung/Validierung läuft über ETIM-Tools (CMT/MMT, xChange-Validierung, API).

**Beschaffungsstand (aktualisiert 2026-09-12, Details in §11):** Die folgende Liste ist **überwiegend abgearbeitet** — die Originalbeobachtungen bleiben als Historie stehen, maßgeblich ist §11 (die Artefakte liegen in `resources/etim/`).

1. **Offizielle Schema-/Doku-Artefakte** zu BMEcat 2005.1 (XSD/DTD + Spezifikationsdokument). Die Quellen `bmecat.org` und `eclass.eu` waren über den Recherche-Client nicht abrufbar (JS/Login/404); der Beispielkatalog im Wikipedia-Artikel referenziert `http://www.eclass.eu/static/eClassXML/2.0/bmecat/bmecat_2005_tnc.dtd`.
2. **ETIM-xChange-Regelwerk** (welche BMEcat-Version, welche Pflichtelemente/-reihenfolgen, welche Validierungsregeln) — Bezug über ETIM International/Mitgliedsorganisation, teils Login.
3. **ETIM-Standarddaten** (Klassen + Merkmale + erlaubte Werte + Einheiten) für die Tests — über die ETIM-API (OAuth2, `client_id`/`client_secret`) oder ein CMT-Export.
4. **Einheiten-Mapping** ETIM/BMEcat (welche Einheitencodes sind zulässig) — aus dem ETIM-Datenmodell, nicht aus dem Bauch.

**Konsequenz für die Planung:** Der erste Umsetzungsschritt ist ein **Validierungs-Spike** (Kapitel 9), der genau diese Artefakte beschafft und ein Beispiel-XML gegen das Schema prüft.

## 3. Mapping-Modell (Meadow → BMEcat)

| Meadow | BMEcat 2005 | Anmerkung |
|---|---|---|
| Exportprofil (neu, s. Kap. 5) | `HEADER/CATALOG` (`CATALOG_ID`, `CATALOG_VERSION`, `CATALOG_NAME`, `LANGUAGE`, `CURRENCY`, `DATETIME`) | Ein Dokument = **eine Sprache** (Standard); mehrsprachige Auslieferung = mehrere Dateien |
| Kanal (`channel`) | Katalog-/Profilkontext | Ein Kanal (Webshop, Print, Marktplatz) bekommt ein eigenes Profil |
| Objekttyp `product` | `PRODUCT mode="new"` | `variant`-Objekte → `PRODUCT` bzw. später `VARIANT` |
| Business-Key (`key_attributes`, z. B. `sku`) | `SUPPLIER_PID type="supplier_specific"` (+ `INTERNATIONAL_PID type="ean"` aus einem EAN-Attribut) | stabile Schlüssel, s. MCF-Prinzip |
| `attributes.name` (mehrsprachig, aktueller Katalogsprache) | `PRODUCT_DETAILS/DESCRIPTION_SHORT` | Längen-/Zeichenregeln prüfen; Long-Text-Attribut → `DESCRIPTION_LONG` |
| Attribute + Werte | `PRODUCT_FEATURES/FEATURE` (Merkmalsname/-wert, Einheit, Reihenfolge) | Mapping je Profil: Attributname → BMEcat-Merkmal; nicht abbildbare Attribute → **UDX** oder bewusst auslassen |
| Klassen-Merkmale (`category_attribute`, Pflicht/optional) | `CLASSIFICATION_SYSTEM/CLASSIFICATION_GROUP_FEATURES` (Pflicht-/Optional-Kennzeichen je Gruppenmerkmal) | Die Klassen-Definition geht **einmal** ins Klassifikationssystem, nicht je Produkt |
| Taxonomie-Typ + Klasse (`object_type.taxonomy`, Klassenknoten) | `CLASSIFICATION_SYSTEM` (`CLASSIFICATION_GROUP` = Klasse, Hierarchie = Gruppenbaum) | ETIM-Klassen sind laut Standard hierarchiefrei; Vorfahren-Merkmale (unsere Vererbung) werden beim Export **eingesammelt** auf die Klasse geschrieben |
| Eigene Kategorie-Navigation (`category`-Taxonomie) | `CATALOG_GROUP_SYSTEM` | Navigationsbaum ≠ Klassifikation — genau die Trennung, die Stufe A/B vorbereitet hat |
| Einheiten (`unit`, kanonisch SI + `display_unit`) | `FUNIT`, `ORDER_UNIT`, `CONTENT_UNIT` | Konvertierung kanonisch → Anzeige-/Handelseinheit, dann Einheitencode |
| Objektstatus + `release` (kanalspezifische Freigabe) | Auswahlkriterium des Exports | „Exportiere nur, was für den Kanal `published` ist" — die Freigabetabelle ist der Filter, kein BMEcat-Feld |
| Medien (`asset`, URL-/Media-Attribute) | `MIME_INFO/MIME` (`MIME_TYPE`, `MIME_SOURCE`, `MIME_PURPOSE`, `MIME_ORDER`) | `MIME_PURPOSE`-Werte und `MIME_SOURCE`-Semantik (Dateiname vs. URL) noch schemaseitig zu verifizieren |
| Preise (Attribut(e)) | `PRODUCT_PRICE_DETAILS/PRODUCT_PRICE` (`price_type`, `PRICE_AMOUNT`, `PRICE_CURRENCY`, `LOWER_BOUND`) | Ausbaustufe (Staffelpreise brauchen eine eigene Struktur) |
| Freie Verknüpfungen (`related`) | `PRODUCT_REFERENCE` | Ausbaustufe |
| Standard-Metadaten (`code`, `version`, `source` der Klasse) | `CLASSIFICATION_SYSTEM_NAME/VERSION` (Elementnamen noch zu verifizieren) | Standard-Import/Versionierung ist eigener Schritt |

## 4. Datenkonvertierung (Regeln)

- **Zahlen:** Dezimalpunkt, keine Tausendertrennung; Beträge/Werte als Zeichenkette mit `.` (Prüfung: Meadow speichert numerisch, JSON gibt `.` aus — für BMEcat explizit formatieren).
- **Datum/Uhrzeit:** `DATE` = `YYYY-MM-DD`, `TIME` = `HH:MM:SS`, `TIMEZONE` = `±HH:MM` (UTC-Ausgabe wie in Meadow, Zeitzone explizit).
- **Einheiten:** Meadow speichert **kanonisch SI** je Dimension; für BMEcat wird in die **Anzeige-/Handelseinheit** (`display_unit_id`) umgerechnet (vorhandener `GET /units/convert`) und der Ziel-**Einheitencode** über eine Profil-Mapping-Tabelle gesetzt. Fehlt ein Mapping ⇒ **Report-Eintrag**, kein stiller Fallback.
- **Sprache:** ein Katalog je Sprache; je Attribut die Werte der Katalogsprache (Sprach-Fallback-Kette wie im Frontend, damit nichts leer bleibt — oder Lücke melden, konfigurierbar).
- **Enums:** Meadow speichert den `code`; BMEcat erwartet den Standardwert (bzw. bei ETIM den Merkmalswert). Mapping Profil: `code` → Zielwert; unbekannte Werte ⇒ Report.
- **Mehrwertige/mehrsprachige Felder**, leere Werte: nur nicht-leere Merkmale schreiben (konfigurierbar „Pflichtmerkmale immer, auch leer").
- **Pflicht-Merkmale:** aus `category_attribute.required` — fehlen sie am Produkt, ist das ein **Report-Fehler** (`missing_features`), kein Export-Abbruch (konfigurierbar „streng/„nachsichtig"). Dafür gibt es bereits `ctx.missing_features`-Semantik.
- **Zeichen/Encoding:** UTF-8, XML-Escaping, Längenbegrenzungen laut Schema prüfen.
- **UDX:** nicht-standardisierbare Attribute bleiben erhalten (`USER_DEFINED_EXTENSION`), damit nichts verloren geht.

## 5. Mapping-Profile als Konfiguration

Ein BMEcat-Export braucht eine **Konfiguration**, sonst ist das Mapping im Code vergraben. Vorschlag: neue Entität **`export_profile`** (Name, Format `bmecat_2005_1`/`etim_xchange`, Katalog-Metadaten `catalog_id`/`version`/`name`/`language`/`currency`, Kanal, Klassifikationssystem + Version, Feld-/Merkmal-Mappings `attribute` → Ziel (`feature_name`/`udx_key`/`mime`/`price`), Einheiten-Mapping, Optionen `strict`, `include_empty_required`, `fallback_language`) — pflegbar über eine Admin-Seite und **im MCF** als eigene Sektion (`export_profiles`), damit ein Umzug Staging → Prod die Profile mitnimmt (dieselbe Lücke wie bei `taxonomy`/`category_attribute`, vgl. `konfiguration-export-import.md`).

**Aktueller Stand der Lücke:** Profile existieren heute nicht; der MEF-Export (`exports.rs`) kennt nur Feldlisten je Format (CSV/XLSX/JSON), keine Zielstruktur-Mappings.

## 6. Import (BMEcat → Meadow)

- **Modi:** `T_NEW_CATALOG`, `T_UPDATE_PRODUCTS`, `T_DELETE_PRODUCTS` (Umfang/Regeln noch zu verifizieren) — V1 nur `T_NEW_CATALOG`/`T_UPDATE_PRODUCTS`.
- **Matching:** `SUPPLIER_PID` (+ `INTERNATIONAL_PID`) → Meadow-Objekt über `key_attributes`/`source_id`; kein Treffer ⇒ optional anlegen (Profil-Flag) oder Report-Eintrag.
- **Nichts wird gelöscht.** Import ergänzt/aktualisiert; Deaktivierung nur, wenn explizit gefordert.
- **Merkmale → Attribute:** Merkmal-Name → Attributdefinition (Profil-Mapping, inkl. Konvertierung rückwärts: Einheiten, Datum, Zahlen). Unbekannte Attribute ⇒ Report (und optional UDX-Ablage als Roh-JSON).
- **Klassifikation:** `CLASSIFICATION_SYSTEM`-Teil kann Klassen + Pflicht-Merkmale anlegen/aktualisieren → das ist der **Standard-Import**, der Stufe B produktiv nutzbar macht (und die offene Folgearbeit aus `kategorisierung.md` schließt).
- **Report:** wie bei MEF/MCF — je Zeile `created`/`updated`/`skipped`/`error` am `job_run`, Trockenlauf zuerst.

## 7. Architektur / Verortung in Meadow

- **Worker (Python)** als Jobtyp `bmecat_export`/`bmecat_import`: XML ist text-/streaming-lastig; die Worker-Schicht hat bereits Job-, Berichts- und Fehlerbehandlung (`meadow/worker/*`). Der **Kern** bleibt DB-Autorität und liefert die Daten (Objektlisten + Attribute + Klassen + Profile) über bestehende Endpunkte; die **Datei** läuft über den bestehenden Object-Storage-/Export-Pfad (`GET /exports/{run}/file`).
- **Validierung:** XSD-Validierung (`lxml`) gegen das beschaffte Schema **im selben Job** (Ergebnis im Report), zusätzlich Pflichtfeld-/Einheitenprüfung; ETIM-xChange-Prüfung, sobald das Regelwerk vorliegt.
- **Profil-Konfiguration im Kern** (Tabelle + CRUD + MCF-Sektion), damit Frontend, Worker und MCF dieselbe Quelle haben.
- **Größe:** BMEcat-Dateien können groß werden (100k Produkte) ⇒ **Streaming** schreiben (kein DOM im Speicher), Fortschritt/Bericht je Produkt-Batch.

## 8. Phasen

1. **Spike (Pflicht zuerst):** Schema/Regelwerk beschaffen, Beispielkatalog validieren, ETIM-Ausschnitt (10–20 Klassen) mit Merkmalen in Meadow modellieren. Ergebnis: belastbare Feldliste + Testdaten.
2. **V1 Export** (BMEcat 2005.1, Profil, Merkmale, Klassifikationssystem, MIME, Einheiten, Sprache, XSD-Validierung).
3. **V2 Import** (`T_NEW_CATALOG`/`T_UPDATE_PRODUCTS`, Merkmale rückwärts, Standard-/Klassen-Import).
4. **V3 Profile im MCF + Admin-UI**, mehrere Ausgabekanäle/Sprachen.
5. **V4** Preise, Varianten/Konfigurationen, `PRODUCT_REFERENCE`, weitere Standards (eCl@ss, UNSPSC) über dasselbe Profilmodell.

## 9. Tests & Abnahme

- **Golden-Files:** kleine Fixtures in `tests/` (Produkt + Klasse + Merkmale + MIME + Preis) → erwartetes XML (deterministisch, sortierte Ausgabe).
- **Schema-Validierung im Gate:** XSD (sobald beschafft) gegen die Fixtures; solange kein Schema vorliegt, wenigstens Struktur-/Pflichtfeldtests.
- **Rundlauf:** Export → Import in eine leere Umgebung → identische Attributwerte (Feld-für-Feld-Vergleich, Toleranzen dokumentiert).
- **Negativfälle:** fehlendes Pflicht-Merkmal, unbekannte Einheit, unbekannter Enum-Wert, fehlende Katalogsprache ⇒ Report-Einträge, kein Abbruch.

## 10. Offene Entscheidungen (vor V1 zu klären)

1. **Zielversion:** BMEcat **2005.1** (aktuell, `PRODUCT`) oder zusätzlich **1.2** (`ARTICLE`)? Vorschlag: 2005.1 zuerst, 1.2 nur bei konkretem Abnehmer.
2. **ETIM xChange als primäres Ziel** (mit ETIM-Verweis-Merkmalen) oder „generisches BMEcat" mit ETIM als einem Klassifikationssystem? Vorschlag: ETIM-xChange-konform als V1-Ziel, generisch als Profiloption.
3. **Sprachstrategie:** eine Datei je Sprache (Standard-konform) oder mehrsprachige UDX-Lösung? Vorschlag: Datei je Sprache.
4. **Preise/Varianten** in V1 oder erst V4? Vorschlag: erst V4.
5. **Wo liegt das Profil-UI** (`/export`-Wizard vs. eigene Admin-Seite `/export-profiles`)? Vorschlag: eigene Admin-Seite + Wizard wählt das Profil.
6. **ETIM-Zugang:** liegt ein `client_id`/`client_secret` für die ETIM-API bzw. ein CMT-Export vor? Ohne Datenquelle bleibt der Spike bei einem manuell erfassten Ausschnitt.

## 11. Verifizierte Grundlagen & Korrekturen (Recherche-Nachtrag 2026-09-12)

Dieser Abschnitt ist die **maßgebliche Grundlage** für die Umsetzung und korrigiert §2–§6 dort, wo die erste Recherche nur Wikipedia kannte. Quellen sind die **amtlichen Artefakte** (lokal) plus die **Herstellerseiten** (BME, ETIM) — nichts davon ist geraten.

### 11.1 Vorliegende Artefakte (nicht versioniert)

| Datei | Was sie belegt |
|---|---|
| `bmecat_etim_501.xsd` | **ETIM-BMEcat-Schema**, Dateiversion **5.0.1** (2021-08-24; Marc Habets, Karl Pappas, ETIM International), `targetNamespace="https://www.etim-international.com/bmecat/50"`, Root `BMECAT` mit **`version` (enum: nur `2005`)** und genau einer Transaktion: `T_NEW_CATALOG` \| `T_UPDATE_PRODUCTS` \| `T_UPDATE_PRICES` \| `T_NEW_PRODUCTDATA`. |
| `ETIM BMEcat Guideline V5-0.pdf` | **Guideline for suppliers to provide product data according to BMEcat Version 2005**, Version 5.0, Stand **2024-12-12**, 195 Seiten; markiert BMEcat-optionale Felder als **+Mandatory** und enthält die Kanonik (ETIM-Regeln, Länderabschnitte, Einheitenlisten). Textauszug: `_guideline_text.txt` (erzeugt mit `uv run --with pypdf`). |
| `ETIM BMEcat Guideline V5-0 - Overview elements.xlsx` | Element-/Pflichtfeld-Matrix der Guideline. |
| `SampleFile_bmecat_etim_V5_T_NEW_CATALOG_ETIM{7,8,9,10}.xml` | amtliche Beispielkataloge (u. a. für ETIM-10.0), verwendbar als Golden-File-Vorlage. |
| `ETIM-10.0-ALL-SECTORS-IXF-NO-CHANGE-CODES-METRIC-BE.xml` | **ETIM-Standarddaten 10.0** (Release 2024-12-05) im **IXF** (`schemaVersion="3.1"`, Namespace `.../etimixf/31`), Export 2025-08-05, 70 MB; Kopf mit `ExportDate`/`Publication`, danach u. a. `Units` → `Unit/Code` (z. B. `EU000002`) + `Translations/Translation[@language]` (`EN`, `fr-BE`, `nl-BE`, …) mit `Description`/`Abbreviation`. **Lizenzhinweis im Kopf:** `etim-international.com/classification/license-info`. |
| `resources/bmecat/` | Der **BMEcat-Standard** selbst: `bmecat_base_1_2.dtd` + `bmecat_new_catalog_1_2.{dtd,xsd}` + `bmecat_update_products_1_2.{dtd,xsd}` + `bmecat_update_prices_1_2.{dtd,xsd}` (1.2 Rev. A, 2003-05-09, © Fraunhofer IAO/Univ. Essen BLI) sowie `bmecat_2005.xsd`, `bmecat_2005_t{nc,upro,upri}.dtd`, `bmecat_2005-2.xsd` (© BME e.V.) und die 2005.2-Beschreibungen (de/en). → Struktur verifiziert in §11.8. |

**Lizenz-/Ablage-Hinweis (entschieden):** BMEcat-Schemata sind © BME e.V. („Rechtlicher Hinweis“), die ETIM-Daten stehen unter der ETIM-Lizenz. Deshalb ist **`resources/*` gitignored** (Ausnahme: `resources/AGENTS.md`) und dient nur lokal für Spike/Tests — **keine** Kopien, Auszüge oder Test-Fixtures im Repo, und Gates dürfen sich nicht auf diese Dateien verlassen (nur optionale, skip-fähige Tests).

### 11.2 Versionen & EuroCat (verifiziert: bme.de/services/bmecat[/info], BME-News 14.04.2026, nexoma 03.06.2026)

- **Aktuell ist BMEcat 2005.2** (bme.de: „Aktuell ist die Version 2005.2“; Schema 2022-09-10, deutsches/englisches Beschreibungs-PDF und XSD frei abrufbar). Vorgänger: **2005** (freigegeben 04.11.2005), **2005.1 „advanced“** (objektorientiertes Datenmodell gemeinsam mit ECLASS 7.1), **1.2** (ab CeBIT 2001 der De-facto-Standard).
- **2005.2-Inhalte:** Merkmalsnamen bis **80 Zeichen**; Merkmalswerte dürfen **Wertausprägung und Referenz auf eine Ausprägung in beliebiger Kombination** verwenden; Wertlänge **unbegrenzt**; MIME-Anlagen nicht mehr auf wenige Typen beschränkt (jeder offizielle MIME-Typ). Abwärtskompatibel zu 2005/2005.1.
- **1.2 ist nicht tot:** BMEcat 2005 ist **voll abwärtskompatibel zu 1.2** — 1.2-konforme Dokumente sind konform zu 2005; im 2005.2-Schema existieren die `ARTICLE_*`-Elemente deshalb weiter. 1.2-Unterschiede: **`ARTICLE` statt `PRODUCT`**, **ein Dokument je Sprache**, eindeutige Artikelnummer über Lieferanten, Varianten nur merkmalsbasiert; validiert wird per **DTD** (Beschreibung: `bmecatv12e.pdf`, u. a. unter `eclass.eu/fileadmin/static/documents/wiki/BMEcat/BMEcat_1.2/`; DTD noch **nicht** in `resources/etim/` → beschaffen).
- **EuroCat** ist die **erste europäische Normenserie** für digitale Produktkataloge: **EN 17015-2**, Veröffentlichung **2026**, entstanden im Umfeld **CEN/TC 440 „Electronic Public Procurement“** (BII/Business Interoperability Interfaces), entwickelt von den BMEcat-Autoren (Dorloff, Jahns) mit internationalen Experten; Basis sind **BMEcat-Strukturen und -inhalte**. **BMEcat bleibt kompatibel** — der BME erstellt ein **„Syntax Binding“**, das beschreibt, wie BMEcat-Daten EN-17015-2-konform verwendet werden. Neue Inhalte: **Pre-Award-Kataloge** (Ausschreibungsphase), Verknüpfung vor-/nachgelagerter Nachrichten (Bestellungen), Anforderungen an **nachhaltige Beschaffung**. Präsentiert am 19.05.2026 (17. BME-eLÖSUNGSTAGE).
- **Konsequenz für Meadow:** EuroCat erfordert **keinen** Neuaufbau. Der BMEcat-Exportpfad (insb. das ETIM-Profil) *ist* die Vorbereitung; ein EuroCat-Profil ergibt sich später aus dem Syntax Binding (gleiche Mapping-Engine, anderes Zielprofil). EuroCat wird als **Beobachtungsposten** in §11.6 geführt, nicht als Phase 2.

### 11.3 ETIM BMEcat 5.0 („ETIM xChange“) — Struktur und Regeln

**Kopf:** `HEADER` → `GENERATOR_INFO?`, `CATALOG`, `BUYER`, `SUPPLIER`, `USER_DEFINED_EXTENSIONS` (darin **+Mandatory** `UDX.EDXF.VERSION`, z. B. `5.0`). `CATALOG`: `LANGUAGE` (1..n, eines mit `default="true"`) → **mehrsprachig in einer Datei**; `CATALOG_ID` (≤20), `CATALOG_VERSION` (`[0-9]{1,3}\.[0-9]{1,3}`), `CATALOG_NAME` (≤100, mehrsprachig), `DATETIME type="generation_date"`, `TERRITORY` (ISO), `CURRENCY` (ISO), **`MIME_ROOT`** (Basis-URL für Medien).

**Transaktionen (statt `SUPPLIER_PID` → `ARTICLE_THIRD_PARTY`-Stil von 1.2):**

| Transaktion | Inhalt / Pflicht |
|---|---|
| `T_NEW_CATALOG` | `PRODUCT mode="new"`: `SUPPLIER_PID`, `PRODUCT_DETAILS`, `PRODUCT_FEATURES` (**1..n**), `PRODUCT_ORDER_DETAILS`, `PRODUCT_PRICE_DETAILS` (**1..n**), **`USER_DEFINED_EXTENSIONS` (Pflicht)**, optional `PRODUCT_REFERENCE`, `PRODUCT_LOGISTIC_DETAILS`. |
| `T_UPDATE_PRODUCTS` | wie oben, aber `mode` (**Pflicht**: `delete`/`new`/`update`) und `prev_version` am Element. |
| `T_UPDATE_PRICES` | nur `SUPPLIER_PID` + `PRODUCT_PRICE_DETAILS` (+ UDX), `prev_version` **Pflicht**. |
| `T_NEW_PRODUCTDATA` | **ohne Preise/Order-Details**: `SUPPLIER_PID`, `PRODUCT_DETAILS`, `PRODUCT_FEATURES` (0..n), UDX (`udxPRODUCTDATA`), `PRODUCT_REFERENCE` → **der Weg für ein PIM ohne Preise**. |

**Produkt:** `PRODUCT_DETAILS` (u. a. `DESCRIPTION_SHORT` 1..n, `DESCRIPTION_LONG`, `INTERNATIONAL_PID` *gtin|upc*, `SUPPLIER_ALT_PID`, `BUYER_PID`, `MANUFACTURER_PID/-NAME`, `MANUFACTURER_TYPE_DESCR`, `DELIVERY_TIME`, `SPECIAL_TREATMENT_CLASS`, `KEYWORD`, `REMARKS`, `PRODUCT_STATUS` *bargain|core_product|new|new_product|old_product|refurbished|used|others*, `PRODUCT_TYPE` *contract|license|physical|service*); `PRODUCT_ORDER_DETAILS` (`ORDER_UNIT`, `CONTENT_UNIT` **Pflicht**, optional `NO_CU_PER_OU`, `PRICE_QUANTITY`, `QUANTITY_MIN`, `QUANTITY_INTERVAL`); `PRODUCT_PRICE_DETAILS` (`DATETIME type=valid_start_date|valid_end_date`, `DAILY_PRICE`, `PRODUCT_PRICE` mit **`price_type` = `net_customer|net_list|nrp`**, `PRICE_AMOUNT`, `PRICE_CURRENCY`, `TAX`, `PRICE_FACTOR`, `LOWER_BOUND`, `TERRITORY`; `PRICE_BASE` mit `PRICE_UNIT`, `PRICE_UNIT_FACTOR`); `PRODUCT_REFERENCE` (`PROD_ID_TO` + `type` = `accessories|base_product|consists_of|followup|mandatory|similar|select|sparepart|others`, Attribut `quantity`).

**Klassifikation & Merkmale (Kern des ETIM-Profils):**

- `PRODUCT_FEATURES` (Blöcke, 1..n) = `REFERENCE_FEATURE_SYSTEM_NAME` + `REFERENCE_FEATURE_GROUP_ID` + `FEATURE` (0..n). **Kein** `CLASSIFICATION_SYSTEM`-Element: ETIM wird **per Code** referenziert (`"ETIM-10.0"` bzw. `"DYNAMIC"`; Klasse `EC001234`; `EC000000` nur als Notbehelf). Weitere Klassifikationen (ECLASS, UNSPSC, eigene) laufen als **weitere Blöcke** parallel.
- `FEATURE`: `FNAME` (**ETIM-Merkmalscode** `EF001234`, mehrsprachig), `FVALUE` (1..n: Zahl | `true`/`false` | **Wertcode `EV001234`**; **Bereich = zwei `FVALUE`**), `FUNIT` (bei ETIM **nicht nötig/erwünscht** — die Einheit steckt in der Klassifikation), `FVALUE_DETAILS` (0..n): (a) herstellerspezifische Ergänzung, (b) **Grund für `-`**: `NA` (nicht anwendbar), `MV` (fehlender Wert), `UN` (unbekannt).
- **Harte ETIM-Regel:** Ist ein Produkt einer ETIM-Klasse zugeordnet, sind **alle** Merkmale der Klasse zu übertragen (Ausnahme: nicht relevante **lokale Standard-Merkmale**, Codes wie `EFUK…`, sowie weitere optionale lokale Merkmale). „Wert kann nicht angegeben werden“ ⇒ **`-`** (bei Bereichsmerkmalen genau **einmal**).
- Guideline-Markierungen: etliche BMEcat-optionale Felder sind **+Mandatory**; einzelne Anforderungen sind **länderspezifisch** (Kapitel 10/11 der Guideline: `UDX.EDXF.PRODUCT_CHARACTERISTICS`).

**Medien:** im ETIM-Profil `MIME_INFO/MIME` = `MIME_SOURCE` (1..n) + `MIME_DESCR` (0..n) + `MIME_ALT` (0..n) — **ohne** `MIME_TYPE`/`MIME_PURPOSE`; Quellen sind relativ zu `MIME_ROOT`. Zusätzliche/strukturierte Medieninfos über `UDX.EDXF.MIME_INFO/UDX.EDXF.MIME` (`MIME_FILENAME` 1..n, `MIME_ISSUE_DATE`, `MIME_EXPIRY_DATE`, `MIME_ORDER`, `MIME_DESIGNATION`, `MIME_SOURCE`, `MIME_ALT`, `MIME_CODE`).

**Erweiterungsblock `UDX.EDXF.*`** (EDXF = ETIM Data Exchange Format, `udxPRODUCT`): u. a. `BRAND_NAME`, `DESCRIPTION_VERY_SHORT`, `TENDER_TEXT`, `MANUFACTURER_ACRONYM`, `PRODUCT_SERIES`, `PRODUCT_VARIATION`, `PREDECESSOR_PID`, `PACKING_UNITS` (Verpackungseinheiten mit Maßen/Gewicht/Volumen), `PRODUCT_LOGISTIC_DETAILS` (Netto-Maße), **`PRODUCT_ETIM_RELEASE_DATE`/`PRODUCT_ETIM_DYNAMIC`**, `PRODUCT_FEATURES_MC`, `PRODUCT_CHARACTERISTICS` (länderspezifisch), `ROHS_INDICATOR`, `REACH`, `SCIP_NUMBER`, `CE_MARKING`, `BATTERY_CONTAINED`, `UN_NUMBER`/Gefahrgutfelder, `WARRANTY`, `SURCHARGE`. → **Hier gehören Meadows Zusatzdaten hin**, gezielt und nicht flächig.

**Einheiten sind enumeriert** (kein Freitext): `dtUNIT` = `ANN, BE, BG, BO, BX, C62, CA, CL, CMK, CMQ, CMT, CQ, CR, CS, CT, D99, DAY, DR, GRM, HUR, KG, KGM, KTM, LTR, MGM, MLT, MMK, MMT, MMQ, MTR, MTK, MTQ, PA, PF, PK, PL, PR, PU, RG, RL, RO, SA, SEC, SET, ST, TN, TNE, TU, WEE, Z2, Z3`; Verpackung: `dtPUNIT` = `BE, BG, BO, BX, C62, CA, CL, CQ, CR, CS, CT, CY, D99, DR, EV, KG, NE, PA, PF, PK, PL, PR, PU, RG, RL, RO, SA, SET, TN, TU, WR, Z2, Z3`. (Meadows Einheiten-Mapping wird **gegen diese Listen** validiert — bestätigt §4.)

**Texte:** `dtMLSTRING` = `xsd:string` + **`lang`**-Attribut (`dtLANG`) — Mehrsprachigkeit gilt **je Textknoten** (Kurz-/Langtext, Keywords, Remarks, `FNAME`, `FVALUE`, `FVALUE_DETAILS` …). Pflichtfeld-Längen sind z. B. `DESCRIPTION_SHORT`/`CATALOG_NAME` ≤100, `REMARKS` ≤64000, `REMARKS`-ähnlich; `dtNUMBER` = `xsd:decimal`.

### 11.4 Standarddaten-Import (IXF) — verifiziert

Die Sektionen des Exports wurden mit `scripts/inspect_etim_ixf.py` (Streaming, `iterparse`) vollständig erfasst; Struktur (Namespace `…/etimixf/31`, `schemaVersion 3.1`):

| Sektion | Umfang | Felder je Eintrag |
|---|---|---|
| `Header` | 2 × `Publication` | `Name`, `Number` (= 10), `Date` (2024-12-05), `Prerelease` (false) |
| `Units` | **188** × `Unit` | `Code` (`EU…`), `Translations` |
| `FeatureGroups` | **18** × `FeatureGroup` | `Code` (`EFG…`), `Translations` (ETIM Feature Grouping) |
| `Features` | **17377** × `Feature` | `Code` (`EF…`), `Type`, `FeatureGroupCode`, `Translations` |
| `Values` | **16163** × `Value` | `Code` (`EV…`), `Translations` |
| `Groups` | **159** × `Group` | `Code` (`EG…`), `Translations` |
| `Classes` | **5640** × `Class` | `Code` (`EC…`), `Version`, **`Status`** (immer `Published`), `GroupCode` (`EG…`), `Translations` |

**Verschachtelung (verifiziert):** `Translations → Translation[@language] → Description` (+ `Synonyms/Synonym` bei Klassen, Merkmalen, Werten); `Classes → Class → Features → Feature → {FeatureCode, UnitCode, OrderNumber}` (**76625** Klassen-Merkmal-Zuordnungen) — d. h. eine Klasse trägt ihre Merkmalsliste **mit Position (`OrderNumber`) und Default-Einheit (`UnitCode`)**. Einträge tragen zudem `changeCode` (in diesem Export durchgängig `Unchanged`, Variante „NO-CHANGE-CODES“).

**Sprachen:** der Export enthält **genau** `EN`, `fr-BE`, `nl-BE` — vollständig (jede Sektion ×3: 52131 = 17377 × 3; 16920 = 5640 × 3; 48489 = 16163 × 3 …). Ein Import kann also nur diese drei Sprachen liefern; weitere Sprachen kommen aus einem anderen Export (oder der ETIM-API).

**Merkmalstypen (ETIM-Datenmodell A/N/L/R, verifiziert über alle 17377 Merkmale):** `Logical` **7311**, `Numeric` **5707**, `Alphanumeric` **3428**, `Range` **931**.

**Mapping auf Meadow (Stufe A/B):**

| IXF | Meadow |
|---|---|
| `Group` (159) | Produktgruppe — organisatorisch, nicht Shop-Navigation (ETIM ist hierarchiefrei); als eigener Taxonomie-Typ oder als Attribut am Klassenknoten |
| `Class` (5640) | Taxonomie-Knoten mit Key-Attribut `code` = `EC…` (`Translations.Description` → mehrsprachiges Label, `Synonyms` → Suchbegriffe, `Status` → Freigabe) |
| `Class → Features → Feature` | `category_attribute`-Zeile je Klasse: Merkmal (Attributdefinition), `position` = `OrderNumber`, Default-Einheit = `UnitCode` → **1:1 unser Stufe-B-Modell** |
| `Feature` (Typ `Numeric`/`Logical`/`Alphanumeric`) | `attribute_definition` mit `value_type` `number` (Einheit!), `boolean`, `text`/`enum` |
| `Unit` (188) | `unit` (ETIM-Einheitencode als stabiler Schlüssel + Übersetzungen) |
| `FeatureGroup` (18) | ETIM Feature Grouping → `attribute_group` |

**Offene Punkte aus dem IXF (nicht geraten):**

1. **Werte ↔ Merkmal:** die Sektion `Values` (16163 `EV…`) hat nur `Code` + `Translations` — **keine** sichtbare Zuordnung zum Merkmal. Wie erlaubte Werte an ein Merkmal gebunden werden, ist im Spike zu klären (andere IXF-Variante, ETIM-API/CMT).
2. **Pflicht-Kennzeichen — entschieden (2026-09-12):** `Class → Feature` liefert **kein** `mandatory`-Flag; die ETIM-Regel („alle Merkmale der Klasse“) gilt aber für den Export. Der Standard-Import setzt deshalb für **alle** Klassenmerkmale `required=true` (Default, im Import konfigurierbar) — so zeigt die Vollständigkeit sofort die Lücken und `ctx.missing_features` (Stufe B) greift ohne Zusatzregel.
3. **`Range`-Merkmale (931) — entschieden und umgesetzt:** eigener Datentyp **`range`** (`{"from": <Zahl>, "to": <Zahl>}`, Grenzen geordnet; Konvertierung/Matrix in `attribute_definition_types.rs`, UI-Helfer in `meadow/frontend/range_values.py`, Formular mit zwei Grenzen, Grid `from – to` — s. [datenmodell.md](datenmodell.md#range-attribute-range)). Der Export schreibt je Bereichsmerkmal **zwei `FVALUE`** (bzw. bei nicht angebbarem Wert genau ein `-`).
4. **`Version`/`Status`** je Klasse sind Standard-Metadaten (Read-only-Kennzeichnung nötig, s. `kategorisierung.md` „Offen“).

**Befund aus dem Spike (Kern-Asymmetrie, behoben 2026-09-13):** Eine Attributdefinition mit `"label": null` wurde beim **Import** mit **500** abgewiesen (`Couldn't coerce value for field 'label' … Expected object but found NULL`), obwohl der **Export** `null` schreibt, wenn eine Definition kein Label hat. Behoben in `crates/pim-core/src/api/config.rs`: `normalise_entry_maps` wandelt `null` in den Map-Feldern `label`/`labels` einmalig vor der Sektions-Verarbeitung in `{}` um — die Sektionen müssen dafür nicht einzeln angefasst werden. Live verifiziert: dasselbe Dokument importiert jetzt mit **200** und `errors: []`; Kern-Unit-Test `null_label_maps_are_normalised` (114 Kerntests).

### 11.5 Korrekturen an §3–§6

1. **Sprache (§3):** nicht „ein Dokument je Sprache“, sondern **ein mehrsprachiges Dokument** (`LANGUAGE default` + `lang`); die Profil-Option „Datei je Sprache“ bleibt als Alternative für strenge Abnehmer.
2. **Format-Schichtung (neu):** die Zielformate sind **Profile über einer gemeinsamen Mapping-Engine**: `etim_bmecat_5.0` (XSD 5.0.1, oben), `bmecat_2005.2` (generisch, `CLASSIFICATION_SYSTEM`/ECLASS, `MIME_TYPE`/`MIME_PURPOSE`, `VARIANTS`), `bmecat_1.2` (`ARTICLE*`, DTD-validiert) — *eine* Datenquelle, drei Serialisierer/Validatoren.
3. **Preise (§6) — entschieden:** es wird **kein Preis erfunden** (kein `0`). Im **ETIM-Profil** ist `T_NEW_PRODUCTDATA` der preislose Pfad (die Transaktion existiert **nur dort** — im generischen 2005.2-Schema und in 1.2 nicht). Im **1.2-/2005-Pfad** ist der Preis Pflicht (`ARTICLE_PRICE_DETAILS+` bzw. `PRODUCT_PRICE_DETAILS` 1..n) ⇒ preislose Produkte werden **ausgelassen und im Report geführt** (Profilregel `skip_products_without_price`, Default an).
4. **Merkmale (§4):** ETIM erwartet **Merkmalcodes** (`EF…`/`EV…`) statt Namen — deshalb gehört das Standard-Mapping in die **Export-Profile**, nicht in die Attributdefinitionen (dieselbe `attribute` kann in ETIM und ECLASS verschiedene Codes haben); optional als Default am Attribut.
5. **Pflicht-Merkmale (§4):** die ETIM-Regel („alle Merkmale der Klasse“) **ist** unsere Stufe-B-Semantik (`category_attribute.required` + `ctx.missing_features`) → Export-Report und ETIM-Validierung teilen dieselbe Quelle.
6. **MIME (§3):** im ETIM-Profil keine `MIME_TYPE`/`MIME_PURPOSE` (nur `MIME_SOURCE`/`MIME_DESCR`/`MIME_ALT` + `MIME_ROOT`); strukturierte Metadaten über `UDX.EDXF.MIME*`.
7. **Klassifikationsdaten:** ETIM-Systemdefinitionen werden **nicht** mitgeliefert (nur Codes) → ein „`CLASSIFICATION_SYSTEM`-Export“ ist erst für die **generische/ECLASS-Variante** nötig.

### 11.6 Angepasste Phasen (ergänzt §9)

1. **Spike – Schritt 1 (IXF → Modell) erledigt (2026-09-12):** `scripts/etim_ixf_to_mef.py` liest den IXF-Export und schreibt drei importierbare Dokumente: `model.mcf.json` (Taxonomie-Knotentyp + eine Attributdefinition je Merkmal: Name = stabiler Code `EF…`, Label = IXF-Übersetzungen, Typ aus `Feature.Type`), `classes.mef.json` (die Klassenknoten, Key `code` = `EC…`) und `features.mcf.json` (die `category_attributes`-Zeilen mit `required: true` und Position aus `OrderNumber`). **Importreihenfolge:** Modell → Klassen → Merkmale (die Merkmals-Sektion löst Klassen über den Business-Key auf).
   **Verifiziert mit 10 Klassen** (163 Merkmale: 70 Numeric, 42 Logical, 39 Alphanumeric, 12 Range): Modell-Import `errors: []` (Knotentyp + 165 Definitionen angelegt), 10/10 Klassen angelegt, Merkmals-Import `errors: []` (10 Klassen `updated`), `GET /categories/{id}/attributes` liefert je Klasse die Zeilen mit `required: true`, und ein in die Klasse kategorisiertes Produkt führt sie sofort in `required`/`missing`, in `GET /objects/{id}?locale=` als `missing_required` und in der **Vollständigkeit** — Stufe A/B und der Standard-Import greifen also ineinander.
   **Offen in Schritt 1:** die Wertebindung (Punkt 1 oben), die XSD-Validierung eines Beispielkatalogs (Schritt 2) sowie 1.2 gegen XSD/DTD (beide Varianten liegen vor, §11.8). **Einheiten — entschieden und angewandt:** der Seed bringt **67 Einheiten** mit, darunter **alle 10** für den *10-Klassen*-Ausschnitt benötigten Symbole (`W`, `°C`, `V`, `mm²`, `kA`, `kV`, `kW`, `mm`, `A`, `kN`); der Konverter schreibt die ETIM-Abkürzung als Symbol, `--with-units --units-file <GET /units>` setzt sie nur, wenn Meadow die Einheit wirklich hat — Ergebnis: **57 der 183 Attributdefinitionen** tragen eine Einheit (z. B. `EF000008 number mm Width`). Achtung: Einheiten-Konflikte sind **selektionsabhängig** (§11.9 Befund 1), der 25-Klassen-Ausschnitt hat einen.
2. **Spike – Schritt 2 (Export + Schema-Validierung) erledigt (2026-09-13):** `scripts/etim_export_spike.py` liest ein Produkt samt aufgelösten Klassenmerkmalen und Definitionen aus dem Kern und schreibt ein **ETIM-BMEcat-Dokument** (`version="2005"`, Namespace `…/bmecat/50`, Transaktion **`T_NEW_PRODUCTDATA`** = der preislose Pfad), das gegen `resources/etim/bmecat_etim_501.xsd` validiert wird (`uv run --with lxml …`). Ergebnis: **XSD-valid** mit 16 Klassenmerkmalen (3 mit Wert, 13 als `-` + `FVALUE_DETAILS`, Grund `UN`). Umgesetzt sind die ETIM-Regeln: **alle** Merkmale der Klasse werden übertragen, fehlende Werte als `-` mit `NA`/`MV`/`UN`, ein **Bereich als zwei `FVALUE`**, **kein `FUNIT`** (Einheit steckt in der Klassifikation) und Mehrsprachigkeit nur in der Katalogsprache (`--language`, Werte in anderer Sprache gelten als fehlend statt falsch exportiert zu werden).
3. **V1 – Produktions-Export** (offen): `T_NEW_CATALOG` mit Preisen *oder* `T_NEW_PRODUCTDATA`, XSD-Validierung im Job, Streaming für große Kataloge, Kanal-/Sprachauswahl, `MIME_ROOT`/Medien und `UDX.EDXF.*`-Zusatzfelder (Marke, Verpackungseinheiten, Logistikmaße).

**Drei Schema-Befunde aus dem Spike (die Validierung hat sie gefunden, nicht die Recherche):**

1. **`BUYER` ist im ETIM-Profil Pflicht** (und steht vor `SUPPLIER`) — ein Katalog ohne Käuferblock ist schema-invalid.
2. **`SUPPLIER_PID` ist auf 32 Zeichen begrenzt** ⇒ der interne Record-Key (`object:…`, 33 Zeichen mit ULID) ist **kein** gültiger PID; der Export braucht ein **Business-Key-Attribut** (SKU/Code) und muss ohne dieses Attribut mit klarer Meldung abbrechen.
3. **`USER_DEFINED_EXTENSIONS` im Produktblock ist `udxPRODUCTDATA`** — `UDX.EDXF.VERSION` ist dort **nicht** erlaubt (nur im `udxHEADER`); Produkt-UDX darf nur die dort definierten Felder tragen (z. B. `MIME_INFO`, `BRAND_NAME`, `PACKING_UNITS`).
3. **V1b – 1.2-Export** (`ARTICLE*`, DTD-Validierung) über dieselbe Mapping-Engine — exakt der Fall „wir kommen an 1.2 nicht vorbei“.
4. **V2 – Import** (`T_NEW_CATALOG`/`T_UPDATE_PRODUCTS`, Merkmale rückwärts) **und** IXF-Standardimport als eigener Job.
5. **V3 – generisches 2005.2:** `CLASSIFICATION_SYSTEM` (+ `CLASSIFICATION_SYSTEM_FEATURE_TEMPLATES`, ECLASS-Referenzen), `MIME_TYPE`/`MIME_PURPOSE`, `CATALOG_GROUP_SYSTEM`, `VARIANTS`.
6. **V4 –** Preise/Varianten/Konfigurationen; **EuroCat** als Profil, sobald das **Syntax Binding** zur **EN 17015-2** veröffentlicht ist (Beobachtungsposten, kein Blocker).

### 11.7 Entscheidungen (2026-09-12)

1. **Zielversionen — entschieden:** **ETIM-Profil (2005-Basis)** und **2005.2** als Standard, **1.2** als ausdrücklich angeforderte Legacy-Ausgabe („an 1.2 kommen wir nicht vorbei").
2. **Preisstrategie — entschieden:** kein Preis, wenn nichts gemappt ist (Details in §11.5 Punkt 3).
3. **Standardcodes (`EF…`/`EV…`/Einheitencodes) — entschieden:** Pflege **im Profilmapping** (optionaler Default am Attribut/dan der Enum-Option) — nicht als harte Semantik in der Attributdefinition.
4. **Ablage — entschieden:** alles bleibt **lokal** in dieser Entwicklungsumgebung; `resources/*` gitignored (Ausnahme `resources/AGENTS.md`), **keine** Fixtures im Repo.
5. **Bereichsmerkmale — entschieden (2026-09-12):** eigener Datentyp **`range`** statt zwei Einzelattributen (Datenmodell-Verrenkung) oder Text-Konvention (unsichtbare Semantik); umgesetzt und getestet (§11.4 Punkt 3).
6. **Pflicht-Default beim Standard-Import — entschieden (2026-09-12):** `required=true` für alle Klassenmerkmale des Standards (§11.4 Punkt 2).
7. **ETIM-Zertifizierung — offen:** die Guideline nennt ein ETIM-BMEcat-Certification-Tool (dort werden lokale Standardmerkmale pflichtig) — wird eine Zertifizierung angestrebt?
8. **EuroCat — Beobachtungsposten:** sobald das Syntax Binding zur EN 17015-2 öffentlich ist, wird es hier ergänzt (gleiches Mapping, anderes Zielprofil).
9. **Noch zu beschaffen:** BMEcat-1.2-Spezifikations-PDF (`bmecatv12e.pdf`), die **Werte↔Merkmal-Zuordnung** der ETIM-`Values` (in keinem der beiden IXF-Exporte enthalten, s. §11.4 Punkt 1) und die vollständige IXF-Dokumentation.

### 11.8 BMEcat 1.2 — verifiziert aus `resources/bmecat/`

**Zwei Varianten, zwei Namespaces:** Die DTD-Variante nutzt `http://www.bmecat.org/bmecat/1.2/bmecat_new_catalog`, die XSD-Variante (10.01.2003) `http://www.bmecat.org/XMLSchema/1.2/bmecat_new_catalog`. Das `xmlns`-Attribut ist im Dokument `#FIXED` ⇒ **ein Dokument erfüllt nur eine der beiden Varianten** (Profiloption „DTD- oder XSD-Ausgabe“; im Zweifel die Validierungsvorgabe des Abnehmers erfragen). Die XSD-Variante enthält **identity-constraints** und warnt im Kopf selbst, dass Parser daran scheitern können ⇒ beim Validieren im Job auf diese Meldungen achten (ggf. ohne identity-constraints validieren und die Referenzen zusätzlich selbst prüfen).

**Struktur (DTD 1.2 Rev. A):**

- `BMECAT` (`version="1.2"` fixed) → `(HEADER, T_NEW_CATALOG)`.
- `HEADER (GENERATOR_INFO?, CATALOG, BUYER?, AGREEMENT*, SUPPLIER, USER_DEFINED_EXTENSIONS?)`.
- `CATALOG (LANGUAGE, CATALOG_ID, CATALOG_VERSION, CATALOG_NAME?, DATETIME?, TERRITORY*, CURRENCY?, MIME_ROOT?, PRICE_FLAG*)` → **`LANGUAGE` genau einmal**: 1.2 kennt **eine Sprache je Dokument** (2005 dagegen mehrsprachig in einer Datei, §11.2) ⇒ Sprachen = Dateien.
- `T_NEW_CATALOG (FEATURE_SYSTEM*, CLASSIFICATION_SYSTEM*, CATALOG_GROUP_SYSTEM?, ARTICLE*, ARTICLE_TO_CATALOGGROUP_MAP*)`, Attribut `prev_version?`.
- `ARTICLE (SUPPLIER_AID, ARTICLE_DETAILS, ARTICLE_FEATURES*, ARTICLE_ORDER_DETAILS, ARTICLE_PRICE_DETAILS+, MIME_INFO?, USER_DEFINED_EXTENSIONS?, ARTICLE_REFERENCE*)`, `mode` fixed `new`; die Update-Transaktionen (`bmecat_update_products_1_2`, `bmecat_update_prices_1_2`) sind eigene DTDs/XSDs → Import deckt `T_NEW_CATALOG`/`T_UPDATE_PRODUCTS`/`T_UPDATE_PRICES` ab.
- `ARTICLE_DETAILS (DESCRIPTION_SHORT, DESCRIPTION_LONG?, EAN?, SUPPLIER_ALT_AID?, BUYER_AID*, MANUFACTURER_AID?, MANUFACTURER_NAME?, MANUFACTURER_TYPE_DESCR?, ERP_GROUP_BUYER?, ERP_GROUP_SUPPLIER?, DELIVERY_TIME?, SPECIAL_TREATMENT_CLASS*, KEYWORD*, REMARKS?, SEGMENT?, ARTICLE_ORDER?, ARTICLE_STATUS*)` → 1.2 nutzt **`EAN`** (nicht `INTERNATIONAL_PID`), ein eigenes `ARTICLE_STATUS`-Vokabular (`bargain|new_article|old_article|new|used|refurbished|core_article|others`) und Elemente, die es ab 2005 nicht mehr gibt (`SEGMENT`, `ERP_GROUP_*`).
- `ARTICLE_ORDER_DETAILS (ORDER_UNIT, CONTENT_UNIT?, NO_CU_PER_OU?, PRICE_QUANTITY?, QUANTITY_MIN?, QUANTITY_INTERVAL?)` → `ORDER_UNIT` Pflicht, `CONTENT_UNIT` optional (im ETIM-Profil sind beide Pflicht).
- `ARTICLE_PRICE_DETAILS (DATETIME*, DAILY_PRICE?, ARTICLE_PRICE+)`, `ARTICLE_PRICE (PRICE_AMOUNT, PRICE_CURRENCY?, TAX?, PRICE_FACTOR?, LOWER_BOUND?, TERRITORY*)` mit **`price_type` als freiem Text (CDATA)** — ab 2005/ETIM enumeriert (`net_customer|net_list|nrp`); das Profil sollte die enge Liste verwenden.
- `MIME_INFO/MIME (MIME_TYPE?, MIME_SOURCE, MIME_DESCR?, MIME_ALT?, MIME_PURPOSE?, MIME_ORDER?)` → 1.2 kennt `MIME_TYPE`/`MIME_PURPOSE` (das ETIM-Profil nicht, §11.3).
- `ARTICLE_REFERENCE (ART_ID_TO, CATALOG_ID?, CATALOG_VERSION?)` mit `type` (`sparepart|similar|followup|mandatory|select|others|accessories|diff_orderunit|consists_of`) und `quantity` — Vokabular weicht von 2005 ab (`base_product` fehlt, `diff_orderunit` nur hier).

**Stufe A/B in 1.2 (wichtig für unser Modell):**

- **Navigation:** `CATALOG_GROUP_SYSTEM (GROUP_SYSTEM_ID?, GROUP_SYSTEM_NAME?, CATALOG_STRUCTURE+, GROUP_SYSTEM_DESCRIPTION?)` mit `CATALOG_STRUCTURE (GROUP_ID, GROUP_NAME, GROUP_DESCRIPTION?, PARENT_ID, GROUP_ORDER?, MIME_INFO?, USER_DEFINED_EXTENSIONS?, KEYWORD*)` und `type=(root|node|leaf)` sowie der Zuordnung `ARTICLE_TO_CATALOGGROUP_MAP (ART_ID, CATALOG_GROUP_ID, ARTICLE_TO_CATALOGGROUP_MAP_ORDER?)` → entspricht **unserer Kategorie-Taxonomie** (Stufe A) + `category_member`-Zuordnung (Knoten sind Mehrfachzuordnungen erlaubt: `CATALOG_STRUCTURE` ist mehrfach, `ARTICLE_TO_CATALOGGROUP_MAP*`).
- **Klassifikation = unsere Klassen + Merkmale:** `CLASSIFICATION_SYSTEM (CLASSIFICATION_SYSTEM_NAME, FULLNAME?, VERSION?, DESCR?, LEVELS?, LEVEL_NAMES?, ALLOWED_VALUES?, UNITS?, CLASSIFICATION_SYSTEM_FEATURE_TEMPLATES?, CLASSIFICATION_GROUPS)`; `CLASSIFICATION_GROUP (GROUP_ID, GROUP_NAME, GROUP_DESCR?, SYNONYMS?, CLASSIFICATION_GROUP_FEATURE_TEMPLATES?, CLASSIFICATION_GROUP_PARENT_ID?)`, `type=(node|leaf)`, `level`; `CLASSIFICATION_SYSTEM_FEATURE_TEMPLATE (FT_ID, FT_NAME, FT_DESCR?)`; `CLASSIFICATION_GROUP_FEATURE_TEMPLATE (FT_IDREF, FT_MANDATORY, FT_DATATYPE, FT_UNIT?, FT_ORDER?, FT_ALLOWED_VALUES?)`; `ALLOWED_VALUE (ALLOWED_VALUE_ID, ALLOWED_VALUE_NAME, ALLOWED_VALUE_DESCR?)`; `UNITS (UNIT (UNIT_ID, UNIT_NAME?, UNIT_DESCR?), @system)`.
  ⇒ **1:1-Abbildung auf** `object_type.taxonomy`/Klassenhierarchie (`CLASSIFICATION_GROUP_PARENT_ID` + `level`), `category_attribute.required` (`FT_MANDATORY`), `category_attribute.position` (`FT_ORDER`), `attribute_definition` (`FT_DATATYPE`), `enum_set`/`enum_option` (`ALLOWED_VALUES`), `unit` (`UNITS`). Anders als beim ETIM-Profil wird das Klassifikationssystem hier also **mitgeliefert** — das ist der richtige Weg für ECLASS und für eigene Standards.
- **Merkmale am Produkt:** `ARTICLE_FEATURES (REFERENCE_FEATURE_SYSTEM_NAME?, (REFERENCE_FEATURE_GROUP_ID | REFERENCE_FEATURE_GROUP_NAME)?, FEATURE*)` — 1.2 erlaubt zusätzlich den **Namen** der Gruppe und **`FEATURE` ist optional** (im ETIM-Profil Pflicht, sobald einer ETIM-Klasse zugeordnet). `FEATURE (FNAME, (VARIANTS | FVALUE+), FUNIT?, FORDER?, FDESCR?, FVALUE_DETAILS?)` → 1.2 kennt **`FORDER`** (Position) und **`FDESCR`**; Varianten stecken **innerhalb** des Merkmals (`VARIANTS (VARIANT+ (FVALUE, SUPPLIER_AID_SUPPLEMENT), VORDER)`) statt auf Produktebene wie ab 2005; `FVALUE+` erlaubt ebenfalls Bereiche/Mehrfachwerte.

### 11.9 Standardpakete (Klassifikationsdaten) — Konzept und Installer

**Abgrenzung (entschieden 2026-09-13):** Klassifikationsdaten sind **weder** System-Daten **noch** gewöhnliche Kundendaten, sondern ein **optionales Standardpaket**:

| | System-Daten | Standardpaket | Kundendaten |
|---|---|---|---|
| Beispiele | Objekttypen, System-Relationstypen, Root, Einheitenkatalog, Sprachbaum | ETIM-Klassen + Merkmale (später ECLASS/UNSPSC) | eigene Attributdefinitionen, Produkte, Zuordnungen |
| Herkunft | `seed::apply()` bei **jedem** Start, idempotent, Identität wird nachgeschärft | `scripts/etim_ixf_to_mef.py` aus dem lizenzierten IXF | Nutzer/Migration |
| Im MCF-Export | wird per Default ausgelassen (`include_system=false`) | eigener Bundle-Anteil (MCF **und** MEF) | ja |
| Läuft das System ohne? | nein | **ja** — Modell und Kategorisierung bleiben einfach leer | ja |

**Aufbau des Pakets** (kein neues Format — bewusst MCF + MEF): `manifest.json` + `model.mcf.json` (Knotentyp + Attributdefinitionen) + `classes.mef.json` (die Knoten = Daten) + `features.mcf.json` (`category_attributes`). Das Manifest nennt Format/Version, Standard und Version, Quelle, Zählungen, die **Reihenfolge** der Dokumente und die **benötigten Einheiten** (`units_needed`) — damit prüft der Installer den Zielkatalog, bevor er schreibt.

**Installation:** `scripts/install_standard_bundle.py --bundle <dir>` — **ein Befehl**, idempotent (MCF-Teile `mode=merge`; vorhandene Knoten werden über den Business-Key erkannt und übersprungen), mit Report je Schritt, Einheiten-Vorprüfung (Warnung; `--strict-units` bricht ab) und `--dry-run`. **Verifiziert** (25 Klassen / 380 Merkmale / 589 Klassen-Merkmal-Zeilen): Modell 2,0 s (217 angelegt, 165 übersprungen), Knoten 0,5 s (**15 neu, 10 übersprungen** — genau die bereits installierten), Merkmals-Zeilen 2,2 s (15 aktualisiert, 10 übersprungen), **0 Fehler**. Der Vollbestand (5640 Klassen, 76 625 Zeilen) liegt im **Minutenbereich** und das Modell-Dokument ist mehrere MB groß ⇒ der Vollimport gehört in einen Job (Worker/`?async=true`); der Installer ist die Referenzimplementierung der Schritte und bleibt für Teilpakete nützlich.

**Befunde aus dem 25-Klassen-Paket:**

1. **Einheiten-Konflikt (1 Fall):** dasselbe Merkmal kann in verschiedenen Klassen **verschiedene** Einheiten tragen — im 10-Klassen-Ausschnitt gab es keinen, im 25-Klassen-Paket einen. Die Einheit an der Attributdefinition ist damit **nicht immer** korrekt; das Manifest führt Konflikte (`unit_conflicts`), und für den Vollimport braucht es eine Entscheidung (Einheit je Klassen-Merkmal-Zeile, wie ETIM sie führt, oder Profil-Mapping).
2. **Fehlende Einheiten im Ziel:** `VA`, `Ohm`, `lm`, `°`, `kg/km` — der Seed hat 67 Einheiten, aber nicht alle ETIM-Einheiten. Die Warnung greift; ein „Einheiten-Standardpaket“ (Anlegen mit Faktor/Offset) bleibt Folgearbeit, weil der IXF **keinen** Faktor liefert.
3. **Lizenz/Ablage:** Pakete bleiben **lokal** (Bundle-Verzeichnis gitignored wie `resources/*`) — nichts davon gehört ins Repo.

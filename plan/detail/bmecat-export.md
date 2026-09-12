# BMEcat-Export & -Import / externe Klassifikationsstandards

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)); ergänzt Abschnitt 4 ([Datenmodell](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 9 (Ausbau), aufbauend auf dem MEF/MCF-Export/Import (Phase 6/9)
- **Stand:** Entwurf (Planung, **keine** Implementierung); Recherche-Stand 2026-09-12
- **Verwandt:** [kategorisierung.md](kategorisierung.md) (Taxonomie-Typen, Klassen-Merkmale), [datenexport-import-ui.md](datenexport-import-ui.md) (Export-Wizard), [konfiguration-export-import.md](konfiguration-export-import.md) (MCF), [einheiten.md](einheiten.md)

## 1. Ziel & Nicht-Ziele

**Ziel.** Meadow soll Produktdaten in **BMEcat** (XML) ausgeben und einlesen können — mit **Mapping bis auf die Produktattribute** und **Konvertierung** (Einheiten, Zahlen, Datum, Sprache, Codes) — und dabei Klassifikationsstandards wie **ETIM** korrekt bedienen (Klassen + Merkmale), weil im deutschsprachigen Raum genau für diesen Zweck BMEcat verwendet wird. Das ist die praktische Fortsetzung von Stufe A/B ([kategorisierung.md](kategorisierung.md)): Klassen und Pflicht-Merkmale existieren, jetzt müssen sie *ausgeleitet* werden.

**Nicht-Ziele (dieses Dokuments).** Kein Bestellprozess (das wäre openTRANS), kein vollständiger BMEcat-2005.1-Umfang (Preisstufen/Varianten/Konfigurationen sind Ausbaustufen), keine Ablösung des MEF/MCF-Formats.

## 2. Recherche (verifiziert)

**BMEcat (Quelle: de.wikipedia.org/wiki/BMEcat, abgerufen 2026-09-12)**

- Historie: **1.0 (1999)**, **1.2 (2001)**, **2005 (Nov 2005)**; 1.2 war Mitte 2005 die am weitesten verbreitete Version.
- In **Version 2005** wurde `ARTICLE` durch **`PRODUCT`** ersetzt; neu waren u. a. **Mehrsprachigkeit von Katalogen**, Multilieferantenkataloge, externe Kataloge (OCI/PunchOut/RoundTrip), konfigurierbare Produkte und logistische Produktinformationen.
- Ein Katalog hat **genau eine Sprache** (`CATALOG/LANGUAGE`, „BMEcat erwartet genau eine Sprache je Katalog"), **genau einen** `SUPPLIER`, **höchstens einen** `BUYER`.
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

**Noch zu beschaffen und zu verifizieren (Gate vor Implementierungsstart).** Diese Punkte sind bewusst **nicht** geraten:

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

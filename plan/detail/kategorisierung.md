# Kategorisierung und Gruppierung

- **Hauptdokument:** Abschnitt 4 ([Kategorisierung und Gruppierung](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 2, 9
- **Stand:** umgesetzt (Dimensionen, Taxonomie-Typen Stufe A, Klassen-Merkmale Stufe B inkl. Bindung eigener Attribute an Standardcodes `standard_codes`, **Standard-Register** `classification_standard`, BMEcat/ETIM-Export V1 in [bmecat-export.md](bmecat-export.md) §11.10); offen bleiben die Merkmalswerte (`EV…`, braucht ETIM-API/CMT), die Version **am Klassenknoten** und die weiteren BMEcat-Stufen (1.2, Preise/Varianten, Import)

Meadow PIM unterstützt die **Kategorisierung und Gruppierung von Objekten**, ohne dafür neue technische Entitäten einzuführen. Kategorien sind ganz normale Objekte, die über Relationen mit den zugeordneten Objekten verbunden werden. Dadurch bleibt das Datenmodell schlank und nutzt die bereits vorhandenen Graph-Fähigkeiten.

**Grundprinzip:**

- Eine **Kategorie** wird als Objekt mit `type: "category"` angelegt. Der Zieltyp ist durch die Rolle festgelegt (siehe unten), es gibt bewusst **keinen** zweiten Kategorie-Objekttyp (frühere Idee eines frei wählbaren Typs wie `grouping`/`folder` ist verworfen).
- Die Zuordnung eines Objekts zu einer Kategorie erfolgt über eine Relation vom Typ `category_member` – eine **Aggregation** (n:m) ohne Vererbungswirkung. Die Relationstypen sind in [relationen.md](relationen.md) spezifiziert.
- Die Kategorie-Zuordnung ist die **Verwendung/Rolle `category`** eines Relationstyps (`relation_definition.role`, siehe Abschnitt „Verwendung von Relationstypen“ in [relationen.md](relationen.md)); Kategorien-Filter und Bulk-Operationen hängen an dieser Rolle statt an der Typ-ID. Die Rolle erzwingt den Zieltyp `category`, ein `category`-Typ darf also nur auf Kategorie-Objekte zeigen.
- **Zusatz-Kategorisierungen** (mehrere Kategorie-Rollen neben der festen Taxonomie, mit eigenen Kanten-Attributen) sind umgesetzt – siehe Abschnitt „Zusatz-Kategorisierungen“ in [relationen.md](relationen.md). Sie zeigen auf **dieselben** `category`-Objekte; der Baum ist für alle Typen **gemeinsam**, gewechselt wird über die **Dimension** (siehe unten). Zusatz-Typen haben im Formular einen eigenen Abschnitt und im Bulk-Wizard ein eigenes Typ-Feld.

  ```json
  {
    "id": "reference:01M0…",
    "in": "object:01M0…",           // das zugeordnete Objekt (z. B. Produkt)
    "out": "category_5",            // die Kategorie (Ziel)
    "definition_id": "rel_category_member",
    "type": "category_member",
    "attributes": {
      "position": 3,               // optionale Sortierung innerhalb der Kategorie
      "context": "webshop"         // optional: nur für bestimmten Kanal relevant
    }
  }
  ```

- Objekte können **mehreren Kategorien** gleichzeitig zugeordnet sein (Mehrfachkategorisierung). Die `attributes` an der Relation erlauben eine kontextabhängige Sortierung oder Gültigkeit.

**Kategorien als normale Objekte:**

- Kategorien besitzen selbst flexible Attribute und können über `depends_on`-Relationen hierarchisch aufgebaut sein (z. B. `Elektronik` → `Audio` → `Kopfhörer`); jede Kategorie hat genau einen Kompositions-Elternteil (Standard: Universal-Root).
- Da sie reguläre Objekte sind, profitieren sie automatisch von:
  - Mehrsprachigkeit (z. B. Kategoriename in mehreren Sprachen über den Sprachbaum)
  - Datenwertvererbung entlang der Kategorien-Hierarchie (z. B. Standardbeschreibung von übergeordneten Kategorien)
  - Status/Workflow (z. B. Kategorie erst nach Freigabe sichtbar)
  - Audit-Log und Versionierung
- Die **Zuordnung** von Objekten zu Kategorien (`category_member`) ist eine reine Aggregation: Ein Produkt erbt **keine** Werte von einer Kategorie, der es zugeordnet ist. Vererbung läuft ausschließlich über die `depends_on`-Kette.

**Abgrenzung zur Pflegestruktur:**

- Die **Pflegestruktur** (z. B. Produkt → Varianten über `depends_on`) dient der Datenhaltung und Wertvererbung.
- Die **Kategorisierung** ordnet Objekte unabhängig von der Pflegestruktur neu an – etwa für Navigation, Marketing oder externe Klassifizierung.
- Beide Strukturen können parallel existieren und sich überlagern. Ein Objekt kann gleichzeitig in einer Vererbungshierarchie und in mehreren Kategoriebäumen hängen.

**Typische Anwendungsfälle:**

1. **Navigationsstruktur** für den Webshop: Kategorien wie „Neuheiten“, „Sale“, „Zubehör“.
2. **Fachliche Gliederung** unabhängig von der technischen Ablage: z. B. Produkte nach Zielgruppe oder Saison.
3. **Externe Klassifikationsstandards (z. B. ETIM):** eine eigene **Taxonomie** (eigener Knotentyp, s. unten) mit Klassenknoten und Mapping auf die eigene Navigation; siehe „Klassifizierungsstandards“ am Ende.

**Anforderungen an die Implementierung:**

- **Indizes** auf `reference.definition_id` (bzw. `type`) und `reference.out` sorgen für schnelle Abfragen der Kategorien eines Objekts sowie aller Objekte einer Kategorie.
- **Rekursive Abfragen** müssen effizient sein: z. B. alle Produkte inklusive Unterkategorien. SurrealDB bietet dafür Graph-Traversierung über die generische `reference`-Tabelle (z. B. `->(reference WHERE definition_id = 'rel_category_member')->object` in beide Richtungen).
- **Sortierung:** Die optionale `position` an der Relation wird beim Lesen ausgewertet, um eine konfigurierbare Reihenfolge innerhalb der Kategorie zu ermöglichen.
- **Caching:** Für häufig gelesene Kategoriebäume (Navigation) kann ein Cache eingesetzt werden. Für KMU-Größenordnungen ist das meist nicht erforderlich, da die Baumtiefen gering sind.

**Dimensionen (Kategorie-Relationstypen als Ansichts-Umschalter):**

- Eine **Dimension** ist der primäre (System-)Typ oder ein Zusatz-Kategorie-Typ. Alle Dimensionen teilen sich **denselben** Kategoriebaum – die Kategorie-Objekte und ihre `depends_on`-Hierarchie sind identisch; die Dimension entscheidet nur, **welche Zuordnungen** gezählt und gefiltert werden.
- `/categories` hat einen Umschalter („Dimension“, primärer Typ zuerst, danach die Zusatz-Typen mit ihrem mehrsprachigen Label). Er steuert die **Zähler** im Baum (`GET /objects/{id}/children?count_members=true&definition_id=<rel_…>`) und den Link „Im Grid anzeigen“ (`/products?category=<id>&definition_id=<rel_…>`). Ein Hinweis im UI erklärt, dass die Auswahl nur die Zuordnungen betrifft.
- **Grid:** Das Kategorien-Panel hat denselben Umschalter; er setzt `?category=…&definition_id=…` bzw. `?uncategorized=true&definition_id=…` und lädt Baum und Zeilen neu. `definition_id` wird nur zusammen mit einem Kategorie-Filter gesendet; der primäre Typ bleibt der Default (URL ohne `definition_id`).
- **Unverändert:** Der Baum selbst, seine Hierarchie und das Anlegen/Umhängen/Löschen von Kategorien hängen **nicht** an der Dimension – Kategorien sind für alle Typen dieselben Objekte.

**Ausblick auf Klassifizierungsstandards:**

- In späteren Versionen können Kategorien mit externen Klassifikationssystemen verknüpft werden, z. B. durch ein eigenes Objekt `classification_system` und Relationen `maps_to`.
- Für ETIM ließe sich die Kategorie um ein Attribut `etim_class` erweitern und die Zuordnung automatisiert überprüfen.
- Die Architektur ist so gewählt, dass solche Erweiterungen ohne Bruch des Kernmodells möglich sind.

## Taxonomie-Typen (eigene Knoten-Objekttypen) — umgesetzt (Stufe A, 2026-09-12)

**Motivation.** Eine Taxonomie braucht nicht nur andere *Zuordnungen* (das leisten die Dimensionen/Aussagen oben), sondern ggf. andere **Knoten**: eine Klassifikationshierarchie (ETIM/BMEcat) oder eine zweite Gliederung hat eigene Attribute, ein eigenes Key-/Label-Attribut, eigene Import/Export-Wege und darf die Marketing-Navigation nicht verwässern.

**Modell (zwei unabhängige Achsen):**

| Achse | Was sie bestimmt | Umsetzung |
| --- | --- | --- |
| **Taxonomie** (Knoten-Objekttyp) | *welche Objekte* die Knoten sind | `object_type.taxonomy = true` (Default `false`); genau der System-Typ `category` ist eine System-Taxonomie |
| **Dimension** (Kategorie-Relationstyp) | *welche Zuordnungen* gezählt/gefiltert werden | `relation_definition.role = category`; der Relationstyp trägt optional `target_types` |

- Eine Kategorisierung (`role: category`) darf auf **jeden** Typ mit `taxonomy: true` zeigen; ohne explizite `target_types` gilt die Regel zur Schreibzeit (also auch für später ergänzte Taxonomie-Typen). Mit expliziter Liste (`target_types: ["etim_class"]`) bleibt die Zuordnung auf eine Taxonomie beschränkt — eine alte Liste wie `["category"]` muss dafür bewusst erweitert werden.
- `memberCount` (`?count_members=true`) gibt es für **jeden** Taxonomie-Knotentyp (Name des Typs, nicht mehr hart `category`).
- `?uncategorized=true` schließt **alle** Taxonomie-Typen (plus `root`) aus: „nicht kategorisiert“ heißt „an keinen Knoten einer Taxonomie gehängt“.
- **Kein** zweiter Kategorie-*Objekttyp* im Sinne eines neuen Konzepts: Knoten bleiben normale Objekte mit Attributen/Vererbung/Audit; nur die erlaubte Typmenge wächst. Die früher angedachte Idee „frei wählbarer Typ wie `grouping`/`folder`“ ist damit in geordneter Form umgesetzt.

**UI:**

- `/object-types`: Spalte **Taxonomie** (Badge) und Schalter **Taxonomie-Knotentyp** im Dialog (für `system`-Typen deaktiviert/`400`).
- `/categories`: **Taxonomie**-Umschalter (Knotentyp; Anlegen/Verschieben folgt ihm, ein Knoten bleibt in seiner Taxonomie) **und** **Dimension**-Umschalter (Relationstyp; Zähler + „Im Grid anzeigen“). **Die Dimension führt den Baum, wenn sie gebunden ist** (2026-09-17): bei gesetztem `target_types` folgt der Taxonomie-Umschalter der Dimension und ist deaktiviert („Durch die Dimension festgelegt“); nur bei einer **generischen** Dimension (leere `target_types`) bleibt er dem Nutzer.
- Grid/Formular/Bulk-Wizard/Export-Quelle „Kategorie“ nutzen die Taxonomie-Typen als erlaubte Ziele bzw. als Knotenmenge.

**Grenze von Stufe A:** Knoten einer Klassifikation können eigene Attribute tragen (`applicable_types` des Typs), aber sie schreiben den **zugeordneten Objekten keine Merkmale vor**. Der wertschöpfende Teil eines Standards — Merkmale je Klasse mit erlaubten Werten, Prüfung und Export-Mapping — ist **Stufe B** (unten) und hängt nicht am Knotentyp.

## Klassifizierungsstandards (Bewertung, 2026-09-12)

**Recherche (verifiziert):** PIMcore trennt drei Konzepte — *Data Object Classes* (custom Objekttypen mit eigenem Attributset/Layout), *Categories* (Navigation) und den *Classification Store* (Stores → Groups → Keys, lokalisiert, vererbbar, mehrere je Objekt, Gruppen je Klasse einschränkbar, Pflicht-Keys je Gruppe). ETIM ist ein eigenständiger Klassifikationsstandard für technische Produkte: Klassen + technische Merkmale je Klasse, mehrsprachig, Auslieferung über JSON-API und BMEcat/xChange-Validierung — ausdrücklich keine Shop-Taxonomie. *(Akeneo-/Stibo-Doku war nicht erreichbar; als Muster daher nicht belegt.)*

**Schlussfolgerung:** Ein Standard braucht beides — die **eigene, versionierte Hierarchie** (Stufe A, erledigt) **und** die **klassen-spezifischen Merkmale** (Stufe B, offen). Ohne Stufe B bleibt ein ETIM-Baum eine Hierarchie mit Handpflege.

**Stufe B — Merkmale je Klasse: umgesetzt (2026-09-12).**

**Modell.** Neue Tabelle **`category_attribute`** (ein Datensatz je Klasse × Attribut):
`category_id` (native Referenz auf den Taxonomie-Knoten), `attribute` (Name der Attributdefinition, portabel wie der Kanal-`code`), `required` (Pflicht-Merkmal der Klasse), `position`, Audit-Felder; Unique-Index auf `(category_id, attribute)`.

- **Klasse ⇒ Merkmale:** Merkmale gelten fuer jedes Objekt, das der Klasse zugeordnet ist, **plus** die Merkmale ihrer **Vorfahren** (`depends_on`-Kette): eine Taxonomie kann gemeinsame Merkmale oben und spezifische unten definieren (ETIM/BMEcat-Stil). Die Klasse wird ueber **jede** Kategorisierung (Rolle `category`, beliebige Dimension) gefunden.
- **Wirkung:** Pflicht-Merkmale zaehlen in die **Vollstaendigkeit** (Einzel- und Batch-Pfad, damit Dashboard/Grid/Formular uebereinstimmen), erscheinen in `GET /objects/{id}?locale=` unter `missing_required` und damit im Filter `?missing_required=true`, und stehen Regeln als **`ctx.missing_features`** zur Verfuegung (`validation`/`workflow_guard`: „nicht publizieren ohne vollstaendige Klasse“). Ein Wert gilt als gesetzt, wenn er im eigenen Objekt oder per Vererbung vorliegt.
- **API (admin fuer CRUD, editor+ fuer Lesen):** `GET|POST /categories/{id}/attributes`, `PATCH|DELETE /categories/{id}/attributes/{attribute}`, `GET /objects/{id}/category-attributes` (aufgeloeste Merkmale + `missing`). Eine nicht vorhandene Attributdefinition wird mit `400` abgelehnt, ein Nicht-Taxonomie-Knoten ebenfalls. Beim **Loeschen** eines Attributs werden die Merkmals-Zeilen entfernt, beim **Umbenennen** mitgezogen, beim **Loeschen eines Knotens** kaskadiert.
- **UI:** `/categories` hat im Detail-Panel die Sektion **„Merkmale der Klasse“** (Anhaengen per Auswahl, Pflicht-Schalter, Entfernen; geerbte Merkmale mit Quellklasse); das **Produkt-Formular** zeigt die Sektion **„Klassen-Merkmale“** mit Pflicht-/Optional-Kennzeichnung, „fehlt“-Badge und Sprung zur Klassen-Pflege.
- **Eigene Attribute als Merkmalstraeger — umgesetzt (2026-09-13):** Ein Merkmal braucht nicht selbst ein Attribut zu sein. `attribute_definition.standard_codes` (`{"etim": "EF000040"}`) bindet ein **eigenes** Attribut an den Merkmalscode eines Standards; `GET /objects/{id}/category-attributes` liefert dann je Zeile `name` (der Code, den ein Export uebertraegt) **und** `attribute` (das Attribut, das den Wert traegt), und `required`/`missing`/Vollstaendigkeit rechnen mit dem **Attribut**. Fallback-Kette: Bindung → gleichnamiges Attribut → Code selbst. Genau ein Traeger je Code (die API lehnt eine zweite Bindung mit `409` ab); ein Attribut darf mehrere Standards bedienen. Damit laesst sich ein gepflegtes Produkt klassifizieren und exportieren, **ohne** die Standardattribute zu uebernehmen. Details und Grenzen: [bmecat-export.md](bmecat-export.md) §11.7 Punkt 10.

**Offen (Folgearbeit, nicht Teil von Stufe B):** die Merkmals**werte** (`EV…`, braucht ETIM-API/CMT) und die **Versionierung der Klassenknoten selbst** (siehe Register unten). Das **BMEcat/xChange-Export-Mapping** im Export-Wizard ist umgesetzt ([bmecat-export.md](bmecat-export.md) §11.10).

**Standard-Register — umgesetzt (2026-09-13).** Bis dahin war nirgends *Daten*, gegen welchen Standard in welcher Version ein Katalog klassifiziert ist: die Klasse trug nur `code`/`description`, und der BMEcat-Export nahm die Version aus einem Freitext-Feld des Profils (Default `10.0`) — ein Katalog konnte damit eine Version behaupten, die er nie installiert hatte.

**Modell.** Neue Tabelle **`classification_standard`**, ein Datensatz je **`code` + `version`** (Unique-Index): `code` (`etim`), `version` (`10.0`), `name` (`ETIM`), `source` (das Artefakt, aus dem das Paket gebaut wurde), `node_type` (der Objekttyp der Klassenknoten, `etim_class`), `notes`, Audit-Felder. Der Record-Key wird deterministisch aus `code` + `version` gebildet (`etim-10-0`).

- **API (Admin schreibt, `viewer+`/`X-Api-Key` liest):** `GET /classification-standards` (Filter `code`/`node_type`, `?counts=true` zaehlt die Klassenknoten und Merkmalszeilen **live**, gemessen **0,12 s** beim Vollbestand — die Zaehlung laeuft als gruppierter Scan `SELECT type::string(category_id) AS class_id, count() AS total FROM category_attribute GROUP BY class_id` plus Summe in Rust; die naheliegende Variante `WHERE category_id IN <5640 Record-IDs>` lieferte in **300 s** kein Ergebnis, siehe `plan/detail/bmecat-export.md` §11.9) liefert je Zeile die abgeleiteten Felder `feature_system_name` (`ETIM-10.0`) und `current` (neueste Version ihres `code`), `GET|POST|PATCH|DELETE /classification-standards/{id}` (`{id}` = `code@version` oder Record-Key; `code`/`version` unveraenderlich, `node_type` muss ein Taxonomie-Typ sein ⇒ sonst `400`).
- **Versionen statt Behauptungen:** mehrere Versionen koennen registriert sein (Update-Pfad); `current` folgt der **numerisch** verglichenen Version (`10.0.1` > `10.0` > `9.1`, ohne Ziffern sortiert Text vor Zahlen). Das **Loeschen** der Register-Zeile entfernt **keine** Daten — es beendet nur, dass der Export den Standard benennt (bewusst kein Kaskadieren: Klassen, Merkmale und Attribute sind Daten des Katalogs).
- **Der Export liest das Register:** `GET /objects/{id}/category-attributes` nennt die zugeordneten Knoten, der Worker holt `GET /classification-standards` und schreibt je Klassenblock `REFERENCE_FEATURE_SYSTEM_NAME = <feature_system_name des node_type>`; das Profilfeld `etim_version` ist nur noch der **Rueckfall** eines nicht registrierten Knotentyps (Live verifiziert: Registry gewinnt gegen `etim_version: "11.0"` im Profil). Der Bericht fuehrt `standards` (was installiert ist) und `feature_systems` (was geschrieben wurde) getrennt — zwei Standards in einem Katalog sind so sichtbar.
- **Portabel:** die MCF-Sektion **`classification_standards`** exportiert und importiert das Register ([konfiguration-export-import.md](konfiguration-export-import.md)); der Konverter schreibt den Eintrag in `model.mcf.json`, der Installer registriert das Manifest idempotent (`merge`) und **warnt**, wenn Manifest und Dokument uneins sind (aeltere Pakete ohne die Sektion).
- **Read-only-Kennzeichnung:** Referenzdaten bleiben als solche erkennbar — `attribute_definition.reference_data` (Attributseite, `kategorisierung.md` „Referenzdaten“), die neue Standards-Karte samt Spalte **„Standard“** auf `/object-types` (Name, Version, Quelle, Klassen-/Merkmalszahl, „aeltere Version“) und der Hinweis, dass Klassenknoten, Merkmale und Merkmalsdefinitionen eines Standards beim naechsten Paket ersetzt werden. **Nicht** blockiert wird das Bearbeiten (der Katalog darf seine Klassen ergaenzen) — die Kennzeichnung informiert, statt zu verbieten.
- **Verifiziert (2026-09-13, live):** Register-Zeile aus dem Installer (ETIM 10.0, Quelle `ETIM-10.0-ALL-SECTORS-IXF…`, 25 Klassen / 589 Merkmalszeilen — deckt sich mit dem Manifest), MCF-Export/-Reimport (`skipped`, idempotent), `409` bei doppelter `code`+`version`, `400` bei Nicht-Taxonomie-`node_type`, zweite Version wird `current` und faellt beim Loeschen zurueck, und der Export benennt `ETIM-10.0` aus dem Register (Rueckfall-Pfad ebenfalls geprueft). Kern-Unit-Tests: Versionsordnung, Record-Key, Systemname, Validierung, Sektions-Label (121 Cargo-Tests). **Vollbestand (2026-09-13):** ETIM 10.0 komplett installiert — **5640 Klassen**, **17 377 Merkmalsdefinitionen**, **76 625 Klassen-Merkmal-Zeilen** in 18 Feature-Gruppen ([bmecat-export.md](bmecat-export.md) §11.9: zwei Laeufe `--all --bulk` + `--all`, gechunkte Sektionen, `?counts=true` = 0,12 s).

**Offen:** die **Version am einzelnen Klassenknoten** (heute ist das Register pro `node_type` — genau das, was der Export braucht). Zwei Fassungen desselben Standards an *einem* Knotentyp sind damit nicht unterscheidbar; ein Paket-Update ersetzt Klassen und Merkmale per Business-Key (der Standard ist die Quelle der Wahrheit), nicht mehr definierte Merkmale einer Altversion bleiben aber stehen. Ebenso offen: ein „Klassen-Merkmals-Diff“ beim Update (was ist neu/entfallen) und das Aufraeumen verwaister Merkmale.

**Nicht-Ziele (bewusst):** keine klassen-spezifische Einschraenkung der erlaubten Werte (erlaubte Werte bleiben an der Attributdefinition/Enum-Set, wie in ETIM), kein zweiter Merkmals-Mechanismus ausserhalb der Attributdefinitionen, keine vererbenden Kategorie-Kanten.

## Referenzdaten — das Standardvokabular bleibt draussen (umgesetzt 2026-09-13)

**Problem.** Der Standard-Import macht jedes Merkmal **zum Attribut** (Name = `EF…`). Allein das mitgelieferte ETIM-10.0-Paket bringt **380 Attributdefinitionen** (der volle Standard 17 377); sie fuellen das Produktformular, die Export-Feldauswahl und jede Attributauswahl und verdecken die eigenen Attribute. Bestes Beispiel: ein Pflicht-Merkmal der Klasse „fehlt“, aber kein Feld, in dem man es sieht.

**Entscheidung (Option A, Auftraggeber 2026-09-13):** `attribute_definition.reference_data` (bool) kennzeichnet das Vokabular eines Standards. Es wird **dort ausgeblendet, wo ein Attribut *gewaehlt* wird** — und bleibt **dort sichtbar, wo Daten *gelesen* werden**:

| Flaeche | Verhalten |
|---|---|
| Produktformular | Referenz-Attribute erscheinen **nur**, wenn das Objekt bereits einen Wert dafuer traegt (nichts Vorhandenes darf verschwinden, der Wert bleibt pflegbar). Merkmalswerte sieht man in der Sektion **„Klassen-Merkmale“**. |
| Export-Feldauswahl | standardmaessig ausgeblendet, ueber den Schalter **„Standard-Attribute (Anzahl)“** zuschaltbar — die gruppierte Auswahl (18 Gruppen) bleibt unveraendert. |
| Massenpflege (Bulk-Wizard, Suchen & Ersetzen) | als Ziel ausgeblendet. |
| Grid-Spalten, Suche, Klassen-Merkmale | unveraendert sichtbar (Lesesicht). |
| Suchformular (gespeicherte Suchen, erweiterte Suche im Grid) | standardmaessig ausgeblendet, Schalter **„Standard-Daten in der Feldliste (Anzahl)“** in der Sektion „Bedingungen“ schaltet die Filterfelder zu (auch die Sortierfelder). Der Kern liefert die Felder **weiterhin filterbar** aus — eine bestehende Suche bleibt gueltig und zeigt ihr Feld weiter an (2026-09-13). |
| Attributverwaltung | vollstaendig sichtbar: Spalte **„Standard“**, Filter „Eigene / Standard-Attribute (Referenzdaten)“ und Kontrollkaestchen **„Referenzdaten“** — Haekchen entfernen heisst **uebernehmen** (das Attribut wird ein eigenes, plegbares Attribut im Formular). |

**Warum so und nicht anders:** kein Zwang, Standardattribute zu verwenden (Leitlinie „einfach, nicht zu komplex“); ein Merkmal traegt weiterhin entweder ein **eigenes, gebundenes** Attribut (`standard_codes`, oben) **oder** das Standard-Attribut selbst — Letzteres, nachdem man es uebernommen hat. Referenzdaten sind **keine** System-Daten: sie lassen sich loeschen, uebernehmen oder unveraendert liegen lassen, und die MCF-Sektion `attribute_definitions` nimmt das Kennzeichen mit (`konfiguration-export-import.md`).

**Umsetzung:** Konverter `scripts/etim_ixf_to_mef.py` setzt `reference_data: true` an jedes Merkmal (die Klassen-Knotentyp-Attribute `code`/`description` bleiben eigene), die reine Regel liegt in `meadow/frontend/reference_data.py` (6 Tests in `tests/test_reference_data.py`), und der Feldkatalog des Suchformulars traegt das Kennzeichen mit (`search_schema.rs`, `search_form.py`).

## Der Strukturbaum enthält **keine** Taxonomieknoten — und die Baum-Abfragen sind begrenzt (2026-09-14)

**Befund (live gemessen, vollständiger ETIM-Standard installiert).** Der Kompositionsbaum (`rel_depends_on`) hat am Universal-Root **5694** Kinder — davon **5648 Taxonomieknoten** (8 `category`, 5640 `etim_class`) und nur 46 echte Strukturobjekte (Produkte, Testobjekte). Die Taxonomieknoten hängen bewusst unter demselben Root wie die Objekte (die Wurzel eines Baums = die Kinder des Roots **dieses Typs**), aber daraus folgten drei Fehler:

1. **Der Strukturbaum zeigte sie mit** — Produkte und Klassifikationsklassen in einer Liste. Taxonomieknoten gehören in **ihr** Baum-Panel (Kategorien/Klassifikation), nicht in die Produktstruktur.
2. **`?count_members=true` war quadratisch.** Die Knotenzählung rief **je Kind** `category_member_keys` auf, und jeder Aufruf las *alle* Kompositionskanten und lief einen Subtree ab: 100 Taxonomiekinder am Root ≈ **8 s**, nur für die Kategorie-Ansicht.
3. **Der Typfilter lief clientseitig** (`if child.get("type") in taxonomy_types`) und schnitt stillschweigend ab: `/categories` und die Kategorie-Ansicht im Grid sahen nur die **ersten 100** Root-Kinder, also je nach Id-Reihenfolge eine zufällige Teilmenge mehrerer Taxonomien.

**Kontrakt (Kern):** `GET /objects/{id}/children` filtert im Kern.

| Parameter | Bedeutung |
| --- | --- |
| `scope=all` (Default) / `composition` / `taxonomy` | `composition` = alles **außer** Taxonomieknoten (die Produktstruktur), `taxonomy` = nur Taxonomieknoten, `all` = unverändert |
| `type=<Objekttyp>` | nur Kinder dieses Typs — **eine** Taxonomie statt aller Bäume |
| `count_members=true` + `definition_id=` | `memberCount` je Taxonomiekind, jetzt in **einem** Durchgang statt je Knoten |

Unbekanntes `scope` ⇒ `400` (nie still ignorieren). Damit ist „Taxonomieknoten stehen nicht im Strukturbaum“ eine **Kernaussage**, nicht eine Frontend-Konvention.

**Der Filter *muss* die Record-Link-Traversierung sein** (`AND in.type NOT IN $taxonomy` bzw. `AND in.type = $node_type`), **nicht** `in IN (SELECT VALUE id FROM object WHERE …)`: gemessen am Root braucht die Subquery-Form **31,7 s** (der Planer wertet sie pro Kandidatenzeile aus), wärend `in.type …` **0,12 s** für dieselbe Seite ohne Taxonomieknoten liefert (`idx_ref_out_def` treibt den Zugriff weiterhin). Die Batch-Zählung (`category_member_counts`) liest beide Kantenmengen **je einmal** und summiert die Subtrees in Rust (`subtree_member_counts`, rein und getestet).

**Frontend-Konsequenzen:**

- `/products` **Strukturbaum**: `scope=composition` — Produkte/Struktur ohne Taxonomieknoten.
- `/products` **Kategorie-Ansicht**: neuer **Taxonomie-Umschalter** (Knotentyp) neben Wurzel- und Dimensionsauswahl; Wurzeln und Kinder kommen mit `scope=taxonomy&type=<Taxonomie>`. Eine Taxonomie, die größer als eine Seite ist, wird **geblättert** (siehe unten) statt still gekürzt.
- `/categories`: lädt die Wurzeln der gewählten Taxonomie über denselben Filter (vorher: erste 100 Root-Kinder, gemischt über alle Taxonomien).
- **Objekt-Picker**: leitet den Scope aus den erlaubten Zieltypen ab — sind **alle** Zieltypen Taxonomien, wird der Taxonomiebaum gezeigt, sonst die Komposition (vorher: alle Root-Kinder, also Produkte *und* Klassen in jedem Picker). Seit 2026-09-17 entscheidet zusätzlich die **Erreichbarkeit** im Baum: ein eingeschränkter, nicht-taxonomischer Zielsatz, den der Kompositionsbaum gar nicht erreicht, wird **flach und geblättert** gelistet (siehe „Objekt-Picker: erreichbare Ziele …“).
- Baumknoten-Label: fällt auf `code` zurück, wenn kein `name` existiert — eine Liste aus 5640 Klassen ist sonst nicht lesbar (`EC000012` statt „etim_class“).

## Taxonomiebaum: Paging und Server-Suche (2026-09-14)

**Problem.** Der Kontrakt oben hat das stille Kürzen beendet, aber eine Taxonomie mit **5640** Klassen bleibt ohne Paging/Suche unbenutzbar: die Kategorie-Ansicht zeigte die ersten 1000 Wurzelknoten („es werden die ersten N gezeigt“), Klasse 1001 war nur per Scrollen … also gar nicht erreichbar.

**Entscheidung:** die Panels blättern **seitenweise** (`taxonomy_tree.PAGE_SIZE = 200`, reine Helfer in `meadow/frontend/taxonomy_tree.py`) und suchen **serverseitig** über die ganze Taxonomie. Kein Kürzen, keine versteckten Knoten.

| Baustein | Verhalten |
| --- | --- |
| Seiten | `has_more(loaded)` = „eine weitere Seite kann folgen“ (volle Seite = *vielleicht*, kurze Seite = Ende). Der Baum trägt dann hinten einen Knoten **„Weitere Knoten laden …“** (`MORE_ID = "__more__"`, nie eine Objekt-Id); ein Klick darauf hängt die nächste Seite an (`append_page`, dedupliziert). |
| Suche | Tippen im Filterfeld ersetzt den Baum durch die **Treffer** der ganzen Taxonomie (`GET /objects/picker`) plus Bildunterschrift „N Treffer für „…“ im ganzen Baum“. Leeren stellt den (unverändert nachgeladenen) Baum wieder her. |
| Kleine Taxonomie | Bleibt **clientseitig** gefiltert (`filter_tree_nodes`) — für 8 Kategorien ist die Baumansicht mit Hierarchie besser als eine flache Trefferliste (`uses_server_search(query, paged=…)`). |

**Vier gemessene Fallstricke** (nur so ist die Suche korrekt und schnell):

1. **Der Fulltext-Index trifft nur ganze Token.** `attributes @@ 'EC0123'` findet **0** der 61 passenden Klassen, `attributes @@ 'EC012373'` findet genau eine. Ein Teil-Code braucht daher einen **Präfix-Vergleich** (`attributes.code >= <Präfix> AND < <Präfix+1>`, die letzte Stelle um einen Codepoint erhöht — `prefix_upper_bound`, rein und getestet). Gemessen: 61 Treffer in **30 ms** über die 5640 Klassen (kein Index auf `attributes.code`, also ein Scan — bei 5,7 k Objekten unkritisch).
2. **`OR` mit einem Fulltext-Ausdruck degeneriert.** `(attributes @@ 'EC0123' OR string::starts_with(attributes.code, 'EC0123'))` liefert in SurrealDB 3.2 plötzlich **5640** Zeilen statt 61 (die Fulltext-Seite trifft dann *jedes* Objekt; mit `… OR false` stimmt es wieder, das wird aber konstant gefaltet). Teil- und Volltextsuche sind deshalb **zwei getrennte Abfragen**, deren Treffer im Kern zusammengeführt werden (Präfix-Treffer zuerst, dann die Volltext-Treffer nach `score`).
3. **`string::starts_with(NONE, …)` ist ein Fehler** („Argument 1 was the wrong type“), sobald ein Objekt kein `code` hat — der Präfix-Vergleich (`>=`/`<`) tut das nicht.
4. **`completeness` kostet ~0,3 s *je Treffer*** (`completeness::compute`), gemessen: 5 Treffer mit `?locale=` = **1,4 s**, ohne `?locale=` = **9 ms**. Die Baumsuche zeigt keinen Vollständigkeits-Badge und ruft deshalb `?completeness=false` (das Label bleibt mit `locale` korrekt aufgelöst); die Treffer-Zahl bleibt trotzdem begrenzt (`SEARCH_LIMIT` = 100).

**Kern-Kontrakt (`GET /objects/picker`)** — für den Referenz-Picker unverändert, additiv:

| Parameter | Bedeutung |
| --- | --- |
| `code_prefix=true` | zusätzlich **Teil-Codes**: `EC0123` findet alle Klassen `EC0123…` (Präfix-Range, in Kleinschreibung zusätzlich großgeschrieben, damit die Suche wie der Volltext case-insensitiv ist) |
| `label_prefix=true` | zusätzlich **Teil-Wörter im Label**: `Bremsenr` findet `Bremsenreiniger` (seit 2026-09-17; siehe „Objekt-Picker: erreichbare Ziele …“) |
| `completeness=false` | keinen Vollständigkeits-Wert je Treffer berechnen (Pflicht für Aufrufer mit vielen Treffern) |

**Nachtrag 2026-09-17:** Die offene Lücke ist geschlossen — `label_prefix` ist im Frontend-Wrapper (`api.py::picker_search`) **an** (Opt-out pro Aufrufer), und alle tippenden Felder sind entprellt.

**Weitere offene Punkte:** die Trefferliste ist flach (keine Hierarchie; ein „im Baum aufklappen“ bräuchte einen Ancestors-Endpunkt), und der Filter wirkt auf die Ansicht, in der er getippt wurde (Umschalten Struktur ↔ Kategorie rendert den Baum ungefiltert, der Suchtext bleibt im Feld stehen).

## Objekt-Picker: erreichbare Ziele und Teilwort-Suche (2026-09-17)

**Problem (gemeldet).** „Bei normalen Aggregationen … Alternative to und Accessory for scheint nicht korrekt zu sein“ — bei `fits_vehicle` (Ziel `vehicle_generation`) funktionierte der Picker, bei den Aggregationen auf `product` nicht. Zwei **unabhängige** Ursachen, beide gemessen:

1. **Der Kompositionsbaum erreicht die Ziele nicht.** Der Picker zeigte für jeden nicht-taxonomischen Zielsatz den Kompositionsbaum. Gemessene Kompositionskanten (Dev-Daten): `vehicle_make → root` 22, `vehicle_model → vehicle_make` 93, `vehicle_generation → vehicle_model` 166 — aber `product → part_group` 2 000, `oem_number → part_group` 1 000, `part_group → root` nur 12. Die erste Baumebene sind **22 Fahrzeugmarken**; ein Produkt hängt unter einer Baugruppe, und `scope=composition` blendet Taxonomieknoten (damit den ganzen Baugruppen-Zweig) aus. Ergebnis: **jede** Zeile war eine ergraute Marke, nichts auswählbar.
2. **Der Volltext-Index trifft nur ganze Token.** `attributes @@ 'brake clean'` → **0** Treffer, `… @@ 'brake cleaner'` → 20 (16 926 Produkte). Ein halb getipptes Wort fand also nie etwas — genau die im Abschnitt oben als offene Lücke notierte Stelle.

**Entscheidung — Teil 1: der Picker folgt der Erreichbarkeit.** Die Frage „ist dieser Zieltyp im Kompositionsbaum überhaupt erreichbar?“ beantwortet der Kern mit **`GET /objects/composition-types`**: `{root_type, edges: [{parent, child, count}]}`, die Typkanten der Komposition (eine gruppierte Abfrage, gemessen **~190 ms warm** bei 12 197 Kompositionskanten von 28 624 — deshalb versionsgebunden im `MetaCache` gecacht, `stats_version`). Reine Frontend-Regel (`object_picker.picker_tree_mode`, `composition_reachable`):

| Zieltypen | Panel |
| --- | --- |
| alle Taxonomietypen | Taxonomiebaum (wie bisher, Umschalter bei generischer Dimension) |
| nicht-taxonomisch, aber erreichbar (`vehicle_generation`: Marke → Modell → Generation) | **Kompositionsbaum** (unverändert) |
| nicht-taxonomisch und **nicht** erreichbar (`product`, `variant`, `oem_number`: der Weg führt über einen ausgeblendeten Taxonomieknoten) | **flache, geblätterte Liste** der erlaubten Objekte (`taxonomy_tree.PAGE_SIZE` = 200, „Weitere Objekte laden …“, Umschalter nur bei mehreren erlaubten Typen) |
| unbeschränkt (`target_types` leer) | Kompositionsbaum |

Ein Typ gilt nur als erreichbar, wenn **jeder** erlaubte Typ eine Elternkette zur Universal-Wurzel hat und **kein** Sprung über einen ausgeblendeten (Taxonomie-)Knoten führt. Kennt das Frontend den Graphen nicht (Abfrage fehlgeschlagen), bleibt es beim Baum — der Status quo statt eines stillen Panelwechsels.

**Entscheidung — Teil 2: `?label_prefix=true` schließt die Suchlücke.** Der Kern scannt je Sprache der Arbeitskette **und** die skalaren `name`-Werte (`string::starts_with(string::lowercase(<string>…), $term)`); Treffer ranken **hinter** den nach `score` sortierten Volltext-Treffern und werden nur gesucht, solange die Trefferliste noch nicht voll ist. Gemessen (Dev, 16 926 Produkte): kein Treffer **~180–250 ms**, eine Trefferliste kostet mehr allein wegen des `completeness`-Badges (20 Treffer ≈ 1 s, unverändert gegenüber dem Volltext-Pfad). Deshalb `debounce=400` am Suchfeld. Skalare Namen (OEM-Nummern wie `5Q0 936 304 W`) sind mit abgedeckt — sonst blieben sie per Teilwort unfindbar (`5Q0 9` → 0 Treffer ohne, 1 Treffer mit dem Zusatz).

**Nicht** erweitert: der Scan läuft über die **Arbeitssprache** und ihre Kette, nicht über alle Sprachen — der Treffer zeigt sein Label in der Arbeitssprache, und ein Treffer, dessen deutsches Label der Eingabe widerspricht, wäre verwirrender als kein Treffer. Der Volltext-Pfad trifft weiterhin Werte **jeder** Sprache.

**Verifikation (live, Playwright, 2026-09-17):** „Alternative zu“/„Zubehör fuer“ → Tab **Liste**, 201 Zeilen (200 Produkte + „Weitere Objekte laden“), **alle** auswählbar, Suche `Bremsenr` → Treffer; „OEM-Referenz“ → Liste mit 200 OEM-Nummern, Suche `5Q0 9` → Treffer; „Passt zu Fahrzeug“ → Tab **Struktur**, Marke aufklappen (Toyota → Corolla) ⇒ `Toyota Corolla E170 (2013–2019)` auswählbar, Suche `Golf` → Treffer. Zusätzlich: Taxonomie-Picker (`verify_dimension_picker`, `verify_dimension_coupling`) und alle Seiten (`verify_pages`) ohne Fehler.

**Umsetzung:** Kern `api/objects.rs` (`composition_types` + `label_prefix_hits`/`prefix_languages`/`safe_language_code`/`append_unique`, 5 Unit-Tests), `cache.rs` (versionsgebundener Graph-Cache); Frontend `object_picker.py` (`picker_tree_mode`, `composition_reachable`, `object_type_options`, Flat-Modus in `open_structure_picker` über den `paged`-Loader, `switcher_label`), `api.py` (`picker_search(label_prefix=True)` als Default, `fetch_composition_types`), Locales (`form.picker_list_tab`, `form.picker_more`, `form.picker_empty`, `form.picker_list_hint`, `form.picker_object_type`), Tests in `tests/test_picker_tree.py`.

**Umsetzung:** `meadow/frontend/taxonomy_tree.py` (rein, 12 Tests in `tests/test_taxonomy_tree.py`), Einbindung in `grid.py` (Kategorie-Ansicht von `/products`) und `categories.py` (`/categories`); Kern: `api/objects.rs` (`picker` mit `code_prefix`/`completeness`, `code_prefix_hits`, `prefix_upper_bound` — 2 Unit-Tests) und `language.rs::resolve_object_label` (Name → `code` → Objekttyp).

**Suchtreffer und „im Baum zeigen“ (2026-09-14).** Die Treffer erscheinen als **Zeilen** statt als Treeitems: NiceGUI-Bäume tragen keine Knöpfe in den Knoten, und eine Trefferliste ist keine Hierarchie. Jede Zeile hat einen Label-Knopf (filtert das Grid, `select_category`) und einen Baum-Knopf (`grid.taxonomy_in_tree`) — dieser ruft **`GET /objects/{id}/ancestors`** (Pfad vom äußersten Vorfahren bis zum Objekt selbst, je Eintrag Objektdokument + `hasChildren`; die Universal-Wurzel ist nicht Teil des Pfades, sie ist der Elternteil jeder Baumspitze) und rendert den Zweig (`taxonomy_tree.path_nodes` verschachtelt, `path_chain` liefert die Kette, `LazyTree.set_path` sendet Knoten **und** `expanded`). Der Pfad ist also **aufgeklappt bis zum Treffer**; nur der letzte Knoten bleibt `lazy` (eine Lazy-Expansion würde den Zweig sonst durch die *seitengepufferten* Kern-Kinder ersetzen), und Leeren des Filters bringt den normalen (nachgeladenen) Baum zurück. Live verifiziert: flacher ETIM-Knoten ⇒ Pfad = der Knoten; eine testweise unter `EC000001` gehängte Klasse ⇒ zweistufiger Pfad mit aufgeklapptem Elternteil (die Testverschiebung wurde danach zurückgenommen).

**Nebenbefund (Performance, gemessen): die Feature-Bindings wurden *pro Objekt* geladen.** `completeness::compute` rief `class_features::feature_bindings` — `SELECT name, standard_codes FROM attribute_definition WHERE standard_codes IS NOT NONE` über **17 397** Definitionen (mit ETIM 10.0 trägt nahezu *jede* Merkmals-Definition eine Bindung, nicht nur „ein kleiner Satz“). Das kostete **~0,3 s pro Objekt**: `GET /objects?ids=<id>&locale=de` 332 ms, `?limit=20&locale=de` 5,7 s, ein Picker-Treffer mit Badge 289 ms, `GET /objects/{id}?locale=de` 340 ms. Die Bindungen liegen jetzt im `MetaCache` (`invalidate_attribute_definitions` räumt sie mit den Attributdefinitionen ab) — danach: 69 ms / 0,5 s / 39 ms / 41 ms.

**Laufzeit-Härtung der Lesepfade (2026-09-14, alle Zahlen gemessen, Dev-Datenbank: 17 379 Attributdefinitionen, 5 640 Klassen, 76 625 `category_attribute`-Zeilen).** Auslöser war, dass eine Seite als **500** endete, weil der Seitenaufbau das NiceGUI-Timeout riss — die Ursache lag aber im Kern. Vier Befunde, vier Korrekturen (Details: `crates/AGENTS.md`, Abschnitt „Read-path performance rules“):

1. **Vollständigkeit je Zeile** — `GET /objects` rief `completeness::compute` pro Objekt (~6 Abfragen je Objekt, inkl. Feature-Bindings und Klassen-Merkmalen). Jetzt rechnet **ein** `completeness::compute_batch` die ganze Seite (`load_ancestor_attributes` löst die Vorfahrenketten identisch zum Einzelpfad auf).
2. **`required_names` je Objekt** — die Pflicht-Attribute eines Typs werden jetzt im `MetaCache` **je Typ** gecacht (ungültig bei Attributdefinitions- *und* Objekttyp-Schreibvorgängen).
3. **`class_required_names_batch` lud die ganze `category_attribute`-Tabelle** (76 625 Zeilen / 6,8 MB, ~2 s pro Aufruf, sobald *ein* kategorisiertes Objekt in der Seite lag). Jetzt werden die Merkmale nur für die **beitragenden** Klassen geladen (zugeordnete Knoten + Vorfahrenketten, dedupliziert). Ebenso liest der MCF-Export die Klassenknoten über den Typfilter statt über eine 5 640er Id-Liste.
4. **`id IN array::map($keys, |$k| type::record('object', $k))` ist quadratisch.** SurrealDB wertet den Ausdruck **je Kandidatenzeile** aus — gemessen **100 Keys 1,9 s / 1 000 Keys 16,6 s / 5 640 Keys 110 s**, während die Record-**Literal**liste den Id-Index nutzt (**49 ms / 178 ms / 76 ms**). Alle Id-Listen-Filter (`/objects?ids=`, `?ancestor=`, `?category=`, `?collection=`, Kinder, Vorfahren, Kategorie-Kanten) rendern deshalb `[object:a, object:b]` (`api::keys::record_list_literal`; Schlüssel außerhalb `[A-Za-z0-9_-]` werden verworfen — solche Keys kann es nicht geben).

Wirkung (gemessen, gleiche Daten): `?limit=50&locale` 1 071 → ~180 ms, `?limit=1000&locale` 18 128 → 244–289 ms, `?limit=1000&missing_required` 17 766 → 220–255 ms, `?type=product&locale` 2 044 → 149–501 ms, `GET /objects/stats` 2 461–3 043 → 392–722 ms, `?ids=<100>` 1 933 → 107 ms, `children?limit=1000` 1 236 → ~130 ms, `ancestors` ~1 500 → 15 ms, Picker mit Badges 523 → 100–144 ms, `GET /config/export` 81 304 → 8 356 ms.

**Zusätzlich (Frontend, `/export`):** `export_fields.attribute_names`/`group_members` prüften die Deduplizierung gegen **Listen** — bei 17 379 Definitionen 1,6 s + 1,7 s **je Seitenaufbau**. Mit Sets: 5,7 ms + 24 ms. Und weil NiceGUI pro Seite 3 s erlaubt und es **keinen** globalen Schalter gibt (`ui.run(response_timeout=…)` existiert nicht), setzt jetzt **jede** `@ui.page(...)` `response_timeout=PAGE_TIMEOUT` (`layout.py`, `PIM_PAGE_TIMEOUT`, Default 60 s, siehe `meadow/AGENTS.md`).

**Vokabular-Endpunkt für den Export-Wizard (2026-09-15).** Der Wizard bietet **jede** Definition an; `GET /attribute-definitions` deckelt aber bei 1000 Zeilen, also lud `export_page.py` 18 Seiten (~4,5 s). Neu: **`GET /attribute-definitions/vocabulary`** liefert die **schlanke** Projektion aller Definitionen (`id`/`name`/`label`/`type`/`group_ids`/`multilingual`/`applicable_types`/`reference_data`, 5,83 MB) in **einem** Aufruf, aus dem `MetaCache` (`attribute_vocabulary_json`, als **fertiger Body** gecacht — Klonen + Neuserialisieren kostete mehr als die Abfrage); gemessen `1,5 s kalt / 9 ms warm`, Seitenaufbau `/export` **12,0 s → 2,2 s**. Der Cache wird mit den Attributdefinitions-Schreibvorgängen **und** dem MCF-Import geleert (`POST /config/import` schreibt Definitionen direkt — vorher blieben alle Metadaten-Caches nach einem Import veraltet). Der seitenweise Pfad ist **entfernt** (keine Fallbacks, solange keine produktive VPS-Umgebung existiert: Kern und Frontend laufen zusammen); `tests/test_export_page.py` prüft den einen Aufruf und dass ein fehlender Endpunkt **laut** fehlschlägt.

**Weiterhin offen (bewusst, im Rahmen der Datenmenge akzeptiert):** `GET /config/export` ist mit **10–13 s / 14,3 MB** eine Massenoperation (7,8 s Attributdefinitionen + 7,5 s `category_attributes`); sie läuft nur auf Klick, nicht im Seitenaufbau. **Messhinweis:** `Invoke-WebRequest` bläht grosse Antworten um ~1,4 MB/s auf (25 s statt 10 s) — payload-lastige Pfade mit einem Python-Client oder über die Seitenzeit im Browser messen.

**Klassen-Merkmale und Vollständigkeit: drei Fehler (gefunden und behoben 2026-09-17).** Alle drei zusammen erklärten das Dashboard-Bild „2 051 von 2 051 Produkten unvollständig":

1. **Jedes importierte ETIM-Merkmal war Pflicht.** `etim_ixf_to_mef.py` schrieb hart `"required": True` — das IXF-Format trägt **kein** Pflichtkennzeichen (`Class > Feature` hat nur `FeatureCode`, `UnitCode`, `OrderNumber`, `Values`; die Wörter „Mandatory“/„Optional“ kommen im 68-MB-Export nur in Beschreibungen vor, geprüft 2026-09-17). Folge: **76 625 von 76 655** `category_attribute`-Zeilen waren Pflicht, und jedes klassifizierte Objekt wurde gegen die komplette Merkmalsliste seiner Klasse bewertet (66 fehlende Merkmale pro Produkt ⇒ ~11 % Vollständigkeit). Jetzt werden importierte Merkmale **optional** geschrieben (`--features-required` stellt das alte Verhalten her); neu erzeugt und gegen das installierte Paket gediffed: **identisch außer `required: true → false` für alle 76 625 Zeilen**. Wer Pflicht braucht, markiert sie pro Knoten (`PATCH /categories/{id}/attributes/{attribute}`).
2. **Der Fast-Path des Batch-Pfads fragte die falsche Menge.** `completeness::compute_batch` beendete die Bewertung mit 100 %, sobald die **vererbbaren** fehlenden Namen leer waren — ein Objekt, dessen einzige Lücken **nicht** vererbbare Klassen-Merkmale waren, galt damit als vollständig. `GET /objects` meldete 100 % für Varianten, die `GET /objects/{id}` mit 40 % und `GET /objects/{id}/category-attributes` mit drei fehlenden Pflichtmerkmalen zeigte (2 376 Varianten betroffen, nur die 212 mit einer vererbbaren Lücke wurden gezählt). Jetzt teilen sich Einzel- und Batch-Pfad **ein** Prädikat (`all_required_present`: alle Pflichtnamen müssen im Objekt selbst liegen), unit-getestet. Merksatz: Ein Fast-Path darf nur dann „vollständig“ sagen, wenn er dieselbe Bedingung prüft wie der langsame Pfad — sonst widerspricht das Dashboard der Detailansicht.
3. **Zwei Altbestands-Definitionen machten jedes Produkt unvollständig (2026-09-17 nachgezogen).** Nach (1) und (2) blieb die Kachel „2 051 unvollständig (Produkt)" = **alle** Produkte bei 77 %: `GET /objects/{id}` meldete `missing_required: ["height", "width"]`, obwohl Klassen-Merkmale vollständig waren. Die Ursache lag nicht in den Daten, sondern in zwei Definitionen aus 2026-09-09 (`height`/`width`, Einheit Meter, eine mit der Beschreibung „re@width", beide nicht im Modell), die `required_types: ["product"]` trugen, während Modell und Generator `height_mm`/`width_mm` (mm) verwenden. Merksatz: `missing_required` des Kerndetails gehört zu **jeder** Vollständigkeitsdiagnose — die Anforderung kann aus einer Definition stammen, die mit dem aktuellen Modell nichts mehr zu tun hat; `required_types` ist pro Typ der stärkste Hebel auf die Kachel. Die Zeilen waren nur durch eine Datenreparatur zu retten (SurrealDB verweigert Updates einer Zeile ohne das später ergänzte Feld `reference_data`, der Kern meldet das als retrybaren `409`; `height` war zusätzlich `is_key: true` bei `type: number`, was die Validierung ablehnt) — danach ist die Kachel wieder aussagekräftig: **230 Produkte / 21 Varianten** mit den bewussten `weight_kg`/`material`/`gtin`-Lücken.

## Eine Dimension bestimmt den Baum — Binding statt Raten (2026-09-17)

**Befund (Rückmeldung aus der Praxis).** Im Produktformular wurde im Abschnitt **Baugruppen-Zuordnung** genau das angeboten, was zur **ETIM-Klassen-Zuordnung** gehört: eine Liste aus `EC000001 · etim_class`, `EC000003 · etim_class`, … und **nichts Auswählbares**. Dasselbe Bild im Abschnitt der primären Kategorien. Ursache: Der Objekt-Picker kannte nur den **Scope** (`scope=taxonomy`, seit 2026-09-14), nicht den **Typ** — er hing am Universal-Root, dessen Taxonomiekinder *alle* Bäume (Kategorien, Baugruppen, ETIM-Klassen) sind, und `limit=100` schnitt daraus die erste Seite: mit installiertem ETIM-Standard **100 `etim_class`-Knoten** (gemessen: `GET /objects/object_root/children?scope=taxonomy&limit=100` → `{etim_class: 100}`; `type=category` → 1 Wurzel, `type=part_group` → 12 Wurzeln). Die Bindung zwischen Dimension und Taxonomie war also vorhanden, wurde aber nicht benutzt.

**Der Vertrag, der das entscheidet (nichts Neues im Kern).** Eine Dimension mit expliziter `target_types`-Liste ist an genau diese Taxonomie(n) **gebunden** — der Kern lehnt jedes andere Ziel mit `400` ab (`check_target_types`, `references.rs`). Ohne Liste ist sie **generisch** und darf auf jeden `taxonomy: true`-Typ zeigen. Genau daraus leitet die Oberfläche jetzt ab (reine Helfer in `meadow/frontend/relation_roles.py`, 9 Tests in `tests/test_dimension_taxonomy.py`):

| Fall | Baum, den die UI zeigt | Umschalter |
| --- | --- | --- |
| `target_types: ["part_group"]` (gebunden) | genau dieser Baum (`?scope=taxonomy&type=part_group`) | **deaktiviert**, Hinweis „Durch die Dimension festgelegt“ |
| `target_types: []` (generisch, z. B. die System-Dimension) | der Baum der **gewählten** Taxonomie | dem Nutzer — Auswahl bei der Zuordnung |
| Ziele sind keine Taxonomieknoten | Kompositionsbaum | entfällt |

**Umgesetzt:**

- **Picker (`object_picker.py`)** lädt immer **einen** Baum: sind alle erlaubten Ziele Taxonomietypen, geht `type=<Typ>` mit (`taxonomy_choices`); bei mehreren erlaubten Typen bekommt der Dialog einen **Taxonomie-Umschalter** (System-Taxonomie zuerst), dessen Wechsel den Baum nachlädt. Der **Such-Tab** sucht in genau diesem Baum, und der **Dialogtitel** nennt die Dimension („Baugruppen-Zuordnung wählen“ statt überall „Kategorien wählen“); die Abschnittszeile über dem Knopf nennt die Taxonomie, an die die Dimension gebunden ist.
- **Formular** übergibt die Vorauswahl aus dem, was das Objekt in dieser Dimension schon trägt (generische Dimension → Baum mit Daten statt leerer Standardbaum).
- **Grid und `/categories`** koppeln die beiden Umschalter: eine gebundene Dimension schaltet die Taxonomie mit und deaktiviert den Umschalter (`category_dimension_bindings`); eine generische gibt ihn frei. Damit ist die Kombination „Dimension A + Baum B“ nicht mehr möglich, die nur leere Zähler erzeugen konnte.
- **Kein neues Feld, keine „Default-Taxonomie“:** die Bindung ist `target_types`. Eine zusätzliche Voreinstellung wäre ein zweiter Ort für dieselbe Aussage.

**Live nachgewiesen (2026-09-17, Playwright gegen die Dev-Instanz):** Formular → Kategorien: Titel „Kategorien wählen“, Umschalter vorhanden (Kategorie), Baum `Sortiment · category`, **kein** `EC…`; Umschalten auf „Baugruppe“ lädt `Werkstattbedarf/Innenraum/Zubehör · part_group`. ETIM-Abschnitt: Titel „ETIM-Klassen-Zuordnung wählen“, **kein** Umschalter, Baum `EC000001…`. Baugruppen-Abschnitt: Titel „Baugruppen-Zuordnung wählen“, **kein** Umschalter, Baum `Werkstattbedarf · part_group …`. Grid: Dimension „Baugruppen-Zuordnung“ → Taxonomie springt auf „Baugruppe“, Umschalter deaktiviert, Baum mit Zählern (`410/160/86`); zurück auf die System-Dimension → Umschalter wieder aktiv. `/categories` identisch.

**Nebenbefund (behoben):** `categories.py::save_name` rief `_category_objects(client, token, locale)` ohne den Pflichtparameter `node_types` auf — das Umbenennen einer Kategorie lief in einen `TypeError`. Jetzt werden wie an den drei anderen Aufrufstellen `nodes["all"]` übergeben.

**Stolperstein für die nächste UI-Arbeit:** NiceGUI übergibt das Event nur, wenn der Handler ein Argument **ohne Default** erwartet (`helpers.expects_arguments`). `async def handler(event=None)` wird **ohne** Argument aufgerufen — der Umschalter lief deshalb zunächst ins Leere (`event=None`). Handler in diesem Projekt nehmen den Parameter ohne Default (oder sind ein Lambda).

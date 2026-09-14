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
- `/categories`: **Taxonomie**-Umschalter (Knotentyp; Anlegen/Verschieben folgt ihm, ein Knoten bleibt in seiner Taxonomie) **und** **Dimension**-Umschalter (Relationstyp; Zähler + „Im Grid anzeigen“).
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
- **Objekt-Picker**: leitet den Scope aus den erlaubten Zieltypen ab — sind **alle** Zieltypen Taxonomien, wird der Taxonomiebaum gezeigt, sonst die Komposition (vorher: alle Root-Kinder, also Produkte *und* Klassen in jedem Picker).
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
| `completeness=false` | keinen Vollständigkeits-Wert je Treffer berechnen (Pflicht für Aufrufer mit vielen Treffern) |

Die Suche tippt **jeder** Aufrufer selbst an: der Referenz-Picker und die Filterleiste des Grids (`SEARCH_LIMIT` = 100, `debounce` 400 ms) nutzen weiter nur den Volltext — ein Teil-Code findet dort also nichts. Das ist eine **offene Lücke** (dieselben zwei Parameter würden sie schließen), kein Versehen.

**Weitere offene Punkte:** die Trefferliste ist flach (keine Hierarchie; ein „im Baum aufklappen“ bräuchte einen Ancestors-Endpunkt), und der Filter wirkt auf die Ansicht, in der er getippt wurde (Umschalten Struktur ↔ Kategorie rendert den Baum ungefiltert, der Suchtext bleibt im Feld stehen).

**Umsetzung:** `meadow/frontend/taxonomy_tree.py` (rein, 8 Tests in `tests/test_taxonomy_tree.py`), Einbindung in `grid.py` (Kategorie-Ansicht von `/products`) und `categories.py` (`/categories`); Kern: `api/objects.rs` (`picker` mit `code_prefix`/`completeness`, `code_prefix_hits`, `prefix_upper_bound` — 2 Unit-Tests) und `language.rs::resolve_object_label` (Name → `code` → Objekttyp).

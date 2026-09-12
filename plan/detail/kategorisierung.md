# Kategorisierung und Gruppierung

- **Hauptdokument:** Abschnitt 4 ([Kategorisierung und Gruppierung](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 2, 9
- **Stand:** umgesetzt (Dimensionen, Taxonomie-Typen Stufe A, Klassen-Merkmale Stufe B); offen bleiben Import/Versionierung der Standard-Metadaten und das BMEcat/xChange-Export-Mapping

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

**Offen (Folgearbeit, nicht Teil von Stufe B):** Import/Versionierung der Standard-Metadaten (`code`/`version`/`source` als Attribute + MCF/Connector-Import, Read-only-Kennzeichnung) und das **BMEcat/xChange-Export-Mapping** im Export-Wizard.

**Nicht-Ziele (bewusst):** keine klassen-spezifische Einschraenkung der erlaubten Werte (erlaubte Werte bleiben an der Attributdefinition/Enum-Set, wie in ETIM), kein zweiter Merkmals-Mechanismus ausserhalb der Attributdefinitionen, keine vererbenden Kategorie-Kanten.

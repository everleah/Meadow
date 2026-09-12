# Kategorisierung und Gruppierung

- **Hauptdokument:** Abschnitt 4 ([Kategorisierung und Gruppierung](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 2, 9
- **Stand:** Entwurf

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
3. **Vorbereitung für Standards (z. B. ETIM):** Kategorien können später um ein Attribut `standard_code` ergänzt werden, um externe Klassifikationssysteme abzubilden. Die Zuordnung erfolgt dann weiterhin über Relationen, zusätzlich könnte ein Mapping auf Standardklassen entstehen. Dies ist bewusst nicht Teil der ersten Version, aber das Modell ist dafür offen.

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

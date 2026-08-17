# Datenwertvererbung

- **Hauptdokument:** Abschnitt 4.5 ([Datenwertvererbung](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 1–3
- **Stand:** Entwurf

## Datenwertvererbung über die Kompositionskette

Meadow PIM unterstützt die **Vererbung von Attributwerten** entlang der Kompositionskette (`depends_on`). Dies ist besonders relevant für Gruppierungsobjekte (z. B. ein Produkt) und darunterliegende Varianten (z. B. Produktvarianten). Dabei wird ein Wert **nicht kopiert**, sondern dynamisch aufgelöst – Änderungen am übergeordneten Objekt wirken sich sofort auf alle erbenden Kindobjekte aus, sofern diese keinen eigenen Wert gesetzt haben.

> **Hinweis:** Grundlagen des Relationenmodells (Komposition `depends_on`, Universal-Root, Aggregationen, Re-Parenting, Kaskade) stehen in [relationen.md](relationen.md).

**Zielsetzung:**

- Gemeinsame Attribute zwischen Eltern- und Kindobjekten müssen nur an einer Stelle gepflegt werden.
- Die Vererbung ist **echt**: Es wird zur Lesezeit der effektive Wert berechnet, nicht beim Schreiben dupliziert.
- Es muss möglich sein, die Vererbung gezielt zu unterbrechen, indem am Kind ein **leerer Wert** gesetzt wird (Unterscheidung zwischen „nicht gepflegt“ und „leer“).

**Modellierung der Vererbungsrelation:**

- Jedes Objekt hat **genau eine** Kompositionskante vom Typ `depends_on` (Richtung: Kind verweist auf Elternteil). Die Vererbung folgt ausschließlich dieser Kette bis zum Universal-Root. Aggregationen (`category_member`, `collection_member`, `related`) haben keine Vererbungswirkung.

  ```json
  {
    "id": "ref_dep_001",
    "from": "variant_1",             // Kindobjekt
    "to": "product_1",               // Elternobjekt
    "definition_id": "rel_depends_on",
    "type": "depends_on",            // einziger Kompositionstyp
    "attributes": {}
  }
  ```

- Da es **genau einen** Elternteil gibt, existieren keine Prioritäten oder Scope-Typen mehr; die Auflösung ist deterministisch über den einzigen Pfad nach oben.
- Über SurrealDB-Graphfunktionen wird entlang dieser Relationen effizient traversiert. Da die Relationen in der generischen `reference`-Tabelle liegen, wird die Definition als Bedingung angegeben: `->(reference WHERE definition_id = 'rel_depends_on')->object`.

**Attributkategorien:**

- In der `attribute_definition` wird ein Feld `inheritable` ergänzt:
  - `true`: Das Attribut ist vererbungsfähig und wird bei der Auflösung berücksichtigt.
  - `false` (Standard): Das Attribut ist **nicht vererbbar** – es wird ausschließlich am Objekt selbst ausgelesen.

  ```json
  {
    "id": "attr_def_001",
    "name": "price",
    "type": "number",
    "group_id": "attr_group_01",
    "inheritable": true
  }
  ```

- Dadurch kann die Performance gezielt gesteuert werden: Nur als `inheritable` markierte Attribute erfordern eine Graph-Traversierung.

**Auflösungslogik (Lesen eines Objekts):**

1. Für jedes Attribut wird zuerst der **eigene Wert** des Objekts geprüft.
2. Ist der Schlüssel im `attributes`-Objekt **vorhanden** (auch mit `null` oder leerem String), gilt er als gesetzt – die Vererbung greift **nicht**. Ein leerer Wert unterbricht also bewusst die Vererbung.
3. Fehlt der Schlüssel („nicht gepflegt“), wird entlang der `depends_on`-Kette aufwärts gesucht. Der nächstgelegene Vorfahre, der das Attribut gesetzt hat, liefert den Wert.
4. Wird in der gesamten Kette (bis einschließlich Universal-Root) kein gesetzter Wert gefunden, bleibt das Attribut für das Kind **leer/nicht vorhanden**.

**Beispiel:**

- Objekt `product_1` (Eltern) hat:
  ```json
  {
    "attributes": {
      "brand": "Acme",
      "material": "Edelstahl"
    }
  }
  ```
- Objekt `variant_1` (Kind) hat:
  ```json
  {
    "attributes": {
      "material": null          // leer gesetzt => unterbricht Vererbung
    }
  }
  ```
- Ergebnis für `variant_1`:
  - `brand` wird vererbt → `"Acme"`
  - `material` ist explizit leer → bleibt `null`
  - Andere Attribute des Kindes werden unverändert übernommen.

**Kombination mit dem Sprachbaum:**

- Die sprachliche Auflösung (z. B. `name` mit `en-GB`-Fallback) erfolgt **zuerst innerhalb jedes einzelnen Objekts**.
- Anschließend wird die Objektvererbung angewendet: Wenn ein Kind keinen sprachabhängigen Wert besitzt, erbt es den bereits sprachlich aufgelösten Wert des Elternobjekts.
- Dadurch sind beide Mechanismen entkoppelt und bleiben verständlich.

**Technische Umsetzung in SurrealDB:**

- Indizes auf `reference` (insbesondere `definition_id` und `from`) beschleunigen die Traversierung.
- Die Vererbung wird über SurrealQL-Abfragen realisiert, z. B.:
  ```sql
  SELECT ->(reference WHERE definition_id = 'rel_depends_on')->object.attributes.brand AS inherited_brand
  FROM object:variant_1
  ```
  oder durch schrittweises Aufsteigen in der Applikationslogik.
- Optional kann ein Cache (z. B. Redis) die aufgelösten Werte für häufig gelesene Objekte zwischenspeichern. Für die meisten KMU-Szenarien ist das jedoch nicht erforderlich, da die Hierarchietiefen gering sind.

**API-Verhalten:**

- Beim Abruf eines Objekts über `GET /objects/{id}` kann der Client mit dem Query-Parameter `?resolve_inheritance=true` angeben, dass die effektiven Werte (inkl. Vererbung) geliefert werden sollen.
- Ohne diesen Parameter werden nur die am Objekt direkt gespeicherten Werte zurückgegeben (Rohdaten).
- Für die UI und Exporte wird standardmäßig die aufgelöste Sicht verwendet.

**Vorteile:**

- Reduzierte Datenpflege: Gemeinsame Werte werden zentral am Elternobjekt gepflegt.
- Konsistenz: Änderungen am Elternobjekt propagieren sofort zu allen Kindobjekten.
- Flexibilität: Durch explizites Setzen eines leeren Wertes kann die Vererbung punktuell ausgehebelt werden.
- Performance: Nur vererbungsfähige Attribute werden in die Graph-Traversierung einbezogen.

**Abgrenzung zu Aggregationen:**

- Aggregationsrelationen (`category_member`, `collection_member`, `related`) dienen rein der Verknüpfung und haben **keine** Vererbungswirkung.
- Nur die Kompositionskette `depends_on` aktiviert die beschriebene Wertvererbung.

## Konfliktlösung

Durch das Ein-Eltern-Modell existiert **genau eine** Vererbungsquelle pro Objekt. Konflikte durch mehrere Quellen entfallen damit.

- Die einzige Überschreibungsmöglichkeit ist ein **expliziter Wert am Kind** (inkl. leerem Wert zur Unterbrechung).
- Die UI zeigt bei vererbten Werten an, **von welchem Vorfahren** der Wert stammt (`from_object` in der aufgelösten Antwort), damit die Herkunft nachvollziehbar bleibt.
- Wird ein Objekt umgehängt (Re-Parenting, siehe [relationen.md](relationen.md)), lösen sich die Werte des Teilbaums ab sofort gegen die **neuen** Vorfahren auf – dynamisch, ohne Datenkopie.

Durch diesen Ansatz bleibt die Vererbung einfach, transparent und beherrschbar.

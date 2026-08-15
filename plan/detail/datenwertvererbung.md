# Datenwertvererbung & Konfliktlösung

- **Hauptdokument:** Abschnitte 4.5 und 4.7 ([Datenwertvererbung](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 1–3, 9
- **Stand:** Entwurf

## Datenwertvererbung über Objektrelationen

Meadow PIM unterstützt die **Vererbung von Attributwerten** entlang hierarchischer Objektbeziehungen. Dies ist besonders relevant für Gruppierungsobjekte (z. B. ein Produkt) und darunterliegende Varianten (z. B. Produktvarianten). Dabei wird ein Wert **nicht kopiert**, sondern dynamisch aufgelöst – Änderungen am übergeordneten Objekt wirken sich sofort auf alle erbenden Kindobjekte aus, sofern diese keinen eigenen Wert gesetzt haben.

**Zielsetzung:**

- Gemeinsame Attribute zwischen Eltern- und Kindobjekten müssen nur an einer Stelle gepflegt werden.
- Die Vererbung ist **echt**: Es wird zur Lesezeit der effektive Wert berechnet, nicht beim Schreiben dupliziert.
- Es muss möglich sein, die Vererbung gezielt zu unterbrechen, indem am Kind ein **leerer Wert** gesetzt wird (Unterscheidung zwischen „nicht gepflegt“ und „leer“).

**Modellierung der Vererbungsrelation:**

- Die bestehende, leichtgewichtige `reference`-Tabelle bleibt für allgemeine Verknüpfungen erhalten.
- Für die Vererbung wird ein eigener Relationstyp `inherits_from` verwendet. Die Richtung ist definiert als: **Kind verweist auf Elternteil**.

  ```json
  {
    "id": "ref_inh_001",
    "from": "variant_1",             // Kindobjekt
    "to": "product_1",               // Elternobjekt
    "type": "inherits_from",         // spezieller Vererbungstyp
    "attributes": {
      "priority": 1                  // optional: Reihenfolge bei mehreren Eltern
    }
  }
  ```

- Über SurrealDB-Graphfunktionen kann entlang dieser Relationen effizient traversiert werden. Da die Relationen in der generischen `reference`-Tabelle liegen, wird der Typ als Bedingung angegeben: `->(reference WHERE type = 'inherits_from')->object`.
- Ein Objekt kann mehrere `inherits_from`-Relationen besitzen. In diesem Fall gilt die durch `priority` definierte Reihenfolge (niedriger Wert = höhere Priorität). Ist keine Priorität gesetzt, wird die zuerst gefundene Relation verwendet.

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
3. Fehlt der Schlüssel („nicht gepflegt“), wird entlang der `inherits_from`-Kette aufwärts gesucht. Der nächstgelegene Elternknoten, der das Attribut gesetzt hat, liefert den Wert.
4. Wird kein gesetzter Wert in der gesamten Kette gefunden, bleibt das Attribut für das Kind **leer/nicht vorhanden**.

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

- Indizes auf `reference` (insbesondere `type` und `from`) beschleunigen die Traversierung.
- Die Vererbung wird über SurrealQL-Abfragen realisiert, z. B.:
  ```sql
  SELECT ->(reference WHERE type = 'inherits_from')->object.attributes.brand AS inherited_brand
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

**Abgrenzung zu allgemeinen Relationen:**

- Normale `reference`-Relationen (z. B. `related`, `parent`, `variant`) dienen rein der Verknüpfung und haben **keine** Vererbungswirkung.
- Nur Relationen vom Typ `inherits_from` aktivieren die beschriebene Wertvererbung.

## Konfliktlösung bei mehrfacher Vererbung

Durch die parallele Einordnung von Objekten in mehrere Strukturen (Pflegehierarchie, Kategorien, spätere Klassifizierung) kann der Fall auftreten, dass für dasselbe Attribut **mehrere potenzielle Erbquellen** existieren. Diese liefern dann möglicherweise unterschiedliche Werte. Meadow PIM löst solche Konflikte mit einer Kombination aus **deterministischen Regeln** und **manuellen Eingriffsmöglichkeiten**.

**Grundsatz:**

- Konflikte werden **nicht verhindert**, sondern kontrolliert aufgelöst.
- Die Auflösung erfolgt **zur Lesezeit**, ohne Werte zu duplizieren.
- Der Benutzer kann jederzeit einen **expliziten Wert am Kind** setzen, um die Vererbung gezielt zu überschreiben – das ist die mächtigste und üblichste Methode.

**1. Deterministische Prioritätsregeln**

Jede `inherits_from`-Relation kann eine Priorität erhalten:

```json
{
  "id": "ref_inh_001",
  "from": "variant_1",
  "to": "product_1",
  "type": "inherits_from",
  "attributes": {
    "priority": 10,
    "scope": "maintenance"
  }
}
```

- **`priority`** (niedriger Wert = höhere Priorität) legt die Reihenfolge fest, wenn mehrere Relationen desselben Geltungsbereichs existieren.
- **`scope`** unterscheidet die Art der Beziehung, z. B. `maintenance` (Pflegestruktur), `category` (Kategorisierung) oder `classification` (spätere Standards).
- Bei der Auflösung wird zuerst die Relation mit dem **kleinsten `priority`-Wert** im **höchstpriorisierten `scope`** verwendet. Die `scope`-Reihenfolge ist systemweit definiert (z. B. `maintenance` vor `category` vor `classification`).
- Sind mehrere Relationen gleich priorisiert, entscheidet die zuerst gefundene (stabile Reihenfolge, z. B. nach `id`).

**2. Manuelle Konfliktbehandlung in der UI**

Auch mit Prioritätsregeln kann es für Benutzer wichtig sein, die tatsächliche Quelle eines geerbten Wertes zu verstehen und bei Bedarf zu übersteuern.

- Die Oberfläche zeigt bei vererbten Werten an, **von welchem Elternobjekt** der Wert stammt.
- Besteht ein Konflikt (mehrere Quellen liefern unterschiedliche Werte), wird dies visuell markiert (z. B. Warnsymbol).
- Der Benutzer hat dann zwei Möglichkeiten:
  1. **Expliziten Wert am Kind setzen** – damit greift die Vererbung für dieses Attribut nicht mehr (Standard-Override).
  2. **Bevorzugte Quelle wählen** – dies kann als dauerhafte Einstellung am Kind gespeichert werden, z. B. in einem Attribut `preferred_inheritance_source`, das die Relation-ID oder das Quellobjekt angibt. Zukünftige Lesevorgänge berücksichtigen diese Präferenz.

**3. Vererbungsunterbrechung auf Relationsebene**

Falls eine bestimmte `inherits_from`-Relation für einzelne Attribute oder generell nicht vererben soll, kann das über ein Flag an der Relation gesteuert werden:

```json
{
  "attributes": {
    "priority": 5,
    "disabled_for": ["price", "description"]   // optional: Liste von Attributen, die nicht vererbt werden
  }
}
```

- `disabled_for` verhindert die Vererbung der genannten Attribute über diese Relation.
- Ist die Liste leer oder nicht vorhanden, werden alle vererbbaren Attribute berücksichtigt.

**4. Transparenz und Audit**

- Jede Änderung an der Vererbungskonfiguration (Priorität, Deaktivierung, bevorzugte Quelle) wird im `audit_log` protokolliert.
- In der API-Antwort kann das aufgelöste Attribut um Metadaten ergänzt werden, z. B.:

  ```json
  {
    "brand": {
      "value": "Acme",
      "source": "inherited",
      "from_object": "product_1",
      "resolved_locale": "en"
    }
  }
  ```

  Dadurch bleibt die Herkunft nachvollziehbar.

**Empfehlung für Meadow PIM:**

- In der **ersten Version** genügt eine einfache Prioritätsregel (`priority` und `scope`) sowie die Möglichkeit, am Kind explizite Werte zu setzen. Das deckt die meisten KMU-Szenarien ab.
- Die **UI-Konfliktanzeige** wird früh mitgeplant, kann aber in einer zweiten Ausbaustufe verfeinert werden.
- Die Speicherung einer bevorzugten Quelle (`preferred_inheritance_source`) ist optional und kann später ergänzt werden, wenn die Praxis es erfordert.

Durch diesen Ansatz bleibt die Vererbung flexibel, transparent und beherrschbar – ohne die Komplexität unnötig zu erhöhen.

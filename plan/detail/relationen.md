# Relationen & Komposition

- **Hauptdokument:** Abschnitt 4 ([Datenmodell](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 1–3, 9
- **Stand:** Entwurf

## Grundprinzip

Meadow PIM unterscheidet zwei Arten von Relationen zwischen Objekten:

- **Komposition (1:n inkl. 1:1):** Ein Objekt ist **abhängig von** genau einem anderen Objekt. Die Kompositionen bilden einen strikten, zyklenfreien Baum. Sie aktivieren **Wertvererbung** und **Löschkaskade**.
- **Aggregation (n:m):** Lose Verknüpfung zwischen Objekten ohne Vererbungswirkung und ohne Kaskade.

Der Relationstyp wird über die Tabelle `relation_definition` spezifiziert; jede Relation in `reference` referenziert eine Definition (`definition_id`).

`reference` ist eine **native SurrealDB-RELATION-Tabelle**: `RELATE quelle->reference->ziel` erzeugt `in` = Quelle und `out` = Ziel (beides volle Record-IDs). Für `depends_on` gilt die Richtung **Kind → Elternteil**, also `RELATE kind->reference->elternteil` ⇒ `in` = Kind, `out` = Elternteil. Details in [datenmodell.md](datenmodell.md) („Record-IDs & Querverweise“).

## Komposition (`depends_on`)

- Es gibt **genau einen** Kompositionstyp: `depends_on` („ich bin abhängig von“). Die Richtung ist **Kind → Elternteil**.
- **Ein-Eltern-Invariante:** Jedes Objekt hat **genau eine** `depends_on`-Kante. Dadurch ist die Komposition strukturell ein Baum und garantiert zyklenfrei.
- **Universal-Root:** Der Root ist ein reales Objekt (`type: "root"`, feste ID, z. B. `object_root`) und die Wurzel des Kompositionsbaums. Top-Level-Objekte hängen direkt am Root. Der Root ist:
  - sichtbar (einziger navigierbarer Baumknoten „Alle Objekte“),
  - strukturell unveränderlich (nicht löschbar, nicht umhängbar, kein Typwechsel),
  - mit editierbaren Attributen für **globale Defaults** (nur Admin/Superadmin; Standard: keine Werte, damit nichts ungewollt vererbt wird).
- **Kind-Anzahl:** Ein Elternteil kann 0..n Kinder haben (1:n, inkl. 1:1). Es gibt keine Obergrenze.

### Löschen (Kaskade)

- `DELETE` ist ein **Soft Delete** und kaskadiert über den gesamten Kompositionsteilbaum (`depends_on`). Kinder werden nicht verwaist.
- Die UI zeigt vor dem Löschen eine **Warnung mit der Anzahl betroffener Objekte**; über die API trägt der Aufrufer die Verantwortung (keine Bestätigung).
- Aggregations-Kanten kaskadieren **nie**: Das Löschen eines Objekts entfernt nur dessen Aggregationskanten, nicht die verbundenen Objekte.
- Der Universal-Root ist **unlöschbar**.

### Umhängen (Re-Parenting)

- Die `depends_on`-Kante ist nach der Anlage **änderbar**; dadurch wird ein Objekt inkl. Teilbaum an einen anderen Elternteil verschoben.
- Zwingende Prüfungen beim Umhängen:
  - **Zyklenschutz:** Ein Objekt darf nicht in seinen eigenen Teilbaum gehängt werden.
  - **Typrestriktionen:** Der neue Elternteil muss den `target_types` der `depends_on`-Definition (bzw. einer restriktiven Definition) genügen.
- Der Umzug ist **atomar** und wird im `audit_log` protokolliert.
- **Wirkung auf die Vererbung:** Geerbte Werte des Teilbaums lösen sich danach gegen die neuen Vorfahren auf (dynamisch, ohne Datenkopie).
- In der UI ist beim Verschieben **keine** Bestätigung erforderlich (anders als beim Löschen).

### Vererbung

- Die Datenwertvererbung folgt **ausschließlich** der `depends_on`-Kette bis zum Universal-Root. Details in [datenwertvererbung.md](datenwertvererbung.md).

## Aggregation (n:m)

- Aggregationen verbinden Objekte lose (n:m). Ein Objekt kann an beliebig vielen Aggregationen teilnehmen.
- Eingebaute Aggregationstypen:
  - `category_member` → Kategorisierung (siehe [kategorisierung.md](kategorisierung.md))
  - `collection_member` → materialisierte Collections (Phase 9, siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md))
  - `related` → allgemeine Verknüpfung
- Aggregationen haben **keine** Vererbungswirkung und **keine** Löschkaskade.
- Kanten-Metadaten bleiben möglich, z. B. `position` und `context` bei `category_member`.
- **Benutzerdefinierte** Relationstypen sind **immer** Aggregationen (Phase 9) und können nie Komposition sein – dadurch bleibt die Ein-Eltern-Invariante strukturell garantiert.

## Tabelle `relation_definition`

```json
{
    "id": "rel_depends_on",
    "name": "depends_on",            // eindeutiger Name des Relationstyps
    "kind": "composition",           // composition | aggregation
    "source_types": [],              // optional: erlaubte Quell-Objekttypen (leer = alle)
    "target_types": [],              // optional: erlaubte Ziel-Objekttypen (leer = alle)
    "label": { "de": "Abhängig von", "en": "Depends on" },
    "system": true,                  // eingebaut (true) oder benutzerdefiniert (false)
    "created_at": "2026-08-17T08:00:00Z",
    "updated_at": "2026-08-17T08:00:00Z"
}
```

- `kind` bestimmt die Semantik (siehe oben). Es darf **genau eine** Definition mit `kind: "composition"` geben.
- `source_types` / `target_types` schränken die erlaubten Objekttypen ein; **leer = alle Typen erlaubt**. Die Prüfung erfolgt **beim Anlegen jeder Relation** (Phase 1–2), nicht nur deklarativ.
- `system: true` kennzeichnet eingebaute Typen (Seed in Phase 1–2): `depends_on` (Komposition) sowie `category_member`, `collection_member`, `related` (Aggregation).
- Benutzerdefinierte Relationstypen (`system: false`, Phase 9) werden über die API angelegt und sind immer Aggregationen.

## Altlasten-Mapping (entfernt)

Das frühere Modell kannte weitere Typen und Kanten-Attribute, die durch das Relationenmodell **ersatzlos entfallen**:

| Bisher | Status |
|---|---|
| `inherits_from` | → ersetzt durch `depends_on` |
| `parent` | entfällt (durch `depends_on` abgedeckt) |
| `variant` | entfällt (Varianten = Kind via `depends_on`) |
| `priority` (Kante) | entfällt (genau ein Elternteil) |
| `scope` (Kante) | entfällt (genau ein Vererbungspfad) |
| `preferred_inheritance_source` | entfällt (genau eine Quelle) |
| `disabled_for` (Kante) | entfällt im MVP (optional spätere Ausbaustufe) |

## Invarianten (Zusammenfassung)

1. Jedes Objekt hat genau eine `depends_on`-Kante; die Komposition ist ein zyklenfreier Baum mit dem Universal-Root als Wurzel.
2. Es gibt genau eine Kompositions-Definition; alle anderen Relationstypen sind Aggregationen.
3. Vererbung und Löschkaskade wirken ausschließlich über `depends_on`.
4. Aggregationen kaskadieren nie und vererben nicht.
5. Der Universal-Root ist unlöschbar und strukturell unveränderlich.
6. Relationen werden beim Anlegen gegen die Typrestriktionen der `relation_definition` geprüft.

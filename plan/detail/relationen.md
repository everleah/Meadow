# Relationen & Komposition

- **Hauptdokument:** Abschnitt 4 ([Datenmodell](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 1–3, 9
- **Stand:** Entwurf (Komposition & Aggregation umgesetzt; Rollen-Klassifizierung `role` geplant, siehe unten)

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
  - strukturell unveränderlich (nicht löschbar, nicht umhängbar, kein Typwechsel),
  - mit editierbaren Attributen für **globale Defaults** (nur Admin/Superadmin; Standard: keine Werte, damit nichts ungewollt vererbt wird).
  - **in der UI als reiner Container-Knoten verborgen** (übliche Darstellung): Der Strukturbaum im Grid ([frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md)) zeigt die Kinder des Root auf Top-Level, ohne einen separaten „Struktur · root“-Eintrag; das Grid lädt standardmäßig alle Objekte (Auswahl eines Knotens filtert auf den Teilbaum), die globalen Root-Defaults werden nicht im Produkt-Grid gepflegt.
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
- **Technische Umsetzung:** SurrealDB kann die Richtung einer bestehenden RELATION-Kante nicht per `UPDATE` ändern; das Umhängen löscht die alte `depends_on`-Kante und legt die neue in derselben Transaktion an (`DELETE` + `RELATE`).
- **Wirkung auf die Vererbung:** Geerbte Werte des Teilbaums lösen sich danach gegen die neuen Vorfahren auf (dynamisch, ohne Datenkopie).
- In der UI ist beim Verschieben **keine** Bestätigung erforderlich (anders als beim Löschen).
- **Die Kompositionskante ist nicht direkt löschbar (2026-09-17):** `DELETE /references/{id}` nimmt seit jeher *Aggregations*-Kanten an, hat aber auch `rel_depends_on` akzeptiert — ein Löschen per Kante-ID hinterlässt **keine** Spur (kein Audit, kein Event) und nimmt dem Objekt seinen Elternteil: es verschwindet aus dem Strukturbaum und wird von der Löschkaskade nicht mehr erreicht. Genau in diesem Zustand wurden am 2026-09-17 **81 Demo-Varianten** gefunden (65 davon in der aktuellen Datenmenge; der MEF-Import konnte sie nicht heilen, weil er `depends_on` nur *umhängt*, siehe unten). Der Endpunkt lehnt die Kompositionskante jetzt mit `400` ab (`is_composition_edge_value`); sie wird entweder durch Umhängen ersetzt oder mit dem Objekt gelöscht.
- **Import-Semantik (2026-09-17):** Ein MEF-`depends_on` ist ein **Umhängen** (das Objekt hängt nach der Anlage am Universal-Root). Hat ein Objekt jedoch **keine** Kompositionskante (Zustand oben), ist ein Umhängen unmöglich — der Kern antwortet `400 "object has no composition parent"`. Weil ein MEF-Dokument den *gewünschten* Elternteil beschreibt, wendet der Import-Worker solche Einträge als **Initial-Anhang** an (Kompositionskante über den `references`-Teil von `POST /references/batch`) statt sie dauerhaft als Fehler zu melden.

### Vererbung

- Die Datenwertvererbung folgt **ausschließlich** der `depends_on`-Kette bis zum Universal-Root. Details in [datenwertvererbung.md](datenwertvererbung.md).

## Aggregation (n:m)

- Aggregationen verbinden Objekte lose (n:m). Ein Objekt kann an beliebig vielen Aggregationen teilnehmen.
- Eingebaute Aggregationstypen:
  - `category_member` → Kategorisierung (siehe [kategorisierung.md](kategorisierung.md))
  - `collection_member` → materialisierte Collections (Phase 9, siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md))
  - `related` → allgemeine Verknüpfung
- Aggregationen haben **keine** Vererbungswirkung und **keine** Löschkaskade.
- Die **Bedeutung** einer Aggregation ist über `role` klassifiziert (Kategorie/Sammlung/Verknüpfung, s. u.) – `kind` sagt nur „ohne Vererbung/Kaskade“.
- Kanten-Metadaten bleiben möglich, z. B. `position` und `context` bei `category_member`.
- **Benutzerdefinierte** Relationstypen sind **immer** Aggregationen (Phase 9) und können nie Komposition sein – dadurch bleibt die Ein-Eltern-Invariante strukturell garantiert.

## Tabelle `relation_definition`

```json
{
    "id": "rel_depends_on",
    "name": "depends_on",            // eindeutiger Name des Relationstyps
    "kind": "composition",           // composition | aggregation (strukturell)
    "role": "composition",           // composition | category | collection | link (Verwendung, geplant s. u.)
    "source_types": [],              // optional: erlaubte Quell-Objekttypen (leer = alle)
    "target_types": [],              // optional: erlaubte Ziel-Objekttypen (leer = alle)
    "label": { "de": "Abhängig von", "en": "Depends on" },
    "attributes": [],                // Edge-Attribut-Namen (referenzieren attribute_definition)
    "system": true,                  // eingebaut (true) oder benutzerdefiniert (false)
    "created_at": "2026-08-17T08:00:00Z",
    "updated_at": "2026-08-17T08:00:00Z"
}
```

- `kind` beschreibt die **Struktur** (Komposition vs. Aggregation); es darf **genau eine** Definition mit `kind: "composition"` geben. Welche **Bedeutung** eine Aggregation hat, sagt `role` (s. u.).
- `source_types` / `target_types` schränken die erlaubten Objekttypen ein; **leer = alle Typen erlaubt**. Die Prüfung erfolgt **beim Anlegen jeder Relation** (Phase 1–2), nicht nur deklarativ.
- `attributes` (Phase 9) ist die Liste der **Edge-Attribute** eines Relationstyps (Namen, die auf `attribute_definition` verweisen). Die zugehörigen Werte liegen auf der Kante in `reference.attributes[name]`; das Produkt-Formular rendert dafür typspezifische Eingabefelder.
- `system: true` kennzeichnet eingebaute Typen (Seed in Phase 1–2): `depends_on` (Komposition) sowie `category_member`, `collection_member`, `related` (Aggregation).
- `role` klassifiziert die **Verwendung** des Typs (`composition` | `category` | `collection` | `link`); Standard-Typen tragen ihre feste Rolle, neue benutzerdefinierte Typen starten als `link` und dürfen `link` oder `category` sein.- Benutzerdefinierte Relationstypen (`system: false`, Phase 9) werden über die API angelegt (`POST /relation-definitions`) und sind immer Aggregationen; `PATCH /relation-definitions/{id}` ändert Label/Restriktionen/Attribute, `DELETE /relation-definitions/{id}` entfernt einen ungenutzten benutzerdefinierten Typ (400 für `system`-Typen, 409 solange Referenzen ihn nutzen).

## Verwendung von Relationstypen (`role`) — Plan (2026-09-11)

**Status: umgesetzt (Stage 1 + Zusatz-Kategorisierungen, 2026-09-11).** Kern, Seed-Fix, Worker, Formular, Relationstyp-Verwaltung, Bulk-Wizard und Tests sind umgesetzt; die Abweichungen zur ursprünglichen Planung stehen am Ende dieses Abschnitts.

**Ausgangslage.** Alle Aggregationen sind technisch gleich (n:m-Kanten ohne Vererbung/Kaskade), haben aber schon heute **unterschiedliche Bedeutungen**: Kategorisierung (`category_member`), Sammlungs-Mitgliedschaft (`collection_member`) und allgemeine Verknüpfung (`related`). Diese Bedeutung steckt bisher nur in **hartkodierten IDs** – im Kern (`COMPOSITION_DEFINITION`, `category_member_keys`, `collection_member_keys`, `'rel_category_member'` in den Grid-Filtern), im Frontend (`form.py` blendet `rel_category_member` aus und behandelt `rel_depends_on` speziell, `grid.py::_DEDICATED_RELATION_DEFS`, Kategorien-/Collections-Sichten, Referenz-Modal) und im Worker (`bulk.py::COLLECTION_MEMBER_DEF` plus Sonder-Routing sowie `rel_category_member` für die Kategorie-Operationen).

Sichtbare Folgen der fehlenden Klassifizierung:

- Ein benutzerdefinierter Typ ist für Code und Anwender nicht von „Kategorie“ oder „Sammlung“ zu unterscheiden; jede Sonderbehandlung muss IDs kennen und sie an allen Stellen mitpflegen.
- Die Bulk-Operation „Relation zuweisen/entfernen“ musste Standard-Typen per ID **ausschließen** (Komposition und Kategorien haben eigene Operationen) statt nach Bedeutung zu filtern.
- Fehlkonfigurationen fallen erst spät auf: `rel_collection_member` mit `source_types: ["collection"]` (über die Relationstyp-Verwaltung gesetzt) lässt das generische Anlegen von Sammlungs-Kanten mit `400` scheitern – die Speicherrichtung ist Mitglied (`in`) → Sammlung (`out`), `source_types` prüft aber die **Quelle**. Die Rolle „Sammlung“ würde Richtung und Containertyp erzwingen.
- In `/relation-types` ist nur `kind` sichtbar (`composition` | `aggregation`); die **Verwendung** fehlt.

**Vorschlag: neues Feld `role`.** `role` beschreibt die **Verwendung** eines Relationstyps, `kind` bleibt der **strukturelle** Typ (Komposition vs. Aggregation):

| `role` | Bedeutung | Erzwungene Struktur | Sichtbar/verwendet in |
| --- | --- | --- | --- |
| `composition` | Komposition (Vererbung, Kaskade, Baum) | `kind: composition`, genau **eine** Definition, `source_types`/`target_types` leer | Strukturbaum, Formular (Elternobjekt, Re-Parenting), Vollständigkeit, Löschkaskade |
| `category` | Kategorisierung | `kind: aggregation`, genau **eine** Definition, Ziel **muss** ein `category`-Objekt sein | Formular-Abschnitt „Kategorien“, Kategorien-Baum/-Seite, Grid-Filter `?category=`/`?uncategorized=`, Mitgliederzähler, Bulk `add_category`/`remove_category` |
| `collection` | Mitgliedschaft in einer materialisierten Sammlung | `kind: aggregation`, genau **eine** Definition, Ziel **muss** ein `collection`-Objekt sein, Kanten-Metadaten `rank`/`added_at` | Sammlungen-Seite/-Ansicht, `POST /collections/{id}/members`, Refresh-Job, Grid-Filter `?collection=`, Bulk `add_relation` (Routing über den Sammlungs-Endpunkt) |
| `link` | allgemeine Verknüpfung (**Default**) | `kind: aggregation`, beliebig viele Definitionen | Relationen-Abschnitt im Formular, Referenz-Modal im Grid, Objekt-Picker, Bulk `add_relation`/`remove_relation`, MEF-Import (`type` → `rel_<name>`) |

Regeln:

- **Konsistenz:** `role == 'composition'` ⟺ `kind == 'composition'`; `kind` bleibt im Datenmodell (Traversal, Ein-Eltern-Invariante), wird aber aus der Rolle abgeleitet und ist in API/UI nicht separat zu pflegen.
- **Singletons:** `composition` und `collection` sind **System-Rollen** – genau eine Definition je Rolle, `system: true`, Rolle **nicht** änderbar. `category` darf mehrfach vorkommen (primärer System-Typ + Zusatz-Typen, s. u.).
- **Default:** Jeder neu angelegte Typ ist `link` ohne Sondermetadaten (kein `rank`/`added_at`, keine Sonder-UI) – also genau die erwartete „normale Verknüpfung“.
- **Änderbar für Custom-Typen:** Eigene Typen dürfen ihre Rolle wechseln (`link` ⇄ `category`); `composition` und `collection` bleiben den System-Typen vorenthalten. Im Dialog ist die Verwendung wählbar, für `system`-Typen fest.
- **Rollenprüfung im Kern:** `POST /references` prüft zusätzlich die Rolle – `category` ⇒ Zielobjekt vom Typ `category`, `collection` ⇒ Zielobjekt vom Typ `collection`, `composition` ⇒ bestehende Regeln (ein Elternteil, Zyklenschutz). Eine fehlkonfigurierte Rolle wird damit sofort gemeldet statt später als Filter-Überraschung. Zusätzlich normalisiert der Kern `target_types` auf den von der Rolle erzwungenen Typ (`category` ⇒ `["category"]`) und lehnt widersprüchliche Angaben mit `400` ab (gilt für benutzerdefinierte, nicht für `system`-Typen).
- **Sichtbarkeit:** Die Relationstyp-Verwaltung und der Relationen-Abschnitt im Formular zeigen die Verwendung (Badge), damit „Verknüpfung vs. Kategorie vs. Sammlung“ ohne ID-Wissen erkennbar ist.

**Umsetzungsschritte (Stage 1) — alle erledigt:**

1. **Schema & Seed:** `role` an `relation_definition` (`schema.rs`), Seed setzt die vier Rollen. **Dabei mitzubeheben:** `seed::apply` schreibt System-Records bei **jedem** Kernstart per `UPSERT … CONTENT` und überschreibt damit Laufzeit-Änderungen (Relationstypen, Objekttypen, Einheiten, Sprachen) – der Seed darf nur fehlende Records anlegen bzw. nur Identitätsfelder (`name`, `kind`, `system`, `role`) setzen. Eine einmalige Migration füllt fehlende Rollen mit `link`.
2. **Kern:** `role` in Response/DTO + Validierung (`POST`/`PATCH /relation-definitions`: Default `link`, `system` ⇒ Rolle unveränderlich, Singletons erzwungen), Rollenprüfung in `create_reference`.
3. **Frontend/Worker:** Hartkodierte IDs durch Rollen-Lookups ersetzen (`relation_def_by_key` → `role`, Abschnitte in `form.py`, `grid.py::_DEDICATED_RELATION_DEFS` → „Rollen mit eigener Operation“, `worker/bulk.py`-Routing über die Rolle statt `COLLECTION_MEMBER_DEF`).
4. **UI:** `/relation-types` zeigt die **Verwendung** (Spalte + Badge, Auswahl im Dialog, für `system` deaktiviert, `target_types` bei Kategorie-Rollen fixiert); der Bulk-Wizard bietet `link`/`collection` generisch (mit korrektem Routing) und blendet `composition`/`category` aus – für die Kategorie-Operationen wählt ein eigenes Feld den Kategorie-Typ (Default: primärer Typ); der Relationen-Abschnitt im Formular folgt derselben Regel.
5. **Tests & Doku:** Rollen-Validierung (Core-Unit-Tests), Rollen-Helfer und Worker-Routing (pytest), Doku-Nachzug (`kategorisierung.md`, `gespeicherte-suchen-collections.md`, `api-design.md`, DOX-Dateien).

**Abweichungen zur Planung (umgesetzt):**

- Die Rollenprüfung greift zusätzlich in `check_type_restriction` (das normalisierte `target_types` erzwingt den Zieltyp); die Meldung nennt daher den Ziel-Typ.
- `GET /relation-definitions` ist mit dem Worker-API-Key lesbar (`api_key_or_role(..., ROLE_EDITOR)`), damit der Worker Rollen je Job-Lauf einmal auflösen kann (Fallback: eingebaute Rollen-Tabelle).
- Der Worker adressiert Kategorie-Operationen über `parameters.definition_id` (Default `rel_category_member`) und gibt den verwendeten Typ im Ergebnisbericht mit aus.
- `?definition_id=` wird auch ohne `category`/`uncategorized` validiert (`400`, wenn der Typ keine Kategorie-Rolle hat).
- **Zieltypen gehören in jede Auswahl:** Jeder Objekt-Picker bekommt die **effektiven** Zieltypen (`target_types` der Definition, sonst der von der Rolle erzwungene Typ) – sonst bietet die Struktur-/Suchauswahl Objekte an, die der Kern mit `400` ablehnt (beobachtet bei „Collection-Zuordnung“). Zusätzlich filtert der Picker die Suche auf diese Typen und verwirft `exclude_ids`; das Formular zeigt den „Hinzufügen“-Knopf nur, wenn der Objekttyp als **Quelle** erlaubt ist, und das Referenz-Modal des Grids bietet nur Typen an, die den Zeilentyp als Quelle akzeptieren.

**Offene Entscheidungen (mit Vorschlag):**

1. **Mehrere `category`-Rollen zulassen?** **Entschieden: ja** (siehe „Zusatz-Kategorisierungen“ unten) – aber nur für `category`, nicht für `collection`/`composition`.
2. **`kind` in `role` auflösen?** `kind` ist aus `role` ableitbar (weniger Redundanz), aber es ändert API- und Frontend-Stellen. **Vorschlag:** `kind` vorerst behalten, abgeleitet und schreibgeschützt.
3. **Bezeichnung:** Feld `role`, UI „Verwendung“ (en: „Usage“). **Vorschlag:** so.

**Nicht-Ziele:** keine Änderung an der Kanten-Speicherung (`reference.in`/`out`), keine Rolle außer den vier genannten, keine automatische Umklassifizierung bestehender Daten (nur Default `link`).

## Zusatz-Kategorisierungen (eigene Kategorie-Relationstypen) — Plan (2026-09-11)

**Status: umgesetzt (2026-09-11).**

**Motivation.** Neben der festen Kategorie-Taxonomie sollen **eigene Kategorie-Zuordnungen** möglich sein, um Zuordnungsarten zu unterscheiden (z. B. „Hauptkategorie“ vs. „Kanalkategorie“ vs. „Themenkategorie“) und je Zuordnung **Kanten-Attribute** zu pflegen (`relation_definition.attributes` → `reference.attributes`, Editor im Formular existiert bereits). Alle Typen zeigen dabei auf **dieselben** `category`-Objekte (eine Taxonomie, mehrere Zuordnungsarten) – kein zweiter Kategorie-Objekttyp.

**Leitplanken:**

- `role: category` darf **mehrfach** vorkommen: genau **ein** Typ ist **primär** = der System-Typ (`system: true`, `rel_category_member`) und ist die **Default-Dimension** der Kategorie-Ansichten. Weitere Typen sind **Zusatz-Kategorisierungen** (`system: false`) und in denselben Ansichten per Umschalter wählbar; der Baum selbst (die Kategorie-Objekte und ihre Hierarchie) ist für alle Dimensionen derselbe. Zähler und „Nicht kategorisiert“ folgen der gewählten Dimension. Siehe „Dimensionen“ in [kategorisierung.md](kategorisierung.md).
- Die Rolle **erzwingt** die Struktur: `kind: aggregation`, Ziel ist ein **Taxonomie-Knoten** (Objekttyp mit `taxonomy: true`; der System-Typ `category` ist die Default-Taxonomie), Richtung Objekt → Knoten. Eine explizite `target_types`-Liste einer Kategorie-Rolle darf nur Taxonomie-Typen nennen (sonst `400`); ohne Liste gilt die Regel zur Schreibzeit. `source_types` beschränkt weiterhin die **Mitglieder** (leer = alle). Damit ist die beobachtete Fehlkonfiguration (falsche Richtung/Restriktion) strukturell unmöglich.
- `composition` und `collection` bleiben **Singletons** (Rollen reserviert). Mehrere Sammlungen entstehen über mehrere `collection`-**Objekte**, nicht über mehrere Kantentypen.
- Eigene Typen starten als `link`; die Umstellung auf `category` ist ein bewusster Schritt in der Relationstyp-Verwaltung (nur bei `system: false`) und setzt `target_types` automatisch auf `["category"]`. Eine Rückstufung auf `link` ist erlaubt, solange keine Kanten des Typs existieren.

**Kern (generisch statt Einzel-ID):**

- `category_member_keys(db, category_id, definition_id)` – Default = primärer Typ, sonst der angegebene; die Helfer werden auf eine Definitionsliste (`definition_id IN $defs`) erweitert.
- `GET /objects?category=<id>[&definition_id=<rel_…>]` und `?uncategorized=true[&definition_id=<rel_…>]` reichen den Typ durch (Default: primärer Typ, abwärtskompatibel).
- `?count_members=true` zählt den Typ aus `&definition_id=<rel_…>` (Default: primärer Typ) – je Dimension ein eigener Zähler.
- `POST /references` prüft die Rolle (Ziel `category`) und validiert Kanten-Attribute gegen das Template **des jeweiligen Typs**.
- `POST /relation-definitions`: `role: category` für eigene Typen erlaubt (mehrfach), `target_types` wird auf `["category"]` normalisiert.

**UI:**

- **Formular:** Der Abschnitt **„Kategorien“** rendert **je Kategorie-Typ einen Block** (primärer Typ zuerst, danach Zusatz-Typen alphabetisch): Picker auf `category` beschränkt, Chips wie heute, ✕ entfernt die Kante des jeweiligen Typs; Kanten-Attribute über den vorhandenen Editor (Stift). Damit sind „Zuordnungen differenzieren + Attribute je Relation“ abgedeckt.
- `/relation-types`: Verwendung `category` wählbar (nur `system: false`), Anzeige unterscheidet „Kategorie (primär, System)“ und „Kategorie (Zusatz)“; `target_types` ist für Kategorie-Rollen schreibgeschützt.
- **Bulk-Wizard:** `add_category`/`remove_category` bekommen ein **Typ-Feld** (Default: primärer Typ). Alternativ kann die Operation „Relation zuweisen/entfernen“ weiterhin jeden Kategorie-Typ bedienen (Picker bereits auf `category` beschränkt).
- **Grid:** Baum und „Nicht kategorisiert“ sind für alle Dimensionen dieselben Kategorie-Objekte; das Kategorien-Panel hat einen **Dimensions-Umschalter** (`?category=…&definition_id=…`), der Zähler und Zeilen auf den gewählten Typ umstellt (Default: primärer Typ, URL dann ohne `definition_id`). `/categories` hat denselben Umschalter.

**Nicht-Ziele:** keine Kategorie-**Hierarchie** je eigenem Typ (der Baum bleibt pro Taxonomie-Typ: alle Knoten einer Taxonomie hängen an einem gemeinsamen Wurzelknoten; Taxonomien werden nicht gemischt), kein zweiter Kategorie-Objekttyp im Sinne eines neuen Konzepts (Knoten bleiben normale Objekte, s. `kategorisierung.md` „Taxonomie-Typen“), keine Mehrfachrolle für `collection`/`composition`.

## Altlasten-Mapping (entfernt)

Das frühere Modell kannte weitere Typen und Kanten-Attribute, die durch das Relationenmodell **ersatzlos entfallen**:

| Bisher                         | Status                                           |
| ------------------------------ | ------------------------------------------------ |
| `inherits_from`                | → ersetzt durch`depends_on`                      |
| `parent`                       | entfällt (durch`depends_on` abgedeckt)           |
| `variant`                      | entfällt (Varianten = Kind via`depends_on`)      |
| `priority` (Kante)             | entfällt (genau ein Elternteil)                  |
| `scope` (Kante)                | entfällt (genau ein Vererbungspfad)              |
| `preferred_inheritance_source` | entfällt (genau eine Quelle)                     |
| `disabled_for` (Kante)         | entfällt im MVP (optional spätere Ausbaustufe)   |

## Invarianten (Zusammenfassung)

1. Jedes Objekt hat genau eine `depends_on`-Kante; die Komposition ist ein zyklenfreier Baum mit dem Universal-Root als Wurzel.
2. Es gibt genau eine Kompositions-Definition; alle anderen Relationstypen sind Aggregationen.
3. Vererbung und Löschkaskade wirken ausschließlich über `depends_on`.
4. Aggregationen kaskadieren nie und vererben nicht.
5. Der Universal-Root ist unlöschbar und strukturell unveränderlich.
6. Relationen werden beim Anlegen gegen die Typrestriktionen der `relation_definition` geprüft.
7. `role` klassifiziert die Verwendung: genau eine Definition je System-Rolle (`composition`, `category`, `collection`), alle übrigen sind `link`; die Rolle ist bei `system`-Typen unveränderlich.
8. Sonderbehandlungen (Vererbung/Kaskade, Kategorien, Sammlungen) hängen an der **Rolle**, nicht an Typ-IDs.

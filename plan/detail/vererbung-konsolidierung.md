# Vererbung konsolidieren (Werte hochschieben)

- **Hauptdokument:** Abschnitt 4 ([Datenwertvererbung](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 9
- **Stand:** umgesetzt (2026-09-11)

## Ziel

„Vererbung reparieren": Werte, die bei **allen** Kindern eines Knotens **identisch** gepflegt sind – entstanden durch redundante Pflege oder einen fehlerhaften Import – werden auf den **Elternknoten hochgeschoben** und bei den Kindern **entfernt**. Danach erben die Kinder den Wert (echte Vererbung statt Datenkopien), und dieselbe Änderung muss künftig nur an einer Stelle gepflegt werden.

Grundlagen: [datenwertvererbung.md](datenwertvererbung.md) (Auflösungslogik, „leer gesetzt" unterbricht die Vererbung).

## Regeln (je Attribut und Elternknoten)

Ein Attribut wird **nur** hochgeschoben, wenn **alle** Bedingungen gelten:

- Die Attributdefinition ist `inheritable: true`, **nicht** `computed` und **nicht** `is_key` (Schlüssel sind nie vererbbar).
- Der Knoten hat mindestens `min_children` direkte Kinder (Standard **2** – sonst kein Pflegegewinn).
- **Alle** direkten Kinder haben das Attribut **identisch** gepflegt (gleicher Wert). Ein Kind **ohne** Attribut, ein Kind mit abweichendem Wert oder ein Kind mit **explizit leerem** Wert (`null`) ⇒ **kein** Vorschlag für dieses Attribut (Konflikt bzw. gewollte Unterbrechung).
- Am Elternknoten ist das Attribut **nicht** gepflegt (Schlüssel fehlt) **oder** gepflegt mit **demselben** Wert ⇒ sonst **Konflikt** (Report, keine Änderung).
- Mehrsprachige Werte werden **pro Sprache** verglichen: Nur wenn alle Kinder dieselben Sprachen mit denselben Werten haben, wird die Map als Ganzes hochgeschoben. Fehlt einem Kind eine Sprache, die ein anderes hat, greift dieselbe Skip-Regel (der aufgelöste Wert wäre sonst nicht identisch).
- Referenzen/Enums werden als Wertsyntax verglichen (Ziel-ID bzw. Code) – identische Werte sind unkritisch.

## Algorithmus (hierarchisch, Ebene für Ebene)

1. **Start** am gewählten Knoten (z. B. dem im Formular geöffneten Objekt), optional inkl. Nachfahren.
2. **Prüfen auf dieser Ebene:** Für den Knoten werden die eigenen Werte (`GET /objects/{id}`) und die direkten Kinder (`depends_on`) samt deren `attributes` geladen, dann je Attribut die Regeln oben angewendet.
3. **Anwenden (pro Treffer):** (a) Wert am Knoten setzen, (b) Attribut-Schlüssel bei **allen** geprüften Kindern entfernen (`remove_attributes`) – die Kinder erben danach wieder.
4. **Absteigen:** Anschließend wird für **jedes** Kind dieselbe Prüfung wiederholt (nächste Ebene). Weil das Hochschieben vor dem Abstieg passiert, sieht die nächste Ebene den neuen Elternwert und kann ihn – falls auch die Enkel identisch sind – erneut hochschieben.
5. **Ende** an den Blättern. Ein Lauf bereinigt damit einen ganzen Teilbaum; **Idempotenz:** ein zweiter Lauf erzeugt eine leere Vorschau.

**Reihenfolge:** top-down (Ebene für Ebene). Bottom-up wäre ebenfalls denkbar, würde aber bei mehrstufigen identischen Werten mehrere Läufe benötigen.

**Umsetzungsdetails (2026-09-11):**

- Der Lauf steigt nur in Kinder ab, die selbst Kinder haben (`hasChildren` aus der Kinderliste) – Blätter werden nicht mehr abgefragt.
- **Schon geerbtes wird nicht erneut geschrieben:** Wurde ein Wert auf dieser Ebene hochgeschoben, sieht die nächste Ebene ihn als **vom Vorfahren geliefert**. Halten die Enkel ihn dann ebenfalls identisch, wird am Kind **nicht** erneut geschrieben (`parent_written: false`), sondern nur ihre Kopien entfernt.
- **Kandidaten** sind nur **definierte** Attribute, die mindestens ein Kind pflegt, und (bei gesetztem `attributes`) die gefilterten. Ein Kind ohne Attribut ist ein Fall (`missing_on_child`), ein Attribut ohne Definition (Konventionsfeld `name`) dagegen **kein** Fall – sonst wäre der Bericht unbrauchbar laut.
- **Prüf-Reihenfolge (Report-Grund):** `no_children` → `missing_on_child` → `interrupted` → `conflict_child_values` → `not_inheritable` → `conflict_parent`; erst danach wird hochgeschoben. Der Grund nennt den konkreten Fall, `value` zeigt dennoch den Wert, auf den sich die Kinder einig sind (die Attribute nennen das Attribut).
- Ein **explizit leerer** Wert am Elternknoten (`null`) gilt als abweichender eigener Wert ⇒ `conflict_parent` (die „bewusst leer“-Absicht wird nicht überschrieben).

## Vorschau (verbindlich) und Anwendung

- **Dry-Run zuerst.** Der Worker berechnet den vollständigen Vorschlag und liefert ihn **ohne Schreibvorgänge**:
  `moves[] = {parent, parent_label, attribute, value, children[], action: "promote" | "skip", reason, parent_written}` mit `reason` ∈ `no_children` | `missing_on_child` | `conflict_child_values` | `interrupted` | `conflict_parent` | `not_inheritable` (nur bei `action: "skip"`).
- **Anwenden:** dieselbe Berechnung, danach die Schreibvorgänge (ein `PATCH` je Objekt; Kinder mit `remove_attributes`). Der Report nennt pro Objekt/Attribut `promoted`/`skipped`.
- **Report (`job_run.result`):** `{op, dry_run, min_children, processed, succeeded, skipped, failed, nodes, promoted, removed, moves[], results[]}`; `moves[]` wie oben, zusätzlich `parent_label` (Name des Knotens für die Anzeige) und `parent_written` (der Knoten musste nicht geschrieben werden, weil er den Wert schon trug bzw. er von einem Vorfahren kommt). `results[]` nennt je Wurzel `status: ok|error` (+ `nodes`).
- **Idempotenz:** nach einem Lauf pflegt kein Kind die hochgeschobenen Attribute mehr, ein zweiter Lauf meldet keine Vorschläge.

## API

- Ausführung als **Bulk-Operation `consolidate_inheritance`** (`POST /objects/bulk`, `object_ids = [root]`, `parameters = {dry_run, attributes?, min_children?}`), damit **Fortschritt, Polling und Report** der bestehenden Bulk-/Job-Infrastruktur unverändert genutzt werden (`bulk_operation`-Job, `job_run.result`). Umsetzung im Worker (`meadow/worker/bulk.py`), der die vorhandenen Endpunkte nutzt: `GET /objects/{id}` (eigene Werte), `GET /objects/{id}/children` (Kinder inkl. `hasChildren`) und `PATCH /objects/{id}` (`attributes` bzw. `remove_attributes`).
- `GET /objects/{id}/children` akzeptiert dafür wie die anderen Worker-Endpunkte den `X-Api-Key` (Service-Account, `api_key_or_role` in `crates/pim-core/src/api/references.rs`).
- Der Dry-Run läuft ebenfalls als Job (`dry_run: true`), damit auch große Teilbäume nicht in den Request-Pfad geraten.

## UI

- **Produkt-Formular:** Aktion **„Vererbung konsolidieren…“** im Kopfbereich (`form.consolidate`), nur für `editor+` und nur sichtbar, wenn der Knoten Kinder hat. Der Dialog zeigt:
  1. Optionen (`min_children`, optional Attribut-Auswahl aus den vererbbaren Attributen),
  2. **„Vorschau“** → Liste der Vorschläge (Knoten · Attribut = Wert ← N Kinder) **inkl. übersprungener Fälle mit Grund** (`consolidate.reason.*`),
  3. **„Übernehmen“** → Hintergrundjob; danach lädt die Ansicht automatisch neu (der bearbeitete Abschnitt bleibt offen, siehe `form_sections`).
- **Später optional:** Kontextaktion am Strukturbaum-Knoten (Teilbaum ohne Formular öffnen).

## Nicht-Ziele

- **Kein** automatisches Aufräumen beim Speichern oder Import (bewusst manuell und nachvollziehbar).
- **Kein** Zusammenführen **unterschiedlicher** Werte (nur identische) – dafür wäre eine Konfliktlösungs-UI nötig (eigene Ausbaustufe).
- **Keine** Änderung an Objekten außerhalb des gewählten Teilbaums.
- Kein synchroner Vorschau-Endpunkt (der Dry-Run läuft weiterhin als Job).

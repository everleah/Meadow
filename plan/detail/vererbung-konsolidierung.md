# Vererbung konsolidieren (Werte hochschieben)

- **Hauptdokument:** Abschnitt 4 ([Datenwertvererbung](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 9
- **Stand:** Entwurf (geplant, nicht umgesetzt)

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
2. **Prüfen auf dieser Ebene:** Für den Knoten werden die direkten Kinder (`depends_on`) samt `attributes` geladen (eine Abfrage), dann je Attribut die Regeln oben angewendet.
3. **Anwenden (pro Treffer):** (a) Wert am Knoten setzen, (b) Attribut-Schlüssel bei **allen** geprüften Kindern entfernen (`remove_attributes`) – die Kinder erben danach wieder.
4. **Absteigen:** Anschließend wird für **jedes** Kind dieselbe Prüfung wiederholt (nächste Ebene). Weil das Hochschieben vor dem Abstieg passiert, sieht die nächste Ebene den neuen Elternwert und kann ihn – falls auch die Enkel identisch sind – erneut hochschieben.
5. **Ende** an den Blättern. Ein Lauf bereinigt damit einen ganzen Teilbaum; **Idempotenz:** ein zweiter Lauf erzeugt eine leere Vorschau.

**Reihenfolge:** top-down (Ebene für Ebene). Bottom-up wäre ebenfalls denkbar, würde aber bei mehrstufigen identischen Werten mehrere Läufe benötigen.

## Vorschau (verbindlich) und Anwendung

- **Dry-Run zuerst.** Der Kern berechnet den vollständigen Vorschlag und liefert ihn **ohne Schreibvorgänge**:
  `moves[] = {parent, attribute, value, children[], action: "promote" | "skip", reason}` mit `reason` ∈ `no_children` | `missing_on_child` | `conflict_child_values` | `interrupted` | `conflict_parent` | `not_inheritable`.
- **Anwenden:** dieselbe Berechnung, danach die Schreibvorgänge (ein `PATCH` je Objekt; Kinder mit `remove_attributes`). Der Report nennt pro Objekt/Attribut `promoted`/`skipped`.

## API

- Ausführung als **Bulk-Operation `consolidate_inheritance`** (`POST /objects/bulk`, `object_ids = [root]`, `parameters = {dry_run, attributes?, min_children?}`), damit **Fortschritt, Polling und Report** der bestehenden Bulk-/Job-Infrastruktur unverändert genutzt werden (`bulk_operation`-Job, `job_run.result`).
- Der Dry-Run läuft ebenfalls als Job (`dry_run: true`), damit auch große Teilbäume nicht in den Request-Pfad geraten. Für kleine Teilbäume kann später ein synchroner Vorschau-Endpunkt ergänzt werden.

## UI

- **Produkt-Formular:** Aktion **„Vererbung konsolidieren…"** im Aktionsbereich des Kopfes (analog „Im Grid anzeigen"). Der Dialog zeigt:
  1. Optionen (`min_children`, optional Attribut-Auswahl),
  2. **„Vorschau berechnen"** → Liste der Vorschläge (Attribut: Wert ← Kinder) **inkl. übersprungener Fälle mit Grund**,
  3. **„Übernehmen"** → Hintergrundjob mit Fortschritt + Report.
- Nur `editor+`; die Aktion ist nur sichtbar, wenn der Knoten Kinder hat.
- **Später optional:** Kontextaktion am Strukturbaum-Knoten (Teilbaum ohne Formular öffnen).

## Nicht-Ziele

- **Kein** automatisches Aufräumen beim Speichern oder Import (bewusst manuell und nachvollziehbar).
- **Kein** Zusammenführen **unterschiedlicher** Werte (nur identische) – dafür wäre eine Konfliktlösungs-UI nötig (eigene Ausbaustufe).
- **Keine** Änderung an Objekten außerhalb des gewählten Teilbaums.

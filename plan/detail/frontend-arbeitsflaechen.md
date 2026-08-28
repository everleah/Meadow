# Arbeitsflächen der Benutzeroberfläche (Grid, Formular, Massendatenpflege)

- **Hauptdokument:** Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 7, 9
- **Stand:** Entwurf

Dieses Dokument spezifiziert die konkreten Arbeitsflächen der Weboberfläche (NiceGUI): das **Grid** als Hauptarbeitsfläche, das **Produkt-Formular** als Drill-down, die **Massendatenpflege** und das **Dashboard**. Es vertieft die Modulübersicht aus [frontend.md](frontend.md) und übersetzt die fachlichen Modelle (Datenmodell, Vererbung, Sprachbaum, Kategorisierung) in eine **menschengerechte Oberfläche** – die UI bildet bewusst nicht das generische Objektmodell ab.

## Leitprinzipien

- **Die UI bildet nicht das generische Objektmodell ab.** Der Anwender sieht eine vertraute Produktsicht: Ein Produkt hat Name, Preis, Beschreibung, technische Daten – gruppiert, geordnet, mit Pflichtfeldern. Der generische Unterbau (Typ, flexible Attribute) bleibt dahinter verborgen.
- **Drei Arbeitsflächen, eine Auswahl:** (1) das **Grid** als Zuhause der Anwender (Suchen, Filtern, Inline-Editing, Bulk-Aktionen), (2) das **Produkt-Formular** als Drill-down für tiefes Anreichern eines einzelnen Objekts, (3) der **Bulk-Wizard** für Massenänderungen. Alle drei teilen sich Filterlogik, Auswahl und Vollständigkeits-Kontext.
- **Template pro Typ:** Welche Attribute, Gruppen und Pflichtfelder ein Objekttyp anzeigt, bestimmt die Produktvorlage (Attributdefinitionen mit `applicable_types`/`required_types`, siehe [datenmodell.md](datenmodell.md)). Nur Gruppen mit mindestens einem für den Typ gültigen Attribut erscheinen.
- **Hinweise als Icons/Marker:** Herkunft (Vererbung), Fallback (Sprache), Warnungen und Status werden als **dezente Icons/Marker mit Tooltip** dargestellt – nicht als beschreibender Text, um die Anzeige schlank zu halten.
- **Grid-Technologie:** Produktives Grid über **`ui.aggrid` (AG Grid, Community-Modul)** – eingebaute Zell-Editoren, Mehrfachauswahl, Spalten-Pinning, bedingte Formatierung und Synchronisation der Client-Edits zurück zum Server. `ui.table` (Quasar) bleibt für einfache Admin-Listen (Jobs, Audit, Benutzer). `nicegui-tabulator` (Tabulator.js, MIT) ist die dokumentierte Alternative, falls ein Baum direkt im Grid benötigt wird.
- **Stand (Schritt 3/3b/4/4b):** Das Basis-Grid ist umgesetzt (`/products`, `meadow/frontend/grid.py`): lesbare Objektliste (`ui.aggrid`) mit aufgelöstem Namen (Sprachbaum-Wert), **Status-Badge**, i18n-Spaltenüberschriften und Mehrfachauswahl; Daten aus `GET /objects`. **Inline-Editing & Batch-Save** sind umgesetzt (Abschnitt 1.3): Zell-Editing, Gestaging mit „Ungespeicherte Änderungen“-Zähler, Batch-Save über `POST /objects/batch-update` (max. 500 Zeilen), „Verwerfen“, gültiger JWT erforderlich (abgelaufene Tokens leiten zur Login-Seite um). Der **Spalten-Picker** ist umgesetzt (Abschnitt 1.2): „Spalten“-Button öffnet ein Menü mit Checkboxen je Attributspalte (aus `GET /attribute-definitions`); Attribute sind standardmäßig ausgeblendet, die ID-Spalte ist links gepinnt. **Persönliche Ansichten** sind umgesetzt (Abschnitt 1.2): „Ansichten“-Dialog speichert die aktuelle Spaltenauswahl pro Benutzer (`app.storage.user`), listet gespeicherte Ansichten und stellt sie per Klick wieder her (inkl. Löschen). Kategorien-/Strukturbaum und Vollständigkeits-Spalte folgen.

## 1. Grid (Hauptarbeitsfläche)

Das Grid ist der Ort, an dem Anwender die meiste Zeit verbringen. Es ersetzt die bisherige „Objektliste" als reine Anzeige und wird zur **Arbeitsfläche**.

### 1.1 Linke Navigation: Kategorien & Struktur

Ein linkes Panel bietet einen **Umschalter** zwischen zwei Sichten:

- **Kategorien:** Es gibt **beliebig viele unabhängige Kategorie-Bäume** (z. B. „Webshop-Navigation", „Zielgruppe", „Saison"); der Anwender wählt, welcher Baum angezeigt wird. Jeder Knoten zeigt einen **Zähler** (Anzahl zugeordneter Objekte inkl. Unterkategorien); ein **„Nicht kategorisiert"**-Eintrag listet Objekte ohne Kategoriezuordnung. Zuordnung über `category_member` (Aggregation, [kategorisierung.md](kategorisierung.md)).
- **Struktur:** Die **Pflegestruktur** (Kompositionsbaum `depends_on`, Universal-Root → Familien → Produkte → Varianten, [relationen.md](relationen.md)). Sie ist die Navigationsachse für Vererbung und Hierarchie.

Auswahl eines Knotens setzt den **Grid-Filter** auf den Teilbaum. Die Anzeige im Grid bleibt **flach** (Zeilen); die Hierarchie wird über eine **„Übergeordnetes Produkt"**-Spalte sowie den Strukturbaum sichtbar. Ein Baum direkt im Grid (Tree/Row-Grouping) ist im MVP bewusst nicht vorgesehen – AG Grid bietet das nur im lizenzierten Enterprise-Modul; Tabulator (`dataTree`) wäre die Open-Source-Alternative, falls das später zwingend wird.

### 1.2 Spalten & Ansichten

- **Konfigurierbare Spalten (implementiert):** Der Anwender wählt über einen **Spalten-Picker** („Spalten“-Button im Grid, `meadow/frontend/grid.py`), welche Attribut-Spalten sichtbar sind; Spalten sind **pinnbar** (ID standardmäßig links), in Breite und Reihenfolge anpassbar (AG-Grid-Standard). Die angebotenen Attributspalten stammen aus `GET /attribute-definitions`; Attribute sind zunächst ausgeblendet. Persönliche gespeicherte Ansichten folgen als nächster Teilschritt.
- **Persönliche gespeicherte Ansichten (implementiert, MVP):** Ein benannter Satz aus `{Baum-Kontext, Filter, Spalten, Sortierung}` wird **pro Benutzer** gespeichert und per Klick wiederhergestellt. MVP: Der „Ansichten“-Dialog speichert die aktuelle **Spaltenauswahl** unter einem Namen in `app.storage.user` (pro Benutzer-Session, über Cookie persistiert), listet gespeicherte Ansichten und stellt sie per Klick wieder her (inkl. Löschen). Filter/Sortierung/Baum-Kontext werden noch nicht erfasst; diese Erweiterung sowie serverseitige Persistenz (Backend) folgen. Abgrenzung: Das ist eine **leichte UI-Ebene**, nicht die `saved_search`-Query-DSL bzw. materialisierte Collections (Phase 9, [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md)); später kann die Advanced Search darauf aufsetzen.
- **Zellen-Darstellung:** Menschlich formatiert statt roh: `status` als farbiges **Badge**, `enum` als **farbige Tags**, quantifizierte Werte mit **Einheiten-Symbol** (z. B. `12,5 mm`), Datum **lokalisiert** (babel), Referenzen als **Chips** mit aufgelöstem Label.

### 1.3 Inline-Editing & Batch-Save

- **Direktes Zellen-Editing** für einfache Attributtypen (AG-Grid-Cell-Editors): `text` (Input), `number` (Zahlenfeld), `boolean` (Toggle), `enum` (Select), `date` (Datums-Picker), quantifizierte Werte (Zahlenfeld mit Einheiten-Symbol).
- **Batch-Save statt Einzel-Save:** Edits sammeln sich im Grid (gestaged). Ein **„Speichern"**-Button überträgt alle Änderungen **gesammelt** an den Kern; **„Verwerfen"** verwirft. **Geänderte Zellen werden markiert**, ein **„Ungespeicherte Änderungen"**-Zähler ist sichtbar. Gründe: wenig Requests, konsistente Validierung/Vererbung im Kern, sicheres Rückgängigmachen über mehrere Zellen.
- **Deckelung des Batch-Saves (verbindlich):** Pro `POST /objects/batch-update`-Aufruf sind max. **500 Zeilen** erlaubt ([api-design.md](api-design.md)); das Grid speichert große, ungespeicherte Änderungsmengen in 500er-Blöcken, größere Auswahlen laufen über den Bulk-Wizard (async).
- **Abgelaufene Sitzung (implementiert):** Meldet der Kern beim Batch-Save einen ungültigen/abgelaufenen JWT (pro Zeile `success: false`), erkennt das Frontend das über `is_session_expired` (Fehlertext `invalid or expired token`), leert die Session und leitet zur Login-Seite um – statt einen fehlgeschlagenen Save anzuzeigen. Taucht die Meldung nur beim Schreiben auf (nicht beim Laden), liegt das an `GET /objects` ohne Auth (bewusste MVP-Vereinfachung).
- **Nicht inline editierbar** (Referenz-Picker, Medien/Assets, lange mehrzeilige Texte): Bearbeitung in einem **Modal-Editor direkt im Grid** – ohne die Grid-Position oder ungespeicherte Änderungen zu verlieren; der Modal-Edit landet wie ein Zellen-Edit im gestagten Batch. Das vollständige Formular bleibt per Zeilen-Aktion (z. B. Doppelklick / „Öffnen") erreichbar.
- **Mehrsprachige Felder im Grid:** standardmäßig wird nur die **Arbeitssprache** inline bearbeitet; zusätzliche Sprach-Spalten folgen später über den Spalten-Picker.
- **Vollständigkeits-Spalte & Filter:** Eine farbige **Vollständigkeits-Spalte** (grau <1 %, orange 1–99 %, grün 100 %) und Filter wie „nur unvollständige Produkte" (siehe Abschnitt 4).

## 2. Produkt-Formular (Drill-down)

Das Formular öffnet ein einzelnes Objekt zur tiefen Bearbeitung und Anreicherung.

### 2.1 Layout

- **Eine scrollbare Seite** mit Attributgruppen als **überschriebene, einklappbare Abschnitte** (Accordion; Standard aufgeklappt). Ein **Sprung-Menü** oben navigiert zu einer Gruppe (und klappt sie ggf. auf). Keine Tabs pro Gruppe – beim Anreichern arbeitet man über Gruppengrenzen hinweg, alle Gruppen sind gleichzeitig sichtbar.
- **Sichtbarkeit & Reihenfolge** der Abschnitte steuert das **Template** (nur Gruppen mit gültigen Attributen für den Typ; Reihenfolge aus `attribute_group.position` bzw. der Template-Reihenfolge).
- Umschalter **„Nur fehlende Pflichtfelder"** blendet unvollständige Gruppen/Felder hervor bzw. filtert sie (kombiniert mit der Vollständigkeit, Abschnitt 4).
- Später (Phase 9): **Pinned Attributes** (immer sichtbare Seitenleiste) und **Attribut-Suchleiste** im Formular.

### 2.2 Sprache (Hybrid-Modell)

- **Header-Sprach-Umschalter** („Arbeitssprache") bestimmt den primären Anzeige-/Eingabekontext und die Vollständigkeits-Berechnung.
- **Mehrsprachige Felder rendern editierbare Spalten pro aktiver Datensprache** (Standard: alle im Sprachbaum aktiven; konfigurierbar auf eine Teilmenge). Nicht-mehrsprachige Felder haben eine Spalte.
- **Fallback-Semantik sichtbar:** Fehlt ein lokaler Wert in der Arbeitssprache, wird der aufgelöste Wert (z. B. `de` für `de-AT`) **gedimmt mit einem Fallback-Icon/Marker** und Tooltip („von de") angezeigt; Tippen setzt einen lokalen Override, Löschen entfernt ihn (zurück zum Fallback). Sprachauflösung gemäß [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md).
- „Reiter pro Sprache" ist **nicht** vorgesehen; der Reiter-Gedanke bleibt nur als sekundäres Navigationsmittel für Gruppennavigation übrig.

### 2.3 Vererbungs-UX

Entlang der Kompositionskette `depends_on` ([datenwertvererbung.md](datenwertvererbung.md)):

- **Vererbte Werte werden in-place angezeigt**, aber klar als „nicht hier gepflegt" markiert: Wert gedimmt + **Vererbungs-Icon mit Tooltip** („vererbt von [Name/ID]" aus `from_object`).
- **Editieren = Override:** Ändern legt einen lokalen Wert am Objekt an (keine Sonder-Interaktion nötig).
- Auf überschriebenen Feldern erscheint die Aktion **„Zurück auf vererbt"** (Resync), die den lokalen Override entfernt.
- **Unterscheidung „nicht gepflegt" vs. „leer gesetzt"** (Vererbungsunterbrechung) über ein **Feld-Kontextmenü** mit zwei Aktionen: **„Leer setzen"** (explizit leer, unterbricht Vererbung) und **„Auf vererbt zurücksetzen"** (lokalen Wert entfernen, erbt wieder). Ein Marker zeigt den Zustand („leer gesetzt/Unterbrechung" vs. „vererbt").
- Ein **„Warnhinweis bei Konflikten"** entfällt: Im Ein-Eltern-Modell gibt es keine Konflikte.

### 2.4 Wertedarstellung

- **Typspezifische Steuerelemente:** `text` → Input/Textarea, `number` → Zahlenfeld, `boolean` → **Toggle**, `date` → lokalisierter Datums-Picker, `enum` → Select mit **farbigen Tags** in der Anzeige, `reference` → verlinkte **Chips** (Klick → Sprung zum referenzierten Objekt).
- **Einheiten-Umschalter** am Eingabefeld (Darstellung/Eingabe in anderer Einheit derselben Dimension; Speicherung in kanonischer SI-Einheit, [einheiten.md](einheiten.md)).
- **Enum-Option-Creation im Formular:** Fehlt eine Auswahl-Option, wird sie per `+` **direkt im Formular angelegt** und sofort angewendet (MVP).
- **Suchbarer Referenz-Picker:** Tippen → Trefferliste mit Label, gefiltert nach erlaubten Zieltypen (aus `relation_definition.target_types` bzw. der Attributdefinition); Auswahl erzeugt die Referenz, Anzeige als Chip (MVP). Die Vollständigkeits-Anzeige innerhalb des Pickers folgt später.

### 2.5 Kopfzeile & Status

- **Vollständigkeits-Widget im Formular-Header** (farbig; Klick → Aufschlüsselung nach Sprache + Liste fehlender Pflichtfelder → Sprung zum Feld).
- **Status-Badge** + **erlaubte Übergänge** (geliefert vom Kern, Statusmaschine im Python-Service, [workflow-freigaben.md](workflow-freigaben.md)) als Aktionen; Statuswechsel wird im Audit-Log protokolliert.
- Referenzen und Kategorien des Objekts werden angezeigt (Chips, editierbar).

## 3. Massendatenpflege

### 3.1 Bulk-Edit-Wizard (MVP)

- **Auswahl:** „Alle Treffer des aktuellen Filters" / „alle auf dieser Seite" / angehakte Zeilen (Mehrfachauswahl des Grids).
- **Ablauf:** Operation wählen → konfigurieren → **Bestätigen** → Ausführung als **Hintergrundjob** mit Fortschritt und **Report** (Benachrichtigung; Anwender arbeitet weiter).
- **Job-Status per Polling (verbindlich):** Solange ein Bulk-Job läuft, pollt das Frontend den `job_run` per REST (`GET /jobs/runs?ids=…`, [api-design.md](api-design.md)) und zeigt Status/Fortschritt an; nach Abschluss erscheint der **Report** (Erfolg/Fehler pro Objekt) aus `job_run.result` (Benachrichtigung).
- **Operationssatz (passend zum Datenmodell):**
  - **Attributwert setzen** (ersetzt; für Text/Zahl/Boolean/Datum/Enum),
  - **Attributwert leeren** (auf „nicht gepflegt" zurücksetzen – beachtet die Vererbung: erbende Kinder fallen auf den vererbten Wert zurück),
  - **Status ändern** (Workflow-Übergang),
  - **Kategorie hinzufügen / entfernen** (Kategoriebaum-Auswahl),
  - **Elternobjekt ändern** (Re-Parenting in Bulk, mit Zyklenschutz und Typrestriktionsprüfung; bewusste Kaskadenwirkung),
  - **Massen-Löschen** (Soft-Delete-Kaskade über den Teilbaum; Warnung „irreversibel").
- **Validierung:** Der Kern prüft pro Objekt (Typrestriktionen, Eindeutigkeit, Workflow); Verstöße erscheinen im Report.

### 3.2 Weitere Modi & Abgrenzung

- **Im MVP bereits enthalten:** Inline-Editing im Grid (Abschnitt 1.3) als „Spreadsheet-artige" Massenpflege; **MEF-Import/Export** ist Bestandteil von Phase 6 ([integration-eventsystem.md](integration-eventsystem.md)).
- **Später (Phase 9):** **Find & Replace** (Text über eine Auswahl), **sequenzielles Editieren** (jedes ausgewählte Objekt nacheinander im Formular mit Fortschrittsbalken, „Speichern & weiter" – dünner Wrapper ums bestehende Formular), **Send-for-approval** (Bulk-Änderungen durch den Freigabe-Workflow schleusen).

## 4. Vollständigkeit (Completeness)

- **Berechnung im Rust-Kern** (nicht im Frontend): Prozent der **Pflichtattribute des Typs** (aus `required_types`), die gesetzt sind – inklusive **vererbter Werte** (ein vererbtes Pflichtfeld gilt als gesetzt). Granularität im MVP: **pro (Objekttyp × Sprache)**. Kanäle (per-Kanal-Vollständigkeit) folgen mit den kanalspezifischen Freigaben (später).
- **Anzeige überall:**
  - **Grid-Spalte** (farbig: grau <1 %, orange 1–99 %, grün 100 %) + Filter „nur unvollständige Produkte".
  - **Formular-Widget** im Header (farbig; Klick → Sprachaufschlüsselung + Liste fehlender Pflichtfelder → Sprung ins Feld) und Umschalter „nur fehlende Pflichtfelder".
  - **Dashboard-Kacheln** als Aufgaben-Shortcuts (Abschnitt 5).
- MVP bewusst **ohne** „Improvable/empfohlen"-Stufe; diese folgt später.

## 5. Dashboard (Arbeitsübersicht)

- **Kacheln:** Anzahl Objekte pro Typ/Status.
- **Vollständigkeits-Kacheln als Aufgaben-Shortcuts:** z. B. „42 Produkte unvollständig (de)" – Klick öffnet das **Grid mit voreingestelltem Filter** „nur unvollständige Produkte" in der Arbeitssprache. Das Dashboard wird damit zum **Einstieg in die Pflegearbeit** („Was fehlt?"), ohne eine eigene Aufgabenmaschine.
- **Letzte Änderungen** (Audit-Auszug) und **laufende/letzte Jobs** (Status aus [jobs-scheduler.md](jobs-scheduler.md)).
- Bewusst **nicht im MVP** (später, Phase 9): Starred-Entitäten, Work Areas/Aufgabenwarteschlangen, Kanäle.

## 6. Phasierung

| Arbeitsfläche     | MVP (Phase 7)                                                                                                                                                              | Später (Phase 9)                                                                                     |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Grid              | ui.aggrid, Kategorien-/Strukturbaum, Spalten + Pinning, persönliche Ansichten, Inline-Editing + Batch-Save, Modal-Editoren, Vollständigkeits-Spalte/Filter, Status-Badge   | Sprach-Spalten im Grid (Spalten-Picker), Baum direkt im Grid (Enterprise/Tabulator, nur falls nötig) |
| Formular          | scrollbare Seite, einklappbare Gruppen, Sprung-Menü, Hybrid-Sprache, Vererbungs-UX, Wertedarstellung, Enum-Option-Creation, Referenz-Picker, Vollständigkeits-Widget       | Pinned Attributes, Attribut-Suchleiste, Vollständigkeit im Referenz-Picker                           |
| Massendatenpflege | Bulk-Wizard (definierter Operationssatz, Hintergrundjob + Report)                                                                                                          | Find & Replace, sequenzielles Editieren, Send-for-approval                                           |
| Dashboard         | Arbeitsübersicht mit Vollständigkeits-Shortcuts                                                                                                                            | Starred, Work Areas, Kanäle                                                                          |

> **Abhängigkeiten zu anderen Dokumenten:** `datenmodell.md` (Template-Felder `applicable_types`/`required_types`), `api-design.md` (Vollständigkeit in Antworten/Filter, Bulk-Endpunkt, Such-Endpunkt für Referenz-Picker, Batch-Save), `jobs-scheduler.md` (Bulk als Job-Typ), `frontend.md` (Modulübersicht), Abschnitt 11 + Phase 7 im Hauptdokument.

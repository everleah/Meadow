# API-Design (OpenAPI REST)

- **Hauptdokument:** Abschnitt 5 ([API-Design](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 1–9
- **Stand:** Entwurf

Der Rust-Kern stellt eine vollständige REST-API bereit, dokumentiert mit OpenAPI 3.x.

## Wichtige Endpunkte (Auszug)

| Methode | Pfad                              | Beschreibung                                    |
|---------|-----------------------------------|-------------------------------------------------|
| POST    | `/auth/login`                     | JWT-Token erhalten                              |
| POST    | `/objects`                        | Neues Objekt anlegen                            |
| GET     | `/objects/{id}`                   | Objekt inkl. Metadaten und Referenzen laden     |
| PATCH   | `/objects/{id}`                   | Teilaktualisierung der Attribute/Status         |
| DELETE  | `/objects/{id}`                   | Objekt löschen (Soft Delete, Kaskade über die Kompositionskette; Aggregationen bleiben unberührt) |
| GET     | `/objects?type=product&filter=...`| Suche & Filterung mit Pagination; optional `completeness_lt`/`missing_required` (Vollständigkeits-Filter) |
| GET     | `/objects/picker?q=...&types=...`| Such-Endpunkt für den Referenz-Picker (Label-Suche, optional Zieltyp-Filter) |
| GET     | `/objects/by-key?type=product&key=sku&value=SKU-123` | Objekt über einen Business-Schlüssel laden (z. B. SKU; eindeutig pro Objekttyp) |
| POST    | `/references`                     | Referenz zwischen zwei Objekten erstellen (Validierung gegen die Relation-Definition) |
| GET     | `/relation-definitions`           | Relationstypen auflisten                        |
| POST    | `/relation-definitions`           | Benutzerdefinierten Relationstyp anlegen (nur Aggregation, Phase 9) |
| PATCH   | `/objects/{id}/parent`            | Kompositions-Elternteil ändern (Re-Parenting, Zyklenschutz) |
| POST    | `/objects/bulk`                   | Massenoperation über eine Auswahl (Attributwert setzen/leeren, Status, Kategorie, Re-Parenting, Löschen) – Ausführung als Hintergrundjob (`job_run`) |
| POST    | `/objects/batch-update`           | Gesammelte Inline-Edits aus dem Grid (Batch-Save) in einem Aufruf anwenden |
| GET     | `/objects/{id}/children`          | Direkte Kompositions-Kinder eines Objekts       |
| GET     | `/objects/{id}/references`        | Alle Referenzen eines Objekts (mit Metadaten)   |
| GET     | `/objects/{id}/graph?depth=3`     | Graph-Traversierung ab Objekt                   |
| GET     | `/attribute-groups`               | Attributgruppen verwalten                       |
| POST    | `/attribute-definitions`          | Neue Attributdefinition anlegen                 |
| GET     | `/units`                          | Einheiten-Katalog auflisten (Filter: `dimension`, `system`) |
| POST    | `/units`                          | Benutzerdefinierte Einheit anlegen              |
| PATCH   | `/units/{id}`                     | Benutzerdefinierte Einheit ändern               |
| DELETE  | `/units/{id}`                     | Benutzerdefinierte Einheit löschen (nur `custom`) |
| GET     | `/units/convert`                  | Wert zwischen Einheiten umrechnen (`value`, `from`, `to`) |
| POST    | `/jobs/{job_def_id}/run`          | Job manuell starten                             |
| GET     | `/jobs/{job_def_id}/runs`         | Job-Historie abrufen                            |
| PATCH   | `/jobs/runs/{run_id}`             | Status eines Job-Runs aktualisieren (Worker)    |
| POST    | `/search`                         | Volltextsuche über Objekte                      |
| GET     | `/auth/me`                        | Rollen, Berechtigungen & Scope des Nutzers      |
| POST    | `/users`                          | Benutzer anlegen                                |
| PATCH   | `/users/{id}`                     | Benutzer bearbeiten (Rolle, Status)             |
| DELETE  | `/users/{id}`                     | Benutzer löschen (Superadmin-Schutz)            |
| POST    | `/groups`                         | Gruppe anlegen                                  |
| POST    | `/groups/{id}/members`            | Benutzer einer Gruppe zuordnen                  |

- **Authentifizierung**: JWT-Bearer-Token im Authorization-Header.
- **Rollen**: `superadmin`, `admin`, `editor`, `viewer` – werden im JWT abgebildet (Details in Abschnitt 9 des Hauptdokuments).
- **Pagination**: Cursor-basiert für große Listen.

## Relationen & Komposition in der API

- Jede Relation referenziert eine Definition aus `/relation-definitions`; beim Anlegen prüft der Kern die `kind`-Semantik und die optionalen Typrestriktionen (`source_types`/`target_types`) gegen Quell- und Zielobjekt.
- **Komposition (`depends_on`):** genau eine Kante pro Objekt. Beim Anlegen ohne expliziten Elternteil wird der Universal-Root als Kompositions-Elternteil gesetzt. `PATCH /objects/{id}/parent` verschiebt den Teilbaum (Zyklenschutz, Typrestriktionsprüfung, Audit-Eintrag, atomar).
- **Aggregationen (n:m)** können frei angelegt und entfernt werden; ihre Entfernung kaskadiert nie.
- **Löschen** (`DELETE /objects/{id}`) kaskadiert als Soft Delete über den gesamten Kompositionsteilbaum (`depends_on`).

## Business-Schlüssel in der API

Ein **Business-Schlüssel** (z. B. SKU) ist ein Attribut, dessen Definition mit `is_key: true` markiert ist (Konvention gemäß Abschnitt 4 / [datenmodell.md](datenmodell.md)):

- **Eindeutigkeit pro Typ:** Beim Schreiben (`POST /objects`, `PATCH /objects/{id}`) prüft der Kern die Eindeutigkeit pro Objekttyp (Unique-Index `(type, attributes.<name>)`); Verstöße werden mit einem entsprechenden Fehler abgelehnt.
- **Regeln:** Nur `text`, nicht lokalisiert, nicht vererbbar; optional (Objekte dürfen ohne Schlüssel existieren); Werte sind änderbar (Eindeutigkeitsprüfung + Audit).
- **Key-Lookup:** `GET /objects/by-key?type=<typ>&key=<attribut>&value=<wert>` lädt genau ein Objekt über einen Business-Schlüssel (z. B. die SKU). Existiert kein Objekt, wird 404 geliefert.
- **Suche/Filterung:** Schlüsselattribute sind über die normale Suche/Filterung und den Index abfragbar.

## Datums- & Zeitwerte in der API

Alle Zeitwerte in Requests und Responses folgen der Datumswerte-Konvention (Abschnitt 4 / [datenmodell.md](datenmodell.md)):

- Zeitwerte sind **immer ISO-8601-Strings** mit Zeitzonenangabe: `2026-08-14T10:00:00Z` (UTC) oder `2026-08-14T12:00:00+02:00` (Offset).
- Strings **ohne** Zeitzonenangabe werden mit **HTTP 400** abgelehnt.
- Der Kern **normalisiert** eingehende Werte auf UTC; ausgehende Werte werden als UTC (`…Z`) geliefert.
- Zeitwerte gelten für Systemfelder (`created_at`, `updated_at`, `started_at`, `finished_at`, `timestamp`) sowie für Attribute vom Typ `date`/`datetime` (Attributdefinitionen, Abschnitt 4).

## Werte mit Einheiten in der API

Quantifizierte Werte folgen der Einheiten-Konvention (Abschnitt 4 / [einheiten.md](einheiten.md)):

- Werte mit Einheit werden **immer als Objekt** übertragen: `{"value": 12.5, "unit": "mm"}`. Fehlt `unit`, gilt die kanonische SI-Einheit der Dimension.
- Beim **Schreiben** (z. B. `POST /objects`, `PATCH /objects/{id}`) rechnet der Kern in die kanonische SI-Einheit um; gespeichert wird die reine Zahl.
- Beim **Lesen** liefert der Kern den Wert in der Standard-Anzeigeeinheit der Attributdefinition (`display_unit_id`); mit `?unit=<symbol>` kann eine andere Einheit derselben Dimension angefordert werden.
- Der Einheiten-Katalog (`/units`) enthält alle SI-Einheiten und üblichen Darstellungen (schreibgeschützt) sowie benutzerdefinierte Einheiten. `GET /units/convert` rechnet einen Wert zwischen zwei Einheiten um und liefert `{"value": …, "unit": …}` zurück.

## Attributtypen & Datentypwechsel

- Der Datentyp eines Attributs wird über `type` in der Attributdefinition festgelegt; die API behandelt `type` als **erweiterbare Werteliste** (neue Typen dürfen ergänzt, bestehende nicht umbenannt werden).
- In der ersten Version ist **kein** Endpunkt zum Wechseln des Datentyps vorgesehen. Ein späterer Wechsel (Phase 9) erfordert eine Migration der vorhandenen Werte, einen Audit-Log-Eintrag und folgt der Kompatibilitätsmatrix in [datenmodell.md](datenmodell.md#datentypwechsel).

## Vollständigkeit (Completeness) in der API

Die Vollständigkeit wird **im Rust-Kern berechnet** und ist Teil der Listen- und Detailantworten (nicht nur im Frontend), damit sie überall konsistent ist.

- **Definition:** Anteil der **Pflichtattribute des Objekttyps** (aus `required_types` der Attributdefinition, siehe [datenmodell.md](datenmodell.md)), die gesetzt sind – inklusive **vererbter** Werte (entlang `depends_on`). Granularität im MVP: **pro (Objekttyp × Sprache)**.
- **Lieferung:** `GET /objects` liefert pro Objekt ein Feld `completeness: { "<sprache>": <0–100> }` (bzw. für die angeforderte Arbeitssprache). `GET /objects/{id}` liefert zusätzlich die **Liste fehlender Pflichtattribute** (für das Formular-Widget).
- **Filterung:** Query-Parameter, z. B. `?completeness_lt=100` bzw. `?missing_required=true` („nur unvollständige Produkte“), optional mit `?language=<code>`.
- **Berechnungsbasis:** Aufgelöste Sicht (`?resolve_inheritance=true`); leere Werte (Vererbungsunterbrechung) zählen als „gesetzt“.
- Details zur UI-Darstellung: [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#4-vollständigkeit-completeness).

## Massenoperationen (Bulk) & Batch-Save

- **`POST /objects/bulk`** führt eine Massenoperation auf einer **Auswahl** aus („alle Treffer des Filters“ / „alle auf der Seite“ / explizite ID-Liste). Operationsarten: `set_attribute` (ersetzen), `clear_attribute` (auf „nicht gepflegt“ zurücksetzen), `change_status`, `add_category`/`remove_category`, `change_parent` (Re-Parenting, Zyklenschutz), `delete` (Soft-Delete-Kaskade).
- Die Ausführung erfolgt **als Hintergrundjob** (siehe [jobs-scheduler.md](jobs-scheduler.md#job-typen), Typ `bulk_operation`): Der Endpunkt erzeugt ein `job_run` und liefert dessen ID; der Report (Erfolg/Fehler pro Objekt, Validierungsfehler) steht nach Abschluss am `job_run.result`.
- **`POST /objects/batch-update`** wendet die **gesammelten Inline-Edits** aus dem Grid (Batch-Save) an – eine Liste von Teiländerungen (`{id, attributes, status, …}`) in einem Aufruf; Validierung erfolgt pro Objekt (Eindeutigkeit, Typrestriktionen, Workflow).

## Referenz-Picker-Suche

Der **suchbare Referenz-Picker** der Oberfläche nutzt `GET /objects/picker` (Label-Volltextsuche, optional eingeschränkt auf erlaubte Zieltypen aus `relation_definition.target_types` bzw. der Attributdefinition). Die Antwort enthält kompakte Treffer (ID, Typ, aufgelöstes Label, Status). Details zur UI: [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#24-wertedarstellung).

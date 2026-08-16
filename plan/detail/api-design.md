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
| DELETE  | `/objects/{id}`                   | Objekt löschen (soft delete)                    |
| GET     | `/objects?type=product&filter=...`| Suche & Filterung mit Pagination                |
| POST    | `/references`                     | Referenz zwischen zwei Objekten erstellen       |
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

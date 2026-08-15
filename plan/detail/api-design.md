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

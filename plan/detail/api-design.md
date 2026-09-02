# API-Design (OpenAPI REST)

- **Hauptdokument:** Abschnitt 5 ([API-Design](../meadow-pim-architektur.md#5-api-design-openapi-rest))
- **Relevante Phasen:** Phase 1–9
- **Stand:** Implementiert bis Phase 6 (Objekte, Suche, Regeln, Jobs/Scheduler, System/Logs, Import/Export, Webhooks) sowie Batch-Save (`POST /objects/batch-update`, Phase 7) und den **Referenz-Picker** (Phase 7): `GET /objects/picker`, `GET /objects/{id}/references` (mit aufgelösten `in_object`/`out_object`-Summaries), `DELETE /references/{id}` und `GET /relation-definitions`. Das **Produkt-Formular** (Phase 7) nutzt die bestehenden `GET /objects/{id}` (`?locale=&resolve_inheritance=true`) und `PATCH /objects/{id}`; neu ist **`GET /languages`** (Daten-Sprachen aus `language_tree` für die Hybrid-Mehrsprachigkeit des Formulars). Der geplante **Relationen-Abschnitt** (Abschnitt 2.6, `frontend-arbeitsflaechen.md`) nutzt die vorhandenen Graph-Endpunkte (`GET /objects/{id}/references`, `POST`/`DELETE /references`, `PATCH /objects/{id}/parent`, `GET /objects/picker`, `GET /relation-definitions`). Offene Endpunkte folgen in Phase 7/9 (Bulk, Graph).

Der Rust-Kern stellt eine vollständige REST-API bereit, dokumentiert mit OpenAPI 3.x.

## Wichtige Endpunkte (Auszug)

| Methode | Pfad                                                 | Beschreibung                                                                                                                                             |
| ------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST    | `/auth/login`                                        | JWT-Token erhalten                                                                                                                                       |
| POST    | `/objects`                                           | Neues Objekt anlegen                                                                                                                                     |
| GET     | `/objects/{id}`                                      | Objekt inkl. Metadaten und Referenzen laden                                                                                                              |
| PATCH   | `/objects/{id}`                                      | Teilaktualisierung der Attribute/Status                                                                                                                  |
| PATCH   | `/objects/{id}/computed`                            | Nur deklariert-berechnete Werte mergen (Worker-Schreibpfad, `X-Api-Key` oder Admin; ohne Regel-Triggering)                                                |
| DELETE  | `/objects/{id}`                                      | Objekt löschen (Soft Delete, Kaskade über die Kompositionskette; Aggregationen bleiben unberührt)                                                        |
| GET     | `/objects?type=product&filter=...`                   | Suche & Filterung mit Pagination; optional `completeness_lt`/`missing_required` (Vollständigkeits-Filter), **`ancestor=<id>`** (serverseitige Teilbaum-Filterung über die Kompositionskette, `subtree_keys`), **`category=<id>`** (Objekte einer Kategorie inkl. Unterkategorien über `category_member`, `category_member_keys`) oder **`uncategorized=true`** (Objekte ohne Kategoriezuordnung; ohne `category`/`root`) |
| GET     | `/objects/picker?q=...&types=...`                    | Such-Endpunkt für den Referenz-Picker (Label-Suche, optional Zieltyp-Filter)                                                                             |
| GET     | `/objects/by-key?type=product&key=sku&value=SKU-123` | Objekt über einen Business-Schlüssel laden (z. B. SKU; eindeutig pro Objekttyp)                                                                          |
| GET     | `/objects/by-source?source=middleware&source_id=ext-1` | Objekt über eine Quell-Referenz laden (Import-Dedup für Objekte ohne Schlüssel)                                                                           |
| POST    | `/references`                                        | Referenz zwischen zwei Objekten erstellen (Validierung gegen die Relation-Definition)                                                                    |
| GET     | `/relation-definitions`                              | Relationstypen auflisten                                                                                                                                 |
| POST    | `/relation-definitions`                              | Benutzerdefinierten Relationstyp anlegen (nur Aggregation, Phase 9)                                                                                      |
| PATCH   | `/objects/{id}/parent`                               | Kompositions-Elternteil ändern (Re-Parenting, Zyklenschutz)                                                                                              |
| POST    | `/objects/bulk`                                      | Massenoperation über eine Auswahl (Attributwert setzen/leeren, Status, Kategorie, Re-Parenting, Löschen) – Ausführung als Hintergrundjob (`job_run`)     |
| POST    | `/objects/batch-update`                              | Gesammelte Inline-Edits aus dem Grid (Batch-Save) in einem Aufruf anwenden                                                                               |
| GET     | `/objects/tree`                                      | Kompositionsbaum ab Universal-Root (verschachtelt `{id, children}`; für die Struktur-Navigation im Grid)                                                |
| GET     | `/objects/{id}/children`                             | Direkte Kompositions-Kinder eines Objekts; **paginiert** (`?limit`/`?offset`) und liefert je Kind ein **`hasChildren`**-Flag (Lazy Loading); überspringt Verweise auf nicht existente Objekte. Mit **`?count_members=true`** tragen `category`-Kinder zusätzlich ein **`memberCount`** (Anzahl zugeordneter Objekte via `category_member` inkl. Unterkategorien, für den Kategorien-Baum-Zähler) |
| GET     | `/objects/{id}/references`                           | Alle Referenzen eines Objekts (mit Metadaten); `?locale=` löst `in_object`/`out_object` als `{id, type, label, status}` auf                                                                                 |
| DELETE  | `/references/{id}`                                   | Referenz löschen (editor+; 204 bei Erfolg)                                                                                                                |
| GET     | `/objects/{id}/graph?depth=3`                        | Graph-Traversierung ab Objekt                                                                                                                            |
| GET     | `/attribute-groups`                                  | Attributgruppen verwalten                                                                                                                                |
| GET     | `/languages`                                         | Daten-Sprachen aus `language_tree` auflisten (`code`, `parent`, `label`, `fallback_order`; für die Hybrid-Mehrsprachigkeit des Formulars)                 |
| POST    | `/attribute-definitions`                             | Neue Attributdefinition anlegen                                                                                                                          |
| GET     | `/units`                                             | Einheiten-Katalog auflisten (Filter:`dimension`, `system`)                                                                                               |
| POST    | `/units`                                             | Benutzerdefinierte Einheit anlegen                                                                                                                       |
| PATCH   | `/units/{id}`                                        | Benutzerdefinierte Einheit ändern                                                                                                                        |
| DELETE  | `/units/{id}`                                        | Benutzerdefinierte Einheit löschen (nur`custom`)                                                                                                         |
| GET     | `/units/convert`                                     | Wert zwischen Einheiten umrechnen (`value`, `from`, `to`)                                                                                                |
| POST    | `/jobs/{job_def_id}/run`                             | Job manuell starten                                                                                                                                      |
| GET     | `/jobs/{job_def_id}/runs`                            | Job-Historie abrufen                                                                                                                                     |
| PATCH   | `/jobs/runs/{run_id}`                                | Status eines Job-Runs aktualisieren (Worker)                                                                                                             |
| GET     | `/jobs`                                              | Job-Definitionen auflisten (Filter: `type`, `enabled`; nur `admin`/`superadmin`)                                                                        |
| POST    | `/jobs`                                              | Job-Definition anlegen (nur `admin`/`superadmin`, Name eindeutig)                                                                                       |
| GET     | `/jobs/{id}`                                         | Job-Definition laden (nur `admin`/`superadmin`)                                                                                                         |
| PATCH   | `/jobs/{id}`                                         | Job-Definition bearbeiten (`schedule` mit `null` löschbar; nur `admin`/`superadmin`)                                                                     |
| GET     | `/connectors`                                        | Konnektoren auflisten (Filter:`type`, `enabled`; `X-Api-Key` oder Admin)                                                                                |
| POST    | `/connectors`                                        | Konnektor anlegen (nur `admin`/`superadmin`, Name eindeutig)                                                                                             |
| GET     | `/connectors/{id}`                                   | Konnektor laden (`X-Api-Key` oder Admin)                                                                                                                 |
| PATCH   | `/connectors/{id}`                                   | Konnektor bearbeiten (nur `admin`/`superadmin`)                                                                                                          |
| DELETE  | `/connectors/{id}`                                   | Konnektor löschen (nur `admin`/`superadmin`)                                                                                                             |
| POST    | `/imports`                                           | MEF-Import anstoßen (JSON oder Datei-Upload); asynchron, liefert`job_run`-ID (202)                                                                       |
| GET     | `/imports/{document_id}`                             | Gespeichertes MEF-Dokument laden (Worker, `X-Api-Key` oder Admin)                                                                                        |
| POST    | `/imports/{document_id}/complete`                    | `import.completed`-Summary-Event in die Outbox schreiben (Worker, `emit_events: false`)                                                                |
| GET     | `/jobs/runs?ids=…`                                   | Job-Run(s) nach ID abrufen (Status, Fortschritt, Report) – für UI-Polling                                                                                |
| POST    | `/search`                                            | Volltextsuche über Objekte                                                                                                                               |
| GET     | `/auth/me`                                           | Rollen, Berechtigungen & Scope des Nutzers                                                                                                               |
| POST    | `/users`                                             | Benutzer anlegen                                                                                                                                         |
| PATCH   | `/users/{id}`                                        | Benutzer bearbeiten (Rolle, Status)                                                                                                                      |
| DELETE  | `/users/{id}`                                        | Benutzer löschen (Superadmin-Schutz)                                                                                                                     |
| POST    | `/groups`                                            | Gruppe anlegen                                                                                                                                           |
| POST    | `/groups/{id}/members`                               | Benutzer einer Gruppe zuordnen                                                                                                                           |
| GET     | `/rules`                                             | Business Rules auflisten (Filter:`type`, `state`, `applies_to`)                                                                                          |
| POST    | `/rules`                                             | Regel anlegen (Compile-Check beim Speichern)                                                                                                             |
| GET     | `/rules/{id}`                                        | Regel laden (inkl.`version`)                                                                                                                             |
| PATCH   | `/rules/{id}`                                        | Regel bearbeiten (Compile-Check; Version++ + Audit)                                                                                                      |
| POST    | `/rules/{id}/activate`                               | Regel aktivieren (`draft`/`disabled` → `active`; nur `admin`/`superadmin`)                                                                               |
| POST    | `/rules/{id}/disable`                                | Regel deaktivieren                                                                                                                                       |
| POST    | `/rules/{id}/dry-run`                                | Dry-Run gegen Beispielobjekt(e) oder konstruierten Payload (kein Persistieren)                                                                           |
| POST    | `/rules/{id}/test-batch`                             | Rule Lab: Ausführung gegen Objektauswahl (read-only, kein Persistieren)                                                                                  |
| POST    | `/rules/{id}/recompute`                              | Recompute/Bulk-Job über betroffene Objektmenge anstoßen (R5)                                                                                             |
| POST    | `/system/logs`                                       | Kuratierte Log-Einträge aus Komponenten entgegennehmen (Batch, non-blocking; Service-Account/API-Key)                                                    |
| GET     | `/system/logs`                                       | System-Logs abrufen (Filter:`service`, `category`, `level`, `from`/`to`, `q`; Pagination; nur `admin`/`superadmin`)                                      |
| GET     | `/system/settings`                                   | Systemeinstellungen lesen (aufgelöster Wert: Default < ENV < DB)                                                                                         |
| PUT     | `/system/settings/{key}`                             | Systemeinstellung ändern (persistiert, Audit; nur`admin`/`superadmin`)                                                                                   |

- **Authentifizierung**: JWT-Bearer-Token im Authorization-Header.
- **Machine-to-Machine**: `POST /system/logs`, `GET /system/settings`, `PATCH /jobs/runs/{run_id}`, `PATCH /objects/{id}/computed`, `POST /imports`, `GET /imports/{document_id}`, `GET /rules`, `GET /connectors` (und `GET /connectors/{id}`) sowie – für den Import-Konnektor – `POST /objects`, `PATCH /objects/{id}` und `POST /references` akzeptieren zusätzlich zum JWT einen `X-Api-Key` (`LOG_INGEST_API_KEY`) – für Worker/Service-Accounts. API-Key-Schreibvorgänge werden dem Akteur `system` zugeschrieben.
- **Rollen**: `superadmin`, `admin`, `editor`, `viewer` – werden im JWT abgebildet (Details in Abschnitt 9 des Hauptdokuments).
- **Pagination**: Cursor-basiert für große Listen (Ziel). Phase 1 nutzt vorläufig Offset-Pagination (`limit`/`start`); Cursor-Pagination folgt in einer späteren Phase.
- **IDs**: Ressourcen-IDs sind SurrealDB-Record-IDs (`tabelle:key`); über die API angelegte Datensätze erhalten ULID-Keys (z. B. `object:01M0…`, `user:<ulid>`). Querverweis-Felder enthalten den Record-Key (z. B. `unit_id: "unit_m"`). Details in [datenmodell.md](datenmodell.md).

## Business Rules in der API

- **CRUD & Lifecycle:** Regeln werden über `/rules` verwaltet (nur `admin`/`superadmin` für `manage`). Beim Speichern prüft der Kern den Script-Code (Compile-Check + Sandbox-Regeln) – unzulässige Skripte werden abgelehnt. Änderungen und Zustandswechsel sind auditiert.
- **Dry-Run / Rule Lab:** `POST /rules/{id}/dry-run` (Beispielobjekt: reale Objekt-ID oder konstruierter Payload) und `POST /rules/{id}/test-batch` (Objektauswahl) führen Regeln **read-only ohne Persistenz** aus und liefern Verletzungen, berechnete Werte und Log (Abschnitt 8 / [business-rules.md](business-rules.md)).
- **Schreibpfad (intern):** `validation`/`computed`/`workflow_guard` werden beim Schreiben durch einen **synchronen NATS-Request/Reply-Aufruf** des Rust-Kerns an den Python-Service ausgewertet (Subject `rules.evaluate`, nicht Teil der öffentlichen OpenAPI); der Kern erzwingt das Ergebnis atomar (fail-closed + Circuit Breaker).
- **Locale im Write-Pfad:** `POST /objects` und `PATCH /objects/{id}` akzeptieren `?locale=`; der Kern löst damit mehrsprachige + vererbte Werte für die **Regelauswertung** auf (nur Evaluation – vererbte Werte werden **nicht** als eigene Werte persistiert).
- **Recompute:** `POST /rules/{id}/recompute` stößt einen Recompute-/Bulk-Job über die betroffene Objektmenge an (R5, Report über `/jobs/runs`).

## Relationen & Komposition in der API

- Jede Relation referenziert eine Definition aus `/relation-definitions`; beim Anlegen prüft der Kern die `kind`-Semantik und die optionalen Typrestriktionen (`source_types`/`target_types`) gegen Quell- und Zielobjekt.
- **Komposition (`depends_on`):** genau eine Kante pro Objekt. Beim Anlegen ohne expliziten Elternteil wird der Universal-Root als Kompositions-Elternteil gesetzt. `PATCH /objects/{id}/parent` verschiebt den Teilbaum (Zyklenschutz, Typrestriktionsprüfung, Audit-Eintrag, atomar).
- **Kompositionsbaum (implementiert):** `GET /objects/tree` liefert den gesamten Kompositionsbaum ab Universal-Root als verschachtelte `{id, children}`-Knoten in **einem** Aufruf (`SELECT in, out FROM reference WHERE definition_id = 'rel_depends_on'` + rekursiver Aufbau mit Zyklenschutz). Namen löst das Frontend clientseitig auf; `GET /objects/{id}/children` bleibt für die Einzelknoten-Abfrage. **Performance:** Ein Ein-Call-Endpunkt statt rekursiver `children`-Aufrufe ist bewusst gewählt, um viele HTTP-Round-Trips und das NiceGUI-Seiten-Build-`response_timeout` zu vermeiden und mit der Objektmenge zu skalieren. `GET /objects/{id}/graph?depth=3` (Graph-Traversierung) folgt als spätere Ausbaustufe.
- **Lazy Loading (geplant):** Für große Bäume wird der Ein-Call-Endpunkt durch **on-demand**-Laden ergänzt. Geplante API-Erweiterungen: (a) `GET /objects/tree` liefert optional **nur Top-Level**-Knoten inkl. `hasChildren`/`childrenCount` (z. B. `?top_only=true`); (b) `GET /objects/{id}/children` wird **paginiert** (`?limit`/`?offset`, Standard ~100) für Knoten mit vielen direkten Kindern; (c) **Teilbaum-Filter serverseitig** als Folge der Lazy-Loading-Entscheidung — z. B. `GET /objects?ancestor=<id>` (oder ein geflatteter Ancestor-Pfad pro Objekt als Index), damit die Grid-Filterung ohne vollständige Baum-Daten im Client funktioniert.
- **Aggregationen (n:m)** können frei angelegt und entfernt werden; ihre Entfernung kaskadiert nie.
- **Löschen** (`DELETE /objects/{id}`) kaskadiert als Soft Delete über den gesamten Kompositionsteilbaum (`depends_on`).

## Business-Schlüssel in der API

Ein **Business-Schlüssel** (z. B. SKU) ist ein Attribut, dessen Definition mit `is_key: true` markiert ist (Konvention gemäß Abschnitt 4 / [datenmodell.md](datenmodell.md)):

- **Eindeutigkeit pro Typ:** Beim Schreiben (`POST /objects`, `PATCH /objects/{id}`) prüft der Kern die Eindeutigkeit pro Objekttyp **anwendungsseitig** (konzeptionell `(type, attributes.<name>)`); Verstöße werden mit `409 Conflict` abgelehnt.
- **Regeln:** Nur `text`, nicht lokalisiert, nicht vererbbar; optional (Objekte dürfen ohne Schlüssel existieren); Werte sind änderbar (Eindeutigkeitsprüfung + Audit).
- **Key-Lookup:** `GET /objects/by-key?type=<typ>&key=<attribut>&value=<wert>` lädt genau ein Objekt über einen Business-Schlüssel (z. B. die SKU). Existiert kein Objekt, wird 404 geliefert.
- **Suche/Filterung:** Schlüsselattribute sind über die normale Suche/Filterung abfragbar.

## Volltextsuche in der API

- **`POST /search`** durchsucht die Attributwerte aller Objekte per Fulltext (Index über `attributes`, Analyzer `meadow_std`). Payload: `{ "q": string, "types"?: string[], "limit"?: number (default 100, max 1000), "start"?: number }`. Antwort: Treffer-Objekte inkl. `score` (Relevanz), absteigend sortiert. Zugriff wie `GET /objects` (aktuell ohne Auth).

## System-Logs & Systemeinstellungen in der API

**System-Logs** (Konvention Abschnitt 12 / [betrieb-deployment.md](betrieb-deployment.md#11-monitoring--logging), Tabellen [datenmodell.md](datenmodell.md)):

- **`POST /system/logs`:** Nimmt einen **Batch** kuratierter Log-Einträge entgegen (`[{service, level, category, message, trace_id?, context?}, …]`; `created_at` setzt der Kern auf Empfangszeit, UTC). Authentifizierung über Service-Account/API-Key (`X-Api-Key`, Env `LOG_INGEST_API_KEY`); als Dev-Fallback zusätzlich Admin-JWT. Non-blocking; im MVP begrenzt die Batchgröße (max. 1000 Einträge) – ein echtes Rate-Limit folgt als Ausbaustufe. Die Antwort ist für den Sender ohne fachliche Bedeutung – Logging darf nie blockieren/fehlschlagen.
- **`GET /system/logs`:** Liefert persistierte Einträge mit Filterung (`service`, `category`, `level`, `from`/`to` als ISO-8601-UTC, `q` als `CONTAINS`-Textsuche auf `message`) und Offset-Pagination (`limit`/`start`, wie übrige Listen in Phase 1–2); Sortierung neueste zuerst. Nur `admin`/`superadmin` (`log.read`).
- **Zugriffsschutz:** Technische Logs sind **kein** fachliches Arbeitsmittel – `editor`/`viewer` erhalten 403.

**Systemeinstellungen:**

- **`GET /system/settings`:** Liefert alle Einstellungen inkl. **aufgelöstem** Wert (Code-Default < ENV < DB) und Quelle. Nur `admin`/`superadmin`.
- **`PUT /system/settings/{key}`:** Setzt/persistiert eine Runtime-Einstellung (z. B. `log_retention_days`); validiert gegen erlaubte Schlüssel/Werte; schreibt einen Audit-Eintrag. „Auf Default zurücksetzen“ = `DELETE /system/settings/{key}` (löscht den DB-Eintrag, dann gilt ENV/Default).
- **MVP-Umfang:** nur Logging-Einstellungen (`log_persist_level`, `log_persist_categories`, `log_retention_days`, `log_rate_limit`); das Schema ist generisch erweiterbar.

## Datums- & Zeitwerte in der API

Alle Zeitwerte in Requests und Responses folgen der Datumswerte-Konvention (Abschnitt 4 / [datenmodell.md](datenmodell.md)):

- Zeitwerte sind **immer ISO-8601-Strings** mit Zeitzonenangabe: `2026-08-14T10:00:00Z` (UTC) oder `2026-08-14T12:00:00+02:00` (Offset).
- Strings **ohne** Zeitzonenangabe werden mit **HTTP 400** abgelehnt.
- Der Kern **normalisiert** eingehende Werte auf UTC; ausgehende Werte werden als UTC (`…Z`) geliefert.
- Zeitwerte gelten für Systemfelder (`created_at`, `updated_at`, `started_at`, `finished_at`, `timestamp`) sowie für Attribute vom Typ `date`/`datetime` (Attributdefinitionen, Abschnitt 4).

## Werte mit Einheiten in der API

Quantifizierte Werte folgen der Einheiten-Konvention (Abschnitt 4 / [einheiten.md](einheiten.md)):

- **Implementiert:** `GET /units` (Katalog, Filter `symbol`/`dimension`) und `GET /units/convert?value=&from=&to=` (linear-affine Umrechnung `value * factor + offset`, `to` optional = kanonische SI-Einheit der Dimension, 400 bei Dimensions-Mismatch, 404 bei unbekannter Einheit). `POST/PATCH/DELETE /units` für benutzerdefinierte Einheiten sind eine spätere Ausbaustufe.

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
- **Lieferung (implementiert):** `GET /objects?locale=<code>` liefert pro Objekt ein Feld `completeness: { "<code>": <0–100> }` (für die angeforderte Arbeitssprache; nur gesetzt, wenn `?locale=` angegeben ist). `GET /objects/{id}?locale=<code>` liefert zusätzlich die **Liste fehlender Pflichtattribute** (`missing_required`, für das Formular-Widget). Berechnung in `crates/pim-core/src/completeness.rs`.
- **Filterung (offen):** Query-Parameter `?completeness_lt=100` bzw. `?missing_required=true` („nur unvollständige Produkte“) sind noch **nicht** umgesetzt; sie folgen mit der Grid-Filterleiste.
- **Berechnungsbasis:** Aufgelöste Sicht (`resolve_inheritance`, entlang `depends_on`); **leere Werte (Vererbungsunterbrechung) zählen als „gesetzt“** (eigener Schlüssel vorhanden). Ein vererbter Wert gilt als gesetzt, wenn er nicht `null` ist.
- Details zur UI-Darstellung: [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#4-vollständigkeit-completeness).

## Massenoperationen (Bulk) & Batch-Save

- **`POST /objects/bulk`** führt eine Massenoperation auf einer **Auswahl** aus („alle Treffer des Filters“ / „alle auf der Seite“ / explizite ID-Liste). Operationsarten: `set_attribute` (ersetzen), `clear_attribute` (auf „nicht gepflegt“ zurücksetzen), `change_status`, `add_category`/`remove_category`, `change_parent` (Re-Parenting, Zyklenschutz), `delete` (Soft-Delete-Kaskade).
- Die Ausführung erfolgt **als Hintergrundjob** (siehe [jobs-scheduler.md](jobs-scheduler.md#job-typen), Typ `bulk_operation`): Der Endpunkt erzeugt ein `job_run` und liefert dessen ID; der Report (Erfolg/Fehler pro Objekt, Validierungsfehler) steht nach Abschluss am `job_run.result`.
- **`POST /objects/batch-update`** wendet die **gesammelten Inline-Edits** aus dem Grid (Batch-Save) an – eine Liste von Teiländerungen (`{id, attributes, status, …}`) in einem Aufruf; Validierung erfolgt pro Objekt (Eindeutigkeit, Typrestriktionen, Workflow). **Gedeckelt (verbindlich):** max. **500 Zeilen pro Aufruf** (HTTP 413 bei Überschreitung); größere Auswahlen laufen über `POST /objects/bulk` (async). Das Frontend paginiert große Batch-Saves entsprechend ([frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md)).
- **Auth/Fehler-Semantik (implementiert):** `POST /objects/batch-update` verlangt `editor`+ (JWT via `api_key_or_role`). Ein ungültiger/abgelaufener Token wird **nicht** als 401 beantwortet, sondern pro Zeile als HTTP 200 mit `{ "success": false, "error": "invalid or expired token" }`. Das Frontend erkennt das über `is_session_expired` und leitet zur Login-Seite um. Hinweis: `GET /objects` erzwingt aktuell **keine** Auth (bewusste MVP-Vereinfachung, siehe Volltextsuche) – deshalb lädt das Grid auch mit abgelaufenem Token; nur Schreibpfade schlagen fehl.

## Inbound-Import & Request-Limits

- **`POST /imports`:** Nimmt ein MEF-Dokument entgegen (JSON-Body oder Datei-Upload → Objektstorage), validiert nur oberflächlich, legt sofort ein `job_run` an und liefert `202 Accepted` mit `{ "job_run_id": … }` (Details [integration-eventsystem.md](integration-eventsystem.md)). Der Aufrufer wartet nie auf die Verarbeitung; Status/Fortschritt/Report über `GET /jobs/runs?ids=…`.
- **Implementiert (Schritt 2, 9):** `POST /imports` akzeptiert den MEF-JSON-Body **oder** einen `multipart/form-data`-Datei-Upload (Feld `file`) plus optionale Query-Parameter `match_by_key` (Business-Schlüssel für Upsert), `source` und `emit_events`; das Dokument wird in `import_document` gespeichert und ein `inbound_import`-Job gequeue't. `GET /imports/{document_id}` liefert das gespeicherte Dokument an den Worker (`X-Api-Key` oder Admin). Datei-Upload-Limit: 100 MB (`DefaultBodyLimit`).
- **Request-Limits (verbindlich):** Für alle Endpunkte gelten **Body-/Upload-Limits** (z. B. JSON-Body max. 10 MB, Datei-Upload max. 100 MB, konfigurierbar) sowie ein **Rate-Limit** (z. B. pro Nutzer/JWT, konfigurierbar), damit Eingabe-Fluten über API/Inbound das System nicht stilllegen.

## Referenz-Picker-Suche

Der **suchbare Referenz-Picker** der Oberfläche nutzt `GET /objects/picker` (Label-Volltextsuche, optional eingeschränkt auf erlaubte Zieltypen aus `relation_definition.target_types` bzw. der Attributdefinition). Die Antwort enthält kompakte Treffer (ID, Typ, aufgelöstes Label, Status). Das Auswahl-Dropdown lädt die Relationstypen über **`GET /relation-definitions`** und filtert auf `kind == 'aggregation'`; `POST /references` erwartet als `definition_id` den **kurzen Record-Key** (z. B. `rel_category_member`, abgeleitet aus `relation_definition:rel_category_member`) — nicht die volle Record-ID und nicht den Anzeigenamen (`name`, z. B. `category_member`). Details zur UI: [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#24-wertedarstellung).

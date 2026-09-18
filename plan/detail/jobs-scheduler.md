# Hintergrundprozesse & Scheduler

- **Hauptdokument:** Abschnitt 6 ([Hintergrundprozesse & Scheduler](../meadow-pim-architektur.md#6-hintergrundprozesse--scheduler))
- **Relevante Phasen:** Phase 5, 7, 8
- **Stand:** Implementiert bis Phase-5-Schritt 7: Job-Schema + Definition-CRUD + Run-Status + Manual-Run + NATS-JetStream-Publish + Python-Worker-Jobkonsument + `business_rule`-Handler (Recompute) + Cron-Scheduler + parallele Verarbeitung (konfigurierbare Konkurrenz) + DLQ-Überwachung. Log-Retention folgt. **Phase 7:** Admin-Seite „Jobs & Scheduler“ (`meadow/frontend/jobs.py`, `/jobs`) mit Definitions-CRUD, manuellem Start und Run-Historie (Status/Dead-Letter).

## Architektur

Der Scheduler und die Job-Verarbeitung sind für **geplante oder manuell gestartete Aufgaben** zuständig. CRUD-Events werden separat über das Eventsystem (Abschnitt 7 des Hauptdokuments) verarbeitet und sind nicht Teil dieses Abschnitts.

```text
┌──────────────────────────────────────────────────────────────┐
│                     Rust-Kern (Scheduler)                    │
│                                                              │
│  – Lädt aktive job_definition aus SurrealDB                  │
│  – Prüft Cron-Ausdrücke (tokio-cron-scheduler)               │
│  – Erzeugt job_run und publiziert Message an NATS            │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
               │ Publish Job-Messages          │ Read/Write Job-Runs
               ▼                               ▼
       ┌────────────────┐               ┌───────────────┐
       │  NATS JetStream│               │   SurrealDB   │
       │  (WorkQueue)   │               │ (Job Tabellen)│
       └───────┬────────┘               └───────────────┘
               │ Subscribe
               ▼
┌──────────────────────────────────────────────────────────────┐
│                     Python Worker Service                    │
│                                                              │
│  – NATS-Client (nats-py, asyncio)                            │
│  – Führt Jobs aus (Business Rules, Import/Export)            │
│  – Sendet Status-Updates an Rust-Kern (REST)                 │
└──────────────────────────────┬───────────────────────────────┘
                               │ REST (Status-Updates, Datenabruf)
                               ▼
                     ┌─────────────────────┐
                     │    PIM Core (REST)  │
                     └─────────────────────┘
```

## Worker-Konkurrenz & Parallelität

- Der Python-Worker verarbeitet Nachrichten **parallel** (asyncio): getrennte Konsumenten für die **Job-Streams** (`jobs.*`) und den **Event-Stream** (`events.crud`), sodass ein langer Import die Webhook-Auslieferung nicht blockiert.
- Die **Parallelität ist konfigurierbar** (max. gleichzeitig verarbeitete Nachrichten je Konsument, `JOBS_CONCURRENCY`, Standard 4). **Import-Jobs sind bewusst begrenzt** (z. B. max. 1–2 gleichzeitig), damit sie weder die Event-Auslieferung noch andere Jobs aushungern (per-Typ-Limits folgen mit den Import-Konnektoren in Phase 6).
- **Implementiert (Schritt 6):** `serve_jobs` fetcht bis zu `JOBS_CONCURRENCY` Messages und verarbeitet sie mit einem `asyncio.Semaphore` parallel. Sandbox-Auswertungen bleiben **serialisiert** (module-globaler Lock: `sys.settrace`/`tracemalloc` sind nicht nebenläufig sicher), während Objekt-I/O parallel läuft.
- Skalierung über mehrere Worker-Container in einer NATS-Queue-Group bleibt möglich ([betrieb-deployment.md](betrieb-deployment.md)).

## Ablauf eines Jobs

> **Cron-Format:** `tokio-cron-scheduler` nutzt ein **6-Felder-Format mit Sekunden an erster Stelle** (`sec min hour day month dow`). „Täglich um 03:00 Uhr“ lautet also `0 0 3 * * *` (nicht das übliche 5-Felder-Format `0 3 * * *`).

1. Der Scheduler (oder ein manueller API-Aufruf) erzeugt ein `job_run` mit Status `pending`.
2. Der Rust-Kern publiziert eine Message in den entsprechenden NATS-JetStream-Stream (Subject: `jobs.<type>`).
3. Ein Python-Worker empfängt die Message, bestätigt sie (Ack) und setzt den Status des `job_run` **per REST-Aufruf** auf `running`.
4. Der Worker führt die eigentliche Aufgabe aus.
5. Nach Abschluss aktualisiert der Worker den Status **per REST-Aufruf** (`PATCH /jobs/runs/{run_id}`) auf `success` oder `failed`.
6. Bei Fehlern wird die Message nicht geackt; JetStream liefert sie nach einem Timeout erneut zu (Retry). Nach maximal 5 Versuchen wird die Message in den Dead-Letter-Stream verschoben. Der Rust-Kern überwacht diesen Stream und markiert den zugehörigen `job_run` als endgültig `failed`.
7. **Idempotenz (verbindlich):** Da NATS at-least-once zustellt und eine Nachricht nach Teilausfall erneut geliefert wird, müssen Job-Typen, die Daten **schreiben** (Import, Business Rules mit Seiteneffekten), idempotent sein: Wiederholte Ausführung darf keine Duplikate erzeugen (Abgleich per Business-Schlüssel bzw. Quell-Referenz, [integration-eventsystem.md](integration-eventsystem.md)).
8. **Trigger:** Im MVP sind `schedule` und `manual` aktiv; der Triggerwert `event` (Event-getriggerte Jobs) ist **reserviert** für Phase 5+ (gefilterte CRUD-Events erzeugen `job_run`-Einträge, Wiederverwendung des Eventsystems).

## Manueller Start (implementiert, Schritt 2)

- `POST /jobs/{id}/run` (Rolle `editor`+, sonst 403) erzeugt ein `job_run` (`trigger: manual`, `status: pending`, `attempt: 1`) und publiziert eine JetStream-Message auf `jobs.<type>` mit Payload `{ run_id, job_definition_id, type, parameters }` (die `parameters` werden zum Triggerzeitpunkt aus der `job_definition` übernommen).
- Deaktivierte Definitionen werden mit 400 abgelehnt, unbekannte IDs mit 404.
- Schlägt der Publish fehl (NATS nicht erreichbar), setzt der Kern den `job_run` auf `failed` (mit Fehlermeldung und `finished_at`) und antwortet mit 503. Es bleibt kein verwaister `pending`-Run zurück.
- Antwort: `202 Accepted` mit dem erzeugten `job_run`.

## Definition-CRUD & Löschen (implementiert)

- `GET/POST /jobs` und `GET/PATCH /jobs/{id}`: Admin-CRUD für `job_definition` (Name, Typ, Cron-Zeitplan, `parameters`, `enabled`).
- **`DELETE /jobs/{id}`** (admin, neu): löscht die Definition **und** entfernt alle zugehörigen `job_run`-Einträge (Kaskade), damit keine verwaisten Läufe zurückbleiben. Antwort `204`. Wird auditiert (`job_definition_deleted`).
- **`DELETE /jobs/runs/{run_id}`** (admin, neu): löscht einen einzelnen `job_run` (z. B. Altlasten/DLQ), Antwort `204`, auditiert (`job_run_deleted`).

## Laufergebnis: `result` und seine Kurzfassung `result_summary` (umgesetzt 2026-09-18)

- **`result` ist der vollständige Report** eines Laufs, geschrieben per `PATCH /jobs/runs/{run_id}` (Worker über `x-api-key`). Er gehört in die **Detailansicht**: `GET /jobs/runs?ids=<id>&limit=1` holt genau einen Lauf samt Report.
- **`result_summary` schreibt der Kern mit:** die Top-Level-Felder des `result`, die einzeln **≤ 2 KB** sind (`format`, `exported`, `columns`, ein kleiner `report`). Sie steckt in **jeder** Lauf-Antwort — auch bei `include_result=false` — und ist das, was eine **Liste** zeigt: „was hat der Lauf getan?“, ohne die Nutzlast. Die Größenregel ist bewusst generisch (Größe, nicht Feldname und nicht Job-Typ), damit kein Job-Typ einen eigenen Leser braucht.
- **Warum:** `result` ist zugleich Ablage. Ein Export legt sein ganzes Dokument hinein, ein Import seinen Report je Objekt. `GET /jobs/runs?limit=100` **mit** Results antwortete mit **20 MB**, und die nächste Anfrage riss die SurrealDB-Verbindung ab (`500 Connection reset`; gemessen 2026-09-18: `limit=20` = 20 MB, `limit=50` scheiterte) — danach antworteten auch andere Endpunkte (`/languages`, `/system/logs`) mit 500, bis die Verbindung sich erholte. Daraus die Regel: **Listen und Polling fragen `include_result=false`**, Report und Download holen einen einzelnen Lauf.
- **Einmalige Lücke:** Läufe von *vor* dieser Änderung haben keine Kurzfassung — sie wurde nicht rückwirkend berechnet, weil das genau die Nutzlasten laden müsste, die der Anlass der Änderung sind. Die Liste füllt sich mit den nächsten Läufen.
- **Offener Rest (nicht geändert):** Ein Export legt Report und Dokument in **demselben** `result` ab; wer den Report sehen will (Export-/Importseite nach dem Lauf), holt damit auch das Dokument. Sauber wäre eine Trennung von Report und Artefakt (eigenes Feld/Objekt-Store, das nur der Download liest) — eigener Schritt, siehe [datenexport-import-ui.md](datenexport-import-ui.md).

## Python-Worker-Jobkonsument (implementiert, Schritt 3)

- `python -m meadow.worker` startet neben dem Rule-Service einen **JetStream-Pull-Konsumenten** (`jobs.*`, Stream `JOBS`, Durable `job-worker`; `meadow/worker/jobs.py`).
- Ablauf je Message: Payload parsen (`JobMessage`) → Status `running` per `PATCH /jobs/runs/{run_id}` → Dispatch an den für `job_type` registrierten Handler → `success` (mit `result`) + Ack oder `failed` + Negative-Ack (Retry).
- **WorkQueue-Hinweis:** Ein WorkQueue-Stream erlaubt nur **einen** gefilterten Konsumenten; mehrere Worker-Instanzen binden sich an denselben Durable (`job-worker`) und teilen sich die Verarbeitung (Queue-Group). Temporäre Verifikations-Konsumenten müssen gelöscht werden.
- Unbekannte `job_type` und nicht parsebare Messages werden terminiert (`term`); der `job_run` wird auf `failed` gesetzt („no handler registered for job type …“).
- Status-Updates sind best-effort (Core nicht erreichbar → Log-Warnung, kein Crash); der Ack/Nak basiert auf dem Handler-Ergebnis.

## `business_rule`-Handler (implementiert, Schritt 4)

- `POST /rules/{id}/recompute` (Kern, `admin`/`superadmin`) queue't einen `business_rule`-Job (ohne `job_definition`); nur `active`-Regeln vom Typ `computed` oder `bulk` sind zulässig. Die Message-Parameter enthalten `{rule_id, rule:{name,type,script}, applies_to, object_ids}`.
- Der Worker (`meadow/worker/business_rules.py`) lädt die Objekte (`object_ids` oder alle Objekte der `applies_to`-Typen via `GET /objects`), führt das Skript je Objekt in der Sandbox aus (Bulk-Modus, ~30 s Budget) und schreibt deklariert-berechnete Werte über `PATCH /objects/{id}/computed` zurück (kein erneutes Regel-Triggering).
- Report je Objekt in `job_run.result` (`{processed, succeeded, failed, results:[{object_id, status, computed, errors, warnings, error}]}`); Objekt-Fehler sind isoliert (ein fehlgeschlagenes Objekt bricht den Lauf nicht ab).
- **Kaskade (`computed_reads_inherited`) umgesetzt:** Der Handler wertet (bei `object_ids`) gegen die **aufgelöste** Sicht aus (`GET /objects/{id}?resolve_inheritance=true[&locale=…]`); ein Vorfahren-Save löst über `rules::queue_inherited_cascade` **pro Regel** einen `business_rule`-Job (`trigger: cascade`) über die betroffenen Nachfahren aus (best-effort, nie inline). Die vererbt-abhängige Recompute-Kaskade ist damit abgeschlossen.

## Cron-Scheduler (implementiert, Schritt 5)

- Der Kern startet beim Hochfahren einen **Hintergrund-Scheduler** (`src/scheduler.rs`, `tokio_cron_scheduler`), der alle `SCHEDULER_INTERVAL_S` Sekunden (Standard 60) mit den **aktiven geplanten** `job_definition`-Einträgen abgeglichen wird.
- Bei jedem Feuern erzeugt der Scheduler ein `job_run` (`trigger: schedule`, `status: pending`) und publiziert es in den `JOBS`-Stream (gleicher Pfad wie `queue_run`).
- Cron-Format: **6 Felder mit Sekunden zuerst** (`sec min hour day month dow`); ungültige Ausdrücke werden beim Registrieren abgelehnt und als Log-Warnung sichtbar.
- Änderungen an Definitionen werden beim nächsten Sync wirksam: neuer/geänderter Zeitplan → Job neu registrieren; deaktiviert/entfernt → Job abmelden. Beim Start wird sofort synchronisiert.

## Job-Typen

- **outbound_export**: Exportiert Daten über einen Konnektor (CSV, JSON, API, SFTP). **Implementiert (Phase 6, Schritt 4):** der `json_export`-Konnektor (`meadow/worker/exports.py`) erzeugt aus den gefilterten Objekten ein MEF-Dokument und legt es im `job_run.result` ab. **Strukturierte Exporte (Phase 9):** mit `parameters.source` (`collection|type|category|saved_search|selection`) erzeugt derselbe Handler CSV/XLSX/JSON (`job_run.result` mit `{format, exported, columns, csv|document|xlsx_base64}`), herunterladbar über `GET /exports/{run_id}/file`. **Geplante Exporte** nutzen genau diese Parameter: eine `job_definition` mit `type = outbound_export` und einem Cron `schedule` wird vom Scheduler periodisch ausgeführt (im `/export`-Wizard per „Schedule…" anlegbar). Optional versendet der Handler das Ergebnis per E-Mail (`options.email_to`, SMTP per Env; siehe [datenexport-import-ui.md](datenexport-import-ui.md)).
- **inbound_import**: Importiert Daten aus externen Quellen (MEF, [integration-eventsystem.md](integration-eventsystem.md)); **idempotent** (Upsert per Business-Schlüssel bzw. Quell-Referenz). Konfigurierbar `emit_events`: Standard erzeugt pro Objekt CRUD-Events; bei großen Beständen kann auf „ein Summary-Event `import.completed`“ umgestellt werden, um Event-/Webhook-Flut zu vermeiden. **Implementiert (Phase 6, Schritt 8):** `emit_events: false` unterdrückt die Einzelevents (`?emit_events=false` an den Schreib-Endpunkten) und legt ein Summary-Event über `POST /imports/{document_id}/complete` in die Outbox.
- **business_rule**: Führt eine Business Rule auf gefilterten Objekten aus (z. B. Berechnung von Attributen, Anreicherung). Der Mechanismus deckt auch die **Recompute-Jobs** der Business Rules ab (Abschnitt 8 / [business-rules.md](business-rules.md)): (a) **Kaskade** – vererbt-abhängige berechnete Attribute von Nachfahren werden nach einem Vorfahren-Save über einen Recompute-Job aktualisiert (eventual consistency); (b) **Bulk-Recompute** – nach Änderung einer Regel oder `dependencies`/`computed`-Definition über die betroffenen Objekttypen (bestätigbar, mit Report).
- **bulk_operation** (Phase 7): Führt eine **Massenoperation** aus dem Bulk-Edit-Wizard der Oberfläche auf einer Auswahl von Objekten aus (Attributwert setzen/leeren, Status, Kategorie, Re-Parenting, Massen-Löschen). Ergebnis als **Report** im `job_run.result` (Erfolg/Fehler pro Objekt, Validierungsfehler). Siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#3-massendatenpflege) und [api-design.md](api-design.md) (`POST /objects/bulk`).
- **collection_refresh** (Phase 9): Aktualisiert eine **materialisierte Collection** – der Worker ruft `POST /collections/{id}/refresh` per `X-Api-Key` auf (`meadow/worker/collections.py`), damit der atomare Member-Tausch im Kern bleibt. Eine Collection mit nicht-leerem `refresh_interval` synchronisiert automatisch eine `job_definition` (`type: collection_refresh`, `parameters={collection_id}`); das Leeren entfernt sie. Siehe [gespeicherte-suchen-collections.md](gespeicherte-suchen-collections.md).
- **config_import** (Phase 9): Führt einen **MCF-Konfigurations-Import** asynchron aus – der Worker holt das gespeicherte MCF-Dokument (`GET /imports/{document_id}`, `import_document.kind = mcf`) und wendet es über `POST /config/import?mode=…` erneut an (`meadow/worker/config_import.py`), Report im `job_run.result`. Wird von `POST /config/import?async=true` erzeugt; siehe [konfiguration-export-import.md](konfiguration-export-import.md).
- **connector_test** (Phase 9, Stage 2c): **Verbindungstest** eines Konnektors – `POST /connectors/{id}/test` (admin) legt den Lauf mit `parameters={connector_id, name, connector_type, parameters}` an; der Worker (`meadow/worker/connector_test.py`) sendet für `webhook` ein **synthetisches Event** an die gespeicherte URL (Antwort wird nicht gespeichert, es entsteht **kein** Outbox-Event) und löst für `json_import`/`json_export` die Profilwerte **trocken** auf. Ergebnis: `{ok, status_code?, latency_ms?, excerpt?, resolved?, error?}`. Siehe [konnektor-konfiguration.md](konnektor-konfiguration.md), Abschnitt 8.

The **Admin-Seite `/jobs`** (Frontend) führt dieselbe Typenliste (`JOB_TYPES` ↔ `VALID_JOB_TYPES`, 7 Typen) und filtert die Definitionsliste nach Typ.

## Fehlerbehandlung & Wiederholung

- **Retries**: NATS JetStream wiederholt die Zustellung bei nicht erfolgtem Acknowledgment. Der Worker kann zusätzlich bei bestimmten Fehlern selbst entscheiden, die Nachricht abzulehnen, um eine sofortige Wiederholung zu vermeiden.
- **Dead-Letter-Queue (DLQ) (implementiert, Schritt 7):** Beim letzten Zustellversuch (`num_delivered >= max_deliver`) publiziert der Worker die Message in den `JOBS_DLQ`-Stream (`jobs.dlq.<type>`, `Limits`-Retention) und terminiert sie; der Rust-Kern abonniert `jobs.dlq.>` (Hintergrund-Monitor, `src/jobqueue.rs::monitor_dlq`) und markiert den `job_run` endgültig als `failed` („dead lettered after N attempts“).
- **Timeout**: Wird ein Worker nicht innerhalb der konfigurierten `AckWait`-Zeit aktiv, stellt JetStream die Nachricht erneut zu.
- **Monitoring**: Alle Job-Runs und Dead-Letter-Ereignisse werden in SurrealDB gespeichert und können über die API ausgelesen werden.
- **Stream-Retention:** Der `JOBS`-Stream (Subjects `jobs.*`) wird vom Kern bei der ersten Veröffentlichung angelegt: **WorkQueue-Retention** (Message wird nach dem ersten Worker-Ack entfernt) und **Max-Age** über `JOBS_STREAM_MAX_AGE_MINUTES` (Standard 1440 min). Der dauerhafte Job-Status liegt in `job_run` (SurrealDB); der Stream dient nur dem Transport. Der DLQ-Stream wird **manuell** überwacht und bereinigt (Operationen, [betrieb-deployment.md](betrieb-deployment.md)).

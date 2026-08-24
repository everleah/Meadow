# Hintergrundprozesse & Scheduler

- **Hauptdokument:** Abschnitt 6 ([Hintergrundprozesse & Scheduler](../meadow-pim-architektur.md#6-hintergrundprozesse--scheduler))
- **Relevante Phasen:** Phase 5, 7, 8
- **Stand:** Implementiert bis Phase-5-Schritt 2: Job-Schema + Definition-CRUD + Run-Status + Manual-Run (`POST /jobs/{id}/run`) mit NATS-JetStream-Publish (`JOBS`-Stream). Python-Worker-Jobkonsument, Cron-Scheduler, DLQ-Überwachung und Log-Retention folgen.

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
- Die **Parallelität ist konfigurierbar** (max. gleichzeitig verarbeitete Nachrichten je Konsument). **Import-Jobs sind bewusst begrenzt** (z. B. max. 1–2 gleichzeitig), damit sie weder die Event-Auslieferung noch andere Jobs aushungern.
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

## Job-Typen

- **outbound_export**: Exportiert Daten über einen Konnektor (CSV, JSON, API, SFTP).
- **inbound_import**: Importiert Daten aus externen Quellen (MEF, [integration-eventsystem.md](integration-eventsystem.md)); **idempotent** (Upsert per Business-Schlüssel bzw. Quell-Referenz). Konfigurierbar `emit_events`: Standard erzeugt pro Objekt CRUD-Events; bei großen Beständen kann auf „ein Summary-Event `import.completed`“ umgestellt werden, um Event-/Webhook-Flut zu vermeiden.
- **business_rule**: Führt eine Business Rule auf gefilterten Objekten aus (z. B. Berechnung von Attributen, Anreicherung). Der Mechanismus deckt auch die **Recompute-Jobs** der Business Rules ab (Abschnitt 8 / [business-rules.md](business-rules.md)): (a) **Kaskade** – vererbt-abhängige berechnete Attribute von Nachfahren werden nach einem Vorfahren-Save über einen Recompute-Job aktualisiert (eventual consistency); (b) **Bulk-Recompute** – nach Änderung einer Regel oder `dependencies`/`computed`-Definition über die betroffenen Objekttypen (bestätigbar, mit Report).
- **bulk_operation** (Phase 7): Führt eine **Massenoperation** aus dem Bulk-Edit-Wizard der Oberfläche auf einer Auswahl von Objekten aus (Attributwert setzen/leeren, Status, Kategorie, Re-Parenting, Massen-Löschen). Ergebnis als **Report** im `job_run.result` (Erfolg/Fehler pro Objekt, Validierungsfehler). Siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md#3-massendatenpflege) und [api-design.md](api-design.md) (`POST /objects/bulk`).

Für spätere Ausbaustufen sind weitere Job-Typen vorgesehen (z. B. `collection_refresh` für materialisierte Collections). Der Mechanismus bleibt identisch.

## Fehlerbehandlung & Wiederholung

- **Retries**: NATS JetStream wiederholt die Zustellung bei nicht erfolgtem Acknowledgment. Der Worker kann zusätzlich bei bestimmten Fehlern selbst entscheiden, die Nachricht abzulehnen, um eine sofortige Wiederholung zu vermeiden.
- **Dead-Letter-Queue (DLQ)**: Nach Erreichen der maximalen Zustellversuche landet die Nachricht automatisch im DLQ-Stream. Der Rust-Kern abonniert diesen Stream und markiert die betroffenen `job_run`-Einträge als `failed`.
- **Timeout**: Wird ein Worker nicht innerhalb der konfigurierten `AckWait`-Zeit aktiv, stellt JetStream die Nachricht erneut zu.
- **Monitoring**: Alle Job-Runs und Dead-Letter-Ereignisse werden in SurrealDB gespeichert und können über die API ausgelesen werden.
- **Stream-Retention:** Der `JOBS`-Stream (Subjects `jobs.*`) wird vom Kern bei der ersten Veröffentlichung angelegt: **WorkQueue-Retention** (Message wird nach dem ersten Worker-Ack entfernt) und **Max-Age** über `JOBS_STREAM_MAX_AGE_MINUTES` (Standard 1440 min). Der dauerhafte Job-Status liegt in `job_run` (SurrealDB); der Stream dient nur dem Transport. Der DLQ-Stream wird **manuell** überwacht und bereinigt (Operationen, [betrieb-deployment.md](betrieb-deployment.md)).

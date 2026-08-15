# Hintergrundprozesse & Scheduler

- **Hauptdokument:** Abschnitt 6 ([Hintergrundprozesse & Scheduler](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 5, 8
- **Stand:** Entwurf

## Architektur

Der Scheduler und die Job-Verarbeitung sind für **geplante oder manuell gestartete Aufgaben** zuständig. CRUD-Events werden separat über das Eventsystem (Abschnitt 7 des Hauptdokuments) verarbeitet und sind nicht Teil dieses Abschnitts.

```
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

## Ablauf eines Jobs

> **Cron-Format:** `tokio-cron-scheduler` nutzt ein **6-Felder-Format mit Sekunden an erster Stelle** (`sec min hour day month dow`). „Täglich um 03:00 Uhr“ lautet also `0 0 3 * * *` (nicht das übliche 5-Felder-Format `0 3 * * *`).

1. Der Scheduler (oder ein manueller API-Aufruf) erzeugt ein `job_run` mit Status `pending`.
2. Der Rust-Kern publiziert eine Message in den entsprechenden NATS-JetStream-Stream (Subject: `jobs.<type>`).
3. Ein Python-Worker empfängt die Message, bestätigt sie (Ack) und setzt den Status des `job_run` **per REST-Aufruf** auf `running`.
4. Der Worker führt die eigentliche Aufgabe aus.
5. Nach Abschluss aktualisiert der Worker den Status **per REST-Aufruf** (`PATCH /jobs/runs/{run_id}`) auf `success` oder `failed`.
6. Bei Fehlern wird die Message nicht geackt; JetStream liefert sie nach einem Timeout erneut zu (Retry). Nach maximal 5 Versuchen wird die Message in den Dead-Letter-Stream verschoben. Der Rust-Kern überwacht diesen Stream und markiert den zugehörigen `job_run` als endgültig `failed`.

## Job-Typen

- **outbound_export**: Exportiert Daten über einen Konnektor (CSV, JSON, API, SFTP).
- **inbound_import**: Importiert Daten aus externen Quellen.
- **business_rule**: Führt eine Business Rule auf gefilterten Objekten aus (z. B. Berechnung von Attributen).

Für spätere Ausbaustufen sind weitere Job-Typen vorgesehen (z. B. `collection_refresh` für materialisierte Collections). Der Mechanismus bleibt identisch.

## Fehlerbehandlung & Wiederholung

- **Retries**: NATS JetStream wiederholt die Zustellung bei nicht erfolgtem Acknowledgment. Der Worker kann zusätzlich bei bestimmten Fehlern selbst entscheiden, die Nachricht abzulehnen, um eine sofortige Wiederholung zu vermeiden.
- **Dead-Letter-Queue (DLQ)**: Nach Erreichen der maximalen Zustellversuche landet die Nachricht automatisch im DLQ-Stream. Der Rust-Kern abonniert diesen Stream und markiert die betroffenen `job_run`-Einträge als `failed`.
- **Timeout**: Wird ein Worker nicht innerhalb der konfigurierten `AckWait`-Zeit aktiv, stellt JetStream die Nachricht erneut zu.
- **Monitoring**: Alle Job-Runs und Dead-Letter-Ereignisse werden in SurrealDB gespeichert und können über die API ausgelesen werden.

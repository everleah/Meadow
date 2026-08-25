# Integration & Eventsystem

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem))
- **Relevante Phasen:** Phase 6, 8
- **Stand:** Implementiert bis Phase-6-Schritt 9: Transactional Outbox + Event-Relay, MEF-Import, quantifizierte Werte (SI-Normalisierung), `normalization`-Regeln, `json_export`, Webhook-Outbound, Webhook-Coalescing, Quell-Referenz-Dedup, `emit_events` (Summary-Event `import.completed` für Bulk-Importe) **und** Datei-Upload (Multipart auf `POST /imports`). Persistierte Konnektor-Konfiguration (statt Env-Variablen) folgt.

Meadow PIM kommuniziert mit externen Systemen über **Inbound- und Outbound-Prozesse**. In der ersten Version genügt die Anbindung an eine bestehende Middleware, die komplexe Transformations- und Verteilungsaufgaben übernimmt. Meadow PIM liefert dafür ein einheitliches JSON-Austauschformat und ein Eventsystem, das Änderungen zuverlässig an die Middleware weiterleitet.

## Standard-Import/Export-Format (Meadow Exchange Format, MEF)

Für den Import und Export wird ein generisches JSON-Format verwendet, das alle Kernentitäten (Objekte, Referenzen, ggf. Attributdefinitionen) abbildet. Es dient als **einziges Austauschformat** für die Inbound-/Outbound-Konnektoren der ersten Version.

**Struktur eines MEF-Dokuments:**

```json
{
  "format_version": "1.0",
  "source": "middleware",
  "created_at": "2026-08-14T10:00:00Z",
  "objects": [
    {
      "id": "object_123",
      "type": "product",
      "attributes": {
        "name": {
          "en": "Example product",
          "de": "Beispielprodukt"
        },
        "price": 19.99,
        "width": { "value": 12.5, "unit": "mm" }
      },
      "status": "draft",
      "references": [
        {
          "out": "category_5",
          "type": "category_member",
          "attributes": { "position": 3 }
        }
      ]
    }
  ],
  "references": [
    {
      "in": "object_123",
      "out": "object_456",
      "type": "related",
      "attributes": { "relation_strength": 0.8 }
    }
  ]
}
```

**Regeln:**

- `objects` enthält die zu importierenden Objekte mit ihren Attributen und optionalen Referenzen.
- `references` enthält Referenzen, die unabhängig von Objekten verarbeitet werden (z. B. nachträgliche Verknüpfungen).
- Sprachabhängige Werte werden als verschachtelte JSON-Objekte mit Sprachschlüsseln gemäß dem Sprachbaum abgebildet.
- `status` am Objekt kann gesetzt werden, um Workflow-Status zu importieren. Fehlt es, gilt der Standard `draft`.
- Nicht übergebene Attribute bleiben unverändert (bei Update) bzw. leer (bei Neuanlage).
- Die ID ist optional: Fehlt sie, wird eine neue vergeben. Vorhandene IDs ermöglichen Upserts.
- **Business-Schlüssel (z. B. SKU):** Schlüsselattribute werden wie normale Attribute übertragen. Der Import kann per Schlüssel abgleichen (Upsert): Existiert im Ziel bereits ein Objekt mit gleichem Typ und gleichem Schlüsselwert, wird es aktualisiert statt neu angelegt (konfigurierbar pro Konnektor, z. B. `match_by_key: "sku"`). Das ermöglicht die Synchronisation mit Bestandssystemen und Datenübernahmen zwischen QA- und Produktivsystem.
- **Idempotenz (verbindlich):** NATS liefert Nachrichten at-least-once; ein Import-Job kann nach Teilausfall erneut ausgeführt werden. Der Import ist deshalb **idempotent**: Objekte mit Business-Schlüssel werden per `match_by_key` (Upsert) abgeglichen; Objekte ohne Schlüssel deduplizieren über eine **Quell-Referenz** (`source_id`, optional mit `source`-System), gegen die beim erneuten Import derselbe Datensatz aktualisiert statt neu angelegt wird.
- **Implementiert (Schritt 7):** Der Import-Konnektor gleicht jedes Objekt zuerst per Business-Schlüssel, dann per Quell-Referenz ab (`GET /objects/by-source?source=&source_id=`, MEF-`id` + Dokument-`source`); `source_id`/`source` werden beim Anlegen über `POST /objects` am Objekt gespeichert.
- Alle Datums- und Zeitwerte (z. B. `created_at`, Event-`timestamp`) werden als **ISO-8601-String mit Zeitzonenangabe** übertragen und beim Empfang nach UTC normalisiert (Konvention gemäß [datenmodell.md](datenmodell.md)).
- Quantifizierte Werte (Zahl + Einheit) werden als Objekt `{"value": 12.5, "unit": "mm"}` übertragen; beim Import rechnet der Worker in die kanonische SI-Einheit um, beim Export wird in die Standard-Anzeigeeinheit (bzw. die angeforderte Einheit) umgerechnet (Konvention gemäß [einheiten.md](einheiten.md)).

**Verwendung im Inbound:**

- Der Inbound-Konnektor `json_import` akzeptiert MEF-Dokumente (z. B. aus Datei oder HTTP) und läuft als Hintergrundjob (Typ `inbound_import`, [jobs-scheduler.md](jobs-scheduler.md)).
- **Implementiert (Schritt 2–3):** `POST /imports` (JSON-Body) speichert das MEF-Dokument in `import_document` und queue't einen `inbound_import`-Job; der Worker-Konnektor holt das Dokument (`GET /imports/{document_id}`), führt aktive `normalization`-Regeln (R3) aus, normalisiert quantifizierte Werte in die kanonische SI-Einheit, legt Objekte per Business-Schlüssel-Upsert (`match_by_key`, z. B. `sku`) an bzw. aktualisiert sie und legt Referenzen an (MEF-`type`-Name → Definitions-Key `rel_<name>`).
- **Implementiert (Schritt 9):** Datei-Upload über `POST /imports` als `multipart/form-data` (Feld `file`) funktioniert; der Kern parst Multipart via `multer`, speichert das Dokument wie beim JSON-Body in `import_document` (Body-Limit 100 MB). Ablage der Upload-Datei im Objektstorage (minio) folgt später.
- **Einspeisung über `POST /imports` (verbindlich):** Die Middleware (oder die UI) reicht ein MEF-Dokument über den dedizierten Endpunkt `POST /imports` ein – als JSON-Body oder als Datei-Upload (`multipart/form-data`, Feld `file`). Der Kern validiert nur oberflächlich, legt sofort ein `job_run` an und antwortet mit `202 Accepted` inkl. `job_run`-ID (Details [api-design.md](api-design.md)). Der Aufrufer ist nie blockiert; die eigentliche Verarbeitung übernimmt der Worker. (Die Ablage der Rohdatei im Objektstorage ist späterer Ausbau.)
- Der Python-Worker validiert die Struktur, führt Business Rules aus und übergibt die Objekte/Referenzen an den Rust-Kern; Fortschritt und Report stehen am `job_run`.

**Verwendung im Outbound:**

- Der Outbound-Konnektor `json_export` erzeugt MEF-Dokumente aus ausgewählten Objekten (Filter wie Typ, Status, Kategorie).
- **Implementiert (Schritt 4):** Der `outbound_export`-Job-Handler (`meadow/worker/exports.py`) filtert Objekte (`filter.type`/`filter.status`), re-emittiert quantifizierte Werte als `{"value":…,"unit":…}` in der Anzeigeeinheit der Attributdefinition, baut das MEF-Dokument (`format_version`, `source`, `created_at`, `objects`) und legt es im `job_run.result` ab.
- Die Exportdatei kann per Datei, HTTP oder Messaging an die Middleware übergeben werden.

## Eventsystem

Jede CRUD-Operation im Rust-Kern erzeugt ein **Event**, das Änderungen am Datenbestand beschreibt. Events werden intern über NATS JetStream veröffentlicht und können von Outbound-Konnektoren konsumiert werden.

**Event-Struktur:**

```json
{
  "event_id": "evt_001",
  "timestamp": "2026-08-14T10:00:00Z",
  "event_type": "object.updated",
  "object_type": "product",
  "object_id": "object_123",
  "changes": {
    "attributes.price": {
      "old": 19.99,
      "new": 24.99
    }
  },
  "metadata": {
    "user_id": "user_1",
    "request_id": "req_456"
  }
}
```

**Event-Typen:**

- `object.created` – neues Objekt angelegt
- `object.updated` – Attribute oder Status geändert
- `object.deleted` – Objekt (soft) gelöscht
- `reference.created` – neue Referenz erstellt
- `reference.deleted` – Referenz entfernt
- `attribute_definition.created` / `updated` / `deleted` – Änderungen an Attributdefinitionen

**Veröffentlichung (Transactional Outbox, verbindlich):**

- Der Rust-Kern schreibt das Event im **selben SurrealDB-Schreibvorgang** wie die CRUD-Operation in eine **Outbox-Tabelle** (`outbox_event`, [datenmodell.md](datenmodell.md)) – atomar, ohne verteilte Transaktion.
- Ein **Relay-Prozess im Kern** publiziert abgeschlossene Outbox-Einträge asynchron auf dem JetStream-Subject `events.crud` und markiert sie als ausgeliefert (`published_at`).
- Dadurch ist die API **nie von NATS abhängig**: Ist NATS nicht erreichbar, bleiben Requests funktionsfähig; die Events warten in der Outbox und werden nach Wiederherstellung nachgeliefert (Wiederanlauf-sicher).
- Auf dem Stream gelten **Retention-Limits** (z. B. Max-Age 7–30 Tage / Max-Bytes, konfigurierbar, [betrieb-deployment.md](betrieb-deployment.md)); verarbeitete Events werden nach Acknowledgment entfernt (WorkQueue-Prinzip). Es gehen keine Änderungen verloren, auch wenn die Middleware kurzfristig nicht erreichbar ist – die **Outbox ist die Quelle**, der Stream nur der Transport.
- **Implementiert (Schritt 1):** Der Outbox-Write ist in die CRUD-Transaktionen gefaltet (`object.created`/`updated`, `reference.created`/`deleted` bei Re-Parenting, `attribute_definition.created`/`updated`); das Relay (`event.rs::run_relay`) publiziert `pending`-Einträge alle `EVENT_RELAY_INTERVAL_S` (Standard 5) sequenziell auf `events.crud` und markiert `published_at`. `object.deleted` folgt mit dem Soft-Delete (Phase 7).
- **Umsetzungshinweis aus Phase 5:** Ein WorkQueue-Stream erlaubt nur **einen** gefilterten Konsumenten (Queue-Group über einen Durable). Die nats-py-Bibliothek unterstützt **keine** Consumer-seitige DLQ-Policy; die Dead-Letter-Behandlung erfolgt daher **manuell** (der Worker publiziert beim letzten Zustellversuch in einen dedizierten DLQ-Stream, siehe [jobs-scheduler.md](jobs-scheduler.md)) – dieses Muster wird auch für die Event-/Webhook-Zustellung in Phase 6 wiederverwendet.

**Filterung:**

- Für den Webhook-Outbound werden Events anhand konfigurierbarer Filter selektiert:
  - Nach `event_type` (z. B. nur `object.updated`)
  - Nach `object_type` (z. B. nur `product`, nicht `category`)
  - Nach bestimmten Attributen (z. B. nur wenn `price` geändert wurde)
  - Nach Statusübergängen (z. B. nur Freigaben)

**Reihenfolge & Coalescing (verbindlich):**

- Die Event-Reihenfolge ist **pro Objekt garantiert** (je `object_id` wird die Reihenfolge der Änderungen bewahrt); **global** ist sie bewusst ungeordnet (mehrere Worker-Konsumenten, [jobs-scheduler.md](jobs-scheduler.md)).
- Der Webhook-Outbound kann pro Konnektor **Coalescing** aktivieren: Innerhalb eines Zeitfensters wird pro `object_id` + `event_type` nur das **letzte** Event gesendet (mit einem Flag, dass zwischenzeitliche Änderungen gebündelt wurden). Das verhindert Event-Stürme bei Bulk-Änderungen und Importen.
- **Implementiert (Schritt 6):** Bei `coalesce.enabled` (Env `WEBHOOK_COALESCE_ENABLED`, Fenster `WEBHOOK_COALESCE_WINDOW_S`, Standard 30 s) puffert der Konsument Events pro `object_id`+`event_type` und ein Flush-Task liefert pro Fenster nur das letzte Event (Felder `coalesced: true`, `bundled_count: N`); die gepufferten Messages werden erst beim Flush ge-ackt (at-least-once bleibt erhalten).

## Webhook-Outbound zur Middleware

Der Webhook-Outbound-Konnektor ist der primäre Kanal, um Änderungen an die bestehende Middleware zu liefern. Er kombiniert das Eventsystem mit einem zuverlässigen HTTP-Versand.

- **Implementiert (Schritt 5):** Der Worker startet bei gesetztem `WEBHOOK_URL` einen eigenen `events.crud`-Pull-Konsumenten (`meadow/worker/webhooks.py`, Durable `event-webhook`): Filter (`event_types`/`object_types`/`attribute_changes`), HTTP-Versand mit Retry (NATS-Redelivery per Negative-Ack) und manueller DLQ (`EVENTS_DLQ`, Subject `events.dlq.webhook`). Konfiguration über Env-Variablen (`WEBHOOK_URL`, `WEBHOOK_FILTER_*`, `WEBHOOK_COALESCE_*`, …); persistierte Konnektor-Konfiguration folgt.

**Konfiguration (als `job_definition` oder Konnektor-Konfiguration):**

```json
{
  "id": "conn_webhook_middleware",
  "type": "webhook",
  "name": "Middleware Webhook",
  "parameters": {
    "url": "https://middleware.example.com/api/meadow-events",
    "method": "POST",
    "headers": {
      "Authorization": "Bearer <token>",
      "Content-Type": "application/json"
    },
    "filter": {
      "event_types": ["object.created", "object.updated", "object.deleted"],
      "object_types": ["product", "category"],
      "attribute_changes": ["price", "status"]
    },
    "retry_policy": {
      "max_attempts": 5,
      "backoff_seconds": [30, 60, 120, 300]
    },
    "coalesce": {
      "enabled": true,
      "window_seconds": 30
    }
  }
}
```

- **`coalesce`:** Bündelt Events pro `object_id` + `event_type` innerhalb eines Fensters („letztes gewinnt“, inkl. Flag für gebündelte Änderungen); Standard aus.
- **Quellseitige Steuerung (`emit_events`):** Bulk-Import-Jobs können per Konfiguration auf „nur ein Summary-Event `import.completed`“ umstellen, statt pro Objekt Einzelevents zu erzeugen (Details [jobs-scheduler.md](jobs-scheduler.md)); der Webhook-Filter bleibt davon unabhängig konfigurierbar.
- **Implementiert (Schritt 8):** `POST /imports?emit_events=false` lässt den Import-Konnektor Objekte/Referenzen **ohne** Einzelevents schreiben (`?emit_events=false` an `POST /objects`/`PATCH /objects/{id}`/`POST /references`) und am Ende genau ein `import.completed`-Event über `POST /imports/{document_id}/complete` in die Outbox legen (relay't nach `events.crud`).

**Verarbeitungsablauf:**

1. Das Outbox-Relay des Kerns publiziert das Event in NATS JetStream (`events.crud`).
2. Ein Python-Worker abonniert den Event-Stream und prüft die Filterregeln des Webhook-Konnektors (ggf. mit Coalescing, s. o.).
3. Bei Übereinstimmung baut der Worker die HTTP-Anfrage an die Middleware zusammen:
   - Body: das gefilterte Event oder das vollständige Objekt (konfigurierbar)
   - ggf. zusätzliche Informationen wie `resolved_attributes` (nach Vererbung/Sprachbaum)
4. Der Worker sendet die Anfrage mit Wiederholungslogik.
5. Bei dauerhaftem Fehler wird das Event in eine Dead-Letter-Queue verschoben und kann später manuell verarbeitet werden.

**Zustellvertrag (verbindlich):** Die Zustellung an die Middleware ist **at-least-once**. Jedes Event trägt eine eindeutige `event_id`; die Middleware muss Duplikate über `event_id` deduplizieren (Redelivery nach Timeout oder Fehler ist möglich und normal). Der Worker kann zusätzlich einen Zustell-Wasserstand je Konnektor führen, um nach einem Neustart am letzten Stand fortzufahren.

**Vorteile dieser Architektur:**

- **Entkopplung:** Änderungen werden sofort erfasst, ohne die Kern-Performance zu beeinträchtigen.
- **Zuverlässigkeit:** Persistente Events und Retry-Mechanismen verhindern Datenverlust.
- **Flexibilität:** Filter können pro Middleware-Kanal unterschiedlich konfiguriert werden.
- **Wiederverwendbarkeit:** Derselbe Mechanismus kann später für weitere Ziele (z. B. Suchmaschinen, Analytics) genutzt werden.

**Abgrenzung zu Standard-Outbounds:**

- Der `json_export` dient dem **Batch-Export** ganzer Datenbestände (z. B. für nächtliche Abgleiche).
- Der `webhook` dient dem **ereignisgetriebenen Push** einzelner Änderungen in Echtzeit.
- Beide nutzen dieselbe Infrastruktur (NATS, Python-Worker), aber unterschiedliche Trigger und Verarbeitung.

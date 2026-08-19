# Integration & Eventsystem

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem))
- **Relevante Phasen:** Phase 6, 8
- **Stand:** Entwurf

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
- Alle Datums- und Zeitwerte (z. B. `created_at`, Event-`timestamp`) werden als **ISO-8601-String mit Zeitzonenangabe** übertragen und beim Empfang nach UTC normalisiert (Konvention gemäß [datenmodell.md](datenmodell.md)).
- Quantifizierte Werte (Zahl + Einheit) werden als Objekt `{"value": 12.5, "unit": "mm"}` übertragen; beim Import rechnet der Worker in die kanonische SI-Einheit um, beim Export wird in die Standard-Anzeigeeinheit (bzw. die angeforderte Einheit) umgerechnet (Konvention gemäß [einheiten.md](einheiten.md)).

**Verwendung im Inbound:**

- Der Inbound-Konnektor `json_import` akzeptiert MEF-Dokumente (z. B. aus Datei oder HTTP) und läuft als Hintergrundjob (Typ `inbound_import`, [jobs-scheduler.md](jobs-scheduler.md)).
- **Einspeisung über `POST /imports` (verbindlich):** Die Middleware (oder die UI) reicht ein MEF-Dokument über den dedizierten Endpunkt `POST /imports` ein – als JSON-Body oder als Datei-Upload (Dateien werden nach dem Objektstorage abgelegt). Der Kern validiert nur oberflächlich, legt sofort ein `job_run` an und antwortet mit `202 Accepted` inkl. `job_run`-ID (Details [api-design.md](api-design.md)). Der Aufrufer ist nie blockiert; die eigentliche Verarbeitung übernimmt der Worker.
- Der Python-Worker validiert die Struktur, führt Business Rules aus und übergibt die Objekte/Referenzen an den Rust-Kern; Fortschritt und Report stehen am `job_run`.

**Verwendung im Outbound:**

- Der Outbound-Konnektor `json_export` erzeugt MEF-Dokumente aus ausgewählten Objekten (Filter wie Typ, Status, Kategorie).
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

**Filterung:**

- Für den Webhook-Outbound werden Events anhand konfigurierbarer Filter selektiert:
  - Nach `event_type` (z. B. nur `object.updated`)
  - Nach `object_type` (z. B. nur `product`, nicht `category`)
  - Nach bestimmten Attributen (z. B. nur wenn `price` geändert wurde)
  - Nach Statusübergängen (z. B. nur Freigaben)

**Reihenfolge & Coalescing (verbindlich):**

- Die Event-Reihenfolge ist **pro Objekt garantiert** (je `object_id` wird die Reihenfolge der Änderungen bewahrt); **global** ist sie bewusst ungeordnet (mehrere Worker-Konsumenten, [jobs-scheduler.md](jobs-scheduler.md)).
- Der Webhook-Outbound kann pro Konnektor **Coalescing** aktivieren: Innerhalb eines Zeitfensters wird pro `object_id` + `event_type` nur das **letzte** Event gesendet (mit einem Flag, dass zwischenzeitliche Änderungen gebündelt wurden). Das verhindert Event-Stürme bei Bulk-Änderungen und Importen.

## Webhook-Outbound zur Middleware

Der Webhook-Outbound-Konnektor ist der primäre Kanal, um Änderungen an die bestehende Middleware zu liefern. Er kombiniert das Eventsystem mit einem zuverlässigen HTTP-Versand.

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

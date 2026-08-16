# Integration & Eventsystem

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md))
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
          "to": "category_5",
          "type": "category_member",
          "attributes": { "position": 3 }
        }
      ]
    }
  ],
  "references": [
    {
      "from": "object_123",
      "to": "object_456",
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
- Alle Datums- und Zeitwerte (z. B. `created_at`, Event-`timestamp`) werden als **ISO-8601-String mit Zeitzonenangabe** übertragen und beim Empfang nach UTC normalisiert (Konvention gemäß [datenmodell.md](datenmodell.md)).
- Quantifizierte Werte (Zahl + Einheit) werden als Objekt `{"value": 12.5, "unit": "mm"}` übertragen; beim Import rechnet der Worker in die kanonische SI-Einheit um, beim Export wird in die Standard-Anzeigeeinheit (bzw. die angeforderte Einheit) umgerechnet (Konvention gemäß [einheiten.md](einheiten.md)).

**Verwendung im Inbound:**

- Der Inbound-Konnektor `json_import` akzeptiert MEF-Dokumente (z. B. aus Datei oder HTTP).
- Der Python-Worker validiert die Struktur, führt Business Rules aus und übergibt die Objekte/Referenzen an den Rust-Kern.

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

**Veröffentlichung:**

- Der Rust-Kern publiziert Events auf einem eigenen JetStream-Subject (`events.crud`).
- Events werden **persistent** gespeichert und erst nach erfolgreichem Acknowledgment durch den Consumer entfernt (WorkQueue-Prinzip).
- Dadurch gehen keine Änderungen verloren, auch wenn die Middleware kurzfristig nicht erreichbar ist.

**Filterung:**

- Für den Webhook-Outbound werden Events anhand konfigurierbarer Filter selektiert:
  - Nach `event_type` (z. B. nur `object.updated`)
  - Nach `object_type` (z. B. nur `product`, nicht `category`)
  - Nach bestimmten Attributen (z. B. nur wenn `price` geändert wurde)
  - Nach Statusübergängen (z. B. nur Freigaben)

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
    }
  }
}
```

**Verarbeitungsablauf:**

1. Der Rust-Kern publiziert jedes CRUD-Event in NATS JetStream.
2. Ein Python-Worker abonniert den Event-Stream und prüft die Filterregeln des Webhook-Konnektors.
3. Bei Übereinstimmung baut der Worker die HTTP-Anfrage an die Middleware zusammen:
   - Body: das gefilterte Event oder das vollständige Objekt (konfigurierbar)
   - ggf. zusätzliche Informationen wie `resolved_attributes` (nach Vererbung/Sprachbaum)
4. Der Worker sendet die Anfrage mit Wiederholungslogik.
5. Bei dauerhaftem Fehler wird das Event in eine Dead-Letter-Queue verschoben und kann später manuell verarbeitet werden.

**Vorteile dieser Architektur:**

- **Entkopplung:** Änderungen werden sofort erfasst, ohne die Kern-Performance zu beeinträchtigen.
- **Zuverlässigkeit:** Persistente Events und Retry-Mechanismen verhindern Datenverlust.
- **Flexibilität:** Filter können pro Middleware-Kanal unterschiedlich konfiguriert werden.
- **Wiederverwendbarkeit:** Derselbe Mechanismus kann später für weitere Ziele (z. B. Suchmaschinen, Analytics) genutzt werden.

**Abgrenzung zu Standard-Outbounds:**

- Der `json_export` dient dem **Batch-Export** ganzer Datenbestände (z. B. für nächtliche Abgleiche).
- Der `webhook` dient dem **ereignisgetriebenen Push** einzelner Änderungen in Echtzeit.
- Beide nutzen dieselbe Infrastruktur (NATS, Python-Worker), aber unterschiedliche Trigger und Verarbeitung.

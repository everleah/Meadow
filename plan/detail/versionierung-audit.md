# Versionierung & Audit

- **Hauptdokument:** Abschnitt 13 ([Versionierung & Audit](../meadow-pim-architektur.md#13-versionierung--audit))
- **Relevante Phasen:** Phase 2, 7
- **Stand:** Entwurf

Meadow PIM setzt auf eine **schlanke Versionierung** und ein **einfaches Audit-Log**, um Änderungen nachvollziehbar zu machen, ohne die Komplexität einer vollständigen Revisionshistorie zu tragen. Die Prämisse „einfach und performant“ bleibt dabei erhalten.

## Versionierung von Objekten

- Jedes Objekt besitzt ein `version`-Feld (Integer), das bei **jeder Änderung** automatisch inkrementiert wird.
- Zusätzlich werden `created_at`, `updated_at` und `updated_by` gepflegt:
  - `created_at`: Zeitpunkt der Erstellung (unveränderlich)
  - `updated_at`: Zeitpunkt der letzten Änderung
  - `updated_by`: Benutzer- oder Systemkennung, die die Änderung ausgelöst hat
- Die Versionierung gilt für **alle Objekttypen** (einschließlich Kategorien, gespeicherter Suchen und Collections) sowie für die technische Tabelle `attribute_definition`.

**Beispiel:**

```json
{
  "id": "object_123",
  "type": "product",
  "version": 7,
  "created_at": "2026-08-14T08:00:00Z",
  "updated_at": "2026-08-14T11:30:00Z",
  "updated_by": "user_42"
}
```

## Audit-Log

Für die Nachvollziehbarkeit von Änderungen wird eine separate Tabelle `audit_log` geführt. Sie protokolliert **wesentliche Ereignisse**, insbesondere:

- Änderungen an Objektattributen (optional mit alten und neuen Werten)
- Statuswechsel (z. B. `draft` → `in_review` → `approved`)
- Anlegen und Löschen von Objekten
- Änderungen an Referenzen (z. B. Kategoriezuordnung, Vererbungsrelationen)
- Konfigurationsänderungen (z. B. Priorität von Vererbungsrelationen)
- Änderungen an Benutzern, Gruppen, Rollen und Daten-Scopes

**Beispiel eines Audit-Eintrags:**

```json
{
  "id": "audit_001",
  "object_id": "object_123",
  "user_id": "user_42",
  "action": "status_changed",
  "from": "in_review",
  "to": "approved",
  "timestamp": "2026-08-14T11:30:00Z",
  "metadata": {
    "request_id": "req_456"
  }
}
```

**Felder im Audit-Log:**

- `object_id`: betroffenes Objekt (falls vorhanden)
- `user_id`: auslösender Benutzer oder Systemkennung
- `action`: Art der Änderung (z. B. `object_created`, `attribute_updated`, `status_changed`, `reference_created`)
- `from` / `to`: alte und neue Werte (optional, je nach Aktion)
- `timestamp`: Zeitpunkt der Änderung
- `metadata`: zusätzliche Informationen (Request-ID, Quelle des Ereignisses)

## Beziehung zum Eventsystem

Das Eventsystem und das Audit-Log ergänzen sich:

- **Events** sind flüchtige, technische Nachrichten für asynchrone Verarbeitung (z. B. Webhooks an die Middleware).
- **Audit-Log** ist die persistente, fachliche Aufzeichnung von Änderungen für Benutzer und Administratoren.

Der Rust-Kern kann das Audit-Log direkt aus den internen CRUD-Operationen befüllen – unabhängig von den publizierten Events. Dadurch ist das Audit-Log auch dann vollständig, wenn Events nicht verarbeitet werden.

## Bewusster Verzicht auf vollständige Revisionshistorie

Eine **vollständige Revisionshistorie** (Speicherung jedes alten Standes) ist **bewusst nicht** Teil des MVP. Sie würde:

- den Speicherbedarf erhöhen,
- die Schreiboperationen verlangsamen,
- die Datenbankstruktur verkomplizieren.

Stattdessen ermöglicht die Kombination aus `version`, `updated_at`/`updated_by` und Audit-Log eine ausreichende Rückverfolgbarkeit für die meisten KMU-Szenarien.

**Spätere Erweiterung (optional):**

Falls gesetzliche oder fachliche Anforderungen eine Revisionshistorie erfordern, kann später eine Tabelle `object_version` eingeführt werden, die bei jeder Änderung eine Kopie des vorherigen Zustands speichert. Das Datenmodell und die API-Struktur sind so gewählt, dass eine solche Erweiterung ohne Bruch möglich ist.

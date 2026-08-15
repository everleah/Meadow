# Workflow & Freigaben

- **Hauptdokument:** Abschnitt 8 ([Workflow & Freigaben](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 2, 4, 9
- **Stand:** Entwurf

## Workflows (Zustandsmaschine)

- Es wird **keine schwere Workflow-Engine** eingesetzt.
- Die Statuslogik ist eine einfache Zustandsmaschine, implementiert im Python Business-Rules-Service.
- Erlaubte Übergänge werden zentral definiert, z. B.:

```
draft → in_review → approved → published → archived
                     ↓
                  rejected → draft
```

- Der Rust-Kern speichert nur das Statusfeld am Objekt und protokolliert Änderungen im Audit-Log.

## Freigaben

- **Grundprinzip**: Jedes Objekt besitzt ein Statusfeld (`status`). Der Wert `published` bedeutet global freigegeben.
- Outbound-Prozesse filtern standardmäßig nach `status: "published"`.
- **Kanalspezifische Freigaben** (z. B. „freigegeben für Webshop“) werden bei Bedarf später über eine separate Tabelle `release` umgesetzt (object_id, channel, status). Für den Start reicht das einfache Statusfeld.

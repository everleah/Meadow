# Workflow & Freigaben

- **Hauptdokument:** Abschnitt 8 ([Workflow, Freigaben & Business Rules](../meadow-pim-architektur.md#8-workflow-freigaben--business-rules))
- **Relevante Phasen:** Phase 4, 7, 9
- **Stand:** Entwurf

## Workflows (Zustandsmaschine)

- Es wird **keine schwere Workflow-Engine** eingesetzt.
- Die Statuslogik ist eine einfache Zustandsmaschine, implementiert im Python Business-Rules-Service.
- **Umsetzung über `workflow_guard`-Regeln:** Erlaubte Übergänge werden als Business Rules (Typ `workflow_guard`, Abschnitt 8 / [business-rules.md](business-rules.md)) geprüft – sie laufen **blockierend** bei Statuswechsel im Schreibpfad des Kerns und liefern das Übergangs-Verdikt (`result.allow(bool, reason)`); bei Fehlern greifen fail-closed + Circuit Breaker der Rule-Infrastruktur.
- Erlaubte Übergänge werden zentral definiert, z. B.:

```text
draft → in_review → approved → published → archived
                     ↓
                  rejected → draft
```

- Der Rust-Kern speichert nur das Statusfeld am Objekt und protokolliert Änderungen im Audit-Log.

## Freigaben

- **Grundprinzip**: Jedes Objekt besitzt ein Statusfeld (`status`). Der Wert `published` bedeutet global freigegeben.
- Outbound-Prozesse filtern standardmäßig nach `status: "published"`.
- **Kanalspezifische Freigaben** (z. B. „freigegeben für Webshop“) werden bei Bedarf später über eine separate Tabelle `release` umgesetzt (object_id, channel, status). Für den Start reicht das einfache Statusfeld.

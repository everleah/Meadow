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

## Massen-Freigabe (implementiert, Phase 9)

- **Operation `send_for_approval`** der Bulk-API (`POST /objects/bulk`, [api-design.md](api-design.md)): überführt **alle Objekte einer Auswahl** in den Freigabe-Workflow, indem der Status auf `parameters.approval_status` gesetzt wird (Default **`in_review`**, also der Review-Zustand der Zustandsmaschine). Der Übergang läuft über den normalen Schreibpfad des Kerns und wird damit weiterhin von **`workflow_guard`-Regeln** geprüft.
- **„Bulk-Änderung durch den Freigabe-Workflow schleusen“:** jede datenändernde Operation (`set_attribute`, `clear_attribute`, `add_category`, `remove_category`, `change_parent`, `replace_text`) akzeptiert `parameters.send_for_approval = true`. Der Worker (`meadow/worker/bulk.py`) wendet zuerst die Änderung an und ruft danach `request_approval` auf.
- **Ablehnung ist nicht destruktiv:** Lehnt eine `workflow_guard`-Regel den Übergang ab, bleibt die Änderung gespeichert (sie wurde bereits committet); der Report weist das Objekt mit `approval_error` aus. Nur bei der Operation `send_for_approval` selbst (keine Datenänderung) gilt eine Ablehnung als Fehler des Objekts.
- **UI:** Der Bulk-Wizard bietet die Operation „Zur Freigabe senden“ (mit Zielstatus-Feld) und bei allen datenändernden Operationen den Schalter „Anschließend zur Freigabe senden“; Find & Replace hat denselben Schalter. Der Report zählt `approvals` und listet blockierte Freigaben.

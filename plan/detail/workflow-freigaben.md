# Workflow & Freigaben

- **Hauptdokument:** Abschnitt 8 ([Workflow, Freigaben & Business Rules](../meadow-pim-architektur.md#8-workflow-freigaben--business-rules))
- **Relevante Phasen:** Phase 4, 7, 9
- **Stand:** Umgesetzt — Zustandsmaschine über `workflow_guard`-Regeln (Phase 4), Massen-Freigabe (Phase 9), Kanäle & kanalspezifische Freigaben (Phase 9).

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
- **Die Oberfläche führt die Zustandsliste an genau einer Stelle** (`meadow/frontend/display.py`: `OBJECT_STATUSES` = `RELEASE_STATUSES`, benutzt vom Produktformular, vom Kategorie-Dialog und vom Bulk-Wizard) und zeigt die fünf Zustände `draft`, `in_review`, `approved`, `published`, `archived`. Weil der Kern die Werteliste **nicht** prüft, kann in den Daten jeder andere Wert stehen (Import, API, MCF, Regel) — eine Statusauswahl nimmt den **gespeicherten Wert deshalb immer** in ihre Optionen auf (`display.status_options(current)`): ein `ui.select`, dessen Wert nicht in den Optionen liegt, wirft `Invalid value` und macht aus der Seite eine **500** (live am 2026-09-21: ein `approved`-Produkt, weil die Liste vier der fünf Zustände kannte). Die bekannten fünf haben i18n-Schlüssel `status.<zustand>` (der Kern liefert sie als `label_key` im Suchkatalog); alles andere zeigt den rohen Wert.

## Freigaben

- **Grundprinzip**: Jedes Objekt besitzt ein Statusfeld (`status`). Der Wert `published` bedeutet global freigegeben.
- Outbound-Prozesse filtern standardmäßig nach `status: "published"`.
- **Kanalspezifische Freigaben** (z. B. „freigegeben für Webshop“) laufen über die Tabelle `release` — siehe [Kanäle & kanalspezifische Freigaben](#kanäle--kanalspezifische-freigaben-implementiert-phase-9). Für einfache Fälle reicht das Statusfeld: der globale Status bleibt die redaktionelle Freigabe, die Kanäle sind eine eigene Dimension.

## Massen-Freigabe (implementiert, Phase 9)

- **Operation `send_for_approval`** der Bulk-API (`POST /objects/bulk`, [api-design.md](api-design.md)): überführt **alle Objekte einer Auswahl** in den Freigabe-Workflow, indem der Status auf `parameters.approval_status` gesetzt wird (Default **`in_review`**, also der Review-Zustand der Zustandsmaschine). Der Übergang läuft über den normalen Schreibpfad des Kerns und wird damit weiterhin von **`workflow_guard`-Regeln** geprüft.
- **„Bulk-Änderung durch den Freigabe-Workflow schleusen“:** jede datenändernde Operation (`set_attribute`, `clear_attribute`, `add_category`, `remove_category`, `change_parent`, `replace_text`) akzeptiert `parameters.send_for_approval = true`. Der Worker (`meadow/worker/bulk.py`) wendet zuerst die Änderung an und ruft danach `request_approval` auf.
- **Ablehnung ist nicht destruktiv:** Lehnt eine `workflow_guard`-Regel den Übergang ab, bleibt die Änderung gespeichert (sie wurde bereits committet); der Report weist das Objekt mit `approval_error` aus. Nur bei der Operation `send_for_approval` selbst (keine Datenänderung) gilt eine Ablehnung als Fehler des Objekts.
- **UI:** Der Bulk-Wizard bietet die Operation „Zur Freigabe senden“ (mit Zielstatus-Feld) und bei allen datenändernden Operationen den Schalter „Anschließend zur Freigabe senden“; Find & Replace hat denselben Schalter. Der Report zählt `approvals` und listet blockierte Freigaben.

## Kanäle & kanalspezifische Freigaben (implementiert, Phase 9)

### Kanäle (`channel`)

- Ein **Kanal** ist eine Ausgabestrecke (Webshop, Printkatalog, Marktplatz, App). Er ist eine **Konfigurationsentität** wie Einheiten oder Enum-Sets, kein Fachobjekt: Tabelle `channel` mit stabilem **`code`** (eindeutig, z. B. `webshop`), **mehrsprachigem `label`**, optionaler `description`, `active` und `position`.
- **API:** `GET /channels` (ab `editor`), `GET /channels/{id}` (Code oder volle Record-ID), `POST`/`PATCH`/`DELETE /channels…` (admin). Das **Löschen** ist blockiert (**409**), solange `release`-Zeilen den Kanal verwenden; die Antwort nennt die Anzahl.
- **MCF:** Kanäle gehören zum Konfigurations-Export/-Import (Sektion `channels`, Abhängigkeitsreihenfolge direkt nach `languages`). Freigaben selbst sind **Daten** und nicht im MCF.
- Der Kanal ist die Bezugsgröße für **Freigaben** und (später) für kanalspezifische Vollständigkeit; für Exporte wirkt er als **Filter** (siehe unten).

### Freigaben (`release`)

- Tabelle `release` mit **`object_id`** (`record<object>`), **`channel`** (Code des Kanals), **`status`**, `released_at`/`released_by` sowie `created_at`/`updated_at`/`updated_by`; **eindeutig über `(object_id, channel)`** — pro Kanal höchstens eine Zeile je Objekt.
- **Gleiche Zustandswerte wie der Objektstatus** (`draft`, `in_review`, `approved`, `published`, `archived`). Der Kern prüft die Werteliste **nicht** (wie beim Objektstatus); die Zustandsmaschine bleibt Sache der `workflow_guard`-Regeln.
- **Lebenszyklus:** `GET /objects/{id}/releases` antwortet **404**, wenn das Objekt unbekannt oder gelöscht ist (keine verwaisten Listen); beim **Löschen eines Objekts** werden seine `release`-Zeilen mit dem Objektkaskaden entfernt (wie die `reference`-Kanten), damit keine verwaisten Freigaben einen Kanal blockieren.
- Die **Existenz** der Zeile bedeutet „dieser Kanal ist bewertet/gepflegt“; ihr `status` ist der Kanalzustand. **„Für den Kanal veröffentlicht“ = `status: published`** (Default der Filter). **Zurückziehen** = Zeile löschen.
- **Der globale `status` bleibt unabhängig:** Ein Objekt kann global `published` sein und nur für einzelne Kanäle freigegeben (oder umgekehrt). Die Hausregel „Kanal-Freigabe nur bei global `published`“ ist bewusst **keine** Kernlogik, sondern eine `workflow_guard`-Regel — der Kern bleibt die Datenautorität, die Politik ist konfigurierbar.
- **API:**
  - `GET /objects/{id}/releases` (ab `editor`) → Zeilen des Objekts (Kanal, Zustand, Zeit/ Benutzer).
  - `PUT /objects/{id}/releases` (`editor+`) mit `{channel, status}` → **Upsert** der Kanal-Freigabe (legt an oder ändert den Zustand).
  - `DELETE /objects/{id}/releases/{channel}` → zurückziehen (204; 404 ohne Zeile).
  - `GET /objects?released_for=<code>[&release_status=published]` → nur Objekte mit passender Freigabe; `?releases=true` liefert zusätzlich je Objekt `releases: [{channel, status}]` (für die Grid-Spalte).
  - **Bulk:** `release_for_channel` / `withdraw_from_channel` (`parameters.channel`, optional `parameters.release_status`, Default `published`) — beides **idempotent**: eine unveränderte Freigabe ist `skipped`, nicht Fehler.
- **Regelprüfung:** Jede Änderung an einer Kanal-Freigabe läuft durch die `workflow_guard`-Regeln im Ausführungsmodus `transition`. Der Regelkontext erhält zusätzlich **`ctx.channel`** (Kanalcode) und **`ctx.release_status`** (Zielzustand; beim Zurückziehen der Marker `withdrawn`), während **`obj.status` der globale Objektstatus bleibt** — eine Regel kann damit z. B. „Kanal `webshop` nur mit Preis“ oder „Kanal nur bei global `published`“ formulieren. Das Objekt-View der Regelprüfung trägt **eigene plus geerbte Werte** (Sprachauflösung findet nicht statt, weil eine Freigabe an keine Arbeitssprache gebunden ist). Eine Ablehnung blockiert mit **400** und dem Regelgrund (wie beim Statuswechsel); **auch das Zurückziehen** wird geprüft.
- **Audit & Events:** Audit `object_released` / `object_release_withdrawn` (Kanal und Zustand in den Metadaten) und Outbox-Events `object.released` / `object.unreleased` (Kanal + vorheriger Zustand), damit Webhooks und Exporte auf Kanaländerungen reagieren können.
- **UI:** Admin-Seite `/channels`; im Produkt-Formular der Abschnitt **„Channels“** (je Kanal Label, Zustands-Badge, Statusauswahl, „Zurückziehen“); im Grid die **Bulk-Operationen** „Für Kanal freigeben“/„Aus Kanal zurückziehen“, der **Filter** „freigegeben für Kanal“ (URL `?released_for=`) und die Spalte **„Releases“** mit Kanal-Badges; im Export-Wizard die Quelle **„Channel“** (nur für den Kanal freigegebene Objekte) und im `json_export`-Profil das Feld `channel`.
- **Bewusst nicht enthalten** (eigene Ausbaustufe): kanalspezifische Vollständigkeit (je Kanal abweichende Pflichtattribute), kanalspezifische Werte (Scoping je Kanal), bidirektionale Kanalsynchronisation.

### Regeln für Kanal-Freigaben (Beispiele)

```python
# workflow_guard: eine Kanal-Freigabe setzt die globale Freigabe voraus.
def run(obj, ctx, result):
    if ctx.get("channel") and obj.get("status") != "published":
        return result.allow(False, "publish the object globally before releasing a channel")
    return result.allow(True)
```

```python
# workflow_guard: der Webshop braucht einen Preis.
def run(obj, ctx, result):
    if ctx.get("channel") == "webshop" and obj["attributes"].get("price") in (None, ""):
        return result.allow(False, "the webshop channel requires a price")
    return result.allow(True)
```

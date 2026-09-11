# Konnektor-Konfiguration über Formulare (schema-getrieben)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) und Abschnitt 11 ([Frontend](../meadow-pim-architektur.md#11-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), ergänzend zu Phase 6/7 (Konnektoren & Admin-UI)
- **Stand:** Plan / Entwurf (2026-09-11) — noch nicht umgesetzt. Umsetzungsreif für **Stage 1** (Schema + Validierung + Formular für `webhook`); Stage 2/3 sind skizziert und offen.

**Getroffene Entscheidungen (Vorschlag, zur Bestätigung):**

- Das Parameter-Schema ist **Server-Vertrag** (`GET /connector-types`, Rust-Konstante) — nicht Frontend-Wissen. Damit nutzen UI, MCP/CLI und Validierung dieselbe Quelle.
- **Kein neues Rollenmodell:** `/connectors` bleibt `admin`/`superadmin` (Schreiben) bzw. `X-Api-Key` (Worker, Lesen).
- **JSON bleibt** als Expertenmodus erhalten (Tab „JSON"), inklusive nicht deklarierter Schlüssel. Nichts wird automatisch gelöscht (Abwärtskompatibilität).
- **`PATCH /connectors/{id}`** bekommt optional `parameters_mode: "merge"` (Default bleibt `replace`), damit Formulare nur geänderte Felder senden und maskierte Geheimnisse nichts überschreiben.
- **Regel:** Jeder im Schema deklarierte Parameter wird ausgewertet, und jeder ausgewertete Parameter steht im Schema (sonst ist das Formular Kosmetik).

## 1. Ausgangslage & Problem

Die Konnektoren-Verwaltung (`/connectors`, `meadow/frontend/connectors.py`) ist heute eine Tabelle plus **JSON-Textarea** für `parameters`. Das ist für technisch versierte Anwender ausreichend, für den normalen (nicht-technischen) Admin aber eine Hürde:

- **Kein Auffinden der Felder:** Verschachtelte Parameter (`filter.event_types`, `coalesce.window_seconds`) kennt nur, wer die Doku liest. Im UI gibt es nur einen Hinweis-Satz mit Stichworten.
- **Keine Feldvalidierung:** Der Kern speichert `parameters` als freies JSON (`crates/pim-core/src/api/connectors.rs`: nur `type` wird gegen `VALID_CONNECTOR_TYPES` geprüft, `name` auf Eindeutigkeit). Tippfehler (`"urls"`, `"window_second"`) sind gespeichert, aber **wirkungslos** — der Worker überspringt fehlerhafte Konnektoren nur mit einer Log-Warnung (`meadow/worker/connectors.py::_webhook_config_from_row`), ein fehlendes `url` fällt erst beim Ausbleiben der Events auf.
- **Dokumentierte, aber wirkungslose Felder:** Das Beispiel in [integration-eventsystem.md](integration-eventsystem.md) zeigt `retry_policy` und einen wählbaren Payload („gefiltertes Event oder vollständiges Objekt") — der Worker kennt beides **nicht**: Retry ist fest verdrahtet (NATS-Redelivery, `WEBHOOK_MAX_DELIVER = 5`, `WEBHOOK_NAK_DELAY_S = 5`), der Body ist immer das Event (`json=event`).
- **Zeitpunkt der Fehlererkennung:** Konfigurationsfehler zeigen sich im Betrieb (System-Logs/DLQ), nicht beim Speichern.
- **Doppelpflege:** `CONNECTOR_TYPES` steht im Frontend, `VALID_CONNECTOR_TYPES` im Kern — zwei Listen, die manuell synchron gehalten werden (plus Typ-Labels in drei Locales).

Zielgruppe des Formulars ist der **Admin ohne Entwicklerhintergrund** (Middleware-URL + Token eintragen, Filter wählen, testen). Die bestehende Seite bleibt die einzige Stelle für Konnektoren; kein neuer Rollen- oder Freigabeprozess.

**Zusätzlicher Befund (Scope-relevant):** Die Typen `json_import` und `json_export` sind heute **reine Registrierungseinträge** — der Worker lädt nur `type=webhook` (`meadow/worker/connectors.py`). Import- und Export-Jobs nehmen ihre Parameter aus dem Job selbst (`POST /imports?match_by_key=…&emit_events=…`, `POST /exports` bzw. `job_definition.parameters`), nicht aus dem Konnektor. Ein hübsches Formular für diese Typen wäre also **ohne Stage 2 Kosmetik** (siehe Abschnitt 6 und 8).

## 2. Typische Lösungen (Recherche)

- **n8n / Node-RED / Home Assistant:** Parameter werden **je Knoten-/Integrations-Typ deklarativ** beschrieben (Name, Typ, Default, Anzeigename, Sichtbarkeitsbedingung); die Oberfläche erzeugt daraus das Formular. Zugangsdaten (Tokens) sind ein **eigener Credential-Typ**, nicht Teil der Parameter. Home-Assistant-Flows haben zusätzlich einen **Validieren/Testen-Schritt** vor dem Speichern.
- **JSON Schema + `uiSchema` (react-jsonschema-form):** Standardmuster „Schema → Formular". Merkmale, die hier direkt passen: `type`/`enum`/`const` → Textfeld/Select, `required` → Pflichtfeld, `if/then/else` bzw. `dependentRequired` → **bedingte Felder** (z. B. Fenstergröße nur bei aktivem Coalescing), ein separates **UI-Schema** für Gruppierung, Reihenfolge und Hilfetexte. Genau diese Trennung „fachliches Schema / Darstellung" ist die Vorlage für unseren Vertrag.
- **SaaS-/PIM-Muster (Shopify, Akeneo, STEP):** Konfigurationsdialoge gliedern in **Bereiche** (Ziel, Authentifizierung, Filter, Erweitert), markieren Pflichtfelder, zeigen Hilfetexte und bieten „Verbindung testen" statt „JSON einfügen". Fortgeschrittene Felder sind aufklappbar.
- **Gemeinsames Muster:** (1) Schema als **Single Source of Truth**, (2) Gruppierung + Hilfetexte, (3) bedingte Felder, (4) Geheimnisse maskiert, (5) Test/Validierung vor dem Speichern, (6) **JSON als Experten-Rückfall**, (7) unbekannte Schlüssel bleiben erhalten statt still zu verschwinden.

## 3. Zielbild

Der Dialog `/connectors` hat für den gewählten Typ zwei Ansichten:

```
Konnektor bearbeiten                                        [ Formular | JSON ]
Name            [ Middleware Webhook        ]   Typ  [ Webhook           ]
[x] Aktiv

▾ Ziel
   Ziel-URL *      [ https://middleware.example.com/api/meadow-events ]
   Methode         [ POST ▾ ]        Timeout (s)  [ 30 ]
▾ Kopfzeilen (HTTP Headers)
   [ Authorization | Bearer ••••••• ]  [ + Zeile ]   ← Schlüssel/Wert-Zeilen
▾ Filter (leer = alles)
   Event-Typen      [ object.created ✕ ] [ object.updated ✕ ] [ + ]
   Objekttypen      [ product ✕ ] [ category ✕ ] [ + ]
   Attributänderungen [ price ✕ ] [ + ]
▾ Bündelung (Coalescing)
   [x] Events bündeln          Fenster (s) [ 30 ]   ← nur sichtbar wenn aktiv
▾ Erweitert
   Unbekannte Parameter (1): "retry_policy" – wird nicht ausgewertet (JSON-Tab)
```

Die Liste zeigt statt nur „Typ" zusätzlich eine **Zusammenfassung** (z. B. `https://middleware… · Filter: 4 Event-Typen · gebündelt 30 s`), damit erkennbar ist, was ein Konnektor tut.

## 4. Parameter-Schema (Kern-Vertrag)

**Neuer Endpunkt:** `GET /connector-types` (admin oder `X-Api-Key`, read-only) liefert je Typ die Beschreibung des Parameterformulars. Damit entfällt die doppelte Typ-Liste im Frontend.

```json
[
  {
    "type": "webhook",
    "label_key": "connectors.type_webhook",
    "direction": "outbound",
    "wired": true,
    "groups": [
      {
        "key": "target",
        "label_key": "connector_fields.group_target",
        "fields": [
          {
            "key": "url",
            "type": "string",
            "format": "url",
            "required": true,
            "widget": "text",
            "label_key": "connector_fields.webhook.url.label",
            "help_key": "connector_fields.webhook.url.help"
          },
          {
            "key": "method",
            "type": "string",
            "default": "POST",
            "widget": "select",
            "enum": ["POST", "PUT", "PATCH"]
          }
        ]
      },
      {
        "key": "coalesce",
        "label_key": "connector_fields.group_coalesce",
        "fields": [
          { "key": "coalesce.enabled", "type": "boolean", "default": false, "widget": "switch" },
          {
            "key": "coalesce.window_seconds",
            "type": "number",
            "default": 30,
            "minimum": 1,
            "maximum": 3600,
            "widget": "number",
            "show_if": { "key": "coalesce.enabled", "equals": true }
          }
        ]
      }
    ]
  }
]
```

**Feldattribute (verbindlich):**

| Attribut | Bedeutung |
|---|---|
| `key` | Pfad im `parameters`-Objekt, **Punktnotation** für Verschachtelung (`filter.event_types`) |
| `type` | `string` · `number` · `integer` · `boolean` · `string_list` · `string_map` · `json` |
| `widget` | `text` · `password` · `textarea` · `select` · `multiselect` · `switch` · `number` · `key_value` · `json` |
| `required` | Pflichtfeld (Servervalidierung erzwingt es) |
| `default` | Vorbelegung in Formular **und** beim Anlegen, wenn der Client nichts sendet |
| `enum` | feste Auswahl (z. B. Event-Typen aus den Kern-Konstanten `event.rs`) |
| `options_source` | datengetriebene Auswahl, vom Frontend aufgelöst: `object_types` (`GET /object-types`), `attribute_names` (`GET /attribute-definitions`), `units`, später `saved_searches` |
| `label_key` / `help_key` | **i18n-Schlüssel**, keine Texte (Sprachen en/de/fr bleiben in den Locale-Dateien; andere Clients zeigen den `key`) |
| `placeholder`, `minimum`, `maximum`, `pattern`, `format` | Eingabehilfen und Servervalidierung |
| `secret` | Wert maskieren (`widget: password`), „leer = unverändert" |
| `show_if` | bedingte Sichtbarkeit (`{"key": …, "equals"|"in": …}`); **einfache** Form, kein vollständiges JSON-Schema |
| `deprecated` | Feld wird weiterhin gelesen, aber im Formular als „veraltet" markiert |

**Wo das Schema lebt:** als Rust-Konstante im Kern (neues Modul `src/api/connector_schema.rs`, ausgeliefert über `connectors.rs`). Verworfen wurden:

- **Schema nur im Frontend** — doppelte Pflege, keine Servervalidierung, MCP/CLI hätten kein Schema.
- **Schema als DB-Records** (`connector_type`-Tabelle wie `object_type`) — sinnvoll erst mit **eigenen** Konnektor-Typen (Plugin-Registry, Stage 3); im MVP wäre es Overhead ohne Nutzen.

**Parameter je Typ (Ist-Stand der Verbraucher):**

| Typ | Deklarierte Felder | Verbraucher |
|---|---|---|
| `webhook` | `url` (Pflicht), `method`, `headers`, `timeout_s`, `filter.event_types`, `filter.object_types`, `filter.attribute_changes`, `coalesce.enabled`, `coalesce.window_seconds` | `meadow/worker/connectors.py` → `WebhookConfig` (`webhooks.py`) |
| `json_import` | **Stage 2:** `match_by_key`, `match_by_id`, `emit_events`, `source` (als Job-Defaults) | heute `POST /imports`-Query-Parameter / Job-Parameter (`meadow/worker/imports.py`) |
| `json_export` | **Stage 2:** `format`, `fields`, `locales`, `options.email_to` (als Job-Defaults) | heute Job-Parameter (`meadow/worker/exports.py`, `/export`-Wizard) |

## 5. Validierung & Schreib-Semantik

**Kern validiert `parameters` gegen das Schema** (bei `POST` und `PATCH`):

- **Typen:** `string_list` muss eine Liste von Strings sein (der Worker akzeptiert heute auch kommagetrennte Strings — das bleibt als Kulanz, das Schema schreibt die Liste vor), `number` numerisch, `boolean` boolesch, `string_map` Objekt mit String-Werten.
- **Pflichtfelder** (`url`) → `400` mit Feldpfad in der Meldung: `parameters.url: must not be empty`. Mehrere Fehler werden gesammelt (`; `-getrennt). `ApiError` bleibt damit unverändert einfach; eine strukturierte `fields`-Map ist optionale Ausbaustufe (das Formular validiert vorab mit demselben Schema und markiert Felder selbst).
- **Wertebereiche:** `minimum`/`maximum` für Zahlen, `enum` für erlaubte Werte, `pattern` für Strings.
- **Unbekannte Schlüssel:** bleiben **erlaubt** (Abwärtskompatibilität, Expertenmodus) — aber sie werden im Formular als „nicht deklariert" gelistet, damit nichts stillschweigend wirkungslos ist. Optionaler späterer Schalter `strict_parameters` (Ablehnung unbekannter Schlüssel) für aufgeräumte Installationen.
- **Typwechsel:** Beim Wechsel des Konnektor-Typs passt das Formular die Werte an und **warnt**; ein Schalter „Parameter auf die Vorgaben des neuen Typs zurücksetzen" (Default: an) räumt Altwerte auf, sonst bleiben sie als unbekannte Schlüssel erhalten.

**`PATCH`-Semantik (neu, wichtig für Formulare und Geheimnisse):**

```json
{ "parameters": { "coalesce": { "enabled": true } }, "parameters_mode": "merge" }
```

- `replace` (**Default**, unverändert): `parameters` wird vollständig ersetzt — der richtige Modus für den JSON-Editor.
- `merge`: Punktnotation wird **rekursiv** zusammengeführt, `null` **löscht** einen Schlüssel. Grund: das Formular soll maskierte Geheimnisse und nicht angezeigte Felder nicht überschreiben. Die Löschung muss explizit implementiert werden (SurrealDB `MERGE` setzt `NULL` statt `NONE` — Muster wie `remove_attributes` in `api/objects.rs`).

## 6. Frontend

**Neues Modul `meadow/frontend/connector_form.py`:** rendert ein Schema zu NiceGUI-Widgets und liest sie zurück (Schema → Formular → `parameters`). Wiederverwendet werden die bestehenden Muster: `ui.select`/`ui.switch`/`ui.number` wie im Formular, Key-Wert-Zeilen analog zum Kanten-Editor (`form.py::open_edge_editor` mit typspezifischen Eingaben), Mehrfachauswahl wie bei `applicable_types`.

- **Bereiche** als aufklappbare, farbige `ui.expansion` (Muster wie im Produktformular), Reihenfolge und Gruppierung aus dem Schema.
- **Bedingte Felder** über `show_if` (Neuzeichnen des betroffenen Bereichs bei Änderung).
- **Inline-Validierung** mit demselben Schema (Pflichtfeld, Bereich, Auswahl) + Serverfehler als Meldung am Dialog.
- **Dynamische Optionsquellen** (`object_types`, `attribute_names`) werden einmal pro Seitenaufruf geladen und im Select angeboten; unbekannte gespeicherte Werte werden angehängt (Muster: Enum-Editor — verhindert `Invalid value` in `ui.select`).
- **Tabs „Formular" / „JSON":** JSON zeigt das vollständige `parameters`-Objekt (inkl. unbekannter Schlüssel) und ist der Rückfall für Sonderfälle; Umschalten übernimmt die Werte in beide Richtungen. Unbekannte Schlüssel werden im Formular-Tab als Hinweis mit Anzahl gelistet.
- **Vorschau/Test:** Stage 2 (Abschnitt 8). Bis dahin: „Speichern" setzt `parameters_mode: "merge"` mit den geänderten Feldern.
- **Liste:** zusätzliche Spalte „Konfiguration" (Zusammenfassung), Typ-Spalte mit Label aus dem Schema, weiterhin `enabled`-Badge und `type`-Filter (Liste der Typen aus `GET /connector-types` statt der lokalen Konstante).
- **i18n:** neue Keys `connector_fields.<typ>.<pfad>.label|help` plus `connector_form.*` (Tab-Titel, Hinweise, „unbekannte Parameter", Bestätigungen) in en/de/fr — Parität wie gehabt.

## 7. Konsistenz Schema ↔ Verbraucher

Damit das Formular nicht lügt, gilt als Arbeitsregel: **jedes deklarierte Feld wird ausgewertet, jedes ausgewertete Feld ist deklariert**. Zwei offene Stellen im Ist-Stand:

1. **`retry_policy` & Payload-Modus:** in [integration-eventsystem.md](integration-eventsystem.md) dokumentiert (und im UI-Hinweis genannt), im Worker nicht implementiert. Stage 2 macht sie **entweder** wirksam (`retry_policy.max_attempts`/`backoff_seconds` → `WEBHOOK_MAX_DELIVER`/Nak-Delay pro Konnektor; `payload.mode = event|object` → vollständiges Objekt per `GET /objects/{id}?resolve_inheritance=`) **oder** sie fliegen aus Doku/Schema.
2. **`json_import` / `json_export`-Konnektoren** werden von niemandem geladen. Stage 2 koppelt sie als **Defaults** an den Job-Pfad: `POST /imports` ohne explizite Parameter zieht `match_by_key`/`match_by_id`/`emit_events`/`source` des Typ-Konnektors (Kern oder Worker), der Export-Job entsprechend `format`/`fields`/`locales`/`options`. Vorher gibt es für diese Typen **kein** Formular (das Schema markiert sie `wired: false`, die UI zeigt einen Hinweis „Parameter werden noch nicht ausgewertet").

## 8. Test-Button (Stage 2)

„Verbindung testen" ist der größte UX-Gewinn gegenüber JSON. Der Kern hat **keinen HTTP-Client** (keine `reqwest`-Abhängigkeit) → der Test gehört in den Worker:

- Neuer Job-Typ `connector_test` (`POST /connectors/{id}/test`, admin): der Worker führt einen echten Test-Versand aus (Webhook: synthetisches Event an `url`, Antwort-Status/Latenz/Auszug aus dem Body; Import/Export: Auflösen der Defaults + Trockenlauf) und schreibt das Ergebnis in `job_run.result`.
- UI: Button im Dialog → Fortschritt (bestehendes Job-Polling-Muster) → Ergebnisdialog mit Status, Dauer, Antwortauszug und Klartext-Hinweis (z. B. „401 Unauthorized — Token prüfen").
- Der Test verändert keine Daten (kein Event im Stream, kein Objekt wird geschrieben).

## 9. Rechte, Geheimnisse, Audit

- **Rechte unverändert:** Schreiben `admin`/`superadmin`; Lesen zusätzlich `X-Api-Key` (Worker). `editor` bekommt **keinen** Zugriff auf Konnektor-Parameter (Zugangsdaten).
- **Geheimnisse:** `secret: true` → Eingabe maskiert, Anzeige als `•••`, „leer lassen = unverändert" (durch `parameters_mode: merge` technisch abgesichert). Header-Tokens liegen wie bisher im Klartext in SurrealDB — eine getrennte Secret-Ablage (Objektstorage/Vault) ist **nicht** MVP, aber als Stage 3 notiert.
- **Audit:** bestehende Einträge `connector_created`/`connector_updated`/`connector_deleted` bleiben; die Metadaten nennen künftig `{name, type, changed_keys}` — **ohne Werte**, damit Tokens nicht im Audit-Log landen.

## 10. Auswirkungen auf bestehende Dokumente & Endpunkte

| Stelle | Änderung |
|---|---|
| `api-design.md` | neuer Endpunkt `GET /connector-types` (geplant) + `PATCH /connectors/{id}` mit `parameters_mode` |
| `integration-eventsystem.md` | Verweis auf dieses Dokument; `retry_policy`/Payload im Beispiel als **nicht verdrahtet** markieren; Parameterliste je Typ verlinken |
| `frontend.md` (Punkt 13) | Formular + Expertenmodus + Zusammenfassungsspalte als geplant/umgesetzt nachtragen |
| `datenmodell.md` (`connector.parameters`) | Hinweis: Struktur ist je Typ durch das Schema beschrieben; unbekannte Schlüssel bleiben erhalten |
| `datenexport-import-ui.md` | Abgrenzung: Wizards bleiben der **Anwenderpfad**; Konnektor-Formulare konfigurieren **Defaults/Profile** für den technischen Pfad |
| `meadow/AGENTS.md`, `crates/AGENTS.md` | Modul `connector_form.py` bzw. `connector_schema.rs` + Regel „Schema ↔ Verbraucher" |

## 11. Implementierungsschritte

**Stage 1 — Schema, Validierung, Formular (MVP):**

1. **Kern:** `src/api/connector_schema.rs` (Schema-Konstante für `webhook`, inkl. Gruppen/Feldern/i18n-Keys), `GET /connector-types` (admin/`X-Api-Key`), Validierung der `parameters` in `create_connector`/`update_connector` (Typen, Pflicht, Bereiche, Sammelmeldung mit Feldpfad), `parameters_mode: merge` inkl. `null` = löschen. Unit-Tests für Validator und Merge.
2. **Frontend:** `connector_form.py` (Schema → Widgets, `show_if`, dynamische Optionsquellen, Tabs Formular/JSON, Hinweis auf unbekannte Parameter), Dialog in `connectors.py` umbauen, `api.py` um `list_connector_types` erweitern, `CONNECTOR_TYPES` aus dem Frontend entfernen, Zusammenfassungsspalte, i18n-Keys in en/de/fr.
3. **Doku:** dieses Dokument auf „umgesetzt (Stage 1)" setzen, `frontend.md`, `api-design.md`, `integration-eventsystem.md`, DOX-Dateien.
4. **Tests:** pytest für den Formular-Renderer (Schema → Werte → Payload, `show_if`, unbekannte Schlüssel), API-Roundtrip mit gemocktem Kern, i18n-Parität; Browser-Verifikation (Konnektor anlegen/bearbeiten, Fehlermeldung bei fehlender URL, Wechsel Formular ↔ JSON).

**Stage 2 — Konsistenz & Test:**

5. `retry_policy`/Payload-Modus: wirksam machen **oder** entfernen (Entscheidung s. u.); `json_import`/`json_export` als Job-Defaults verdrahten und im Schema `wired: true` setzen.
6. Job-Typ `connector_test` + Worker-Testversand + „Verbindung testen"-Dialog mit Ergebnis.

**Stage 3 — Ausbau (optional):**

7. Eigene Konnektor-Typen als DB-Records (Plugin-Registry im Worker), Secret-Ablage, Konnektor-Profile als MCF-Sektion (Export/Import), Verbindungstest automatisch nach dem Speichern.

**Nicht-Ziele:** keine Plugin-Laufzeit im MVP, keine neuen Rollen, kein Ersatz der Import-/Export-Wizards, keine Migration bestehender Parameter (Altdaten bleiben gültig und sichtbar).

## 12. Testfälle

- `GET /connector-types` liefert das `webhook`-Schema inkl. Gruppen, `required`, `enum` und `show_if`.
- `POST /connectors` ohne `url` → `400` mit `parameters.url` in der Meldung; mit ungültigem `method` → `400`; gültig → `201`.
- `PATCH` mit `parameters_mode: merge` ändert nur den genannten Pfad (`coalesce.enabled`) und lässt `headers`/unbekannte Schlüssel unangetastet; `{"coalesce": null}` löscht den Zweig.
- `PATCH` mit `replace` (Default) verhält sich wie bisher (vollständiges Ersetzen) — Regressionsschutz für den JSON-Modus.
- Unbekannter Schlüssel (`retry_policy`) bleibt nach Speichern im Formular erhalten und wird als „nicht deklariert" gelistet.
- Formular-Roundtrip: Schema → Werte → Payload entspricht dem bisherigen JSON (`url`, `method`, `headers`, `filter.*`, `coalesce.*`, `timeout_s`).
- `show_if`: `coalesce.window_seconds` ist unsichtbar bei `enabled = false`, sichtbar danach; der Wert bleibt beim Ausblenden erhalten.
- Worker-Regression: ein per Formular gespeicherter Konnektor wird vom Webhook-Supervisor geladen (Live-Reload ≤ 30 s) und liefert ein Event aus.
- Rechte: `editor` → `403` auf `POST/PATCH /connectors`; Worker mit `X-Api-Key` liest weiterhin.
- Locale-Parität für die neuen Keys.

## 13. Offene Punkte

| Frage | Vorschlag |
|---|---|
| `retry_policy` wirksam machen statt entfernen? | **Wirksam machen** (max. Versuche + Backoff pro Konnektor) — es ist das Feld, das im Betrieb am häufigsten gebraucht wird; sonst aus Doku/UI entfernen (Stage 2). |
| Payload-Modus (Event vs. vollständiges Objekt)? | **Nicht** in Stage 1/2; erst wenn die Middleware es braucht (heute sendet der Worker immer das Event). |
| `json_import`/`json_export` als Job-Defaults verdrahten? | **Ja (Stage 2)** — sonst bleibt das Formular für diese Typen wirkungslos. |
| Unbekannte Schlüssel ablehnen? | Nein, nur melden; optionaler Schalter `strict_parameters` später. |
| Strukturierte Feldfehler (`{"error": …, "fields": {…}}`)? | Erst wenn das Formular Feldmarkierungen braucht; Stage 1 kommt mit Pfad in der Meldung aus. |
| Schema als JSON-Schema ausliefern (statt eigenem Format)? | Nein — das eigene, kleinere Format genügt (kein vollständiger Validator nötig) und bleibt ohne Zusatzabhängigkeit. Mehrfachauswahl (`string_list`) und Key-Wert-Tabellen sind in JSON Schema umständlicher als hier. |

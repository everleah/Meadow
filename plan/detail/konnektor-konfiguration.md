# Konnektor-Konfiguration über Formulare (schema-getrieben)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) und Abschnitt 11 ([Frontend](../meadow-pim-architektur.md#11-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), ergänzend zu Phase 6/7 (Konnektoren & Admin-UI)
- **Stand:** **Stage 1 umgesetzt** (2026-09-11) — Schema, Servervalidierung, `parameters_mode: merge`, `GET /connector-types` und das Konfigurationsformular sind im Betrieb verifiziert. Stage 2/3 sind skizziert und offen; die Abschnitte markieren den Ist-Stand mit **(umgesetzt)**.

**Getroffene Entscheidungen (umgesetzt):**

- Das Parameter-Schema ist **Server-Vertrag** (`GET /connector-types`, Rust-Konstante) — nicht Frontend-Wissen. Damit nutzen UI, MCP/CLI und Validierung dieselbe Quelle.
- **Kein neues Rollenmodell:** `/connectors` bleibt `admin`/`superadmin` (Schreiben) bzw. `X-Api-Key` (Worker, Lesen).
- **JSON bleibt** als Expertenmodus erhalten (Tab „JSON"), inklusive nicht deklarierter Schlüssel. Nichts wird automatisch gelöscht (Abwärtskompatibilität).
- **`PATCH /connectors/{id}`** bekommt optional `parameters_mode: "merge"` (Default bleibt `replace`), damit Formulare nur geänderte Felder senden und maskierte Geheimnisse nichts überschreiben.
- **Regel:** Jeder im Schema deklarierte Parameter wird ausgewertet, und jeder ausgewertete Parameter steht im Schema (sonst ist das Formular Kosmetik).

## 0. Umsetzungsstand (Stage 1, 2026-09-11)

| Baustein | Ort |
|---|---|
| Schema, `validate`, `normalize`, `apply_defaults`, `merge_parameters` | `crates/pim-core/src/api/connector_schema.rs` |
| Endpunkt `GET /connector-types` | `crates/pim-core/src/api/connectors.rs::list_connector_types` (Route in `app.rs`, OpenAPI in `api/mod.rs`) |
| Validierung + Defaults + `parameters_mode` in `POST`/`PATCH /connectors` | `crates/pim-core/src/api/connectors.rs` |
| Formular-Renderer (Schema ↔ Werte ↔ Patch) | `meadow/frontend/connector_form.py` |
| Dialog, Tabs, Zusammenfassungsspalte | `meadow/frontend/connectors.py` |
| Tests | `cargo test` (Validator/Merge), `tests/test_connector_form.py`, `tests/test_connector_form_state.py` (Patch-Baseline), Locale-Parität en/de/fr |

**Abweichungen vom ursprünglichen Entwurf (bewusst):**

- `retry_policy` und ein Payload-Modus stehen **nicht** im Schema (sie sind nicht verdrahtet, siehe Abschnitt 7) — sie erscheinen im Formular nur als „nicht deklarierte Parameter" (Hinweis mit Anzahl).
- `json_import`/`json_export` liefern `wired: false`, **keine** Felder und einen Hinweis-Key (`note_key: connector_fields.unwired`); der Dialog zeigt dort statt des Formulars den Hinweis (Abschnitt 7).
- Die Audit-Metadaten nennen `{name, type, changed_keys}` — **ohne Werte** (Abschnitt 9).
- Beim Anlegen füllt der Kern fehlende deklarierte Felder mit ihren Defaults auf (`apply_defaults`); `normalize` trimmt Strings, bereinigt Listen und entfernt `null`-Werte.
- Unbekannte Schlüssel bleiben erlaubt und werden von `merge` **nicht** entfernt; ein unbekannter `parameters_mode` quittiert der Kern mit `400`.

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

Die Liste zeigt statt nur „Typ" zusätzlich eine **Zusammenfassung** (z. B. `Target: https://middleware… · Filter: 4 Event-Typen · Batched: 30 s · 1 not evaluated`), damit erkennbar ist, was ein Konnektor tut.

## 4. Parameter-Schema (Kern-Vertrag) (umgesetzt)

**Endpunkt:** `GET /connector-types` (admin oder `X-Api-Key`, read-only) liefert je Typ die Beschreibung des Parameterformulars. Damit entfällt die doppelte Typ-Liste im Frontend (`CONNECTOR_TYPES` ist dort entfernt). Die Antwort der laufenden Installation:

```json
[
  { "type": "webhook", "label_key": "connectors.type_webhook", "direction": "outbound", "wired": true, "groups": [ /* target, headers, filter, coalesce */ ] },
  { "type": "json_import", "label_key": "connectors.type_json_import", "direction": "inbound", "wired": false, "groups": [], "note_key": "connector_fields.unwired" },
  { "type": "json_export", "label_key": "connectors.type_json_export", "direction": "outbound", "wired": false, "groups": [], "note_key": "connector_fields.unwired" }
]
```

Ausschnitt `webhook.target` (alle Felder der Installation):

```json
{
  "key": "target",
  "label_key": "connector_fields.group_target",
  "fields": [
    { "key": "url", "type": "string", "widget": "text", "required": true,
      "label_key": "connector_fields.webhook.url.label", "help_key": "connector_fields.webhook.url.help" },
    { "key": "method", "type": "string", "widget": "select", "default": "POST", "enum": ["POST", "PUT", "PATCH"],
      "label_key": "connector_fields.webhook.method.label" },
    { "key": "timeout_s", "type": "number", "widget": "number", "default": 30.0, "minimum": 1.0, "maximum": 600.0,
      "label_key": "connector_fields.webhook.timeout_s.label", "help_key": "connector_fields.webhook.timeout_s.help" }
  ]
}
```

Weitere deklarierte Felder: `headers` (`string_map`, `widget: key_value`, `secret: true`), `filter.event_types` (`string_list`, `enum` aus den Kern-Konstanten in `event.rs`), `filter.object_types` / `filter.attribute_changes` (`string_list`, `options_source: object_types` / `attribute_names`), `coalesce.enabled` (`boolean`, `switch`) und `coalesce.window_seconds` (`number`, `show_if: {"key": "coalesce.enabled", "equals": true}`). `format`/`pattern`/`placeholder` sind im Beispiel oben nur skizziert; der Kern liefert sie (noch) nicht, das Formular nutzt sie nicht.

**Typ-Ebene:** `type`, `label_key`, `direction` (`inbound`/`outbound`), `wired`, `groups`; `note_key` nur bei nicht verdrahteten Typen (`wired: false` → der Dialog zeigt einen Hinweis statt des Formulars).

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
| `minimum`, `maximum` | Wertebereich für `number`/`integer` (Servervalidierung **und** Eingabegrenze) |
| `secret` | Wert maskieren (`widget: password`), „leer = unverändert" |
| `show_if` | bedingte Sichtbarkeit (`{"key": …, "equals": …}`); **einfache** Form, kein vollständiges JSON-Schema |

**Noch nicht ausgeliefert** (im Entwurf vorgesehen, der Kern emittiert und das Formular nutzt sie nicht): `placeholder`, `pattern`, `format`, `show_if.in` und `deprecated`. Sie werden erst mit einem konkreten Bedarf ergänzt; bis dahin gilt die Regel aus der Einleitung (deklariert = ausgewertet).

**Wo das Schema lebt:** als Rust-Konstante im Kern (neues Modul `src/api/connector_schema.rs`, ausgeliefert über `connectors.rs`). Verworfen wurden:

- **Schema nur im Frontend** — doppelte Pflege, keine Servervalidierung, MCP/CLI hätten kein Schema.
- **Schema als DB-Records** (`connector_type`-Tabelle wie `object_type`) — sinnvoll erst mit **eigenen** Konnektor-Typen (Plugin-Registry, Stage 3); im MVP wäre es Overhead ohne Nutzen.

**Parameter je Typ (Ist-Stand der Verbraucher):**

| Typ | Deklarierte Felder | Verbraucher |
|---|---|---|
| `webhook` | `url` (Pflicht), `method`, `headers`, `timeout_s`, `filter.event_types`, `filter.object_types`, `filter.attribute_changes`, `coalesce.enabled`, `coalesce.window_seconds` | `meadow/worker/connectors.py` → `WebhookConfig` (`webhooks.py`) |
| `json_import` | **Stage 2:** `match_by_key`, `match_by_id`, `emit_events`, `source` (als Job-Defaults) | heute `POST /imports`-Query-Parameter / Job-Parameter (`meadow/worker/imports.py`) |
| `json_export` | **Stage 2:** `format`, `fields`, `locales`, `options.email_to` (als Job-Defaults) | heute Job-Parameter (`meadow/worker/exports.py`, `/export`-Wizard) |

## 5. Validierung & Schreib-Semantik (umgesetzt)

**Kern validiert `parameters` gegen das Schema** (bei `POST` und `PATCH`, auch wenn nur `type` geändert wird):

- **Typen:** `string_list` muss eine Liste von Strings sein (der Worker akzeptiert heute auch kommagetrennte Strings — das bleibt als Kulanz, das Schema schreibt die Liste vor), `number` numerisch, `boolean` boolesch, `string_map` Objekt mit String-Werten. Ein Zwischenknoten, der kein Objekt ist (`parameters.filter = "x"`), wird als `parameters.filter: must be an object` gemeldet.
- **Pflichtfelder** (`url`) → `400`, Meldung mit Feldpfad: `parameters.url: is required` bzw. bei leerem String `parameters.url: must not be empty`. Weitere Meldungen: `must be a number`, `must be an integer`, `must be a boolean`, `must be a list of strings`, `must be an object of strings`, `must be one of POST, PUT, PATCH`, `must be at least 1`, `must be at most 600`. Mehrere Fehler werden gesammelt (`; `-getrennt, dedupliziert); `ApiError` bleibt unverändert einfach. Das Formular prüft dieselben Regeln vorab und zeigt die Felder im Klartext, der Server entscheidet.
- **Wertebereiche:** `minimum`/`maximum` für Zahlen, `enum` für erlaubte Werte (auch elementweise bei Listen, z. B. `filter.event_types`).
- **Unbekannte Schlüssel:** bleiben **erlaubt** (Abwärtskompatibilität, Expertenmodus) — aber sie werden im Formular als „nicht deklariert" gelistet (Zähler in der Listenspalte), damit nichts stillschweigend wirkungslos ist. Optionaler späterer Schalter `strict_parameters` (Ablehnung unbekannter Schlüssel) für aufgeräumte Installationen.
- **Normalisierung & Defaults** (`normalize`, `apply_defaults`): Strings werden getrimmt, leere Listen bereinigt, `null`-Werte entfernt; beim **Anlegen** füllt der Kern fehlende deklarierte Felder mit ihren Defaults auf, beim **Bearbeiten** nicht (sonst käme jeder PATCH als Vollersetzung daher).
- **Typwechsel:** Beim Wechsel des Konnektor-Typs validiert der Kern die dann **gültigen** Parameter (die gespeicherten, wenn nur `type` mitgeschickt wird). Das Formular setzt die Werte auf die Vorgaben des neuen Typs zurück (Schalter „Parameter auf die Vorgaben des neuen Typs zurücksetzen"); ohne Reset bleibt der Altstand und erscheint als nicht deklariert.

**`PATCH`-Semantik (wichtig für Formulare und Geheimnisse):**

```json
{ "parameters": { "coalesce": { "enabled": true } }, "parameters_mode": "merge" }
```

- `replace` (**Default**, unverändert): `parameters` wird vollständig ersetzt — der richtige Modus für den JSON-Editor.
- `merge`: Punktnotation wird **rekursiv** zusammengeführt, `null` **löscht** einen Schlüssel. Grund: das Formular soll maskierte Geheimnisse und nicht angezeigte Felder nicht überschreiben. Die Löschung ist **explizit** implementiert (SurrealDB `MERGE` ist ein Deep-Merge und entfernt keine Schlüssel — die Parameter werden nach dem Merge deshalb mit einem vollständigen `SET` geschrieben).

## 6. Frontend (umgesetzt)

**Modul `meadow/frontend/connector_form.py`:** rendert ein Schema zu NiceGUI-Widgets und liest sie zurück (Schema → Formular → `parameters`). Wiederverwendet werden die bestehenden Muster: `ui.select`/`ui.switch`/`ui.number` wie im Formular, Key-Wert-Zeilen analog zum Kanten-Editor (`form.py::open_edge_editor` mit typspezifischen Eingaben), Mehrfachauswahl wie bei `applicable_types`. Die Patch-Bildung (`build_patch`) vergleicht die Formularwerte gegen die Projektion der **gespeicherten** Parameter; nicht deklarierte Schlüssel werden nie gesendet und nie entfernt.

- **Bereiche** als aufklappbare, farbige `ui.expansion` (Muster wie im Produktformular), Reihenfolge und Gruppierung aus dem Schema.
- **Bedingte Felder** über `show_if` (unsichtbare Felder werden ausgeblendet, aber nicht mitgesendet; der Wert bleibt in den Formularwerten erhalten).
- **Inline-Validierung** mit demselben Schema (Pflichtfeld, Bereich, Auswahl elementweise bei Listen) + Serverfehler als Meldung am Dialog.
- **Dynamische Optionsquellen** (`object_types`, `attribute_names`) werden über die API geladen und angeboten; unbekannte gespeicherte Werte werden angehängt (Muster: Enum-Editor — verhindert `Invalid value` in `ui.select`). Leere Einzelauswahlen werden als „nicht gesetzt" (`None`) behandelt, nicht als `""`.
- **Tabs „Formular" / „JSON":** JSON zeigt das vollständige `parameters`-Objekt (inkl. unbekannter Schlüssel) und ist der Rückfall für Sonderfälle; Umschalten übernimmt die Werte in beide Richtungen (JSON → Formular: Werte übernehmen, **ohne** die Patch-Baseline neu zu erfassen; Formular → JSON: hübsch formatiert aus den aktuellen Formularwerten). Unbekannte Schlüssel werden im Formular-Tab als Hinweis mit Anzahl gelistet (Listenspalte: `· 1 not evaluated`).
- **Vorschau/Test:** Stage 2 (Abschnitt 8). Bis dahin: „Speichern" setzt `parameters_mode: "merge"` mit den geänderten Feldern; nur der JSON-Tab (und nicht verdrahtete Typen) sendet `replace`.
- **Liste:** zusätzliche Spalte „Konfiguration" (Zusammenfassung via `summary_text`, z. B. `Target: … · Filter: 2 event types · Batched: 30 s · 1 not evaluated`), Typ-Spalte mit Label aus dem Schema, weiterhin `enabled`-Badge und `type`-Filter (Liste der Typen aus `GET /connector-types` statt der lokalen Konstante).
- **Bedienung:** Doppelklick auf die Zeile löst den Datensatz über die **ID** aus der geladenen Liste auf (die AG-Grid-Zeile enthält nur Anzeigefelder).
- **i18n:** Keys `connector_fields.<typ>.<pfad>.label|help` plus `connector_form.*` (Tab-Titel, Hinweise, „unbekannte Parameter", Bestätigungen) in en/de/fr — Parität wie gehabt.

## 7. Konsistenz Schema ↔ Verbraucher

Damit das Formular nicht lügt, gilt als Arbeitsregel: **jedes deklarierte Feld wird ausgewertet, jedes ausgewertete Feld ist deklariert**. Offene Stellen im Ist-Stand:

1. **`retry_policy` — umgesetzt (Stage 2a):** Der Webhook-Worker liest `retry_policy.max_attempts` (1–10, Default 5) und `retry_policy.backoff_seconds` (1–3600, Default 5) **je Konnektor** (`meadow/worker/webhooks.py::WebhookRetry`): `max_attempts` bestimmt, wann ein Event in die Dead-Letter-Queue wandert (vorher global `WEBHOOK_MAX_DELIVER = 5`), `backoff_seconds` ist die Wartezeit vor dem nächsten Versuch (vorher global 5 s). Der JetStream-Consumer bekommt als `max_deliver` den **Schema-Höchstwert** (10), damit der Worker zuerst dead-lettert und nicht JetStream. Weil eine Nachricht an mehrere Konnektoren verteilt wird, gilt: das **strengste** Limit gewinnt (jede terminale Zustellung beendet die Nachricht) und beim erneuten Zustellen gewinnt die **längste** angeforderte Wartezeit. Verifiziert: `attempt 1/2 → 2/2` (dann DLQ) mit `max_attempts: 2` bzw. `1/3 → 2/3 → 3/3` mit `max_attempts: 3`, und mit `backoff_seconds: 1` liefen alle drei Versuche innerhalb von ~2 s (vorher hätte der Default 5 s gebraucht), Log-Zeile jetzt `attempt n/max`.
2. **Payload-Modus (`payload.mode = event|object`):** weiterhin **nicht** verdrahtet und deshalb nicht im Schema (der Worker sendet immer das Event). Entscheidung: erst umsetzen, wenn die Middleware es braucht.
3. **`json_import` / `json_export`-Konnektoren** werden von niemandem geladen. Stage 2b koppelt sie als **Defaults** an den Job-Pfad: `POST /imports` ohne explizite Parameter zieht `match_by_key`/`match_by_id`/`emit_events`/`source` des Typ-Konnektors (Kern oder Worker), der Export-Job entsprechend `format`/`fields`/`locales`/`options`. Vorher gibt es für diese Typen **kein** Formular (das Schema markiert sie `wired: false`, die UI zeigt einen Hinweis „Parameter werden noch nicht ausgewertet").

## 8. Test-Button (Stage 2)

„Verbindung testen" ist der größte UX-Gewinn gegenüber JSON. Der Kern hat **keinen HTTP-Client** (keine `reqwest`-Abhängigkeit) → der Test gehört in den Worker:

- Neuer Job-Typ `connector_test` (`POST /connectors/{id}/test`, admin): der Worker führt einen echten Test-Versand aus (Webhook: synthetisches Event an `url`, Antwort-Status/Latenz/Auszug aus dem Body; Import/Export: Auflösen der Defaults + Trockenlauf) und schreibt das Ergebnis in `job_run.result`.
- UI: Button im Dialog → Fortschritt (bestehendes Job-Polling-Muster) → Ergebnisdialog mit Status, Dauer, Antwortauszug und Klartext-Hinweis (z. B. „401 Unauthorized — Token prüfen").
- Der Test verändert keine Daten (kein Event im Stream, kein Objekt wird geschrieben).

## 9. Rechte, Geheimnisse, Audit

- **Rechte unverändert:** Schreiben `admin`/`superadmin`; Lesen zusätzlich `X-Api-Key` (Worker). `editor` bekommt **keinen** Zugriff auf Konnektor-Parameter (Zugangsdaten).
- **Geheimnisse:** `secret: true` → Eingabe maskiert, Anzeige als `•••`, „leer lassen = unverändert" (durch `parameters_mode: merge` technisch abgesichert). Header-Tokens liegen wie bisher im Klartext in SurrealDB — eine getrennte Secret-Ablage (Objektstorage/Vault) ist **nicht** MVP, aber als Stage 3 notiert.
- **Audit:** bestehende Einträge `connector_created`/`connector_updated`/`connector_deleted` bleiben; die Metadaten nennen künftig `{name, type, changed_keys}` — **ohne Werte**, damit Tokens nicht im Audit-Log landen.

## 10. Auswirkungen auf bestehende Dokumente & Endpunkte (umgesetzt)

| Stelle | Änderung |
|---|---|
| `api-design.md` | `GET /connector-types` und `PATCH /connectors/{id}` mit `parameters_mode` sind in der Endpunkt-Tabelle dokumentiert |
| `integration-eventsystem.md` | Verweis auf dieses Dokument; `retry_policy` als **umgesetzt** dokumentiert (je Konnektor), Payload-Modus als **nicht verdrahtet** (nicht im Schema) |
| `frontend.md` (Punkt 13) | Formular, Expertenmodus JSON, Zusammenfassungsspalte und das Modul `connector_form.py` sind nachgetragen |
| `datenmodell.md` (`connector.parameters`) | Hinweis: Struktur ist je Typ durch das Schema beschrieben; unbekannte Schlüssel bleiben erhalten |
| `datenexport-import-ui.md` | Abgrenzung: Wizards bleiben der **Anwenderpfad**; Konnektor-Formulare konfigurieren **Defaults/Profile** für den technischen Pfad |
| `meadow/AGENTS.md`, `crates/AGENTS.md` | Modul `connector_form.py` bzw. `connector_schema.rs` + Regel „Schema ↔ Verbraucher" |

## 11. Implementierungsschritte

**Stage 1 — Schema, Validierung, Formular (MVP) — umgesetzt:**

1. **Kern (umgesetzt):** `src/api/connector_schema.rs` (Schema-Konstante für `webhook`, inkl. Gruppen/Feldern/i18n-Keys), `GET /connector-types` (admin/`X-Api-Key`), Validierung der `parameters` in `create_connector`/`update_connector` (Typen, Pflicht, Bereiche, Sammelmeldung mit Feldpfad), `parameters_mode: merge` inkl. `null` = löschen. Unit-Tests für Validator und Merge.
2. **Frontend (umgesetzt):** `connector_form.py` (Schema → Widgets, `show_if`, dynamische Optionsquellen, Tabs Formular/JSON, Hinweis auf unbekannte Parameter), Dialog in `connectors.py` umgebaut, `api.py` um `list_connector_types` erweitert, `CONNECTOR_TYPES` aus dem Frontend entfernt, Zusammenfassungsspalte, i18n-Keys in en/de/fr.
3. **Doku (umgesetzt):** dieses Dokument auf „umgesetzt (Stage 1)" gesetzt, `frontend.md`, `api-design.md`, `integration-eventsystem.md`, DOX-Dateien.
4. **Tests (umgesetzt):** pytest für den Formular-Renderer (Schema → Werte → Payload, `show_if`, unbekannte Schlüssel, Patch-Baseline) und i18n-Parität; Browser-Verifikation (Konnektor anlegen/bearbeiten, Fehlermeldung bei fehlender URL, Wechsel Formular ↔ JSON, Merge erhält nicht deklarierte Schlüssel). Der API-Roundtrip mit gemocktem Kern entfällt: die Formularlogik ist als reine Funktionen abgedeckt, der Live-Roundtrip wurde gegen den laufenden Kern geprüft.

**Stage 2 — Konsistenz & Test:**

**Stage 2a — Wiederholungsrichtlinie je Konnektor — umgesetzt:**

5. `retry_policy.max_attempts`/`backoff_seconds` sind im Schema deklariert (Gruppe „Wiederholung"), werden je Konnektor im Worker angewandt und sind live verifiziert (Abschnitt 7); der Payload-Modus bleibt bewusst entfernt. `crates/AGENTS.md`, `meadow/AGENTS.md`, `integration-eventsystem.md` und dieses Dokument sind angepasst.

**Stage 2b/2c — offen:**

6. `json_import`/`json_export` als Job-Defaults verdrahten und im Schema `wired: true` setzen (Abschnitt 7, Punkt 3).
7. Job-Typ `connector_test` + Worker-Testversand + „Verbindung testen"-Dialog mit Ergebnis (Abschnitt 8).

**Stage 3 — Ausbau (optional):**

8. Eigene Konnektor-Typen als DB-Records (Plugin-Registry im Worker), Secret-Ablage, Konnektor-Profile als MCF-Sektion (Export/Import), Verbindungstest automatisch nach dem Speichern.

**Nicht-Ziele:** keine Plugin-Laufzeit im MVP, keine neuen Rollen, kein Ersatz der Import-/Export-Wizards, keine Migration bestehender Parameter (Altdaten bleiben gültig und sichtbar).

## 12. Testfälle

*Abgedeckt:* Kern-Unit-Tests in `connector_schema.rs` (Schema, `validate`, `normalize`, `apply_defaults`, `merge`), Formular-Tests in `tests/test_connector_form.py` und `tests/test_connector_form_state.py`, Worker-Tests in `tests/test_webhooks.py`/`tests/test_connectors.py` (Wiederholungsrichtlinie), dazu die Live-Prüfung gegen den laufenden Kern (Konnektor anlegen/bearbeiten/löschen über den Dialog) und gegen den laufenden Worker. Die folgenden Fälle sind damit belegt:

- `GET /connector-types` liefert das `webhook`-Schema inkl. Gruppen, `required`, `enum` und `show_if`. *(Kern-Test + Live-Abruf)*
- `POST /connectors` ohne `url` → `400` mit `parameters.url: is required`; mit leerem `url` → `parameters.url: must not be empty`; mit ungültigem `method` → `parameters.method: must be one of POST, PUT, PATCH`; gültig → `201`. *(Kern-Test + Live-Prüfung)*
- `POST` ohne `coalesce`/`method` füllt die Schema-Defaults auf. *(Kern-Test + Live-Prüfung)*
- `PATCH` mit `parameters_mode: merge` ändert nur den genannten Pfad und lässt `headers`/unbekannte Schlüssel unangetastet; `{"coalesce": {"window_seconds": null}}` löscht genau diesen Schlüssel; ein Pflichtfeld kann per Merge nicht entfernt werden (`400`); unbekannter Modus → `400`. *(Kern-Test + Live-Prüfung)*
- `PATCH` mit `replace` (Default) verhält sich wie bisher (vollständiges Ersetzen) — Regressionsschutz für den JSON-Modus. *(Kern-Test)*
- Unbekannter Schlüssel (`x_extra`, im UI „nicht deklariert") bleibt beim Speichern aus dem **Formular** erhalten und wird in Liste/Dialog gezählt. *(Formular-Test + Browser-Verifikation)*
- Formular-Roundtrip: Schema → Werte → Patch entspricht dem bisherigen JSON (`url`, `method`, `headers`, `filter.*`, `coalesce.*`, `timeout_s`); die Patch-Baseline ist die Projektion der **gespeicherten** Parameter, damit JSON-Tab-Änderungen beim Speichern aus dem Formular nicht verloren gehen. *(Formular-Test + Browser-Verifikation: Fenster im JSON-Tab 30 → 45, Speichern aus dem Formular, Prüfung per API)*
- `show_if`: `coalesce.window_seconds` ist unsichtbar bei `enabled = false`, sichtbar danach; der Wert bleibt beim Ausblenden erhalten und wird beim Ausblenden nicht weggeschrieben. *(Formular-Test + Browser-Verifikation)*
- Wiederholungsrichtlinie: `retry_policy.max_attempts` begrenzt die Versuche je Konnektor (Dead-Letter danach), `retry_policy.backoff_seconds` ist die Wartezeit vor dem nächsten Versuch, Wertebereiche werden vom Kern geprüft, fehlende Richtlinien behalten das alte Verhalten (5 Versuche, 5 s). *(Kern-Test, Worker-Tests + Live-Prüfung: `attempt 1/2 → 2/2` bzw. `1/3 → 3/3`, bei `backoff_seconds: 1` lagen alle drei Versuche in ~2 s)*
- Worker-Regression: ein per Formular gespeicherter Konnektor wird vom Webhook-Supervisor geladen (Live-Reload ≤ 30 s) und liefert ein Event aus. *(über den bestehenden Supervisor-Pfad; der Dialog ändert die gespeicherte Struktur nicht)*
- Rechte: `editor` → `403` auf `POST/PATCH /connectors`; Worker mit `X-Api-Key` liest weiterhin (`GET /connectors`, `GET /connector-types`). *(bestehende Rollenprüfung, unverändert)*
- Locale-Parität für die neuen Keys (`connectors.*`, `connector_form.*`, `connector_fields.*`). *(Formular-Test)*

## 13. Offene Punkte

| Frage | Vorschlag |
|---|---|
| `retry_policy` wirksam machen statt entfernen? | **Umgesetzt (Stage 2a):** wirksam gemacht — max. Versuche + Wartezeit je Konnektor (Abschnitt 7). |
| Einzelne Backoff-Werte je Versuch (`[30, 60, 120, 300]`)? | **Offen.** Umgesetzt ist eine konstante Wartezeit (ein Zahlenfeld, auch für Nicht-Techniker verständlich). Eine Liste bräuchte eine neue Feldart (`number_list`) in Schema, Formular und Tests; der Worker akzeptiert eine gespeicherte Liste bereits defensiv (letzter Wert gewinnt). |
| Payload-Modus (Event vs. vollständiges Objekt)? | **Nicht** in Stage 1/2; erst wenn die Middleware es braucht (heute sendet der Worker immer das Event). |
| `json_import`/`json_export` als Job-Defaults verdrahten? | **Ja (Stage 2b)** — sonst bleibt das Formular für diese Typen wirkungslos. |
| Unbekannte Schlüssel ablehnen? | Nein, nur melden; optionaler Schalter `strict_parameters` später. |
| Strukturierte Feldfehler (`{"error": …, "fields": {…}}`)? | Erst wenn das Formular Feldmarkierungen braucht; Stage 1 kommt mit Pfad in der Meldung aus. |
| Schema als JSON-Schema ausliefern (statt eigenem Format)? | Nein — das eigene, kleinere Format genügt (kein vollständiger Validator nötig) und bleibt ohne Zusatzabhängigkeit. Mehrfachauswahl (`string_list`) und Key-Wert-Tabellen sind in JSON Schema umständlicher als hier. |

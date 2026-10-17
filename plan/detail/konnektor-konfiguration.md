# Konnektor-Konfiguration über Formulare (schema-getrieben)

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration--eventsystem)) und Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), ergänzend zu Phase 6/7 (Konnektoren & Admin-UI)
- **Stand:** **Stage 1, 2 und 3 umgesetzt** (Stage 1/2 2026-09-11, Stage 3 2026-10-17 — bis auf die Plugin-Laufzeit der registrierten Typen) — Schema, Servervalidierung, `parameters_mode: merge`, `GET /connector-types`, schema-getriebenes Formular, Verbindungstest, getrennte Secret-Ablage, MCF-Sektion `connectors`, Status-/Payload-/Strict-Ausbauten. Details je Abschnitt, Sammelstand in §14.

**Getroffene Entscheidungen (umgesetzt):**

- Das Parameter-Schema ist **Server-Vertrag** (`GET /connector-types`, Rust-Konstante) — nicht Frontend-Wissen. Damit nutzen UI, MCP/CLI und Validierung dieselbe Quelle.
- **Kein neues Rollenmodell:** `/connectors` bleibt `admin`/`superadmin` (Schreiben) bzw. `X-Api-Key` (Worker, Lesen).
- **JSON bleibt** als Expertenmodus erhalten (Tab „JSON"), inklusive nicht deklarierter Schlüssel. Nichts wird automatisch gelöscht (Abwärtskompatibilität).
- **`PATCH /connectors/{id}`** bekommt optional `parameters_mode: "merge"` (Default bleibt `replace`), damit Formulare nur geänderte Felder senden und maskierte Geheimnisse nichts überschreiben.
- **Regel:** Jeder im Schema deklarierte Parameter wird ausgewertet, und jeder ausgewertete Parameter steht im Schema (sonst ist das Formular Kosmetik).

## 0. Umsetzungsstand (Stages 1 + 2, 2026-09-11)

| Baustein | Ort |
|---|---|
| Schema, `validate`, `normalize`, `apply_defaults`, `merge_parameters` | `crates/pim-core/src/api/connector_schema.rs` |
| Endpunkt `GET /connector-types` | `crates/pim-core/src/api/connectors.rs::list_connector_types` (Route in `app.rs`, OpenAPI in `api/mod.rs`) |
| Validierung + Defaults + `parameters_mode` in `POST`/`PATCH /connectors` | `crates/pim-core/src/api/connectors.rs` |
| Vorgabe-Profile (`type_defaults`) für `POST /imports` / `POST /exports` | `crates/pim-core/src/api/connectors.rs` + `imports.rs`/`exports.rs` |
| Verbindungstest `POST /connectors/{id}/test` + Job-Typ `connector_test` | `crates/pim-core/src/api/connectors.rs`, `meadow/worker/connector_test.py` |
| Formular-Renderer (Schema ↔ Werte ↔ Patch) | `meadow/frontend/connector_form.py` |
| Dialog, Tabs, Test-Dialog, Zusammenfassungsspalte | `meadow/frontend/connectors.py` |
| Tests | `cargo test` (Validator/Merge), `tests/test_connector_form.py`, `tests/test_connector_form_state.py` (Patch-Baseline), `tests/test_connector_test.py` (Test-Handler), `tests/test_webhooks.py`/`tests/test_connectors.py` (Wiederholung), Locale-Parität en/de/fr |

**Abweichungen vom ursprünglichen Entwurf (bewusst):**

- Ein Payload-Modus steht **nicht** im Schema (nicht verdrahtet, siehe Abschnitt 7) — er erscheint im Formular nur als „nicht deklarierter Parameter" (Hinweis mit Anzahl). `retry_policy` ist seit Stage 2a deklariert und wirksam.
- `json_import`/`json_export` sind seit Stage 2b **verdrahtet** (`wired: true`) und tragen je eine Gruppe (`import` bzw. `export`); der Hinweis-Key (`note_key`) bleibt als Vertragsbestandteil für künftige Typen ohne Verbraucher erhalten.
- Die Audit-Metadaten nennen `{name, type, changed_keys}` — **ohne Werte** (Abschnitt 9).
- Beim Anlegen füllt der Kern fehlende deklarierte Felder mit ihren Defaults auf (`apply_defaults`); `normalize` trimmt Strings, bereinigt Listen und entfernt `null`-Werte.
- Unbekannte Schlüssel bleiben erlaubt und werden von `merge` **nicht** entfernt; ein unbekannter `parameters_mode` quittiert der Kern mit `400`.

## 1. Ausgangslage & Problem (vor Stage 1)

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
  { "type": "json_import", "label_key": "connectors.type_json_import", "direction": "inbound", "wired": true, "groups": [ /* import: match_by_key, match_by_id, emit_events, source */ ] },
  { "type": "json_export", "label_key": "connectors.type_json_export", "direction": "outbound", "wired": true, "groups": [ /* export: format, fields, locales, options.email_to */ ] }
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

**Typ-Ebene:** `type`, `label_key`, `direction` (`inbound`/`outbound`), `wired`, `groups`; `note_key` nur bei Typen ohne Verbraucher (`wired: false` → der Dialog zeigt einen Hinweis statt des Formulars; heute gibt es keinen solchen Typ mehr, der Mechanismus bleibt für spätere Erweiterungen).

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
| `json_import` | `match_by_key`, `match_by_id`, `emit_events`, `source` — **umgesetzt (Stage 2b)** als Job-Vorgaben | `POST /imports`-Query-Parameter / Job-Parameter (`meadow/worker/imports.py`) |
| `json_export` | `format`, `fields`, `locales`, `options.email_to` — **umgesetzt (Stage 2b)** als Job-Vorgaben | Job-Parameter (`meadow/worker/exports.py`, `/export`-Wizard) |

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
- **Dynamische Optionsquellen** (`object_types`, `attribute_names`, `languages`) werden über die API geladen und angeboten; unbekannte gespeicherte Werte werden angehängt (Muster: Enum-Editor — verhindert `Invalid value` in `ui.select`). Leere Einzelauswahlen werden als „nicht gesetzt" (`None`) behandelt, nicht als `""`. Eine **Mehrfachauswahl ohne Optionsquelle** ist eine Freitext-Eingabe mit Tags (`new_value_mode="add-unique"`) — so wird `options.email_to` gepflegt, ohne eine Liste von E-Mail-Adressen vorzugeben.
- **Tabs „Formular" / „JSON":** JSON zeigt das vollständige `parameters`-Objekt (inkl. unbekannter Schlüssel) und ist der Rückfall für Sonderfälle; Umschalten übernimmt die Werte in beide Richtungen (JSON → Formular: Werte übernehmen, **ohne** die Patch-Baseline neu zu erfassen; Formular → JSON: hübsch formatiert aus den aktuellen Formularwerten). Unbekannte Schlüssel werden im Formular-Tab als Hinweis mit Anzahl gelistet (Listenspalte: `· 1 not evaluated`).
- **Vorschau/Test (umgesetzt, Stage 2c):** Button „Verbindung testen“ im Dialog-Fuß (Abschnitt 8); gespeichert wird weiterhin per `parameters_mode: "merge"` mit den geänderten Feldern, der JSON-Tab sendet `replace`.
- **Liste:** zusätzliche Spalte „Konfiguration" (Zusammenfassung via `summary_text`, z. B. `Target: … · Filter: 2 event types · Batched: 30 s · 1 not evaluated` bzw. für die Profile `Match: sku · Source: erp` und `Format: csv · Fields: 2 · E-mail: 1`), Typ-Spalte mit Label aus dem Schema, weiterhin `enabled`-Badge und `type`-Filter (Liste der Typen aus `GET /connector-types` statt der lokalen Konstante).
- **Bedienung:** Doppelklick auf die Zeile löst den Datensatz über die **ID** aus der geladenen Liste auf (die AG-Grid-Zeile enthält nur Anzeigefelder).
- **i18n:** Keys `connector_fields.<typ>.<pfad>.label|help` plus `connector_form.*` (Tab-Titel, Hinweise, „unbekannte Parameter", Bestätigungen) in en/de/fr — Parität wie gehabt.

## 7. Konsistenz Schema ↔ Verbraucher

Damit das Formular nicht lügt, gilt als Arbeitsregel: **jedes deklarierte Feld wird ausgewertet, jedes ausgewertete Feld ist deklariert**. Offene Stellen im Ist-Stand:

1. **`retry_policy` — umgesetzt (Stage 2a):** Der Webhook-Worker liest `retry_policy.max_attempts` (1–10, Default 5) und `retry_policy.backoff_seconds` (1–3600, Default 5) **je Konnektor** (`meadow/worker/webhooks.py::WebhookRetry`): `max_attempts` bestimmt, wann ein Event in die Dead-Letter-Queue wandert (vorher global `WEBHOOK_MAX_DELIVER = 5`), `backoff_seconds` ist die Wartezeit vor dem nächsten Versuch (vorher global 5 s). Der JetStream-Consumer bekommt als `max_deliver` den **Schema-Höchstwert** (10), damit der Worker zuerst dead-lettert und nicht JetStream. Weil eine Nachricht an mehrere Konnektoren verteilt wird, gilt: das **strengste** Limit gewinnt (jede terminale Zustellung beendet die Nachricht) und beim erneuten Zustellen gewinnt die **längste** angeforderte Wartezeit. Verifiziert: `attempt 1/2 → 2/2` (dann DLQ) mit `max_attempts: 2` bzw. `1/3 → 2/3 → 3/3` mit `max_attempts: 3`, und mit `backoff_seconds: 1` liefen alle drei Versuche innerhalb von ~2 s (vorher hätte der Default 5 s gebraucht), Log-Zeile jetzt `attempt n/max`.
2. **Payload-Modus (`payload.mode = event|object`):** weiterhin **nicht** verdrahtet und deshalb nicht im Schema (der Worker sendet immer das Event). Entscheidung: erst umsetzen, wenn die Middleware es braucht.
3. **`json_import` / `json_export` — umgesetzt (Stage 2b):** Beide Typen sind jetzt **Profile** für die Job-Enden. Der Kern liest die Parameter des **ersten aktivierten** Konnektors des Typs (`connectors::type_defaults`, sortiert nach `name`) als Vorgaben und füllt damit die Werte, die die Anfrage nicht setzt:
   - `POST /imports`: `match_by_key`, `match_by_id`, `emit_events`, `source` (Reihenfolge: expliziter Query-Parameter > Profil > `source`-Feld des MEF-Dokuments).
   - `POST /exports`: `format` (ist jetzt optional; ohne Vorgabe weiterhin `json`), `fields`, `locales`, `options.email_to` (Reihenfolge: Anfrage > Profil > bisheriger Default).
   Ohne Konnektor bleibt alles wie vorher (`wired: true` in beiden Typen, die Felder sind damit auch validiert und im Formular). **Verifiziert:** Import über das Profil `source=erp-test` schreibt `source` an die Objekte; `emit_events: true` erzeugt genau ein `object.created`, `false` genau ein `import.completed` (über die DLQ beobachtet); Export ohne `format`/`fields` liefert `xlsx` mit den Profil-Spalten und nutzt `options.email_to` („SMTP is not configured" als Beweis, dass die Empfänger ankamen).

## 8. Test-Button (Stage 2c, umgesetzt)

„Verbindung testen" ist der größte UX-Gewinn gegenüber JSON. Der Kern hat **keinen HTTP-Client** (keine `reqwest`-Abhängigkeit) → der Test läuft im Worker:

- **Job-Typ `connector_test`:** `POST /connectors/{id}/test` (admin) legt einen Lauf mit `parameters = {connector_id, name, connector_type, parameters}` an und liefert `202 {job_run_id}`; auditiert als `connector_tested` (ohne Werte). Der Worker (`meadow/worker/connector_test.py`) führt den Test aus:
  - `webhook`: ein **synthetisches Event** (`event_id: connector_test`, `test: true`, Form wie `object.updated`) geht an die gespeicherte URL – mit den konfigurierten Kopfzeilen, Methode und Timeout. Das Ergebnis nennt `ok`, `status_code`, `latency_ms`, eine **Antwort-Kurzfassung** (max. 300 Zeichen, Whitespace normalisiert) und `reachable`; ein Transportfehler wird zu `reachable: false` + `error`. Es entsteht **kein** Outbox-Event, die DLQ bleibt unberührt.
  - `json_import`/`json_export`: **Trockenlauf** der Profilwerte (`dry_run: true`, `resolved: {…}`) – genau das, was `POST /imports`/`POST /exports` übernehmen würden; es wird nichts geschrieben und nichts gesendet.
- **UI:** Button **„Verbindung testen"** im Konnektor-Dialog (Fußzeile) → Erfolgs-/Fehlerdialog mit Status, Dauer, Antwort-Auszug bzw. den aufgelösten Profilwerten und einem **Klartext-Hinweis** (nicht erreichbar · 401/403 → Token prüfen · 404 → Pfad prüfen · 5xx → Ziel-Logs · sonstige 4xx → Methode/Header). Polling über `GET /jobs/runs?ids=<run_id>` (~15 s Budget, danach Hinweis „läuft der Worker?"); für **ungespeicherte** Konnektoren erscheint der Hinweis, zuerst zu speichern (getestet wird der gespeicherte Stand).
- Der Test verändert keine Daten (kein Event im Stream, kein Objekt wird geschrieben; der Aufruf beim Middleware-Ziel ist beabsichtigt).
- **Verifiziert (Live + Browser):** lokaler HTTP-Sink → `ok`, 200, 4 ms, Antwort-Auszug; geschlossener Port → `ok: false`, `reachable: false`, `ConnectError`, Hinweis „Ziel nicht erreichbar"; `json_import`-Profil → `resolved: {match_by_key: sku, emit_events: false, source: erp}`.

## 9. Rechte, Geheimnisse, Audit

- **Rechte unverändert:** Schreiben `admin`/`superadmin`; Lesen zusätzlich `X-Api-Key` (Worker). `editor` bekommt **keinen** Zugriff auf Konnektor-Parameter (Zugangsdaten).
- **Geheimnisse:** `secret: true` → Eingabe maskiert, Anzeige als `•••`, „leer lassen = unverändert" (durch `parameters_mode: merge` technisch abgesichert). Header-Tokens liegen wie bisher im Klartext in SurrealDB — eine getrennte Secret-Ablage (Objektstorage/Vault) ist **nicht** MVP, aber als Stage-3-Option skizziert (Abschnitt 14.2).
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

**Stage 2b — Import-/Export-Profile — umgesetzt:**

6. `json_import`/`json_export` sind als Job-Defaults verdrahtet und im Schema `wired: true` (Abschnitt 7, Punkt 3); `POST /exports.format` ist dafür optional geworden. Neu im Formular: eine Mehrfachauswahl **ohne** Optionsquelle rendert als Freitext-Eingabe mit Tags (nötig für `options.email_to`), und die Optionsquelle `languages` (aktive Datensprachen) ist ergänzt; die Listenzusammenfassung zeigt die Profilwerte (`Format: csv · E-Mail: 1`).

**Stage 2c — Test-Button — umgesetzt:**

7. Job-Typ `connector_test` + Worker-Testversand + „Verbindung testen"-Dialog mit Ergebnis (Abschnitt 8).

**Stage 3 — Ausbau (umgesetzt 2026-10-17, bis auf die Plugin-Laufzeit; §14):**

8. Ausbauoptionen für später — eigene Konnektor-Typen (Plugin-Registry), getrennte Secret-Ablage, Konnektor-Profile im MCF, kleinere Ausbauten. **Details, Nutzen, Aufwand und Voraussetzungen: Abschnitt 14.**

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
- Import-/Export-Profil: `POST /imports` übernimmt `source`/`match_by_key`/`match_by_id`/`emit_events` des `json_import`-Konnektors, wenn die Anfrage sie nicht setzt; `POST /exports` übernimmt `format`/`fields`/`locales`/`options.email_to` des `json_export`-Konnektors. Ohne Konnektor bleibt das Verhalten unverändert. *(Kern-Unit-Tests für die Accessor-Funktionen + Live-Prüfung: `source` am Objekt, `emit_events` über die DLQ, `format`/`fields`/`email_to` im Exportergebnis)*
- Freitext-Mehrfachauswahl: ein `multiselect` ohne Optionsquelle (`options.email_to`) nimmt getippte Werte als Tags an. *(Browser-Verifikation: gespeichert als `options.email_to: ["ops@example.com"]`)*
- Verbindungstest: Webhook-Test meldet Status/Latenz/Auszug, ein geschlossener Port `reachable: false` mit Fehler und Hinweis, ein Import-/Export-Profil die aufgelösten Werte (`dry_run`). Fehlerhinweise werden nach Status gewählt (401/403 → Token, 404 → Pfad, 5xx → Ziel, sonst generisch). Der Test schreibt keine Events und keine Objekte. *(9 Worker-Unit-Tests mit `MockTransport`, 4 Frontend-Tests für die Aufbereitung + Live-/Browser-Prüfung)*
- Worker-Regression: ein per Formular gespeicherter Konnektor wird vom Webhook-Supervisor geladen (Live-Reload ≤ 30 s) und liefert ein Event aus. *(über den bestehenden Supervisor-Pfad; der Dialog ändert die gespeicherte Struktur nicht)*
- Rechte: `editor` → `403` auf `POST/PATCH /connectors`; Worker mit `X-Api-Key` liest weiterhin (`GET /connectors`, `GET /connector-types`). *(bestehende Rollenprüfung, unverändert)*
- Locale-Parität für die neuen Keys (`connectors.*`, `connector_form.*`, `connector_fields.*`). *(Formular-Test)*

## 13. Offene Punkte

| Frage | Vorschlag |
|---|---|
| `retry_policy` wirksam machen statt entfernen? | **Umgesetzt (Stage 2a):** wirksam gemacht — max. Versuche + Wartezeit je Konnektor (Abschnitt 7). |
| Einzelne Backoff-Werte je Versuch (`[30, 60, 120, 300]`)? | **Umgesetzt (Stage 3, 2026-10-17):** `retry_policy.backoff_seconds` ist eine **Liste** (`number_list`, neue Feldart): ein Wert je weiterem Versuch, der letzte wiederholt sich. Ein gespeicherter Einzelwert bleibt gültig (`normalize` macht daraus eine einelementige Liste, der Worker liest ihn genauso). |
| Payload-Modus (Event vs. vollständiges Objekt)? | **Umgesetzt (Stage 3, 2026-10-17):** Feld `payload_mode` (`event` = wie bisher, `object` = Ereignis **plus** `object` aus `GET /objects/{id}`). Das Ereignis bleibt die Hülle, damit `event_id` weiter dedupliziert; ein gelöschtes Objekt (und ein `404` beim Lesen) geht als Ereignis allein hinaus, jeder andere Lesefehler wird wiederholt statt still die falsche Form zu senden. |
| `json_import`/`json_export` als Job-Defaults verdrahten? | **Umgesetzt (Stage 2b)** — der erste aktivierte Konnektor des Typs ist das Profil (Abschnitt 7, Punkt 3). |
| Unbekannte Schlüssel ablehnen? | **Umgesetzt (Stage 3, 2026-10-17):** Opt-in über die System-Einstellung `connector_strict_parameters` (Default aus). Mit ihr ist ein nicht deklarierter **Top-Level**-Schlüssel ein Fehler; der Inhalt deklarierter Objekte (`headers`, `json`-Felder) bleibt frei. Aus bleibt Altdaten gültig und der Hinweis im Formular. |
| Strukturierte Feldfehler (`{"error": …, "fields": {…}}`)? | **Umgesetzt (Stage 3, 2026-10-17):** jeder `400` der Parametervalidierung trägt zusätzlich `fields` als `{pfad: meldung}`; das Formular markiert die Widgets (`ParameterForm.mark_errors`) und löscht die Marke bei der nächsten Änderung. Die flache Meldung bleibt (Logs, KI-Prüfung). |
| Schema als JSON-Schema ausliefern (statt eigenem Format)? | Nein — das eigene, kleinere Format genügt (kein vollständiger Validator nötig) und bleibt ohne Zusatzabhängigkeit. Mehrfachauswahl (`string_list`) und Key-Wert-Tabellen sind in JSON Schema umständlicher als hier. |

## 14. Stage 3 — Ausbauoptionen (**umgesetzt 2026-10-17, bis auf die Plugin-Laufzeit**)

> **Status:** Umgesetzt sind 14.1 in der **Datenform** (registrierte Typen mit gespeichertem Schema, Admin-Seite, `wired: false`), 14.2 (eigene Secret-Ablage), 14.3 (MCF-Sektion `connectors`, ohne Secrets) und die Punkte aus 14.4 **außer** dem Verbindungstest für HTTP-Profile. Offen bleibt die **Plugin-Laufzeit** (`MEADOW_CONNECTOR_PLUGINS`): ohne sie liest kein Verbraucher die Parameter eines registrierten Typs, und genau das sagt die Oberfläche.
>
> Die Abschnitte 1–13 beschreiben den Stand nach Stage 1/2, mit den Nachträgen aus Stage 3.

### 14.1 Eigene Konnektor-Typen (Plugin-Registry) — **umgesetzt als Datenmodell**

**Warum:** Der Kern kannte drei Typen (`webhook`, `json_import`, `json_export`). Ein vierter (z. B. „SAP IDoc") verlangte einen Eingriff in **drei** Stellen: Schema im Kern (Rust), Verbraucher im Worker (Python), Labels in drei Locales.

**Umgesetzt (2026-10-17):**
- Tabelle **`connector_type`** (`name`, `label_key`, `direction`, `schema`); `schema` ist exakt das Format, das `GET /connector-types` liefert (`groups[].fields[]`).
- `GET /connector-types` liefert eingebaute **und** registrierte Typen. Damit dieselben Regeln für beide gelten, sind die Schema-Typen jetzt **owned** (`String` statt `&'static str`) und lassen sich aus JSON lesen (`TypeSpec: Deserialize`); der **`Catalog`** (eingebaute + registrierte) trägt `validate`, `normalize`, `apply_defaults`, `unknown_keys`, `split_secrets`, `resolve_secret_references` und `without_secrets` — ein Validator, ein Formular, ein Export.
- `POST`/`PATCH`/`DELETE /connector-types` (admin, `integration.manage`): Name nur `[a-z0-9_]` und **nie** ein eingebauter Name (der gewinnt), Schema wird vor dem Speichern geparst und auf doppelte Feld-Schlüssel geprüft, Löschen ist gesperrt, solange ein Konnektor den Typ nutzt (`409`).
- Admin-Seite **`/connector-types`** (`meadow/frontend/connector_types_page.py`): Liste, Neu/Bearbeiten mit JSON-Schema (Vorlage-Knopf), Löschen mit Rückfrage.
- Registrierte Typen sind **`wired: false`** mit `note_key = connectors.unwired_hint`; das vorhandene Formular zeigt dafür den Hinweis und den JSON-Tab.

**Offen (bewusst):** die **Plugin-Laufzeit** im Worker (`MEADOW_CONNECTOR_PLUGINS=<modul>` mit `build(config) -> Handler`) und die Zustellung der Events an einen solchen Handler. Bis dahin ist ein registrierter Typ eine gültige, prüfbare Konfiguration **ohne** Zustellung — das ist die ehrliche Zwischenstufe, nicht ein halbfertiger Verbraucher.

### 14.2 Getrennte Secret-Ablage — **umgesetzt**

**Warum:** Header-Token und Passwörter lagen als Klartext in `connector.parameters`; maskiert war nur die **Anzeige**. Ein Backup, eine Listenantwort oder ein MCF-Export trug den Token mit.

**Umgesetzt (2026-10-17):** `secret: true`-Felder werden beim **Schreiben** aus `parameters` herausgezogen (`connector_schema::split_secrets`); die Werte liegen in **`connector_secret:<key>`** (`values`), in `parameters` steht nur die Referenz `{"$secret": "connector:<key>#headers"}`. Regeln:
- Ein **eingehender** Wert wird gespeichert; eine **Referenz** in der Eingabe heißt „unverändert“ (das Formular kennt den Wert nicht, ein erneutes Speichern darf ihn nicht löschen); ein leerer Wert **löscht** Referenz und Ablage.

  **Formular (2026-10-17):** Ein Secret-Feld startet **leer** und meldet die Referenz zurück, solange nichts eingetippt wurde (`secret_reading`, die Basislinie `project_fields` trägt die Referenz) — ohne diese Basislinie hätte **jedes** Speichern eines Konnektors mit gespeichertem Token den Token gelöscht, weil das leere Widget wie eine Änderung aussah. Gelöscht wird deshalb ausdrücklich: unter dem Feld steht ein Kästchen **„Gespeicherten Wert löschen“** (`connector_form.secret_clear`, Marker `clear-secret-<pfad>`), dessen Haken `None` liefert und damit den Schlüssel entfernt. Live geprüft: Haken + Speichern ⇒ `headers` verschwindet aus `parameters` und aus der Maschinensicht; Timeout ändern und speichern ⇒ Referenz und Wert bleiben.
- Der Worker liest die Werte über `GET /connectors?resolve_secrets=true`; das ist **nur dem Maschinen-Aufrufer** erlaubt (`403` für eine Sitzung). Fehlt ein gespeicherter Wert, wird die Referenz **entfernt**, damit nie `{"$secret": …}` an eine Middleware geht.
- `DELETE /connectors/{id}` löscht den Secret-Record mit; das Audit meldet `secrets_changed` (Pfade, keine Werte).
- Das Formular zeigt für eine Referenz ein leeres Key-Wert-Feld (nichts zu sehen, nichts zu überschreiben); zum Entfernen löscht man den Schlüssel im JSON-Tab.

**Nicht** umgesetzt: Verschlüsselung im Ruhezustand (bewusst: nur über bestehende Infrastruktur/Datenbankrechte, keine eigene Krypto), „Wert prüfen“ ohne Anzeige.

### 14.3 Konnektor-Profile im MCF — **umgesetzt**

**Umgesetzt (2026-10-17):** neue Sektion **`connectors`** (`crates/pim-core/src/api/config/connectors.rs`) mit `name`, `type`, `enabled`, `parameters` — **ohne** Secrets (`without_secrets` streicht jedes `secret`-Feld). Der Import identifiziert über `name` (eindeutig), **mischt** die eingehenden Parameter über die lokalen (Secrets und lokale Zusätze bleiben) und meldet je Eintrag eine **Warnung**, wenn das Dokument Secret-Felder enthielt. `dry_run`/`merge`/`replace` wie bei den anderen Sektionen, inklusive Aufräumen der nicht im Dokument stehenden Konnektoren bei `replace`.

### 14.4 Kleinere Ausbauten — **umgesetzt 2026-10-17**

| Ausbau | Stand |
|---|---|
| **Test automatisch nach dem Speichern** | **Umgesetzt:** nach einem erfolgreichen Speichern eines **aktivierten** Konnektors läuft der Test im Hintergrund; gemeldet wird nur ein **Fehlschlag** (Warnung mit Grund), ein gesunder Konnektor bleibt still. |
| **Konnektor-Status in der Liste** | **Umgesetzt:** `POST /connectors/{id}/status` (Maschinen-Key oder Admin) schreibt `last_success_at` bzw. `last_error`/`last_error_at` (gekürzt); der Worker meldet **nur den Wechsel** (erster Fehler, erste Erholung), damit nicht jede Zustellung schreibt. Liste: Spalte **Zustellung** mit Badge (ok / Fehler mit Meldung im Tooltip / noch keine Zustellung). |
| **`number_list`-Felder** | **Umgesetzt:** neue Feldart in Schema (`FieldKind::NumberList`, `Widget::NumberList`), Validierung elementweise, Formular als kommagetrennte Liste, Altlast Einzelwert wird normalisiert. |
| **Payload-Modus** | **Umgesetzt:** `payload_mode` (`event`/`object`), Objekt-Abruf über den Kern (`fetch_object`), 404/`object.deleted` ⇒ Ereignis allein, andere Fehler ⇒ Wiederholung. |
| **Strukturierte Feldfehler** | **Umgesetzt:** `{"error": …, "fields": {pfad: meldung}}` und Feldmarkierung im Formular (siehe §13). |
| **`strict_parameters`** | **Umgesetzt:** System-Einstellung `connector_strict_parameters` (Default aus). |
| **Verbindungstest für HTTP-Profile** | **Offen** — der Test löst heute die Profilwerte auf (Trockenlauf der Werte), erzeugt aber keine Datei und ruft keinen Empfänger. |

**Nicht-Ziele (auch in Stage 3 ausgeschlossen):** Plugin-Laufzeit *im MVP* (siehe 14.1), neue Rollen, Ersatz der Import-/Export-Wizards, Migration bestehender Parameter (Altdaten bleiben gültig und sichtbar), eigene Krypto-Lösung für Secrets (Verschlüsselung nur über bestehende Infrastruktur/ENV).


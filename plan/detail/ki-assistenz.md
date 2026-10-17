# KI-Assistenz (Regeln, berechnete Attribute, Suche, Konfiguration)

- **Hauptdokument:** Abschnitt 8 ([Workflow, Freigaben & Business Rules](../meadow-pim-architektur.md#8-workflow-freigaben--business-rules)) – Business Rules und berechnete Attribute sind die ersten beiden Einsatzbereiche; außerdem Abschnitt 10 ([Suche & Collections](../meadow-pim-architektur.md#10-suche-collections--gespeicherte-suchen), Query-DSL), Abschnitt 11 ([Frontend](../meadow-pim-architektur.md#11-frontend)) und Abschnitt 12 ([Betrieb](../meadow-pim-architektur.md#12-betrieb--deployment), Provider-Konfiguration und Secret-Behandlung)
- **Relevante Phasen:** baut auf Phase 4 (Sandbox, `rules.compile`/`rules.evaluate`), Phase 7 (Rule Lab) und Phase 9 (schema-getriebene Formulare, Query-DSL); die Stufen 0–3 unten sind **neu** und noch nicht Teil der Phasen 1–9
- **Stand:** Stufen 0 **und** 1 umgesetzt (2026-09-19), **Stufe 2 vollständig umgesetzt** (Suche, Konnektoren, Export-Profile, Job-Definitionen, Import-Spalten-Mapping — letzteres 2026-09-21) und **Stufe 2b umgesetzt** (Gruppen-Rechte und Scope, 2026-09-27), Stufe 3 offen; die Machbarkeit ist am 2026-09-19 an der laufenden Dev-Instanz **gemessen** (Spike, Abschnitt 8)

## 0. Kurzfassung

Meadow bekommt eine **KI-Assistenz**, die Beschreibungen in die Artefakte übersetzt, die dieses System ohnehin schon kennt: **Regelcode** (Business Rules), **Regelcode + Abhängigkeiten** (berechnete Attribute), **Query-DSL** (Suche), **JSON-Parameter** (Konnektoren, Export-Profile, Jobs, Import-Mapping) und **Gruppen-Rechte samt Daten-Scope**. Sie ist eine **Schreibhilfe im Editor**: der Vorschlag landet im Feld, **nie automatisch in der Datenbank**. Alles, was die KI erzeugt, läuft durch **dieselben Prüfungen wie handgeschriebener Code** — Compile-Policy der Sandbox, AST-Vorprüfung, Dry-Run gegen ein echtes Objekt bzw. Query-Preview oder Schreibpfad-Prüfung im Kern.

Der Ansatz ist bewusst der **kleinste tragfähige**: kein Agenten-Framework, keine Kette von Werkzeugen, kein Speicher — ein Prompt aus den **Laufzeitquellen der Wahrheit**, ein Modellaufruf, eine Prüfung, optional **ein** Korrekturversuch.

**Umsetzungsstand:** Stufe 0 ist **fertig** (Abschnitt 9): `meadow/ai/` mit Politik, Vertrag, Projektion und Beispielen, das Vertrag-Panel im Regel-Editor und die generierte Referenz `docs/developer/rule-context.md`/`.json` samt Staleness-Check. Alles davon läuft **ohne Modell** und ist durch Tests und live im Browser belegt. **Stufe 1 ist ebenfalls fertig:** die `ai_*`-Einstellungen samt Secret-Maskierung im Kern, die Gruppe „KI-Assistenz“ auf `/system-settings` mit Passwortfeld und „Verbindung testen“, der Worker-Dienst `ai.generate`/`ai.test` mit Prompt, Provider-Aufruf, Prüfkette und einem Korrekturversuch sowie die Knöpfe im Regel-Editor und im Rechen-Bereich der Attributdefinition. Verified live gegen einen **lokalen Fake-Provider** (Abschnitt 9) und am 2026-09-21 gegen ein echtes Modell. **Stufe 2 ist fertig** (Suche, Konnektoren, Export-Profile, Job-Definitionen, Import-Spalten-Mapping — Abschnitt 9), ebenso der **Gruppen-Bereich** (Rechte und Scope, 2026-09-27); Stufe 3 ist offen.

## 1. Ausgangslage

Meadow hat genau die Bausteine, die eine solche Assistenz braucht — und zwar als **Maschinenlesbares**, nicht als Handbuchtext:

| Baustein | Wo | Warum er zählt |
| --- | --- | --- |
| Skript-Vertrag `run(obj, ctx, result)` | `meadow/rules/contract.py` | Ergebnis-Helfer und Kontextfelder lassen sich **erzeugen** statt abschreiben |
| Sandbox-Politik (Built-ins, Import-Whitelist, Limits) | `meadow/rules/sandbox.py`, `whitelist.py` | exakt das, was der generierte Code einhalten muss |
| Prüfung vor dem Speichern | NATS `rules.compile` (Kern ruft den Worker), `crates/pim-core/src/api/rules.rs` | ein KI-Vorschlag wird **wie jeder andere** geprüft |
| Ausführung gegen echte Daten | `POST /rules/{id}/dry-run`, Rule Lab | der Nutzer sieht das Ergebnis **vor** dem Speichern |
| Feldkatalog der Suche | `GET /search-fields` | die DSL ist schema-getrieben, der Katalog ist der Prompt-Kontext |
| Query-Prüfung ohne Speichern | `POST /saved-searches/preview` | read-only-Validierung eines KI-Entwurfs |
| Attribut-Metadaten | `GET /attribute-definitions/vocabulary` | Namen/Typen je Objekttyp, plus `computed`/`dependencies` |

Heute gibt es **keinerlei** KI-Bezug im Python-Baum (geprüft), und das ist die Wurzel des Aufwands: die Verbindung, der Kontextbau und die Einbettung in die Dialoge entstehen komplett neu.

## 2. Verifizierte Grundlagen (Messungen 2026-09-19, Dev-Instanz)

Alle Zahlen sind am laufenden System bzw. an der echten Sandbox gemessen, nicht geschätzt.

**Sandbox-Politik:**

- **101 Built-ins** sind in einer Regel sichtbar (aus `safe_builtins` + `limited_builtins` + `utility_builtins` + `meadow.rules.sandbox._EXTRA_BUILTINS`). Darunter `getattr` (der **abgesicherte** `safer_getattr`), `set`, `sorted`, `sum`, `enumerate`, `math`, `random` — aber **kein `hasattr`**.
- `hasattr`, `eval`, `open`, `globals` und Genossen sind **keine** Built-ins: sie bestehen `compile_restricted_exec` und scheitern erst **zur Laufzeit** mit `NameError`. Verbotene Imports und private Attribute (`__class__`) werden dagegen schon beim Kompilieren abgelehnt.
  → **Folgerung:** Die Prüfkette braucht eine **AST-Vorprüfung** für Namen, die die Sandbox nicht kennt; der Compile-Check allein reicht nicht.
- Heute werden **keine zusätzlichen Bindings** injiziert (`build_globals(bindings=None)` → keine Extra-Globals). Kommen später welche (z. B. Einheitenumrechnung), muss der Prompt-Kontext sie **zur Laufzeit** aus derselben Quelle lesen — deshalb wird er generiert, nicht gepflegt.

**Größe des Kontexts (der kritische Punkt):**

| Quelle | Roh | Kompakte Projektion für den Prompt |
| --- | --- | --- |
| Attribut-Vokabular | 17 463 Definitionen, **8,35 MB**; für `product` **17 443 zutreffend** (Namensliste 174 654 Zeichen ≈ 44 k Token) | **eigene** Attribute (`reference_data` ausgeschlossen): `product` 54 (1 107 Zeichen), `variant` 40, `vehicle_generation` 12 |
| Suchfeld-Katalog | 18 400 Felder, **6,63 MB** | eigene Felder als `key:value_type`: **92 Felder, 2 524 Zeichen** + eine Operator-Tabelle |
| Regeln | 26 Regeln, 12,7 KB, 3 957 Zeichen Skriptcode | gleichartige Regeln als Few-Shot: 2–3 Stück |
| Objekttypen / Einheiten / Konnektortypen | 14 Typen (4,8 KB), 74 Einheiten (22,7 KB), 3 Konnektortypen (5,6 KB) | nur die Namen bzw. der eine Typ |

**Ohne Projektion ist der Bereich nicht machbar** (ein Prompt mit allen Attributen oder allen Suchfeldern wäre zweistellige Zehntausende Token groß und würde die relevanten Namen verwässern). Die Projektion folgt der Regel, die die Oberfläche schon hat: *wo gewählt wird, stehen die eigenen Attribute* (`display.own_attribute_names`).

## 3. Architektur-Entscheidung

**Der LLM-Aufruf läuft im Worker, angesprochen über NATS** — genau wie `rules.compile` und `rules.evaluate` heute:

```
Frontend (NiceGUI)  --HTTP-->  Kern (Rust)  --NATS request/reply-->  Worker (Python)  --HTTPS-->  Provider
   Beschreibung, Modus           Auth, Audit,      AiGenerate-Request      Kontext bauen, LLM rufen,
   Vorschlag im Editor           Settings                                 AST+Compile prüfen, 1 Retry
```

Begründung (jeweils geprüft):

| Variante | Bewertung |
| --- | --- |
| **Worker über NATS (gewählt)** | Der Worker hat `httpx`, ist der etablierte Außen-I/O-Prozess (Importe, Exporte, Konnektoren, Webhooks) und **beherbergt die Sandbox** — das Wissen über Vertrag und Politik liegt dort schon. Der API-Key bleibt im Kern/Worker, erreicht weder Browser noch UI-Prozess. Fehlerbild wie bei den Regeln: `503`, wenn der Worker fehlt. |
| Frontend-Prozess direkt | Weniger Bausteine, aber: der dünne Sicht-Vertrag des Frontends bricht (es müsste `meadow/rules` importieren oder die Politik duplizieren — heute importiert es das Paket **nirgends**), und der Key läge im UI-Prozess. Verworfen. |
| Kern (Rust) ruft den Provider | Der Kern hat **keinen** HTTP-Client (kein `reqwest` in `crates/pim-core/Cargo.toml`) — das hieße eine neue Abhängigkeit plus Prompt-Bau in Rust und damit eine zweite Quelle der Wahrheit für Vertrag/Politik. Verworfen. |

**Kein LangGraph, keine LangChain.** Der Ablauf ist linear (Kontext → Aufruf → Prüfung → ggf. ein Retry); es gibt keine Werkzeugschleife, keinen Graphen, keinen Zustand zwischen Anfragen. Genau so ist auch im Vorgängerprojekt die tragfähige Vorstufe gebaut (OpenAI-kompatibles Chat-Completions-Protokoll per HTTP, bewusst ohne Provider-Bibliothek). Ein Agenten-Framework würde eine schwere Abhängigkeitskette (LangChain-Core, Provider-Adapter) für einen einzelnen POST-Aufruf einführen. Sollte später einmal eine **Graph-Generierung** anstehen (Meadow hat keine Workflow-Graphen, aber die Query-DSL und MCF-Dokumente als strukturierte Artefakte), ist das eine eigene Entscheidung — die Assistenz hier braucht sie nicht.

**Provider-neutral:** der Worker spricht das **OpenAI-kompatible Chat-Completions-Protokoll**; damit sind Azure OpenAI, OpenAI, DeepSeek und lokale Ollama-Instanzen ohne Codeänderung nutzbar (nur Endpoint/Modell unterscheiden sich).

## 4. Konfiguration und Secret-Behandlung

Meadow ist **Einzelmandant** — anders als im Vorgängerprojekt (dort eine Konfiguration pro Kunde) genügt **eine** globale Konfiguration in der vorhandenen Settings-Registry (`code default < environment < database`, Admin-Seite `/system-settings` existiert bereits).

| Schlüssel | Typ | Default | Bedeutung |
| --- | --- | --- | --- |
| `ai_enabled` | bool | `false` | **Opt-in**; solange falsch, ist die Funktion im UI unsichtbar |
| `ai_provider` | enum | `generic` | `generic` \| `openai` \| `azure_openai` \| `ollama` (steuert Endpoint-Aufbau/Header) |
| `ai_endpoint` | string | `""` | Basis-URL, Pflicht für `generic`/`azure_openai`/`ollama` |
| `ai_model` | string | `""` | Modell- bzw. Deployment-Name |
| `ai_api_key` | **secret** | `""` | Zugangsschlüssel — **maskiert** in jeder Antwort |
| `ai_temperature` | number | `0.2` | niedrig halten (Codegenerierung) |
| `ai_max_output_tokens` | int | `2048` | Antwortbudget |
| `ai_timeout_ms` | int | `30000` | Aufruf-Timeout |
| `ai_max_requests_per_hour` | int | `60` | Kostenbremse pro Instanz (0 = unbegrenzt) |

Umgebungsvariablen (`AI_ENABLED`, `AI_PROVIDER`, `AI_ENDPOINT`, `AI_MODEL`, `AI_API_KEY`, …) haben **Vorrang** vor dem DB-Wert — dieselbe Auflösung wie bei den vorhandenen Einstellungen.

### 4.1 Konfiguration in der Oberfläche (vorgesehen — Admin)

**Ja, die Konfiguration erfolgt im UI**, auf der vorhandenen Admin-Seite **`/system-settings`** (`meadow/frontend/settings.py`), in einer neuen Gruppe **„AI"** neben „Logging" und „Rules & Worker". Die Seite kann heute schon Werte anzeigen (`GET /system/settings?include_source=true` mit Herkunfts-Badge `default`/`env`/`db`), speichern (`PUT /system/settings/{key}`) und zurücksetzen (`DELETE /system/settings/{key}`) — es fehlen genau drei Dinge, alle klein:

| Schlüssel | Bedienelement im UI | Anmerkung |
| --- | --- | --- |
| `ai_enabled` | Schalter | Opt-in; solange aus, sind die KI-Knöpfe unsichtbar |
| `ai_provider` | Auswahlfeld | `generic` \| `openai` \| `azure_openai` \| `ollama` |
| `ai_endpoint` | Textfeld | Pflicht bei `generic`/`azure_openai`/`ollama`, z. B. `https://api.deepseek.com/v1` |
| `ai_model` | Textfeld | Modell- bzw. Deployment-Name |
| `ai_api_key` | **Passwortfeld** | **write-only**: nie im Klartext zurückgegeben; die Seite zeigt „gesetzt"/„nicht gesetzt" |
| `ai_temperature` | Zahlenfeld | 0–2, Vorgabe 0,2 |
| `ai_max_output_tokens` | Zahlenfeld | Vorgabe 2048 |
| `ai_timeout_ms` | Zahlenfeld | Vorgabe 30 000 |
| `ai_max_requests_per_hour` | Zahlenfeld | 0 = unbegrenzt |
| — | Knopf **„Verbindung testen"** | `POST /ai/test` → NATS → Worker schickt einen Minimal-Prompt an den Provider und meldet Ergebnis/Latenz |

**Was dafür neu ist:**

1. **Kern: Secret-Behandlung in der Settings-Registry.** Die Registry ist eine geschlossene Liste (`default_settings()`, `env_setting()`, `validate_setting()`, `is_known_setting()`); `ai_*` kommt dazu. Neu ist eine Markierung `secret`: `resolve_settings*` liefert für Secret-Schlüssel **nie** den Wert, sondern die Form `{"value": null, "configured": true, "secret": true, "source": "db"}` — damit kann die Oberfläche „gesetzt" anzeigen und die KI-Knöpfe einblenden, ohne den Schlüssel je zu sehen. `PUT /system/settings/ai_api_key` nimmt den echten Wert entgegen (write-only), `DELETE` setzt zurück.
2. **Frontend: zwei Editor-Typen mehr.** Die Seite kennt heute nur Auswahl/Schalter/Zahl (`_make_card`); ein **Textfeld** und ein **Passwortfeld** sowie die neue Gruppe „AI" kommen dazu — inklusive des Knopfs „Verbindung testen".
3. **Herkunft sichtbar machen (Falle vermeiden).** Ist `AI_API_KEY` in der **Umgebung** gesetzt, gewinnt sie gegen den DB-Wert — das Feld wäre dann wirkungslos. Deshalb zeigt die Karte (wie heute bei allen Einstellungen) den Badge `env` und **sperrt das Eingabefeld** mit dem Hinweis, dass die Umgebung Vorrang hat.

**Bewertung/Randbedingungen:**

- **Möglich und konsistent:** Auth (Admin-Rolle wie bei allen anderen Einstellungen), Audit (`PUT` schreibt heute schon `system_setting_updated`) und die Verteilung an den Worker sind vorhanden bzw. geplant.
- **Wer darf was:** Konfigurieren nur `admin`/`superadmin` (die Seite prüft das bereits); *benutzen* dürfen es Rollen mit `business_rule.manage` bzw. `attribute_definition.manage` (`ai.use` ist im Katalog `admin`).
- **Ehrliche Grenze:** In der Datenbank liegt der Schlüssel im Klartext (wie heute die Zugangsdaten der Konnektoren) — maskiert wird die **Ausgabe**, nicht die Ablage; die DB ist die Schutzgrenze. Wem das nicht reicht, konfiguriert **nur** per Umgebungsvariable (`AI_API_KEY`); die UI zeigt den Schlüssel dann als „aus Umgebung gesetzt" an und lässt ihn nicht überschreiben.
- **Transport:** Browser → Kern und Kern → Worker laufen über die internen, authentifizierten Wege; der echte Wert geht nie an den Browser zurück und nie in einen Prompt.

**Secret-Regel (kleine Kern-Ergänzung, Muster `secret: true` aus `connector_schema.rs`):** Die Settings-Registry markiert `ai_api_key` als Secret. `GET /system/settings` liefert dafür **nie** den Wert, sondern die Maskierung plus `configured`/`source` (Abschnitt 4.1); `PUT /system/settings/ai_api_key` schreibt ihn. Der echte Wert verlässt den Kern nur in der **NATS-Anfrage an den Worker** (internes Netz) — nie Richtung Browser oder UI-Prozess. Der Worker hält ihn nicht fest, er benutzt ihn pro Aufruf.

## 5. Kontextbau je Bereich

Der Kontext entsteht **im Worker aus den Laufzeitquellen** (`meadow.rules.*` für Politik/Vertrag, Kern-API für Datenmetadaten) — nicht aus gepflegten Texten. Zwei Blöcke sind für alle Bereiche gleich:

1. **Politik-Block:** verfügbare Built-ins, importierbare Module, Verbotsliste, Limits — erzeugt aus der echten Sandbox.
2. **Vertrags-Block:** `run(obj, ctx, result)`, die `result`-Helfer, die `ctx`-Felder — erzeugt aus `meadow/rules/contract.py`.

Dazu je Bereich ein Aufgaben-Block und 2–3 Few-Shot-Beispiele (die ausgelieferten Demo-Regeln aus `scripts/demo_data_catalog.json` sind dafür geprüft und passen zur Politik).

| Bereich | Zusätzlicher Kontext | Prüfung des Ergebnisses | Ausgabe |
| --- | --- | --- | --- |
| **Business Rule** | Regeltyp, `applies_to`-Objekttyp, dessen **eigene** Attribute (`name:type`), gleichartige aktive Regeln als Beispiele, bei „ändern" das aktuelle Skript als Baseline | AST-Vorprüfung → `compile_rule` (NATS-Policy) → optional Dry-Run | vollständiger `def run(...)`-Block |
| **Berechnetes Attribut** | wie oben, zusätzlich: Zielattribut **mit Typ** und die **deklarierten** berechneten Attribute des Typs (nur diese darf `set_value` schreiben) | wie oben, plus Abgleich der `set_value`-Zielfelder gegen die Deklaration | Skript **+** Regelname **+** `dependencies` **+** `computed_reads_inherited` **+** Beschreibungstext |
| **Suche (Query-DSL)** | DSL-Kurzspezifikation (Struktur, Grenzen, „fehlende Werte sind keine Treffer"), Feldliste `key:value_type`, Operator-Tabelle mit `arity`/`multi`, Basisgrenzen | `json.loads` → `POST /saved-searches/preview` (Kern validiert Feld, Operator, Wertigkeit, Tiefe) | DSL-JSON |
| **JSON-Parameter** (Konnektor, Export-Profil, Job) | das Feld-Schema des jeweiligen Typs (`GET /connector-types` je Typ; `GET /export-profiles/schema`; `GET /jobs/schema` je Jobtyp) | Kern-Validierung ohne Speichern (`POST /connectors/validate`, `/export-profiles/validate`, `/jobs/validate`); MCF-Import zusätzlich mit `?dry_run=true` | JSON-Objekt |
| **Import-Spalten-Mapping** | Zielattribute des Importtyps + Spaltenköpfe **und wertfreie Form** der Datei (`tabular.describe_columns`), Business-Key und Label-Attribut des Typs, Datensprachen | `tabular.check_mapping` (dasselbe Modul, das die Zuordnung ausführt) gegen Datei und Modell; eine MEF-Vorschau im Kern bleibt offen | Zuordnung Spalte → Ziel |
| **Gruppen-Rechte und Scope** (`group_permissions`) | Rechte-Katalog (`GET /permissions`: Schlüssel, Bereich, Aktion, **Rollen-Default**, englische Beschreibung), die Typ-Schlüssel des Objekttyp-Registers und die **Workflow-Zustände**, die die Scope-Karte anbietet (sie reisen mit der Anfrage, wie `declared_computed`); im `edit`-Modus der Stand auf dem Bildschirm | `POST /groups/validate` — die zwei Prüfungen des **Schreibpfads** (`reject_unknown_permissions` + `check_scope` = `scope::parse` + `scope::validate`) mit denselben Meldungen wie ein abgelehntes Speichern | `{grant_permissions, deny_permissions, scope:{rules}}` + optional `note` |

## 6. Prüfkette und Sicherheitsregeln

Die KI ist eine **Quelle wie jeder Mensch am Editor** — sie bekommt keine Sonderrechte:

1. **Nie automatisch speichern.** Der Vorschlag wird per `set_value(...)` in den Editor geschrieben; der Nutzer prüft und speichert.
2. **AST-Vorprüfung** (neu, `meadow/ai/policy.py`): genau **eine** Funktion `run`, Signatur `(obj, ctx, result)`, keine Namen aus der Verbotsliste (`hasattr`, `eval`, `exec`, `open`, …). Diese Prüfung fängt genau die Klasse ab, die der Compile-Check **nicht** sieht (gemessen, Abschnitt 2).
3. **Compile-Policy** über `meadow.rules.compile_rule` — dasselbe, was `POST /rules` beim Speichern prüft (Imports, private Attribute, Größe).
4. **Ausführung/Dry-Run** dort, wo es ihn schon gibt: Rule Lab gegen ein echtes Objekt, `POST /saved-searches/preview` für Suchen, Import-Vorschau für Mappings. Der Nutzer sieht das Ergebnis vor dem Speichern.
5. **Ein Selbstkorrektur-Versuch:** schlägt Schritt 2–3 fehl, geht die Fehlermeldung **einmal** zurück an das Modell; danach ist Schluss und der Nutzer sieht den Fehler. Kein Endlos-Loop.
6. **Fail-closed wie bei den Regeln:** ist `ai_enabled=false`, fehlt der Key oder ist der Worker nicht erreichbar, gibt es eine klare Meldung (`503`/Hinweis) — **keine** stille Ersatzfunktion und **kein** Einfluss auf Speicherpfade.
7. **Audit:** jeder Aufruf wird als `audit_log`-Eintrag (`ai_generated`) mit Bereich, **Ziel** (`target`: die Identität des bearbeiteten Artefakts), Modell, Versuchen, Latenz und Tokenzahl festgehalten — **ohne** Prompt, ohne Skript und ohne Objektwerte. Damit ist nachvollziehbar, woher ein Skript kam, und **`GET /ai/history?area=…&target=…`** liefert genau diese Einträge (neueste zuerst, nur Metadaten) — ein eigener Speicher für die Vorschlags-Historie ist damit überflüssig.
8. **Datenminimierung:** in den Prompt gehen **Schema und Metadaten** (Attributnamen, Typen, Status) — **keine Objektwerte**. Wer Objektwerte braucht, nutzt den Dry-Run im Haus (Regeln), nicht das Modell.

## 7. Bedienung

- **Stufe 0 (ohne KI, sofort nützlich):** ein Panel neben dem Regel-Editor mit **Vertrag und Politik** (Globals, `result`-Helfer, `ctx`-Felder, erlaubte Imports, Limits, eigene Attribute des Objekttyps). Es ist gleichzeitig die sichtbare Prüfung, dass der generierte Kontext stimmt — und der Spickzettel, den man heute im Handbuch sucht.
- **Regel-Editor (Rule Lab):** Knopf „Vorschlag erzeugen" mit Modus **Neu** / **Bestehendes ändern** / **Erklären**; Beschreibungsfeld, Ergebnis im CodeMirror-Feld, Hinweiszeile mit Prüfergebnis (`policy ok`, Fehlerliste) und Verweis auf den Dry-Run.
- **Berechnetes Attribut (Attributdefinition):** im Rechen-Bereich des Dialogs derselbe Knopf; die KI füllt zusätzlich **Regelname**, **`dependencies`** und `computed_reads_inherited` vor (die Felder existieren dort bereits).
- **Suche (Suchformular/gespeicherte Suche):** „Suchbeschreibung → DSL", Ergebnis im JSON-Tab, Prüfung über die Vorschau (Trefferzahl erscheint sofort).
- **Gruppen-Editor (`/users`, Abschnitt Gruppe):** „Vorschlag erzeugen" neben der Rechte-Matrix mit den Modi **Neu** und **Verbessern** (kein „Erklären": eine Gruppe hat kein Prosa-Feld). Der Vorschlag füllt die Matrix und die **Typ-/Status-Regeln** der Scope-Karte; Kategorie- und Collection-Regeln bleiben unverändert, weil eine Referenz-Regel Record-Ids benennt, die kein Prompt kennen darf. Der Dialog zeigt den Vorschlag als JSON **vor** dem Übernehmen, der Zähler der Karte antwortet danach mit der Größe der vorgeschlagenen Sicht, und gespeichert wird erst über „Speichern".
- **„Erklären"** (Gegenrichtung Code → Klartext) füllt das vorhandene `description`-Feld von Regel/Attribut — nützlich für Handbuch, Audit und Übergabe; dort greift auch die Regel „keine Fremdprodukt-Namen in der Oberfläche".
- **Sichtbarkeit:** alle Knöpfe nur bei `ai_enabled` **und** konfiguriertem Provider (die Oberfläche erfährt über `configured`/`source`, ob ein Key hinterlegt ist, ohne ihn zu sehen) und passender Rolle (im Katalog `ai.use`, Vorgabe `admin`). Die **Konfiguration** selbst liegt auf `/system-settings` und ist wie diese Seite **admin-only** (Abschnitt 4.1).

## 8. Spike-Ergebnisse (2026-09-19, echte Modellaufrufe)

Gefahren wurde die komplette geplante Kette mit `deepseek-chat` (OpenAI-kompatibel) gegen die laufende Dev-Instanz: Kontext aus den echten Quellen, Modellaufruf, AST-Prüfung, `compile_rule`, Ausführung gegen ein synthetisches Objekt, Query-Preview im Kern.

| Szenario | Prompt | Latenz | Token | Ergebnis |
| --- | --- | --- | --- | --- |
| Berechnetes Attribut (neu) | 5 189 Zeichen (≈1 297 Token) | 1,94 s | 1 531 | **policy ok**, Dry-Run schreibt `gross_price` |
| Validierungsregel (neu) | 4 994 | 2,06 s | ~1 500 | **policy ok** |
| Berechnetes Attribut (**ändern**, Baseline = ausgelieferte Regel) | 5 409 | 1,62 s | 1 579 | **policy ok**, Dry-Run `gross_price = 119.0` |
| Provokation: „nutze bitte unbedingt `hasattr`" | 4 993 | 1,39 s | 1 498 | **policy ok** — das Modell wich selbst auf `"name" in attributes` aus |
| Suche (Query-DSL) | 4 947 | 1,61 s | 1 472 | **`POST /saved-searches/preview` akzeptiert** (1 000 Treffer) |

**Ergebnis:** 5 von 5 Vorschlägen bestanden die Prüfkette im **ersten** Versuch; der Selbstkorrektur-Loop kam nicht zum Einsatz (bleibt als Netz). Ein Aufruf kostet ~1,3–1,6 k Token.

**Zwei Befunde aus dem Spike (beide schon eingearbeitet):**

1. **System-Prompt ist bereichsspezifisch.** Mit dem *Regel*-System-Prompt bat das Modell für eine **Suchanfrage** brav `def run(obj, ctx, result)` an (FAIL). Erst mit einem eigenen Such-Prompt kam reines JSON. → `meadow/ai/prompts.py` braucht **einen System-Prompt je Bereich**, und der Bereich muss im Kern-Endpunkt explizit sein.
2. **Roh-Endpunkte sind als Kontext unbrauchbar.** Vokabular (8,35 MB) und Suchfeld-Katalog (6,63 MB) müssen projiziert werden (eigene Felder, kompakte Feldschreibweise, eine Operator-Tabelle) — sonst sprengt schon `product` allein das Budget (174 654 Zeichen für Namenslisten).

**Nicht verifiziert** (offen): Verhalten bei sehr großen Typen mit vielen eigenen Attributen, Nicht-DeepSeek-Provider (Azure/Ollama), Import-Mapping-End-to-End, Verhalten bei Modellausfall während eines Aufrufs (Timeout-Pfad ist implementiert, aber nicht ausgelöst), sowie die Wirkung der Assistenz bei echten Nutzern.

## 9. Stufenplan

**Stufe 0 — Vertrag sichtbar machen (kein Modell, kein Provider).** ✅ **umgesetzt 2026-09-19.** `meadow/ai/policy.py` (Politik aus der laufenden Sandbox, `check_script` mit AST-Vorprüfung + Compile-Politik) und `meadow/ai/context.py` (Vertragsblock mit **per `inspect` erzeugten** Signaturen, Politikblock, Projektion `attribute_facts`) plus `meadow/ai/examples.py` (kuratierte Beispielregeln, im Paket — das Frontend-Image kopiert `scripts/` nicht). Dazu das **Vertrag-Panel** im Regel-Editor („Vertrag & Politik“, `ui.expansion`, lazy beim ersten Aufklappen: die Attributliste braucht das Vokabular) und `scripts/ai_context.py`, das `docs/developer/rule-context.md` + `.json` **erzeugt**; `--check` (und ein Test) schlägt fehl, sobald die Dateien veralten. Live geprüft: Panel öffnet, zeigt Vertrag/Politik/Importe/Grenzen, ohne Objekttyp den Hinweis, mit `product` **54 eigene Attribute** und **kein** `EF…`-Merkmal.

**Stufe 1 — Business Rules + berechnete Attribute (Kern der Sache).** ✅ **umgesetzt 2026-09-19.**

- **Kern** (`crates/pim-core/src/api/system.rs`, `ai.rs`, `api/ai.rs`): neun `ai_*`-Schlüssel in der Registry (Default, `AI_*`-Umgebungsvariablen mit Vorrang, Validierung); `ai_api_key` ist als Secret markiert und erscheint in **keiner** Antwort — die detaillierte Form liefert `{value: null, configured, secret: true, source}`, die flache Form `null`. `AiClient` schickt die **aufgelöste** Konfiguration inklusive Schlüssel im NATS-Auftrag (der Schlüssel verlässt den Kern nur dorthin). `POST /ai/generate` prüft Bereich/Modus/Beschreibung, ruft den Worker, schreibt **einen** `ai_generated`-Audit-Eintrag (ohne Prompt, ohne Skript) und antwortet `503`, sobald `ai_enabled` falsch ist, die Konfiguration unvollständig ist oder der Worker fehlt.
- **Worker** (`meadow/worker/ai_service.py`, `meadow/ai/{prompts,provider,service}.py`): Subjects `ai.generate`/`ai.test`; Vokabular-Abruf mit 300-s-Cache (die Projektion braucht es, 8 MB pro Aufruf wären unsinnig); **ein System-Prompt je Bereich** (Lehre aus §8); OpenAI-kompatibler Aufruf per `httpx` (generic/openai/ollama → `/chat/completions`, Azure → Deployment-Pfad + `api-key`-Header); Prüfkette AST + Compile-Politik; danach **genau ein** Korrekturversuch, dann `ok: false` mit der Fehlerliste. Stundenbremse (`ai_max_requests_per_hour`) im Worker.
- **Frontend**: Gruppe „KI-Assistenz“ auf `/system-settings` (Schalter, Anbieter-Auswahl, Text-/Passwortfeld, Zahlenfelder, **„Verbindung testen“**); ein Feld mit Herkunft `env` ist **gesperrt** statt wirkungslos; das Schlüsselfeld ist write-only (leer = unverändert, die Karte meldet „gesetzt“/„nicht gesetzt“). Der gemeinsame Dialog `meadow/frontend/ai_assist.py` sitzt im Regel-Editor (Neu/Ändern/Erklären; füllt Skript, Name und Beschreibung nur, wenn leer) und im Rechen-Bereich der Attributdefinition (füllt `dependencies`, `computed_reads_inherited` und — falls die Regel schon existiert — den Regelnahmen; das Skript bleibt sichtbar, weil die Regel auf der Regelseite entsteht). Ist die Assistenz aus oder unkonfiguriert, sagen die Editoren **warum**, statt den Knopf zu verstecken.
- **Nachweis (2026-09-19, laufende Dev-Instanz):** 21 neue Python-Tests (`tests/test_ai_provider.py`, `tests/test_ai_service.py`) und 2 neue Rust-Tests; live geprüft: Maskierung (kein Schlüssel in der Antwort), Validierung, `503` bei `ai_enabled=false`, `400` ohne Beschreibung; **die ganze Kette** Browser → Kern → NATS → Worker → Provider gegen einen **lokalen Fake-Provider** (16 Prüfungen: gültiger Vorschlag, Prompt enthält Projektion und keinen Standard-Treffer, Korrekturversuch nach `hasattr`, Abbruch nach dem zweiten Fehlversuch, „Erklären“, Verbindungstest) sowie 13 Browser-Prüfungen (Playwright).
- **Nebenbefund und behoben:** `PUT /system/settings/{key}` schrieb dasselbe SurrealDB-Record zweimal in einer Transaktion (`UPSERT … ; UPDATE …;`) und antwortete deshalb beim **zweiten** Speichern *jeder* Einstellung mit `409 concurrent write conflict`. Jetzt ein einziges `UPSERT … SET … RETURN AFTER` (`crates/pim-core/src/api/system.rs`) — ohne die neue KI-Seite, auf der neun Werte nacheinander gespeichert werden, wäre das nicht aufgefallen.
- **Offen aus dieser Stufe:** der Feinabgleich der Prompts an weiteren echten Antworten; geprüft ist bisher ein Lauf gegen `deepseek-chat`/`deepseek-flash`.
- **Erster echter Modelllauf (2026-09-19, `deepseek-chat` über `generic`):** `/ai/test` ok (1,5 s); berechnetes Merkmal ok im **ersten** Versuch (1,7–1,9 s, ~1 700 Token, `prompt_cache_hit_tokens` 1 024–1 408 — der statische Teil des Prompts liegt im Cache); Validierungsregel ok im ersten Versuch, gespeichert (`201`) und **im Rule Lab trocken gelaufen** (schlechte SKU → Fehler, gute SKU → keine); „Erklären“ liefert deutsche Prosa. Zwei Fehler kamen dabei ans Licht, beide behoben: (1) `RulesClient` serialisierte `missing_features: None` als `null`, woran der Worker **jede** Rule-Lab-Anfrage ablehnte (`Input should be a valid array`) — der Schreibpfad lief weiter, weil er immer eine Liste mitgibt; das Payload ist jetzt eine eigene, getestete Funktion. (2) Das Modell benutzte `re.fullmatch(…)` **ohne** `import re`: die Prüfkette ließ es durch und der Dry-Run meldete `name 're' is not defined` — `check_script` lehnt solche Skripte jetzt ab und nennt den fehlenden Import (dieselbe „läuft nie“-Klasse wie `hasattr`), der Politik-Block sagt es und die SKU-Beispielregel zeigt es.

**Stufe 2 — Suche und JSON-Parameter.** ✅ **Umgesetzt** (2026-09-19/21): Suche, Konnektor-Konfiguration, Export-Profil, Job-Definitionen und das Import-Spalten-Mapping.

- **Umgesetzt (Suche):** `area = "search"`; Kontext aus der Projektion von `GET /search-fields` (eigene Felder, gruppiert nach Operator-Satz: 92 Felder/3 075 Zeichen statt 18 400 Felder/6,54 MB), Operator-Tabelle und Grenzen in Worten; ein **Such-System-Prompt** (nur JSON, DSL-Form, „fehlender Wert ist kein Treffer“) und ein eigener Erklär-Prompt; Prüfung durch **den Kern** (`POST /saved-searches/preview` mit dem Maschinen-Key) mit einem Korrekturversuch; Knopf „Suchbeschreibung → DSL“ im JSON-Tab des Sucheditors, der den Vorschlag ins Feld schreibt und die Trefferzahl der Prüfung anzeigt. **Live gegen DeepSeek geprüft:** drei Beschreibungen (Zeitraum + Status + Sortierung, „GTIN fehlt“, „nur die ersten 50“) ergaben im **ersten** Versuch gültige DSL (1,6–1,9 s, ~1,5 k Token), die der Kern akzeptierte; die Suche ließ sich speichern (`201`) und ausführen. Dazu fünf Browser-Prüfungen (Playwright).
- **Umgesetzt (Konnektor-Konfiguration, 2026-09-19):** `area = "connector"` mit `config_kind` = Konnektortyp. Kontext ist die Projektion des Typschemas aus `GET /connector-types` (Gruppen abgeflacht, Felder mit Typ, Pflichtfeld, Enum, Bereich, Default, `show_if`, `secret`) plus die **englischen UI-Texte** zu `label_key`/`help_key` — die Worte liegen in den Frontend-Übersetzungen, die im selben Paket liegen, also gibt es eine Quelle und der Prompt spricht die Sprache des Formulars. Geprüft wird mit dem neuen `POST /connectors/validate` (Maschinen-Key oder Admin-JWT), das genau den Schreibpfad ausführt (Normalisierung + Defaults + `connector_schema::validate`), ohne zu speichern. Knopf im JSON-Tab des Konnektor-Editors. **Live gegen DeepSeek:** Validator lehnt fehlende `url` ab; drei Beschreibungen (Webhook mit Retry/Coalesce, CSV-Export, Import mit `match_by_key`) ergaben im ersten Versuch schema-konforme Konfigurationen (1,1–1,2 s, 350–650 Token), eine ließ sich speichern (`201`); „Erklären“ auf Deutsch. Dazu vier Browser-Prüfungen.
- **Umgesetzt (Export-Profil, 2026-09-19):** `area = "export_profile"`; das Artefakt ist `{format, parameters}`. Der Kontext kommt aus dem neuen `GET /export-profiles/schema` — Formate plus Parametername mit **Shape und Bedeutung**, direkt neben den Konstanten, die der Kern prüft (kein abgeschriebener Text). Geprüft wird mit `POST /export-profiles/validate` (`validate_format` + `normalize_parameters`) inklusive unbekannter Schlüssel (die der Kern bewusst ablehnt, statt sie zu schlucken). Knopf im Export-Profil-Editor. **Live gegen DeepSeek:** drei Beschreibungen (CSV mit Semikolon ohne Kopfzeile, MEF mit vererbten Werten, BMEcat 2005) ergaben im ersten Versuch gültige Profile (0,7–1,0 s, ~400 Token), jedes vom Kern akzeptiert; eines ließ sich speichern (`201`).
- **Umgesetzt (Job-Definitionen, 2026-09-21):** Voraussetzung war der **fehlende maschinenlesbare Vertrag**: eine `job_definition` speichert freies JSON, nur die Konsumenten kannten seinen Inhalt. Neues Kern-Modul `crates/pim-core/src/api/job_schema.rs` (Muster `connector_schema.rs`) beschreibt alle sieben Jobtypen mit ihren Parametern (Key, Shape, Pflichtfeld, Bedeutung, erlaubte Werte) und für `bulk_operation` die **14 Operationen** mit ihren eigenen Parametern; ausgeliefert über `GET /jobs/schema` (Editor oder Maschinen-Key). `area = "job"` mit `config_kind` = Jobtyp; der Kern lehnt einen unbekannten Jobtyp schon vor dem Modellaufruf mit `400` ab. Geprüft wird mit dem neuen `POST /jobs/validate`, das Parameter **und** den Cron-Ausdruck prüft (mit dem Parser des Schedulers, sechs Felder). Knopf im Definition-Dialog; der Vorschlag füllt Zeitplan und Parameterfeld.
  **Nachgezogen (2026-10-16):** der Endpunkt ist **der Schreibpfad** — `POST`/`PATCH /jobs` prüfen dieselbe Funktion (`job_schema::problems_for_write`): ein deklariertes Feld, das fehlt oder die falsche Form hat, ⇒ `400`; **unbekannte** Schlüssel werden gespeichert und gemeldet (`notices` in der Antwort des Prüfendpunkts, dazu ein `WARN`-Eintrag der Log-Kategorie `job`). Die Assistenz bleibt **strenger als ein Mensch**: ein Vorschlag mit erfundenem Schlüssel fällt durch, weil ein Modell damit rät (`meadow/worker/ai_service.py::validate_job_definition`).
  **Live gegen DeepSeek:** `bulk_operation` aus einer deutschen Beschreibung (korrekte Operation, Attribut und Sprach-Map, `send_for_approval`), `inbound_import` mit Zeitplan (`0 30 2 * * *`), `outbound_export` als BMEcat (strukturierte `source`), „Erklären“ auf Deutsch, unbekannter Jobtyp → `400`. Der Vertrag deckt genau die Felder ab, die Kern **und** Worker lesen (Producer und Consumer gegengelesen); ein Rust-Test hält fest, dass jeder Jobtyp genau einen Eintrag hat.
- **Umgesetzt (Import-Spalten-Mapping, 2026-09-21):** `area = "import_mapping"`; das Artefakt ist `{"mapping": {"<spalte>": "<ziel>"}}`. Voraussetzung war die **Prüfung dort, wo die Zuordnung ausgeführt wird**: `meadow/tabular.py` bekam `describe_columns` (Art, Länge, leere Zeilen, Eindeutigkeit — **kein Zellwert**) und `check_mapping` (Fehler: unbekannte Spalte, zwei Spalten auf dasselbe Ziel, unbekannte Sprache, Sprache auf `id`/`type`/`status`, gar nichts zugeordnet, kein Objekttyp; Hinweise: Ziel ohne Attributdefinition, mehrsprachiges Merkmal ohne Sprache, Spalte ohne Kopfzeile, Business-Key von keiner Spalte gefüllt). Der Worker holt dafür Vokabular, Objekttypen und Sprachen aus dem Kern (zwei neue Caches) und gibt die Spalten aus dem Auftrag weiter — **die Datei selbst verlässt den Browser nicht**. Der Kern kennt den Bereich und verlangt `columns`, damit kein Modellaufruf ohne Dateikontext bezahlt wird. Knopf „Zuordnung vorschlagen“ über der Mapping-Zeile der Importseite. **24 neue Python-Tests** (16 für `tabular`, 4 Kontext, 4 Service) und 2 Rust-Tests.
- **Umgesetzt (Gruppen-Rechte und Scope, 2026-09-27):** `area = "group_permissions"`; das Artefakt ist das Dokument der Gruppe (`grant_permissions`, `deny_permissions`, `scope.rules`) — der Zustand, den die Rechte-Matrix und die Scope-Karte ohnehin zeigen. Geprüft wird mit dem neuen `POST /groups/validate`, das **genau die Prüfungen des Schreibpfads** ausführt (Katalog + `scope::parse`/`scope::validate`) und dieselben Meldungen liefert (`unknown permission(s): … — the catalog is at GET /permissions`, `unknown relation …`, `unknown scope target(s): …`, `invalid scope: …`) — die grüne Antwort heißt deshalb „so würde das Speichern durchgehen" und nicht bloß „passt zum Vertrag". Der Prompt enthält den Rechte-Katalog **mit den Rollen-Defaults** (die Listen sind Abweichungen von der Rolle), die Typ-Schlüssel des Registers und die Workflow-Zustände, die die Scope-Karte anbietet (sie reisen mit der Anfrage, wie `declared_computed` — so kann ein Vorschlag keinen Zustand nennen, den die Karte nicht anzeigen kann). **Zwei bewusste Grenzen:** eine **Referenz**-Regel (Kategorie, Collection) benennt Record-Ids, die in keinen Prompt dürfen — der Vorschlag schreibt keine, und die Karte lässt die vorhandenen unverändert (ersetzt werden nur `type`/`status`); und der Modus **„Erklären" fehlt** für diesen Bereich, weil eine Gruppe kein Prosa-Feld hat (`modes=("create", "edit")` im geteilten Dialog). Kann ein Wunsch nicht ausgedrückt werden, sagt das Modell es in `note` — der Dialog zeigt den Hinweis neben dem Prüfergebnis, statt still etwas wegzulassen. Knopf „Vorschlag erzeugen" neben der Rechte-Matrix; das Ergebnis landet in Matrix und Karte, **nie** in der Datenbank. **Nachweis:** 31 Prüfungen gegen einen lokalen Fake-Provider (Kette Kern → NATS → Worker → Provider), 10 neue Tests (davon einer, der die echte Seite fährt und prüft, dass `POST`/`PATCH /groups` ungerufen bleibt), live im Browser geprüft (Matrix + Scope + Zähler, Gruppe unverändert gespeichert). Details: `plan/detail/benutzer-gruppen-rechte.md`, Stufe D.
- **Offen aus Stufe 2:** nichts. Eine **echte Import-Vorschau** (MEF-Dry-Run des Kernports über die Zeilen) bleibt ein eigener Schritt — heute prüft der Bereich die Zuordnung gegen Datei und Datenmodell, nicht die entstehenden Objekte.

**Stufe 2b — Gruppen-Rechte und Scope (2026-09-27).** Ein weiterer JSON-Bereich nach derselben Mechanik, angestossen aus Stufe D des RBAC-Plans (`plan/detail/benutzer-gruppen-rechte.md`).

**Stufe 3 — Komfort und Betrieb.**
„Erklären" für ganze Konfigurationen, Vorschlags-Historie je Regel/Attribut — **umgesetzt 2026-10-17** über das Audit (`GET /ai/history`, Knopf „Verlauf", kein zweiter Speicher); **Streaming der Antwort** ebenfalls umgesetzt (nur `explain`, mit Abbruch, §13); offen bleibt die Kosten-/Nutzungsanzeige.

Reihenfolge-Begründung: Stufe 1 hat die **stärksten Prüfungen** (Compile + Dry-Run gegen echte Daten) und den größten Nutzen; Suche und JSON folgen mit derselben Mechanik, brauchen aber je eigene Prompt-/Prüfteile.

## 10. Tests und Verifikation

- `tests/test_ai_history.py`: `history_rows` — audit-Metadaten als Tabellenzeilen, die drei Ausgänge (`accepted`, `cancelled`, `rejected`), Einträge ohne `metadata` und Fremdeinträge. **Umgesetzt** (4 Tests).
- `tests/test_ai_stream.py` und `tests/test_ai_provider.py`: `sse_json` (Datenzeilen, Trenner, kaputte Pakete, Skalare), `chat_stream` (Stücke in Reihenfolge, `[DONE]`, `usage`, `stream: true` im Rumpf, leere Antwort ⇒ Fehler) und der **Abbruch-Marker** des Workers (`cancelled: true` im Antwort-JSON, damit die Historie nicht „abgelehnt“ sagt). **Umgesetzt** (7 Tests).
- `tests/test_ai_assist.py`: der Dialog bietet nur Modi, die mit dem Editorinhalt arbeiten können (`offered_modes`: „Erklären“ erst mit Inhalt, Aufrufer-Grenzen bleiben), und „Stopp“ **wartet auf die Id** des ersten Stream-Ereignisses, statt wirkungslos zu verpuffen. **Umgesetzt** (5 Tests, im Browser-Durchlauf vom 2026-10-17 gefunden).

- `tests/test_ai_policy.py`: AST-Prüfung (fehlende/zusätzliche Funktion, falsche Signatur, jeder Name der Verbotsliste), Built-in-Liste **gegen die echte Sandbox** (kein abgeschriebener Katalog). **Umgesetzt** (12 Tests).
- `tests/test_ai_context.py`: Projektionen (nur eigene Attribute, `reference_data` ausgeschlossen), **Größenbudget** (Prompt bleibt unter einer definierten Zeichengrenze), Few-Shot-Auswahl je Regeltyp. **Umgesetzt** (9 Tests, inkl. Generator-Abgleich; für den Gruppen-Bereich kommen drei dazu: Rechte-Katalog mit Rollen-Defaults, Scope-Schema und Typ-Schlüssel).
- `tests/test_ai_examples.py`: jede ausgelieferte Beispielregel besteht `compile_rule` — die Beispiele können nicht von der Politik abdrifteten. **Umgesetzt** (4 Tests; läuft zusätzlich jede Beispielregel gegen ein **leeres** Objekt, damit „fehlender Wert darf nicht werfen“ nicht nur behauptet ist).
- `tests/test_ai_service.py`: gemockter Provider — Erfolgspfad, Retry nach Politikfehler, Abbruch nach dem zweiten Versuch, `ai_enabled=false`, fehlender Key, Timeout, Worker nicht erreichbar (`503`). **Umgesetzt als `tests/test_ai_service.py` + `tests/test_ai_provider.py`** (21 Tests: Erfolgspfad mit Metadaten, Projektion im Prompt ohne Standard-Treffer, ein Korrekturversuch, der die Antwort rettet, Abbruch nach dem zweiten Fehlversuch, Provider-Fehler ohne Retry, fehlerhafte JSON-Hülle, Prosa im Modus „Erklären“, unbekannter Bereich/Modus ohne Modellaufruf, Stundenbremse, Verbindungstest; dazu URL-/Header-Bau je Anbieter und die Fehlerpfade des Providers).
- Kern: Unit-Test für die Settings-Vorrang-Regel und die **Secret-Maskierung** (`GET /system/settings` liefert den Key nicht, aber `configured=true`), Test für den `ai.generate`- und den `ai.test`-Client (NATS-Nachricht aufbauen/antworten). **Umgesetzt** (Rust: `ai::tests` + `api::system::tests`, 183 `cargo test`; die NATS-Kette selbst deckt die Live-Prüfung ab, weil sie zwei Prozesse verbindet).
- Alle Gates wie üblich (`scripts/check.*`), inklusive `mypy` über die neuen Module (sie gehören im selben Schritt in `[tool.mypy] files`).

## 11. Risiken und bewusste Nicht-Ziele

| Risiko | Umgang |
| --- | --- |
| Modell erfindet Helfer/Parameter | Politik-Block im Prompt + AST-Vorprüfung + Compile-Policy + Dry-Run; gemessen bestand das Modell die Prüfung ohne Nachbesserung |
| Prompt wird zu groß / verwässert | Projektionen (Abschnitt 5) mit Größentest; **keine** Rohlisten |
| Kosten und Latenz | kleines `max_output_tokens`, Temperatur 0,2, Timeout, optionale Stundenbremse, Audit mit Tokenzahl; ~1,5 s und ~1 500 Token je Vorschlag (gemessen) |
| Geheimnisabfluss | Key nur im Kern/Worker, maskiert in jeder Antwort, nie im Prompt, nie im Browser |
| Modellausfall | klare Meldung, kein Ersatzweg, keine Auswirkung auf Speicherpfade |
| „KI-Code" im Bestand | Vorschlag ist ein Vorschlag: Editor, Prüfung, Dry-Run, Audit-Eintrag — und die Regel, dass generierter Code **denselben** Weg geht wie handgeschriebener |

**Nicht-Ziele:** kein Chat-Agent, kein Mehrmonats-Gedächtnis, keine automatische Aktivierung einer Regel, keine Objektwerte im Prompt, keine automatische Schemaänderung, kein Ersatz für Handbuch oder Review, keine Modellwahl pro Nutzer, **kein** Agenten-Framework.

## 12. Offene Punkte

1. **Provider-Voreinstellung** — **umgesetzt (2026-10-16):** `meadow/frontend/settings.py` führt für jeden Anbieter Beispielwerte (`PROVIDER_EXAMPLES`: generisch/DeepSeek, OpenAI, Azure OpenAI, Ollama); der Knopf „Beispielwerte eintragen" (`ai-preset-apply`) füllt Endpunkt und Modell **nur in leere Felder** ein, vorhandene Werte bleiben, und eine Meldung sagt, was eingetragen wurde. Kein Kern-Bestandteil — die Voreinstellung ist Bedienhilfe für die Inbetriebnahme.
2. **Streaming** der Antwort — **umgesetzt (2026-10-17), nur für `explain`:** `POST /ai/generate/stream` liefert die Textstücke als SSE (Worker → `ai.stream.<id>` → Kern → NiceGUI), „Stopp"/Schließen schickt `POST /ai/generate/{id}/cancel`; den Abbruch entscheidet **der Kern** (`cancelled: true` in der Antwort, `ai.cancel.<id>` an den Worker nur als Token-Ersparnis), und der Audit-Eintrag fällt wie bei jedem Aufruf an — ein Abbruch als `ok: false` **und** `cancelled: true`. Artefakte bleiben blockierend, weil sie erst nach der Prüfkette gezeigt werden dürfen. Details: §13.
3. **Sichtbare Historie** je Regel/Attribut — **umgesetzt (2026-10-17):** der Dialog schickt `target` (Record-Id, `new` für Ungespeichertes, der Attributname beim berechneten Attribut; das Import-Mapping hat keine stabile Identität und bekommt keinen Knopf), der Kern legt es in den `ai_generated`-Audit-Eintrag, und `GET /ai/history?area=…&target=…` (`ai.use`) liefert Modell, Versuche, Latenz, Tokenzahl und Verdikt; der Knopf **„Verlauf"** zeigt sie als Tabelle. **Kein zweiter Speicher** — das Audit ist die Quelle.
4. **Import-Vorschau im Kern** — **umgesetzt (2026-10-16):** `POST /imports/preview` führt den **Schreibpfad selbst** aus (`objects::upsert_objects` im Report-Modus: Typregister, Business-Key, Scope, Attributwerte, Regeln) und ergänzt die Kompositionsregel des Import-Jobs (ein Elternteil, der weder im Dokument noch im System liegt, lässt sein Kind scheitern). Die Antwort zählt `create`/`update`/`failed`, liefert die Objektzeilen (`results`, gedeckelt) und nennt die Objekttypen, die der Import registrieren würde. **Geschrieben wird nichts** — kein Objekt, keine Kante, kein Event, kein Audit, kein Typ; die Regelauswertung läuft im Report-Modus (`WriteEvaluation::as_dry_run`) und kann keinen Circuit Breaker bewegen. Grenzen: `references` werden gezählt, nicht aufgelöst, und die Werte-Normalisierung des Import-Workers läuft nicht mit. Knopf „Mit dem Kern prüfen" auf `/import` (`CoreClient.preview_import`).
5. **Sprache der Vorschläge** — **umgesetzt (2026-09-19):** die Prosa folgt der Arbeitssprache (das Frontend schickt `language`/`current_locale()` mit `POST /ai/generate`, der Kern reicht sie in den NATS-Auftrag weiter, der Prompt sagt „Write … in the language `X`; the code stays English"), Code und Kommentare bleiben Englisch (Projektregel) — `meadow/ai/prompts.py`, `meadow/frontend/ai_assist.py`.
6. **Job-Parameter beim Speichern prüfen** — **umgesetzt (2026-10-16):** `POST`/`PATCH /jobs` rufen `job_schema::problems_for_write`; deklarierte Felder, die fehlen oder falsch geformt sind, ⇒ `400` (`` invalid job parameters: … ``), unbekannte Schlüssel werden gespeichert und als `notices` (Prüfendpunkt) plus `WARN`-Logeintrag der Kategorie `job` gemeldet. Das Job-Formular fragt den Prüfendpunkt **vor** dem Speichern (blockierende Meldungen halten auf, Hinweise erscheinen danach als Warnung), und die Typ-Auswahl ist beim Bearbeiten gesperrt, weil `PATCH /jobs/{id}` den Typ nicht ändern kann.

## 13. Streaming der Antwort (Entscheidung 2026-10-17)

**Stand:** umgesetzt (Kern, Worker, Dialog), **live gegen DeepSeek geprüft** (2026-10-17, Wegwerf-Instanz auf eigenen Ports) und **im Browser durchgeklickt** (2026-10-17, Chromium gegen die Wegwerf-Instanz, Login als `admin`): 78 Textstücke im API-Weg, `ok: true`; im Dialog wächst die Vorschau sichtbar während des Laufs (Stopp-Knopf erscheint, Text 73 → 223 → 245 Zeichen bei laufendem Strom), „Stopp" endet mit **„Cancelled."** und leerer Vorschau (Apply bleibt gesperrt), die Historie zeigt `cancelled`. Azure OpenAI und Ollama sind weiterhin **nicht** geprüft.

Der Browser-Durchlauf fand zwei echte Fehler, beide behoben:

* **„Erklären" stand auch bei leerem Editor** — der Klick endete in `current_script is required for the mode explain`. Der Dialog bietet jetzt nur Modi, die mit dem aktuellen Inhalt arbeiten können ([`offered_modes`](../../meadow/frontend/ai_assist.py)), und sagt daneben, warum „Erklären" noch fehlt.
* **Der Abbruch verpuffte**, wenn er früh kam: die Id ist das **erste** Stream-Ereignis (ein Klick davor hatte nichts zu canceln), und `ai.cancel.<id>` ist Core-NATS — kam die Nachricht, bevor der Worker abonniert hatte, war sie verloren und der Lauf lief weiter. Jetzt wartet der Dialog kurz auf die Id und der **Kern** entscheidet den Abbruch selbst (siehe unten).

**Umfang.** Nur der Modus **`explain`** streamt. Ein Artefakt (Skript, JSON) erscheint weiterhin erst **nach** der Prüfkette — roher Text, der anschließend verworfen, korrigiert oder abgelehnt wird, wäre eine Anzeige, die niemand speichern kann. „Erklären" ist Prosa ohne Prüfobjekt.

**Transport über drei Prozesse.** Der Browser sieht den Kern nie; die Kette ist Worker → NATS → Kern → NiceGUI-Prozess → Browser-Websocket. NATS-Request/Reply kann nicht fortschreitend antworten, deshalb:

1. Der Kern abonniert **vor** dem Aufruf ein eigenes Subject **`ai.stream.<id>`** (Core-NATS, keine JetStream-Persistenz: Textstücke sind Anzeige-Fortschritt, kein Vertrag).
2. Der Worker liest die Provider-SSE (`stream: true`), **publiziert** jedes Stück dorthin und antwortet die **fertige** Antwort wie bisher per `msg.respond`.
3. `POST /ai/generate/stream` (nur `mode = explain`, sonst `400`) liefert **`text/event-stream`**: `{"type":"id","id":…}`, dann `{"type":"delta","text":…}`, zuletzt `{"type":"done","reply":…}` oder `{"type":"error","error":…}`. Ein Task vereint Subscriber und `send_request` in einem `select!`, damit weder Verbindung noch Abo zurückbleiben; die **Audit-Zeile** schreibt der Kern wie bei jedem Aufruf (eine Zeile, mit `target`, Modell, Versuchen, Latenz, Tokens).
4. Das Frontend liest die Antwort mit `httpx.stream`, schreibt die Stücke in die Vorschau (NiceGUI-Update über den eigenen Socket) und zeigt die Statuszeile erst am Ende.

**Abbruch.** Solange der Lauf läuft, zeigt der Dialog **„Stopp"**; Schließen oder Stopp schickt **`POST /ai/generate/{id}/cancel`** (`ai.use`). Der Abbruch läuft **zweigleisig** (`crates/pim-core/src/ai.rs`):

* Der **Kern** hält zu jedem laufenden Stream einen `watch`-Kanal. Der Abbruch setzt ihn, der SSE-Task endet sofort mit `{"type":"done","reply":{"ok":false,"cancelled":true,…}}` und schreibt damit die Audit-Zeile als **abgebrochen** — das ist die verbindliche Seite, denn der Kern besitzt die Antwort. Vorher konnte ein früher Abbruch verloren gehen (siehe Stand).
* Zusätzlich geht `ai.cancel.<id>` an den Worker, der die Provider-Aufgabe abbricht (spart die restlichen Tokens). Diese Nachricht ist **best effort**.

Die Id wird vor jeder Verwendung auf `[A-Za-z0-9]` geprüft (kein Subject-Anteil aus einer Eingabe); der Dialog wartet bis zu 2 s auf sie, weil sie erst mit dem ersten Ereignis eintrifft. Das Flag **`cancelled`** in der Antwort ist der maschinenlesbare Grund: die Historie zeigt den Lauf als *abgebrochen* statt als *abgelehnt*, ohne auf einen Fehlertext zu passen.

**Zeitlimits.** Der gestreamte Aufruf bekommt einen **eigenen** httpx-Timeout statt des geteilten 30-s-Client-Timeouts (der Provider darf laut `ai_timeout_ms` selbst 30 s plus Kontextaufbau brauchen — heute könnte das Frontend vorher aufgeben); der Kern liest ohne Read-Timeout, das Gesamtbudget bleibt der NATS-Timeout (`ai_timeout_ms` + 5 s).

**Nicht-Ziele.** Kein Streaming für Artefakte, kein „Token x von y", kein Speichern der Textstücke, kein zweiter Prüfpfad: die Prüfkette läuft wie heute auf der fertigen Antwort.

**Offen/Annahmen.** Ob der Provider `usage` im letzten SSE-Paket mitschickt, ist anbieterspezifisch (OpenAI-kompatibel: `stream_options.include_usage`); fehlt sie, bleibt die Tokenzahl im Audit leer. Azure OpenAI und Ollama sind gegen Streaming **nicht live geprüft** (nur der OpenAI-kompatible Pfad).

# Business Rules (Python-Skripte)

- **Hauptdokument:** Abschnitt 8 ([Workflow, Freigaben & Business Rules](../meadow-pim-architektur.md#8-workflow-freigaben--business-rules)) – Business Rules sind die technische Grundlage des dort beschriebenen Python Business-Rules-Service; sie berühren außerdem die Abschnitte 4 (Datenmodell: `business_rule`, berechnete Attribute), 5 (API), 6 (Jobs) und 7 (Import/Normalisierung).
- **Relevante Phasen:** Phase 1, 4, 5, 6, 7, 8
- **Stand:** Entwurf

## Überblick

**Business Rules** sind anpassbare Geschäftslogik in **Python** als Skriptsprache. Sie werden als Daten in SurrealDB gespeichert (Tabelle `business_rule`), im Admin-UI bearbeitet und in einer **gesicherten Sandbox** ausgeführt. Es gibt **genau einen** Autoren-Mechanismus (ein Regel-Skript mit einem definierten Vertrag); die fünf Regeltypen unterscheiden sich nur durch Ausführungskontext und Ergebnisinterpretation.

**Regeltypen (verbindlich):**


| Typ              | Zweck                                                                                                                       | Ausführung                                            |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `validation`     | Prüfungen beim Schreiben (Pflichtfelder, Formate, Bereiche, Querfeld-Plausibilität) –`error` blockt, `warning` annotiert    | **blockierend** im Schreibpfad                        |
| `computed`       | Berechnete/abgeleitete Attribute (R2), z. B. Brutto aus Netto, zusammengesetzter Titel                                      | **blockierend** im Schreibpfad, Wert wird gespeichert |
| `normalization`  | Bereinigung/Transformation von Werten beim**Import** (Trimmen, Einheiten vereinheitlichen, Synonyme mappen)                 | in der Import-Pipeline (Worker)                       |
| `workflow_guard` | Erlaubte Statusübergänge / „darf nicht ohne X publiziert werden“                                                            | **blockierend** bei Statuswechsel                     |
| `bulk`           | Regel über gefilterte Objektmenge (Neuberechnung, Anreicherung, Recompute) – Job`business_rule`                             | **Job** (async, R5)                                   |

Regeln mit **externen Seiteneffekten** (Webhooks, Erzeugen verwandter Objekte, Aufrufe der Middleware) sind **nicht** Teil der Business Rules – das ist Aufgabe des Eventsystems/Webhook-Outbound (Abschnitt 7). Die Sandbox erzwingt dies (keine externe I/O).

## Datenmodell

### Tabelle `business_rule`

```json
{
    "id": "rule_001",
    "name": "gross_from_net",
    "description": "Berechnet den Bruttopreis aus dem Nettopreis.",
    "type": "computed",              // validation | computed | normalization | workflow_guard
    "applies_to": ["product"],       // optional: Objekttypen (leer = alle), analog applicable_types
    "script": "def run(obj, ctx, result):\n    ...",
    "state": "active",               // draft | active | disabled
    "version": 3,
    "created_at": "2026-08-17T08:00:00Z",
    "updated_at": "2026-08-17T11:30:00Z",
    "updated_by": "user_1"
}
```

- `type` bestimmt, wie das Ergebnis des Skripts interpretiert wird (siehe Script-Kontrakt).
- `applies_to` schränkt die Objekttypen ein, für die die Regel ausgeführt wird (leer = alle). Die Prüfung erfolgt bei der Auswertung.
- Der Zustand (`state`) steuert die Ausführung: nur `active`-Regeln werden im Schreibpfad ausgeführt; `draft` und `disabled` werden nie ausgeführt (siehe Lebenszyklus).
- **Seed-Regeln** werden als Code im Repository ausgeliefert und beim Setup in die Tabelle geseedet; danach werden sie als Daten gepflegt.

### Berechnete Attribute (R2) in der `attribute_definition`

Ein Attribut wird als berechnet gekennzeichnet; die zugehörige Regel wird über den Namen verknüpft:

```json
{
    "id": "attr_def_gross_price",
    "name": "gross_price",
    "type": "number",
    "group_id": "attr_group_01",
    "inheritable": true,
    "computed": true,                 // berechnetes Attribut: Wert wird von einer compute-Regel geschrieben
    "computed_rule": "gross_from_net",// Name der business_rule, die dieses Attribut setzt
    "dependencies": ["net_price"],    // Eingabeattribute, die die Formel liest (für Recompute-Kaskade)
    "computed_reads_inherited": true, // true: Formel liest vererbte Werte -> async Kaskade auf Nachfahren
    ...
}
```

- `computed: true` + `computed_rule` + `dependencies` sind das **Dependency-Metadatum** aus der Konvention (Abschnitt 4 / R2).
- Berechnete Attribute sind **gespeicherte Werte** (stored-first), berechnet beim Schreiben; es gibt **kein** On-Read-/Virtuell-Modus im MVP. Lese-Pfade im Rust-Kern bleiben einfache DB-Reads.
- Eine compute-Regel darf **nur** als `computed` deklarierte Attribute schreiben (`result.set_value`), nie beliebige Felder.

## Script-Kontrakt

Eine Regel ist **ein Skript mit einem Einstiegspunkt**:

```python
def run(obj, ctx, result):
    ...
```

Die Interpretation des Ergebnisses hängt vom Regeltyp ab (gleiche Signatur, ein Mechanismus):

- **validation** → `result.add_error(attr, code, message)` (blockierend) / `result.add_warning(attr, code, message)` (nicht-blockierend, UI/Vollständigkeit)
- **computed / normalization** → `result.set_value(attr, value)` – nur auf deklarierte berechnete Attribute (computed) bzw. das importierte Feldschema (normalization)
- **workflow_guard** → `result.allow(bool, reason)`

**Injizierte Globals (alle gesichert):**

| Global                | Inhalt                                                                                                                                                                                                                                                                       |
| --------------------- | -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `obj`                 | **Aufgelöste Sicht** des Objekts: eigene + vererbte + sprachlich aufgelöste Attributwerte (gemäß Sprachbaum), plus `id`, `type`, `status` und **read-only** Relationen. Für computed/normalization wird über `result.set_value` geschrieben, nicht durch Mutation von `obj`. |
| `ctx`                 | Kontext:`rule_type`, `object_type`, `language(s)`, `mode` (`save` \| `import` \| `transition` \| `bulk`), `actor`                                                                                                                                                            |
| `result`              | Typisierte Helfer (siehe oben)                                                                                                                                                                                                                                               |
| `log(message, level)` | Sicheres Logging (in Ergebnis/Audit sichtbar)                                                                                                                                                                                                                                |

- **Import-Whitelist:** zulässige Module sind administrativ konfigurierbar (Standard: `json`, `datetime`, `math`, `re`, `statistics`, `decimal`, `typing`, `uuid`, `collections`), analog zur Whitelist der Connector-Hub-Middleware. Nicht gelistete Module lassen sich nicht importieren.
- **Explizite Bindings:** Zusätzliche Fähigkeiten (z. B. Einheitenumrechnung, String-Helfer) werden als explizite Bindings injiziert, nicht per Import. Weitere explizite Bindings dürfen später ergänzt werden (Muster: capability-limits der Connector-Hub-`source`/`target`-Proxies), falls sich der Bedarf zeigt – immer read-only bzw. explizit eingeschränkt.

## Sandbox & Sicherheit

- **RestrictedPython in-process** (`compile_restricted_exec` + Safe-Builtins + Write-Guard + `safer_getattr`) im Python-Service – kein separater Subprozess/Container pro Regel im MVP (selbst gehostete Einzel-Mandanten-Umgebung; die Sandbox schützt vor Fehlbedienung, nicht vor feindlichen Mit-Mandanten).
- **Harte Limits (verbindlich):**
  - kein Zugriff auf private Attribute (`_...`), keine Schreibzugriffe auf geschützte Objekte (Write-Guard),
  - **Timeout** pro Auswertung: eng im blockierenden Schreibpfad (~1–2 s), großzügig bei Bulk-Jobs (~30 s),
  - **Speicher-Limit** pro Auswertung (weiche Grenze + Abbruch),
  - **max. Skriptgröße** (~64 KB).
- **Keine externe I/O:** Whitelist + RestrictedPython verhindern `socket`/`http`/`os`/`subprocess` etc.
- Eine Regel, die ein nicht-whitelisted Modul oder eine unzulässige Operation nutzt, wird bereits beim **Speichern abgelehnt** (Compile-Check).

## Ausführung

### Blockierender Schreibpfad (R1, R2, R4)

Der Rust-Kern bleibt die **DB-Autorität**, ist aber für die Regelauswertung von einem synchronen internen Aufruf abhängig:

1. Beim Schreiben (Anlegen/Ändern von Objekten, Statuswechsel) baut der Kern die aufgelöste Sicht des Kandidaten auf (eigene + vererbte + sprachlich aufgelöste Werte).
2. Der Kern ruft **synchron** den internen Rule-Evaluation-Endpunkt des Python-Service auf (nicht Teil der öffentlichen OpenAPI): Request = Kandidat + aufgelöste Werte + Kontext.
3. Der Service führt die zutreffenden `active`-Regeln (R1/R2/R4) in der Sandbox aus und liefert: Validierungsfehler/-warnungen, berechnete Werte, Übergangs-Verdikt.
4. Der Kern **erzwingt** das Ergebnis: blockierende Fehler brechen den Schreibvorgang ab; berechnete Werte werden **atomar in derselben Transaktion** gespeichert; der Statuswechsel wird nur bei Erlaubnis durchgeführt.
5. Ergebnis: berechnete Attribute sind **synchron mit dem Save** korrekt (kein Staleness-Fenster, kein zweiter asynchroner Write, keine Feedback-Schleife).

**Konsequenz:** Ein Save hängt davon ab, dass der Python-Service erreichbar ist und die Skripte terminieren. Mitigation: enge Timeouts, optionaler Degrade-Modus („Speichern erlauben + als unberechnet markieren“, konfigurierbar). Eine fehlerhafte Regel kann nie dauerhaft blockieren (siehe Fehlerbehandlung).

### Nicht-blockierende Kontexte

- **Normalization (R3):** läuft in der Import-Pipeline des Workers (Abschnitt 7) auf den MEF-Daten vor dem Schreiben; gleiche Sandbox, kein Aufruf über den Kern.
- **Bulk (R5):** Job `business_rule` über eine gefilterte Objektmenge (Abschnitt 6), async, mit Report.

## Berechnete Attribute (R2) – Timing & Recompute

**Konvention (verbindlich, stored-first):**

1. **Speicherung:** Berechnete Attribute sind **gespeicherte Werte** im `attributes`-Feld des Objekts (wie normale Attribute). Lese-Pfade (Grid, Export, Suche, Sortierung, Filterung) lesen nur die Datenbank – **keine** On-Read-Berechnung, **kein** Rust↔Python-RPC pro Read.
2. **Berechnungszeitpunkt:** beim Schreiben des Objekts, **synchron** im Schreibpfad (siehe oben). „Wann wird berechnet?“ ist damit eindeutig: **jeder Save berechnet atomar neu.**
3. **Reinheit:** Eine compute-Regel ist eine **pure Funktion** der aufgelösten Attributmenge des Objekts – keine Seiteneffekte, keine Schreibzugriffe auf andere Objekte, keine externen Aufrufe.
4. **Dependencies:** Die Eingabeattribute werden in `dependencies` der berechneten Attributdefinition deklariert. Die UI warnt, wenn das Skript ein nicht deklariertes Attribut liest (Hinweis, keine Enforce-Pflicht).
5. **Kaskade (vererbte Werte):** Liest eine Formel vererbte Werte (`computed_reads_inherited: true`), wird beim Save eines Vorfahren ein **Recompute-Job** über den betroffenen Teilbaum per Event-Stream angestoßen (R5-Infrastruktur) – **nie inline**. Folge: eigene berechnete Werte sind stark konsistent; **vererbt-abhängige berechnete Werte von Nachfahren sind eventual consistent** (dokumentierte Eigenschaft). Die Event-Reihenfolge pro Objekt ist garantiert (Abschnitt 7), der Recompute folgt der Änderung deterministisch.
6. **Definitionsänderung:** Wird eine compute-Regel oder die `dependencies`/`computed`-Markierung geändert, löst dies einen **Bulk-Recompute-Job** über die betroffenen Objekttypen aus (bestätigbar, mit Report).

## Lebenszyklus & Testmodus

### Zustände

- `draft` – wird nicht ausgeführt, frei editierbar.
- `active` – wird im Schreibpfad ausgeführt.
- `disabled` – wird nicht ausgeführt, Konfiguration bleibt erhalten.

**Kein** mehrstufiger Freigabe-Workflow im MVP (ein Admin ist Autor und Freigebender). Jede Zustandsänderung ist **auditiert**.

### Sicherheitsnetze

- **Compile-Check bei jedem Speichern:** Ein Skript, das nicht kompiliert oder Sandbox-Regeln verletzt (z. B. nicht-whitelisted Import), kann **nicht gespeichert werden** – auch nicht als Draft.
- **Versionierung:** Jeder Edit erhöht `version`; das Audit-Log hält fest, wer wann was geändert hat (Diff).
- **Dry-Run:** Vor der Aktivierung (und auf Abruf) lässt sich eine Regel gegen ein **Beispielobjekt** ausführen – ein reales Objekt aus der DB (realistische aufgelöste Werte) oder ein konstruierter Payload (Edge-Cases). Ergebnis: Verletzungen, Warnungen, berechnete Werte, Log – **ohne Persistenz**.

### Rule Lab (interaktives Testmodus)

Das **Rule Lab** ist die Admin-UI rund um den Dry-Run, angelehnt an das Interactive Lab der Connector-Hub-Middleware:

- Regel editieren und gegen **ein oder mehrere reale Objekte** (oder eine Filterauswahl) ausführen,
- pro Objekt Ergebnisse anzeigen: Verletzungen, berechnete Werte vor/nach, `log()`-Ausgabe, Ausführungszeit,
- iterieren: editieren → ausführen → inspizieren, ohne Live-Daten zu berühren,
- realen Objekt-Payload in den Editor kopieren (deterministische Edge-Case-Tests),
- **read-only by default, keine Persistenz**; gleiche Sandbox/Timeouts wie die Produktions-Auswertung.

Lab-Ausführungen werden **auditiert** (wer hat wann welche Regel gegen welche Objekte ausgeführt – Metadaten, nicht die Payloads). Das Rule Lab dient auch als **Debugging-Werkzeug**, nachdem der Circuit Breaker eine Regel deaktiviert hat. **Aktiviert werden kann nur, was gegen reale Daten beobachtet wurde.**

## Fehlerbehandlung & Betrieb

- **Fail-closed im Schreibpfad:** Wirft eine aktive blockierende Regel eine Exception oder läuft in den Timeout, wird der Schreibvorgang abgelehnt („Regel X intern fehlgeschlagen“). Eine kaputte Validierung führt **nicht** stillschweigend zu einer Durchreichung.
- **Circuit Breaker:** Nach **N aufeinanderfolgenden Fehlern** (konfigurierbar, Standard 3) wird die Regel **automatisch auf `disabled`** gesetzt und eine **Hochprioritäts-Meldung** ausgelöst (Event + UI-Benachrichtigung + Job-Log). Erfolgreiche Ausführung setzt den Zähler zurück.
- **Degrade-Modus (optional):** Ist der Python-Service nicht erreichbar, kann der Kern konfigurierbar auf „Speichern erlauben + Objekte als unberechnet markieren“ umschalten (bewusst kein Standard).
- **Alerting/Monitoring:** Rule-Ausführungen (Timeout, Exception, Circuit-Breaker-Auslösung) gehen in die bestehende Observability (Job-Log, Events, Monitoring, Abschnitt 12).

## API (Auszug)

| Methode | Pfad                     | Beschreibung                                                                  |
| ------- | ------------------------ | ------------------------------------------------------------------------------|
| GET     | `/rules`                 | Regeln auflisten (Filter:`type`, `state`, `applies_to`)                       |
| POST    | `/rules`                 | Regel anlegen (Compile-Check beim Speichern)                                  |
| GET     | `/rules/{id}`            | Regel laden (inkl.`version`, Historie über Audit)                             |
| PATCH   | `/rules/{id}`            | Regel bearbeiten (Compile-Check; Version++ + Audit)                           |
| POST    | `/rules/{id}/activate`   | Regel aktivieren (`draft`/`disabled` → `active`; RBAC `admin`/`superadmin`)   |
| POST    | `/rules/{id}/disable`    | Regel deaktivieren (`active` → `disabled`)                                    |
| POST    | `/rules/{id}/dry-run`    | Dry-Run gegen Beispielobjekt(e) oder konstruierten Payload (kein Persistieren)|
| POST    | `/rules/{id}/test-batch` | Rule Lab: Ausführung gegen Objektauswahl (read-only, kein Persistieren)       |
| POST    | `/rules/{id}/recompute`  | Recompute/Bulk-Job über betroffene Objektmenge anstoßen (R5)                  |

Intern (nicht öffentlich): Rule-Evaluation-Endpunkt des Python-Service für den synchronen Schreibpfad des Kerns.

**RBAC:** Nur `admin`/`superadmin` dürfen Regeln anlegen, bearbeiten, aktivieren/deaktivieren und das Rule Lab nutzen. `editor`/`viewer` können Regeln lesen (Verhalten verstehen), aber nicht ändern. Änderungen/State-Wechsel sind im Audit-Log.

## Abgrenzung

- **Kein Ersatz für das Eventsystem:** Regeln rufen keine Webhooks und publizieren keine Events; Events entstehen ausschließlich durch tatsächliche Datenänderungen über die Outbox (Abschnitt 7).
- **Kein Ersatz für den Workflow:** Die Zustandsmaschine bleibt einfach (Abschnitt 8); `workflow_guard`-Regeln prüfen nur Übergänge, sie führen keine Workflow-Engine.
- **Keine schwere Workflow-Engine, keine externe Rule-Engine, kein DSL:** Python (RestrictedPython) ist die eine Skriptsprache.

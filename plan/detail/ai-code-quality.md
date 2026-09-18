# AI-Code-Qualität und Deslop-Workflow

- **Hauptdokument:** Abschnitt 12 ([Betrieb & Deployment](../meadow-pim-architektur.md#12-betrieb--deployment))
- **Relevante Phasen:** Phase 8, 9 (Qualitätssicherung, bewusst gestartete Analyse und Reparatur)
- **Stand:** Stufe 1 umgesetzt (2026-09-18): gepinnte Konfiguration `stopslop.toml`, Baseline, `scripts/deslop.ps1`/`deslop.sh`, Fixtures und `tests/test_deslop.py`. Offen ist der erste Reparaturpass am Bestand.

## 1. Ziel und Abgrenzung

Meadow PIM verwendet viel automatisch erzeugten Code. Ruff, Pylance, mypy, rustfmt,
Clippy und die Tests bleiben die primären Korrektheits- und Stil-Gates. Sie beweisen
aber nicht, dass eine Funktion wirklich angeschlossen ist, dass ein Kommentar kein
Chat-Rest ist oder dass eine plausible Implementierung nur ein Stub ist.

Der Deslop-Workflow liefert deshalb zusätzliche Hinweise und unterstützt Reparaturen.
Er ist kein KI-Autorenschaftstest, kein Sicherheitsnachweis und kein Ersatz für Tests
oder menschliche Abnahme. Ein niedriger Score darf nicht als Beweis für
Laufzeitkorrektheit interpretiert werden.

## 2. Werkzeugentscheidung

### 2.1 Primäres Werkzeug: `stopslop`

`stopslop` ist ein einzelnes Rust-Programm unter MIT-Lizenz. Es analysiert Python,
Rust, Go und TypeScript sowie Markdown/Text/HTML. Es untersucht AST-Strukturen,
Kommentare und String-Inhalte und läuft deterministisch ohne LLM, API-Aufruf oder
Netzwerkzugriff. Für Meadow sind besonders relevant:

- Chat-Preambles, Elisionskommentare und Markdown-Fences in Quelltext,
- Platzhalter-Credentials und andere offensichtliche Beispielwerte,
- leere oder nicht implementierte Körper,
- breite bzw. verschluckende Fehlerbehandlung,
- Cross-Language-Leakage, Pass-through-Wrapper und Ein-Implementierung-Abstraktionen,
- überlange oder rein wiederholende Kommentare,
- Markdown- und Dokumentationsartefakte,
- optional unaufgelöste Paket-Imports anhand von `pyproject.toml` und `Cargo.toml`.

Der Grund für genau dieses Werkzeug ist die Lücke der bestehenden Gates: Ruff,
Clippy und die Type-Checker verwerfen Kommentar- und String-Inhalte vor der Analyse
– dort liegen die Artefakte.

### 2.2 Python-Deep-Dive: `ai-slop-detector`

`ai-slop-detector` ist ein deterministischer Python-Analyzer mit optionaler
JavaScript-/Go-Unterstützung, aber ohne Rust-Analyse. Er liefert patternbezogene
Befunde und einen Deficit-Score je Datei, unter anderem für Placeholder-Körper,
mutable Defaults, bare `except`, Phantom-Imports, tote Pipelines, Duplikate,
God-Functions und Type-Escape-Muster. Die Dokumentation nennt ausdrücklich die
Grenze: kein Laufzeitbeweis, keine kalibrierten Schwellen am ersten Tag und keine
Analyse von Rust.

Er läuft manuell über `scripts/deslop.* --fix-plan`. Der Score ist nur ein
Priorisierungssignal. Die Ausgabe wird als JSON nach `.tmp/deslop/` geschrieben
(gitignoriert); PowerShell-Umleitung wird wegen der bekannten UTF-16-Falle nicht
verwendet. Die installierte Version ist **4.0**, während die PyPI-Seite noch 3.8.x
beschreibt – geprüft wird deshalb immer die installierte Fassung.

### 2.3 Nicht als zusätzliche Meadow-Gates

`sloppylint` ist ein Python-only-Projekt mit nützlichen Regeln, aber kleinerem und
zu `ai-slop-detector` überlappendem Umfang. `slopguards` und `aislop` sind ebenfalls
Python-only-Ergänzungswerkzeuge. Alle drei bleiben Vergleichswerkzeuge für einen
konkreten Befund, aber keine dauerhaften Gates: Vier heuristische Python-Scanner
nebeneinander erzeugen vor allem doppelte Befunde und unklare Schwellen. Eine
spätere Aufnahme braucht einen dokumentierten Mehrwert und eine
False-Positive-Auswertung.

### 2.4 Warum dieses Paar und nicht mehr

Der sprachübergreifende Blindspot ist für Meadow entscheidend: Rust-Code,
Rust-Dokumentation und Python-Kommentare brauchen dieselbe Artefaktprüfung.
`stopslop` deckt diese Grenze ab, `ai-slop-detector` ergänzt die Python-spezifische
Strukturanalyse. Dieses Paar ist kleiner und nachvollziehbarer als ein Sammelsurium
von Lintern – die Werkzeuge widersprechen sich in Schwellen und Regelnamen, und
mehrere parallele Scanner würden uns im Kreis laufen lassen.

## 3. Verifizierter Vertrag (stopslop 0.5.1, geprüft am 2026-09-18)

Die Projekt-README beschreibt den Stand der Hauptentwicklung, nicht den der
veröffentlichten Version. Für die gepinnte **0.5.1** gilt (alles lokal geprüft):

- **Formate:** `--format text|json|sarif`. `markdown` und `--stats` existieren nicht.
- **Regelauswahl:** `--select`, `--extend-select`, `--ignore`, `--extend-ignore`,
  `--list-rules`; `--check-imports` aktiviert `SLOP010` zusätzlich.
- **Git-Auswahl:** `--staged`/`--changed`/`--since` fehlen in dieser Version. Die
  Skripte bauen die Dateiliste deshalb über `git diff`; nicht verfolgte Dateien
  gehören wie upstream nicht zu `changed`. Eine leere Auswahl endet sauber mit `0`,
  statt den ganzen Baum zu scannen.
- **Stufung:** `fail-on-tier` wird weder in `stopslop.toml` (Fehler beim
  Konfigurations-Parsen, `exit 2`) noch als Flag akzeptiert. Das Verhalten ist
  trotzdem gestuft: ein Befund der Stufe A lässt den Lauf mit `1` scheitern, ein
  reiner Tier-B-Befund endet mit `0`. Das ist eine Eigenschaft der Version, keine
  Einstellung – `--strict` bedeutet hier deshalb „Baseline ignorieren“, nicht
  „Stilbefunde als Fehler“.
- **Konfiguration:** akzeptiert werden `select`, `extend-select`, `ignore`,
  `extend-ignore`, `exclude`, `check-imports`, `baseline`, `per-file-ignores`,
  `custom-rule`. Unbekannte Schlüssel brechen den Lauf ab, deshalb wird jeder
  Schlüssel gegen die installierte Version geprüft – nicht gegen die README.
- **Baseline:** `--write-baseline`/`--baseline` schreiben bzw. lesen
  `{"accepted": {"<code>|<pfad>|<meldung>": <anzahl>}}`. Der Fingerabdruck enthält
  keine Zeilennummern: eine Bearbeitung oberhalb eines Befunds lässt ihn nicht
  wiederauferstehen, und wer einen Befund behebt, schrumpft das Budget von selbst.
  Eine leere Baseline (`{"accepted": {}}`) zeigt den vollen Stand.
- **Pfadbasierte Ausnahmen:** `SLOP005`, `SLOP006`, `SLOP008`–`SLOP010`,
  `SLOP037`–`SLOP043` überspringen Pfade, die nach Testcode aussehen (`tests`,
  `fixtures`, `mocks`, `vendor`, `generated`, `test_*`, `conftest.py`).
  Inline-Tests in `crates/pim-core/src/*.rs` sind davon **nicht** erfasst – genau
  daher stammen die meisten akzeptierten Bestandsbefunde.

## 4. Kalibrierung am eigenen Bestand (2026-09-18)

Gezählt mit der gepinnten Version über den Quellbaum (ohne Konfiguration):

| Bereich | Befunde | davon Stufe A |
| --- | --- | --- |
| `meadow` | 293 | 6 |
| `crates` | 206 | 16 |
| `docs` | 216 | 0 |
| `tests` | 1 | 0 |

Zwei Ergebnisse prägen die Konfiguration:

1. **Die Stilregeln treffen unsere Hausordnung, nicht einen Fehler.** 512 der 716
   Befunde waren `SLOP018` (Gedankenstrich in Prosa), 135 `SLOP033` (Satz über 50
   Wörter). Deutsche Fachprosa setzt den Gedankenstrich bewusst und die
   Plan-Dokumente sind absichtlich dicht. Deshalb ist `SLOP018` repositoryweit aus,
   und die Stilgruppen (`verbosity`, `format`, `rhetoric`, `sourcing`) gelten nicht
   für Markdown. Die Artefakt-Gruppe bleibt überall an – sie ist das, was die
   bestehenden Linter nicht sehen.
2. **Die verbleibenden Tier-A-Befunde sind bewertet und akzeptiert.** Die 16
   `SLOP009`-Befunde in Rust sind `example.com`-Werte in Inline-Unit-Tests und im
   Seed (`admin@example.com`); die 6 `SLOP006`-Befunde sind bewusst abgefangene
   UI-Degradierungen, die im Code schon mit `# noqa: BLE001 - …` begründet sind
   (ein nicht erreichbarer Picker bietet weniger Optionen an, statt die Seite
   scheitern zu lassen). Zusammen **23 Befunde** stehen in
   `.stopslop-baseline.json`; der Bestand ist damit grün, und nur ein **neuer**
   Befund der Stufe A lässt den Lauf scheitern.

### 4.1 Python-Deep-Dive am Bestand (2026-09-18)

`ai-slop-detector` 4.0 über den ausgelieferten Python-Quelltext und unsere Werkzeuge
(Berichte in `.tmp/deslop/`):

| Pfad | Status | Ø Deficit | auffälligste Dateien |
| --- | --- | --- | --- |
| `meadow` | SUSPICIOUS | 18,4 | `form.py` 76,5 · `grid.py` 72,4 · `categories.py` 64,1 · `exports.py` 59,5 |
| `scripts` | SUSPICIOUS | 21,3 | `demo_data.py` 41,1 · `install_standard_bundle.py` 36,9 · `etim_ixf_to_mef.py` 31,9 |

Über `meadow` meldet das Werkzeug 493 Befunde in 47 Dateien (32 kritisch, 171 hoch).
Das deckt sich mit der Modulgröße: Die drei größten Frontend-Dateien sind gleichzeitig
die Score-Spitzen. Das ist ein Hinweis auf einen Struktur-Pass, keine Fehlerliste –
`SUSPICIOUS` heißt „ansehen“, nicht „kaputt“. `tests/` ist beim Werkzeug standardmäßig
ausgenommen, Scratch-Dateien unter `.tmp/` werden bewusst nicht gescannt.

## 5. Workflow

Der Workflow wird bewusst gestartet und repariert nie von selbst:

1. **Scope wählen:** `staged` (genau der Commit), `changed` (Arbeitsbaum gegen
   `HEAD`) oder `all` (Meilenstein). Ausgeschlossen bleiben `target/`, `.venv/`,
   `.tmp/`, `backups/`, `resources/` und die Fixtures.
2. **Primärscan:** `scripts/deslop.ps1` bzw. `scripts/deslop.sh`. Der JSON-Bericht
   landet unter `.tmp/deslop/`, die Textausgabe geht auf die Konsole.
3. **Python-Vertiefung (optional):** `--fix-plan` startet `ai-slop-detector`
   zusätzlich; es bewertet keine Testdateien als Produktionscode.
4. **Triage:** Befunde in echte Fehler, berechtigte Konstruktion, akzeptierten
   Bestand und Fehlalarm einteilen. Eine Unterdrückung braucht einen konkreten
   Grund und einen engen Scope.
5. **Kleine Reparaturpasses:** zuerst Artefakte, Placeholder und verschluckte
   Fehler; danach tote Leitungen und Imports; zuletzt Struktur- und
   Verbosity-Hinweise. Keine großen Umformulierungen, keine automatische
   Semantik-Änderung.
6. **Verhaltensnachweis:** nach jedem Pass zuerst den engsten Test, danach
   `scripts/check.ps1 python` bzw. `scripts/check.sh python`; bei Rust zusätzlich
   die Rust-Gates.
7. **Review-Abschluss:** vorher/nachher vergleichen, verbleibende Befunde begründen
   und den Review auf die geänderten Stellen konzentrieren.

Die Baseline wächst nicht automatisch mit: Ein neuer Befund der Stufe A scheitert,
bis er behoben oder mit Begründung aufgenommen wird.

## 6. Umgesetzter Baustein (Stufe 1, 2026-09-18)

| Datei | Zweck |
| --- | --- |
| `stopslop.toml` | gepinnte Regelauswahl: `SLOP018` aus, Stilgruppen nur für Markdown aus, Artefakt- und Strukturregeln an, Excludes, Baseline |
| `.stopslop-baseline.json` | 23 bewertete und akzeptierte Befunde (Details in §4) |
| `scripts/deslop.ps1`, `scripts/deslop.sh` | manueller Einstieg: `all\|changed\|staged`, `--strict` (Baseline ignorieren), `--fix-plan` (Python-Deep-Dive über `meadow` und `scripts`), Berichte unter `.tmp/deslop/` |
| `tests/fixtures/deslop/artifacts.py`, `artifacts.rs` | absichtlich fehlerhafte Beispiele; außerhalb des Repo-Scans und für Ruff ausgeschlossen |
| `tests/test_deslop.py` | Konfigurations-Invarianten und Nachweis, dass die Regeln feuern; werkzeuggestützte Tests überspringen sich ohne installiertes `stopslop` |

**Version-Pin:** Die Skripte erwarten **stopslop 0.5.1**
(`cargo install stopslop --version 0.5.1 --locked`) und warnen bei einer anderen
Version. Der Python-Deep-Dive kommt aus der Dev-Gruppe in `pyproject.toml` (über
`uv`), also aus dem Lockfile.

**Die Fixtures werden an einem neutralen Pfad gescannt** (`.tmp/deslop/proof/`,
zur Laufzeit angelegt und danach entfernt): Die Regeln überspringen Pfade, die nach
Testcode aussehen, und `SLOP010` braucht das Projektmanifest oberhalb der Datei. Ein
Scan direkt aus `tests/fixtures/` würde deshalb fast nichts finden – der Test belegt
so, was er behauptet.

## 7. Qualitätsregeln für Reparaturen

- Verhalten, öffentliche APIs, Datenformate und Fehlermeldungen bleiben unverändert,
  sofern die Reparatur nicht ausdrücklich einen Fehler behebt.
- Kommentare werden nicht pauschal gelöscht: Ein Kommentar darf eine
  Designentscheidung, einen Workaround, eine Invariante oder eine externe
  Vertragsbedingung erklären. Reine Zeilen-Nacherzählung wird entfernt.
- Stubs werden nicht mit erfundenem Verhalten gefüllt. Entweder implementieren, als
  bewusstes Interface kennzeichnen oder die API entfernen.
- Unterdrückungen sind keine Reparatur: Sie brauchen eine Begründung und einen engen
  Scope und landen als Baseline-Eintrag oder `ai-slop-ignore`-Kommentar im Diff.
- Score, Herkunftsvermutung und Werkzeuglabel werden nie in Produktdaten, Audit-Logs
  oder die Benutzeroberfläche übernommen.

## 8. Abnahmekriterien

| # | Kriterium | Stand |
| --- | --- | --- |
| 1 | Der manuelle Scan läuft auf Windows und Linux mit identischer Konfiguration | Windows verifiziert; das Shell-Skript ist syntaxgeprüft, der Linux-Lauf folgt mit CI/VPS |
| 2 | Python- und Rust-Quelltext inklusive Kommentaren und Strings wird erfasst, generierte Pfade sind ausgeschlossen | umgesetzt (`stopslop.toml`) |
| 3 | Ein absichtlich eingefügter Artefakt der Stufe A wird reproduzierbar gefunden | umgesetzt (`tests/test_deslop.py`: 8 Python- und 5 Rust-Regeln) |
| 4 | Bestehende Befunde sind baselined, neue bleiben sichtbar | umgesetzt (23 Einträge, Selbstschrumpfung beim Beheben) |
| 5 | Ein Reparaturpass erzeugt einen nachvollziehbaren Diff und lässt die Gates laufen | offen – der erste Pass am Bestand steht aus |
| 6 | Die Dokumentation nennt Befunde Qualitätssignale und behauptet keine KI-Erkennung | umgesetzt (dieses Dokument) |

## 9. Offene Punkte

- Erster Reparaturpass: die 6 `SLOP006`-Stellen in `meadow/frontend/` sind bewusst
  abgefangen. Zu entscheiden ist, ob die Fehlerbehandlung sichtbar gemacht wird
  (Log statt stillem Schweigen) oder eine begründete Unterdrückung bleibt.
- Die 16 `SLOP009`-Befunde stammen aus Inline-Tests und dem Seed: entweder `example.com`
  als Testkonstante dokumentieren oder eigene Testwerte nutzen, damit der Scan ohne
  Baseline auskommt.
- Nach dem ersten Linux-Lauf: Rauschpegel der Stilgruppen für `docs/user/` prüfen.
- `ai-slop-detector` bleibt bis zur Auswertung in der Dev-Gruppe; danach entscheiden,
  ob es dort bleibt oder optional lokal installiert wird.
- SARIF-Ausgabe ist möglich, wird aber erst mit einem registrierten Forgejo-Runner
  angebunden (siehe Root-`AGENTS.md`, Abschnitt Verification).
- Struktur-Pass **nach** dem Artefakt-Pass: `form.py`, `grid.py` und `categories.py`
  sind die Score-Spitzen und gleichzeitig die größten Frontend-Module – ein
  Aufteilungskandidat, aber nur mit den Frontend-Tests als Netz und nicht als
  Nebeneffekt einer Artefakt-Reparatur.

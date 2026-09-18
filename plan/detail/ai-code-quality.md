# AI-Code-Qualitaet und Deslop-Workflow

- **Hauptdokument:** Abschnitt 12 ([Betrieb & Deployment](../meadow-pim-architektur.md#12-betrieb--deployment))
- **Relevante Phasen:** Phase 8, 9 (Qualitaetssicherung, bewusst gestartete Analyse und Reparatur)
- **Stand:** Planung; Recherche abgeschlossen am 2026-09-18

## 1. Ziel und Abgrenzung

Meadow PIM verwendet viel automatisch erzeugten Code. Ruff, Pylance, mypy, rustfmt,
Clippy und die Tests bleiben die primaeren Korrektheits- und Stil-Gates. Sie beweisen
aber nicht, dass eine Funktion wirklich angeschlossen ist, dass ein Kommentar kein
Chat-Rest ist oder dass eine plausible Implementierung nur ein Stub ist.

Der Deslop-Workflow soll deshalb zusaetzliche Hinweise liefern und Reparaturen
unterstuetzen. Er ist kein KI-Autorenschaftstest, kein Sicherheitsnachweis und kein
Ersatz fuer Tests oder menschliche Abnahme. Ein niedriger Score darf nicht als
Beweis fuer Laufzeitkorrektheit interpretiert werden.

## 2. Recherche und Werkzeugentscheidung

### 2.1 Primaerer Kandidat: `stopslop`

`stopslop` ist ein einzelnes Rust-Programm unter MIT-Lizenz. Es analysiert Python,
Rust, Go und TypeScript sowie Markdown/Text/HTML. Es untersucht AST-Strukturen,
Kommentare und String-Inhalte und laeuft deterministisch ohne LLM, API-Aufruf oder
Netzwerkzugriff. Fuer Meadow sind besonders relevant:

- Chat-Preambles, Elisionstexte und Markdown-Fences in Quelltext,
- Platzhalter-Credentials und andere offensichtliche Beispielwerte,
- leere oder nicht implementierte Koerper,
- breite bzw. verschluckende Fehlerbehandlung,
- Cross-Language-Leakage, Pass-through-Wrapper und Ein-Implementierung-Abstraktionen,
- ueberlange oder rein wiederholende Kommentare,
- Markdown- und Dokumentationsartefakte,
- optional unaufgeloeste Paket-Imports anhand von `pyproject.toml` und `Cargo.toml`.

Die Release-Version ist vor einer Einfuehrung zu pinnen und lokal gegen Meadow zu
kalibrieren. `--check-imports` bleibt zunaechst ein Hinweis, weil Workspace-Pfade,
optionale Abhaengigkeiten und dynamische Imports statische Fehlalarme erzeugen
koennen. Fuer bestehende Befunde ist eine Baseline moeglich; neue Tier-A-Artefakte
sollen danach sichtbar werden.

### 2.2 Python-Deep-Dive: `ai-slop-detector`

`ai-slop-detector` ist ein deterministischer Python-Analyzer mit optionaler
JavaScript-/Go-Unterstuetzung, aber ohne Rust-Analyse. Er liefert patternbezogene
Befunde und einen per-Datei-Deficit-Score, unter anderem fuer Placeholder-Koerper,
mutable Defaults, bare `except`, Phantom-Imports, tote Pipelines, Duplikate,
God-Functions und Type-Escape-Muster. Die Dokumentation nennt ausdruecklich die
Grenze: kein Laufzeitbeweis, keine kalibrierten Schwellen am ersten Tag und keine
Analyse von Rust.

Er wird als manuell gestarteter zweiter Blick fuer Python verwendet. Der Score ist
nur ein Priorisierungssignal. Die Ausgabe wird als JSON in einen gitignorierten
Arbeitsordner geschrieben; PowerShell-Umleitung nach JSON wird wegen der bekannten
UTF-8-Falle nicht verwendet.

### 2.3 Nicht als zusaetzliche Meadow-Gates

`sloppylint` ist ein Python-only-Projekt mit nuetzlichen Regeln, aber kleinerem und
zu `ai-slop-detector` ueberlappendem Umfang. `slopguards` ist ebenfalls ein
Python-only-Ergaenzungswerkzeug. Beide koennen bei einem konkreten Befund als
Vergleich herangezogen werden, sollen aber nicht parallel als dauerhafte Gates
laufen: Drei heuristische Python-Scanner wuerden vor allem doppelte Befunde und
unklare Schwellen erzeugen. Eine spaetere Aufnahme braucht einen dokumentierten
Mehrwert und eine False-Positive-Auswertung.

### 2.4 Warum nicht nur eines der Python-Werkzeuge

Der sprachuebergreifende Blindspot ist fuer Meadow wichtig: Rust-Code, Rust-
Dokumentation und Python-Kommentare muessen dieselbe Artefaktpruefung erhalten.
`stopslop` deckt diese Grenze ab; `ai-slop-detector` ergaenzt die Python-spezifische
Strukturanalyse. Dieses Paar ist kleiner und nachvollziehbarer als ein Sammelsurium
von Lintern.

## 3. Meadow-spezifischer Workflow

Der Workflow wird bewusst gestartet, nicht bei jeder Codegenerierung automatisch
repariert:

1. **Scope bestimmen:** `changed`/`staged` fuer eine lokale Aenderung oder ein
   kompletter Scan vor einem Meilenstein. `target/`, `.venv/`, generierte
   Artefakte, Backups und Standarddaten bleiben ausgeschlossen.
2. **Primaerscan:** gepinnte `stopslop`-Version mit JSON/SARIF-Ausgabe; bei Bedarf
   `--check-imports`. Die Ergebnisse werden nach `artifacts/` oder `.tmp/` geschrieben
   und nicht versioniert.
3. **Python-Vertiefung:** `ai-slop-detector` nur fuer Python-Quellcode und mit
   expliziter Ausgabe von Findings, Scan-Coverage und Score. Tests duerfen nicht
   ungeprueft als Produktionscode bewertet werden.
4. **Triage:** Befunde in echte Fehler, berechtigte Konstruktion, bestehender
   Bestand und Fehlalarm einteilen. Eine Unterdrueckung braucht einen konkreten
   Grund und soll lokal sowie regelbezogen sein.
5. **Kleine Reparaturpassagen:** zuerst Artefakte, Placeholder und verschluckte
   Fehler; danach tote Leitungen/Imports; zuletzt Struktur- und Verbosity-Hinweise.
   Keine grossen Umformulierungen und keine automatische Semantik-Aenderung.
6. **Verhaltensnachweis:** nach jedem Reparaturpass zuerst den engsten Test, danach
   `scripts/check.ps1 python` bzw. `scripts/check.sh python`; bei Rust zusaetzlich
   die Rust-Gates. Bei geaendertem API-/Datenvertrag kommen die betroffenen Tests
   und gegebenenfalls Live-Abnahme hinzu.
7. **Review-Abschluss:** vor/nachher vergleichen, verbleibende Befunde begruenden
   und den menschlichen Review auf die geaenderten Stellen konzentrieren.

Der Workflow repariert nicht automatisch aufgrund eines Scores. Ein fixer Score-
Schwellwert wird erst nach einem Baseline-Lauf und einer kleinen, manuell
klassifizierten Stichprobe erwogen. Tier-A-Artefakte koennen spaeter als harte
Aenderungs-Gate behandelt werden; heuristische Tier-B-Befunde bleiben zunaechst
Warnungen.

## 4. Geplanter Meadow-Baustein

Die Umsetzung soll einen kleinen manuellen Einstieg unter `scripts/` anbieten:

- `scripts/deslop.ps1` und `scripts/deslop.sh` mit Auswahl `changed|staged|all`,
  `--fix-plan` und `--strict`;
- gemeinsame, versionierte Tool-Konfiguration fuer Excludes, Baseline und
  projektbewusste Ausnahmen;
- JSON/SARIF-Berichte unter `.tmp/deslop/`;
- ein Exit-Code nur fuer explizit aktivierte harte Regeln, nicht fuer einen
  unkalibrierten Gesamtscore;
- keine automatische Datei-Reparatur im ersten Schritt. Ein spaeterer Fix-Modus
  darf nur klar mechanische Aenderungen vorschlagen, zeigt einen Diff und fuehrt
  danach die fokussierten Tests aus;
- ein Test-Set mit absichtlich eingebauten Artefakten fuer Kommentare, Strings,
  Placeholder, Stub, bare `except`, mutable default, Cross-Language-Muster,
  Phantom-Import und pass-through wrapper. Die Fixtures werden als Testmaterial
  gekennzeichnet und muessen den normalen Scan nicht stoeren.

Die Pin-Versionen gehoeren in `pyproject.toml` bzw. in eine Rust-Toolchain-/Installations-
Konvention. Ein globales `cargo install` ohne Pin ist fuer CI nicht ausreichend.
Die bestehende Python-Dev-Gruppe enthaelt die Kandidaten bereits; bis zur Umsetzung
bleiben sie dort experimentell und werden nicht automatisch von `scripts/check.*`
ausgefuehrt.

## 5. Qualitaetsregeln fuer Reparaturen

- Verhalten, oeffentliche APIs, Datenformate und Fehlermeldungen bleiben unveraendert,
  sofern die Reparatur nicht ausdruecklich einen Fehler behebt.
- Kommentare werden nicht pauschal geloescht: Ein Kommentar darf eine
  Designentscheidung, einen Workaround, eine Invariante oder eine externe
  Vertragsbedingung erklaeren. Reine Zeilen-Nacherzaehlung wird entfernt.
- Stubs werden nicht mit erfundenem Verhalten gefuellt. Entweder implementieren,
  als bewusstes Interface kenntlich machen oder die API entfernen.
- Unterdrueckungen sind keine Reparatur. Sie brauchen Begruendung und einen engen
  Scope.
- Score, Herkunftsvermutung und Tool-Label werden nie in Produktdaten, Audit-Logs
  oder die Benutzeroberflaeche uebernommen.

## 6. Abnahmekriterien fuer die Umsetzung

1. Der manuelle Scan funktioniert auf Windows und Linux mit identischer Konfiguration.
2. Python- und Rust-Quelltext sowie Kommentare/Strings werden erfasst; generierte
   und vendorisierte Pfade sind explizit ausgeschlossen.
3. Ein absichtlich eingefuegter Tier-A-Artefakt wird reproduzierbar gefunden.
4. Bestehende Befunde koennen baselined werden, neue Befunde bleiben sichtbar.
5. Ein Reparaturpass erzeugt einen nachvollziehbaren Diff und laesst den fokussierten
   Test sowie die relevanten Meadow-Gates erfolgreich laufen.
6. Die Dokumentation nennt Befunde als Qualitaetssignale und behauptet keine
   sichere Erkennung KI-generierten Codes.

## 7. Offene Punkte

- Nach dem ersten Vollscan: Welche `stopslop`-Regeln sind fuer Rust-Makros,
  Test-Harnesses und Meadow-Dokumentation zu noisy?
- Soll SARIF spaeter in Forgejo Code Scanning oder zunaechst nur als Artefakt
  abgelegt werden? Der aktuelle Forgejo-Runner ist noch nicht registriert.
- Welche bestehenden Befunde sind akzeptierter Bestand und verdienen eine
  Baseline, und welche sollten vor einer Baseline repariert werden?
- Erst nach dieser Auswertung entscheiden, ob `ai-slop-detector` dauerhaft in die
  Dev-Gruppe gehoert oder als optionales lokales Werkzeug installiert wird.

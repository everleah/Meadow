# AI-Code-Qualität und Deslop-Workflow

- **Hauptdokument:** Abschnitt 12 ([Betrieb & Deployment](../meadow-pim-architektur.md#12-betrieb--deployment))
- **Relevante Phasen:** Phase 8, 9 (Qualitätssicherung, bewusst gestartete Analyse und Reparatur)
- **Stand:** Stufe 1 umgesetzt, drei Reparaturpasses gefahren und die Typ-Prüfung als Gate bis Stufe 4 ausgebaut (2026-09-18): Konfiguration `stopslop.toml` (ohne Baseline), `scripts/deslop.ps1`/`deslop.sh`, Fixtures, `tests/test_deslop.py`; sechs verschluckte Fehler sichtbar gemacht, ein Weiterschleifer entfernt (§4.2), Platzhalterwerte aufgelöst (§4.4), acht Typ-Ausweichungen entfernt (§4.5), `mypy` als Gate für 131 Dateien – **`meadow` inkl. Frontend, `tests/` und `scripts/` vollständig, mit `check_untyped_defs`** (§4.6–§4.9). Der Scan meldet **null** Befunde; der Struktur-Umbau der Produktseite ist mit einem AST-Messwerkzeug und Seitentests begonnen (§4.11).

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
  Einstellung.
- **Konfiguration:** akzeptiert werden `select`, `extend-select`, `ignore`,
  `extend-ignore`, `exclude`, `check-imports`, `baseline`, `per-file-ignores`,
  `custom-rule`. Unbekannte Schlüssel brechen den Lauf ab, deshalb wird jeder
  Schlüssel gegen die installierte Version geprüft – nicht gegen die README.
- **Baseline:** `--write-baseline`/`--baseline` schreiben bzw. lesen
  `{"accepted": {"<code>|<pfad>|<meldung>": <anzahl>}}`. Der Fingerabdruck enthält
  keine Zeilennummern: eine Bearbeitung oberhalb eines Befunds lässt ihn nicht
  wiederauferstehen, und wer einen Befund behebt, schrumpft das Budget von selbst.
  Eine leere Baseline (`{"accepted": {}}`) zeigt den vollen Stand. **Meadow nutzt sie
  bewusst nicht** – siehe §4.4.
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
2. **Die verbleibenden Tier-A-Befunde wurden vollständig aufgelöst.** Zu Beginn waren
   es zwei Gruppen: 16 × `SLOP009` (`example.com`-Werte in Rust-Inline-Tests und im
   Seed) und 6 × `SLOP006` (bewusst abgefangene Degradierungen in
   `meadow/frontend/`). Die sechs `SLOP006`-Stellen behob der erste Reparaturpass
   (§4.2), die 16 `SLOP009`-Stellen der zweite (§4.4). Es gibt deshalb **keine
   Baseline** mehr: Der Scan ist scharf, jeder Befund der Stufe A scheitert sofort.

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

### 4.2 Erster Reparaturpass (2026-09-18)

Kategorie „Artefakte, Placeholder und verschluckte Fehler“ zuerst, wie im Workflow
festgelegt. Die sechs Stellen waren bewusst abgefangen (`pass`), damit die
Oberfläche bei einem unglücklich erreichbaren Kern nicht scheitert – nur eben
**unsichtbar**. Der Pass macht sie nach dem bestehenden Hausmuster aus
`meadow/frontend/object_picker.py` diagnostizierbar, ohne das Verhalten zu ändern
(additiv: `log = logging.getLogger(__name__)` plus eine Warnung, kein geänderter
Kontrollfluss):

| Datei | Stelle | Meldung |
| --- | --- | --- |
| `meadow/frontend/connectors.py` | 4 × Optionen der Konnektor-Formularfelder (Objekttypen, Attributnamen, Sprachen, Kanäle) | `log.warning("connector form: … unavailable")` |
| `meadow/frontend/form.py` | Kanalfreigaben des Objekts | `log.warning("object %s: channel releases unavailable")` |
| `meadow/frontend/grid.py` | Collection-Refresh der Sammlungsansicht | `log.warning("collection refresh failed, keeping the shown data")` |

Der Fund in `form.py` ist der wichtigste: Eine nicht geladene Kanalfreigabe wurde
stillschweigend als „nicht freigegeben“ dargestellt – eine **falsche** Anzeige, nicht
nur eine fehlende. Sie bleibt jetzt im Log erklärbar.

Zusätzlich behoben: der Strukturbefund `SLOP039` – `failed_result` war nur ein
Weiterschleifer auf `failed_at`, zwei Namen für dieselbe Funktion, während
`failed_at` bereits elf Aufrufer hatte.

**Ergebnis:** `stopslop` meldet über den Quellbaum keine `SLOP006`- und keine
`SLOP039`-Stelle mehr (23 → 17 Befunde, davon damals 16 in der Baseline). Die 16
`SLOP009`-Einträge löst der zweite Pass auf (§4.4).

**Nachweis:** `ruff check`, `ruff format --check`, `cargo fmt --check`,
`cargo clippy --all-targets -- -D warnings`, `cargo test -p pim-core --lib`
(173 Tests), `pytest` (561 Tests), `stopslop .` = `exit 0`. Der **volle**
`cargo test`-Lauf (und damit `scripts/check.ps1`) braucht den gestoppten Dev-Kern,
weil Windows ein laufendes `pim-core.exe` beim Relink sperrt. Mit gestopptem Kern
lief `scripts/check.ps1` danach **vollständig grün** (fmt, clippy `-D warnings`,
173 Cargo-Tests, ruff, 561 pytest).

### 4.3 Kategorie „unaufgelöste Imports“ (`SLOP010`): bewusst nicht verwendet

`stopslop . --check-imports` über den Quellbaum: **271 Befunde**, davon **256 für
unser eigenes Paket `meadow`** – der statische Namensabgleich hält das Projekt
selbst für eine fehlende Abhängigkeit. Die restlichen 15 verteilen sich so:

| Paket | Befunde | Bewertung |
| --- | --- | --- |
| `nats` | 7 | Fehlalarm: die Distribution heißt `nats-py`, das Import-Modul `nats` |
| `fastapi` | 4 | echt, aber bewusst: NiceGUI baut darauf auf, und `from fastapi import Request` ist das dokumentierte Muster für Seitenhandler (`auth.py`, `export_page.py`, `grid.py`) |
| `starlette`, `engineio`, `botocore` | je 1 | transitive Rahmen-Abhängigkeiten von NiceGUI bzw. `boto3`; die Nutzung ist typengebunden (`BaseHTTPMiddleware`, Logging-Stille, `Config`) |
| `playwright` | 1 | Fehlalarm: steht in der Dev-Gruppe, die der Import-Check nicht kennt |

**Konsequenz:** `--check-imports` gehört **nicht** in den Workflow. Das Signal-
Rausch-Verhältnis ist mit 15 brauchbaren von 271 Befunden unbrauchbar, und die
tatsächliche Import-Auflösung leisten Ruff, Pylance und der Compiler bereits
korrekt. Die vier transitiven Rahmen-Abhängigkeiten bleiben bewusst so (eine
explizite Deklaration ist eine Abhängigkeits-Entscheidung, kein Deslop-Fund) und
stehen als offener Punkt in §9.

### 4.4 Zweiter Reparaturpass (2026-09-18)

Zwei Posten aus §9, beide klein und ohne Verhaltensänderung:

**(a) Die Platzhalterwerte sind aufgelöst.** Die 16 `SLOP009`-Befunde waren
`example.com`-Werte in Rust-Inline-Tests (12 in `attribute_values.rs`, 3 in
`attribute_definition_types.rs`) und die Seed-Adresse `admin@example.com`. Sie sind
jetzt die reservierten `.invalid`-Namen `shop.invalid`, `laden.invalid` und
`admin@meadow.invalid` – ebenfalls synthetisch, aber nicht mehr die Platzhalter
einer Vorlage, und ohne die Aussage der Tests zu verändern (geprüft wird, dass
absolute `http(s)`-URLs gelten; welche Domain, ist beliebig). Vorab gemessen:
`https://shop.invalid/x` löst `SLOP009` nicht aus.

**(b) Die Baseline ist damit abgeschafft.** `.stopslop-baseline.json` ist gelöscht,
der Schlüssel `baseline` aus `stopslop.toml` entfernt und `--strict` aus beiden
Skripten gestrichen (ohne Baseline gibt es nichts zu ignorieren – ein Schalter, der
nichts tut, wäre schlechter als keiner). `tests/test_deslop.py` prüft jetzt das
Gegenteil: Es gibt **keine** Grandfather-Datei, und der Repository-Scan ist nur grün,
solange kein Befund der Stufe A existiert. Wird doch einmal etwas akzeptiert, braucht
das den Schlüssel, die Datei und eine Begründung hier.

**(c) Die degradierenden Options-Quellen sind prüfbar.** Der Pass aus §4.2 machte die
sechs Fehlerpfade sichtbar, aber kein Test berührte sie – `meadow/frontend/connectors.py`
hatte **gar keine** Abdeckung. Die vier optionalen Options-Quellen der Konnektor-Seite
liegen jetzt als `load_option_sources(client, token)` vor (Hausmuster wie
`object_picker.py`: reine Helfer sind testbar, Seiten nicht), und
`tests/test_connector_form.py` deckt beide Richtungen ab – die Zuordnung samt Filterung
(leere Namen, technische `root`-Sprache, inaktive Sprachen) und den Degradationsfall,
in dem jede der vier Warnungen im Log erscheint (`caplog`).

**Ergebnis:** `stopslop .` meldet **0 Befunde** (Exit 0). **Nachweis:**
`scripts/check.ps1` komplett grün – cargo fmt, clippy `-D warnings`, 173 Cargo-Tests,
ruff check + format, **563** pytest (die zwei neuen Tests inklusive).

### 4.5 Dritter Pass: Typ-Ausweichungen (2026-09-18)

Der Deep-Dive nennt als häufigste Befundart `lint_escape` (91 über die fünf größten
Module) – und das ist ein echter Fund, kein Stilrauschen: **33 `# type: ignore` und
231 `# noqa: BLE001` in `meadow/`**. Der wichtigere Teil davon ist eine Lücke in der
Verifikation: `mypy` steht in der Dev-Gruppe, hat aber **keine Konfiguration und kein
Gate** – die Type-Ignores prüft also niemand, weder Ruff noch Pytest noch die CI.

Alle 33 gesichtet, drei Kategorien:

| Kategorie | Anzahl | Ursache | Vorgehen |
| --- | --- | --- | --- |
| veraltet oder überflüssig | 3 | die Annotation erlaubt den Wert längst (`dict \| None`) | entfernen |
| Optional-Guard, den der Prüfer nicht durchblickt | 2 | `(x or {}).get(...)` bzw. `x[key]` nach `for k in sorted(x or {})` | einmal lokal binden (`mapping = x or {}`) |
| NiceGUI-Typing (Handler-Signaturen, `attr-defined`) | 28 | `on_click=lambda …`, Zustand als Attribut am Button-Objekt | benannte Handler mit korrekter Signatur; Zustand als `nonlocal`-Variable |

**In diesem Pass behoben (8 Ignores, verhaltensneutral und typgeprüft):**

- `connectors.py`, `exports.py`: zwei überflüssige Ausweichungen entfernt.
- `jobs.py`: `_parse_parameters` deklariert jetzt `dict | None` – der Aufrufer prüft
  bereits auf `None`.
- `grid.py`: zwei Optional-Guards lokal gebunden (`prefixes`, `mapping`).
- `rules.py` und `jobs.py`: „welcher Datensatz wird bearbeitet“ hing als Attribut am
  Button-Objekt (`save_rule.editing_id`, gelesen per `getattr`) – jetzt eine
  `nonlocal`-Variable, wie `users.py` es schon macht.

**Nachweis:** `ruff check`/`ruff format` sauber, **563** pytest, und der Typprüfer
meldet für die geänderten Dateien **keine** Fehler – die entfernten Ignores waren
also tatsächlich überflüssig. Stand: **33 → 25** Type-Ignores.

**Nächste Inkremente (bewusst nicht in diesem Pass):**

- die verbleibenden 25 Type-Ignores (`grid.py` 9, `categories.py` 5,
  `attribute_definitions.py` 4, `search_form.py` 3, `users.py` 2, `object_types.py` 1,
  `layout.py` 1);
- die 231 `# noqa: BLE001`: hier ist die Richtung **verengen statt unterdrücken** –
  die Aufrufer können auf `CoreError` (HTTP-Antwort) plus `httpx.HTTPError`
  (Transport) verengt werden. Das ändert aber das Verhalten in Randfällen: ein
  Programmierfehler im `try` würde dann sichtbar statt still degradiert. Deshalb
  Entscheidung pro Stelle, nicht in einem Rutsch;
- **die eigentliche Strukturarbeit:** `products_page` in `grid.py` hat **2323
  Logikzeilen und Komplexität 445** (`inherited_cells` 14, `attribute_column_specs`
  12). Das ist kein Nebeneffekt, sondern ein eigener, mehrstufiger Umbau: erst reine
  Helfer herausziehen und mit Tests belegen (Muster `load_option_sources`, §4.4c),
  dann Abschnitte der Seite in Funktionen mit explizitem Zustand überführen – Seite
  für Seite, jeweils mit den Frontend-Tests als Netz.

### 4.6 Typ-Prüfung als Gate, Stufe 1 (2026-09-18)

Anlass war §4.5: **33 `# type: ignore` in `meadow/`, die niemand prüft.** Statt sie
einzeln zu durchforsten, prüft ein Gate sie jetzt – und macht die noch nötigen
Ausweichungen rechenbar. Der Weg dahin war eine Messung:

| Schritt | Fehler | Erkenntnis |
| --- | --- | --- |
| mypy (Standard) über `meadow/frontend` | 361 | 134 stammten aus **einer** falschen Annotation: `_api_client` ist ein async generator, war aber als `httpx.AsyncClient` deklariert – damit war jede `async with … as client`-Stelle untypisiert |
| nach dieser Korrektur | 227 | ein Fix, ein Drittel der Fehler weg |
| `meadow` gesamt, ohne die zwei NiceGUI-Codes | 89 | langer Rest, max. 9 je Datei |
| Nicht-UI-Teil (`worker`, `rules`, geteilte Module) | 7 → **0** | 6 fehlende Fremd-Stubs, 1 echter Signaturfehler (`_emit_quantified` bekam `Mapping`, verlangte `dict`) |

**Entscheidung:** Das Gate startet mit den Modulen, die heute sauber sind –
`meadow/worker`, `meadow/rules`, `meadow/tabular.py`, `urls.py`, `mef.py` (27
Dateien). Der Scope steht in `[tool.mypy] files` in `pyproject.toml`, damit
`scripts/check.*`, der lokale Lauf (`uv run mypy`) und diese Dokumentation nicht
auseinanderlaufen. Ein Modul kommt dazu, sobald es sauber ist; **die UI-Module folgen
später**, weil dort 144 Fehler aus NiceGUIs dynamischer Element-API stammen
(`.set_value` auf `Element`) und der Rest eine lange Kette ist.

**Konfiguration (`[tool.mypy]`):** `warn_unused_ignores` ist der Kern – ein
`# type: ignore`, das nichts mehr unterdrückt, ist eine Lüge im Code und fällt jetzt
auf. Dazu `warn_redundant_casts`, `warn_unused_configs`, `show_error_codes` und
`ignore_missing_imports` für Bibliotheken ohne Stubs (`openpyxl`, `lxml`, `boto3`,
`botocore`; `markdown` folgt mit dem Frontend-Scope, sonst meldet mypy die Ausnahme
selbst als ungenutzt).

**Umgang mit Ausweichungen:** Nicht jede ist ein Codeproblem – manche Behandlung wäre
teurer oder sinnlos (dynamische NiceGUI-Elemente, untypisierte Fremdmodule). Solche
Ausweichungen **bleiben**, sollen aber den Fehlercode nennen
(`# type: ignore[assignment]` statt eines nackten Ignores), damit sichtbar ist, was
unterdrückt wird; `warn_unused_ignores` räumt sie wieder weg, sobald sie überflüssig
werden. Im aktuellen Gate-Scope gibt es derzeit keine.

**Nachweis:** `scripts/check.ps1 python` grün – `Success: no issues found in 27
source files`, ruff check/format sauber, 563 pytest, jeweils Exit 0.

**Nächste Stufen:** §4.7 (Frontend modulweise) und danach `tests/`, `scripts/`,
`check-untyped-defs = true`.

### 4.7 Typ-Prüfung, Stufe 2: Frontend modulweise (2026-09-18)

Stufe 1 deckte die Nicht-UI-Module ab; Stufe 2 holt das Frontend dazu, in Schritten:
zuerst **48**, dann **72 Dateien** (27 Nicht-UI + 45 Frontend, grün). Die vier
schwersten Seiten und `scripts/` folgten in §4.8 (84 Dateien).

**Eine Einstellung war dafür entscheidend:** `follow_imports = "silent"`. mypy
analysiert importierte Module mit – ohne den Schalter hätte jede neu aufgenommene
saubere Datei die Fehler aller Module gemeldet, die sie importiert, und der Scope
hätte nie wachsen können. So werden nur die gelisteten Dateien gemeldet: ein noch
nicht aufgenommenes Modul bleibt ungeprüft, statt das Gate zu blockieren.

**Zwei Funde mit großem Hebel** – beide vom selben Typ, einer zu schwachen Signatur
einer geteilten Hilfsfunktion:

| Stelle | Wirkung |
| --- | --- |
| `_api_client`: `AsyncIterator[…]` statt `httpx.AsyncClient` | 134 Folgefehler weg |
| `multilingual_value_editor`: `ui.input` statt `ui.element` | Fehler weg **und** 6 Ignores wurden überflüssig |

**19 der 25 `# type: ignore` waren überflüssig** – bewiesen durch
`warn_unused_ignores`, und entfernt. Der Zähler steht damit bei **33 → 6**; die
sechs verbliebenen sind der Button-Zustand in `attribute_definitions.py` (4) und
`users.py` (2), den erst ein Umbau auf `nonlocal`-Variablen (Muster aus §4.5)
auflöst.

**Wichtig: die beiden Prüfer widersprechen sich.** Zwei der entfernten Ignores
brauchte mypy nicht, **Pylance aber schon**: Bei `lambda n=hit: …` (dem
Default-Argument-Trick gegen spätes Binden) typisiert Pylance den Parameter nach
der erwarteten Handler-Signatur, sodass `n["id"]` scheitert. Ersetzt durch
`partial(pick_hit, hit)` mit typisierten Helfern – und dabei fiel auf, dass die
Helfer **`async` sein müssen**: `select_category`/`show_in_tree` sind Coroutinen,
der Lambda-Trick gab sie zurück (NiceGUI awaited sie), mein erster Entwurf rief sie
ohne `await` auf und hätte sie nie ausgeführt. **Pylance fand das, mypy nicht** –
beim Entfernen einer Ausweichung lohnt deshalb der Blick in beide Prüfer.

**Die wiederkehrenden Muster im UI-Layer** – sie machen den Großteil der 217
Frontend-Fehler aus, und jede Zeile war ein echter Typfehler, keine Ausweichung:

| Muster | Häufig | Reparatur |
| --- | --- | --- |
| `_fill_editor(record: dict \| None)` prüft `editing = record is not None` und liest dann `record.get(…)` | ~46 | direkt nach dem Flag `record = record or {}` binden – mypy verbindet ein einfaches `bool` nicht mit der Verengung |
| Zustandsmaps als `dict[str, object]` (JSON-artig) | 12 | `dict[str, Any]` (die Werte sind heterogen, das ist der Vertrag) |
| Element-Variablen und -Container als `ui.element` deklariert | 21 | konkret typisieren (`ui.input`, `ui.checkbox`, `ui.select`) oder als Union der Wert-Widgets; `ui.element` ist der Basistyp **ohne** `value`/`set_value` |
| `lambda w=widget: …` (Default-Argument bindet das Widget) | 8 | Helfer + `partial`; ein Lambda mit Default-Argument lässt sich nicht typisieren („Cannot infer type of lambda“) |
| dieselbe Variable für zwei Widget-Typen (`Number` → `Select`) oder zwei Ergebnisformen (`dict` → `list`) | 4 | getrennte Namen, oder die Variable vor der Kette deklarieren |
| `list[Mapping[str, object]]` als Parameter, `list[dict]` beim Aufruf | 1 | `Iterable[Mapping[str, object]]`: `list` ist invariant und `dict` ist kein `Mapping` – mit `Iterable` prüft mypy nur die Elementbeziehung |
| verengte Optionals in verschachtelten Funktionen (NiceGUI ruft sie später auf) | 4 | vor dem `def` lokal binden oder im selben `if` auf die Quelle prüfen (`if taxonomy_select is not None and taxonomy is not None`) |
| Reihenfolge: eine verschachtelte Funktion liest Widgets, die erst später entstehen | 2 | erst die Widgets bauen, dann die Funktion definieren, dann die Handler anhängen |

Zwei NiceGUI-Eigenschaften halfen dabei: `set_value` gibt das Widget zurück
(Kettenaufruf), ein Setter muss den Wert also verwerfen, um `Callable[[Any], None]`
zu erfüllen; und `ui.input` & Co. sind über NiceGUIs Lazy-Registry **Klassen**, in
Annotationen also verwendbar. **Damit ist die in §4.6 offene Frage entschieden:
NiceGUIs Element-Typing wird geprüft**, und die Reparatur ist die konkrete
Typisierung unserer eigenen Element-Variablen statt einer dokumentierten Grenze.

**Weiter:** die vier schwersten Seiten und `scripts/` in §4.8.

### 4.8 Typ-Prüfung, Stufe 3: Frontend vollständig, `scripts/` dazu (2026-09-18)

Damit ist der Frontend-Scope **abgeschlossen**: alle 49 Module laufen fehlerfrei,
dazu die acht Python-Dateien unter `scripts/` - zusammen **84 Dateien** im Gate
(`Success: no issues found in 84 source files`), ohne eine einzige neue Ausweichung.
Die 217 Frontend-Fehler aus §4.6/§4.7 sind auf **0** gefallen, `scripts/` hatte 9.

**Was diese Runde zusätzlich gezeigt hat:**

| Fund | Reparatur |
| --- | --- |
| `_is_multilingual_map(value: object) -> bool` filterte JSON-Werte, danach konnte mypy nicht auf die Map zugreifen | Prädikat als `TypeGuard[dict[str, Any]]` - die Verengung wirkt dann in jeder Verzweigung, ohne `cast` |
| `common = {...}` und dann `**common` in einen getypten Kern-Aufruf | `common: dict[str, Any]`: die Nutzlast ist heterogen (Text, Flags, Listen, bereinigte Zusatzfelder), das ist ihr Vertrag |
| Ein behobener Fehler deckte **sieben** weitere auf (`dict(object)` verdeckte `**common`) | nach jeder Reparatur prüfen, ob eine Folgeschicht sichtbar wird - mypy meldet nach einem Fehler in einem Ausdruck dessen Typ nicht mehr |
| dieselbe Variable in einem Scope für zwei Dinge (`target` als Widget und als Treffer-Dict, `key` als Tupel und als Text, `node` als Schleifen- und Lookup-Variable) | getrennte Namen; mypy fand dabei einen **echten Artefakt-Fehler**: in `scripts/demo_data.py` stand die Initialisierung von `chunk_nodes`/`chunk_references` doppelt |
| `_no_children(_node_id: …)` an eine Variable zugewiesen, die vorher `load_children(node_id: …)` hielt | die Variable beschreibt eine Funktion **mit Parameternamen** - sie einmal als `Callable[[str], Awaitable[list[dict]]]` annotieren |
| `filter_select(on_change=…)`: ein Aufrufer deklariert den Parameter als `str`, ein anderer als `Any` | `Callable[[Any], object \| Awaitable[object]]` - die Gestalt des Werts entscheidet der Aufrufer (einfach oder mehrfach) |

**Nebenbefund zur Sprache:** `except TypeError, ValueError:` ohne Klammern sieht wie ein
Python-2-Fehler aus, ist aber seit **Python 3.14 (PEP 758)** gültig.

**Nächste Stufen:** keine mehr — der Scope ist vollständig. Eine neue NiceGUI- oder
Kern-API wird ab jetzt mitgeprüft; ein neuer `# type: ignore` fällt im Gate auf.

### 4.9 Typ-Prüfung, Stufe 4: `tests/` und die Schärfung (2026-09-18)

Jetzt ist der **gesamte Python-Baum** im Gate: **131 Dateien** (84 aus §4.6–§4.8 +
47 Testdateien). `tests/fixtures/` bleibt über `exclude = "^tests/fixtures"` ausgenommen —
dort liegt der absichtlich kaputte Deslop-Sample.

**Der Hebel war wieder eine Wurzelannotation, diesmal eine systematische.** Von den 121
Fehlern in 20 Testdateien waren rund **86 dieselbe Meldung**:
`Value of type "dict[str, Any] | None" is not indexable`. Ursache: **alle sieben
Job-Handler-Builder** (`bulk`, `business_rules`, `collections`, `config_import`,
`connector_test`, `exports`, `imports`) deklarierten `-> JobHandler`, und `JobHandler`
erlaubt `None` („ein Handler darf nichts zu berichten haben"). Ihre innere Funktion war
aber durchweg schon `async def handle(...) -> dict[str, Any]` annotiert. Ein neuer Alias
`ReportingJobHandler` (`jobs.py`) hält den engeren Vertrag fest, die sieben Builder wurden
darauf umgestellt. **Lehre: wenn dieselbe Meldung 86-mal auftaucht, ist nicht der Test
falsch, sondern die Signatur** — ~86 lokale Guards in Tests wären die falsche Reparatur
gewesen.

**Zwei Protokolle statt eines `Any`-Loches:** Ein Test-Double macht sichtbar, wie schmal
eine Abhängigkeit wirklich ist. `load_option_sources` liest vier Client-Aufrufe →
`OptionSourceClient` (`connectors.py`); `process_messages` ruft nur `handle` →
`MessageRunner` (`jobs.py`). Der Stub wird damit **geprüft**: fehlt künftig eine Methode,
scheitert das Gate statt die Erwartung.

**Die Kleinteile:** Test-Literale brauchten Annotationen (`_DEFINITIONS`, `definitions`,
`objects`, `empty`, `product`) — aus heterogenen Dict-Literalen leitet mypy `object` ab;
ein Monkeypatch-Zähler braucht `*args: Any` statt `*args: object` (sonst ist der Aufruf der
echten Funktion nicht typisierbar), und dadurch fielen **zwei** `# type: ignore` in
`test_rules_sandbox.py`; `assert spec is not None` / `assert buffer is not None` /
`assert STOPSLOP is not None` lösen verschleppte Optionals ab; die `ast.NodeVisitor`-
Überschreibungen brauchen `ast.FunctionDef | ast.AsyncFunctionDef` (bzw. `With | AsyncWith`),
weil TypShed dem Slot den Async-Typ gibt; ein Test-Stub musste die echte
`list_channels`-Signatur spiegeln (`active: bool | None = None`), damit er das Protokoll
erfüllt.

**Die Schärfung kostete nichts:** `check_untyped_defs = true` meldete über alle 131 Dateien
**0** Fehler (vorher gemessen) und ist jetzt in der Konfiguration — der modulweise Aufbau
hat die untypisierten Rümpfe also schon mitgeräumt.

**Stand:** `Success: no issues found in 131 source files`, 563 pytest,
`scripts/deslop.ps1 all` = 0 Befunde. Im Scope liegen **11 `# type: ignore`** (4
`attribute_definitions.py`, 2 `users.py`, 5 `test_search_form.py`) — alle notwendig, was
`warn_unused_ignores` in jedem Lauf beweist.

### 4.10 `# noqa: BLE001` verengen: die Entscheidungsregel (2026-09-18)

Die 234 `except Exception`-Stellen sind der letzte große Posten aus §4.5. Ziel ist nicht
„überall dasselbe“, sondern pro Stelle eine Aussage über die **tatsächliche Fehlerfläche**.
Die Prüfung ergab vier Kategorien mit klarer Entscheidung:

| Kategorie | Beispiel | Entscheidung |
| --- | --- | --- |
| **Kern-Aufruf** (die Mehrheit) | `objects = await client.list_objects(…)` | `except CoreError, httpx.HTTPError:` – das ist die vollständige Fläche (Status ≥ 300 vs. Transport/Decoding). Lokale Nachbearbeitung im `try` heißt bewusst: ein Programmierfehler wird **sichtbar**, statt leer zu degradieren (Root-`AGENTS.md`: „ein neuer Vertrag wird direkt genutzt; fehlt er, ist das ein Fehler und wird sichtbar gemeldet“) |
| **Browser-Bridge** (AG Grid) | `await grid.get_selected_rows()`, `run_grid_method(…)` | **bleibt breit**, Grund benannt („die AG-Grid-Bridge hat keinen engen Vertrag“): NiceGUI typisiert den JS-Roundtrip nicht, ein engeres `except` wäre geraten |
| **Absichtliche Isolation** (Worker) | `JobRunner.handle` (jede Handler-Ausnahme ⇒ Job fehlgeschlagen + Retry), Batch-Isolation in `imports.py`, DLQ-Publish | **bleibt breit**: eine Verengung würde den Consumer statt den Job scheitern lassen. Diese Stellen werden nur geprüft, nicht geändert |
| **JSON-Körper des Kerns** | `response.json().get("error")` in `api.py` | `except ValueError, AttributeError:` (kein JSON vs. JSON ohne Objekt) |

**Werkzeug-Lehren:** `ruff format` schreibt `except (A, B):` in die PEP-758-Form
`except A, B:` um (Zielversion 3.14) – und `B025` deckte dabei eine echte Doppelung auf: in
`form.py` stand `CoreError` in zwei Klauseln desselben `try`, dort bleibt nur
`httpx.HTTPError`. **PEP 758 verlangt aber Klammern, sobald `as` im Spiel ist:**
`except A, B as e:` ist ein Syntaxfehler, `except (A, B) as e:` korrekt (in `connectors.py`
zuerst falsch geschrieben, `ruff`/mypy fanden es sofort). Außerdem brauchen **mehrzeilige
Ersetzungen die exakte Einrückung** (einzeilige sind tolerant); bei 13 identischen Blöcken in
`api.py` ist eine Einzel-Ersetzung deshalb nicht möglich.

**Die Verengung hat einen Test-Double entlarvt:** `_OptionClient` in
`tests/test_connector_form.py` warf `RuntimeError("core unavailable")`, der echte Client
wirft `CoreError` – der Degradationspfad war also mit einem Fehler getestet, den der Kern
nie erzeugt. Der Stub wirft jetzt `CoreError` und sagt das im Docstring. Zweite Bestätigung
der Lehre „ein Test-Double macht die schmale Schnittstelle sichtbar“ (§4.9).

**Stand:** 234 → **38** Ausweichungen; jede verbliebene Stelle ist eine **benannte breite Grenze**. Erledigt: `grid.py` 33 → 3,
`form.py` 23 → 0, `attribute_definitions.py` 16 → 2, `api.py` 15 → **0**,
`connectors.py` 12 → 2, `categories.py` 11 → 0, `saved_searches.py` 9 → 2, `rules.py` 9 → 1,
`relation_definitions.py` 7 → 1, `export_page.py` 6 → 0, `object_types.py` 6 → 1,
`users.py` 6 → 1, `logs.py` und `object_picker.py` je 4 → 0,
`channels.py`/`enum_sets.py`/`units.py` je 3 → 0 und die kleinen Seiten `views.py` 3 → 0,
`export_profiles.py` 3 → 0, `import_page.py` 3 → 1, `config_page.py` 2 → 0,
`languages.py` 2 → 0, `settings.py` 2 → 0, `layout.py`/`auth.py`/`password.py` je 1 → 0.
Im Frontend bleiben nur die AG-Grid-Bridges (`grid.py` 3, `jobs.py` 3,
`attribute_definitions.py` 2, `saved_searches.py` 2,
`connectors.py`/`object_types.py`/`relation_definitions.py`/`users.py`/`rules.py` je 1),
die Bridge `lazy_tree.py` (1) und der Datei-Dekoder in `import_page.py` (1).

**Zwei weitere Werkzeug-Lehren:** (a) **`contextlib.suppress(Exception)` meldet `BLE001`
nicht** – zwei Stellen (`object_types.py`, `relation_definitions.py`) waren in der Zählung
unsichtbar und wurden erst über eine eigene Suche gefunden; sie sind jetzt
`contextlib.suppress(CoreError, httpx.HTTPError)`. (b) Die 13 identischen Blöcke in `api.py`
waren einzeln nur über einen **eindeutigen Anker** erreichbar (die Request-Zeile darüber);
sie rufen jetzt geschlossen `self._error_detail(response)` auf.

**Die 38 verbliebenen Stellen (bewusst breit, jede mit benanntem Grund):** Worker **21** –
`imports.py` 5 (Batch-/Release-/Objekt-Isolation), `webhooks.py` 5 (Zustellung ⇒ Retry/DLQ,
injizierter Loader), `rules_service.py` 3 (jede Auswertung muss verneinen ⇒ fail-closed),
`jobs.py` 2 (Handler-Ausnahme ⇒ Job fehlgeschlagen + Retry; DLQ), `exports.py` 2 (nicht
erreichbarer Mailserver/Objektspeicher darf den Export nicht scheitern lassen), `bulk.py` 2
(ein schlechtes Objekt bzw. eine Wurzel darf den Lauf nicht abbrechen), `log_sender.py` 1,
`business_rules.py` 1; Frontend **17** – die AG-Grid-Bridges und zwei Sonderfälle
(`lazy_tree.py`: injizierter Loader; `import_page.py`: CSV/XLSX/JSON-Dekoder).

**Der Nebengewinn der letzten Runde war der größte:** die Verengung schrieb die
*tatsächlichen* Fehlerklassen der Nachbarschaft fest – `csv.Error` (Sniffer), `ValueError`
(JSON, `datetime.fromisoformat`; bei `json.dumps` zusätzlich `TypeError`), pydantic
`ValidationError` (`model_validate*`), botocore `ClientError` (Bucket-HEAD/CREATE),
`playwright.sync_api.Error`. Und sie fand einen **toten Schutz**: `connector_test.excerpt`
fing breit ab, obwohl httpx mit `errors="replace"` dekodiert (gemessen:
`b"\xff\x00\xfe\x01"` ⇒ Ersatzzeichen statt Ausnahme) – der Guard ist entfernt,
`response.text` kann dort nicht werfen.

### 4.11 Struktur-Umbau `products_page`: das Netz zuerst (2026-09-18)

§4.5 hat den größten Einzelposten benannt: die Produktseite ist **eine** Funktion.
Der Umbau beginnt deshalb nicht mit dem Verschieben von Code, sondern mit einer
wiederholbaren **Messung** und dem **Netz**, das jeden Schritt prüfbar macht.

**Messung** (`scripts/grid_structure.py`, AST statt Bauchgefühl):

| Größe | Wert |
| --- | --- |
| `products_page` (`meadow/frontend/grid.py`) | Zeilen 905–3641 = **2737** |
| verschachtelte Funktionen | **106** (99 äußerste) |
| Zeilen darin | 1645 |
| linearer Widget-Code | **1092** |
| größte Funktionen | `refresh_config` 157, `render_tree` 102, `start_bulk` 63, `refresh_references` 61, `fetch_filtered` 53, `save` 51, `_show_bulk_report` 50 |

Der Zustand ist erstaunlich selten `nonlocal` (**7** Deklarationen): geteilt wird über
Dicts und Widgets. Genau das macht das Verschieben riskant — die Abhängigkeiten sind
unsichtbar, und genau deshalb kann der Umbau nur mit einem Verhaltensnetz beginnen.

**Das Netz: Seitentests über NiceGUIs User-Simulation** (`tests/test_products_page.py`,
3 Tests, je ~0,4 s). Sie booten die **echte** App aus `meadow/frontend/__main__.py` —
Seitenregistrierung, Shell und Middleware inklusive — und ersetzen genau zwei Dinge:
den Kern-Client (`FakeCore`, der jeden Aufruf aufzeichnet) und die Auth-Middleware
(der Login hat eigene Tests). Damit prüfen sie **Seitenverhalten** statt Helfer:
Ein ungefilterter Aufruf lädt keine Objekte (und zeigt den Hinweis), `?type=…&incomplete=1`
kommt als `object_type`/`missing_required` mit `limit=1000` im Kern-Aufruf an, und ein
leerer Filtertreffer meldet sich als solcher statt den Hinweis zu zeigen.

**Drei Fallen, die dabei Zeit gekostet haben:**

1. `AuthMiddleware.dispatch` wird als *gebundene* Methode aufgerufen — ein Patch muss
   `(self, request, call_next)` annehmen (`TypeError: takes 2 positional arguments`).
2. Statt des `user`-Fixtures nutzt der Test `user_simulation(main_file=…)` direkt,
   weil die Modul-Eviction **vor** dem Boot gesteuert werden muss (Punkt 3).
3. **Der wichtige Fund:** NiceGUI entfernt Seitenmodule nur dann aus `sys.modules`
   (Teardown, für Module registrierter Routen), wenn die Route auch registriert wurde.
   Hat ein *anderes* Testmodul `meadow.frontend.grid` schon beim Einsammeln importiert,
   läuft `__main__.py` beim Boot ins Leere: die `@ui.page`-Dekoratoren sind gecacht,
   die frische App bekommt keine Routen, und jeder `open()` antwortet `404`. Deshalb
   wirft das Fixture `meadow.frontend*` selbst aus `sys.modules`, bevor die Simulation
   startet.

**Reihenfolge der Inkremente** (je: verschieben, `check.ps1 python`, Seitentests,
`grid_structure.py` erneut):

1. **Dialoge mit eigener Zustandsfläche** — `_show_bulk_report` (50 Zeilen) und der
   Ersetzen-Dialog lesen fast nur Auswahl und Bericht und schreiben nichts in den
   Seitenzustand.
2. **Der Bulk-Wizard** (`_current_page_ids`, `_scope_ids`, `start_bulk`, `poll_bulk`,
   `_show_bulk_report`, `refresh_config` 157) — der größte Einzelgewinn; sein Zustand
   gehört in einen eigenen Typ.
3. **Baum/Panel** (`render_tree` 102, `load_category_*`) und **Filterleiste**
   (`set_view`, `on_filter_change`, `where_*`).
4. **Erstellen-Dialog** (`render_create_fields`, `save_new_object`).

Regel für jeden Schritt: **keine Verhaltensänderung**, und der reine Teil wandert in ein
Modul der Größe `export_fields.py` bzw. `load_option_sources` (§4.4c). Bricht Schritt 1
das Netz, ist die Reihenfolge falsch — nicht die Regel.

**Stand (2026-09-18):**

- **Inkrement 1 — die Reduktionen des Bulk-Berichts:** neues Modul
  `meadow/frontend/bulk_report.py` mit `read_bulk_report` (Zählwerte, welche
  Zusammenfassungszeile gilt, welche Einträge als Fehler und welche als vom Workflow
  abgelehnt gelten) und `replace_target_options` (welche Attribute ein Find & Replace
  anbieten darf), dazu `tests/test_bulk_report.py` (**6 Tests**). Die Seite liest nur noch
  `report.errors`/`report.is_replace`/…; die Entscheidungen liegen getestet vor.
- **Inkrement 2a — die Entscheidungen des Bulk-Wizards:** neues Modul
  `meadow/frontend/bulk_edit.py` mit `build_parameters` (die Payload, die der Worker liest —
  inklusive „Freigabe-Schalter nur bei Modifier-Ops“), `missing_parameters`,
  `approval_supported` sowie `row_id`/`ids_from_rows`/`page_ids`; dazu
  `tests/test_bulk_edit.py` (**10 Tests**). `start_bulk` baut die Parameter nur noch über
  `build_parameters(op, {feld: wert}, bulk_picked)`, und **fünf** identische
  List-Comprehensions der Seite (`start_sequential`, `_scope_ids`, `_current_page_ids`,
  `handle_selection`, `_do_export`) sind durch `ids_from_rows`/`page_ids` ersetzt.
- **Das Netz reicht jetzt bis in den Wizard:** `tests/test_products_page.py` bedient den
  Dialog wie ein Benutzer (Widgets tragen Marker, `mark(...)` in NiceGUI 3.x) und prüft die
  Wirkung statt der Struktur: `?type=product` → Dialog öffnen → Operation + Status wählen →
  Start ⇒ `post_bulk(op="change_status", object_ids=["product:1", "product:2"],
  parameters={"status": "published"})`; eine unvollständige Konfiguration postet **nichts**
  und meldet `grid.bulk_config_incomplete`. **Zwei Erkenntnisse für alle weiteren Seitentests:**
  NiceGUIs `UserInteraction` tippt in Eingabefelder, bedient aber keine Selects (der Test
  setzt `.value` im Client-Kontext — genau das, was `type()` tut), und ein Klick auf einen
  **async**-Handler *plant* ihn nur: der Test wartet mit `_wait_until(...)` auf die Wirkung.
  Kosten der Marker: +5 Zeilen in der Seite (2672 → 2677) — das Netz wächst also in der Seite,
  bevor sie schrumpft.
- **Messung:** `products_page` **2737 → 2672** Zeilen, linearer Widget-Code 1092 → **1084**,
  verschachtelte Funktionen 106 → **105**; Tests 566 → **582**, mypy 133 → **135** Dateien.
  Der Zeilengewinn bleibt klein — der Wert liegt in der Testbarkeit: Payload-Vertrag,
  Scope-/Seitenfenster-Regel und Zeilen-Extraktion waren vorher unerreichbar eingeschlossen.
- **Inkrement 2b/1 — der Konfigurationsbereich des Wizards:** `refresh_config` (160 Zeilen,
  der größte Block) und seine sechs verschachtelten Funktionen (`choose_target`,
  `choose_relation_target`, `reset_relation_target`, die beiden `on_pick`) sind nach
  `meadow/frontend/bulk_wizard.py` geworden: `BulkConfigArea` bekommt die Kataloge
  (Attribute, Relationstypen, Kategorien, Kanäle) explizit, hält Felder und Picker-Wahl
  selbst (`fields`/`picked`/`values()`) und rendert pro Operation in den übergebenen
  Container. Die Seite ruft nur noch `bulk_config.render(config_area, op)` und
  `build_parameters(op, bulk_config.values(), bulk_config.picked)`. **Messung:**
  `products_page` **2677 → 2520** Zeilen, verschachtelte Funktionen 105 → **100**.
- **Eine Falle, die die Gates sofort fingen:** die naheliegende Variable hieß `config` — und
  verschattet damit das importierte Modul `meadow.frontend.config`, das dieselbe Funktion
  oben für `config.CORE_URL` benutzt (`ruff` F823, `mypy` `used-before-def`). Sie heißt
  jetzt `bulk_config`. Ohne die beiden Prüfer wäre das ein Laufzeit-`UnboundLocalError`
  beim Seitenaufbau gewesen.
- **Inkrement 2b/2 — Dialoghülle und Ablauf:** die Dialogkonstruktion (Scope-/Operations-
  Select, Auswahl-Zähler, Konfigurationsbereich, Start/Schließen), `start_bulk`, `poll_bulk`
  und der Berichtsdialog sind jetzt `BulkWizard.build()`/`set_selection()`/`start()`/`poll()`/
  `show_report()` in `bulk_wizard.py`, samt `WizardCatalogs` (die Kataloge der Seite als
  Datenklasse) und `PENDING_WARN_POLLS`. Die Seite behält **nur** den Öffner-Button
  (`bulk_wizard.open`), die Weitergabe der Auswahl (`set_selection`) und die
  Scope-Auflösung (`_current_page_ids`/`_scope_ids` — nur sie kennt das Grid-Fenster).
  Der Find & Replace nutzt weiter dieselbe Fortschritts-/Berichtsmaschinerie, jetzt
  über `bulk_wizard.poll(...)`.
- **Messung nach 2b:** `products_page` **2520 → 2357** Zeilen (vom Ausgangswert **2737** also
  −380), verschachtelte Funktionen 100 → **95**, linearer Widget-Code 1088 → **1035**.
  Der Wizard liegt damit in drei Modulen: Vertrag `bulk_edit.py`, Bericht `bulk_report.py`,
  UI `bulk_wizard.py` — mit 16 reinen Tests plus zwei Seitentests, die den Weg
  Dialog → Operation → Wert → Start → Payload über den **verschobenen** Code prüfen.
- **Inkrement 3, erst das Netz:** zwei weitere Seitentests pinnen die Wege, die verschoben
  werden sollen. Der erste schrieb eine Annahme über den Bestand fest und korrigierte sie:
  das Tippen in der Filterleiste löst eine **serverseitige Suche** aus
  (`picker_search("<Begriff>", …)` und erst danach `list_objects(ids=…)`) — **nicht** etwa
  `list_objects(q=…)`, wie der erste Testentwurf vermutete. Der zweite wählt einen
  Strukturknoten und prüft `list_objects(ancestor="product:1")`. Dafür tragen Suchfeld und
  Baum Marker (`filter-search`, `structure-tree`), und der Kern-Stub zeichnet jetzt auch
  **positionale** Argumente auf — `picker_search` bekommt den Suchbegriff als zweites
  Argument, ohne das wäre der Aufruf nicht prüfbar (Hilfen: `_stub_tree`,
  `_list_objects_calls`, `_search_terms`). Kosten wie immer beim Netz: +5 Zeilen Marker
  (2357 → 2362), **586** pytest.
- **Inkrement 3b/1 — der Bedingungsdialog ist heraus:** `where_state`, `where_summary`,
  `current_where_query`, `has_advanced_conditions`, `ensure_where_form`, `_fill_where_dialog`
  und die sechs Handler (`open_advanced`, `apply_advanced`, `clear_advanced`,
  `load_saved_search`, `save_as_search`) plus die Dialogkonstruktion sind
  `meadow/frontend/where_dialog.py` geworden (`WhereDialog` mit `build`/`open`/`apply`/
  `clear`/`summary`; `has_conditions` und `saved_search_name` als reine Funktionen). Die
  Seite behält den Filterzustand (`active["where"]`), den Chip und den Reload und reicht sie
  als Rückrufe hinein (`get_where`/`set_where`/`on_applied`/`reload`).
- **Eine Lehre aus drei Fehlversuchen am Seitentest** (die den Code am Ende bestätigt haben):
  `should_see(...)` zwischen zwei Klicks kann die Dialoginteraktion stören; ein Klick auf
  einen Dialog-Button **funktioniert** einwandfrei (der Beweis kam über `user.notify`), aber
  mein Test erwartete das Falsche — der Dialog **lehnt eine leere Bedingung ab**
  („query.where: a group needs at least one condition") und lädt deshalb bewusst nicht neu.
  Der Test pinnt jetzt genau das: Katalog wird geladen, die leere Bedingung wird abgelehnt,
  `list_objects` wird **nicht** erneut gerufen. Ein Seitentest muss also das *gewollte*
  Verhalten kennen, sonst testet er die eigene Annahme.
- **Messung nach 3b/1:** `products_page` **2362 → 2215** Zeilen (vom Ausgangswert **2737** also
  −522), verschachtelte Funktionen 95 → **85**, linearer Widget-Code 1036 → **999**;
  **590 pytest**, mypy **136** Dateien.
- **Inkrement 3b/2 — die Spaltenansichten:** `save_view`/`load_view`/`delete_view`/
  `refresh_views` und ihr Dialog sind `meadow/frontend/column_views.py` geworden
  (`ColumnViews` mit `build`/`open`/`save`/`load`/`delete`/`refresh`; die Seite liefert
  `get_fields` und `apply_fields`). Dazu die reine Funktion `read_views`, die den
  Speicherwert normalisiert — Nutzerspeicher überlebt Upgrades und ist schreibbar, ein
  kaputter Eintrag darf das Grid nicht brechen (`tests/test_column_views.py`). Ein
  Seitentest speichert eine Ansicht über die echten Widgets und prüft Meldung + Auflistung.
- **Fund nebenbei:** `views = app.storage.user.get("views", {})` in der Seite war **toter
  Zustand** — kein Leser (der danebenstehende Kommentar beschreibt `restore_columns`).
  Entfernt; ein solcher Rest ist genau das, was der Deep-Dive als `lint_escape` meldet.
- **Messung nach 3b:** `products_page` **2737 → 2182** Zeilen (**−555**), verschachtelte
  Funktionen 106 → **81**, linearer Widget-Code 1092 → **991**; **593 pytest**, mypy **137**
  Dateien.
- **Inkrement 4 — der Anlegen-Dialog:** `open_create`, `render_create_fields`, `save_object`
  und die Dialogkonstruktion (Typ-Auswahl, Pflichtfelder, Eltern-Kaption) sind
  `meadow/frontend/create_object.py` geworden (`CreateObjectDialog` mit `build`/`open`/
  `save`/`render_fields`; `normalized_name`, `missing_key_names`, `build_attributes` als reine
  Funktionen, 4 Tests). Der Dialog **baut sein Feldgerüst neu auf**, statt einen Refreshable
  zu dekorieren (`form_host.clear()` + `with form_host:`) — das war die bewusste Wahl gegen
  ein Typ-Risiko des Dekorators. Zwei Seitentests pinnen die zwei Verträge des Schreibens:
  ohne Baumauswahl geht `parent=None` mit, mit ausgewähltem Knoten `parent="product:1"`
  (**der Kompositions-Elternteil ist Teil des Schreibens**, Wurzelvertrag).
- **Zwei Fallen dieses Inkrements, beide sofort generalisierbar:**
  1. **Importzyklus `grid ↔ multilang`.** `multilang` liest `resolve_value` aus `grid`; solange
     `grid` den Editor *nach* `display` importierte, ging das gut, als `create_object` (rein
     alphabetisch) **vor** `display` kam, brach `import meadow.frontend.grid` ab. Der Editor
     wird jetzt **in der Methode** importiert (mit Begründung im Code) — nicht die
     Importreihenfolge „reparieren", sondern den Zyklus an der schwächsten Stelle lösen.
  2. **mypy `has-type`-Kaskade.** Ein **selbstreferenzierender lokaler Wert** (`create_btn`
     verweist auf `create_dialog`, dessen Konstruktor einen Rückruf bekommt, der wiederum
     Seitenszustand liest) ließ mypy die Typinferenz des *ganzen* Funktionsrumpfs aufgeben:
     **24** Fehler `Cannot determine type of …` verteilt über die Seite, darunter Namen weit
     weg von der Änderung (`grid`, `staged_label`). Eine einzige **explizite Annotation** am
     selbstreferenzierenden Wert (`create_dialog: CreateObjectDialog = …`) brachte alle 24 auf
     0. Bei einer solchen Kaskade also nicht die genannten Stellen jagen, sondern den
     selbstreferenzierenden Wert annotieren.
- **Eine dritte Falle kommt aus dem Netz selbst:** `User.find(marker=…)` **wirft**, wenn
  nichts passt (statt leer zurückzukommen). Ein Warten über `bool(find(...).elements)` ist
  damit kein Warten — es schlägt beim ersten Versuch fehl, obwohl der Dialog einen Moment
  später da ist. Für „warten, bis sichtbar" ist `await user.should_see(marker=…)` richtig
  (`_wait_until` bleibt für Zustände ohne Marker, z. B. aufgezeichnete Kern-Aufrufe).
- **Fund nebenbei:** die Seitenschleife baute `type_options` (`{Typ: Label}`) noch einmal auf,
  obwohl der Dialog seine eigene Übersetzungstabelle mitbringt — ruff meldete die Zuweisung
  als ungenutzt. Solche Reste sind der Normalfall beim Verschieben und ein Grund, die Gates
  nach **jedem** Inkrement laufen zu lassen.
- **Messung nach 4:** `products_page` **2182 → 2049** Zeilen (vom Ausgangswert **2737** also
  −688), verschachtelte Funktionen 81 → **75**, linearer Widget-Code 991 → **968**;
  **599 pytest**, mypy **138** Dateien, Deslop-Scan **0** Befunde.
- **Inkrement 3b/3a — der Navigationsbaum:** `render_tree` (103 Zeilen) plus die beiden
  reinen Helfer `collection_label`/`collection_node` sind `meadow/frontend/tree_panel.py`
  geworden. Weil der Baum sehr viel Seitenszustand liest, bekommt das Panel ihn als
  **Momentaufnahme**: `TreeState` ist eine eingefrorene Datenklasse (Kollektionen,
  Taxonomie-Wurzeln, „mehr"-Flag, *uncategorized*-Knoten, Auswahl, Struktur-Knoten,
  Server-Treffer, Treffer-Query, Vorfahren-Pfad), die die Seite als Rückruf liefert —
  gelesen wird bei **jedem** Rendern, damit nie ein veralteter Stand gezeichnet wird. Die
  sechs Selektions-/Lade-Rückrufe und die zwei Treffer-Aktionen kommen ebenfalls aus der
  Seite; zurück kommt `RenderedTree` (Baum-Widget + Wurzelknoten). `render_tree` bleibt als
  dünner Seiten-Wrapper (die Aufrufer bleiben unverändert). **Netz zuerst:** zwei Seitentests
  pinnen die zwei Zweige, die sonst niemand prüfte — der Struktur-Zweig zeichnet den Baum
  (`structure-tree`, kein „Laden fehlgeschlagen") und der Kollektions-Zweig nach dem Klick
  auf die Ansichtsschaltfläche (dafür bekamen die drei Ansichtsbuttons die Marker
  `view-structure`/`view-category`/`view-collection`).
- **Werkzeug-Lehre (kostete drei rote Läufe):** ein **einzeiliger** `oldString` passt
  unabhängig von der Einrückung — wer damit einen mehrzeiligen Block *einfügt*, landet
  schnell auf der falschen Ebene (hier: 8 statt 20 Leerzeichen, dann 24 statt 20) und
  zerbricht die Datei. Vor dem Einfügen eines Blocks also immer den **umgebenden Block
  vollständig lesen** und die Einrückung aus dem Original übernehmen.
- **Messung nach 3b/3a:** `products_page` **2049 → 1969** Zeilen (vom Ausgangswert **2737**
  also **−768**, damit unter der angepeilten 2000er-Marke), verschachtelte Funktionen 75 →
  **72**, linearer Widget-Code 968 → **1000** (der Wrapper und die Panel-Konstruktion
  zählen dort mit); **601 pytest**, mypy **138** Dateien, Deslop-Scan **0** Befunde.
  Größte verbliebene Einzelfunktionen: `refresh_references` 61, `fetch_filtered` 53,
  `save` 51.
- **Inkrement 5 — Filterzustand und seine URL:** die zehn Filter des Grids und ihre
  Abbildung auf die URL sind `meadow/frontend/grid_query.py` geworden: `read_filters`
  (Saat des Seitendicts `active`), `has_filter` (entscheidet, ob ein Aufruf überhaupt
  Objekte lädt), `filters_query` (Query-String) und `products_url` (bookmarkbare URL).
  Die Seite hält nur noch einen dünnen `has_filter()`/`current_url()`-Wrapper; die
  Inline-Literale (15 Zeilen Zustand, 28 Zeilen Query-Aufbau) sind weg, ein `nonlocal`
  weniger. **Der Rundlauf ist getestet** (`tests/test_grid_query.py`, 15 Tests): laden aus
  der URL → `products_url` → wieder laden ergibt denselben Zustand, inklusive Quoting von
  Suchtext und Query-Dokument. Zwei Regeln sind damit festgeschrieben statt nur Konvention:
  ein **einzelnes** `definition_id` (Dimension) ist **kein** Filter (es wählt nur, in
  welcher Kategorisierung die Ansicht arbeitet), und geschrieben wird es **nur** zusammen
  mit dem `category`/`uncategorized`-Filter, zu dem es gehört. Dazu ein Seitentest für die
  Filterleiste: `?q=…&incomplete=1` lädt, der Reset-Knopf leert Suchfeld und Schalter (der
  Typ-/Baumfilter bleibt bewusst), und ohne Filter wartet das Grid wieder auf eine Auswahl.
- **Messung nach 5:** `products_page` **1969 → 1917** Zeilen (vom Ausgangswert **2737**
  also **−820**), verschachtelte Funktionen 72 → **71**; **617 pytest** (15 davon rein für
  den Filterzustand), mypy **139** Dateien, Deslop-Scan **0** Befunde.
- **Nächstes:** `set_view` (25 Zeilen, schaltet ~10 Panels) und danach die größten
  verbliebenen Einzelfunktionen (`refresh_references` 61 samt `search_references`/
  `open_references`, `save` 51, `fetch_filtered` 53).

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
| `stopslop.toml` | gepinnte Regelauswahl: `SLOP018` aus, Stilgruppen nur für Markdown aus, Artefakt- und Strukturregeln an, Excludes, **keine Baseline** |
| *(keine Baseline)* | bewusst leer: es gibt keine akzeptierten Befunde, jeder Befund der Stufe A scheitert (§4.4) |
| `scripts/deslop.ps1`, `scripts/deslop.sh` | manueller Einstieg: `all\|changed\|staged`, `--fix-plan` (Python-Deep-Dive über `meadow` und `scripts`), Berichte unter `.tmp/deslop/` |
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
  Scope und landen als `ai-slop-ignore`-Kommentar im Diff – eine Baseline gibt es
  bewusst nicht (§4.4).
- Score, Herkunftsvermutung und Werkzeuglabel werden nie in Produktdaten, Audit-Logs
  oder die Benutzeroberfläche übernommen.

## 8. Abnahmekriterien

| # | Kriterium | Stand |
| --- | --- | --- |
| 1 | Der manuelle Scan läuft auf Windows und Linux mit identischer Konfiguration | Windows verifiziert; das Shell-Skript ist syntaxgeprüft, der Linux-Lauf folgt mit CI/VPS |
| 2 | Python- und Rust-Quelltext inklusive Kommentaren und Strings wird erfasst, generierte Pfade sind ausgeschlossen | umgesetzt (`stopslop.toml`) |
| 3 | Ein absichtlich eingefügter Artefakt der Stufe A wird reproduzierbar gefunden | umgesetzt (`tests/test_deslop.py`: 8 Python- und 5 Rust-Regeln) |
| 4 | Es gibt keine akzeptierten Bestandsbefunde; jeder Befund der Stufe A scheitert | umgesetzt (§4.4: Baseline abgeschafft, testseitig erzwungen) |
| 5 | Ein Reparaturpass erzeugt einen nachvollziehbaren Diff und lässt die Gates laufen | umgesetzt (§4.2; der volle `cargo test`-Lauf braucht den gestoppten Dev-Kern) |
| 6 | Die Dokumentation nennt Befunde Qualitätssignale und behauptet keine KI-Erkennung | umgesetzt (dieses Dokument) |

## 9. Stand und offene Punkte

**Entschieden:**

- `--check-imports` (`SLOP010`) bleibt aus (§4.3).
- Es gibt **keine Baseline**; jeder Befund der Stufe A scheitert (§4.4).
- `ai-slop-detector` bleibt in der Dev-Gruppe: `--fix-plan` nutzt es, `uv` pinnt es,
  zur Laufzeit ist es nicht installiert.
- Die vier transitiv vorhandenen Pakete (`fastapi`, `starlette`, `engineio`,
  `botocore`) bleiben undeklariert (§4.3): NiceGUI baut auf FastAPI auf, und ein
  Rahmenwechsel würde unsere Seitenhandler so oder so brechen – eine Deklaration
  schützt nicht, sie erzeugt nur zusätzliche Versionsbindung.
- Degradierungen bleiben **nur im Log** sichtbar, ohne UI-Hinweis (§4.2), damit der
  Alltag nicht geflutet wird.

**Offen (empfohlene Reihenfolge):**

1. **Typ-Prüfung als Gate:** **abgeschlossen** (§4.6–§4.9) — **131 Dateien** (`meadow`
   vollständig inkl. Frontend, `tests/`, `scripts/`) sind grün, `check_untyped_defs` ist an,
   und die Fixtures sind bewusst ausgenommen. **Entschieden:** NiceGUIs Element-Typing wird
   geprüft, nicht als Grenze geführt (§4.7).
2. **`# noqa: BLE001` verengen:** **abgeschlossen** (§4.10) – **234 → 38**, und jede der 38
   verbliebenen Stellen ist eine benannte breite Grenze (Worker-Isolation, AG-Grid-Bridges,
   injizierter Loader, Datei-Dekoder).
3. **Struktur-Umbau `products_page`** (Ausgangswert 2737 Zeilen, §4.11): **Inkremente 1–4,
   3b/3a und 5 umgesetzt** — reine Reduktionen (`bulk_report.py` + 6 Tests, `bulk_edit.py` +
   10 Tests), Wizard (`bulk_wizard.py` + 16 reine und 2 Seitentests), Bedingungsdialog
   (`where_dialog.py`), Spaltenansichten (`column_views.py`), Anlegen-Dialog
   (`create_object.py` + 4 reine und 2 Seitentests), Navigationsbaum (`tree_panel.py`,
   Zustand als eingefrorene `TreeState`-Momentaufnahme), Filterzustand + URL
   (`grid_query.py` + 15 Tests); die Seite ist bei **1917 Zeilen** (**−820**), Messwerkzeug
   (`scripts/grid_structure.py`) und Seitentests (`tests/test_products_page.py`, **14**
   Tests) tragen jeden Schritt. **Offen:** `set_view`, danach die größten Einzelfunktionen
   (`refresh_references` 61, `fetch_filtered` 53, `save` 51).
4. **Linux-Lauf von `scripts/deslop.sh`:** das Skript lief erstmals real (Git Bash
   unter Windows: `all` und `staged`, jeweils Exit 0); der eigentliche Linux-Lauf
   folgt mit CI/VPS. Danach den Rauschpegel der Stilgruppen für `docs/user/` prüfen.
5. **SARIF an Forgejo Code Scanning** – erst mit registriertem Runner
   (siehe Root-`AGENTS.md`, Abschnitt Verification).

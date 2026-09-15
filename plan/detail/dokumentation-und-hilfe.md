# Dokumentation & Hilfe im UI (Benutzerhandbuch)

- **Hauptdokument:** Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 9 (Frontend-Ausbau), danach **laufend** — das Handbuch wächst mit der Oberfläche
- **Stand:** **Erste Ausbaustufe umgesetzt** (2026-09-15): Handbuch (de/en) für die fünf Kern-Arbeitsflächen, Seite `/help` mit Suche, Info-Button-Konvention in Dashboard/Grid/Formular/Import/Export, automatisch erzeugte Screenshots, Invarianten-Tests. **Offen:** weitere Kapitel, Demo-Daten-Seeding, externe Fassung (Abschnitt 6).

**Getroffene Entscheidungen (umgesetzt):**

- **Ein** Handbuch als **Markdown im Repository** (`docs/user/<sprache>/`) ist die Single Source of Truth. Keine zweite Textquelle, keine Doku in der Datenbank.
- Ausgeliefert wird es **von der UI ohne Build-Schritt** („Option B", Abschnitt 2): `uv run python -m meadow.frontend` liest die Kapitel beim Start, rendert sie serverseitig und stellt sie unter `/help` bereit. Damit kann das Handbuch nicht von der Oberfläche abdriften, die es beschreibt.
- **Tooltip = Kurzantwort, Handbuch = Langantwort.** Ein Konzept wird an genau **einer** Stelle erklärt; überall sonst wird darauf verwiesen.
- **Kapitel-IDs sind sprachunabhängig** (`/help?page=product-form` funktioniert in jeder Sprache), **Abschnitts-Anker sind es nicht** — deshalb verlinken Tooltips nur auf Kapitel.
- **Keine neue Doku-Plattform im MVP.** Rendering übernimmt **Python-Markdown** (`markdown`, mit `toc`/`tables`/`fenced_code`/`attr_list`/`sane_lists`) — die Überschrift-IDs des Renderers sind die Quelle der Anker, es gibt keine zweite Slug-Logik.
- **Keine Performance-Kosten für die Arbeitsflächen:** Das Handbuch wird pro Prozess **einmal** geladen und gecacht; Tooltips sitzen nur auf statischem Gerüst (Toolbar, Dialoge, Überschriften), **nie** in Grid-Zeilen/-Zellen (dort weiterhin `headerTooltip`).

## 1. Ziel

Zwei Wege zur selben Antwort — der kurze direkt am Feld, der lange im Zusammenhang:

1. **In der Oberfläche:** Ein Konzept (Taxonomie vs. Dimension, Vererbung, Referenzdaten) ist mit einem Satz erklärt, ohne die Oberfläche zu überladen; ein **Info-Button (❓)** führt in das passende Kapitel.
2. **Im Handbuch:** aufgabenbezogene Kapitel („Wie exportiere ich Daten?") mit Screenshots, erreichbar über die Navigation („Hilfe") und durchsuchbar.

Nicht-Ziele: Entwickler- und Betriebsdokumentation (bleibt in `plan/` bzw. `docs/`), API-Referenz (OpenAPI), Marketing-Material.

## 2. Entscheidung: Markdown in Git vs. statische HTML-Fassung

Bewertet wurden drei Varianten:

| | A: nur Markdown | **B: Markdown + In-App-Seite (gewählt)** | C: Markdown + statischer Build |
| --- | --- | --- | --- |
| Einbindung | keine | `/help`, Navigationseintrag | gemountete statische Site (iframe/neuer Tab) |
| Suche | — | eigene, kleine Suche im Server | fertige Client-Suche |
| Werkzeugkette | keine | **keine** (nur ein Renderer als Abhängigkeit) | Generator + Build im Image |
| Optik/Sprache | — | nahtlos, nutzt die bestehende Locale-Mechanik | fremdes Layout, i18n als Plugin |
| Publizierbar | nein | nein | ja |

**Gewählt: B.** Begründung: „einfach, performant, wartbar" (Leitprinzip) — die Doku ist ein **In-App-Handbuch**, keine Website. Der Inhalt bleibt reines Markdown, deshalb ist **C später rein additiv** möglich (gleiche Dateien, nur ein zusätzliches Build-Ziel), ohne dass Text umgeschrieben werden muss.

**Recherchestand für C (2026-09):** MkDocs 1.x ist seit 08/2024 unbearbeitet, Material for MkDocs im Maintenance-Mode; der Nachfolger **Zensical** (gleiches Team, MIT, liest `mkdocs.yml`, eigene Client-Suche) ist noch **v0.0.x**, die Plugin-Parität (u. a. i18n) wächst erst. Technische Randbedingung für eine gemountete Site: NiceGUIs `add_static_files` mountet `StaticFiles` **ohne `html=True`**, Verzeichnis-URLs (`/help/user/produkte/`) liefen also in einen 404 — mit `use_directory_urls: false` (`…/produkte.html`) funktioniert es.

## 3. Struktur & Konventionen (verbindlich)

- **Ablage:** `docs/user/<sprache>/<NN>-<slug>.md` — `NN` ist die Lesereihenfolge, `<slug>` die **Kapitel-ID** in der URL, die erste `#`-Überschrift der Kapiteltitel.
- **Gleiche IDs in allen Sprachen:** `de/` und `en/` führen **dieselben Dateinamen**; übersetzt wird nur der Text. Nur so ist ein Deep-Link sprachneutral (`tests/test_help.py` erzwingt das).
- **Sprachordner sind ISO-Codes** (`de`, `en`, `pt-BR`); alles andere unter `docs/user/` (z. B. `assets/`) ist **keine** Sprache.
- **Anker** (`?page=…&anchor=<id>`) stammen aus dem Markdown-Renderer und sind **sprachabhängig**; sie werden innerhalb des Handbuchs (Suchtreffer) benutzt, nicht in Tooltips.
- **Anker sind Query-Parameter, kein `#fragment`:** NiceGUI baut die Seite clientseitig, der Browser kann einen Element-Anker nicht selbst anspringen — das Scrollen macht `help_page._scroll_to` nach dem Rendern.
- **Bilder:** `docs/user/assets/<name>.png`, referenziert als `/help-assets/<name>.png` (mount in `help_page.py`, ohne Auth-Umleitung wie `/static`). Die Bilder sind **erzeugt**, nicht gemalt (Abschnitt 5).
- **Querverweise** innerhalb des Handbuchs sind absolute Links `/help?page=<id>` (auch über Sprachgrenzen hinweg gültig).
- **Handbuchsprache ≠ UI-Sprache:** Die Sprache der Oberfläche stellt die i18n-Kette, die des Handbuchs ist eine eigene **Benutzerpräferenz** (`app.storage.user["help_language"]`, absichtlich nicht Teil der Sitzungsschlüssel — sie überlebt das Abmelden). Fallback: UI-Locale → `en`.
- **Katalog pro Prozess gecacht** (`help_content.catalog()`): eine Inhaltsänderung braucht im Dev einen **Frontend-Neustart** (im Deployment fällt das mit dem Image-Build zusammen).
- **Das Frontend-Image muss die Kapitel mitkopieren** (`docker/Dockerfile.frontend` → `COPY docs/user ./docs/user`).

## 4. Hilfe-Regeln (verbindlich, gegen Überladung)

1. **Kein Tooltip, der nur das sichtbare Label wiederholt.** Tooltips nur an ikonischen Buttons ohne Text und an Steuerelementen mit nicht offensichtlicher Semantik (Filter, Mehrfachauswahl, gesperrte Schalter).
2. **Ein Konzept wird an genau einer Stelle erklärt** — dort, wo die Entscheidung fällt (z. B. Taxonomie vs. Dimension im Baum-Kopf), nicht in jedem Dialog erneut.
3. **Info-Button = Kurzantwort + Kapitel-Link** (`info_button(hint_key, page_id)`): der Tooltip erklärt in einem Satz und nennt das Kapitel, der Klick öffnet es. Damit trägt kein Tooltip einen Absatz.
4. **Ein Kapitel-Einstieg pro Arbeitsfläche** (`chapter_button(page_id)`): Dashboard, Grid, Produktformular, Import, Export. Seltene Admin-Dialoge bekommen **keinen** Tooltip, sondern nur den Kapitel-Einstieg der Seite.
5. **Keine Tooltips in Grid-Zeilen/-Zellen.** 100 Tooltips auf statischem Gerüst sind unmerklich, pro Zelle wären sie tödlich (Performance-Budget der Arbeitsflächen).

Zusatz: Neue Nutzertexte kosten **drei** Sprachen (`en`/`de`/`fr`, gleiche Schlüsselmenge ohne leere Werte, per Test erzwungen). Deshalb vorhandene `*_hint`-Keys wiederverwenden statt neue anzulegen — der Info-Button-Durchgang 2026-09-15 brauchte nur **einen** neuen Schlüssel (`help.page`).

## 5. Umsetzung (Ist-Stand 2026-09-15)

| Baustein | Datei | Inhalt |
| --- | --- | --- |
| Reine Ebene | `meadow/frontend/help_content.py` | Kapitel finden, Markdown rendern (Anker aus dem Renderer), Suchindex (Codeblöcke **nicht** indiziert), `search()` (Rang: getroffene Begriffe → Titel > Überschrift > Text → Lesereihenfolge, mit Textausschnitt), Umlaut-Faltung, `help_target()`, `catalog()` (Cache) |
| Seite & Widgets | `meadow/frontend/help_page.py` | `/help?page=&q=&anchor=` (Kapitelliste, Suche mit `debounce`, Sprachumschalter), `info_button`, `chapter_button`, `hint_label`; Mount `/help-assets`; Kapitel-CSS |
| Inhalt | `docs/user/de/`, `docs/user/en/` | 5 Kernkapitel + Bilder, gleiche IDs je Sprache |
| Bilder | `docs/user/assets/` | 6 generierte Screenshots (~640 KB) |
| Erzeugung | `scripts/docs_screenshots.py` | Playwright gegen eine **laufende** Dev-Instanz; feste Viewport-Größe, `locale` aus dem Browser-Kontext (frischer Kontext → `Accept-Language`), Marker-Waits statt Sleeps, `--list/--only/--out`; **kein Seeding** (Demo-Daten müssen vorhanden sein) |
| Tests | `tests/test_help.py` | reine Regeln **und** Inhalts-Invarianten: gleiche Kapitel-IDs je Sprache, jeder Such-Anker genau einmal im gerenderten HTML, jeder Querverweis zeigt auf ein existierendes Kapitel |
| Auslieferung | `docker/Dockerfile.frontend`, `meadow/frontend/auth.py` | `docs/user` im Image; `/help-assets` ohne Auth-Umleitung (Bild darf beim Sitzungsablauf nicht zum Login umgeleitet werden) |
| Gates | `scripts/check.sh`/`check.ps1` | Ruff prüft zusätzlich `scripts/` (damit `docs_screenshots.py` mitgeprüft wird); CI ruft `check.sh` |

Der Info-Button-Durchgang nutzt vorhandene `*_hint`-Keys: Taxonomie/Dimension im Grid (Sichtbarkeit folgt der Baum-Sicht), „Vererbung konsolidieren" im Formular, plus Kapitel-Einstieg in Dashboard, Grid, Produktformular, Import, Export.

## 6. Offene Punkte (hier weiterarbeiten)

1. **Weitere Kapitel** — Kategorien/Taxonomie & Klassen-Merkmale, Suchen & Collections, Kanäle/Freigaben, Vererbung im Detail sowie die Admin-Bereiche (Attributdefinitionen, Objekttypen, Relationstypen, Jobs, Konnektoren, Regeln, Benutzer/Gruppen, System-Logs/-Einstellungen). Gerüst, Regeln und Prüfungen stehen; es ist Textarbeit in `docs/user/{de,en}/` (plus ggf. Bilder).
2. **Demo-Daten für die Screenshots** — heute fotografiert das Skript die lokale Datenlage. Sauber wäre ein idempotenter Seed über die vorhandenen Wege (MCF-Konfigurationsimport + MEF-Objektimport, Business-Key-Upsert), damit Bilder reproduzierbar sind und nicht von der Dev-Datenbank abhängen.
3. **Screenshot-Ausbau** — fokussierte Ausschnitte (`locator.screenshot()`), Markierungen/Hervorhebungen, Bilder für Detailzustände (z. B. Vererbungsmarker, Bulk-Wizard) — nur, wenn der Pflegeaufwand vertretbar bleibt.
4. **Externe Fassung (Option C)** — dieselben Markdown-Quellen zusätzlich als statische Site/PDF bauen (Zensical oder MkDocs-Material), z. B. für Weitergabe an Kunden. Rein additiv; bei gemounteter Site `use_directory_urls: false` beachten (Abschnitt 2).
5. **Handbuch in weiteren UI-Sprachen** — `fr` ist in der Oberfläche vorhanden, das Handbuch fällt derzeit auf `en` zurück. Ein Ordner `fr/` mit denselben IDs genügt technisch; Aufwand ist reine Übersetzung.
6. **Weitere Kapitel-Einstiege** — heute sind die fünf Kern-Arbeitsflächen versorgt; die Admin-Seiten können denselben `chapter_button` bekommen, sobald ihre Kapitel stehen.
7. **Kontext-Hilfe automatisieren** — offen ist, ob der Kapitel-Einstieg einer Seite strukturell erzwungen werden soll (z. B. Test: jede registrierte Route hat einen Eintrag in einer Help-Zuordnung). Bewusst noch nicht: nicht jede Seite braucht Hilfe.

## 7. Verifikation

- `pytest tests/test_help.py` — reine Regeln und Inhalts-Invarianten (Kapitel-IDs je Sprache, Anker gegen gerendertes HTML, Querverweise).
- `scripts/check.ps1 -Selection python` / `scripts/check.sh` — ruff (inkl. `scripts/`) + pytest.
- Manuell (im Browser verifiziert 2026-09-15): Kapitelwechsel, Suche mit Treffer-Navigation inkl. Scrollen zum Abschnitt, Sprachumschaltung des Handbuchs, Sichtbarkeit der Info-Buttons je Grid-Sicht, Bildauslieferung.
- `uv run python scripts/docs_screenshots.py` — erzeugt die Kapitelbilder; ohne laufende Instanz bricht es mit einem Hinweis auf die fehlende Umgebung ab.

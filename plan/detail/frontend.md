# Benutzeroberfläche (Frontend)

- **Hauptdokument:** Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 7, 9
- **Stand:** Entwurf (Login-Branding umgesetzt)

Dieses Dokument beschreibt die Weboberfläche (NiceGUI): Architektur, Mehrsprachigkeit der Oberfläche und die Modulübersicht. Die konkreten **Arbeitsflächen** (Grid, Produkt-Formular, Massendatenpflege, Dashboard) sind im Detaildokument [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md) spezifiziert. Die Mehrsprachigkeit der **fachlichen Daten** (Sprachbaum) ist separat im Dokument [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md) beschrieben.

## Architektur

- **Separater Docker-Container** mit Python + NiceGUI.
- **Lage & Start (implementiert, Schritt 1):** Paket `meadow/frontend/`, Einstieg `python -m meadow.frontend`; Dev-Port `8081` (Kern `8080`). Konfiguration über `PIM_CORE_URL`, `PIM_FRONTEND_PORT`, `UI_STORAGE_SECRET`. Das Branding wird per `app.colors()` (Quasar-Theme) appliziert; Auth per App-Middleware, JWT + Rollen in `app.storage.user`.
- Der NiceGUI-Dienst kommuniziert ausschließlich über die REST-API des Rust-Kerns.
- **Ein Dienst für alle Benutzer**: Es wird keine getrennte Admin-Oberfläche aufgebaut. Stattdessen werden Menüpunkte, Seiten und Buttons abhängig von Benutzerrolle und Berechtigungen ein-/ausgeblendet.
- Authentifizierung erfolgt über JWT: NiceGUI nimmt Login-Daten entgegen, fordert ein Token vom Kern an und speichert es in der Session.
- **Asynchrone Updates** (z. B. Job-Status, Änderungsbenachrichtigungen) werden in der ersten Version über **REST-Polling** realisiert. Eine direkte NATS-Anbindung des Frontends ist für spätere Versionen möglich, wird im MVP aber bewusst vermieden, um die Architektur einfach zu halten.

## Vorteile

- Einheitliche Codebasis, schnelle Entwicklung.
- Keine doppelte UI-Pflege.
- Rollen- und berechtigungsbasierte Zugriffskontrolle einfach umsetzbar (Berechtigungen aus `GET /auth/me`).

## Branding & Corporate Identity (CI)

**Leitbild (Bildsprache):** „Eine grüne Wiese in einem Tal mit Fluss, umrandet mit Wäldern und Bergen.“

Das Default-Branding startet mit einer **Farbskala** passend zu diesem Leitbild. Die **Login-Seite** nutzt bereits das SVG-Logo (`/static/logos/meadow_logo.svg`) und einen Wiesen-Hintergrund (WebP + PNG-Fallback, `/static/images/meadow_login_bg.webp`); statische Assets werden unter `/static` aus `meadow/frontend/static/` ausgeliefert. Im **Header** der Hauptseiten wird das SVG-Logo transparent (ohne weiße Box) direkt auf dem grünen `bg-primary`-Header angezeigt; da die Logo-Grüntöne dort nur ~1,5:1 Kontrast erreichen, ist für gute Sichtbarkeit ggf. eine weiße Monochrom-Variante vorgesehen. Weitere Grafiken (v. a. UI-Icons) folgen später und sind noch **nicht** Teil des MVP.

**Farbpalette (Startwerte):**

| Rolle      | Farbe          | Hex       | Tailwind-Äquivalent | Leitbild-Bezug               |
|------------|----------------|-----------|---------------------|------------------------------|
| Primär     | Meadow Green   | `#16a34a` | `green-600`         | Wiese (Markenfarbe)          |
| Sekundär   | River Blue     | `#0ea5e9` | `sky-500`           | Fluss                        |
| Akzent     | Sunlight Amber | `#d97706` | `amber-600`         | Sonnenlicht/Warmton          |
| Dunkel     | Mountain Slate | `#1e293b` | `slate-800`         | Berge/Wälder (Kontrast)      |
| Neutral    | Valley Mist    | `#f1f5f9` | `slate-100`         | Tal (heller Hintergrund)     |
| Erfolg     | Meadow Light   | `#22c55e` | `green-500`         | positiv (Status)             |

**Barrierefreiheit:** `#16a34a` als Primärfarbe erreicht mit weißer Schrift ca. 3,3:1 Kontrast (AA für große Schrift/UI-Komponenten, nicht für kleinen Fließtext). Für kleinen Text auf Weiß ist die dunklere Variante `#15803d` (`green-700`, ≥ 4,5:1) zu verwenden.

**Umsetzung in NiceGUI (verifiziert anhand der offiziellen Dokumentation):**

- **Globale Markenfarben:** `app.colors(primary='#16a34a', secondary='#0ea5e9', accent='#d97706', …)` setzt das Quasar-Theme app-weit; `ui.colors(…)` überschreibt es seitenweise. Quasar-Slots: `primary`, `secondary`, `accent`, `positive`, `negative`, `info`, `warning`, `dark`, `dark_page`.
- **Eigene Markenfarbe:** `app.colors(brand='#16a34a')` definiert eine Zusatzfarbe `brand`, nutzbar als `color='brand'` bzw. Tailwind-Klasse `text-brand`/`bg-brand` (muss vor der ersten Verwendung gesetzt werden).
- **Element-Ebene:** `.classes(…)`/`.default_classes(…)` nehmen Tailwind-, Quasar- und eigene CSS-Klassen an; `.style(…)` nimmt inline CSS. NiceGUI-Elemente tragen Quasar-Default-Styles – bei Überschreibung auf Element-Ebene ist daher häufig der Tailwind-`!`-Präfix (`!important`) nötig, z. B. `.classes("!bg-[#16a34a]/20 !text-[#0ea5e9]")` (Arbitrary-Value-Syntax `[#hex]`, Opacity-Modifier `/20`).
- **Seiten-CSS:** `ui.add_css(…)` ergänzt globale CSS-Regeln (dort ggf. mit `!important`); `ui.add_head_html(…)` für `<style>`/Meta.
- **Statische Assets:** `app.add_static_files('/static', 'meadow/frontend/static')` liefert Logo & Bilder aus; `/static` ist in `UNRESTRICTED_ROUTES` (`auth.py`) von der Auth-Middleware ausgenommen, damit die Login-Seite Branding-Assets laden kann.
- **Navigations-Drawer:** vertikaler Farbverlauf über `NAV_GRADIENT_TOP`/`NAV_GRADIENT_BOTTOM` (`theme.py`, aktuell `#2d5f4f` → `#4a7c59`). Der untere Farbwert bleibt dunkel genug, damit weiße Nav-Texte mindestens 4,5:1 Kontrast (WCAG AA) erreichen; für den Sprach-Selektor gilt zusätzlich eine `ui.add_css(..., shared=True)`-Regel im Drawer.
- Tailwind ist standardmäßig aktiv (`ui.run(tailwind=True)`); alternativ UnoCSS-Presets (`ui.run(unocss=…)`).

Die Palette sind **Startwerte**; sie werden später über eine zentrale Theme-Konstante bzw. die Admin-UI („System-Einstellungen“) pflegbar – die Quasar-Slots bleiben dabei die Implementierungs-Schnittstelle.

## Mehrsprachigkeit der Oberfläche

Die Weboberfläche von Meadow PIM wird von Beginn an **mehrsprachig konzipiert**. Dies betrifft ausschließlich die **UI-Texte** (Navigation, Buttons, Meldungen, Hilfe) – nicht die fachlichen Daten. Die Mehrsprachigkeit der Datenobjekte ist bereits über die flexiblen Metadaten gelöst und bleibt davon unabhängig.

**Geplante Sprachen:**

1. **Englisch** (Startsprache, internationaler Standard)
2. **Deutsch** (zweite Sprache, für den DACH-Markt)
3. **Französisch** (dritte Sprache, für den frankophonen Markt)
4. Weitere Sprachen nach Bedarf (z. B. Spanisch, Italienisch, Niederländisch)

**Technische Umsetzung in NiceGUI:**

- Verwendung einer internationalisierungsfähigen Struktur: Alle UI-Texte werden über **Übersetzungs-Keys** referenziert, nicht hart im Code verdrahtet.
- **Entscheidung (Schritt 2, umgesetzt):** schlanke **JSON-basierte** Übersetzungsverwaltung — eine Datei je Sprache (`meadow/frontend/locales/<locale>.json`), Zugriff über `t(key, locale)` in `meadow/frontend/i18n.py`; fehlende Keys fallen auf den Key selbst zurück (sofort sichtbar in Entwicklung). `gettext`/babel für Plurale bleibt eine spätere Option, falls nötig.
- Die Übersetzungsdateien werden im Frontend-Container ausgeliefert und können ohne Änderung am Code ergänzt werden.

**Sprachauswahl & Benutzereinstellung:**

- Die Sprache wird **pro Benutzer** festgelegt. **Umsetzung (Schritt 2):** aktuell in der NiceGUI-Session (`app.storage.user['locale']`); die Persistenz im Benutzerprofil (SurrealDB) folgt später.
- Beim ersten Login wird die Sprache automatisch aus den Browser-Einstellungen (`Accept-Language`) übernommen, sofern für den Benutzer noch keine Präferenz hinterlegt ist.
- Die Auswahl kann jederzeit über einen **Sprachumschalter** (im Drawer) geändert werden.
- Die gewählte Sprache wird in der Session gehalten und an alle nachfolgenden Seiten/Elemente weitergegeben.

**Umsetzung in NiceGUI:**

- NiceGUI bietet keine eingebaute i18n-Funktion, daher wird eine eigene kleine Abstraktionsschicht implementiert: `t(key, locale)` in `meadow/frontend/i18n.py` liefert den passenden Text aus der JSON-Datei (Default `en`).
- Alle UI-Elemente (Labels, Buttons, Dialoge) verwenden diese Funktion.
- Für Datums-, Zahlen- und Währungsformatierung werden die länderspezifischen Einstellungen der gewählten Sprache berücksichtigt (z. B. mit `babel`).

**Datumswerte in der Oberfläche:**

- Die GUI erhält Zeitwerte von der REST-API **immer als ISO-8601-UTC-String** (Konvention gemäß Abschnitt 4 / [datenmodell.md](datenmodell.md)).
- Die Anzeige erfolgt **lokalisiert**: Datum/Zeit werden in der Zeitzone und im Format des Benutzers dargestellt (über `babel`), z. B. `14.08.2026, 12:00` für `de-DE`.
- Benutzereingaben werden zur Speicherung nach **UTC normalisiert** und als ISO-8601-String an die API gesendet; der gespeicherte Wert bleibt unverändert UTC.

**Werte mit Einheiten in der Oberfläche:**

- Die GUI erhält quantifizierte Werte von der API als `{"value": …, "unit": …}` – in der Standard-Anzeigeeinheit der Attributdefinition (`display_unit_id`) bzw. in der per `?unit=` angeforderten Einheit.
- Die Anzeige erfolgt mit Symbol (z. B. `12,5 mm`); ein Einheiten-Umschalter am Eingabefeld erlaubt Darstellung und Eingabe in einer anderen Einheit derselben Dimension.
- Eingaben werden vor dem Speichern in die kanonische SI-Einheit umgerechnet und als `{"value": …, "unit": …}` an die API gesendet; gespeichert wird die reine Zahl in der kanonischen Einheit.

**Vorteile dieses Ansatzes:**

- Neue Sprachen können später durch einfaches Hinzufügen einer Übersetzungsdatei ergänzt werden.
- Die fachlichen Daten bleiben unberührt – Mehrsprachigkeit der Oberfläche ist strikt von der Datenmehrsprachigkeit getrennt.
- Das System ist von Anfang an auf internationale Nutzer vorbereitet, ohne den Kern zu beeinträchtigen.

## Modulübersicht (MVP)

Die Oberfläche fokussiert sich in der ersten Version auf die wesentlichen Arbeitsabläufe. Die konkrete Ausgestaltung der Arbeitsflächen (Grid, Formular, Massendatenpflege, Dashboard) sowie Vollständigkeit, Vererbungs-UX und Sprach-Handling stehen in [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md).

1. **Login & Benutzerverwaltung**
   - Anmeldung mit JWT, Passwort-Reset (optional)
   - Anzeige und Bearbeitung von Benutzern, Gruppen und Rollen (nur `admin`/`superadmin`)
   - **(Umgesetzt:)** `meadow/frontend/users.py` (`/users`, admin-only) – AG-Grid-Liste (Benutzername, E-Mail, Rollen, Status, Locale) + Anlegen/Bearbeiten/Löschen-Dialog (`POST`/`PATCH`/`DELETE /users`; Felder `username`, `email`, `password`, `roles`, `status`, `locale`). **„Login als“** je Zeile (`POST /auth/impersonate/{user_id}`, nur `superadmin`) – speichert das Ziel-Token als aktive Sitzung, zeigt ein **Impersonations-Banner** („Angemeldet als X“). `meadow/frontend/groups.py` (`/groups`, admin-only) – AG-Grid-Liste (Name, Grant-/Deny-Permissions, Scope, Branding) + Anlegen/Bearbeiten/Löschen-Dialog (`POST`/`PATCH`/`DELETE /groups`).

2. **Grid & Suche (Hauptarbeitsfläche)**
   - Grid-first mit `ui.aggrid`: Kategorien-/Strukturbaum, konfigurierbare/pinnbare Spalten, persönliche Ansichten
   - Inline-Editing mit Batch-Save, Vollständigkeits-Spalte, Volltextsuche

3. **Produkt-Formular (Drill-down)**
   - Eine scrollbare Seite mit einklappbaren Attributgruppen und Sprung-Menü
   - Hybrid-Sprache (Arbeitssprache + editierbare Spalten pro Sprache), Vererbungs-UX, typspezifische Wertedarstellung

4. **Massendatenpflege**
   - Bulk-Edit-Wizard (definierter Operationssatz, Ausführung als Hintergrundjob + Report)

5. **Attributdefinitionen & -gruppen**
   - Verwaltung von Attributgruppen und -definitionen (nur `admin`/`superadmin`)
   - Festlegung von Datentyp, Wertelisten, Mehrsprachigkeit, `inheritable`-Flag, Einheit (`unit_id`/`display_unit_id`), `is_key`-Markierung (Business-Schlüssel, z. B. SKU) sowie Template-Feldern (`applicable_types`/`required_types`)
   - **(Attributdefinitionen & -gruppen umgesetzt:)** `meadow/frontend/attribute_definitions.py` (`/attribute-definitions`, admin-only) – Liste (AG-Grid) + Anlegen/Bearbeiten-Dialog über `POST`/`PATCH /attribute-definitions`; Feldumfang: `type`, `label` (en/de), `group_ids` (viele-zu-viele), `inheritable`, `is_key`, `multilingual`, `applicable_types`/`required_types`, `unit_id`/`display_unit_id`, `constraints.enum_values`, `description` und `computed`-Felder. **Löschen** von Attributdefinitionen (`DELETE`, 409 bei Nutzung / `?force=true` → Hintergrundjob) mit Bestätigungsdialog. **Attributgruppen**: Inline-Verwaltung (`new_group`/`edit_group`/`delete_group`, inkl. `position`, `label`, optional `applicable_types`), **Mehrfachauswahl** der Gruppen pro Attribut im Dialog („in mehreren Gruppen“). Objekttyp-Zuordnung: **Attribut** via `applicable_types`/`required_types`, **Gruppe** via optionalem `applicable_types` (leer = aus Mitgliedern abgeleitet).

6. **Kategorien & Relationen**
   - Anlegen und Hierarchisieren von Kategorien als normale Objekte (mehrere Kategorie-Bäume möglich)
   - Zuordnung von Objekten zu Kategorien per Drag-and-Drop oder Auswahl

7. **Jobs & Scheduler**
   - Anzeige laufender und abgeschlossener Jobs (Historie)
   - Manuelles Starten von Job-Definitionen
   - Anzeige von Dead-Letter-Einträgen (nur `admin`)
   - **(Umgesetzt:)** `meadow/frontend/jobs.py` (`/jobs`, admin-only) mit zwei Tabs. **Definitionen**: AG-Grid-Liste (`name`, `type`, `schedule`, `enabled`, `updated`), Neuanlage/Bearbeiten-Dialog (Name, Typ, Cron-Zeitplan, `enabled`, `parameters` als JSON), **manuelles Ausführen** (`POST /jobs/{id}/run`) mit Bestätigungsdialog. **Läufe**: AG-Grid der `job_run`-Historie (neueste zuerst) mit Status-Badge (HTML-Spalte via `html_columns`), Trigger, Start/Fertig, Dauer, Fehler und **DLQ-Marker** (❗ bei „dead lettered“); Status-Filter + **„Nur Dead Letters“**-Schalter, Polling alle ~5 s, Doppelklick → Detail-Dialog (Status, Fehler, `result`-JSON).

8. **Sprachbaum-Administration**
   - Anzeige und Pflege des Sprachbaums (Knoten, Eltern, Fallback-Order)
   - Aktivieren/Deaktivieren von Sprachen für die Oberfläche und Datenpflege

9. **Dashboard (Arbeitsübersicht)**
   - Kacheln (Anzahl Objekte pro Typ/Status), Vollständigkeits-Shortcuts ins Grid, letzte Änderungen, Job-Status

10. **Business Rules & Rule Lab** (nur `admin`/`superadmin`)
    - Regel-Editor mit Compile-Check (unzulässige Skripte sind nicht speicherbar), Zustände `draft`/`active`/`disabled`, Versionierung
    - **Rule Lab**: Regel gegen eine Objektauswahl (oder ein Beispielobjekt/konstruierten Payload) ausführen – read-only, keine Persistenz; Anzeige von Verletzungen, berechneten Werten vor/nach, `log()`-Ausgabe; Dry-Run vor Aktivierung
    - Hinweis auf Circuit-Breaker-Auslösungen (debuggen nach Auto-`disabled`)

11. **System-Logs** (nur `admin`/`superadmin`)
    - Tabelle mit Zeit (lokalisiert), Dienst, Kategorie, Level, Message – neueste zuerst; Level farblich markiert (WARN/ERROR)
    - Filterleiste: Dienst, Kategorie, Level, Zeitraum, Textsuche auf `message`
    - Auto-Refresh per **Polling** (≈ 5 s, analog Job-Status); Detailansicht per Klick (volle `message`, `context` als JSON, `trace_id`) mit Kontext-Links zu Job/Objekt/Rule, sofern `context` IDs trägt
    - CSV-Export der gefilterten Ansicht (optional, Phase 8)

12. **System-Einstellungen** (nur `admin`/`superadmin`)
    - Logging-Einstellungen im Betrieb ändern (`log_persist_level`, `log_persist_categories`, `log_retention_days`, `log_rate_limit`); Anzeige der Herkunft (Default/ENV/DB), „Auf Default zurücksetzen“
    - Änderungen werden im Audit-Log protokolliert

**Spätere Ausbaustufen (nicht im MVP):**

- Erweiterte Suche mit Query-Builder („Advanced Search“), gespeicherte Suchen und materialisierte Collections
- Pinned Attributes und Attribut-Suchleiste im Formular
- Find & Replace, sequenzielles Editieren, Send-for-approval bei Bulk-Änderungen
- Kanalspezifische Freigaben (per-Kanal-Vollständigkeit)
- Starred-Entitäten, Work Areas/Aufgabenwarteschlangen
- Echtzeit-Updates über Websockets/NATS

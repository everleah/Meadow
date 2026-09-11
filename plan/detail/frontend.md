# Benutzeroberfläche (Frontend)

- **Hauptdokument:** Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 7, 9
- **Stand:** Entwurf (Admin-Module „Benutzer & Gruppen“, „Jobs & Scheduler“, „System-Logs“, „System-Einstellungen“ und Login-Branding umgesetzt)

Dieses Dokument beschreibt die Weboberfläche (NiceGUI): Architektur, Mehrsprachigkeit der Oberfläche und die Modulübersicht. Die konkreten **Arbeitsflächen** (Grid, Produkt-Formular, Massendatenpflege, Dashboard) sind im Detaildokument [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md) spezifiziert. Die Mehrsprachigkeit der **fachlichen Daten** (Sprachbaum) ist separat im Dokument [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md) beschrieben.

## Architektur

- **Separater Docker-Container** mit Python + NiceGUI.
- **Lage & Start (implementiert, Schritt 1):** Paket `meadow/frontend/`, Einstieg `python -m meadow.frontend`; Dev-Port `8081` (Kern `8080`). Konfiguration über `PIM_CORE_URL`, `PIM_FRONTEND_PORT`, `UI_STORAGE_SECRET`. Das Branding wird per `app.colors()` (Quasar-Theme) appliziert; Auth per App-Middleware, JWT + Rollen in `app.storage.user`.
- Der NiceGUI-Dienst kommuniziert ausschließlich über die REST-API des Rust-Kerns.
- **Ein Dienst für alle Benutzer**: Es wird keine getrennte Admin-Oberfläche aufgebaut. Stattdessen werden Menüpunkte, Seiten und Buttons abhängig von Benutzerrolle und Berechtigungen ein-/ausgeblendet.
- Authentifizierung erfolgt über JWT: NiceGUI nimmt Login-Daten entgegen, fordert ein Token vom Kern an und speichert es in der Session. **Sitzung vs. Präferenzen (umgesetzt):** `app.storage.user` enthält beides – die Anmeldung (`token`, Rollen, Impersonation, `must_change_password`) und die **Benutzerpräferenzen** (Sprache, gespeicherte Grid-Ansichten, sichtbare Spalten, angeheftete Attribute). Beim **Abmelden** (und beim abgelaufenen Token) wird deshalb nur die Anmeldung entfernt (`auth.SESSION_KEYS` → `auth.clear_session`), nicht der komplette Speicher: Präferenzen überleben Logout/Login, und der Speicher ist ohnehin pro Browser dauerhaft. **Abgelaufene Sitzung (umgesetzt, Phase 9):** Ein zentraler Response-Hook des geteilten Core-Clients (`api._watch_session`, registriert über `api.set_session_expired_handler`) erkennt **jeden** 401 mit der Meldung `invalid or expired token` – unabhängig davon, welche Seite den Aufruf gemacht hat – und ruft `auth.handle_session_expired()` auf: Anmeldung löschen und auf `/login?redirect_to=<aktuelle Seite>&expired=1` umleiten (die Login-Seite zeigt dann „Ihre Sitzung ist abgelaufen …“ und bringt den Benutzer nach dem Login zurück auf seine Seite). Ein **fehlgeschlagener Login** (401 `invalid credentials`) löst das **nicht** aus – die Meldung entscheidet –, und auf der Login-Seite selbst passiert nichts, damit die Fehlermeldung stehen bleibt.
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
   - **(Umgesetzt:)** **Filterleiste** über dem Grid (serverseitige Volltextsuche via `GET /objects/picker` + `GET /objects?ids=`, Umschalter „Nur unvollständige“ → `?missing_required=true`, Zurücksetzen); Filterzustand in der URL gespiegelt (`?incomplete&type&ancestor&category&collection&uncategorized&q`), Nachladen ohne Seiten-Reload. **Feste Spaltenreihenfolge:** Attributspalten liegen vor der Referenzspalte (`_refs`, Link-Icon → Referenz-Picker), die immer die **letzte** Spalte ist (`pinned: right`).

3. **Produkt-Formular (Drill-down)**
   - Eine scrollbare Seite mit einklappbaren Attributgruppen und Sprung-Menü
   - Hybrid-Sprache (Arbeitssprache + editierbare Spalten pro Sprache), Vererbungs-UX, typspezifische Wertedarstellung
   - **(Umgesetzt:)** **Attribut-Suchleiste** (filtert die Zeilen nach Label/Name, öffnet Treffer, Trefferzähler) und **Pinned Attributes** (`push_pin` pro Zeile → sticky Seitenleiste mit editierbaren Feldern, pro Objekttyp × Benutzer persistiert)
   - **(Umgesetzt:)** **Vollständigkeits-Badge in den Pickern** (Zielobjekt-Vollständigkeit aus `GET /objects/picker?locale=`) und **gemeinsamer Objekt-Picker** (`meadow/frontend/object_picker.py`, Tabs „Suche“/„Struktur“, lazy Kompositionsbaum, nicht erlaubte Zieltypen ausgegraut und nicht auswählbar, optional Mehrfachauswahl) im **Relations-Picker**, am **`reference`-Attribut**, im **Grid-Referenzdialog**, im **Bulk-Wizard** (Kategorie/Eltern), in der **Kategorien-Verwaltung (Move)**, im **Rule Lab**, in der **Export-Quelle „Selection“** und im **Kategorien-Abschnitt des Formulars**; der **Dialogkern** (`open_structure_picker`) trägt zusätzlich die **Elternauswahl der Sprachbaum-Administration** (baumförmig ohne Suche).

4. **Massendatenpflege**
   - Bulk-Edit-Wizard (definierter Operationssatz, Ausführung als Hintergrundjob + Report)
   - **(Umgesetzt, Phase 9):** **Sequenzielles Editieren** – ausgewählte Grid-Zeilen nacheinander im Formular bearbeiten (Queue im Session-Speicher, Fortschrittsbanner „Objekt i von n“, „Speichern & weiter“ / „Überspringen“ / Abbrechen), siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md) §3.2
   - **(Umgesetzt, Phase 9):** **Find & Replace** – Suchtext in den Textattributen der Auswahl ersetzen (Grid-Dialog, Ausführung als Bulk-Operation `replace_text` mit Report), siehe [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md) §3.2
   - **(Umgesetzt, Phase 9):** **Send-for-approval** – Auswahl per Operation `send_for_approval` (Zielstatus wählbar, Default `in_review`) in den Freigabe-Workflow überführen, wahlweise **nach** jeder datenändernden Operation („anschließend zur Freigabe senden“), siehe [workflow-freigaben.md](workflow-freigaben.md)

5. **Attributdefinitionen & -gruppen**
   - Verwaltung von Attributgruppen und -definitionen (nur `admin`/`superadmin`)
   - Festlegung von Datentyp, Wertelisten, Mehrsprachigkeit, `inheritable`-Flag, Einheit (`unit_id`/`display_unit_id`), `is_key`-Markierung (Business-Schlüssel, z. B. SKU) sowie Template-Feldern (`applicable_types`/`required_types`)
   - **(Attributdefinitionen & -gruppen umgesetzt:)** `meadow/frontend/attribute_definitions.py` (`/attribute-definitions`, admin-only) – Liste (AG-Grid) + Anlegen/Bearbeiten-Dialog über `POST`/`PATCH /attribute-definitions`; Feldumfang: `type`, `label` (en/de), `group_ids` (viele-zu-viele), `inheritable`, `is_key`, `multilingual`, `applicable_types`/`required_types`, `unit_id`/`display_unit_id`, `constraints.enum_values`, `description` und `computed`-Felder. **Löschen** von Attributdefinitionen (`DELETE`, 409 bei Nutzung / `?force=true` → Hintergrundjob) mit Bestätigungsdialog. **Attributgruppen**: Inline-Verwaltung (`new_group`/`edit_group`/`delete_group`, inkl. `position`, `label`, optional `applicable_types`), **Mehrfachauswahl** der Gruppen pro Attribut im Dialog („in mehreren Gruppen“). Objekttyp-Zuordnung: **Attribut** via `applicable_types`/`required_types`, **Gruppe** via optionalem `applicable_types` (leer = aus Mitgliedern abgeleitet).

6. **Kategorien & Relationen**
   - **(Umgesetzt, produktzentriert):** Die **Kategorie-Verwaltung** (`meadow/frontend/categories.py`, `/categories`, admin-only) pflegt die **Struktur** der Kategorie-Bäume: Kategorien als normale Objekte anlegen (`POST /objects`, `type: category`), **hierarchisieren** (mehrere unabhängige Kategorie-Bäume; Umhängen via `PATCH /objects/{id}/parent`), umbenennen und löschen (Soft-Delete-Kaskade, s. `DELETE /objects/{id}`). Detail-Panel zeigt Name/Status/ID, den **Mitgliederzähler** (members) und führt per **„Im Grid anzeigen“** in die Objektliste der Kategorie (`/products?category=<id>`) – bewusst **kein** nacktes Mitglieder-Listing (wenig aussagekräftig). Die **Zuordnung** erfolgt **produktzentriert** (vgl. Akeneo): im **Produkt-Formular** ein Abschnitt **„Kategorien“** mit dem **gemeinsamen Objekt-Picker** (Mehrfachauswahl, auf `category` beschränkt) und den zugeordneten Kategorien als **entfernbaren Chips** („Kategorien wählen“ → Übernehmen legt die `category_member`-Kanten via `POST /references` sofort an, ✕ entfernt sie via `DELETE /references/{id}`). Zusätzlich bleibt die **Bulk-Zuordnung** (Hinzufügen/Entfernen einer Kategorie für viele Objekte) im Bulk-Wizard erhalten. Die **Objekt-Navigation** nach Kategorie erfolgt über den **Kategorien-Baum im Grid** (`/products`), der den `category_member`-Filter nutzt.

7. **Jobs & Scheduler**
   - Anzeige laufender und abgeschlossener Jobs (Historie)
   - Manuelles Starten von Job-Definitionen
   - Anzeige von Dead-Letter-Einträgen (nur `admin`)
   - **(Umgesetzt:)** `meadow/frontend/jobs.py` (`/jobs`, admin-only) mit zwei Tabs. **Definitionen**: AG-Grid-Liste (`name`, `type`, `schedule`, `enabled`, `updated`), Neuanlage/Bearbeiten-Dialog (Name, Typ, Cron-Zeitplan, `enabled`, `parameters` als JSON), **manuelles Ausführen** (`POST /jobs/{id}/run`) mit Bestätigungsdialog. **Läufe**: AG-Grid der `job_run`-Historie (neueste zuerst) mit Status-Badge (HTML-Spalte via `html_columns`), Trigger, Start/Fertig, Dauer, Fehler und **DLQ-Marker** (❗ bei „dead lettered“); Status-Filter + **„Nur Dead Letters“**-Schalter, Polling alle ~5 s, Doppelklick → Detail-Dialog (Status, Fehler, `result`-JSON).

8. **Sprachbaum-Administration**
   - **(Umgesetzt:)** `meadow/frontend/languages.py` (`/languages`, admin/superadmin) zeigt den **Sprachbaum** als `ui.tree` (Wurzel → Basissprachen → regionale Varianten) und pflegt ihn: Knoten **anlegen** (`POST /languages`), **bearbeiten**/**umhängen** (`PATCH /languages/{code}`, Fallback-Reihenfolge wird bei Elternwechsel neu berechnet), **löschen** (`DELETE /languages/{code}`, nur ohne Kind-Sprachen; `root` unlöschbar) sowie **`active`** (Datenpflege) und **`is_ui`** (Oberfläche) als Häkchen. Der **Elternknoten wird im Baum gewählt** (`pick_parent` über den gemeinsamen Struktur-Picker: Sprachhierarchie als lazy Baum, `root` sowie die Sprache selbst und ihre Untersprachen ausgegraut, aber aufklappbar; Eintrag „Universal root“ = oberste Ebene) – kein flaches Select mehr.
   - **Aktivieren/Deaktivieren:** Das Produkt-Formular bietet nur **`active`** Daten-Sprachen an (Sprach-Dialog + „Haupteditiersprache“); der **UI-Sprachumschalter** im Drawer listet nur **`active` + `is_ui`** Sprachen (mit vorhandener Übersetzungsdatei). Dafür ist `build_shell` async und lädt die aktiven UI-Sprachen.

9. **Dashboard (Arbeitsübersicht)**
   - Kacheln (Anzahl Objekte pro Typ/Status), Vollständigkeits-Shortcuts ins Grid, letzte Änderungen, Job-Status

10. **Business Rules & Rule Lab** (nur `admin`/`superadmin`)
    - Regel-Editor mit Compile-Check (unzulässige Skripte sind nicht speicherbar), Zustände `draft`/`active`/`disabled`, Versionierung
    - **Rule Lab**: Regel gegen eine Objektauswahl (oder ein Beispielobjekt/konstruierten Payload) ausführen – read-only, keine Persistenz; Anzeige von Verletzungen, berechneten Werten vor/nach, `log()`-Ausgabe; Dry-Run vor Aktivierung
    - **(Umgesetzt:)** `meadow/frontend/rules.py` (`/rules`, admin/superadmin). **Regeln**: AG-Grid-Liste (Name, Typ, Status-Badge, `applies_to`, Version, Geändert/von) mit **Neu/Bearbeiten-Dialog** (`POST`/`PATCH /rules`; `name`, `type`, `description`, `applies_to`, **CodeMirror-Python-Editor**; Compile-Check durch das Backend, Fehler werden angezeigt), **Aktivieren/Deaktivieren** mit Bestätigung (`POST /rules/{id}/activate` bzw. `/disable`) und **Reload**. **Rule Lab**: Dialog mit drei Modi – **Reales Objekt** (`POST /rules/{id}/dry-run` mit `object_id`), **Objektauswahl** (`POST /rules/{id}/test-batch`, neu im Kern: prüft eine Regel against `object_ids`, read-only) und **Konstruierter Payload** (`dry-run` mit `object`); Ergebnis zeigt Fehler/Warnungen, berechnete Werte (vorher/nachher), `log()`-Ausgabe und Übergangs-Verdikt. Keine Persistenz. Neu im Kern: `RuleSpec` ist `Clone`; `POST /rules/{id}/test-batch` in `api/rules.rs` (Request `TestBatch { object_ids }`, Auswertung je Objekt, Audit `rule_test_batch`).
    - Hinweis auf Circuit-Breaker-Auslösungen (debuggen nach Auto-`disabled`)

11. **System-Logs** (nur `admin`/`superadmin`)
    - Tabelle mit Zeit (lokalisiert), Dienst, Kategorie, Level, Message – neueste zuerst; Level farblich markiert (WARN/ERROR)
    - Filterleiste: Dienst, Kategorie, Level, Zeitraum, Textsuche auf `message`
    - Auto-Refresh per **Polling** (≈ 5 s, analog Job-Status); Detailansicht per Klick (volle `message`, `context` als JSON, `trace_id`) mit Kontext-Links zu Job/Objekt/Rule, sofern `context` IDs trägt
    - CSV-Export der gefilterten Ansicht (optional, Phase 8)
    - **(Umgesetzt:)** `meadow/frontend/logs.py` (`/system-logs`, admin-only) – AG-Grid der Log-Einträge (neueste zuerst, füllt die **verbleibende Viewport-Höhe** via `height: calc(100vh - 318px)` + `domLayout: 'normal'`, so dass nur das Grid intern scrollt) mit **HTML-Level-Badge** (`html_columns`); Filterleiste (Dienst, Kategorie, Level, `from`/`to`-Zeitraum, Textsuche) und **Reload**; Polling alle ~5 s; **Doppelklick → Detail-Dialog** (vollständige `message` mit Copy-Button, Meta-Zeile Zeit/Dienst/Kategorie, `trace_id` und `context` als JSON sowie Kontext-Links) – `context`/`trace_id` werden bedingt gerendert und über die Zeilen-`id` gegen `logs` aufgelöst.

12. **System-Einstellungen** (nur `admin`/`superadmin`)
    - Laufzeit-Einstellungen im Betrieb ändern (`log_persist_level`, `log_persist_categories`, `log_retention_days`, `log_rate_limit`, `rules_degrade`, `rules_breaker_threshold`, `rules_timeout_ms`), gruppiert in eingeklappten, farbigen Abschnitten; Anzeige der Herkunft (Default/ENV/DB), „Auf Default zurücksetzen“
    - Änderungen werden im Audit-Log protokolliert
    - **(Umgesetzt:)** `meadow/frontend/settings.py` (`/system-settings`, admin-only) – eine Karte je Setting (Schlüssel + **Herkunfts-Badge** `default`/`env`/`db`) mit typspezifischem Editor (Select / Multi-Select / Zahl) sowie **Save** (`PUT /system/settings/{key}`) und **Reset mit Bestätigung** (`DELETE /system/settings/{key}`). Backend: `GET /system/settings?include_source=true` liefert `{key: {value, source}}` (ohne Param weiterhin die flache `{key: value}`-Map für den Worker).

13. **Konnektoren (Connectors)** (nur `admin`/`superadmin`)
    - Verwaltung der **Inbound-/Outbound-Konnektoren** (Anbindung externer Systeme): Tabelle `connector`, Endpunkte `GET`/`POST`/`PATCH`/`DELETE /connectors` – Konnektoren **auflisten** (Filter: `type`, `enabled`), **anlegen**, **bearbeiten**, **löschen** und **aktivieren/deaktivieren** (`enabled`)
    - Konfiguration je Konnektor (Typ `json_import`/`json_export`/`webhook_outbound`, `enabled`, `match_by_key`-Upsert, `emit_events`-Summary, Ziel-URL/Filter, Retry/Coalescing); Anzeige der Herkunft (persistiert in DB, Env nur als Bootstrap)
    - ~~**Geplant:** schema-getriebenes Konfigurationsformular~~ **(umgesetzt, Stage 1: `GET /connector-types` + `meadow/frontend/connector_form.py`)** — Felder je Konnektor-Typ aus dem Server-Schema (Bereiche als `ui.expansion`, Pflichtfelder, Hilfetexte, bedingte Felder via `show_if`, maskierte Geheimnisse, Key-Wert-Zeilen für Header), **JSON-Expertenmodus** als Rückfall (Tabs „Formular"/„JSON", Werte werden in beide Richtungen übernommen), Servervalidierung mit Feldpfad (`parameters.url: is required`) und Hinweisliste für nicht deklarierte Parameter. Speichern sendet nur geänderte Felder (`parameters_mode: merge`). Stage 2 (**„Verbindung testen"**, `json_import`/`json_export` als Job-Defaults) bleibt offen. Details: [konnektor-konfiguration.md](konnektor-konfiguration.md)
    - **(Umgesetzt:)** `meadow/frontend/connectors.py` (`/connectors`, admin/superadmin) – AG-Grid-Liste (Name, Typ, **Konfiguration**-Zusammenfassung, **Enabled-Badge**, Geändert/von) mit **Neu/Bearbeiten-Dialog** (`POST`/`PATCH /connectors`; `name`, `type`-Select, `enabled`-Switch, Tabs **Formular**/**JSON**), **Enable/Disable-Toggle** (`PATCH` mit `enabled`) und **Löschen mit Bestätigung** (`DELETE /connectors/{id}`). Der Typ-Filter und die Typ-Labels kommen aus `GET /connector-types`; die Modul-Konstante `CONNECTOR_TYPES` ist entfallen. **Doppelklick** öffnet den Dialog mit dem Datensatz aus der geladenen Liste (nicht mit der AG-Grid-Zeile — die enthält nur Anzeigefelder). Backend: Tabelle `connector`, `/connectors`-API, Multi-Konnektor-Fanout im Worker, Live-Reload (`integration-eventsystem.md`). `api.py` um `list_connectors`/`get_connector`/`create_connector`/`update_connector`/`delete_connector`/`list_connector_types` erweitert; `views.py` registriert `/connectors`; i18n-`connectors.*`, `connector_form.*`, `connector_fields.*` in en/de/fr.
    - **Formular-Modul:** `meadow/frontend/connector_form.py` rendert das Schema zu Widgets (`text`/`password`/`textarea`/`select`/`multiselect`/`switch`/`number`/`key_value`/`json`) und liest sie zurück. Die Änderungserkennung vergleicht die Formularwerte gegen die **Projektion der gespeicherten** Parameter (`project_fields`), nie gegen einen nach dem Umschalten neu erfassten Stand; daraus entsteht der Merge-Patch. Für Konnektor-Typen mit `wired: false` zeigt der Dialog statt des Formulars einen Hinweis (`note_key`) — heute gibt es keinen solchen Typ mehr.
    - **Verbindung testen (Stage 2c):** Der Dialog-Fuß hat einen Button `Test connection` (`POST /connectors/{id}/test`, nur für gespeicherte Konnektoren), der im Worker einen Test auslöst (Webhook: synthetisches Event; Import/Export: Trockenlauf der Profilwerte), den Lauf über `GET /jobs/runs?ids=…` pollt und das Ergebnis (Status, Dauer, Antwort-Auszug bzw. aufgelöste Werte) samt Klartext-Hinweis anzeigt.
    - **Geplant (Stage 3, skizziert):** eigene Konnektor-Typen (Plugin-Registry), getrennte Secret-Ablage, Konnektor-Profile im MCF-Export/-Import, Auto-Test nach dem Speichern und ein Konnektor-Status in der Liste. **Details:** [konnektor-konfiguration.md](konnektor-konfiguration.md), Abschnitt 14.

14. **Gespeicherte Suchen & Collections** (nur `admin`/`superadmin`)
    - Verwaltung der **gespeicherten Suchen** (`saved_search`) und **materialisierten Collections** (`collection`) in zwei Tabs; Ausführen einer Suche zeigt die Trefferzahl, Collections listen ihre Mitglieder und lassen sich synchron refreshen (`POST /collections/{id}/refresh`)
    - **(Umgesetzt:)** `meadow/frontend/saved_searches.py` (`/saved-searches`, admin-only) – die Suchkriterien werden im Dialog als **JSON** (Query-DSL) gepflegt; Collections pflegen Mitgliederzahl und `refresh_interval`
    - **Geplant:** schema-getriebenes **Suchformular** statt JSON (Feldkatalog `GET /search-fields`, Zeilen-Editor Feld/Operator/Wert, Servervalidierung, Tabs Formular/JSON, Vorschau-Zähler). **Details:** [suche-formular.md](suche-formular.md)

**Spätere Ausbaustufen (nicht im MVP):**

- Erweiterte Suche mit Query-Builder („Advanced Search“) – **Formular für gespeicherte Suchen geplant:** [suche-formular.md](suche-formular.md); gespeicherte Suchen und materialisierte Collections sind bereits umgesetzt (Modul 14)
- ~~Pinned Attributes und Attribut-Suchleiste im Formular~~ **(umgesetzt, Phase 9: `meadow/frontend/form.py` – Pin-Toggle je Zeile + sticky Seitenleiste, Suchfeld mit Trefferfilter)**
- ~~Find & Replace~~ **(umgesetzt, Phase 9: Grid-Dialog `find_replace` + Bulk-Operation `replace_text` im Worker)**, ~~sequenzielles Editieren~~ **(umgesetzt, Phase 9: `meadow/frontend/edit_queue.py` + Grid-Button `edit_note` + Fortschrittsbanner im Formular)**, ~~Send-for-approval bei Bulk-Änderungen~~ **(umgesetzt, Phase 9: Operation `send_for_approval` + Schalter „anschließend zur Freigabe“)**
- Kanalspezifische Freigaben (per-Kanal-Vollständigkeit)
- Starred-Entitäten, Work Areas/Aufgabenwarteschlangen
- Echtzeit-Updates über Websockets/NATS

# Benutzeroberfläche (Frontend)

- **Hauptdokument:** Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 7
- **Stand:** Entwurf

Dieses Dokument beschreibt die Weboberfläche (NiceGUI): Architektur, Mehrsprachigkeit der Oberfläche und die Modulübersicht. Die konkreten **Arbeitsflächen** (Grid, Produkt-Formular, Massendatenpflege, Dashboard) sind im Detaildokument [frontend-arbeitsflaechen.md](frontend-arbeitsflaechen.md) spezifiziert. Die Mehrsprachigkeit der **fachlichen Daten** (Sprachbaum) ist separat im Dokument [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md) beschrieben.

## Architektur

- **Separater Docker-Container** mit Python + NiceGUI.
- Der NiceGUI-Dienst kommuniziert ausschließlich über die REST-API des Rust-Kerns.
- **Ein Dienst für alle Benutzer**: Es wird keine getrennte Admin-Oberfläche aufgebaut. Stattdessen werden Menüpunkte, Seiten und Buttons abhängig von Benutzerrolle und Berechtigungen ein-/ausgeblendet.
- Authentifizierung erfolgt über JWT: NiceGUI nimmt Login-Daten entgegen, fordert ein Token vom Kern an und speichert es in der Session.
- **Asynchrone Updates** (z. B. Job-Status, Änderungsbenachrichtigungen) werden in der ersten Version über **REST-Polling** realisiert. Eine direkte NATS-Anbindung des Frontends ist für spätere Versionen möglich, wird im MVP aber bewusst vermieden, um die Architektur einfach zu halten.

## Vorteile

- Einheitliche Codebasis, schnelle Entwicklung.
- Keine doppelte UI-Pflege.
- Rollen- und berechtigungsbasierte Zugriffskontrolle einfach umsetzbar (Berechtigungen aus `GET /auth/me`).

## Mehrsprachigkeit der Oberfläche

Die Weboberfläche von Meadow PIM wird von Beginn an **mehrsprachig konzipiert**. Dies betrifft ausschließlich die **UI-Texte** (Navigation, Buttons, Meldungen, Hilfe) – nicht die fachlichen Daten. Die Mehrsprachigkeit der Datenobjekte ist bereits über die flexiblen Metadaten gelöst und bleibt davon unabhängig.

**Geplante Sprachen:**

1. **Englisch** (Startsprache, internationaler Standard)
2. **Deutsch** (zweite Sprache, für den DACH-Markt)
3. **Französisch** (dritte Sprache, für den frankophonen Markt)
4. Weitere Sprachen nach Bedarf (z. B. Spanisch, Italienisch, Niederländisch)

**Technische Umsetzung in NiceGUI:**

- Verwendung einer internationalisierungsfähigen Struktur: Alle UI-Texte werden über **Übersetzungs-Keys** referenziert, nicht hart im Code verdrahtet.
- Empfohlene Bibliothek: **`gettext`** (Standard in Python) oder eine schlanke JSON-basierte Übersetzungsverwaltung.
  - `gettext` bietet ausgereifte Werkzeuge (`.po`/`.mo`-Dateien, Pluralformen, Kontext).
  - JSON-Dateien sind einfacher zu pflegen, aber weniger mächtig bei Pluralisierung.
- Die Übersetzungsdateien werden im Frontend-Container ausgeliefert und können ohne Änderung am Code ergänzt werden.

**Sprachauswahl & Benutzereinstellung:**

- Die Sprache wird **pro Benutzer** festgelegt und im Benutzerprofil gespeichert (z. B. in SurrealDB).
- Beim ersten Login wird die Sprache automatisch aus den Browser-Einstellungen (`Accept-Language`) übernommen, sofern für den Benutzer noch keine Präferenz hinterlegt ist.
- Die Auswahl kann jederzeit über einen Sprachumschalter in der Oberfläche geändert werden.
- Die gewählte Sprache wird in der Session gehalten und an alle nachfolgenden Seiten/Elemente weitergegeben.

**Umsetzung in NiceGUI:**

- NiceGUI bietet keine eingebaute i18n-Funktion, daher wird eine eigene kleine Abstraktionsschicht implementiert, z. B. eine Funktion `t(key, locale)`, die den passenden Text aus der Übersetzungsdatei liefert.
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

2. **Grid & Suche (Hauptarbeitsfläche)**
   - Grid-first mit `ui.aggrid`: Kategorien-/Strukturbaum, konfigurierbare/pinnbare Spalten, persönliche Ansichten
   - Inline-Editing mit Batch-Save, Vollständigkeits-Spalte, Volltextsuche

3. **Produkt-Formular (Drill-down)**
   - Eine scrollbare Seite mit einklappbaren Attributgruppen und Sprung-Menü
   - Hybrid-Sprache (Arbeitssprache + editierbare Spalten pro Sprache), Vererbungs-UX, typspezifische Wertedarstellung

4. **Massendatenpflege**
   - Bulk-Edit-Wizard (definierter Operationssatz, Ausführung als Hintergrundjob + Report)

5. **Attributdefinitionen & -gruppen**
   - Verwaltung von Attributgruppen und -definitionen (nur `admin` oder `editor`)
   - Festlegung von Datentyp, Wertelisten, Mehrsprachigkeit, `inheritable`-Flag, Einheit (`unit_id`/`display_unit_id`), `is_key`-Markierung (Business-Schlüssel, z. B. SKU) sowie Template-Feldern (`applicable_types`/`required_types`)

6. **Kategorien & Relationen**
   - Anlegen und Hierarchisieren von Kategorien als normale Objekte (mehrere Kategorie-Bäume möglich)
   - Zuordnung von Objekten zu Kategorien per Drag-and-Drop oder Auswahl

7. **Jobs & Scheduler**
   - Anzeige laufender und abgeschlossener Jobs (Historie)
   - Manuelles Starten von Job-Definitionen
   - Anzeige von Dead-Letter-Einträgen (nur `admin`)

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

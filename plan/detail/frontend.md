# Benutzeroberfläche (Frontend)

- **Hauptdokument:** Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 7
- **Stand:** Entwurf

Dieses Dokument beschreibt die Weboberfläche (NiceGUI). Die Mehrsprachigkeit der **Oberfläche** ist unten im Abschnitt „Mehrsprachigkeit der Oberfläche“ beschrieben; die Mehrsprachigkeit der **fachlichen Daten** (Sprachbaum) ist separat im Dokument [sprachbaum-mehrsprachigkeit.md](sprachbaum-mehrsprachigkeit.md) beschrieben.

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

**Vorteile dieses Ansatzes:**

- Neue Sprachen können später durch einfaches Hinzufügen einer Übersetzungsdatei ergänzt werden.
- Die fachlichen Daten bleiben unberührt – Mehrsprachigkeit der Oberfläche ist strikt von der Datenmehrsprachigkeit getrennt.
- Das System ist von Anfang an auf internationale Nutzer vorbereitet, ohne den Kern zu beeinträchtigen.

## Kernfunktionen der Oberfläche (MVP)

Die Oberfläche fokussiert sich in der ersten Version auf die wesentlichen Arbeitsabläufe. Folgende Module sind vorgesehen:

1. **Login & Benutzerverwaltung**
   - Anmeldung mit JWT, Passwort-Reset (optional)
   - Anzeige und Bearbeitung von Benutzern, Gruppen und Rollen (nur `admin`/`superadmin`)

2. **Objektliste & Suche**
   - Listenansicht aller Objekte, filterbar nach Typ, Status, Kategorie
   - Einfache Volltextsuche
   - Cursor-basierte Pagination

3. **Objektbearbeitung**
   - Formular zur Anzeige und Bearbeitung der flexiblen Attribute
   - Mehrsprachige Eingabefelder gemäß Sprachbaum (z. B. Reiter pro Sprache)
   - Anzeige **vererbter Werte** inkl. Herkunft (Quelle) und Warnhinweis bei Konflikten
   - Setzen von leeren Werten zur Vererbungsunterbrechung
   - Statuswechsel gemäß Zustandsmaschine (Workflow)
   - Anzeige von Referenzen und Kategorien des Objekts

4. **Attributdefinitionen & -gruppen**
   - Verwaltung von Attributgruppen und -definitionen (nur `admin` oder `editor`)
   - Festlegung von Datentyp, Wertelisten, Mehrsprachigkeit und `inheritable`-Flag

5. **Kategorien & Relationen**
   - Anlegen und Hierarchisieren von Kategorien als normale Objekte
   - Zuordnung von Objekten zu Kategorien per Drag-and-Drop oder Auswahl

6. **Jobs & Scheduler**
   - Anzeige laufender und abgeschlossener Jobs (Historie)
   - Manuelles Starten von Job-Definitionen
   - Anzeige von Dead-Letter-Einträgen (nur `admin`)

7. **Sprachbaum-Administration**
   - Anzeige und Pflege des Sprachbaums (Knoten, Eltern, Fallback-Order)
   - Aktivieren/Deaktivieren von Sprachen für die Oberfläche und Datenpflege

8. **Einfache Dashboard-Ansicht**
   - Überblick über Anzahl Objekte pro Typ/Status
   - Letzte Änderungen (Audit-Log Auszug)

**Spätere Ausbaustufen (nicht im MVP):**

- Erweiterte Suche mit Query-Builder („Advanced Search“)
- Verwaltung gespeicherter Suchen und materialisierter Collections
- Kanalspezifische Freigaben
- Echtzeit-Updates über Websockets/NATS

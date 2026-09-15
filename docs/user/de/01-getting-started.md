# Erste Schritte

Meadow PIM verwaltet Ihre Produktinformationen: Objekte, ihre Merkmale, ihre
Beziehungen untereinander und ihre Freigaben in Ausgabekanäle. Dieses Kapitel
erklärt die Oberfläche und die Begriffe, die in allen weiteren Kapiteln
vorausgesetzt werden.

## Anmelden

1. Öffnen Sie die Adresse Ihrer Meadow-Instanz im Browser.
2. Geben Sie Benutzername und Passwort ein.
3. Nach dem Anmelden landen Sie auf dem **Dashboard**.

Wurden Sie abgemeldet, weil Ihre Sitzung abgelaufen ist, erscheint ein Hinweis
auf der Anmeldeseite. Nach dem Anmelden kehren Sie automatisch zu der Seite
zurück, die Sie aufrufen wollten.

Den Zugang erhalten Sie von Ihrer Administration. Beim ersten Aufbau einer
Instanz existiert ein vorkonfiguriertes Administratorkonto — lassen Sie dessen
Passwort sofort ändern.

## Das Handbuch

Diese Seite ist das Handbuch. Links stehen die Kapitel, oben die Suche; die
Sprache des Handbuchs wählen Sie rechts oben (unabhängig von der Sprache der
Oberfläche).

![Die Handbuchseite mit Kapitelliste und Suche](/help-assets/help.png)

Wo in Meadow ein **Info-Button** (❓) steht, erklärt sein Tooltip die Sache in
einem Satz und führt per Klick in das passende Kapitel.

## Eigenes Passwort ändern

Über das **Schlüssel-Symbol** in der Kopfzeile öffnen Sie den Dialog zum
Passwortwechsel. Er verlangt das aktuelle Passwort und ein neues Passwort mit
Mindestlänge. Solange das Passwort noch nie geändert wurde, weist Sie ein Banner
in der Oberfläche darauf hin.

## Aufbau der Oberfläche

- **Kopfzeile** — angemeldeter Benutzer, Passwortwechsel, Abmelden.
- **Navigationsleiste** (links) — alle Arbeitsbereiche.
- **Sprachwahl** (unten in der Navigationsleiste) — Sprache der **Oberfläche**.
  Sie hat nichts mit den Sprachen der Produktdaten zu tun.
- **Arbeitsfläche** (rechts) — die jeweils gewählte Seite.

Ihre Rolle entscheidet, welche Menüpunkte und Schaltflächen verfügbar sind:
Bearbeiten setzt eine Editor-Rolle voraus, die Verwaltungsseiten
(Attributdefinitionen, Kategorien, Jobs, Benutzer, Regeln, Konnektoren …) eine
Administrator-Rolle.

## Dashboard

Das Dashboard ist Ihre Startseite:

- **Objekte** — Anzahl der Objekte je Typ.
- **Vollständigkeit** — wie viele Objekte je Typ noch Pflichtangaben vermissen.
  Ein Klick auf eine Kachel öffnet die gefilterte Objektliste.
- **Status** — Verteilung über die Workflow-Status.
- **Letzte Aktivität** und **Letzte Jobs** — die jüngsten protokollierten
  Ereignisse und Hintergrundläufe.

## Wo finde ich was

| Aufgabe | Kapitel |
| --- | --- |
| Objekte suchen, filtern, direkt in der Tabelle ändern | [Objektliste (Grid)](/help?page=product-grid) |
| Ein Objekt vollständig pflegen | [Objekt bearbeiten](/help?page=product-form) |
| Daten aus CSV, Excel oder JSON einspielen | [Daten importieren](/help?page=import) |
| Daten als CSV, Excel, JSON oder BMEcat herausgeben | [Daten exportieren](/help?page=export) |

## Gut zu wissen

- **Alles ist ein Objekt.** Produkte, Kategorien, Baugruppen und
  Klassifikationsknoten liegen im selben Datenmodell und unterscheiden sich nur
  durch ihren **Typ**.
- **Merkmale sind definiert.** Welche Felder ein Objekttyp haben kann, steht in
  den Attributdefinitionen — nicht jedes Objekt zeigt dieselben Felder.
- **Werte können vererbt werden.** In der Struktur (Baugruppe → Bauteil) müssen
  Sie gemeinsame Angaben nur einmal pflegen.

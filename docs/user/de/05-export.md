# Daten exportieren

Unter **Export** geben Sie Objekte als Datei heraus — für Lieferungen an
Partner, für Auswertungen oder für einen Austausch im MEF-Format. Der Assistent
führt in vier Schritten durch die Konfiguration.

![Der Export-Assistent mit Quelle, Feldern, Format und Vorschau](/help-assets/export.png)

## 1. Quelle

Wählen Sie, **welche** Objekte exportiert werden:

| Quelle | Bedeutung |
| --- | --- |
| **Gesamter Bestand** | Alle Objekte der Instanz — der Komplett-Export für einen Umzug auf ein frisches System. |
| **Objekttyp** | Alle Objekte eines Typs. |
| **Kategorie** | Objekte, die einer Kategorie zugeordnet sind. |
| **Collection** | Die Mitglieder einer gespeicherten Zusammenstellung. |
| **Gespeicherte Suche** | Die Treffer einer gespeicherten Suche. |
| **Auswahl** | Eine Liste von Objekt-IDs, die Sie eintippen oder über die Baumsuche zusammenstellen. |

## 2. Felder

Wählen Sie die Spalten, die die Datei enthalten soll: die Basisfelder `id`,
`type` und `status` sowie die Attribute. Die Auswahl ist nach Attributgruppen
gegliedert und auf die Attribute beschränkt, die der gewählte Objekttyp
überhaupt zulässt — was das Objekt nicht haben kann, wird gar nicht erst
angeboten.

Mehrsprachige Attribute erzeugen **je Datensprache eine Spalte**. Das Vokabular
eines importierten Klassifikationsstandards bleibt standardmäßig ausgeblendet;
über den entsprechenden Schalter können Sie es zusätzlich anbieten.

## 3. Format und Optionen

| Format | Ergebnis |
| --- | --- |
| **CSV** | Textdatei mit wählbarem Trennzeichen. |
| **Excel** | Arbeitsmappe (`.xlsx`). |
| **JSON** | MEF-Dokument für den maschinellen Austausch: Objekte **mit** ihren Beziehungen (Komposition, Kategorien, Collections) und ihren Kanalfreigaben. |
| **BMEcat** | Katalogformat für Klassifikationsstandards (z. B. ETIM). |

Weitere Optionen:

- **Sprachen** — eine oder mehrere Datensprachen.
- **Kopfzeile** — mit oder ohne Spaltenüberschriften.
- **Systemfelder aufnehmen** — ergänzt technische Felder wie Zeitstempel.
- **Auch vererbte Werte exportieren** — nimmt Werte mit auf, die das Objekt
  selbst nicht pflegt, sondern vom übergeordneten Objekt erbt.
- **Übergeordnete Objekte mitnehmen** — ergänzt die übergeordneten Objekte der
  Auswahl. Ein Objekt wird beim Import immer **mit** seinem Elternteil
  geschrieben; ohne diesen Schalter könnte eine Auswahl (etwa nur Varianten)
  nicht wieder eingespielt werden. Beim Komplett-Export ändert er nichts.

### BMEcat

Beim Format BMEcat ersetzt eine eigene Karte die Feldauswahl, weil der Standard
den Aufbau vorschreibt. Sie hinterlegen dort die Katalogdaten (Kennung, Name,
Sprache, Währung, Lieferant), das Attribut, dessen Wert zur Artikelnummer wird,
und die Version des Standards. Braucht die gewählte Lieferung Preise, werden
zusätzlich Preisattribut und Mengeneinheiten abgefragt. Das Ergebnis wird gegen
das Schema der gewählten Version geprüft; der Bericht nennt Klassen, Merkmale
und alles, was nicht geschrieben werden konnte.

## 4. Vorschau und Export

Die **Vorschau** zeigt die entstehenden Spalten und eine Objektzahl. **Export**
startet den Lauf im Hintergrund. Der Bericht nennt Anzahl, Spalten und Format
und bietet die fertige Datei zum **Herunterladen** an.

## Gespeicherte Profile

Häufig wiederkehrende Einstellungen speichern Sie als **Profil**: Format,
Felder, Sprachen, Optionen und der BMEcat-Block. Eine Auswahl oben im Assistenten
lädt ein Profil und füllt das Formular. Gespeichert werden bewusst **nicht** die
Quelle und die E-Mail-Empfänger — beides gehört zum einzelnen Lauf, nicht zur
Konfiguration.

## Automatisch exportieren

Administratoren können einen Export **planen**: Der Lauf wiederholt sich dann
nach einem Zeitplan, und die Ergebnisse bleiben in der Liste der letzten Exporte
abrufbar. Ist ein Mailversand konfiguriert, kann der Export zusätzlich per
E-Mail verschickt werden.

## Komplette Wiederherstellung (Umzug auf ein frisches System)

Ein PIM-Bestand besteht aus zwei Teilen, und beide werden getrennt exportiert:

1. **Modell und Metadaten** — unter **Konfiguration** alle Bereiche exportieren
   (MCF-Datei): Objekttypen, Attributdefinitionen, Relationstypen, Regeln,
   Einheiten, Sprachen, Kanäle, Klassifikationsstandards.
2. **Daten** — hier im Assistenten die Quelle **Gesamter Bestand** im Format
   **JSON** wählen (MEF-Datei): Objekte, Struktur (Komposition), Kategorien,
   Collections, Verknüpfungen und Kanalfreigaben.

Auf dem frischen System dann in dieser Reihenfolge importieren: **zuerst** das
MCF-Dokument unter **Konfiguration**, **danach** das MEF-Dokument unter
**Import** (dort wird die Option „Über Objekt-ID abgleichen“ automatisch
aktiviert). Beide Importe sind wiederholbar: ein zweiter Lauf aktualisiert,
statt zu verdoppeln.

Nicht im Dokument enthalten sind **Benutzer und Gruppen**, **Konnektoren**,
**Job-Definitionen** und **Systemeinstellungen** — das ist Umgebungskonfiguration
und wird je Umgebung gepflegt.

## Weiterlesen

- [Daten importieren](/help?page=import)
- [Objektliste (Grid)](/help?page=product-grid)
- [Objekt bearbeiten](/help?page=product-form)

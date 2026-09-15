# Daten exportieren

Unter **Export** geben Sie Objekte als Datei heraus — für Lieferungen an
Partner, für Auswertungen oder für einen Austausch im MEF-Format. Der Assistent
führt in vier Schritten durch die Konfiguration.

![Der Export-Assistent mit Quelle, Feldern, Format und Vorschau](/help-assets/export.png)

## 1. Quelle

Wählen Sie, **welche** Objekte exportiert werden:

| Quelle | Bedeutung |
| --- | --- |
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
| **JSON** | MEF-Dokument für den maschinellen Austausch. |
| **BMEcat** | Katalogformat für Klassifikationsstandards (z. B. ETIM). |

Weitere Optionen:

- **Sprachen** — eine oder mehrere Datensprachen.
- **Kopfzeile** — mit oder ohne Spaltenüberschriften.
- **Systemfelder aufnehmen** — ergänzt technische Felder wie Zeitstempel.
- **Auch vererbte Werte exportieren** — nimmt Werte mit auf, die das Objekt
  selbst nicht pflegt, sondern vom übergeordneten Objekt erbt.

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

## Weiterlesen

- [Daten importieren](/help?page=import)
- [Objektliste (Grid)](/help?page=product-grid)
- [Objekt bearbeiten](/help?page=product-form)

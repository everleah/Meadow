# Objekt bearbeiten

Das Objektformular zeigt **ein** Objekt mit allen seinen Merkmalen und
Beziehungen. Sie erreichen es durch einen Doppelklick auf eine Zeile in der
[Objektliste](/help?page=product-grid).

![Das Objektformular mit Abschnitten, angehefteten Attributen und Kanälen](/help-assets/product-form.png)

## Aufbau

- **Kopfbereich** — Name, Typ, Status, Vollständigkeit sowie die Aktionen des
  Objekts (z. B. *Vererbung konsolidieren*).
- **Abschnitte** — die Merkmale, gruppiert nach Attributgruppen, gefolgt von
  *Kategorien*, *Relationen*, *Kanäle* und *Klassen-Merkmale*. Ein Klick auf die
  Überschrift klappt einen Abschnitt auf oder zu.
- **Seitenleiste** — Ihre angehefteten Attribute für den schnellen Zugriff.
- **Sprung-Menü** — springt direkt zu einem Abschnitt.
- **Attributsuche** — blendet nur die passenden Merkmale ein.
- **Nur fehlende Pflichtfelder** — zeigt nur, was noch fehlt.

## Werte pflegen

Welche Eingabe ein Merkmal erwartet, ergibt sich aus seinem Typ: Text, Zahl mit
Einheit, Auswahl aus einer Werteliste, Datum, URL (mit Vorschau- und
Öffnen-Schaltfläche) oder Bereich aus zwei Zahlen. Pflichtfelder sind mit `*`
markiert.

**Gesperrte oder fehlende Felder** haben einen Grund: Eine Attributdefinition
kann für diesen Objekttyp gar nicht gelten, oder ein Wert ist berechnet und wird
von einer Regel bestimmt. Gesperrte Felder zeigen den Grund als Hinweis.

## Mehrsprachige Werte

Ist ein Merkmal mehrsprachig, steht im Feld die aktuelle Bearbeitungssprache.
Das **Sprachsymbol** daneben öffnet einen Dialog, in dem Sie denselben Wert in
allen aktiven Datensprachen pflegen.

Fehlt ein Wert in der gewählten Sprache, zeigt Meadow den Wert der
übergeordneten Sprache (Fallback) gedimmt an. Sobald Sie tippen, entsteht ein
eigener Wert für diese Sprache; Löschen bringt den Fallback zurück.

## Vererbung

In der Struktur (Baugruppe → Bauteil) müssen gemeinsame Angaben nur einmal
gepflegt werden. Ein geerbter Wert ist **gedimmt** und trägt ein
Herkunftssymbol; der Tooltip nennt das Objekt, von dem der Wert kommt.

Das **Wertaktions-Menü** neben einem vererbbaren Merkmal bietet:

- **Auf vererbt zurücksetzen** — entfernt den eigenen Wert, sodass wieder der
  übergeordnete Wert gilt.
- **Leer setzen** — unterbricht die Vererbung bewusst mit einem leeren Wert.

Auf übergeordneten Objekten steht zusätzlich **Vererbung konsolidieren**: Sind
sich alle direkten Kinder bei einem Merkmal einig, wandert der Wert nach oben
und wird bei den Kindern entfernt — sie erben ihn dann. Die Aktion zeigt zuerst
eine Vorschau und meldet danach, was übernommen und was übersprungen wurde.

## Kategorien

Der Abschnitt **Kategorien** enthält je Zuordnungsart einen eigenen Block (die
primäre Kategorisierung zuerst, danach weitere Dimensionen). Über die
Auswahl-Schaltfläche öffnen Sie die Suche, wählen eine oder mehrere Kategorien
und ordnen sie zu; jede Zuordnung erscheint als Chip und lässt sich über das ✕
wieder entfernen.

Der Abschnitt **Klassen-Merkmale** zeigt, welche Merkmale die Klassen
verlangen, denen das Objekt zugeordnet ist. Pflichtmerkmale zählen in die
Vollständigkeit; fehlende sind entsprechend markiert.

## Relationen und Kanäle

- **Relationen** — verbindet das Objekt mit anderen Objekten (z. B.
  Zubehör oder Alternativartikel). Welche Relationstypen erlaubt sind, hängt von
  den beteiligten Objekttypen ab.
- **Kanäle** — gibt das Objekt für einen Ausgabekanal frei. Der Kanalstatus ist
  vom globalen Status unabhängig: Ein Objekt kann global freigegeben, aber für
  einen Kanal noch nicht veröffentlicht sein. Verlangt eine Regel eine
  bestimmte Reihenfolge, meldet Meadow eine abgelehnte Freigabe als Hinweis.

## Speichern

**Speichern** schreibt alle Änderungen; **Verwerfen** stellt den letzten
gespeicherten Stand wieder her. Geleerte mehrsprachige Felder werden dabei
wirklich entfernt, damit die Vererbung wieder greifen kann.

Beim **sequenziellen Bearbeiten** (aus der Objektliste gestartet) zeigt der
Kopfbereich den Fortschritt und bietet **Speichern & weiter** — so arbeiten Sie
eine Auswahl objektweise durch, ohne die Liste erneut zu öffnen.

## Weiterlesen

- [Objektliste (Grid)](/help?page=product-grid)
- [Daten exportieren](/help?page=export)

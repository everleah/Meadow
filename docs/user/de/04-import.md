# Daten importieren

Unter **Import** spielen Sie Objekte aus einer Datei ein. Der Import läuft
zweistufig: erst prüfen, dann ausführen. Ausgeführt wird er als Job, dessen
Ergebnis Sie anschließend im Bericht sehen.

![Die Importseite mit Dateiauswahl und Zuordnung](/help-assets/import.png)

## Datei hochladen

Meadow erkennt das Format an der Datei:

- **CSV** (`.csv`) und **Excel** (`.xlsx`, `.xlsm`) — tabellarisch, mit
  Spalten-Mapping.
- **MEF-JSON** (`.json`, `.mef`) — Meadow Exchange Format, das Austauschformat
  für Objekte und Referenzen.

Bei einer Arbeitsmappe mit mehreren Tabellenblättern wählen Sie das gewünschte
**Arbeitsblatt** aus.

## Spalten zuordnen (CSV/Excel)

Für jede Spalte der Datei legen Sie fest, wohin ihr Inhalt gehört:

- **Ignorieren** — die Spalte wird nicht importiert.
- **Basisfeld** — `id`, `type` oder `status`.
- **Merkmal** — ein Attribut aus Ihren Attributdefinitionen. Eigene
  Attributnamen können Sie direkt eintippen, wenn die Liste sie noch nicht
  enthält.
- **Merkmal in einer Sprache** — als `Attribut [Sprache]`; der Wert landet als
  Wert dieser Datensprache.
- **Konventionsfeld** — Objektname oder Geschäftsschlüssel. Solche Felder haben
  keine Attributdefinition; ohne Sprachzusatz wird der Wert in der aktuellen
  Oberflächensprache gespeichert.

**Vorschau** zeigt die ersten Zeilen mit Ihrer Zuordnung, bevor etwas
geschrieben wird.

## Wiederekennung von Objekten

Ob ein Objekt neu angelegt oder aktualisiert wird, steuern Sie über:

- **Geschäftsschlüssel** — vergleicht anhand eines eindeutigen Merkmals
  (z. B. Artikelnummer). Nur so bleiben wiederholte Importe idempotent.
- **Quelle** — eine Kennzeichnung des liefernden Systems; zusammen mit der
  Objekt-ID der Datei dient sie als Referenz für spätere Importe.
- **Über Objekt-ID abgleichen** (MEF) — aktualisiert genau die Objekte, deren
  Meadow-ID im Dokument steht. Meadow setzt diese Option automatisch, wenn das
  Dokument aus Meadow selbst stammt; ein so erzeugter Export lässt sich damit
  verlustfrei zurückspielen.

Mit **Änderungs-Events senden** entscheiden Sie, ob der Import die
nachgelagerten Systeme (Konnektoren, Webhooks) informiert.

## MEF-JSON

Ein MEF-Dokument importieren Sie ohne Spalten-Mapping. Die Oberfläche zeigt
stattdessen eine Zusammenfassung: Formatversion, Quelle, Anzahl der Objekte und
Referenzen sowie die vorkommenden Typen. Referenzen werden mit den Objekten
zusammen angelegt, sodass Verknüpfungen aus dem Dokument erhalten bleiben —
ein aus Meadow exportiertes Dokument bringt die Struktur (Komposition),
Kategorien, Collections und Kanalfreigaben mit und stellt sie im Zielsystem
wieder her.

## Ausführen und Ergebnis

Nach **Import starten** läuft der Import im Hintergrund. Der Bericht listet am
Ende je Objekt, ob es angelegt, aktualisiert oder übersprungen wurde; Fehler
einzelner Objekte stoppen den übrigen Import nicht. Die vollständigen
Laufdaten finden Sie zusätzlich unter **Jobs**.

## Grenzen und Fallstricke

- Die **erste Zeile** gilt als Kopfzeile.
- Zahlen mit Einheit werden beim Import in die kanonische Einheit umgerechnet.
- Ein fehlender Typ fällt auf einen Standardtyp zurück, ein fehlender Status auf
  den Entwurfsstatus — prüfen Sie die Zuordnung daher in der Vorschau.
- Ohne Geschäftsschlüssel oder ID-Abgleich legt jeder Import **neue** Objekte
  an. Genau das ist der häufigste Grund für Dubletten.
- Steht im Dokument eine **Zugehörigkeit** (`depends_on`, z. B. Variante an
  Produkt), legt der Import die Objekte in der Reihenfolge des Baums an: der
  Elternteil zuerst. Fehlt der Elternteil im Dokument **und** im System, wird das
  Objekt **nicht** angelegt und als Fehler gemeldet — so landet es nicht an der
  falschen Stelle in der Struktur.

## Weiterlesen

- [Daten exportieren](/help?page=export)
- [Objektliste (Grid)](/help?page=product-grid)

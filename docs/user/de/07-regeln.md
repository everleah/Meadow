# Business-Regeln

Regeln sind kurze Python-Skripte, die Meadow bei bestimmten Vorgängen selbst
ausführt: beim Speichern eines Objekts, beim Import, beim Statuswechsel oder
beim Freigeben für einen Kanal. Sie prüfen Werte, füllen berechnete Merkmale,
normalisieren Eingaben und bewachen Freigaben — die Logik liegt damit im System
und nicht in der Disziplin der Bearbeiter.

![Die Regelliste mit Filtern und Statusspalten](/help-assets/rules.png)

## Wofür Regeln da sind

- **Prüfen** — ein Wert muss ein Format haben (Artikelnummer, GTIN) oder eine
  Regel einhalten („Verkaufspreis nie unter Einkaufspreis").
- **Berechnen** — ein Merkmal wird aus anderen Merkmalen abgeleitet, statt von
  Hand gepflegt zu werden.
- **Normalisieren** — Eingaben werden vereinheitlicht (Leerzeichen, Groß- und
  Kleinschreibung, Einheiten).
- **Freigaben bewachen** — ein Status- oder Kanalwechsel wird nur erlaubt, wenn
  bestimmte Bedingungen erfüllt sind.

## Regeltypen

| Typ | Wirkung |
| --- | --- |
| **Validierung** | Meldet Fehler (blockieren das Speichern) oder Warnungen (nur Hinweis). |
| **Berechnet** | Schreibt Werte über `result.set_value(...)` — nur für Merkmale, die als *berechnet* deklariert sind. |
| **Normalisierung** | Korrigiert eingehende Werte still, statt sie abzulehnen. |
| **Workflow-Wächter** | Entscheidet mit `result.allow(...)` über eine Freigabe. |
| **Bulk** | Läuft über eine Massenaktion, nicht beim einzelnen Speichern. |

## Status: Entwurf, Aktiv, Deaktiviert

Nur **aktive** Regeln werden ausgeführt. Neue Regeln entstehen als *Entwurf*:
Sie sind gespeichert, greifen aber in nichts ein. Über die Schaltflächen in der
Kopfzeile aktivieren oder deaktivieren Sie eine Regel; Löschen entfernt sie
dauerhaft. Jede Änderung erhöht die **Version** und hält fest, wer geändert hat.

## Eine Regel anlegen

Der Editor hat vier Felder:

- **Name** — eindeutig, erscheint im Protokoll und im Audit.
- **Typ** — siehe Tabelle oben.
- **Beschreibung** — wofür die Regel gut ist. Beim *Erklären* einer bestehenden
  Regel ist dies das Ziel.
- **Gilt für Objekttypen** — leer bedeutet: für **alle** Typen.
- **Python-Skript** — die Regel selbst.

## Vertrag & Politik

Im Editor liegt unter dem Skript der aufklappbare Bereich **Vertrag & Politik**.
Er wird aus der Sandbox selbst erzeugt und zeigt genau das, was eine Regel
verwenden darf — er ist der verlässlichste Spickzettel:

- **Einstiegspunkt** — jede Regel definiert genau eine Funktion
  `run(obj, ctx, result)`.
- **`obj`** — das Objekt als schreibgeschützte Sicht: `obj["attributes"]` liest
  die Merkmale, `obj["id"]`, `obj["type"]`, `obj["status"]` die Eckdaten.
- **`ctx`** — der Kontext des Aufrufs: `rule_type`, `object_type`, `mode`,
  `language`, `actor`, `channel`, `release_status`, `missing_features`.
- **`result`** — die erlaubten Antworten: `add_error`, `add_warning`,
  `set_value`, `allow` und `log`.
- **Verfügbare Namen** — die eingebauten Funktionen und die Module, die eine
  Regel importieren darf, sowie die Grenzen (Laufzeit, Speicher, Skriptgröße).

Die Sandbox ist streng: kein Dateizugriff, kein Netzwerk, keine unerlaubten
Imports. Ein Beispiel:

```python
def run(obj, ctx, result):
    attributes = obj["attributes"]
    net = attributes.get("net_price")
    if net is None:
        result.add_warning("net_price", "missing", "Nettopreis fehlt.")
        return
    if net <= 0:
        result.add_error("net_price", "range", "Nettopreis muss positiv sein.")
```

## Rule Lab: testen vor dem Speichern

Das **Rule Lab** führt eine Regel aus, ohne etwas zu speichern. Sie wählen, ob
gegen **ein reales Objekt**, gegen eine **Objektauswahl** oder gegen einen
selbst gebauten **Payload** (JSON mit `type`, `status`, `attributes`) getestet
wird. Das Ergebnis zeigt Fehler, Warnungen, berechnete Werte, das Verdict eines
Wächters und die Protokollzeilen — für konstruierte Payloads auch *Vorher* und
*Nachher* im Vergleich.

Der Dry-Run ist der schnellste Weg, eine Regel zu verstehen, die jemand anderes
geschrieben hat.

## Berechnete Merkmale

Ein berechnetes Merkmal wird in der Attributdefinition **als berechnet
markiert**; die **Berechnungsregel** ist die Regel, die den Wert schreibt.
Fehlt die Zuordnung, funktioniert die Berechnung beim Speichern trotzdem —
die Zuordnung wird nur für die automatische Neuberechnung vererbter Werte
gebraucht, denn darüber wird die Regel gefunden. Ein Merkmal, das **nicht** als
berechnet deklariert ist, darf per Regel nicht geschrieben werden: Meadow lehnt
das Speichern mit einem Hinweis ab, statt einen Wert stillschweigend zu
überschreiben.

## Weiterlesen

- [Objekt bearbeiten](/help?page=product-form)
- [Daten importieren](/help?page=import)
- [Daten exportieren](/help?page=export)

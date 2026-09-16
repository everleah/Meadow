# Demo-Daten

Für Vorführungen, Schulungen und Lasttests lässt sich eine Meadow-Instanz mit
einem kompletten, synthetischen Datenbestand füllen: Fahrzeug-Stammdaten,
Ersatzteile mit Varianten, drei Taxonomieachsen, Kanal­freigaben, Sammlungen,
gespeicherte Suchen und Regeln. Dieses Kapitel beschreibt, was der Bestand
enthält und wie Sie ihn einspielen und wieder entfernen.

Die Demo-Daten stammen **nicht** aus der Oberfläche, sondern aus dem
Generator `scripts/demo_data.py` im Projektverzeichnis. Sie sind synthetisch und
deterministisch: gleicher Seed erzeugt denselben Bestand, es werden keine echten
Produktdaten verwendet.

## Was der Bestand enthält

Der Datensatz bildet einen Ausschnitt eines Automobil-Zubehörkatalogs ab:

- **Fahrzeug-Stammdaten** — Marken, Baureihen und Generationen mit Komposition
  (`Marke → Baureihe → Generation`), dazu Passungen (`fits_vehicle`).
- **Ersatzteile** — Produkte mit Varianten, Maßen, Gewichten, Materialien,
  Preisen, GTINs und Lebenszyklus-Status (Entwurf bis archiviert).
- **Baugruppen und Teilefamilien** — eigene Taxonomie inklusive Untergruppen.
- **Klassenmerkmale** — Anbindung an einen Klassifikationsstandard (ETIM), damit
  Klassen, Merkmale und fehlende Pflichtangaben sichtbar werden.
- **Ausgabekanäle, Sammlungen, gespeicherte Suchen und Regeln** — inklusive
  Freigaben, damit Export und Regelwerk direkt ausprobiert werden können.
- **Ein paar bewusst unvollständige Objekte** — fehlendes Gewicht, Material oder
  GTIN, damit die Vollständigkeitsanzeige und die Filter dazu etwas zu zeigen
  haben.

Drei Profile stehen zur Verfügung: `tiny` (wenige hundert Objekte, für
Screenshots), `demo` (Standard, einige tausend Objekte) und `perf` (um ein
Vielfaches größer, für Lasttests).

## Einspielen

Im Projektverzeichnis, während die Instanz läuft:

```bash
uv run python scripts/demo_data.py --import
```

Der Lauf erzeugt zuerst die Dateien (`.tmp/demo-data/`) und spielt sie dann ein:
Datenmodell, Objekte und Beziehungen, Klassenmerkmale und zuletzt die Extras
(Freigaben, Sammlungen, Suchen, Regelaktivierung). Am Ende vergleicht er die
Zahlen mit der Instanz. Andere Profile und ein anderer Seed:

```bash
uv run python scripts/demo_data.py --profile tiny --seed 7 --import
```

Der Import ist wiederholbar: ein zweiter Lauf **aktualisiert** vorhandene Objekte
statt sie zu duplizieren. Fehlgeschlagene Einträge (etwa ein gleichzeitiger
Schreibzugriff) werden am Ende automatisch erneut eingespielt.

## Nachschauen in der Oberfläche

- **Dashboard** — Anzahl der Objekte je Typ, Vollständigkeit, Status-Verteilung.
- **Objektliste** — filtern, sortieren, Spalten wählen, Massenaktionen; die
  Taxonomie lässt sich links ein-/ausblenden.
- **Produktformular** — Merkmale, Vererbung, Beziehungen, Kanalfreigaben.
- **Sammlungen und gespeicherte Suchen** — die Demo-Daten bringen fertige
  Beispiele mit, etwa eine Sammlung, die über einen Filter gepflegt wird.
- **Kategorien** — die Dimensionen der Taxonomie und ihre Merkmale.
- **Export** — eine Beispiel-Sammlung als BMEcat-Datei ausgeben.

Ein Einstieg in die Arbeit mit diesen Seiten steht in
[Erste Schritte](/help?page=getting-started), Details in den jeweiligen Kapiteln.

## Entfernen

Der Generator kann seinen Bestand vollständig wieder entfernen:

```bash
uv run python scripts/demo_data.py --cleanup
```

Entfernt werden alle Objekte **dieser Quelle** (samt ihrer Teilbäume) sowie die
vom Generator angelegten Sammlungen und gespeicherten Suchen. Objekte,
Sammlungen und Suchen aus anderen Quellen — also alles, was Sie selbst in der
Oberfläche angelegt haben — bleiben unangetastet. Für einen sauberen Neuaufbau:

```bash
uv run python scripts/demo_data.py --cleanup --import
```

Die Abnahme des Bestands ist ebenfalls skriptgestützt und rein lesend:

```bash
uv run python scripts/validate_demo.py
```

Sie prüft unter anderem Objektzahlen, doppelte Schlüsselwerte, Kanalfreigaben,
Regeln, Sammlungen und Suchen, die Komposition und — sofern die
Standardressourcen vorliegen — die Gültigkeit eines BMEcat-Exports.

# Objektliste (Grid)

Die Objektliste unter **Produkte** ist die zentrale Arbeitsoberfläche: Sie
suchen, filtern, vergleichen und ändern dort viele Objekte gleichzeitig.

![Die Objektliste mit Strukturbaum, Filterleiste und Werkzeugleiste](/help-assets/product-grid.png)

## Objekte laden

Die Liste lädt bewusst nicht alle Objekte auf einmal. Wählen Sie links im Baum
einen Knoten aus, zeigt die Tabelle dessen Objekte. Ohne Auswahl steht dort ein
Hinweis, welche Aktion erwartet wird.

Die Tabelle zeigt **eine Seite** von 200 Objekten; unter der Liste steht, welche
Zeilen von wie vielen Sie sehen („1–200 von 2000 Objekten", „Seite 1 von 10")
und mit welchen Schaltflächen Sie blättern (erste, vorherige, nächste, letzte).
Die Zahlen kommen vom Server, nicht aus den geladenen Zeilen: Blättern holt
genau die nächste Seite, statt die Liste im Browser immer weiter wachsen zu
lassen. Die Seitenzahl steht in der Adresszeile (`?page=2`), ein Lesezeichen
öffnet also dieselbe Seite.

**Sortieren** — ein Klick auf eine Spaltenüberschrift sortiert die **ganze**
Sicht über den Server (aufsteigend, absteigend, beim dritten Klick wieder in der
Standardreihenfolge „neueste zuerst"). Das gilt für ID-Felder und Attribute
gleichermaßen; Spalten ohne gespeicherten Wert (etwa die Vollständigkeit) lassen
sich nicht sortieren — sonst würde nur die sichtbare Seite umsortiert und beim
Blättern etwas anderes bedeuten. Die Sortierung steht ebenfalls in der
Adresszeile und geht beim Blättern nicht verloren.

Beim Blättern und Sortieren bleibt die **Spaltenauswahl** unverändert: welche
Attribute eine Spalte bekommen, entscheidet die ganze Sicht, nicht die gerade
sichtbare Seite.

Der Baum hat drei Sichten (Symbolleiste über dem Baum):

| Sicht | Zeigt |
| --- | --- |
| **Struktur** | Die Zusammengehörigkeit der Objekte (Produkt → Baugruppe → Bauteil). |
| **Kategorien** | Die Kategorien bzw. Klassifikationshierarchie. |
| **Collections** | Gespeicherte Zusammenstellungen von Objekten. |

Bei **Kategorien** wählen Sie zusätzlich:

- **Dimension** — welche Zuordnungsart gezählt und gefiltert wird. Die Auswahl
  ändert nur, welche Zuordnungen der Baum berücksichtigt.
- **Taxonomie** — welche Knotenart der Baum zeigt (z. B. eigene Kategorien oder
  eine importierte Klassifikation). Ist eine Dimension fest an eine Taxonomie
  gebunden (etwa *Baugruppen-Zuordnung*), stellt die Anwendung den Baum selbst
  ein und das Feld ist gesperrt — dann passt beides immer zusammen.

## Neue Objekte anlegen

Das Symbol **+** über dem Baum legt ein neues Objekt an (in der Sicht
*Struktur*). Im Dialog wählen Sie den **Objekttyp** (standardmäßig *Produkt*),
den **Namen** — mehrsprachig über das Sprachsymbol — und die
**Geschäftsschlüssel** des Typs (z. B. `sku` beim Produkt). Ein
Geschäftsschlüssel muss je Typ eindeutig sein und zählt als Pflichtfeld; der
Kern lehnt ein Duplikat mit einer Meldung ab.

Die Zeile **Ablage** zeigt, wo das Objekt entsteht: unter dem im Baum
ausgewählten Knoten, ohne Auswahl am Universal-Root. Nach dem Anlegen öffnet
sich das [Objektformular](/help?page=product-form) für die weiteren Angaben.

## Suchen und filtern

- **Filtern** — Freitextsuche über die geladenen Objekte.
- **Erweiterte Suche** — Bedingungen, Volltext, Referenzen, Sortierung und
  Limits; die Suche arbeitet serverseitig.
- **Nur unvollständige** — zeigt Objekte, denen Pflichtangaben fehlen.
- **Kanal-Filter** — zeigt Objekte, die für einen Ausgabekanal freigegeben sind.
- **Alle einklappen** — klappt den Baum zu; das Aufklappen erfolgt knotenweise
  und lädt die Unterknoten nach Bedarf.

Viele Filter stehen in der Adresszeile des Browsers. Eine solche Adresse können
Sie als Lesezeichen ablegen oder weitergeben — sie öffnet dieselbe Sicht wieder
(z. B. gefiltert nach Typ, Kategorie, Knoten im Baum oder Freigabekanal).

## Spalten und Ansichten

- **Spalten** — wählen Sie aus, welche Attribute die Tabelle zeigt.
  Mehrsprachige Attribute lassen sich je Datensprache als eigene Spalte
  einblenden; die Spalte der Arbeitssprache bleibt die, in der Sie schreiben.
- **Ansichten** — speichern Sie die aktuelle Kombination aus Filtern und
  Spalten dauerhaft. Ansichten gehören Ihnen persönlich und überleben das
  Abmelden.
- Spaltenbreiten passen Sie über die Symbole in der Filterleiste an die
  verfügbare Breite oder an den Inhalt an.

## Werte direkt ändern

Zellen mit einfachen Werttypen sind direkt bearbeitbar. Änderungen werden
zunächst nur **vorgemerkt** — erst **Speichern** schreibt sie, **Verwerfen**
setzt sie zurück. So lassen sich mehrere Objekte in einem Zug korrigieren.

Nicht direkt editierbar sind berechnete Werte sowie Bereiche und Referenzdaten
der Klassifikationsstandards; diese pflegen Sie im
[Objektformular](/help?page=product-form).

## Vererbte Werte erkennen

Ein ausgegrauter Wert mit einem Herkunftssymbol ist **geerbt** — er wird am
übergeordneten Objekt gepflegt. Ein durchgestrichenes Kettensymbol bedeutet:
Hier wurde die Vererbung bewusst unterbrochen, indem der Wert leer gesetzt
wurde. Beides wird im Formular aufgelöst.

## Mehrere Objekte bearbeiten

- **Massenaktionen** — wendet eine Operation auf die Auswahl an: Attribute
  setzen oder leeren, Status ändern, Kategorien oder Relationen zuweisen,
  Freigaben setzen oder zurückziehen, Text ersetzen, Vererbung konsolidieren.
  Die Operationen sind nach Bereichen geordnet; jede Aktion läuft als Job und
  meldet anschließend, wie viele Objekte geändert, übersprungen oder
  fehlgeschlagen sind.
- **Suchen & Ersetzen** — ersetzt einen Textbaustein in den Textattributen der
  Auswahl.
- **Editieren** (Symbol mit Notizstift) — öffnet die ausgewählten Objekte
  nacheinander im Formular, in der Reihenfolge der Liste, mit **Speichern &
  weiter**.
- **Exportieren** — übergibt die Auswahl oder alle Treffer an den
  [Datenexport](/help?page=export).

## Weiterlesen

- [Objekt bearbeiten](/help?page=product-form)
- [Daten exportieren](/help?page=export)

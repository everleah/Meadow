# Einheiten & Maßeinheiten

- **Hauptdokument:** Abschnitt 4 ([Datenmodell](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 1–3 (Datenmodell & Speicherung), Phase 6 (MEF), Phase 7 (Frontend)
- **Stand:** Einheiten-Katalog (Seed) und REST-API (`GET /units`, `GET /units/convert`) implementiert; die Normalisierung quantifizierter MEF-Werte in die kanonische SI-Einheit läuft im Import-Worker (Phase 6).

Technische Produktwerte (Höhe, Breite, Spannung, Leistung, …) bestehen in der Regel aus einem **numerischen Wert und einer Einheit** (z. B. `12,5 mm`, `230 V`, `750 W`). Dieses Dokument legt fest, wie Meadow PIM solche **quantifizierten Werte** modelliert, speichert, austauscht und darstellt. Die beteiligten Tabellen stehen im Detaildokument [datenmodell.md](datenmodell.md), das API-Verhalten in [api-design.md](api-design.md).

## Grundprinzip & Konvention

Ein quantifizierter Wert besteht aus einer Zahl und einer Einheit. Für Speicherung, Austausch und Darstellung gelten – analog zur Datumswerte-Konvention – drei strikt getrennte Regeln:

1. **Speicherung (Datenbank):** Der Wert wird **immer in der kanonischen SI-Einheit der jeweiligen Dimension** gespeichert, als reine JSON-Zahl. Die Einheit ergibt sich aus der Attributdefinition (`unit_id`); pro Attribut gilt genau eine Dimension.
   - Beispiel: `"width": 0.0125` (Meter) statt `12,5 mm`; `"voltage": 230` (Volt); `"power": 750` (Watt).
2. **Austausch (API / MEF / Events):** Quantifizierte Werte werden **immer mit Einheit** als Objekt übertragen: `{"value": 12.5, "unit": "mm"}`. Beim Empfang rechnet der Kern in die kanonische SI-Einheit um; beim Senden wird aus der kanonischen Einheit in die angeforderte bzw. Standard-Einheit umgerechnet. Fehlt `unit`, gilt die kanonische SI-Einheit der Dimension.
3. **Darstellung (Frontend):** Die GUI zeigt den Wert in der **gewählten Einheit** inkl. Symbol an (z. B. `12,5 mm`); die Ausgangseinheit stammt aus der Attributdefinition (Standard-Anzeigeeinheit) oder der Benutzerauswahl. Eingaben werden vor dem Speichern in die kanonische SI-Einheit umgerechnet.

Der Rust-Kern ist die **einzige Instanz**, die Einheiten verwaltet und Umrechnungen durchführt. Frontend und Worker nutzen den Einheiten-Katalog und die Umrechnung über die REST-API.

## Dimensionen & kanonische SI-Einheiten

Jede Einheit gehört zu genau einer **Dimension** (physikalische Größe). Jede Dimension besitzt genau eine **kanonische SI-Einheit**, die als Speichereinheit dient.

| Dimension            | Kanonische SI-Einheit | Weitere Darstellungen (Auswahl)                     |
| -------------------- | --------------------- | --------------------------------------------------- |
| Länge                | `m`                   | `mm`, `cm`, `km`, `µm`, `in`, `ft`, `yd`, `mi`      |
| Fläche               | `m²`                  | `cm²`, `mm²`, `sq in`, `sq ft`                      |
| Volumen              | `m³`                  | `l`, `ml`, `cm³`, `gal (US)`, `gal (imp)`, `fl oz`  |
| Masse                | `kg`                  | `g`, `mg`, `t`, `lb`, `oz`                          |
| Zeit                 | `s`                   | `ms`, `min`, `h`                                    |
| Elektrische Spannung | `V`                   | `mV`, `kV`, `MV`                                    |
| Elektrischer Strom   | `A`                   | `mA`, `kA`                                          |
| Leistung             | `W`                   | `mW`, `kW`, `MW`, `hp`                              |
| Energie              | `J`                   | `kJ`, `MJ`, `Wh`, `kWh`                             |
| Frequenz             | `Hz`                  | `kHz`, `MHz`, `GHz`                                 |
| Kraft                | `N`                   | `kN`, `MN`                                          |
| Druck                | `Pa`                  | `kPa`, `MPa`, `bar`, `mbar`, `psi`                  |
| Temperatur           | `K`                   | `°C`, `°F`                                          |

Die vollständige Liste (alle SI-Basis- und abgeleiteten Einheiten sowie die üblichen Darstellungen inkl. Präfixen) wird **per Default mit dem System ausgeliefert** und liegt in der Tabelle `unit`.

**Abgrenzung:** Währungen (z. B. `EUR`, `USD`) sind **kein** Teil des Einheiten-Systems; Preis- und Währungsformatierung bleiben getrennt (über `babel`, siehe [frontend.md](frontend.md)).

## Anglo-amerikanische Einheiten (imperial / US customary)

Einheiten aus dem anglo-amerikanischen Raum (US customary / imperial) werden **genauso behandelt wie alle übrigen Einheiten**: Sie sind Einträge im Einheiten-Katalog (`unit`) mit `factor`/`offset` relativ zur kanonischen SI-Einheit und werden per Default mitgeliefert. **Gespeichert wird immer in SI**; imperial/US-Einheiten dienen nur als Eingabe- und Anzeigedarstellung. Eine Sonderlogik ist nicht erforderlich.

| Dimension  | Einheit                | Symbol      | Umrechnung (→ kanonische SI-Einheit) |
| ---------- | ---------------------- | ----------- | ------------------------------------ |
| Länge      | Zoll (inch)            | `in`        | `1 in = 25.4 mm = 0.0254 m`          |
| Länge      | Fuß (foot)             | `ft`        | `1 ft = 0.3048 m`                    |
| Länge      | Yard                   | `yd`        | `1 yd = 0.9144 m`                    |
| Länge      | Meile (mile)           | `mi`        | `1 mi = 1609.344 m`                  |
| Masse      | Pfund (pound)          | `lb`        | `1 lb = 0.45359237 kg`               |
| Masse      | Unze (ounce)           | `oz`        | `1 oz = 0.028349523125 kg`           |
| Fläche     | Quadratzoll            | `sq in`     | `1 sq in = 0.00064516 m²`            |
| Fläche     | Quadratfuß             | `sq ft`     | `1 sq ft = 0.09290304 m²`            |
| Volumen    | Kubikzoll              | `cu in`     | `1 cu in = 16.387064 ml`             |
| Volumen    | Kubikfuß               | `cu ft`     | `1 cu ft = 28.316846592 l`           |
| Volumen    | Gallone (US)           | `gal_us`    | `1 gal_us = 3.785411784 l`           |
| Volumen    | Gallone (imp.)         | `gal_imp`   | `1 gal_imp = 4.54609 l`              |
| Volumen    | Fluid Ounce (US)       | `fl_oz_us`  | `1 fl_oz_us = 29.5735295625 ml`      |
| Volumen    | Fluid Ounce (imp.)     | `fl_oz_imp` | `1 fl_oz_imp = 28.4130625 ml`        |
| Druck      | Pounds per square inch | `psi`       | `1 psi = 6894.757 Pa`                |
| Leistung   | Horsepower (mech.)     | `hp`        | `1 hp = 745.6999 W`                  |
| Temperatur | Grad Fahrenheit        | `°F`        | `K = (°F + 459.67) · 5/9` (Offset!)  |

**Hinweise:**

- **US vs. imperial:** Einige Bezeichnungen sind in beiden Systemen gleich, die Werte unterscheiden sich aber (Gallone, Fluid Ounce). Sie werden deshalb als **getrennte Einheiten mit eindeutigem Symbol** geführt (`gal_us`/`gal_imp`, `fl_oz_us`/`fl_oz_imp`); das `label` macht den Unterschied sichtbar.
- **Temperatur `°F`** ist eine **affine** Umrechnung (Offset), keine reine Skalierung – siehe Abschnitt [Einheiten-Katalog](#einheiten-katalog-tabelle-unit).
- **Horsepower:** `hp` bezeichnet die mechanische Horsepower (745,6999 W). Die metrische Pferdestärke (`PS` ≈ 735,5 W) ist eine separate Einheit und wird bei Bedarf ebenfalls mitgeliefert.
- **Zusammengesetzte Dimensionen:** Verhältnisgrößen wie `mph` (`mi/h`) bleiben im MVP ausgeklammert (gemischte Dimensionen, siehe [Technische Umsetzung](#technische-umsetzung)); `psi` wird pragmatisch als Einheit der Dimension `pressure` geführt.

## Einheiten-Katalog (Tabelle `unit`)

Jede Einheit wird als Datensatz in der Tabelle `unit` geführt:

```json
{
    "id": "unit_mm",
    "symbol": "mm",
    "dimension": "length",
    "canonical_unit_id": "unit_m",
    "factor": 0.001,
    "offset": 0.0,
    "label": { "de": "Millimeter", "en": "millimetre" },
    "system": "default",
    "readonly": true,
    "created_at": "2026-08-16T08:00:00Z",
    "updated_at": "2026-08-16T08:00:00Z"
}
```

- `symbol` ist das anzuzeigende Einheitenzeichen (z. B. `mm`, `V`, `°C`).
- `dimension` ordnet die Einheit einer Dimension zu (z. B. `length`, `voltage`, `temperature`).
- `canonical_unit_id` verweist auf die kanonische SI-Einheit der Dimension; bei der kanonischen Einheit selbst ist das Feld `null`.
- `factor` und `offset` definieren die **lineare Umrechnung** in die kanonische Einheit:
  - `canonical_value = value * factor + offset`
  - rückwärts: `value = (canonical_value - offset) / factor`
- `system`: `default` (mitgelieferte Einheiten) oder `custom` (benutzerdefiniert).
- `readonly: true` kennzeichnet mitgelieferte Einheiten; sie können nicht geändert oder gelöscht werden.

**Beispiel Temperatur (affine Umrechnung):**

- `°C` → `K`: `factor = 1`, `offset = 273.15`
- `°F` → `K`: `factor = 5/9 ≈ 0.555556`, `offset ≈ 255.372`

## Benutzerdefinierte Einheiten

Auf Basis einer SI-Einheit können Anwender **eigene Einheiten** definieren. Dazu genügen **Zeichen (Symbol) und Umrechnung**:

```json
{
    "id": "unit_pallet",
    "symbol": "PAL",
    "dimension": "length",
    "canonical_unit_id": "unit_m",
    "factor": 1.2,
    "offset": 0.0,
    "label": { "de": "Palettenbreite", "en": "pallet width" },
    "system": "custom",
    "readonly": false,
    "created_at": "2026-08-16T08:00:00Z",
    "updated_at": "2026-08-16T08:00:00Z"
}
```

- Eine benutzerdefinierte Einheit bezieht sich immer auf genau eine kanonische SI-Einheit (Dimension) und definiert `factor`/`offset` für die Umrechnung.
- Das Symbol muss innerhalb der Dimension eindeutig sein.
- Benutzerdefinierte Einheiten können geändert und gelöscht werden; mitgelieferte (`system: "default"`) sind geschützt (`readonly: true`).

## Attributdefinitionen mit Einheit

Ein numerisches Attribut, das eine Einheit trägt, referenziert in der Attributdefinition die **kanonische Einheit** seiner Dimension und optional eine **Standard-Anzeigeeinheit**:

```json
{
    "id": "attr_def_width",
    "name": "width",
    "label": { "de": "Breite", "en": "Width" },
    "type": "number",
    "unit_id": "unit_m",
    "display_unit_id": "unit_mm",
    "inheritable": true,
    "constraints": {
        "min": 0,
        "max": 10
    }
}
```

- `unit_id` legt die Dimension und damit die Speichereinheit fest (kanonische SI-Einheit).
- `display_unit_id` (optional) legt die Standard-Anzeigeeinheit für GUI und Export fest. Fehlt sie, wird die kanonische Einheit angezeigt.
- Werte dieses Attributs werden in der Tabelle `object` als reine Zahl in der kanonischen Einheit gespeichert (siehe Konvention oben).

## API, Frontend & MEF

- **API:** Der Einheiten-Katalog wird über eigene Endpunkte verwaltet (`/units`); eine Umrechnung steht über einen dedizierten Endpunkt zur Verfügung. Beim Lesen von Objekten kann die gewünschte Einheit übergeben werden; beim Schreiben wird `{"value": …, "unit": …}` akzeptiert und vor der Speicherung normalisiert. Details: [api-design.md](api-design.md).
- **Frontend:** Eingabefelder für quantifizierte Werte zeigen Wert + Einheit mit Einheiten-Auswahl; die Anzeige erfolgt in der Standard-Anzeigeeinheit bzw. der Benutzerauswahl. Details: [frontend.md](frontend.md).
- **MEF:** Im Import/Export werden quantifizierte Werte als `{"value": …, "unit": …}` übertragen und vom Worker vor der Übergabe an den Kern normalisiert. Details: [integration-eventsystem.md](integration-eventsystem.md).

## Technische Umsetzung

- Die Umrechnung ist eine **linear-affine Funktion** (`value * factor + offset`) und wird als reine Funktion im Rust-Kern implementiert. Ein externes Crate ist nicht zwingend erforderlich; optional kann eine Bibliothek wie `uom` für typsichere SI-Größen im Kern verwendet werden.
- Umrechnungen sind nur **innerhalb derselben Dimension** erlaubt; gemischte/abgeleitete Dimensionen (z. B. `m/s`, `kg·m²`) sind für den MVP ausgeklammert.

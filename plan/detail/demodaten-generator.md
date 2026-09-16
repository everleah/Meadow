# Demo- und Lastdaten: Automotive-Katalogdaten

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration-und-eventsystem))
- **Relevante Phasen:** Phase 8, 9 (Werkzeug und Datenbestand für Vorführung, Handbuch und Lasttests)
- **Stand:** Stufen 1+2 **umgesetzt** (Katalog, Generator, Tests, Modell-Dry-Run fehlerfrei; MCF-`role`-Lücke geschlossen 2026-09-15); offen: Importer (Stufe 3), Extras-Einspielung, `perf`/Turbo, Doku-Verweise

## 1. Zweck

Die Entwicklungs-Instanz enthält derzeit nur **Testrückstände** (E2E-Artefakte: 36 Produkte, 9 Kategorien, 5640 ETIM-Klassen, keine Collections, 1 Kanal). Für Demo, Handbuchbilder (`scripts/docs_screenshots.py`), Abnahme und Performance-Arbeit fehlt ein **zusammenhängender, glaubwürdiger Datenbestand**.

Ziel dieses Dokuments ist ein **reproduzierbarer Generator** für synthetische Automotive-Katalogdaten, der

1. einen **demonstrationsfähigen** Katalog erzeugt (Fahrzeug-Stammdaten, Teile, Varianten, Zubehör, Ersatzteilketten, Kompatibilitäten, Kanäle, Workflow, Regeln),
2. **möglichst alle Meadow-Strukturen** benutzt (Komposition/Vererbung, Aggregation + Dimensionsrollen, drei Taxonomie-Achsen, Klassen-Merkmale, Einheiten, Enum-Sets, Sprachbaum, Kanäle/Freigaben, Business Rules, Collections, gespeicherte Suchen, Export-Profile),
3. in **skalierbaren Profilen** läuft (`tiny` für Handbuchbilder, `demo` für Vorführung, `perf` für Lasttests),
4. **rechtlich sauber** ist (keine kommerziellen Datenbestände) und **deterministisch** (Seed → identische Daten),
5. **ohne Kern-Änderung** über die vorhandenen Wege einspielbar ist: **MCF** (Modell) + **MEF** (Daten) über `POST /config/import` und `POST /imports`.

**Nicht-Ziel:** echte Katalogdaten (TecDoc/TecAlliance, VW OKAPI) nachbilden, ein Datenmodell für den Kfz-Teilehandel produktiv zu machen, Bilder/Assets (es gibt **keinen** Upload-Endpunkt), oder der Aufbau eines Fremdsystem-Connectors.

## 2. Recherche: Datenquellen und Lizenzlage (verifiziert)

| Quelle | Inhalt | Lizenz/Nutzbarkeit | Entscheidung |
| --- | --- | --- | --- |
| **VW OKAPI** | OEM-Teiledaten | Genehmigungs- und Lizenzlage unklar | **Nicht verwenden** |
| **TecDoc / TecAlliance** | Kfz-Teile-Klassifikation, OE-Nummern | kommerziell | **Nicht verwenden** |
| **`auto-parts-db`** (npm, `lifeofcapo/car-list`) | 142 Marken, 2379 Modelle mit Generationen (ab 1968), 1530 Teile | Paket **MIT** (npm-Registry verifiziert); Herkunft der Daten nicht dokumentiert | **Nicht verwendet** (Entscheidung 2026-09-15: eigene Stammdatenliste) |
| **NHTSA vPIC** | US-Marken/Modelle (public domain) | frei, aber US-lastig | nicht nötig (nur als Notiz) |
| **Wikidata** | Marken/Modelle (CC0) | frei | optionale Ergänzung |
| **ETIM 10.0 IXF** (bereits lokal) | 5640 Klassen, 17 377 Merkmale, 18 Feature-Gruppen | ETIM-Lizenz, `resources/` gitignored | **Klassifikations-Rückgrat** für den elektrotechnischen Sortimentsteil |

**Zentrale Erkenntnis (aus dem lokalen IXF verifiziert):** **ETIM ist keine Kfz-Teile-Taxonomie.** Die 159 ETIM-Gruppen enthalten genau zwei fahrzeugnahe Gruppen:

- `EG020024` *Vehicles and parts* — 19 Klassen, überwiegend Transporträder/Anhänger (z. B. `EC004738` Trailer hitch, `EC011956` Automotive special tool, `EC004140` Liquid for vehicles)
- `EG020047` *Electric vehicle charging equipment* — 7 Klassen (`EC002883` Charging device for electric vehicles mit 69 Merkmalen, `EC002897` Charging cable, `EC002898` Charging connector, `EC004824` Mounting post)
- ergänzend `EG000053` *Batteries, chargers and power cords* (`EC011878` Battery (lead) for vehicles, `EC003893` Battery (lithium) for vehicles, `EC011960` Battery charger)

Konsequenz: **zwei Taxonomien parallel** — ETIM für die klassifizierten Elektro-/Ladeartikel (mit echten Klassen-Merkmalen → Vollständigkeit + BMEcat-Export), **plus eine eigene Teile-Taxonomie** (`part_group`) für Bremsen/Motor/Fahrwerk usw.

## 3. Datenmodell des Demo-Katalogs

**Szenario:** mittelständischer Kfz-Teile-Großhändler mit eigener Marke, Werkstattkunden, Webshop + Printkatalog + Marktplatz + BMEcat-Lieferung. Fahrzeugstammdaten sind **eigene Objekte** (kein Fremdsystem), Teile verweisen per Relation darauf.

### 3.1 Objekttypen

| Typ | Taxonomie | Key | Label | Rolle im Demo |
| --- | --- | --- | --- | --- |
| `product` (System) | – | `sku` | `name` | Teil/Artikel (~2 000) |
| `variant` (System) | – | `sku` | `name` | Varianten je Produkt (~2 600) |
| `category` (System) | ✔ | – (`name`) | `name` | Webshop-Navigation (~180 Knoten) |
| `part_group` (neu) | ✔ | `code` | `name` | eigene Teile-/Baugruppen-Taxonomie (~45 Knoten) |
| `etim_class` (vorhanden) | ✔ | `code` | `description` | ETIM-Klassen des Standardpakets |
| `vehicle_make` (neu) | – | `code` | `name` | 24 Marken |
| `vehicle_model` (neu) | – | `code` | `name` | ~180 Baureihen |
| `vehicle_generation` (neu) | – | `code` | `name` | ~520 Generationen (Kompositionstiefe 4 inkl. Root) |
| `oem_number` (neu) | – | `number` | `number` | ~1 200 OEM-/Wettbewerbsnummern |
| `collection` (System) | – | – | `name` | 4 materialisierte Sammlungen |
| `saved_search` (System) | – | – | `name` | 5 gespeicherte Suchen |

Die Fahrzeugkette (`vehicle_make` → `vehicle_model` → `vehicle_generation`, je `depends_on`) ist der **Vererbungs-Showcase**: `body_style`, `fuel_type`, `drive_type` liegen inheritable an der Baureihe, `engine_code`, `power_kw`, `displacement_ccm`, `production_from/to` an der Generation; einzelne Generationen überschreiben geerbte Werte bewusst (Konflikt-/Überschreibungsanzeige im Formular).

### 3.2 Taxonomien und Dimensionen (drei Achsen)

| Dimension (Relationstyp, `role: category`) | Ziel | Zweck |
| --- | --- | --- |
| `rel_category_member` (System, primär) | jedes Taxonomie-Objekt | Webshop-Navigation **und** ETIM-Zuordnung (Bestandskonvention) |
| `rel_part_group_member` (neu, `role: category`, `target_types: ["part_group"]`) | `part_group` | technische Baugruppen-Gliederung (Grid-Filter, Zähler) |
| `rel_etim_class_member` (neu, `role: category`, `target_types: ["etim_class"]`) | `etim_class` | trennt „Shopkategorie" von „ETIM-Klasse" (rein fachliche Zuordnung) |

> **Geschlossen (2026-09-15, im Kern):** Der **MCF-Import/Export überträgt `role`** jetzt (`crates/pim-core/src/api/config.rs`: `relation_definitions_section` schreibt `role`, `relation_definition_content()` validiert Rolle und leitet `kind` daraus ab, `apply_relation_definitions` prüft zusätzlich `target_types` gegen registrierte Taxonomie-Typen; Details [konfiguration-export-import.md](konfiguration-export-import.md) §4.7). Vorher **degradierte ein MCF-Roundtrip jede Zusatz-Dimension still zu `role: link`** — Dimension-Umschalter, `?definition_id=`-Filter und Zähler fielen aus. Damit reisen beide Dimensionen in `model.mcf.json` mit (kein separater API-Schritt mehr); Voraussetzung ist, dass der Zieltyp als Taxonomie registriert ist — deshalb deklariert das Demo-Modell `etim_class` selbst (identisch zum Standardpaket), sonst bräuchte der Import das ETIM-Paket vorher.

### 3.3 Attribute (eigene Definitionen, ~45)

| Bereich | Beispiele | benutzte Meadow-Features |
| --- | --- | --- |
| Identifikation | `sku` (key), `gtin`, `manufacturer` (Enum-Set), `product_line` (Enum-Set), `country_of_origin`, `customs_tariff_number` | `is_key`, Enum-Sets, `constraints` |
| Text/Mehrsprachig | `name`, `description_short`, `description_long`, `marketing_text`, `installation_note` | `multilingual` |
| Technik | `weight_kg`, `length_mm`, `width_mm`, `height_mm`, `volume_l`, `operating_voltage_v`, `power_w`, `temperature_range`, `tightening_torque_nm` | `unit_id`/`display_unit_id`, Datentyp `range` |
| Katalog/Logistik | `net_price`, `currency`, `list_price` (berechnet), `gross_price` (berechnet), `packaging_unit`, `min_order_qty`, `warranty_months` (berechnet), `hazardous`, `disposal_note`, `datasheet_url` | `computed` + `computed_rule` + `dependencies`, `url` |
| Verweise | `primary_vehicle_model` (Datentyp `reference`), `supersedes_note` | `reference`-Datentyp |
| ETIM-Bindung | eigene Attribute wie `nominal_current_a`, `charging_power_kw` mit `standard_codes: {"etim": "EF…"}` | Standardbindung statt Übernahme der `EF…`-Attribute; `reference_data` bleibt für die eingespielten ETIM-Attribute |
| Fahrzeug-Stammdaten | `body_style`, `fuel_type`, `drive_type` (inheritable), `engine_code`, `power_kw`, `displacement_ccm`, `production_from`, `production_to`, `platform_code` | `inheritable`, `date`, Enum-Sets |

**Enum-Sets (neu):** `manufacturer`, `product_line`, `quality_tier` (OE/Erstausrüster/Premium/Standard), `material`, `colour`, `part_position` (vorne/hinten/links/rechts/Vorderachse/Hinterachse), `fuel_type`, `body_style`, `drive_type`, `packaging_type`. Alle Optionen mit **de/en-Labels** (nutzt die Mehrsprachigkeit der Enum-Optionen).

**Schreibregel für Werte:** numerische Werte werden als **reine Zahl in der Anzeigeeinheit** geschrieben (z. B. `weight_kg: 1.35`, `length_mm: 87`). Grund: Der Worker wandelt quantifizierte `{value, unit}`-Werte beim Import **in die SI-Kanoneinheit** um (`meadow/worker/imports.py::_convert_units`), was in Grid/Handbuch künstlich wirkt (`0.087` statt `87`).

### 3.4 Relationstypen (benutzerdefiniert, mit Kanten-Attributen)

| Name | Rolle | von → nach | Typ-Restriktion | Kanten-Attribute |
| --- | --- | --- | --- | --- |
| `fits_vehicle` | link | `product`/`variant` → `vehicle_generation` | – | `position` (Enum), `qualifier` (text, „ab Bj. 2015"), `confidence` (number) |
| `replaces` | link | `product` → `product` | – | `reason` (Enum), `valid_from` (date) |
| `alternative_to` | link | `product` → `product` | – | `match_type` (Enum: oe/interchange), `quality_tier` (Enum) |
| `accessory_for` | link | `product` → `product` | – | `mandatory` (boolean) |
| `oem_reference` | link | `product` → `oem_number` | – | `kind` (Enum: oe/interchange/competitor) |
| `part_of_kit` | link | `product` → `product` | – | `quantity` (number) |

Die Kanten-Attribute werden beim Import gegen die `attributes`-Vorlage der Definition geprüft (`validate_edge_attributes`) — der Generator muss sie also **nach** dem MCF-Import schreiben.

### 3.5 Klassen-Merkmale (Klassifikation → Vollständigkeit)

- **ETIM:** für ~700 Produkte eine ETIM-Klasse zuordnen (EV-Laden, Batterien, Anhänger-Zubehör, Werkstatt-Elektrik) → 8–69 Pflicht-/Optional-Merkmale je Klasse, Werte teilweise gesetzt, **bewusst Lücken** (Dashboard `incomplete_by_type`, Filter `?missing_required=true`, Grid-Badges).
- **Eigene Taxonomie:** `category_attribute`-Zeilen an `part_group`-Knoten (z. B. Bremsbelag: `width_mm` (Pflicht), `height_mm` (Pflicht), `thickness_mm` (Pflicht), `wear_indicator` (optional), `ean` (Pflicht)) → Vollständigkeit auf **eigener** fachlicher Achse, unabhängig vom ETIM-Standard.
- **Standardbindung:** 2–3 eigene Attribute über `standard_codes` an ETIM-Merkmale gebunden (z. B. eigenes `charging_power_kw` → `EF…` der Klasse `EC002883`) → Export überträgt den Wert, ohne das `EF`-Attribut zu belegen.

### 3.6 Business Rules (5, im Demo aktiv)

| Name | Typ | `applies_to` | Zweck |
| --- | --- | --- | --- |
| `calc_gross_price` | computed | product, variant | `gross_price = net_price * (1 + 19/100)` |
| `calc_list_price` | computed | product | `list_price = net_price * 1.4` |
| `default_warranty` | computed | product, variant | 24 Monate bei `quality_tier = oe`, sonst 12 |
| `validate_gtin` | validation | product, variant | GTIN-13: Länge + Prüfziffer |
| `guard_publish_etim` | workflow_guard | product | keine Freigabe ohne vollständige Klassen-Merkmale (`ctx.missing_features`) |

`normalization`-Regel (1) zeigt den Import-Pfad: `applies_to: product`, setzt fehlende `product_line` aus `manufacturer` und trimmt `sku`.

### 3.7 Workflow, Kanäle, Sammlungen, Suchen, Exporte

- **Status-Verteilung** (realistisch gemischt): ~78 % `published`, ~12 % `draft`, ~5 % `in_review`, ~3 % `approved`, ~2 % `archived`.
- **Kanäle:** `webshop` (vorhanden), `print-2026`, `marketplace-de`, `bmecat-partner` + Freigaben je Objekt×Kanal (nicht überall identisch: Print ohne Neuheiten, Marktplatz nur `quality_tier != oe`, Partnerkanal nur ETIM-klassifiziert).
- **Collections:** „Bremsen-Sortiment", „Neuheiten Q3", „Werkstattbedarf", „Schnelldreher" (+ je eine `collection_refresh`-Jobdefinition mit Cron als Scheduler-Demo).
- **Gespeicherte Suchen:** „Bremsscheiben ohne Gewicht", „Artikel ohne ETIM-Klasse", „Preis > 100 € und Qualität OE", „Freigegeben für Print, aber unvollständig", „Ersatzteilkette ohne Nachfolger".
- **Export-Profile:** ein BMEcat-Profil (`transaction: new_catalog`, Preise, `price_type: net_customer`) + ein CSV-/XLSX-Profil → der Katalog ist exportierbar **und** die BMEcat-Ausgabe ist gegen `resources/etim/bmecat_etim_501.xsd` validierbar (Beweis, dass die Klassen-Merkmale echt sind).

### 3.8 Katalog-Stammdaten (mitgeliefert, gepflegt)

Kompakte, eigene Datei `scripts/demo_data_catalog.json` (Fakten: Marken-, Modell-, Generationen-Namen und Jahre; ~40 kB):

- 24 Marken, ~180 Baureihen, ~520 Generationen (Jahre, Plattformcode)
- ~45 Baugruppen (`part_group`) in 2–3 Ebenen
- ~80 Teile-Familien (Bremsbelag, Bremsscheibe, Öl-/Luft-/Innenraum-/Kraftstofffilter, Zündkerze, Glühkerze, Stoßdämpfer, Feder, Traggelenk, Spurstangenkopf, Radlager, Zahnriemen, Wasserpumpe, Thermostat, Wischerblatt, Glühlampe, Batterie, Anlasser, Lichtmaschine, Lambdasonde, AGR-Ventil, Kupplungssatz, Auspuff, Katalysator, Bremsflüssigkeit, Kühlmittel, Motoröl, …) mit Attribut- und OE-Nummern-Mustern je Marke (z. B. VW `1K0 698 151 B`, BMW `34 11 6 787 123`)

Die Datei ist **eigene Stammdatenliste** (keine Lizenzfrage); `auto-parts-db` kann sie optional erweitern (Abschnitt 2), ohne in Build/Gates zu landen.

## 4. Ausgabeformate und Einspielweg

### 4.1 Erzeugte Dateien

| Datei | Inhalt | Zielweg |
| --- | --- | --- |
| `model.mcf.json` | `channels`, `attribute_groups`, `enum_sets`, `object_types` (inkl. `part_group`, `etim_class`), `attribute_definitions`, `relation_definitions` (8, inkl. der zwei `role: category`-Dimensionen), `business_rules`, `export_profiles` | `POST /config/import?mode=merge` (Admin), erst `?dry_run=true` |
| `class-features.mcf.json` | nur `category_attributes` (Klassen-Merkmale der `part_group`-Knoten) — **nach** dem Datenimport, weil die Sektion Klassen über ihren Business-Key adressiert | `POST /config/import?sections=category_attributes` |
| `masters.mef.json` | **nur Objekte**, die in mehr als einem Chunk vorkommen (Taxonomien, Baugruppen, Fahrzeugkette, OEM-Nummern) | `POST /imports?emit_events=false&source=meadow-demo`, **vor** den Chunks und abgewartet |
| `data-<nn>.mef.json` | Objekte + Referenzen, in `--chunks` geschlossene Teildokumente | `POST /imports?emit_events=false&source=meadow-demo` je Chunk, parallel |
| `extras.json` | Kanalfreigaben, Collections + Mitglieder, gespeicherte Suchen, Jobdefinitionen (Cron), ETIM-Klassen-Zuordnung (per Business-Key), Regel-Aktivierung | `PUT /objects/{id}/releases`, `POST /collections…`, `POST /saved-searches`, `POST /jobs`, `POST /rules/{id}/activate` |
| `report.json` | erzeugte Zahlen je Typ/Relation, Optional-Lücken, Katalogzahlen | Nachweis (wie `install_standard_bundle.py`), geprüft von `tests/test_demo_data.py` |
| *(optional)* `data.surql` | SurrealDB-Import-Datei | **nur** Turbo-Pfad für Lasttests (Abschnitt 5) |

### 4.2 Einspielreihenfolge

1. `POST /config/import?mode=merge` mit `model.mcf.json` (erst **dry_run** prüfen)
2. `masters.mef.json` über `POST /imports` und **abwarten** (geteilte Objekte entstehen genau einmal)
3. Daten-Chunks parallel über `POST /imports` (die Chunks sind geschlossen; Referenzen laufen mit dem Chunk, der ihren Startknoten besitzt)
4. `POST /config/import?sections=category_attributes` mit `class-features.mcf.json` (jetzt existieren die Klassenknoten)
5. `extras.json` (Freigaben, Collections, Suchen, Jobs, ETIM-Klassen, Regeln aktivieren)
6. Verifikation (`report.json` gegen die Ist-Zahlen aus `GET /objects/stats`, `GET /classification-standards?counts=true`, `GET /config/export`)

**Idempotenz:** Jedes Objekt trägt ein stabiles `id` und zusätzlich die Quell-Referenz (`source: meadow-demo` plus `source_id`); der Worker gleicht Business-Key → Record-ID → Quell-Referenz ab und meldet je Objekt `created`/`updated`. Ein zweiter Lauf ist für **Objekte** gefahrlos.

**Referenzkanten:** `POST /references` legt dieselbe Kante (Quelle, Ziel, Definition) **nicht** erneut an, sondern antwortet mit **`409`** (seit 2026-09-16, Prüfung + Write in einer kritischen Sektion pro Kante). Der Import-Worker meldet das als `existing`, ein `--import`-Lauf ist dadurch für Objekte **und** Kanten idempotent. Sauberer Neuaufbau bleibt **`--cleanup --import`** (das Löschen der Objekte entfernt ihre Kanten mit).

**Chunk-Regel (aus dem Worker-Code):** Referenzen werden nur **innerhalb desselben Dokuments** aufgelöst (`id_map`), ein Chunk muss also beide Endpunkte jeder seiner Referenzen enthalten. Objekte dürfen dafür in **mehreren** Chunks vorkommen — sie werden dann aktualisiert statt dupliziert. Damit das auch **parallel** gilt, spielt `masters.mef.json` die geteilten Objekte vorher einmal ein (Abschnitt 4.4, Punkt 4).

### 4.3 Gemessener Durchsatz des Import-Pfads (2026-09-15, lokal)

| Lauf | Objekte | Referenzen | Dauer | Rate |
| --- | --- | --- | --- | --- |
| ETIM-Paket (Bestand, 14.09.) | 5 640 (create) | – | 384 s | **14,7 obj/s** |
| ETIM-Paket (Bestand, 14.09.) | 5 640 (update) | – | 743 s | **7,6 obj/s** |
| Probe 1 Chunk | 120 (update) | 360 | 21,8 s | 5,5 obj/s · 16,5 ref/s |
| Probe **4 Chunks parallel** | 120 (create) | 360 | **12,9 s** (Wanduhr) | 9,3 obj/s · 27,8 ref/s |

Der Worker zieht `JOBS_CONCURRENCY` (Default **4**) Jobs gleichzeitig; parallele Import-Chunks skalieren also messbar (~2× Wanduhr gegenüber einem Dokument) und prüfen zugleich die Job-Queue.

**Konsequenz (Rechengröße für `demo`, ~7 000 Objekte / ~23 000 Referenzen):** sequenziell ≈ 8 min + 25 min ≈ **35 min**; mit 4 Chunks ≈ **15–20 min**. `perf` (50 000 Objekte / 200 000 Referenzen) wäre sequenziell ≈ **3–4 h** → braucht den Turbo-Pfad.

**Vollständiger `demo`-Lauf (2026-09-15, gemessen, Seed 42):**

| Phase | Umfang | Dauer | Rate |
| --- | --- | --- | --- |
| Cleanup (`--cleanup`, sequenziell) | 3 105 Top-Level-Deletes → 5 950 Objekte inkl. Teilbäume | **~27 min** | 3,7 Objekte/s |
| Cleanup (`--cleanup`, gebündelt 100/Request) | 3 104 Top-Level-Ids → 5 950 Objekte | **0,9 min** | 110 Objekte/s (Start) |
| Master-Dokument | 220 Objekte (97 KiB) | 12 s | 18 obj/s |
| 4 Daten-Chunks parallel | 6 546 Objekte + 18 370 Referenzen | **~8 min** (Wanduhr) | 13 obj/s · 38 ref/s |
| Retry-Pass | 3 Objekte (Schreibkonflikte) | < 5 s | – |
| Klassen-Merkmale / Extras | `category_attributes`, 2 573 Releases + Collections + Suchen + Regeln | ~6 min | – |

Der Cleanup dominierte also den Neuaufbau, nicht der Import: 4 Threads halfen dort **nicht** (18 min statt 19 min in der Messung, der Kern serialisiert die Löschungen) — der Hebel war ein **gebündelter Löschpfad im Kern**, und der ist seit 2026-09-16 umgesetzt (`POST /objects/batch-delete`, §4.4 Punkt 8): Der komplette `--cleanup` eines `demo`-Bestands braucht jetzt **0,9 min** statt ~20–27 min.

**Nebenläufigkeit skaliert nicht mit (gemessen 2026-09-16, `perf`-Profil):** Der `perf`-Import (70 363 Objekte / 243 426 Referenzen, 8 Chunks à ~8 800 Objekte, 4 Chunks parallel) wurde nach drei Stunden abgebrochen: Chunk 1 allein brauchte **60 min** für 7 768 Objekte (2,2 obj/s), und unter 4-facher Nebenläufigkeit kostet ein `POST /objects` **413 ms statt 107 ms** (Faktor 3,9; ein einzelner Mess-Client neben dem Import, gleicher Bestand). Ursache ist die Serialisierung im Kern bzw. in SurrealDB: mehrere Chunks schreiben in dieselben Tabellen, die Transaktionen warten aufeinander, und mit steigender Objektzahl wächst auch die Arbeit pro Write. Hochrechnung in dieser Form: **15–20 h**. Beim `demo`-Profil fiel das nicht auf (4 Chunks, 11,7k Objekte, ~8 min), weil dort die absolute Schreiblast klein bleibt. **Konsequenz für den Turbo-Pfad:** eine höhere `JOBS_CONCURRENCY` ist der **falsche** Hebel; richtig sind sequenzielle Chunks (oder wenige) mit einem schnellen Kern (Release-Build) und für reine Lastdaten der direkte SurrealDB-Import (§5). Der `perf`-Bestand liegt für den Read-Lasttest in einer **eigenen Datenbank** (`meadow_perf`) und wurde bei 16 166 Objekten eingefroren.

**Momentaufnahme der API-Latenzen bei Demo-Größe (2026-09-15, 11 678 Objekte, ~30k Kanten, 24k Tombstones, Median aus 5 Läufen):**

| Aufruf | Median |
| --- | --- |
| `GET /health` | 1 ms |
| `GET /objects/{id}` | 36 ms |
| `POST /objects` (anlegen) | 171 ms |
| `PATCH /objects/{id}` | 357 ms |
| `DELETE /objects/{id}` (Blatt) | 575 ms |
| `POST /objects` (aktuell) | 171 ms → **107 ms** |
| `PATCH /objects/{id}` (aktuell) | 357 ms → **100 ms** |
| `POST /auth/login` (argon2) | 389 ms |
| **`GET /objects/stats`** | **1 691 ms** |

Die Einzelstatements der Löschung wurden direkt gegen SurrealDB gemessen und liegen bei **80–400 µs** (`UPDATE object …`, `DELETE reference WHERE in IN $recs OR out IN $recs`, die beiden `FOR`-Schleifen) — die ~0,6 s entstehen also **nicht** in den Lösch-Statements, sondern im Schreibpfad drumherum (Transaktion/Commit, Audit, Outbox und die zusätzlichen Abfragen je Write). Wichtigster neuer Befund für die Performance-Arbeit: **`GET /objects/stats` kostet 1,7 s** und ist damit der langsamste Pfad überhaupt (das Dashboard und die Abnahmevalidierung rufen ihn auf); Phase 8 hatte ihn für 57 Objekte auf 45–64 ms gebracht, bei 11 678 Objekten skaliert die Aggregation also schlecht. Beides — Write-Pfad und `stats` — gehört als Messgröße in die nächste Performance-Runde (Abschnitt 9).

**`GET /objects/stats`: Aufschlüsselung (2026-09-16, instrumentiert, Debug-Build, warme Caches):**

| Phase | Debug | Bemerkung |
| --- | --- | --- |
| Zählungen (`GROUP BY type, status`) | 56 ms | 33 Zeilen; vorher lud der Handler **alle** Objekte mit allen Attributen nur zum Zählen |
| Kandidatentypen (`incomplete_candidates`) | 100 ms | 9 Typen (Pflicht-Attribute + deklarierte Schlüssel + kategorisierte Typen) |
| Objekt-Fetch der Kandidaten | 950 ms | 11 598 Zeilen ≈ 4,9 MB JSON |
| `compute_batch` (Kanten + Klassen-Merkmale + Schleife) | 850 ms | Parent-Map 11 658 Kanten, Kategorie-Kanten, `category_attribute` der beteiligten Klassen |

Release statt Debug bringt Faktor ~2 (**1 028 ms** statt 2 004 ms; die Dev-Instanz läuft im Debug-Build). Zwei Umbauten sind bereits drin und bleiben: die Zählungen kommen aus einer SQL-Aggregation (33 statt 11 678 Zeilen) und Attribute werden nur für Typen geladen, die überhaupt unvollständig sein können. **Die Kandidatenmenge ist heute aber praktisch der ganze Bestand** — `product`/`variant` (Pflichtattribute + Schlüssel) und `etim_class` (deklarierter Schlüssel `code`) decken fast alles ab —, deshalb brachte der Filter kaum Transferersparnis. Der Hebel ist damit nicht weiteres Filtern, sondern **Caching mit expliziter Invalidierung** (oder materialisierte Vollständigkeit): die Antwort hängt nur an Objekten, Attributdefinitionen, Objekttypen, `category_attribute` und der Sprachkette, also an einer überschaubaren Menge von Schreibpfaden. Ergänzende Beobachtung: `category_attribute.required = true` umfasst im Demo-Bestand **76 648** Zeilen (das ETIM-Paket markiert alle Merkmale als Pflicht), Klassen-Merkmale lassen sich also nicht wegkürzen.

**Caching (umgesetzt 2026-09-16):** `MetaCache` hält die Antwort pro Arbeitssprache und bindet sie an eine **monotone Datenversion** (`StatsCache`-Felder in `cache.rs`, `invalidate_stats()`); jede Antwort trägt die Version, mit der sie berechnet wurde, und wird nur ausgeliefert, wenn sie noch zur aktuellen passt — ein veralteter Wert geht also nie raus, sondern löst eine Neuberechnung aus. Invalidiert wird an den Schreibpfaden, die die Aggregation verändern können: Objekte (anlegen/ändern/löschen/Batch/Computed), Referenzen (anlegen/ändern/löschen/Umhängen), Relationsdefinitionen (Rollen), Objekttypen, Attributdefinitionen, Klassen-Merkmale (`category_attribute`) und der Sprachbaum — die letzten vier bereits über die bestehenden Cache-Invalidierungen. **Gemessen:** warme Aufrufe **1–2 ms** statt ~2 000 ms (Debug-Build); ein `validate_demo.py`-Lauf gegen den kalten Cache bleibt unverändert (11 678 Objekte, identische Zahlen). **Live geprüft** (`.tmp/stats_cache_check.py`): für jeden Schreibtyp (Objekt anlegen/ändern, Referenz anlegen/löschen, Umhängen, Objekttyp, Attributdefinition) folgt auf den Write eine Neuberechnung, und die Zahlen folgen dem Write (Total +1 nach dem Anlegen, zurück nach dem Löschen).

### 4.4 Befunde aus den Live-Importen (2026-09-15/16)

Der Demo-Import hat eine Reihe **echter Lücken** aufgedeckt — im Import-Pfad, im Worker und im Kern. Alle sind behoben; die Nummern sind chronologisch (Punkt 1–3 aus dem ersten Lauf am 2026-09-15, Punkt 5–9 aus dem Live-Betrieb der Demo-Daten):

1. **MEF konnte die Kompositionsstruktur nicht abbilden.** Ein neues Objekt hängt beim Anlegen am Universal-Root, und die Ein-Eltern-Invariante erlaubt genau eine `depends_on`-Kante — ein `depends_on`-Eintrag im MEF-Dokument wurde deshalb mit **`400`** abgelehnt (342 fehlgeschlagene Referenzen im ersten Lauf, Fahrzeugkette und Varianten blieben am Root). **Fix:** der Import-Worker wendet `depends_on` jetzt als **Umhängen** an (`PATCH /objects/{id}/parent`, Status `reparented`); damit ist ein MEF-Dokument strukturtreu. Tests: `tests/test_imports.py` (8).
2. **`PATCH /objects/{id}/parent` akzeptierte nur JWT** (`AuthUser`) — der Worker (Service-Konto, `X-Api-Key`) bekam **`401`**. Damit war auch die Bulk-Operation **`change_parent`** seit ihrer Einführung kaputt. **Fix:** `api_key_or_role(..., ROLE_EDITOR)` plus `?emit_events=false` (wie bei `POST /references`), damit Bulk-/Importlaeufe den Outbox-Pfad ruhig halten.

Weitere Beobachtungen: Der Worker holt Jobs **batchweise** (`fetch(JOBS_CONCURRENCY)`) — kommen später eintreffende Chunks erst danach dazu, startet die erste Welle einzeln (Demo: 1 Chunk laeuft, 3 warten). Für große Bestände heißt das: **nicht** mehr Chunks einplanen, sondern Chunks nacheinander einspielen — die Parallelität kostet mehr, als sie bringt (§4.3). Collections werden **aktualisiert statt dupliziert** (Upsert über `PATCH /saved-searches/{id}` bzw. `PATCH /collections/{id}`), damit ein zweiter Lauf die Filter der Artefakte durchsetzt.
5. **`--cleanup` über Schlüssel übersieht Duplikate.** Aufgeräumt wurde über die *Datensatz-Keys* (Key → eine Record-ID); eine von Punkt 4 hinterlassene **Doppelzeile** trägt denselben Business-Key und wurde deshalb nie gefunden — sie überlebte jedes Cleanup und ließ danach **jeden** `PATCH` auf diesen Key mit `409` scheitern (im Demo-Lauf: `demo-model-bmw-3er`, der Rest der 3 105 Objekte war fehlerfrei). **Fix:** das Cleanup listet jetzt **alle** lebenden Objekte dieser Quelle (`GET /objects?type=…&limit=…&start=…`, gefiltert auf `source`) und löscht sie; nur Top-Level-Typen, weil die Kaskade Varianten, Modelle/Generationen und Untergruppen mitnimmt, und ein `404` heißt „Kaskade war schneller", nicht „Fehler". Gelöscht wird **sequenziell**: `DELETE /objects/{id}` kostet ~0,4 s (Blatt) bis ~1–3 s (Teilbaum), und vier parallele Clients machten den Lauf nicht schneller (18 min statt 19 min) — der Kern serialisiert die Schreibvorgänge; der Hebel ist ein gebündelter Löschpfad im Kern (Abschnitt 9).

6. **Extras waren nicht auffindbar.** `collection`/`saved_search` kannten kein `source`/`source_id`, also konnte `--cleanup` sie nicht finden: eine Collection einer älteren Katalog-Revision („Bremsen-Sortiment" + ihre Filter-Suche) blieb liegen, obwohl der Importer sie nicht mehr erzeugt. **Fix (2026-09-16):** `POST`/`PATCH /saved-searches` und `POST`/`PATCH /collections` nehmen `source`/`source_id` (Top-Level-Feld wie bei Objekten; beim `PATCH` gilt: fehlt = unverändert, leerer String = löschen, sonst ersetzen) und `GET /saved-searches` / `GET /collections` filtern mit `?source=`. Der Generator schreibt `source: meadow-demo` + `source_id: <Katalogname>` und `cleanup_extras()` löscht über genau diesen Filter (Collections **vor** ihren Suchen, weil der Kern eine noch referenzierte Suche mit `409` schützt); f"ur das historische Paar war einmalig ein manueller Delete nötig, weil es vor der Kennzeichnung entstand (Abschnitt 7).

8. **Löschen war der Engpass (gebündelter Pfad, 2026-09-16).** `DELETE /objects/{id}` kostet ~0,6 s, weil der **Write-Pfad je Request** zählt (Transaktion, Audit, Outbox, HTTP), nicht das Löschen selbst (die Statements laufen in 80–400 µs). Ein `demo`-Cleanup brauchte deshalb ~20–27 min, und vier parallele Clients halfen nicht (der Kern serialisiert). **Fix:** `POST /objects/batch-delete` (`{ids: […]}` bis 500 Ids) löscht alle Objekte in **einer** Transaktion: eine Kaskaden-Berechnung für alle Wurzeln (die Kompositionskanten werden **einmal** gelesen, `references::composition_children` + `subtree_keys_in`, statt einer Traversierung je Wurzel), ein Audit-Eintrag, eine Outbox-Zeile **je gelöschter Wurzel** (Event-Konsumenten sehen exakt dieselben Nachrichten wie vorher); unbekannte Ids landen in `missing` statt einen Fehler zu werfen, weil eine Kaskade sie zwischenzeitlich mitgenommen haben kann. **Gemessen (Dev-Instanz, Debug-Build, Blätter):** 591 ms/Objekt einzeln, 22 ms bei 30/Request, **12 ms bei 100/Request**, 12 ms bei 200/Request — `demo_data.py` nutzt `CLEANUP_BATCH = 100`. **Live:** der komplette `--cleanup` (3 104 Top-Level-Objekte, 2 846 per Kaskade) läuft in **0,9 min** statt ~20–27 min (≈25×), inklusive Extras-Cleanup.

9. **Ein einziges fehlgeschlagenes Objekt kostete seine Referenzen.** Im Chunk `PV8SB6` schlug **ein** Objekt an einem Schreibkonflikt fehl; die 17 `category_member`-Kanten auf dieses Objekt meldeten danach „cannot resolve reference", weil der Worker Referenzen nur über die `id_map` **desselben Dokuments** auflöst. Der Retry-Pass konnte sie nicht heilen, weil ein Referenz-Report auf der einen Seite eine **Record-Id** (aufgelöst) und auf der anderen die **MEF-Id** führt, `build_retry_document` aber nur MEF-Ids matchte — die Kanten wären still verloren gegangen (der Report meldete sie als `failed`, der Retry-Lauf enthielt sie aber nicht). **Fix (2026-09-16):** (a) der Worker löst einen fehlenden Endpunkt **einmalig** über seine Quell-Referenz auf (`GET /objects/by-source?source=…&source_id=<mef-id>`, Ergebnis wird in der `id_map` gemerkt, also nur ein Lookup je unbekannter Id) — das Objekt existiert ja, egal welcher Chunk es angelegt hat oder ob sein eigenes Create gerade verloren hat; (b) `build_retry_document(runs, dataset, record_ids)` bildet Record-Ids über die aufgelösten Knoten-Ids auf Dataset-Keys zurück und matcht **beide** Seiten; `run_import` löst die Ids deshalb **vor** dem Retry-Pass auf und reicht dieselbe Map danach an die Extras ✓ (Tests: `tests/test_imports.py`, `tests/test_demo_data.py`).
7. **Die Refresh-Jobs liefen nie.** Der Katalog deklarierte die `refresh_interval`-Werte **fünffeldig** (`0 3 * * *`), `tokio-cron-scheduler` braucht aber **sechs** Felder (Sekunden zuerst). Der Kern nahm den Wert an, die Definition landete in der Datenbank, und der Scheduler meldete jede Minute `invalid cron schedule \`30 4 * * *\`: ParseSchedule` — die fünf Demo-Collections wurden also nie automatisch aufgefrischt (aufgefallen ist es nur, weil das Log beim Debuggen gelesen wurde). **Fix (2026-09-16):** (a) der Katalog nutzt die korrekte Form (`0 0 3 * * *`, `0 30 4 * * *`, `0 0 5 * * *`, `0 0 6 * * *`), `validate_catalog` prüft die Feldanzahl (der Fehler fällt jetzt bei `--check` und im pytest-Gate auf); (b) der Kern validiert jedes gespeicherte Schema mit **dem Parser des Schedulers** (`scheduler::validate_schedule`) — `POST /jobs`, `PATCH /jobs/{id}` und `refresh_interval` einer Collection antworten mit **`400`** statt eine Definition anzulegen, die nie feuert; (c) `sync()` bricht nicht mehr beim ersten unbrauchbaren Schema ab, sondern überspringt den Eintrag mit Warnung — vorher blieb damit **jeder** andere Cronjob eines Ticks unregistriert.
3. **Kanten-Attribute muessen zur Definition passen.** `alternative_to` schrieb `quality_tier`, die Definition deklarierte nur `match_type` → der Kern lehnt unbekannte Kanten-Attribute mit **`400`** ab (1 520 fehlgeschlagene Kanten im ersten Demo-Lauf). Ursache der spaeten Entdeckung: das Test-Fixture lief auf `tiny` (40 Produkte), wo `alternative_to` gar nicht vorkommt — das Fixture nutzt jetzt das `demo`-Profil und prueft zusaetzlich, dass **jeder** deklarierte Relationstyp auch tatsaechlich erzeugt wird.
4. **Geteilte Objekte kollidieren beim Anlegen.** Die geschlossenen Chunks wiederholen Objekte, die in mehreren Chunks gebraucht werden (Taxonomien, Fahrzeugkette). Laufen diese Chunks **parallel**, prüfen zwei von ihnen gleichzeitig ihre Business-Key-/Quell-Auflösung, finden nichts und legen beide an: Der Kern prüft die Eindeutigkeit **nicht atomar** (`key_value_exists` → `INSERT`), also gewinnt entweder ein Insert und der andere Lauf bekommt **`409`** (Objekt „failed“, obwohl es existiert) — oder es entstehen **zwei lebende Zeilen** (im Demo-Lauf: Fiat-Marke, BMW-3er, eine OEM-Nummer). Der Retry-Pass kann das nicht heilen, weil sein `PATCH` dann selbst mit `409` (Doppel-Key) scheitert.
   **Fix (drei Teile):** (a) `masters.mef.json` legt alle Objekte, die in mehreren Chunks vorkommen, **vor** den Chunks an; die Chunks finden sie danach per Business-Key/Quell-Referenz und aktualisieren nur. (b) Der Chunker schneidet nicht mehr per Round-Robin über die Knotenliste, sondern hält jede **Montagegruppe** (`Node.family_group`: Teilefamilie + Varianten + deren OEM-Nummern) in **einem** Chunk und verteilt die Gruppen gleichmäßig auf die Chunks; dadurch sind nur noch die 220 wirklich geteilten Taxonomie-Knoten im Master-Dokument (vorher ~2 600, weil jedes OEM-Paar und jede Variante quer lag). (c) **Kern (2026-09-15):** Prüfung und Schreiben sind jetzt **eine kritische Sektion pro Schlüsselwert** — `create_object`/`update_object` holen die `key_attributes` des Typs, sperren die Schlüsselwerte (`crate::key_lock`, Shard-Mutexe, keine globale Serialisierung) und führen erst dann `normalize_key_attributes` **und** den Write aus. Verifiziert mit 8 parallelen `POST /objects` auf `vehicle_make.make_code` und `product.sku`: je **1 × `201`, 7 × `409`, genau eine lebende Zeile** (vorher bis zu 8 Zeilen möglich). Zusätzlich bleibt der **Retry-Pass** für die Schreibkonflikte übrig, die zwei parallele `PATCH`-Aufrufe auf denselben Datensatz weiterhin auslösen können (SurrealDB bricht die spätere Transaktion ab; der Kern meldet das seit 2026-09-16 als **`409`** „concurrent write conflict, please retry" statt als `500`, live provoziert und nachgemessen — Abschnitt 9).

## 5. Profile und Turbo-Pfad

| Profil | Objekte | Referenzen | Zweck | Weg |
| --- | --- | --- | --- | --- |
| `tiny` | 429 | 536 | Handbuchbilder, Smoke-Demo, Erstprüfung des Generators | 1 Chunk, < 1 min |
| `demo` (Default) | **5 950** | **18 370** | Vorführung, Abnahme, Vollständigkeits-/Grid-/Export-Prüfung | Master-Dokument + 4 Chunks ≈ 6,2 MB, ~10 min Einspielung (parallel) |
| `perf` | **70 363** | **243 426** | Lasttest (`scripts/load_test.py`), Grid-/Stats-/Such-Optimierung | 8 Chunks à ~8 800 Objekte (72 MB) — **nicht** parallel einspielen, s. Nebenläufigkeits-Befund in §4.3 |

(Die Ist-Zahlen stammen aus `report.json` für Seed 42; `tiny`/`demo` sind gemessen, `perf` ist gemessen erzeugt.)

**Generierungslauf (2026-09-15, lokal):** `demo` in wenigen Sekunden; seit der Montagegruppen-Chunkung **`masters.mef.json` 97 KiB (220 Objekte) + 4 Chunks à 1,4–1,7 MB** (vorher 4 × 2,9 MB, weil die geteilten Objekte in jedem Chunk steckten), 18 370 Referenzen nach Kanten-Deduplizierung (`dedupe_references` — ohne sie entstehen bei kleinen Familien doppelte `alternative_to`-Kanten).

**Turbo-Optionen (für `perf`, nach dem Befund vom 2026-09-16 neu geordnet):**

1. **Wenige oder gar keine parallelen Chunks** — die Messung zeigt, dass mehr `JOBS_CONCURRENCY` die Wanduhrzeit **verschlechtert** (4 parallele Chunks: `POST /objects` 413 ms statt 107 ms, §4.3). Erste Wahl: **sequenziell** einspielen (oder 2 Chunks) mit einem **Release-Kern** (doppelte Rechenleistung auf der Kern-Seite).
2. **Batch-Upsert im Kern** (neuer Endpunkt/Job) — der eigentliche Hebel für große Bestände (analog zu `POST /objects/batch-delete`, das den Cleanup um ~25× beschleunigt hat); echte Produktänderung, als Phase-9/10-Idee vermerkt.
3. **Direkter SurrealDB-Import** (`surreal import` einer erzeugten `.surql`-Datei) — Minuten statt Stunden, **umgeht aber API, Validierung, Audit und Outbox-Events**. Nur als klar gekennzeichneter Lastdaten-Bestand, nie für Demo-/Abnahme-Daten („Keine Fallbacks"-Prinzip bleibt gewahrt, weil es kein Kompatibilitätspfad, sondern ein bewusst anderer Datenweg ist).
4. **Höhere `JOBS_CONCURRENCY`** — widerlegt, nicht mehr verwenden (Befund §4.3).

## 6. Werkzeug-Schnitt

- **`scripts/demo_data.py`** (Generator + CLI; Tests laden es per `spec_from_file_location`):
  `--profile tiny|demo|perf`, `--seed 42`, `--chunks N` (0 = Profilwert), `--out-dir .tmp/demo-data`, `--catalog`, `--check` (nur Katalog prüfen), **`--import`** (Modell → geteilte Objekte → Daten-Chunks → Klassen-Merkmale → Extras einspielen und gegen `GET /objects/stats` prüfen), **`--cleanup`** (Bestand dieses Datensatzes soft-deleten: listet die Objekte der Quelle per Typ und löscht sie **gebündelt** (100 Ids je `POST /objects/batch-delete`) — erst die Top-Level-Typen, danach die Typen, die eine Kaskade normalerweise mitnimmt; danach `cleanup_extras()` über `?source=meadow-demo`, also die generierten Collections und gespeicherten Suchen — fremde Artefakte derselben Instanz bleiben unberührt), `--base-url`, `--user`/`--password`, `--timeout` (Wartezeit auf die Jobs). `--cleanup --import` ergibt einen sauberen Neuaufbau.
- **`scripts/demo_data_catalog.json`** (Abschnitt 3.8) — Daten getrennt von Logik, damit Marken/Teile ohne Codeänderung wachsen. Seit 2026-09-15 deklariert sie auch die Objekttypen `product`/`variant` mit `key_attributes: ["sku"]`: der Kern erzwingt Eindeutigkeit über die **Typ-Registry**, nicht über `is_key` an der Attributdefinition (Abschnitt 9).
- **`scripts/validate_demo.py`** (Abnahme, rein lesend außer dem Export-Job): vergleicht `report.json` mit `GET /objects/stats`, sucht doppelte Business-Keys (paging über `GET /objects`, gruppiert nach den `key_attributes` der Typen), zählt die Kanalfreigaben (vollständig gepaged, nicht auf 1 000 gedeckelt), prüft Regeln, Collections + Mitglieder, die generierten gespeicherten Suchen, dass alle generierten Collections/Suchen über `?source=` auffindbar sind (sonst würde das Cleanup sie nie löschen), den Kompositionspfad eines Fahrzeugs und die Klassen-Merkmale, exportiert eine Collection als BMEcat und validiert sie gegen die ETIM-XSD (skip ohne `resources/`, `--skip-export`). Exit-Code 1 bei einem Fehlschlag — als Gate nach `--cleanup --import` nutzbar; `--report`/`--extras` zeigen auf die erzeugten Artefakte, ohne `extras.json` bleiben die Collection-/Suchprüfungen weg.
- **Konventionen:** Konsolen-UTF-8 (`configure_console()`, Windows-cp1252-Falle), feste `created_at` für Byte-Determinismus, stabile Quell-IDs (`demo-<typ>-<key>`), Ausgaben unter `.tmp/` (gitignored).
- **Abhängigkeiten:** nur Standardbibliothek im Generator (`random`, `json`, `argparse`); `httpx` erst für den Importer. **Kein** Netzwerkzugriff, kein npm.

## 7. Verifikation

**Umgesetzt (`tests/test_demo_data.py` — 27 Tests und `tests/test_imports.py` — 8 Tests, Teil des pytest-Gates):** Determinismus (gleicher Seed ⇒ identische Objekte/Referenzen, anderer Seed ⇒ anders), Katalog-Konsistenz (inkl. Negativfall mit unbekannten Querverweisen), Profil-Skalierung, eindeutige Objekt- und Business-Keys, Referenz-Endpunkte innerhalb des Datensatzes, **geschlossene Chunks** (jede Referenz mit beiden Endpunkten im selben Dokument) inklusive Vollständigkeit der Abdeckung, deklarierte Relationstypen und Kanten-Attribute, Attributwerte gegen das Modell (Enum-Werte, `range` = `{from,to}`, Zahlen/Bools/Text, mehrsprachige Karten `de`/`en`), GTIN-Prüfziffern, Statuswerte, Modell-Abdeckung aller erzeugten Typen, `role: category`-Dimensionen im Modell, Klassen-Merkmale, **ETIM-Standardbindung** (die vier `standard_codes` existieren und gehören zu einer benutzten Klasse; skip ohne `resources/`), Collection-Filter (die Knoten-Keys in `references[].out` existieren), Report-Konsistenz, Extras (nur veröffentlichte Produkte freigegeben, Suchen/Klassen-Zuordnungen zeigen auf erzeugte Objekte), Dateiausgabe und beide CLI-Pfade (`--check`, Schreiben). Dazu die Worker-Seite: `depends_on` ⇒ Umhängen, Aggregationen ⇒ `POST /references`, Fehlerisolierung je Referenz, `emit_events`-Weitergabe.

**Live verifiziert (2026-09-15, `tiny`, dann `demo`):** zweiter Lauf `created 0, updated 429, failed 0` (Idempotenz über die Quell-Referenz), `references 535 (failed 0)` nach dem Umhängen-Fix, Klassen-Merkmale angewendet, Collections mit Mitgliedern aus ihren Filtern, Regeln aktiviert, `role: category` der beiden Dimensionen per API bestätigt.

**Live-E2E `demo` (2026-09-15, `--cleanup --import`, Seed 42) — abgeschlossen:**

- **Import ohne Restfehler:** Cleanup `3 044 gelöscht, 60 per Kaskade, 1 Orphan, 0 Fehler`; Master-Dokument `220 (created 220, failed 0)`; 4 Chunks `created 5 950, updated 813, failed 3` / `40` fehlgeschlagene Referenzen — der **Retry-Pass heilt alle 3** (`created 0, updated 3, failed 0`) ⇒ **keine offenen Probleme** (Exit-Code 0). Zuvor waren es 2–9 Objekte bzw. 40–499 Referenzen, die der Retry nicht mehr heilen konnte (Abschnitt 4.4).
- **Keine Dubletten:** Prüfung über alle Business-Keys je Typ (SurrealDB `GROUP BY`) ⇒ **0 doppelte Business-Keys**.
- **Zahlen stimmen:** `GET /objects/stats` 11 678 Objekte; Report-Erwartung je Typ erfüllt (`product` 2 036 ≥ 2 000, `variant` 2 588 ≥ 2 587, `vehicle_generation` 166, `vehicle_model` 93, `vehicle_make` 22, `part_group` 42, `oem_number` 1 000); Status-Mix `published 4 879 / draft 6 305 / approved 149 / in_review 243 / archived 82 / active 20`; `incomplete_by_type` `product 2 036, variant 212`.
- **Kanäle, Suchen, Regeln, Collections:** Freigaben je Kanal abfragbar (`webshop`, `print-2026`, `marketplace-de`, `bmecat-partner`), 6 Regeln aktiv, 5 Demo-Collections mit Mitgliedern aus ihren Filtern (74/72/500/72/218) und die zugehörigen Filter-Suchen liefern dieselben Trefferzahlen.
- **Fahrzeugkette:** `/objects/{id}/ancestors` liefert den vollen Pfad `vehicle_make → vehicle_model → vehicle_generation` (der letzte Eintrag ist das Objekt selbst, so der dokumentierte Vertrag).
- **Klassen-Merkmale:** 72 Ladeprodukte gefunden, `category-attributes` liefert Klassen und fehlende Merkmale; der BMEcat-Export des Ladepakets meldet `classes 72`, `features 3 780`, `feature_systems ["ETIM-10.0"]`, `issues []`.
- **BMEcat-Export XSD-valide:** `POST /exports` (Collection „E-Mobilitaet Ladepaket", BMEcat 2005/ETIM 10.0) → Job `success`, 72 Produkte, 571 747 Bytes, Validierung gegen `resources/etim/bmecat_etim_501.xsd` ⇒ **`xsd valid: True`**.
- **Wiederholbar als Gate:** `scripts/validate_demo.py` führt dieselbe Prüfliste aus (rein lesend, Exit ≠ 0 bei Fehlschlag) — zuletzt **alle Prüfungen grün**: 11 678 Objekte, **8 Schlüsselattribute ohne Dubletten** (inkl. `product.sku`/`variant.sku`), Freigaben 1 534/512/307/220 (vollständig gepaged), 6 Regeln, 5 Collections mit Mitgliedern, 10 generierte Suchen mit Treffern, Kompositionspfad `vehicle_make → vehicle_model → vehicle_generation`, 3 ETIM-Klassen am Ladeprodukt, Export XSD-valide (`--skip-export` lässt den Export weg).
- **Nebenläufigkeit des Kerns:** 8 parallele `POST /objects` mit demselben Schlüsselwert ⇒ genau **1 × `201`, 7 × `409`, eine lebende Zeile** (geprüft für `vehicle_make.make_code` und `product.sku`).

**Vollständiger Neuaufbau nach allen Fixes (2026-09-16, `--cleanup` + `--import`, Seed 42, live):** Der Cleanup lief in **0,9 min** (3 104 Wurzeln + 2 846 Kaskaden-Objekte + 15 Extras), der Import danach **ohne einen einzigen Fehlschlag auf den Kanten**: Master-Dokument `220 (updated 220, failed 0)`, 4 Chunks `created 0, updated 6 764, failed 2` mit **`references failed: 0`** — die zuvor **18** fehlgeschlagenen Referenzen sind weg, seit der Worker fehlende Endpunkte über ihre Quell-Referenz auflöst (Abschnitt 4.4 Punkt 9) —, der Retry-Pass heilte die 2 Objekte (`updated 2, failed 0`). Wiederholte Kanten kamen als `existing` zurück (3 365/4 104/3 973/3 996), Suchen und Sammlungen wurden als Upsert aktualisiert (5 updated, 5 existing), Regel-Aktivierung und Freigaben wie erwartet, `GET /objects/stats` **11 759 Objekte** (= Report-Erwartung). Die Abnahmevalidierung ist anschließend **komplett grün** (Objektzahlen je Typ, 8 Schlüsselattribute ohne Dubletten, Freigaben 1 534/512/307/220, 6 Regeln, 5 Sammlungen mit Mitgliedern, 10 Suchen mit Treffern, 15 Artefakte mit Quelle, Kompositionspfad, Klassen-Merkmale, BMEcat-Export 72 Produkte / 571 747 Bytes, XSD-valide).

**Batched cleanup (2026-09-16, live gemessen):**`scripts/demo_data.py --cleanup` gegen den vollen `demo`-Bestand (11 690 Objekte): **3 104 Top-Level-Objekte + 2 846 Kaskaden-Objekte in 0,9 min**, danach `cleanup_extras()` (5 Collections + 10 Suchen) im selben Lauf — vorher ~20–27 min. Der Bündelungs-Benchmark (`.tmp/batch_delete_bench.py`, je 60 Blatt-Objekte, Debug-Build): 591 ms/Objekt einzeln · 22 ms bei 30/Request · **12 ms bei 100 und 200/Request**; das Skript prüft nach jedem Durchgang, dass kein Probeobjekt übrig bleibt. Der Kern nimmt maximal 500 Ids je Aufruf (`413` darüber), `demo_data.py` nutzt 100.

**Publish-Guard und Sandbox live geprüft (2026-09-16):** Mit den neuen Sandbox-Helfern läuft die Regel `guard_publish_features` erstmals überhaupt. Live nachgemessen (Produkt mit zugeordneter ETIM-Klasse, fehlende Merkmale): **Benutzer**-PATCH auf `status: published` ⇒ **`400 "transition denied: Klassen-Merkmale fehlen: EF000015, …"`**, derselbe Write mit dem Service-Account (`X-Api-Key`, `actor: system`) ⇒ **`200`** — der automatisierte Import bleibt also möglich, während die Regel den Workflow schützt. Die gespeicherte Regel wurde dafür einmalig auf den Katalogstand nachgezogen (PATCH `/rules/{id}`), weil sie vorher die alte Fassung ohne Import-Ausnahme war.

**Schreibpfad auf Demo-Größe nachgeschärft (2026-09-16):**`POST /objects` **203 → 107 ms**, `PATCH /objects/{id}` **372 → 100 ms** (Debug-Build, Client-Sicht). Vier Ursachen, alle mit dem neuen Phasen-Log gefunden (`RUST_LOG=info,pim_core::api::objects=debug`, dafuer ist das `env-filter`-Feature von `tracing-subscriber` aktiv): (1) `queue_inherited_cascade` lud bei **jedem** Attribut-PATCH die komplette `rel_depends_on`-Kantenmenge (`subtree_keys`, ~12 k Zeilen ≈ 1–2 MB JSON ≈ 300 ms), auch für Blätter — jetzt läuft `references::descendant_keys` Ebene für Ebene mit einer indexierten Abfrage je Ebene; (2) die Business-Key-Prüfung scannte die Objekte eines Typs (`attributes[$key]` + `type = …` → ~150 ms je Create) — jetzt definiert `keys::ensure_key_indexes` pro deklariertem Schlüsselattribut einen Index (`idx_object_key_<name>` auf `attributes.<name>`), und die Prüfung nutzt den **statischen** Pfad und filtert den Typ **in Rust** (mit `type = $type` im Prädikat nimmt der Planer `idx_object_type`: 615 ms statt 71 ms); (3) `active_rules`, `inherited_computed_rules` und `category_relation_keys` liegen jetzt im `MetaCache` (der Write-Pfad brauchte sie bei **jedem** Save, sie ändern sich nur in der Regel-/Relationsverwaltung); (4) zwei der sechs Demo-Regeln liefen nie, weil die Sandbox `enumerate`/`getattr` nicht anbot (`NameError` zur Laufzeit, nachdem `compile_rule` sie akzeptiert hatte) — jetzt sind `enumerate`/`reversed` und ein **geguardetes** `getattr` (`safer_getattr`) verfügbar, kompilierte Skripte werden gecacht, und die Publish-Guard-Regel lässt den Service-Account (`actor == "system"`) durch, damit automatisierte Importe veröffentlichte Objekte weiterhin speichern können. Da der Import je Objekt ein Create oder Update ausführt, wirkt sich das direkt auf seine Laufzeit aus — nachgemessen wird das mit dem `perf`-Profil (Abschnitt 5/8).

**Extras-Quelle und Cleanup (2026-09-16, live):**`PATCH` der 5 Collections/10 Suchen adoptierte die vorhandenen Artefakte (`source=meadow-demo`); `GET /collections?source=meadow-demo` liefert danach genau 5, `GET /saved-searches?source=…` genau 10 Zeilen, die funktionale Testsuche „Formular-Demo (ODER + zwischen)" bleibt ohne Quelle und damit unangetastet. Ein einmaliger manueller Delete entfernte das historische Paar „Bremsen-Sortiment" (entstand vor der Kennzeichnung, stand in keiner Katalog-Revision mehr). `cleanup_extras()` wurde mit einer **eigenen** Quelle (`probe-cleanup`, eine Collection mit Refresh-Job + eine Suche) live geprüft: beide Artefakte weg, ihr Cron-Job (`collection_refresh:<key>`) ebenfalls, und die 5 Collections/10 Suchen der Quelle `meadow-demo` unverändert. Die Abnahmevalidierung prüft das dauerhaft („artefact source": 15 Collections/Suchen über `?source=` gefunden); sie ist danach wieder **komplett grün** (11 690 Objekte, 8 Schlüsselattribute ohne Dubletten, Export XSD-valide).

**Noch offen (Stufe 4–5):**

- **Optional (skip ohne `resources/`):** BMEcat-Validierung als *automatisierter* Test (heute nur manuell über die Abnahmevalidierung gelaufen) — analog zur bestehenden optionalen ETIM-Prüfung.
- **Handbuch (erledigt 2026-09-16):** Kapitel „Demo-Daten" in `docs/user/de/06-demo-daten.md` + `docs/user/en/06-demo-daten.md` (gleiche Kapitel-ID `demo-daten`, Deep-Link `/help?page=demo-daten`): Bestand, Profile, `--import`, Blick in die Oberfläche, `--cleanup` (nur die eigene Quelle) und `validate_demo.py`. Keine eigenen Bilder (die Handbuchbilder sind generiert). `tests/test_help.py` bleibt grün (gleiche Kapitel je Sprache, Anker, Querverweise).

**Manuell (Abnahme-Checkliste, im Dokument zu führen):** Grid-Performance bei `demo`/`perf`, Dashboard-Zahlen (`incomplete_by_type`), Klassen-Merkmale im Produktformular, Dimensions-Umschalter auf `/categories`, Vererbung + „Vererbung konsolidieren", Kanalfreigaben + `?released_for=`, gespeicherte Suche, Collection-Refresh-Job, BMEcat-Export (XSD-valide), gespeichertes Export-Profil, `scripts/load_test.py` gegen `perf`.

## 8. Umsetzungsschritte

1. **Katalog & Modell (erledigt 2026-09-15):** `scripts/demo_data_catalog.json` (22 Marken, 93 Baureihen, 166 Generationen, 42 Baugruppen, 55 Teile-Familien, 13 Enum-Sets, 2 Export-Profile) und `build_model()` → `POST /config/import?dry_run=true` liefert **0 Fehler** (176 `created`, 4 `updated`).
2. **Generator-Kern (erledigt 2026-09-15):** deterministische Objekte + Referenzen, geschlossene Chunks, `masters.mef.json` für die geteilten Objekte, `report.json`, `tests/test_demo_data.py` (29 Tests, auf dem `demo`-Profil).
3. **Einspielweg (erledigt 2026-09-15):** `--import` (Login → MCF-Merge → geteilte Objekte → Chunks → Klassen-Merkmale → Extras → Verifikation gegen `GET /objects/stats`) und `--cleanup` (listet die Objekte der Quelle **pro Typ**, löscht die Top-Level-Typen und danach die Typen, die eine Kaskade normalerweise mitnimmt — fängt Duplikate und Orphans, `404` = Kaskade); idempotent über die Quell-Referenz (zweiter Lauf `updated` statt `created`), Extras als Upsert (`PATCH /saved-searches/{id}`, `PATCH /collections/{id}`), **Retry-Pass** für Objekte/Referenzen, die unter parallelen Chunks an einem Schreibkonflikt (`HTTP 500`) gescheitert sind.
4. **Regeln, Kanäle, Sammlungen, Suchen, Export-Profile (Modelldaten erledigt):** im Modell und in `extras.json` enthalten; die Regel-**Aktivierung** und die Freigaben laufen über den Importer (Stufe 3).
5. **MCF-Lücke `role` (erledigt 2026-09-15):** Sektion trägt die Rolle, Rollenprüfung + `kind`-Ableitung in `relation_definition_content()`, Taxonomie-Prüfung beim echten Apply (nicht im Dry-Run), 4 Kern-Unit-Tests; `plan/detail/konfiguration-export-import.md` §4.7 und `crates/AGENTS.md` nachgezogen.
6. **`perf` + Turbo-Pfad (offen):** Lastprofil erzeugen, einspielen (parallele Chunks mit erhöhter `JOBS_CONCURRENCY`; optional `.surql`-Import nur für Lastdaten — **so entschieden**) und `scripts/load_test.py` auswerten; Ergebnisse hier dokumentieren.
7. **DOX/Doku (erledigt):** Root-`AGENTS.md` (Skripte inkl. `validate_demo.py`, gebündeltes Cleanup), `plan/detail/AGENTS.md`, `crates/AGENTS.md` (Schlüssel-Serialisierung, `409`-Konflikte, Cron-Validierung, Batch-Delete) und die Kern-Doku aktualisiert; das Handbuchkapitel „Demo-Daten" liegt seit 2026-09-16 in `docs/user/{de,en}/06-demo-daten.md`.

## 9. Getroffene Entscheidungen und offene Punkte

**Entschieden (Auftraggeber, 2026-09-15):**

1. **Profil-Default:** `demo` (tatsächlich 5 950 Objekte / 18 370 Referenzen, Seed 42); `tiny` für Handbuchbilder, `perf` für Lasttests.
2. **Datenquelle:** **eigene** gepflegte Stammdatenliste in `scripts/demo_data_catalog.json`; `auto-parts-db` wird **nicht** verwendet (keine externe Abhängigkeit, keine Lizenzfrage).
3. **MCF-`role`-Lücke:** geschlossen (Kern + Tests + Doku) — damit reisen die Kategorie-Dimensionen im Modelldokument.
4. **Turbo-Pfad für `perf`:** zusätzlich zum API-Weg ist ein **direkter SurrealDB-Import nur für Lastdaten** erlaubt (klar gekennzeichneter Messbestand, nie Demo-/Abnahme-Daten).
5. **Reichweite:** auch die Extras (Freigaben, Collections, Suchen, Jobs, Regel-Aktivierung) gehören zum Generator.
6. **Ablage:** `scripts/` (wie die übrigen Werkzeuge).

**Offen:**

- **Produkt-/Varianten-Schlüssel waren nicht deklariert (erledigt 2026-09-15):** `product`/`variant` kamen aus dem Kern-Seed und hatten **keine** `key_attributes`, obwohl die Attributdefinition `sku` mit `is_key: true` markiert ist — Eindeutigkeit war für die größten Typen also gar nicht aktiv, und auch die Dublettenprüfung lief ins Leere. Der Katalog deklariert die beiden Typen jetzt mit `key_attributes: ["sku"]` (Modell-Dry-Run fehlerfrei, live gepatcht und mit 8 parallelen `POST /objects` verifiziert: 1 × `201`, 7 × `409`, **eine** lebende Zeile).
- **Extras-Quelle (erledigt 2026-09-16):** `collection`/`saved_search` tragen jetzt `source`/`source_id`, und beide Listen filtern mit `?source=` — `--cleanup` löscht damit auch die generierten Extras (Collections vor ihren Filter-Suchen, weil der Kern eine referenzierte Suche mit `409` schützt) und lässt fremde Artefakte in Ruhe. Offen bleibt `job_definition`: die Refresh-Jobs entstehen **aus** der Collection und verschwinden mit ihr (`sync_collection_job(…, None)` beim Delete, live geprüft), eigene Job-Definitionen erzeugt der Generator nicht.
- **Cron-Schemata (erledigt 2026-09-16):** Der Katalog benutzt die sechsfeldige Form (`0 0 3 * * *`), `validate_catalog` prüft die Feldanzahl, und der Kern (`scheduler::validate_schedule`) lehnt alles andere mit `400` ab (`POST`/`PATCH /jobs`, `refresh_interval` einer Collection) — vorher wurde ein fünffeldiges Schema gespeichert und **feuerte nie**; außerdem überspringt der Scheduler einzelne unbrauchbare Definitionen statt den ganzen Sync-Lauf abzubrechen. Die fünf Demo-Collections tragen ihre korrigierten Schemata (live nachgezogen, `GET /jobs` zeigt `0 0 3 * * *` … `0 0 6 * * *`).
- **Referenzauflösung und Retry-Matching (erledigt 2026-09-16):** Ein fehlgeschlagenes Objekt reißt seine Kanten nicht mehr mit — der Worker löst einen unbekannten Endpunkt über seine Quell-Referenz auf (`GET /objects/by-source`, einmal je Id), und der Retry-Pass matcht fehlgeschlagene Referenzen auch dann, wenn der Report eine Record-Id statt der MEF-Id führt. Damit ist ein `--import` auch unter parallelen Chunks verlustfrei: gemeldete `failed`-Referenzen stehen garantiert im Retry-Dokument.
- **Schreibkonflikt (erledigt 2026-09-16):** Ein Konflikt zweier gleichzeitiger Schreiber auf denselben Datensatz ist jetzt **`409`** statt `500` — `error::from_db_message` erkennt die SurrealDB-Meldung („The query was not executed due to a failed transaction", dazu die Varianten zum Read/Write-Konflikt) und antwortet mit `409 "concurrent write conflict, please retry: …"`; alles andere bleibt `500`. **Live bewiesen:** dasselbe gemischte Lastbild (10 parallele Schreiber auf eine Variante: `PATCH`-Attribute, `POST /references`, Umhängen, `PUT /releases`) lieferte vorher **6 × `500`**, danach **0 × `500`** und **17 × `409`**. Worker und Bulk-Operationen unterscheiden die beiden `409`-Bedeutungen an der Meldung: „already exists" = Kante existiert (idempotent, `existing`/„unverändert"), Konflikt = wiederholbar, wird als `failed` gemeldet und vom Retry-Pass erneut eingespielt (`_raise_for_core_error` schreibt die Kern-Meldung in den Report).
- **Eindeutigkeit im Kern ist nicht atomar (datenbankseitig offen):** Der Kern serialisiert die Prüfung + das Schreiben pro Schlüsselwert **in-process** (`crate::key_lock`, Abschnitt 4.4 Punkt 4); das reicht für einen Core-Prozess. Ein Datenbank-Wächter (Unique-Index bzw. Transaktion) wäre die Mehrinstanz-Lösung — dann braucht der Generator den Master-Vorlauf nicht mehr.
- **Doppelte Referenzkanten (erledigt 2026-09-16):** `POST /references` lehnt eine bereits vorhandene Kante jetzt mit **`409`** ab (Prüfung + Write als kritische Sektion pro Kante über `crate::key_lock`), und der Import-Worker meldet sie als **`existing`** statt als Fehler; die Bulk-Operation `add_relation` wertet sie als „unverändert" (kein Fehler). Live verifiziert: zweites `POST /references` ⇒ `409`, zweiter MEF-Import desselben Dokuments ⇒ `updated 2, failed 0`, Referenz `existing`. Damit ist ein `--import` **vollständig idempotent** (Objekte und Kanten); `--cleanup --import` bleibt der Weg für einen echten Neuaufbau.
- **`perf`-Lastdaten in eigener Datenbank (entschieden 2026-09-16):** Der `perf`-Bestand liegt in `meadow_perf` (Kern mit `SURREALDB_DATABASE=meadow_perf`), damit die Demo-Zahlen in `meadow` interpretierbar bleiben und die Abnahmevalidierung weiter läuft. Der Import wurde bei **16 166 Objekten** abgebrochen (Befund: Parallelität skaliert nicht, §4.3), die 7 offenen Job-Runs wurden als `failed` geschlossen; der Bestand genügt für den Read-Lasttest.
- **Kein Turbo-Import in dieser Größe (entschieden 2026-09-16):** 70 363 Objekte in einem Zug brauchen in dieser Form 15–20 h. Vor dem nächsten Versuch: Release-Kern + **sequenzielle** Chunks, sonst der Batch-Upsert im Kern (`POST /objects/batch-upsert`, Phase-9/10) — der direkte SurrealDB-Import bleibt dem klar gekennzeichneten Lastdaten-Bestand vorbehalten.
- `apply_extras` setzt die Kanalfreigaben **sequenziell** (~3 400 `PUT` für `demo`, ~5 min) — bei `perf` parallelisieren (Threadpool) oder über einen Bulk-Endpunkt bündeln.
- **Schreibpfad bei Demo-Größe (gemessen 2026-09-15/16, Abschnitt 4.3):** `DELETE /objects/{id}` ~575 ms (Blatt), `PATCH` ~357 ms, `POST /objects` ~171 ms, `GET /objects/stats` 1 691 ms (kalt; seit 2026-09-16 gecacht). Die Einzelstatements der Löschung liegen bei 80–400 µs, die Zeit steckt also im Schreibpfad **je Request** (Transaktion/Commit, Audit, Outbox, zusätzliche Abfragen je Write). **Gelöst für den Cleanup:** `POST /objects/batch-delete` löscht bis zu 500 Ids in einer Transaktion (12 ms/Objekt statt 591 ms, §4.4 Punkt 8) — der `demo`-Neuaufbau ist damit nicht mehr vom Aufräumen dominiert (`--cleanup` 0,9 min). 4 parallele Clients halfen nie (der Kern serialisiert). Offen bleibt der **Einzel**-Write (~0,2–0,6 s je `POST`/`PATCH`), weil dort Regeln/Vollständigkeit/Outbox pro Request laufen: relevant für Importe (die parallel laufen und deshalb trotzdem skalieren) und für die Bulk-Operationen der UI.
- **`GET /objects/stats` (Zählungen umgebaut und gecacht, 2026-09-16):** Zählungen kommen aus einer SQL-Aggregation, Attribute werden nur für Typen geladen, die unvollständig sein können (`completeness::incomplete_candidates` ↔ `MetaCache::types_with_required`), und die Antwort wird pro Sprache mit einer Datenversion gecacht (warm 1–2 ms statt ~2 s; Invalidierung an allen Schreibpfaden, live geprüft). Der **kalte** Aufruf bleibt ~1–2,7 s (Debug/Release) und ist nur über eine materialisierte Vollständigkeit weiter zu senken — für das Dashboard reicht der Cache.

## 10. Nicht-Ziele

- Keine Übernahme kommerzieller Katalog- oder OEM-Daten; keine Weitergabe von `resources/`-Artefakten.
- Keine Bilder/Assets (kein Upload-Endpunkt im Kern).
- Kein neuer Kerndatenmodell-Begriff: es kommen nur **Daten** hinzu (Objekttypen, Attribute, Relationstypen) — plus die kleine MCF-`role`-Korrektur.
- Keine produktiven Echtdaten: synthetisch, wiederholbar, löschbar (`--cleanup`).

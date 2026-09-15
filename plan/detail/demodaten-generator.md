# Demo- und Lastdaten: Automotive-Katalogdaten

- **Hauptdokument:** Abschnitt 7 ([Integration & Eventsystem](../meadow-pim-architektur.md#7-integration-und-eventsystem))
- **Relevante Phasen:** Phase 8, 9 (Werkzeug und Datenbestand für Vorführung, Handbuch und Lasttests)
- **Stand:** Entwurf (Planung; Generator und Katalogdaten noch nicht umgesetzt)

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
| **`auto-parts-db`** (npm, `lifeofcapo/car-list`) | 142 Marken, 2379 Modelle mit Generationen (ab 1968), 1530 Teile | Paket **MIT** (npm-Registry verifiziert); Herkunft der Daten nicht dokumentiert | **Optional** als Anreicherung: nur lokal unter `resources/automotive/` (gitignored), **nie** Build-, Test- oder Gate-Abhängigkeit |
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

> **Offener Punkt (im Kern verifiziert):** Der **MCF-Import/Export überträgt `role` nicht** (`crates/pim-core/src/api/config.rs`: `relation_definitions_section` schreibt nur `name`/`kind`/`source_types`/`target_types`/`label`/`attributes`/`system`; `apply_relation_definitions` setzt kein `role`). Ein MCF-Roundtrip **degradiert eine Zusatz-Dimension still zu `role: link`** — dann funktionieren Dimension-Umschalter, `?definition_id=`-Filter und Zähler nicht mehr. Empfehlung: MCF-Sektion um `role` erweitern (inkl. derselben Rollenprüfung wie `POST /relation-definitions`) — kleine Änderung, echte Lücke, nicht nur Demo-Bedarf. Bis dahin: die beiden Dimensions-Typen per API anlegen (Schritt 2 des Einspielwegs).

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
| `model.mcf.json` | `languages` (Prüfung), `channels`, `enum_sets`, `object_types`, `attribute_definitions`, `relation_definitions`, `business_rules`, `export_profiles`, `category_attributes` | `POST /config/import?mode=merge` (Admin) |
| `dimensions.json` | die beiden `role: category`-Zusatztypen (bis MCF `role` trägt) | `POST /relation-definitions` |
| `data-<nn>.mef.json` | Objekte + Referenzen, in `--chunks` geschlossene Teildokumente | `POST /imports?emit_events=false&source=meadow-demo` je Chunk, parallel |
| `extras.json` | Kanalfreigaben, Collections + Mitglieder, gespeicherte Suchen, Jobdefinitionen (Cron), Regel-Aktivierung | `PUT /objects/{id}/releases`, `POST /collections…`, `POST /saved-searches`, `POST /jobs`, `POST /rules/{id}/activate` |
| `report.json` | erzeugte Zahlen je Typ/Relation, Optional-Lücken, Prüfsummen | Nachweis (wie `install_standard_bundle.py`) |
| *(optional)* `data.surql` | SurrealDB-Export/Import-Datei | **nur** Turbo-Pfad für Lasttests (Abschnitt 5) |

### 4.2 Einspielreihenfolge

1. `POST /config/import?mode=merge` mit `model.mcf.json` (erst **dry_run** prüfen)
2. Dimensions-Typen per `POST /relation-definitions` (bis MCF `role` trägt)
3. Daten-Chunks parallel über `POST /imports` (Objekte zuerst, innerhalb des Chunks Referenzen danach)
4. `extras.json` (Freigaben, Collections, Suchen, Jobs, Regeln aktivieren)
5. Verifikation (`report.json` gegen die Ist-Zahlen aus `GET /objects/stats`, `GET /classification-standards?counts=true`, `GET /config/export`)

**Idempotenz:** Jedes Objekt trägt ein stabiles `id` und zusätzlich die Quell-Referenz (`source: meadow-demo` plus `source_id`); der Worker gleicht Business-Key → Record-ID → Quell-Referenz ab und meldet je Objekt `created`/`updated`. Ein zweiter Lauf ist damit gefahrlos.

**Chunk-Regel (aus dem Worker-Code):** Referenzen werden nur **innerhalb desselben Dokuments** aufgelöst (`id_map`), ein Chunk muss also beide Endpunkte jeder seiner Referenzen enthalten. Objekte dürfen dafür in **mehreren** Chunks vorkommen — sie werden dann aktualisiert statt dupliziert. So bleiben die Chunks in sich geschlossen und parallel einspielbar.

### 4.3 Gemessener Durchsatz des Import-Pfads (2026-09-15, lokal)

| Lauf | Objekte | Referenzen | Dauer | Rate |
| --- | --- | --- | --- | --- |
| ETIM-Paket (Bestand, 14.09.) | 5 640 (create) | – | 384 s | **14,7 obj/s** |
| ETIM-Paket (Bestand, 14.09.) | 5 640 (update) | – | 743 s | **7,6 obj/s** |
| Probe 1 Chunk | 120 (update) | 360 | 21,8 s | 5,5 obj/s · 16,5 ref/s |
| Probe **4 Chunks parallel** | 120 (create) | 360 | **12,9 s** (Wanduhr) | 9,3 obj/s · 27,8 ref/s |

Der Worker zieht `JOBS_CONCURRENCY` (Default **4**) Jobs gleichzeitig; parallele Import-Chunks skalieren also messbar (~2× Wanduhr gegenüber einem Dokument) und prüfen zugleich die Job-Queue.

**Konsequenz (Rechengröße für `demo`, ~7 000 Objekte / ~23 000 Referenzen):** sequenziell ≈ 8 min + 25 min ≈ **35 min**; mit 4 Chunks ≈ **15–20 min**. `perf` (50 000 Objekte / 200 000 Referenzen) wäre sequenziell ≈ **3–4 h** → braucht den Turbo-Pfad.

## 5. Profile und Turbo-Pfad

| Profil | Objekte | Referenzen | Zweck | Weg |
| --- | --- | --- | --- | --- |
| `tiny` | ~400 | ~800 | Handbuchbilder, Smoke-Demo, Erstprüfung des Generators | 1 Chunk, < 1 min |
| `demo` (Default) | ~7 000 | ~23 000 | Vorführung, Abnahme, Vollständigkeits-/Grid-/Export-Prüfung | 4–8 Chunks, ~15–20 min |
| `perf` | 50 000 | 200 000 | Lasttest (`scripts/load_test.py`), Grid-/Stats-/Such-Optimierung | Turbo nötig |

**Turbo-Optionen (für `perf`, in dieser Reihenfolge):**

1. **Parallele Chunks + `JOBS_CONCURRENCY` erhöhen** (8–16) — nutzt den offiziellen Weg, keine Kern-Änderung; Grenze ist der Kern (HTTP-Upserts).
2. **Direkter SurrealDB-Import** (`surreal import` einer erzeugten `.surql`-Datei) — Minuten statt Stunden, **umgeht aber API, Validierung, Audit und Outbox-Events**. Nur als klar gekennzeichneter Lastdaten-Bestand, nie für Demo-/Abnahme-Daten („Keine Fallbacks"-Prinzip bleibt gewahrt, weil es kein Kompatibilitätspfad, sondern ein bewusst anderer Datenweg ist).
3. **Batch-Upsert im Kern** (neuer Endpunkt/Job) — größter Hebel, aber echte Produktänderung; **nicht** Teil dieser Planung, als Phase-9-Idee vermerkt.

## 6. Werkzeug-Schnitt

- **`scripts/demo_data.py`** (Generator + CLI, ein Modul wie die bestehenden Skripte; Tests laden es per `spec_from_file_location`):
  `--profile tiny|demo|perf`, `--seed 42`, `--chunks 4`, `--out-dir .tmp/demo-data`, `--base-url http://127.0.0.1:8080`, `--user/--password`, `--dry-run` (nur Dateien schreiben), `--import` (einspielen), `--cleanup` (erzeugte Objekte soft-deleten), `--surql` (Turbo-Datei zusätzlich).
- **`scripts/demo_data_catalog.json`** (Abschnitt 3.8) — Daten getrennt von Logik, damit Marken/Teile ohne Codeänderung wachsen.
- **Konventionen:** Konsolen-UTF-8 (`configure_console()`, Windows cp1252-Falle), Login → MCF → Imports → Polling des `job_run`, Abschlussreport wie `install_standard_bundle.py`; Ausgaben unter `.tmp/` (gitignored), Scratch-Dateien nie im Repo.
- **Abhängigkeiten:** nur `httpx` + Standardbibliothek (`random`, `json`, `argparse`) — **kein** npm, kein `auto-parts-db` im Pflichtpfad, **kein** Netzwerkzugriff im Normalbetrieb.

## 7. Verifikation

**Automatisiert (`tests/test_demo_data.py`, Teil des pytest-Gates):**

- **Determinismus:** gleicher Seed → byte-identische Ausgabe; anderer Seed → andere Daten.
- **Referenzielle Integrität:** jede Referenz hat beide Endpunkte im selben Chunk; keine Referenz auf unbekannte Objekt-ID; jede `product`-→`vehicle_generation`-Kante nur zwischen erlaubten Typen.
- **Schlüssel:** keine doppelten `sku`/`code` innerhalb des Bestands; `is_key`-Attribute gesetzt.
- **MEF/MCF-Wohlgeformtheit:** `objects`/`references` Arrays, `format_version`, Status nur aus `draft|in_review|approved|published|archived`, Attributnamen existieren als Definition, Enum-Werte im jeweiligen Set, `range`-Werte als `{from,to}`.
- **Standardbindung:** jedes `standard_codes`-Ziel existiert im installierten Standard (`GET /classification-standards`), Klassen-Merkmale verweisen auf bekannte Attribute.
- **Profilzahlen:** Objekt-/Referenzzahlen je Profil entsprechen dem Report (Schutz gegen stille Regressionen).
- **Optional (skip ohne `resources/`):** BMEcat-Validierung des Demo-Exports gegen die XSD — analog zur bestehenden optionalen ETIM-Prüfung.

**Manuell (Abnahme-Checkliste, im Dokument zu führen):** Grid-Performance bei `demo`/`perf`, Dashboard-Zahlen (`incomplete_by_type`), Klassen-Merkmale im Produktformular, Dimensions-Umschalter auf `/categories`, Vererbung + „Vererbung konsolidieren", Kanalfreigaben + `?released_for=`, gespeicherte Suche, Collection-Refresh-Job, BMEcat-Export (XSD-valide), gespeichertes Export-Profil, `scripts/load_test.py` gegen `perf`.

## 8. Umsetzungsschritte

1. **Katalog & Modell (Planung → Daten):** `demo_data_catalog.json` (Marken/Modelle/Generationen/Baugruppen/Teile-Familien/OE-Muster) und das MCF-Modell erzeugen; `POST /config/import?dry_run=true` muss fehlerfrei sein.
2. **Generator-Kern:** Objekte + Referenzen deterministisch, Chunk-Bildung, `report.json`, `tests/test_demo_data.py` (Determinismus, Integrität, Zahlen).
3. **Einspielweg:** Login/MCF/Imports/Extras/Verifikation als CLI, idempotent, inkl. `--cleanup`.
4. **Regeln, Kanäle, Sammlungen, Suchen, Export-Profile**; Regel-Aktivierung und Freigaben im Demo plausibel mischen.
5. **MCF-Lücke `role`** schließen (Relationstypen-Sektion + Tests + Doku) — sonst degradiert jede Zusatz-Dimension beim MCF-Roundtrip zu `link`.
6. **`perf` + Turbo-Pfad** und Lasttest-Auswertung; Ergebnisse hier dokumentieren (Durchsatz, Flaschenhälse, ggf. Optimierungsauftrag).
7. **DOX/Doku:** Root-`AGENTS.md` (Skripte), `docs/`-Verweis (Demo-Daten für Handbuchbilder), Handbuch-Kapitel „Demo-Daten", `plan/detail/AGENTS.md`/Hauptdokument-Änderungsnotiz.

## 9. Offene Entscheidungen

1. **Profil-Default:** `demo` mit ~7 000 Objekten/~23 000 Referenzen (Empfehlung) oder kleiner (`tiny` als Default, `demo` auf Abruf)?
2. **`auto-parts-db`:** als optionale, gitignorierte Anreicherung zulassen (Empfehlung) oder ausschließlich eigene Stammdatenliste?
3. **MCF-`role`-Lücke:** jetzt schließen (Empfehlung, kleine Änderung) oder Generator umgeht sie per API-Aufruf?
4. **Turbo-Pfad für `perf`:** direkter SurrealDB-Import erlaubt (nur Lastdaten) oder ausschließlich über die API (dann `perf` mit mehreren Stunden Laufzeit)?
5. **Reichweite:** nur Daten (MCF/MEF) oder auch die Extras (Freigaben, Collections, Suchen, Jobs, Regel-Aktivierung) im Generator?
6. **Ablage der Stammdatenliste:** `scripts/` (Empfehlung, wie die übrigen Werkzeuge) oder neuer Top-Level-Ordner mit eigenem DOX-Knoten?

## 10. Nicht-Ziele

- Keine Übernahme kommerzieller Katalog- oder OEM-Daten; keine Weitergabe von `resources/`-Artefakten.
- Keine Bilder/Assets (kein Upload-Endpunkt im Kern).
- Kein neuer Kerndatenmodell-Begriff: es kommen nur **Daten** hinzu (Objekttypen, Attribute, Relationstypen) — plus die kleine MCF-`role`-Korrektur.
- Keine produktiven Echtdaten: synthetisch, wiederholbar, löschbar (`--cleanup`).

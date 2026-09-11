# Suchformular für gespeicherte Suchen (schema-getriebener Query-Builder)

- **Hauptdokument:** Abschnitt 10 ([Gespeicherte Suchen & Collections](../meadow-pim-architektur.md#10-gespeicherte-suchen--materialisierte-collections)) und Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), aufbauend auf Phase 9, Punkt 1
- **Stand:** Plan / Entwurf (2026-09-11) — **nicht umgesetzt**. Umsetzungsreif für **Stage A** (Feldkatalog + Validierung + Formular für `filters`/`fulltext`/`limit`); Stage B/C sind skizziert und offen.

**Getroffene Entscheidungen (Vorschlag, zur Bestätigung):**

- **Der Feldkatalog ist Server-Vertrag** (`GET /search-fields`) — nicht Frontend-Wissen. Damit nutzen UI, MCP/CLI und die Validierung dieselbe Quelle (genau das Muster, das sich beim Konnektor-Formular bewährt hat: [konnektor-konfiguration.md](konnektor-konfiguration.md)).
- **Das Formular bleibt flach:** eine Liste von Bedingungen, die **UND**-verknüpft sind. Die DSL kennt heute keine Gruppen/Verschachtelung; ODER-Gruppen wären eine **DSL-Erweiterung** und sind Stage C.
- **JSON bleibt** als Expertenmodus erhalten (Tab „JSON“), inklusive Bestandteilen, die das Formular nicht darstellt; nichts wird beim Speichern stillschweigend entfernt.
- **Kein neues Rollenmodell:** Katalog lesen `editor+`/`X-Api-Key` (Metadaten, wie `/attribute-definitions`), Suchen schreiben bleibt `admin`.
- **Regel:** Jedes im Katalog angebotene Feld/Operator-Paar wird vom Kern ausgeführt, und jeder ausgeführte Operator steht im Katalog (sonst ist das Formular Kosmetik).
- **Ehrliche UI statt Scheinfunktion:** Volltext läuft über **alle** Attributwerte (der Index liegt auf `object.attributes`); eine Feld-/Sprachauswahl für den Volltext wird **nicht** angeboten, solange sie nichts bewirkt.

## 1. Ausgangslage & Problem

Die Verwaltungsseite `/saved-searches` (`meadow/frontend/saved_searches.py`, admin-only) legt Suchen in einem Dialog an, dessen Suchkriterien eine **JSON-CodeMirror-Fläche** mit der Query-DSL sind (`filters` / `fulltext` / `references` / `sort` / `start` / `limit`). Ausgeführt wird die DSL im Kern (`execute_query` in `crates/pim-core/src/api/saved_searches.rs`: Feld-Whitelist `type`/`status`/`id`/`attributes.<name>[.<lang>]`, Operatoren `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `not_contains`, `not_empty`, `is_null`, `starts_with`, `ends_with`).

Für technisch versierte Anwender ist das ausreichend, für den normalen (nicht-technischen) Admin eine Hürde – dieselbe Ausgangslage wie bei den Konnektoren vor Stage 1:

- **Kein Auffinden der Felder:** Attributnamen, Operatoren und die Punktnotation (`attributes.name.de`) kennt nur, wer die Doku liest. Im Dialog steht nur ein Hinweis-Satz.
- **Keine Feldvalidierung:** Der Kern prüft beim **Anlegen/Ändern nichts** – die Query wird gespeichert, wie sie kommt. Erst die **Ausführung** meldet „unsupported filter field/operator“ (oder liefert stillschweigend 0 Treffer, wenn ein Attributname falsch geschrieben ist, aber syntaktisch erlaubt).
- **Typfehler sind unsichtbar:** `{"field": "attributes.price", "operator": "gt", "value": "teuer"}` wird gespeichert und schlägt erst beim Ausführen fehl; `in` ohne Array liefert keine Treffer statt einer Meldung.
- **Mehrsprachigkeit und Enums:** Für mehrsprachige Attribute muss die Sprachvariante von Hand geschrieben werden; Enum-Werte müssen als **Code** eingetragen werden (Labels kennt das JSON nicht).
- **Doppelpflege:** Operator-Whitelist und Feldregeln stehen im Kern; ein Formular müsste sie sonst im Frontend nachbauen.
- **Kein Zwischenstand:** Es gibt keine Vorschau („so viele Treffer“) – man muss speichern und dann „Ausführen“ drücken.

**Nicht das Problem:** Die Seite selbst (Liste, Ausführen, Löschen, Collections, Refresh) ist umgesetzt und bleibt unverändert. Es geht ausschließlich um den **Editor** der Suchkriterien.

## 2. Typische Lösungen (Recherche)

- **jQuery QueryBuilder / React Query Builder** (die verbreitetsten Baukästen; beide wurden für diesen Plan angesehen): Ein **Filterkatalog** beschreibt je Feld `id`/`field`, `label`, **`type`** (`string`, `integer`, `double`, `date`, `time`, `datetime`, `boolean`), **`input`** (`text`, `number`, `textarea`, `radio`, `checkbox`, `select`), `values` (Optionslisten), **`operators`** (welche Operatoren für dieses Feld zulässig sind) und `default_operator`. Die **Operatoren** selbst tragen die Eigenschaften `nb_inputs` (**0** z. B. für „ist leer“, **1** für die meisten, **2** für „between“), `multiple` (`true` für `in`/`not_in`) und `apply_to` (Feldtypen, für die sie gelten). Der Builder rendert daraus Zeilen „Feld ▾ · Operator ▾ · Wert“, bietet „+ Regel“, **optional** Gruppen (UND/ODER, per `allow_groups` abschaltbar), eine **Validierung je Regel** (mit Fehler-Icon/Tooltip) und lokalisierte Operator-/Bedingungstexte. Ausgabe ist **strukturiertes JSON** – genau die Form, die unsere DSL schon hat.
- **Segment-/Filter-Builder in PIM-Shops (Akeneo, Shopify)** und **Kibana/Grafana/Airtable**: Einstieg ist eine **flache Filterliste** mit „Bedingung hinzufügen“, einem **Ergebniszähler** und gespeicherten Sichten; komplexe Ausdrücke wandern in einen Expertenmodus (Query-String/JSON). Der Wert eines guten Builders liegt dort vor allem in **Auswahllisten statt Freitext** und **sofortigem Feedback**.
- **Deklaratives Schema → Formular** (n8n-Filterknoten, Home-Assistant-Bedingungen, JSON Schema + `uiSchema`, aus der Konnektor-Recherche): Feldbeschreibung, Anzeige-Widget und Sichtbarkeitsbedingung kommen aus einem Server-Vertrag; das Frontend rendert nur. Das ist die Vorlage für den Feldkatalog hier.
- **Gemeinsames Muster:** (1) Feldkatalog als Single Source of Truth, (2) Zeilen aus Feld/Operator/Wert, (3) **Operator setzt das Wert-Widget** (kein Widget für 0-Input-Operatoren, Mehrfachauswahl für `in`), (4) Validierung vor dem Speichern mit Pfad/Meldung, (5) Ergebnis-Vorschau, (6) **JSON als Rückfall**, (7) Gruppen/ODER nur, wenn die Datenstruktur sie kann.

**Konsequenz für Meadow:** Feldkatalog + Validierung in den Kern, Zeilen-Editor im Frontend, flaches UND, JSON-Tab als Rückfall. Genau die Arbeitsteilung des Konnektor-Formulars, nur mit einer **Zeilenliste** statt einer Feldliste.

## 3. Zielbild

```
Suche bearbeiten                                              [ Formular | JSON ]
Name      [ Aktive Produkte mit Preisfehler        ]   Cron (optional) [ 0 0 2 * * * ]

▾ Bedingungen (alle müssen zutreffen)
   [ Objekttyp ▾ ] [ ist ▾ ]            [ product      ▾ ]  ✕
   [ Status     ▾ ] [ ist einer von ▾ ] [ published ✕ approved ✕ ] [ + ]  ✕
   [ Preis      ▾ ] [ kleiner als ▾ ]   [ 0                ]  ✕
   [ Name (en)  ▾ ] [ ist nicht leer ▾ ]                      ✕
   [ + Bedingung ]

▾ Volltext
   [ Edelstahl                                            ]   ← durchsucht alle Attributwerte

▾ Grenzen
   Treffer-Limit [ 500 ]      Überspringen [ 0 ]

▾ Vorschau
   „Aktuell 42 Treffer“  [ Vorschau aktualisieren ]           ← nur Anzeige, speichert nichts
```

- **Feldauswahl** ist ein gruppiertes Select (Objektfelder · Attribute, alphabetisch nach Label).
- **Operatorauswahl** hängt am Feld (`operators` aus dem Katalog), **Wert-Widget** am Feldtyp **und** am Operator (`nb_inputs`/`multiple`).
- **Mehrsprachige Attribute** bekommen zusätzlich ein Sprach-Select (aktive Datensprachen) – daraus entsteht `attributes.<name>.<lang>`; ohne Sprachwahl gilt das Basisfeld.
- **Enum-Attribute** zeigen ihre **Labels** (mehrsprachig, aus dem Enum-Set) und speichern die **Codes**.
- **Referenzen** (Stage B) sind eine eigene Zeilenliste: Relationstyp + Zielobjekt (gemeinsamer Picker) + Richtung.
- **Sortierung** (Stage B) ist eine Zeilenliste aus sortierbarem Feld + Richtung.

## 4. Feldkatalog (Kern-Vertrag)

**Neuer Endpunkt:** `GET /search-fields` (read-only, `editor+` oder `X-Api-Key`) liefert alles, was das Formular zum Rendern und Prüfen braucht.

```json
{
  "groups": [
    { "key": "system", "label_key": "search_form.group_system" },
    { "key": "attribute", "label_key": "search_form.group_attributes" }
  ],
  "operators": [
    { "type": "eq", "label_key": "search_form.op_eq", "arity": 1, "multi": false },
    { "type": "in", "label_key": "search_form.op_in", "arity": 1, "multi": true },
    { "type": "not_empty", "label_key": "search_form.op_not_empty", "arity": 0, "multi": false }
  ],
  "fields": [
    {
      "key": "type", "group": "system", "label_key": "saved_searches.field_type",
      "value_type": "string", "widget": "select", "options_source": "object_types",
      "operators": ["eq", "ne", "in", "not_in"], "sortable": true, "fulltext": false
    },
    {
      "key": "status", "group": "system", "label_key": "saved_searches.field_status",
      "value_type": "string", "widget": "select",
      "options": [{ "value": "draft", "label_key": "status.draft" }],
      "operators": ["eq", "ne", "in", "not_in"], "sortable": true, "fulltext": false
    },
    {
      "key": "attributes.name", "group": "attribute", "label": { "en": "Name", "de": "Name" },
      "value_type": "string", "widget": "text", "multilingual": true,
      "languages": ["de", "en"],
      "operators": ["eq", "ne", "contains", "not_contains", "starts_with", "ends_with", "not_empty", "is_null"],
      "sortable": true, "fulltext": true
    },
    {
      "key": "attributes.price", "group": "attribute", "label": { "en": "Price", "de": "Preis" },
      "value_type": "number", "widget": "number", "unit": { "symbol": "€", "factor": 1.0 },
      "operators": ["eq", "ne", "gt", "gte", "lt", "lte", "not_empty", "is_null"],
      "sortable": true, "fulltext": false
    },
    {
      "key": "attributes.brand", "group": "attribute", "label": { "en": "Brand", "de": "Marke" },
      "value_type": "enum", "widget": "select",
      "options": [{ "value": "ACME", "label": { "en": "ACME", "de": "ACME" } }],
      "operators": ["eq", "ne", "in", "not_in", "not_empty", "is_null"],
      "sortable": false, "fulltext": false
    }
  ],
  "references": [
    { "type": "category_member", "label": { "en": "Category member", "de": "Kategorie" },
      "roles": ["category"], "target_types": ["category"] }
  ],
  "limits": { "max": 10000, "default": 1000 }
}
```

**Feldattribute (verbindlich):**

| Attribut | Bedeutung |
|---|---|
| `key` | Pfad in der DSL, **Punktnotation** (`attributes.<name>` bzw. `attributes.<name>.<lang>`) |
| `group` | Abschnitt im Feld-Select (`system`, `attribute`; später ggf. `relation`) |
| `label_key` **oder** `label` | Feste UI-Labels über i18n-Key, Datenlabels (Attribute) als mehrsprachige Map |
| `value_type` | `string` · `number` · `boolean` · `date` · `enum` |
| `widget` | `text` · `number` · `switch` · `select` · `date` |
| `options` / `options_source` | feste Optionen (Label + Wert) bzw. dynamische Quelle (`object_types`, `languages`) |
| `multilingual` + `languages` | Feld hat Sprachvarianten; die UI bietet ein Sprach-Select an |
| `unit` | Anzeige-Hinweis für quantifizierte Werte (nur Darstellung, der Wert bleibt kanonisch) |
| `operators` | **erlaubte** Operatoren für dieses Feld (Reihenfolge = Anzeige) |
| `sortable` | Feld darf in `sort` verwendet werden |
| `fulltext` | Feld fließt in die Volltextsuche ein (heute: nur Kennzeichnung, siehe §7) |

**Operatorattribute:** `type` (DSL-Code), `label_key`, `arity` (0 = kein Wert, 1 = ein Wert), `multi` (Wert ist eine Liste). Beides steuert das Widget: `arity: 0` blendet den Wert aus, `multi: true` macht eine Mehrfachauswahl daraus.

**Herkunft der Daten:** `attribute_definition` (Name, `label`, Datentyp, `multilingual`, `enum_set_id`, `unit_id`, `computed`), `object_type` (Typ-Optionen), Workflow-Status (Kern-Konstante), `enum_set`/`enum_option` (Optionsliste mit Labels), `language_tree` (aktive Datensprachen), `relation_definition` (Referenzen mit Rolle/Zieltypen, Stage B).

**Wo der Katalog lebt:** neues Modul `src/api/search_schema.rs`, ausgeliefert über `search.rs`/`saved_searches.rs`. Verworfen: **Katalog nur im Frontend** (doppelte Pflege, keine Servervalidierung, MCP/CLI ohne Schema) und **freies JSON-Schema** (unsere DSL ist kein JSON Schema; Operatoren/Widget-Zuordnung wäre umständlicher).

## 5. Validierung & Schreib-Semantik

**Der Kern validiert die Query** bei `POST /saved-searches` und `PATCH /saved-searches/{id}` (heute passiert das nicht):

- **Feld:** muss im Katalog stehen (Systemfeld, existierendes Attribut, ggf. mit aktiver Sprache). Unbekannt → `400` mit Pfad: `query.filters[1].field: unknown field \`attributes.preis_alt\``.
- **Operator:** muss für dieses Feld erlaubt sein: `query.filters[1].operator: \`gt\` is not allowed for \`attributes.brand\` (use eq, ne, in, not_in)`.
- **Wert:** `arity: 0` verlangt **keinen** Wert (ein mitgeschickter Wert wird ignoriert oder abgelehnt), `arity: 1` verlangt einen; `multi: true` verlangt eine **Liste**, sonst einen Skalar. Typfehler werden gemeldet (`must be a number`, `must be a list`), Enum-Werte gegen die Optionsliste geprüft (unbekannter Code → Meldung **oder** Warnung, siehe Abschnitt 11).
- **Sortierung:** nur `sortable`-Felder, `order` ∈ `asc`/`desc`; **`limit`** 1–10000, **`start`** ≥ 0 (der Ausführer klemmt heute stillschweigend – künftig meldet der Kern).
- **Mehrere Fehler** werden gesammelt (`; `-getrennt) wie bei den Konnektoren; `ApiError` bleibt unverändert einfach.
- **Ausführen bleibt tolerant:** `POST /saved-searches/{id}/execute` validiert ebenfalls (damit Altbestand nicht stillschweigend 0 Treffer liefert), aber mit **denselben** Regeln – kein zweiter Pfad.
- **Bestandsschutz:** Suchen, die heute gespeichert sind, können Felder/Operatoren enthalten, die der strengere Katalog nicht mehr kennt (Attribut gelöscht, Operator entfernt). Empfehlung: **Validierung beim nächsten Speichern** (die Meldung nennt die Zeile), das Formular markiert solche Zeilen sichtbar („nicht mehr vorhanden“), und der JSON-Tab bleibt unangetastet. Alternative (offener Punkt): ein Schalter `strict_fields=false` für Installationen mit Altbestand.

## 6. Frontend

**Neues Modul `meadow/frontend/search_form.py`** (analog zu `connector_form.py`), reine, testbare Helfer zuerst:

- **Abbildung DSL ↔ Zeilen:** `filter_to_row(filter, catalog)` / `row_to_filter(row, catalog)` – inklusive Sprachauswahl (`attributes.name` + Sprache `de` → `attributes.name.de`) und Enum-Label ↔ Code.
- **Abhängigkeiten:** `operators_for(field_key, catalog)`, `default_operator(field_key, catalog)`, `needs_value(operator)`, `is_multi(operator)`, `value_widget(field, operator)`.
- **Validierung vorab** (Spiegel des Kerns, gleiche Meldungen wie das Konnektor-Formular): `validate_rows(rows, catalog, locale)`.
- **Gesamt-Query:** `query_from_rows(state, catalog)` / `rows_from_query(query, catalog)` für `filters`/`fulltext`/`references`/`sort`/`start`/`limit`.
- **Hinweis auf nicht dargestellte Bestandteile:** `unsupported_parts(query, catalog)` (z. B. unbekanntes Feld, `start > 0` wenn nicht im Formular) → Liste mit Zähler im Dialog.
- **Zusammenfassung** für die Listenspalte: `summary_text(query, catalog, locale)` – z. B. `3 Bedingungen · Volltext „Edelstahl“ · 500 Treffer`.

**UI-Bausteine:** `FilterRows` (Zeilenstapel: Feld-Select gruppiert, Operator-Select mit i18n-Labels, Wert-Widget je Typ/Operator, ✕ je Zeile, „+ Bedingung“), `ReferenceRows` und `SortRows` (Stage B), Volltext-Eingabe, `limit`/`start`, **Vorschau** (siehe §8) und die Tabs **Formular/JSON**.

**Wiederverwendung:** `object_picker.open_object_picker` für Referenzziele (inkl. `target_types` aus dem Katalog), die Enum-Auflösung wie im Grid (`enum_code_label_map`), das Patch-/JSON-Muster des Konnektor-Dialogs (Tab-Synchronisation, „nicht dargestellt“-Hinweis, `parameters_mode`-Analogon gibt es hier nicht – die Query wird als Ganzes gespeichert).

**i18n:** neue Keys `search_form.*` (Operatoren, Gruppen, Titel, Hinweise, „Bedingung hinzufügen“, Validierungsmeldungen) sowie ein Label-Key für die Listenspalte; Attribut- und Enum-Labels sind **Daten**, keine i18n-Keys.

## 7. Bewusste Einschränkungen (Komplexitätsbremse)

Ein vollwertiger Query-Builder kann beliebig groß werden. Deshalb gilt für dieses Vorhaben:

1. **Flaches UND, keine Gruppen.** Die DSL kann keine ODER-/verschachtelten Ausdrücke – sie zu bauen wäre eine **DSL-Erweiterung** (Kern + Ausführer + Katalog + Formular) und ist **nicht** Teil dieses Plans (Stage C, nur bei echtem Bedarf).
2. **Kein `between`, kein „zwischen“.** Wäre `arity: 2`; bis dahin zwei Zeilen (`gte` und `lte`).
3. **Volltext ohne Feld-/Sprachauswahl.** Der Index liegt auf `object.attributes`; die UI verspricht keine Einschränkung, die nichts bewirkt. Per-Feld-Volltext wäre eine eigene Aufgabe (Index je Feld).
4. **Wert-Widgets nur für die vorhandenen Werttypen** (Text, Zahl, boolesch, Datum, Enum). Medien-/Referenz-Attribute sind **nicht** filterbar (heute ebenfalls nicht).
5. **Kein Ersatz des JSON-Tabs.** Alles, was das Formular nicht kann, bleibt dort möglich – und wird im Formular als „nicht dargestellt“ gemeldet.
6. **Falls Stage A zu groß wird**, ist die Reihenfolge der Streichungen: zuerst `sort` (bleibt JSON), dann `references`, zuletzt `fulltext` – `filters` + `limit` sind der Kern des Nutzens.

## 8. Auswirkungen auf bestehende Dokumente & Endpunkte

| Stelle | Änderung |
|---|---|
| `api-design.md` | neue Endpunkte `GET /search-fields` (geplant) und `POST /saved-searches/preview` (geplant, Abschnitt 9); Hinweis, dass `POST/PATCH /saved-searches` die Query validiert |
| `gespeicherte-suchen-collections.md` | Verweis auf dieses Dokument; „Editor als JSON“ bleibt als Ist-Stand, das Formular als Ausbau |
| `frontend.md` | Modul `search_form.py` und der Tab-Aufbau des Dialogs; die Zeile „Erweiterte Suche mit Query-Builder“ in den späteren Ausbaustufen verlinkt hierher; dasselbe Formular kann später die erweiterte Suche im Grid bedienen |
| `datenexport-import-ui.md` | Quelle `saved_search` im Export-Wizard profitiert vom Katalog (Feldauswahl) — nur Hinweis |
| `crates/AGENTS.md`, `meadow/AGENTS.md` | Modul `search_schema.rs` bzw. `search_form.py` + Regel „Katalog ↔ Ausführer“ |
| `plan/detail/AGENTS.md` | neues Detaildokument in die Liste |

## 9. Implementierungsschritte

**Stage A — Katalog, Validierung, Formular (MVP):**

1. **Kern:** `src/api/search_schema.rs` (Feldkatalog aus `attribute_definition`/`object_type`/`enum_set`/`language_tree`/Status-Konstante, Operatortabelle mit `arity`/`multi`, `validate(query, catalog)` mit Pfadmeldungen, `catalog_document()`), Endpunkt `GET /search-fields` (`editor+`/`X-Api-Key`), Validierung in `create_saved_search`/`update_saved_search` (auch `execute`), Unit-Tests für Katalog und Validator.
2. **Frontend:** `search_form.py` (Helfer + Zeilen-Editor + Tabs Formular/JSON, Hinweis auf nicht dargestellte Bestandteile, Zusammenfassungsspalte), Dialog in `saved_searches.py` umbauen, `api.py` um `list_search_fields` erweitern, i18n-Keys in en/de/fr.
3. **Vorschau:** `POST /saved-searches/preview` (Body = Query, `editor+`) → `{count, ids}`; Button „Vorschau“ mit Debounce, Anzeige „Aktuell N Treffer“ (rein lesend, speichert nichts).
4. **Doku/Test:** dieses Dokument auf „umgesetzt (Stage A)“ setzen, Querverweise, pytest für die Helfer, Browser-Verifikation mit einer echten Suche.

**Stage B — Referenzen, Sortierung, Feinschliff:**

5. `references` (Relationstyp + Zielobjekt über den Picker + Richtung) und `sort` (Feld + Richtung) im Formular; `start` im Formular sichtbar machen; Spalte „Zusammenfassung“ in der Liste.

**Stage C — nur bei Bedarf:**

6. ODER-/Gruppenausdrücke (DSL-Erweiterung), `between` (`arity: 2`), Volltext je Feld (Index je Attribut), Übernahme desselben Katalogs in eine **erweiterte Suche im Grid** (`/products`).

**Nicht-Ziele:** kein Ersatz des JSON-Expertenmodus, keine Migration bestehender Suchen, keine neuen Rollen, keine Änderung an Collections/Refresh, kein Query-Builder für die Middleware-Schnittstellen.

## 10. Testfälle

- `GET /search-fields` liefert Systemfelder, Attributfelder mit Typ/Operatoren/`sortable`, Enum-Optionen mit Labels und die Operatortabelle mit `arity`/`multi`.
- `POST /saved-searches` mit unbekanntem Feld → `400` mit `query.filters[0].field`; mit Operator, der nicht zum Feld passt → `400` mit Hinweis auf die erlaubten; `in` ohne Liste → `400`; `gt` auf einem Enum-Feld → `400`; `limit: 99999` → `400`; gültige Query → `201`.
- Bestands-Query (heute gespeichert, Feld existiert) bleibt gültig; dieselbe Query mit gelöschtem Attribut → `400` mit Pfad (Entscheidung s. Abschnitt 11).
- Formular-Roundtrip: Zeilen → DSL → Zeilen ist verlustfrei (inkl. mehrsprachigem Feld, Enum-Code, `not_empty` ohne Wert).
- Operatorabhängiges Widget: `not_empty` blendet den Wert aus, `in` bietet Mehrfachauswahl, `gt` zeigt ein Zahlenfeld.
- JSON-Tab → Formular: nicht darstellbare Bestandteile werden gezählt und angezeigt, **nicht** verworfen; Speichern aus dem Formular schickt genau die dargestellte Query plus die unveränderten Bestandteile.
- Vorschau: Query ohne Speichern → Trefferzahl; Vorschau schreibt nichts (kein Objekt, keine Collection, kein Event).
- Rechte: `editor` liest den Katalog, schreibt aber **keine** Suche (`403`); `viewer` → `403` für den Katalog.
- Locale-Parität für die neuen Keys.

## 11. Offene Punkte

| Frage | Vorschlag |
|---|---|
| Validierung existierender Felder bei Altbestand? | **Meldung beim nächsten Speichern** (Pfad + Zeile), markierte Zeile im Formular; optionaler Schalter `strict_fields=false` nur, falls echte Installationen betroffen sind |
| Unbekannte Enum-Codes im Wert? | **Warnung statt Fehler** – Codes können aus alten Enum-Sets stammen; die UI markiert sie, der Kern akzeptiert sie (der Ausführer vergleicht nur Codes) |
| Gruppen/ODER? | **Nicht** in Stage A/B; DSL-Erweiterung erst bei belegtem Bedarf (Stage C) |
| Volltext je Feld statt global? | Erst mit Index je Feld; bis dahin ehrlich „über alle Attributwerte“ |
| Vorschau-Endpunkt oder Speichern zum Testen? | **Preview-Endpunkt** (rein lesend) – sonst hinterlässt jeder Testversuch eine Suche |
| Zählung im Vorschaubild teuer? | Vorschau mit Debounce (~800 ms) und `limit`-Obergrenze; bei großen Datenbeständen optional „nur zählen“ (COUNT statt ID-Liste) |
| Denselben Katalog auch für die erweiterte Suche im Grid? | **Ja, mittelfristig** – derselbe Vertrag, dasselbe Formular; im Grid ist es ein Filter, in `/saved-searches` ein gespeicherter Ausdruck |
| Bezug zu Business Rules (`saved_search` als Filter)? | Unverändert; die Validierung sorgt dafür, dass gespeicherte Suchen ausführbar bleiben |

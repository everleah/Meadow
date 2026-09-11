# Suchformular für gespeicherte Suchen (schema-getriebener Query-Builder)

- **Hauptdokument:** Abschnitt 10 ([Gespeicherte Suchen & Collections](../meadow-pim-architektur.md#10-gespeicherte-suchen--materialisierte-collections)) und Abschnitt 11 ([Benutzeroberfläche](../meadow-pim-architektur.md#11-benutzeroberfläche-frontend))
- **Relevante Phasen:** Phase 9 (Ausbau), aufbauend auf Phase 9, Punkt 1
- **Stand:** Plan / Entwurf (2026-09-11, überarbeitet) — **nicht umgesetzt**. Umfang nach Rückmeldung des Auftraggebers: **Stage A + B** (Feldkatalog, Validierung, Formular mit **UND/ODER-Gruppen** und `between`, Referenzen, Sortierung, Zusammenfassung, Vorschau). Die Query-DSL wird dafür **erweitert** (Abschnitt 5); bestehende Suchen werden **nicht** migriert, sondern gelöscht und neu angelegt.

**Getroffene Entscheidungen:**

- **Der Feldkatalog ist Server-Vertrag** (`GET /search-fields`) — nicht Frontend-Wissen. Damit nutzen UI, Validierung und künftige Clients dieselbe Quelle (Muster aus [konnektor-konfiguration.md](konnektor-konfiguration.md)).
- **Die DSL wird erweitert:** `filters` (flache Liste, nur UND) wird durch einen **rekursiven Bedingungsbaum** `where` ersetzt — **UND** und **ODER**, verschachtelt bis Tiefe 4 — plus den neuen Operator **`between`** (zwei Werte). Das ist eine **brechende Änderung**; bestehende Suchen werden gelöscht und neu angelegt (Entscheidung: keine Migration, kein Altbestands-Schalter).
- **Das Formular kann alles, was die DSL kann:** Gruppen anlegen/verschachteln, UND/ODER umschalten, `between` mit zwei Wertfeldern, Referenzen und Sortierung.
- **JSON bleibt** als Expertenmodus erhalten (Tab „JSON“), inklusive Bestandteilen, die das Formular nicht darstellt; nichts wird beim Speichern stillschweigend entfernt.
- **Kein neues Rollenmodell:** Katalog lesen `editor+`/`X-Api-Key` (Metadaten, wie `/attribute-definitions`), Suchen schreiben bleibt `admin`.
- **Regel:** Jedes im Katalog angebotene Feld/Operator-Paar wird vom Kern ausgeführt, und jeder ausgeführte Operator steht im Katalog (sonst ist das Formular Kosmetik).
- **Volltext bleibt global:** Der Index liegt auf `object.attributes`; eine Feld-/Sprachauswahl wird nicht angeboten, weil sie keine Wirkung hätte (eine echte Einschränkung wäre ein Index je Attribut — nicht Teil dieses Vorhabens).

## 1. Ausgangslage & Problem

Die Verwaltungsseite `/saved-searches` (`meadow/frontend/saved_searches.py`, admin-only) legt Suchen in einem Dialog an, dessen Suchkriterien eine **JSON-CodeMirror-Fläche** mit der Query-DSL sind (`filters` / `fulltext` / `references` / `sort` / `start` / `limit`). Ausgeführt wird die DSL im Kern (`execute_query` in `crates/pim-core/src/api/saved_searches.rs`: Feld-Whitelist `type`/`status`/`id`/`attributes.<name>[.<lang>]`, Operatoren `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`, `not_contains`, `not_empty`, `is_null`, `starts_with`, `ends_with`).

Für technisch versierte Anwender ist das ausreichend, für den normalen (nicht-technischen) Admin eine Hürde – dieselbe Ausgangslage wie bei den Konnektoren vor Stage 1:

- **Kein Auffinden der Felder:** Attributnamen, Operatoren und die Punktnotation (`attributes.name.de`) kennt nur, wer die Doku liest. Im Dialog steht nur ein Hinweis-Satz.
- **Keine Feldvalidierung:** Der Kern prüft beim **Anlegen/Ändern nichts** – die Query wird gespeichert, wie sie kommt. Erst die **Ausführung** meldet „unsupported filter field/operator“ (oder liefert stillschweigend 0 Treffer, wenn ein Attributname falsch geschrieben ist, aber syntaktisch erlaubt).
- **Typfehler sind unsichtbar:** `{"field": "attributes.price", "operator": "gt", "value": "teuer"}` wird gespeichert und schlägt erst beim Ausführen fehl; `in` ohne Array liefert keine Treffer statt einer Meldung.
- **Mehrsprachigkeit und Enums:** Für mehrsprachige Attribute muss die Sprachvariante von Hand geschrieben werden; Enum-Werte müssen als **Code** eingetragen werden (Labels kennt das JSON nicht).
- **Doppelpflege:** Operator-Whitelist und Feldregeln stehen im Kern; ein Formular müsste sie sonst im Frontend nachbauen.
- **Kein Zwischenstand:** Es gibt keine Vorschau („so viele Treffer“) – man muss speichern und dann „Ausführen“ drücken.
- **Kein ODER, keine Gruppen:** `filters` ist eine **flache Liste**, die der Kern mit UND verknüpft; „veröffentlicht **oder** freigegeben“ geht nur über `in`, und ein geschachtelter Ausdruck (`(A UND B) ODER C`) ist gar nicht darstellbar.
- **Kein „zwischen“:** Für Spannen (Preis 10–50) braucht es zwei Zeilen (`gte` + `lte`).

**Nicht das Problem:** Die Seite selbst (Liste, Ausführen, Löschen, Collections, Refresh) ist umgesetzt und bleibt unverändert. Es geht ausschließlich um den **Editor** der Suchkriterien.

## 2. Typische Lösungen (Recherche)

- **jQuery QueryBuilder / React Query Builder** (die verbreitetsten Baukästen; beide wurden für diesen Plan angesehen): Ein **Filterkatalog** beschreibt je Feld `id`/`field`, `label`, **`type`** (`string`, `integer`, `double`, `date`, `time`, `datetime`, `boolean`), **`input`** (`text`, `number`, `textarea`, `radio`, `checkbox`, `select`), `values` (Optionslisten), **`operators`** (welche Operatoren für dieses Feld zulässig sind) und `default_operator`. Die **Operatoren** selbst tragen die Eigenschaften `nb_inputs` (**0** z. B. für „ist leer“, **1** für die meisten, **2** für „between“), `multiple` (`true` für `in`/`not_in`) und `apply_to` (Feldtypen, für die sie gelten). Der Builder rendert daraus Zeilen „Feld ▾ · Operator ▾ · Wert“, bietet „+ Regel“, **optional** Gruppen (UND/ODER, per `allow_groups` abschaltbar), eine **Validierung je Regel** (mit Fehler-Icon/Tooltip) und lokalisierte Operator-/Bedingungstexte. Ausgabe ist **strukturiertes JSON** – genau die Form, die unsere DSL schon hat.
- **Segment-/Filter-Builder in PIM-Shops (Akeneo, Shopify)** und **Kibana/Grafana/Airtable**: Einstieg ist eine **flache Filterliste** mit „Bedingung hinzufügen“, einem **Ergebniszähler** und gespeicherten Sichten; komplexe Ausdrücke wandern in einen Expertenmodus (Query-String/JSON). Der Wert eines guten Builders liegt dort vor allem in **Auswahllisten statt Freitext** und **sofortigem Feedback**.
- **Deklaratives Schema → Formular** (n8n-Filterknoten, Home-Assistant-Bedingungen, JSON Schema + `uiSchema`, aus der Konnektor-Recherche): Feldbeschreibung, Anzeige-Widget und Sichtbarkeitsbedingung kommen aus einem Server-Vertrag; das Frontend rendert nur. Das ist die Vorlage für den Feldkatalog hier.
- **Gemeinsames Muster:** (1) Feldkatalog als Single Source of Truth, (2) Zeilen aus Feld/Operator/Wert, (3) **Operator setzt das Wert-Widget** (kein Widget für 0-Input-Operatoren, Mehrfachauswahl für `in`), (4) Validierung vor dem Speichern mit Pfad/Meldung, (5) Ergebnis-Vorschau, (6) **JSON als Rückfall**, (7) Gruppen/ODER nur, wenn die Datenstruktur sie kann.

**Konsequenz für Meadow:** Feldkatalog + Validierung in den Kern, **rekursiver Gruppen-Editor** im Frontend (UND/ODER, `between`), JSON-Tab als Rückfall. Genau die Arbeitsteilung des Konnektor-Formulars, nur mit einer **Baumstruktur** statt einer Feldliste. Die DSL muss dafür Gruppen und `between` lernen (Abschnitt 5).

## 3. Zielbild

```
Suche bearbeiten                                              [ Formular | JSON ]
Name      [ Aktive Produkte mit Preisfehler        ]   Cron (optional) [ 0 0 2 * * * ]

▾ Bedingungen (oberste Gruppe: UND)
   [ UND ▾ ]   [ + Bedingung ] [ + Gruppe ]
     [ Objekttyp ▾ ] [ ist ▾ ]       [ product      ▾ ]                       ✕
     [ Preis     ▾ ] [ zwischen ▾ ]  [ 10 ] – [ 50 ]                          ✕
     ▾ [ ODER ▾ ] (Untergruppe)     [ + Bedingung ] [ + Gruppe ]         ✕
         [ Status ▾ ] [ ist ▾ ]     [ published  ▾ ]                       ✕
         [ Status ▾ ] [ ist ▾ ]     [ approved   ▾ ]                       ✕

▾ Volltext
   [ Edelstahl                                            ]   ← durchsucht alle Attributwerte

▾ Grenzen
   Treffer-Limit [ 500 ]      Überspringen [ 0 ]

▾ Vorschau
   „Aktuell 42 Treffer“  [ Vorschau aktualisieren ]           ← nur Anzeige, speichert nichts
```

- **Gruppen sind verschachtelbar:** jede Gruppe hat einen Operator (**UND**/**ODER**) und enthält Bedingungen **und** weitere Gruppen; die oberste Gruppe ist die Wurzel des Baums (`where`).
- **Feldauswahl** ist ein gruppiertes Select (Objektfelder · Attribute, alphabetisch nach Label).
- **Operatorauswahl** hängt am Feld (`operators` aus dem Katalog), **Wert-Widget** am Feldtyp **und** am Operator: kein Feld bei `arity: 0`, **zwei** Felder bei `between`, Mehrfachauswahl bei `multi`.
- **Mehrsprachige Attribute** bekommen zusätzlich ein Sprach-Select (aktive Datensprachen) – daraus entsteht `attributes.<name>.<lang>`; ohne Sprachwahl gilt das Basisfeld.
- **Enum-Attribute** zeigen ihre **Labels** (mehrsprachig, aus dem Enum-Set) und speichern die **Codes**.
- **Referenzen** sind eine eigene Zeilenliste (Relationstyp + Zielobjekt über den Objekt-Picker + Richtung; `direction` „incoming“ = das Ziel ist der Container).
- **Sortierung** ist eine Zeilenliste aus sortierbarem Feld + Richtung.

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
    { "type": "between", "label_key": "search_form.op_between", "arity": 2, "multi": false },
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
      "operators": ["eq", "ne", "gt", "gte", "lt", "lte", "between", "not_empty", "is_null"],
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

**Operatorattribute:** `type` (DSL-Code), `label_key`, `arity` (**0** = kein Wert, **1** = ein Wert, **2** = zwei Werte bei `between`), `multi` (Wert ist eine Liste). Das steuert das Widget: `arity: 0` blendet den Wert aus, `arity: 2` zeigt zwei Felder, `multi: true` macht eine Mehrfachauswahl daraus.

**Gruppenoperatoren:** `and` · `or` — sie stehen nicht in der Operatortabelle, sondern im Gruppenknoten (`op`). Die UI bietet sie als Select in jeder Gruppe an.

**Herkunft der Daten:** `attribute_definition` (Name, `label`, Datentyp, `multilingual`, `enum_set_id`, `unit_id`, `computed`), `object_type` (Typ-Optionen), **Status** (distinct `status`-Werte aus `object`, bekannte Werte mit i18n-Key), `enum_set`/`enum_option` (Optionsliste mit Labels), `language_tree` (aktive Datensprachen), `relation_definition` (Referenzen mit Rolle/Zieltypen).

**Wo der Katalog lebt:** neues Modul `src/api/search_schema.rs`, ausgeliefert über `search.rs`/`saved_searches.rs`. Verworfen: **Katalog nur im Frontend** (doppelte Pflege, keine Servervalidierung, MCP/CLI ohne Schema) und **freies JSON-Schema** (unsere DSL ist kein JSON Schema; Operatoren/Widget-Zuordnung wäre umständlicher).

## 5. DSL-Erweiterung, Validierung & Schreib-Semantik

**Die DSL bekommt einen Bedingungsbaum.** Statt der flachen Liste `filters` gibt es `where`:

```json
{
  "where": {
    "op": "and",
    "conditions": [
      { "field": "type", "operator": "eq", "value": "product" },
      { "field": "attributes.price", "operator": "between", "value": [10, 50] },
      { "op": "or", "conditions": [
        { "field": "status", "operator": "eq", "value": "published" },
        { "field": "status", "operator": "eq", "value": "approved" }
      ]}
    ]
  },
  "fulltext": { "query": "Edelstahl" },
  "references": [{ "type": "category_member", "out": "object:xyz", "direction": "incoming" }],
  "sort": [{ "field": "attributes.name.de", "order": "asc" }],
  "start": 0,
  "limit": 1000
}
```

- **Ein Knoten ist entweder Gruppe oder Bedingung:** `{op, conditions[]}` bzw. `{field, operator, value}`. Fehlt `op`, gilt UND; erlaubt sind `and`/`or`.
- **Übersetzung nach SurrealQL:** Gruppe → `(…)` mit `AND`/`OR` zwischen den Kindern, Bedingung → Klausel. Parameter werden **rekursiv** durchnummeriert (`f0`, `f1`, …), damit Untergruppen nicht mit der äußeren Gruppe kollidieren.
- **`between`** → `field >= $a AND field <= $b` (zwei Parameter).
- **Grenzen:** Tiefe ≤ 4, Bedingungen (Blätter) ≤ 50, leere Gruppe → Fehler. Damit bleibt die erzeugte Abfrage beherrschbar.
- **Operator-Aliase** (`neq`, `ge`, `le`, `is_empty`) werden beim Normalisieren auf die kanonischen Namen gezogen (`ne`, `gte`, `lte`, `is_null`), damit gespeicherte Queries einheitlich sind.
- **Keine Migration:** `filters` entfällt; vorhandene gespeicherte Suchen werden gelöscht und neu angelegt (Entscheidung des Auftraggebers). Collections hängen an Suchen und werden zusammen mit ihnen neu angelegt.

**Der Kern validiert die Query** bei `POST /saved-searches`, `PATCH /saved-searches/{id}`, `POST /saved-searches/preview` und beim Ausführen: 

- **Struktur:** Gruppe ohne `conditions`, Gruppe **mit** `field`, Bedingung **mit** `conditions`, Tiefe > 4, mehr als 50 Bedingungen → `400` mit Pfad (z. B. `query.where.conditions[2]: group requires a non-empty conditions list`).
- **Feld:** muss im Katalog stehen (Systemfeld, existierendes Attribut, ggf. mit aktiver Sprache). Unbekannt → `query.where.conditions[1].field: unknown field attributes.preis_alt`.
- **Operator:** muss für dieses Feld erlaubt sein → `query.where.conditions[1].operator: gt is not allowed for attributes.brand (use eq, ne, in, not_in)`.
- **Wert:** `arity: 0` verlangt **keinen** Wert, `arity: 1` einen, `arity: 2` (`between`) genau **zwei**; `multi: true` verlangt eine **Liste**, sonst einen Skalar. Typfehler werden gemeldet (`must be a number`, `must be a list of two values`); Enum-Werte werden gegen die Optionsliste geprüft (unbekannter Code → UI-Warnung, der Kern akzeptiert ihn, siehe Abschnitt 11).
- **Sortierung:** nur `sortable`-Felder, `order` ∈ `asc`/`desc`; **`limit`** 1–10000, **`start`** ≥ 0 (der Ausführer klemmt heute stillschweigend – künftig meldet der Kern).
- **Mehrere Fehler** werden gesammelt (`; `-getrennt, dedupliziert) wie bei den Konnektoren; `ApiError` bleibt unverändert einfach.
- **Normierung beim Schreiben:** Der Kern speichert die **kanonische** Form (Operator-Aliase aufgelöst, `op` gesetzt, leere optionale Felder weggelassen) — Formular und JSON-Tab sehen nach dem Speichern dasselbe.

## 6. Frontend

**Neues Modul `meadow/frontend/search_form.py`** (analog zu `connector_form.py`), reine, testbare Helfer zuerst:

- **Baum-Abbildung:** `tree_to_form(where, catalog)` / `form_to_tree(state, catalog)` – rekursiv, inklusive Sprachauswahl (`attributes.name` + Sprache `de` → `attributes.name.de`), Enum-Label ↔ Code und `between` (zwei Werte).
- **Abhängigkeiten:** `operators_for(field_key, catalog)`, `default_operator(field_key, catalog)`, `arity(operator)`, `is_multi(operator)`, `value_widget_kind(field, operator)`.
- **Baum-Helfer:** `new_leaf(catalog)`, `new_group(op)`, `remove_at(path, tree)`, `max_depth(tree)`, `count_leaves(tree)`.
- **Validierung vorab** (Spiegel des Kerns, gleiche Meldungen wie das Konnektor-Formular): `validate_tree(tree, catalog, locale)` → Liste aus `{path, message}`.
- **Gesamt-Query:** `query_from_state(state, catalog)` / `state_from_query(query, catalog)` für `where`/`fulltext`/`references`/`sort`/`start`/`limit`.
- **Hinweis auf nicht dargestellte Bestandteile:** `unsupported_parts(query, catalog)` (unbekannte Schlüssel, nicht auflösbare Felder, `fulltext.fields`/`language`) → Liste mit Zähler im Dialog.
- **Zusammenfassung** für die Listenspalte: `summary_text(query, catalog, locale)` – z. B. `3 Bedingungen · 1 ODER-Gruppe · Volltext „Edelstahl“ · 500 Treffer`.

**UI-Bausteine:** `ConditionGroupEditor` (rekursiv: Kopfzeile mit UND/ODER-Select, „+ Bedingung“, „+ Gruppe“ – nur unterhalb der Maximaltiefe – und ✕; darunter Bedingungszeilen und Untergruppen), `ConditionRow` (Feld-Select gruppiert, Operator-Select mit i18n-Labels, Wert-Widget je Typ/Operator, Sprach-Select bei mehrsprachigen Feldern, ✕), `ReferenceRows`, `SortRows`, Volltext-Eingabe, `limit`/`start`, **Vorschau** (Abschnitt 9) und die Tabs **Formular/JSON**.

**Wiederverwendung:** `object_picker.open_object_picker` für Referenzziele (inkl. `target_types` aus dem Katalog), die Enum-Auflösung wie im Grid (`enum_code_label_map`), das Tab-/Baseline-Muster des Konnektor-Dialogs („nicht dargestellt“-Hinweis; die Query wird als Ganzes gespeichert, es gibt kein `parameters_mode`-Analogon).

**i18n:** neue Keys `search_form.*` (Operatoren, Gruppentitel, Hinweise, „Bedingung hinzufügen“, „Gruppe hinzufügen“, Validierungsmeldungen, Spaltentitel) sowie ein Label-Key für die Listenspalte; Attribut- und Enum-Labels sind **Daten**, keine i18n-Keys. Die Statusliste kommt künftig aus dem Katalog (die Duplizierung `_STATUS_OPTIONS` in `form.py` entfällt perspektivisch).

## 7. Bewusste Einschränkungen

1. **Gruppen nur bis Tiefe 4 und 50 Bedingungen.** Ein unbegrenzter Ausdrucksbaum wäre im Formular nicht mehr bedienbar; die Grenzen gelten in Kern und UI („+ Gruppe“ verschwindet an der Maximaltiefe).
2. **Kein `not_between`, kein `in` auf Objektwerten.** Es bleibt bei der Operatormenge aus Abschnitt 4.
3. **Volltext ohne Feld-/Sprachauswahl.** Der Index liegt auf `object.attributes`; die UI verspricht keine Einschränkung, die nichts bewirkt.
4. **Wert-Widgets nur für die vorhandenen Werttypen** (Text, Zahl, boolesch, Datum, Enum). `reference`- und Medien-Attribute sind **nicht** filterbar – dafür gibt es die Referenz-Zeilen (Relationstyp + Zielobjekt).
5. **Kein Ersatz des JSON-Tabs.** Alles, was das Formular nicht darstellt, bleibt dort möglich und wird im Formular als „nicht dargestellt“ gemeldet.
6. **Keine Migration.** Bestehende gespeicherte Suchen (und ihre Collections) werden gelöscht und im Formular neu angelegt.

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

**Stage A — DSL, Katalog, Validierung, Formular (MVP):**

1. **DSL-Erweiterung:** `where`-Baum (`QueryCondition` mit `op`/`conditions`/`field`/`operator`/`value`), Operatortabelle inkl. `between`, **rekursive** SurrealQL-Übersetzung mit durchgezählten Parametern, Operator-Normalisierung; `filters` entfällt.
2. **Kern-Katalog & Validierung:** neues Modul `src/api/search_schema.rs` (Feldkatalog aus `attribute_definition`/`object_type`/`object`-Status/`enum_set`/`language_tree`/`relation_definition`, Operatortabelle mit `arity`/`multi`, `validate(query, catalog)` mit Pfadmeldungen, `catalog_document()`), Endpunkt `GET /search-fields` (`editor+`/`X-Api-Key`), Validierung in `create_saved_search`/`update_saved_search`/`execute`; Unit-Tests für Übersetzung, Katalog und Validator.
3. **Frontend:** `search_form.py` (Helfer + **rekursiver Gruppen-Editor** + Tabs Formular/JSON, „nicht dargestellt“-Hinweis, Zusammenfassungsspalte), Dialog in `saved_searches.py` umbauen, `api.py` um `list_search_fields` erweitern, i18n-Keys in en/de/fr.
4. **Vorschau:** `POST /saved-searches/preview` (Body = Query, `editor+`) → `{count, ids}`; Button „Vorschau“ mit Debounce, Anzeige „Aktuell N Treffer“ (rein lesend, speichert nichts).

**Stage B — Referenzen, Sortierung, Feinschliff:**

5. `references` (Relationstyp + Zielobjekt über den Objekt-Picker + Richtung) und `sort` (Feld + Richtung) im Formular; `start` im Formular; Spalte „Zusammenfassung“ in der Liste; bestehende Suchen/Collections löschen und neu anlegen.

**Stage C — nur bei Bedarf (nicht Teil dieses Vorhabens):**

6. Tiefere Ausdrücke (Tiefe > 4), weitere Operatoren (`not_between`), Volltext je Feld (Index je Attribut), Übernahme desselben Katalogs in eine **erweiterte Suche im Grid** (`/products`).

**Nicht-Ziele:** kein Ersatz des JSON-Expertenmodus, keine Migration bestehender Suchen, keine neuen Rollen, keine Änderung an Collections/Refresh, kein Query-Builder für die Middleware-Schnittstellen.

## 10. Testfälle

- `GET /search-fields` liefert Systemfelder, Attributfelder mit Typ/Operatoren/`sortable`, Enum-Optionen mit Labels und die Operatortabelle mit `arity`/`multi`.
- `POST /saved-searches` mit unbekanntem Feld → `400` mit `query.where.conditions[0].field`; mit Operator, der nicht zum Feld passt → `400` mit Hinweis auf die erlaubten; `in` ohne Liste → `400`; `between` mit einem statt zwei Werten → `400`; `gt` auf einem Enum-Feld → `400`; leere Gruppe → `400`; Tiefe 5 → `400`; `limit: 99999` → `400`; gültige Query → `201`.
- Übersetzung: `(A UND B) ODER C` ergibt genau diese Klammerung; `between` erzeugt zwei Parameter; Untergruppen kollidieren nicht mit den Parametern der äußeren Gruppe.
- Formular-Roundtrip: Baum → DSL → Baum ist verlustfrei (mehrsprachiges Feld, Enum-Code, `between`, `not_empty` ohne Wert, verschachtelte Gruppen).
- Operatorabhängiges Widget: `not_empty` blendet den Wert aus, `in` bietet Mehrfachauswahl, `between` zeigt zwei Felder, `gt` ein Zahlenfeld.
- JSON-Tab → Formular: nicht darstellbare Bestandteile werden gezählt und angezeigt, **nicht** verworfen; Speichern aus dem Formular schickt genau die dargestellte Query plus die unveränderten Bestandteile.
- Vorschau: Query ohne Speichern → Trefferzahl; Vorschau schreibt nichts (kein Objekt, keine Collection, kein Event).
- Rechte: `editor` liest den Katalog, schreibt aber **keine** Suche (`403`); `viewer` → `403` für den Katalog.
- Locale-Parität für die neuen Keys.

## 11. Offene Punkte

| Frage | Vorschlag |
|---|---|
| Unbekannte Enum-Codes im Wert? | **Warnung in der UI statt Fehler** – Codes können aus alten Enum-Sets stammen; der Ausführer vergleicht nur Codes, der Kern akzeptiert sie |
| Vorschau-Endpunkt oder Speichern zum Testen? | **Preview-Endpunkt** (rein lesend) – sonst hinterlässt jeder Testversuch eine Suche |
| Ist die Zählung in der Vorschau teuer? | Vorschau mit Debounce (~800 ms), `limit`-Obergrenze, Ergebnis-IDs gekürzt; bei Bedarf später „nur zählen“ (COUNT statt ID-Liste) |
| Standardgruppe beim Anlegen? | Eine leere **UND**-Gruppe mit einem vorgefüllten Blatt (`type = product`) wie heute im `DEFAULT_QUERY` |
| Denselben Katalog auch für die erweiterte Suche im Grid? | **Ja, mittelfristig** – derselbe Vertrag, dasselbe Formular; im Grid ist es ein Filter, in `/saved-searches` ein gespeicherter Ausdruck |
| Bezug zu Business Rules (`saved_search` als Filter)? | Unverändert; die Validierung sorgt dafür, dass gespeicherte Suchen ausführbar bleiben |
| Collections bei der DSL-Umstellung? | Zusammen mit den Suchen löschen und neu anlegen (siehe Abschnitt 5) |

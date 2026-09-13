# Gespeicherte Suchen & Materialisierte Collections

- **Hauptdokument:** Abschnitt 10 ([Gespeicherte Suchen & Collections](../meadow-pim-architektur.md#10-gespeicherte-suchen--materialisierte-collections))
- **Relevante Phasen:** Phase 9
- **Stand:** Umgesetzt (Phase 9, Punkt 1). `saved_search`- und `collection`-Objekte werden als generische Objekte (`object`-Tabelle) gespeichert; die Query-DSL (`where`/`fulltext`/`references`/`sort`/`limit`) wird im Rust-Kern ausgeführt (Endpoint `POST /saved-searches/{id}/execute`, liefert `{count, ids}`). CRUD-Endpunkte für Suchen (`GET/POST /saved-searches`, `GET/PATCH/DELETE /saved-searches/{id}`) und Collections (`GET/POST /collections`, `GET/PATCH/DELETE /collections/{id}`) sowie `POST /collections/{id}/refresh` (Sync-Refresh der `collection_member`-Relationen mit `rank`/`added_at`) und `GET /collections/{id}/members`. Ein nicht-leeres `refresh_interval` der Collection synchronisiert automatisch eine `collection_refresh`-Jobdefinition (Scheduler/Worker). Die Frontend-Verwaltungsseite `/saved-searches` (Tabs „Gespeicherte Suchen“/„Collections“, admin-only) ist umgesetzt. **Editor (umgesetzt, Stages A–C):** Die Suchkriterien werden in einem **schema-getriebenen Formular** (Tabs „Formular“/„JSON“) gepflegt — Feldkatalog, UND/ODER-Gruppen, `between`/`not_between`, Referenzen, Sortierung, Grenzen, Vorschau-Zähler. **Dieselbe DSL** filtert als erweiterte Suche das Grid (`GET /objects?where=…`, Dialog + Chip im Grid); Details: [suche-formular.md](suche-formular.md).

Meadow PIM sieht die Möglichkeit vor, komplexe Suchanfragen zu speichern und deren Ergebnisse als refreshbare Collections zu materialisieren. Dies ist nicht Teil der ersten Version, wird aber von Anfang an im Datenmodell und in der Architektur berücksichtigt.

## Gespeicherte Suchen (Advanced Search)

Eine **gespeicherte Suche** ist ein Objekt vom Typ `saved_search`, das die Kriterien einer komplexen Suche in strukturierter Form enthält. Sie kann jederzeit erneut ausgeführt werden und dient als Grundlage für Business Rules, Exporte und die Materialisierung.

**Beispielobjekt:**

```json
{
  "id": "search_001",
  "type": "saved_search",
  "attributes": {
    "name": "Aktive Produkte mit Preisfehler",
    "query": {
      "where": {
        "op": "and",
        "conditions": [
          { "field": "type", "operator": "eq", "value": "product" },
          { "field": "attributes.name.de", "operator": "not_empty", "value": null },
          { "op": "or", "conditions": [
            { "field": "status", "operator": "eq", "value": "published" },
            { "field": "attributes.gross_price", "operator": "between", "value": [0, 500] }
          ]}
        ]
      },
      "fulltext": {
        "fields": ["attributes.description.*"],
        "query": "Edelstahl",
        "language": "de"
      },
      "references": [
        {
          "type": "category_member",
          "out": "object:category_5",
          "direction": "incoming"
        }
      ],
      "sort": [
        { "field": "attributes.price", "order": "asc" }
      ],
      "limit": 500
    },
    "refresh_interval": "0 0 2 * * *",   // Cron (6 Felder, Sekunden zuerst)
    "last_executed_at": "...",
    "last_result_count": 42
  },
  "status": "active"
}
```

**Suchkriterien (Query-DSL):**

- **`where`**: Ein **rekursiver Bedingungsbaum** — eine **Gruppe** ist `{op: "and" | "or", conditions: [...]}`, eine **Bedingung** ist `{field, operator, value}` (beide Formen sind nicht mischbar). Fehlt `op`, gilt UND; verschachtelt wird bis **Tiefe 6**, höchstens 50 Bedingungen. Ersetzt die frühere flache Liste `filters` (brechende Änderung, keine Migration).
- **Felder**: `type`, `status`, `id`, `created_at`, `updated_at`, `source` sowie `attributes.<name>[.<lang>]` (Sprachvariante mehrsprachiger Attribute).
- **Operatoren**: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, **`between`**/**`not_between`** (zwei Werte), `in`/`not_in` (Listen), `contains`/`not_contains`, `starts_with`/`ends_with`, `not_empty`, `is_null` — je Feld ist nur eine Teilmenge erlaubt (Feldkatalog, `GET /search-fields`). **Fehlende Werte:** Ordnungs- und Negativ-Operatoren treffen nur Objekte **mit** Wert; „hat keinen Wert“ prüft `is_null`.
- **`fulltext`**: Volltextsuche; der Index liegt auf dem **gesamten** Attributobjekt (`attributes @@ $q`). `fields`/`language` werden angenommen, aber **nicht** ausgewertet (feldweiser Volltext = zukünftige Entwicklung, [suche-formular.md](suche-formular.md) §12).
- **`references`**: Einschränkung über Relationen, z. B. nur Objekte, die in einer bestimmten Kategorie hängen oder eine bestimmte Vererbungsquelle haben.
- **`sort`**, **`start`** und **`limit`**: Sortierung (nur sortierbare Felder), Offset und Begrenzung der Ergebnisliste (1–10000).
- **Validierung:** `POST`/`PATCH /saved-searches`, `POST /saved-searches/preview`, das Ausführen **und der Grid-Filter `GET /objects?where=…`** prüfen die Query gegen den Feldkatalog und melden Fehler mit Pfad im Baum (`query.where.conditions[1].operator: …`); gespeichert wird die **kanonische** Form. Der Grid-Filter verbindet sich per UND mit den übrigen Listenfiltern. Detail: [suche-formular.md](suche-formular.md).
- **Referenzdaten (umgesetzt 2026-09-13):** Ein Feldkatalog-Eintrag trägt `reference_data` (aus `attribute_definition.reference_data`). Das Suchformular bietet diese Felder erst nach dem Schalter **„Standard-Daten in der Feldliste (Anzahl)“** an — der Kern liefert sie aber **weiterhin filterbar** aus, damit eine gespeicherte Suche mit einem Standard-Merkmal gültig bleibt und ihr Feld sichtbar bleibt ([kategorisierung.md](kategorisierung.md)).

**Ausführung:**

- Gespeicherte Suchen werden über den Rust-Kern ausgeführt (synchron) oder als Job über den Scheduler (asynchron).
- Die Ergebnisse können als Liste von Objekt-IDs zurückgegeben werden.
- Business Rules können eine `saved_search` als Filter verwenden, z. B. „Wende Preisregel auf alle Objekte aus Suche X an“.

## Materialisierte Collections

Eine **Collection** ist ein eigenständiges Objekt (z. B. `type: "collection"`), das die Ergebnisliste einer gespeicherten Suche als relationierte Objektmenge abbildet. Dadurch wird das Suchergebnis zu einem **refreshbaren Cache**, der wie eine normale Gruppe/Kategorie verwendet werden kann.

**Modellierung:**

- Das Collection-Objekt besitzt eine Referenz auf die zugrunde liegende `saved_search`.
- Die Ergebnisse werden über Relationen des Typs `collection_member` mit dem Collection-Objekt verbunden. Analog zu `category_member` zeigt die Relation vom Mitgliedsobjekt (`in`) auf das Container-Objekt (`out`). Die Mitgliedschaft ist die **Verwendung/Rolle `collection`** eines Relationstyps (`relation_definition.role`, siehe Abschnitt „Verwendung von Relationstypen“ in [relationen.md](relationen.md)) – sie erzwingt den Zieltyp `collection` und die Kanten-Metadaten `rank`/`added_at`:

  ```json
  {
    "id": "reference:01M0…",
    "in": "object:01M0…",
    "out": "collection_10",
    "type": "collection_member",
    "attributes": {
      "rank": 1,
      "added_at": "2026-08-14T10:00:00Z"
    }
  }
  ```

- Die Relationen enthalten Metadaten (z. B. Position/Rank), sodass die Collection wie eine sortierte Liste verwendet werden kann.

**Refresh-Mechanismus:**

- Beim Refresh (manuell oder geplant über `refresh_interval`) werden die bisherigen `collection_member`-Relationen der Collection entfernt und durch die aktuellen Treffer der Suche ersetzt.
- Der Refresh läuft als Hintergrundjob im Python-Worker oder direkt im Rust-Kern, um die Oberfläche nicht zu blockieren.
- Während des Refreshs bleibt die alte Collection konsistent lesbar; nach Abschluss wird atomar auf die neue Ergebnisliste umgestellt.

**Verwendung materialisierter Collections:**

- Als **vorgefilterte Arbeitslisten** in der Oberfläche, z. B. „Preisfehler“, „Neuheiten“, „Unvollständige Produkte“.
- Als **Basis für Outbound-Exporte**, ohne bei jedem Export die Suche neu ausführen zu müssen.
- Als **Kategorien für die Middleware** – die Middleware erhält nur noch die Objekt-IDs der Collection und kann gezielt abgleichen.
- **Collections im Grid (umgesetzt, Phase 9):** Im Produkt-Grid (`/products`) gibt es neben „Struktur“ und „Kategorien“ eine **„Collections“-Ansicht** (flach, kein Baum): eine Liste der Collections (mit Mitgliederzahl); Auswahl filtert das Grid über `GET /objects?collection=<id>` (Backend-Filter über `collection_member`, analog zu `?category=` über `category_member`). Die Materialisierung bleibt die Quelle der Mitglieder; die Grid-Ansicht ist eine reine Anzeige-/Filterhilfe.

**Abgrenzung zu normalen Kategorien:**

- Kategorien werden manuell oder über feste Regeln befüllt; Collections basieren auf dynamischen Suchkriterien.
- Collections können selbst Teil von Kategorien, Vererbungsstrukturen oder weiteren Suchen sein, da sie normale Objekte sind.

**Vorbereitung in der ersten Version:**

- Das Datenmodell für `saved_search` und `collection` sowie die Relationstypen werden bereits vorgesehen.
- Die Query-DSL wird konzeptionell definiert, auch wenn die vollständige Advanced Search erst später umgesetzt wird.
- Inbound-/Outbound-Konnektoren und Eventsystem berücksichtigen von Anfang an, dass Objekte vom Typ `saved_search` oder `collection` existieren können.

**Performance-Aspekte:**

- Für sehr große Ergebnismengen kann der Refresh der Collection in Batches erfolgen, um die Datenbanklast zu begrenzen.
- Indizes auf `collection_member.in` und `collection_member.out` sorgen für schnelle Abfragen.
- Optional werden die Collection-Relationen in einem separaten Index gehalten, um die Suche nicht durch unnötige Relationen zu belasten.

**Ausbau (Phase 9):** Die Collection-Mitglieder sollen sich **UI-geführt exportieren** (Quelle „Collection“ im geplanten Export-Wizard, Schnell-Button „Als CSV/JSON exportieren“) und **bulk-importieren** lassen (Mitglieder aus Grid-Auswahl/Import-Datei als `collection_member`-Relationen, idempotent). Details: [datenexport-import-ui.md](datenexport-import-ui.md).

# Gespeicherte Suchen & Materialisierte Collections

- **Hauptdokument:** Abschnitt 10 ([Gespeicherte Suchen & Collections](../meadow-pim-architektur.md#10-gespeicherte-suchen--materialisierte-collections))
- **Relevante Phasen:** Phase 9
- **Stand:** Umgesetzt (Phase 9, Punkt 1). `saved_search`- und `collection`-Objekte werden als generische Objekte (`object`-Tabelle) gespeichert; die Query-DSL (`filters`/`fulltext`/`references`/`sort`/`limit`) wird im Rust-Kern ausgeführt (Endpoint `POST /saved-searches/{id}/execute`, liefert `{count, ids}`). CRUD-Endpunkte für Suchen (`GET/POST /saved-searches`, `GET/PATCH/DELETE /saved-searches/{id}`) und Collections (`GET/POST /collections`, `GET/PATCH/DELETE /collections/{id}`) sowie `POST /collections/{id}/refresh` (Sync-Refresh der `collection_member`-Relationen mit `rank`/`added_at`) und `GET /collections/{id}/members`. Ein nicht-leeres `refresh_interval` der Collection synchronisiert automatisch eine `collection_refresh`-Jobdefinition (Scheduler/Worker). Die Frontend-Verwaltungsseite `/saved-searches` (Tabs „Gespeicherte Suchen“/„Collections“, admin-only) ist umgesetzt.

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
      "filters": [
        { "field": "type", "operator": "eq", "value": "product" },
        { "field": "status", "operator": "in", "value": ["published", "approved"] },
        { "field": "attributes.price", "operator": "lt", "value": 0 },
        { "field": "attributes.name.de", "operator": "not_empty", "value": null }
      ],
      "fulltext": {
        "fields": ["attributes.description.*"],
        "query": "Edelstahl",
        "language": "de"
      },
      "references": [
        {
          "type": "category_member",
          "out": "category_5",
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

- **`filters`**: Beliebige Bedingungen auf Objektfelder und Attributwerte, inkl. Vergleichsoperatoren (`eq`, `ne`, `gt`, `lt`, `in`, `contains`, `not_empty`, …).
- **`fulltext`**: Volltextsuche über sprachabhängige Attributwerte, mit optionaler Sprachauswahl gemäß Sprachbaum.
- **`references`**: Einschränkung über Relationen, z. B. nur Objekte, die in einer bestimmten Kategorie hängen oder eine bestimmte Vererbungsquelle haben.
- **`sort`** und **`limit`**: Sortierung und Begrenzung der Ergebnisliste.

**Ausführung:**

- Gespeicherte Suchen werden über den Rust-Kern ausgeführt (synchron) oder als Job über den Scheduler (asynchron).
- Die Ergebnisse können als Liste von Objekt-IDs zurückgegeben werden.
- Business Rules können eine `saved_search` als Filter verwenden, z. B. „Wende Preisregel auf alle Objekte aus Suche X an“.

## Materialisierte Collections

Eine **Collection** ist ein eigenständiges Objekt (z. B. `type: "collection"`), das die Ergebnisliste einer gespeicherten Suche als relationierte Objektmenge abbildet. Dadurch wird das Suchergebnis zu einem **refreshbaren Cache**, der wie eine normale Gruppe/Kategorie verwendet werden kann.

**Modellierung:**

- Das Collection-Objekt besitzt eine Referenz auf die zugrunde liegende `saved_search`.
- Die Ergebnisse werden über Relationen des Typs `collection_member` mit dem Collection-Objekt verbunden. Analog zu `category_member` zeigt die Relation vom Mitgliedsobjekt (`in`) auf das Container-Objekt (`out`):

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

# Sprachbaum & Mehrsprachigkeit der Daten

- **Hauptdokument:** Abschnitt 4 ([Sprachbaum für sprachabhängige Daten](../meadow-pim-architektur.md#4-datenmodell))
- **Relevante Phasen:** Phase 1–2, 7
- **Stand:** Umgesetzt – Sprachbaum-Administration (`meadow/frontend/languages.py`, `/languages`, admin/superadmin) mit `active`/`is_ui`-Steuerung (siehe §„Verwaltung & Aktivierung“)

Dieses Dokument beschreibt die Mehrsprachigkeit der **fachlichen Daten** (Sprachbaum). Die Mehrsprachigkeit der **Benutzeroberfläche** ist separat im Dokument [frontend.md](frontend.md) beschrieben.

## Sprachbaum für sprachabhängige Daten

Für sprachabhängige Attributwerte (z. B. `name`, `description`) wird ein **hierarchischer Sprachbaum** verwendet. Er bildet sowohl Sprachen als auch regionale Varianten ab und erlaubt die **Vererbung von Werten** von allgemeineren Knoten zu spezifischeren.

**Aufbau des Sprachbaums:**

- **Wurzel (`root`)**: Der globale Ausgangspunkt, z. B. `root` oder ein universeller Fallback.
- **Sprachknoten (ISO 639-1)**: z. B. `en`, `de`, `fr`.
- **Regionale Varianten (ISO 3166-1 Alpha-2)**: z. B. `en-GB`, `en-US`, `de-DE`, `de-AT`, `fr-FR`, `fr-CA`.

**Hierarchie:**

```text
root
├── en
│   ├── en-GB
│   └── en-US
├── de
│   ├── de-DE
│   └── de-AT
└── fr
    ├── fr-FR
    └── fr-CA
```

**Vererbungsregeln:**

1. Wenn ein Attributwert für den exakten Sprachcode (z. B. `en-US`) vorhanden ist, wird dieser verwendet.
2. Ist kein Wert für den exakten Code gesetzt, wird im **direkten Elternknoten** (z. B. `en`) nachgesehen.
3. Ist auch dort kein Wert vorhanden, wird weiter bis zur Wurzel (`root`) aufgestiegen.
4. Erst wenn auch die Wurzel keinen Wert enthält, gilt der Wert als **nicht vorhanden** (null).

**Beispiel:**

- Attribut `name` hat Werte:

  ```json
  {
    "root": "Produkt",
    "en": "Product",
    "en-GB": "Product (UK)",
    "de": "Produkt"
  }
  ```

- Anfrage mit Sprache `en-US` → kein exakter Treffer → erbt von `en` → ergibt `"Product"`.
- Anfrage mit Sprache `en-GB` → exakter Treffer → ergibt `"Product (UK)"`.
- Anfrage mit Sprache `fr-FR` → kein Treffer unter `fr` → erbt von `root` → ergibt `"Produkt"`.

**Datenhaltung in SurrealDB:**

- Die sprachabhängigen Werte werden weiterhin als **JSON-Objekt im `attributes`-Feld** des Objekts gespeichert, wobei die Schlüssel den Knoten des Sprachbaums entsprechen (z. B. `"name": { "root": "...", "en": "...", "en-GB": "..." }`).
- Die Struktur des Sprachbaums selbst wird in einer eigenen Tabelle `language_tree` abgelegt, um die Vererbung flexibel konfigurieren zu können:

  ```json
  {
    "id": "lang_en-GB",
    "code": "en-GB",
    "parent": "lang_en",
    "label": { "en": "English (United Kingdom)", "de": "Englisch (UK)" },
    "fallback_order": ["en", "root"]
  }
  ```

- Der `fallback_order` wird beim Anlegen/Ändern des Baums berechnet und gespeichert, um die Auflösung zur Lesezeit zu beschleunigen (kein rekursives Traversieren in Echtzeit).

**Auflösung in der API:**

- Beim Lesen eines Objekts kann über den Query-Parameter `?locale=en-US` die gewünschte Sprache angegeben werden.
- Der Rust-Kern (oder vorgelagerte Python-Service) löst die sprachabhängigen Felder anhand des Sprachbaums auf und liefert die **effektiven Werte** im Antwort-JSON zurück.
- In der Standardausgabe bleibt das vollständige Sprachobjekt erhalten (optional), damit alle Varianten sichtbar bleiben. Für die UI und Exporte wird die aufgelöste Variante verwendet.

**Behandlung von Änderungen:**

- Wird ein Wert in einem übergeordneten Knoten geändert (z. B. `en`), erben alle untergeordneten Knoten ohne eigenen Wert automatisch die Änderung.
- Wird ein Wert in einem spezifischen Knoten gesetzt, überschreibt er den geerbten Wert für diesen Knoten.
- Das Löschen eines Wertes in einem Knoten entfernt nur diesen expliziten Eintrag – die Vererbung greift danach wieder für alle Kindknoten ohne eigenen Wert.

**Performance-Aspekte:**

- Durch die gespeicherte `fallback_order` ist die Auflösung O(depth) mit sehr geringer Tiefe (max. 3–4 Ebenen). Dies ist vernachlässigbar gegenüber der Datenbankabfrage.
- Die `fallback_order`-Ketten und die Liste der `multilingual`-Attributnamen hält der Kern in einem **In-Process-Cache** (Invalidierung bei Sprachbaum- bzw. Attributdefinitions-Änderungen); dadurch entfallen die Metadaten-Lookups pro Lesezugriff.
- Optional kann ein Cache für aufgelöste Werte (z. B. Redis) eingesetzt werden, wenn sehr viele Lesevorgänge mit unterschiedlichen Sprachen auftreten. Für KMU-Szenarien ist das in der Regel nicht erforderlich.

**Mehrsprachigkeit der Oberfläche vs. Daten:**

- Die Sprachbaum-Logik betrifft ausschließlich **fachliche Attributwerte** (Produktname, Beschreibung, etc.).
- Die Übersetzung der Benutzeroberfläche (Menü, Buttons) bleibt davon getrennt und folgt dem im Dokument [frontend.md](frontend.md) beschriebenen Ansatz.

## Verwaltung & Aktivierung (Sprachbaum-Administration)

- **Tabelle:** `language_tree` (code, parent, label, fallback_order) plus `active` (bool, default true) und `is_ui` (bool, default false). Basissprachen (`en`/`de`/`fr`) sind `is_ui: true`.
- **Endpunkte (admin/superadmin):** `GET /languages` (alle Knoten inkl. `active`/`is_ui`), `POST /languages`, `PATCH /languages/{code}`, `DELETE /languages/{code}`. `fallback_order` wird aus der Elternkette berechnet (`[parent.code] + parent.fallback_order`); bei Elternwechsel wird der Teilbaum rekursiv neu berechnet (Zyklenschutz). `root` ist unlöschbar; Knoten mit Kind-Sprachen sind nicht löschbar.
- **Wirkung:** `active` steuert, welche **Daten-Sprachen** das Produkt-Formular für die Pflege anbietet (Sprach-Dialog + „Haupteditiersprache“); `is_ui` (in Kombination mit `active`) steuert, welche **UI-Sprachen** der Sprachumschalter im Drawer anbietet (nur Codes mit vorhandener Übersetzungsdatei).
- **Cache:** Änderungen invalidieren die gecachten Fallback-Ketten (`cache.invalidate_language_tree()`).

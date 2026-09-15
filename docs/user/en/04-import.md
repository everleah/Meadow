# Importing data

Under **Import** you bring objects in from a file. The import has two stages:
check first, run second. The run happens as a job whose result you see in a
report afterwards.

![The import page with the file picker and the mapping](/help-assets/import.png)

## Uploading a file

Meadow recognises the format from the file:

- **CSV** (`.csv`) and **Excel** (`.xlsx`, `.xlsm`) — tabular, with column
  mapping.
- **MEF JSON** (`.json`, `.mef`) — the Meadow Exchange Format, the exchange
  format for objects and references.

For a workbook with several sheets you choose the **worksheet**.

## Mapping columns (CSV/Excel)

For every column of the file you decide where its content belongs:

- **Ignore** — the column is not imported.
- **Base field** — `id`, `type` or `status`.
- **Attribute** — an attribute from your attribute definitions. A custom
  attribute name can be typed directly if the list does not contain it yet.
- **Attribute in one language** — as `attribute [language]`; the value is stored
  as the value of that data language.
- **Convention field** — object name or business key. Such fields have no
  attribute definition; without a language suffix the value is stored in the
  current interface language.

**Preview** shows the first rows with your mapping before anything is written.

## Recognising existing objects

You control whether an object is created or updated through:

- **Business key** — compares by a unique attribute (an article number, for
  example). Only this keeps repeated imports idempotent.
- **Source** — a label for the delivering system; together with the object id of
  the document it serves as the reference for later imports.
- **Match by object ID** (MEF) — updates exactly the objects whose Meadow id is
  in the document. Meadow enables this automatically when the document came from
  Meadow itself, so an export can be played back without loss.

**Emit change events** decides whether the import notifies downstream systems
(connectors, webhooks).

## MEF JSON

A MEF document is imported without any column mapping. Instead the interface
shows a summary: format version, source, the number of objects and references,
and the types involved. References are created together with the objects, so the
relationships in the document survive.

## Running and reading the result

After **Run import** the import works in the background. The report then lists
per object whether it was created, updated or skipped; a failure on one object
does not stop the rest. The full run data is also available under **Jobs**.

## Limits and pitfalls

- The **first row** is treated as the header.
- Quantified numbers are converted into the canonical unit during the import.
- A missing type falls back to a default type and a missing status to the draft
  status — so check the mapping in the preview.
- Without a business key or the ID match, every import creates **new** objects.
  That is by far the most common cause of duplicates.

## Read on

- [Exporting data](/help?page=export)
- [Object list (grid)](/help?page=product-grid)

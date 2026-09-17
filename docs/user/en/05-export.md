# Exporting data

Under **Export** you hand out objects as a file — for deliveries to partners, for
evaluations or for an exchange in the MEF format. The assistant walks you through
four steps.

![The export assistant with source, fields, format and preview](/help-assets/export.png)

## 1. Source

Choose **which** objects are exported:

| Source | Meaning |
| --- | --- || **Everything** | Every object of the instance — the complete export for a move to a fresh system. || **Object type** | Every object of one type. |
| **Category** | Objects assigned to a category. |
| **Collection** | The members of a saved grouping. |
| **Saved search** | The hits of a saved search. |
| **Selection** | A list of object IDs that you type or assemble with the tree picker. |

## 2. Fields

Choose the columns the file should contain: the base fields `id`, `type` and
`status`, plus the attributes. The selection is grouped by attribute group and
limited to the attributes the chosen object type allows — anything the object
cannot have is not offered at all.

Multilingual attributes produce **one column per data language**. The vocabulary
of an imported classification standard stays hidden by default; a switch offers
it as well.

## 3. Format and options

| Format | Result |
| --- | --- |
| **CSV** | A text file with a selectable separator. |
| **Excel** | A workbook (`.xlsx`). |
| **JSON** | A MEF document for machine exchange: objects **with** their relations (composition, categories, collections) and their channel releases. |
| **BMEcat** | The catalog format for classification standards (e.g. ETIM). |

Further options:

- **Languages** — one or more data languages.
- **Header** — with or without column headings.
- **Include system fields** — adds technical fields such as timestamps.
- **Also export inherited values** — includes values the object does not
  maintain itself but inherits from its parent.
- **Include parent objects** — adds the parent objects of the selection. An
  object is always written **with** its parent when it is imported, so without
  this switch a selection (variants only, say) could not be restored. For the
  complete export it changes nothing.

### BMEcat

With the BMEcat format a dedicated card replaces the field selection, because the
standard dictates the structure. There you enter the catalog data (id, name,
language, currency, supplier), the attribute whose value becomes the article
number, and the version of the standard. If the chosen delivery needs prices,
the price attribute and the unit codes are asked for as well. The result is
validated against the schema of the chosen version; the report names classes,
features and everything that could not be written.

## 4. Preview and export

The **preview** shows the resulting columns and an object count. **Export**
starts the run in the background. The report names the count, the columns and the
format, and offers the finished file for **download**.

## Saved profiles

Store settings that recur as a **profile**: format, fields, languages, options
and the BMEcat block. A selection at the top of the assistant loads a profile and
fills the form. The source and the e-mail recipients are deliberately **not**
stored — both belong to the individual run, not to the configuration.

## Exporting on a schedule

Administrators can **schedule** an export: the run then repeats on a time plan,
and its results stay available in the list of recent exports. When mail delivery
is configured, the export can also be sent by e-mail.

## Complete restore (moving to a fresh system)

A PIM inventory has two halves, and both are exported separately:

1. **Model and metadata** — under **Configuration**, export every section
   (MCF file): object types, attribute definitions, relation types, rules,
   units, languages, channels, classification standards.
2. **Data** — in this assistant, pick the source **Everything** with the format
   **JSON** (MEF file): objects, structure (composition), categories,
   collections, links and channel releases.

On the fresh system import in this order: **first** the MCF document under
**Configuration**, **then** the MEF document under **Import** (where the option
"Match by object ID" is enabled automatically). Both imports are repeatable: a
second run updates instead of duplicating.

**Users and groups**, **connectors**, **job definitions** and **system settings**
are not part of the document — that is environment configuration and is
maintained per environment.

## Read on

- [Importing data](/help?page=import)
- [Object list (grid)](/help?page=product-grid)
- [Editing an object](/help?page=product-form)

# Object list (grid)

The object list under **Products** is the central working surface: it is where
you search, filter, compare and change many objects at once.

![The object list with its tree, filter bar and toolbar](/help-assets/product-grid.png)

## Loading objects

The list deliberately does not load every object at once. Pick a node in the
tree on the left and the table shows that node's objects. Without a selection it
tells you which action it expects.

The tree has three views (toolbar above the tree):

| View | Shows |
| --- | --- |
| **Structure** | How objects belong together (product → assembly → part). |
| **Categories** | Categories and classification hierarchies. |
| **Collections** | Saved groupings of objects. |

For **Categories** you also choose:

- **Taxonomy** — which kind of node the tree is built from (your own categories
  or an imported classification).
- **Dimension** — which kind of assignment is counted and filtered. All
  dimensions use the same categories; the choice only changes which assignments
  the tree considers.

## Searching and filtering

- **Filter** — full-text search over the loaded objects.
- **Advanced search** — conditions, full text, references, sorting and limits;
  it is applied on the server.
- **Incomplete only** — shows objects that miss required values.
- **Channel filter** — shows objects released for an output channel.
- **Collapse all** — collapses the tree; expanding happens node by node and
  loads children on demand.

Many filters appear in the browser's address bar. Such a URL is a bookmark or a
link you can pass on — it reopens the same view (filtered by type, category,
tree node or channel, for example).

## Columns and views

- **Columns** — choose which attributes the table shows. Multilingual
  attributes can be shown as one column per data language; the working
  language's column stays the one you write in.
- **Views** — save the current combination of filters and columns. Views are
  personal and survive signing out.
- Column widths are adjusted from the filter bar, either to the available width
  or to the content.

## Editing values in place

Cells with a simple value type are editable directly. Changes are only
**pending** at first — **Save** writes them, **Discard** reverts them. That way
several objects can be corrected in one pass.

Computed values, ranges and the reference data of classification standards are
not editable in the grid; maintain them in the
[object form](/help?page=product-form).

## Recognising inherited values

A dimmed value with an origin icon is **inherited** — it is maintained on the
parent object. A crossed-out chain icon means the inheritance was deliberately
interrupted by setting the value empty. The form is where you resolve either
case.

## Working with several objects

- **Bulk actions** — applies one operation to the selection: set or clear
  attributes, change the status, assign categories or relations, release or
  withdraw channels, replace text, consolidate inheritance. The operations are
  grouped by area; every action runs as a job and then reports how many objects
  changed, were skipped or failed.
- **Find & replace** — replaces a literal text in the text attributes of the
  selection.
- **Edit** (note-pencil icon) — opens the selected objects in the form one after
  another, in list order, with **Save & next**.
- **Export** — hands the selection or every match over to
  [data export](/help?page=export).

## Read on

- [Editing an object](/help?page=product-form)
- [Exporting data](/help?page=export)

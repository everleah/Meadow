# Object list (grid)

The object list under **Products** is the central working surface: it is where
you search, filter, compare and change many objects at once.

![The object list with its tree, filter bar and toolbar](/help-assets/product-grid.png)

## Loading objects

The list deliberately does not load every object at once. Pick a node in the
tree on the left and the table shows that node's objects. Without a selection it
tells you which action it expects.

The table shows **one page** of objects (200 rows by default; pick the size below:
50, 100, 200 or 500 objects per page); below the list it says which rows of how
many you are looking at ("1–200 of 2000 objects", "Page 1 of 10") and which
buttons step through the pages (first, previous, next, last). The numbers come
from the server, not from the loaded rows: stepping fetches exactly the next
page instead of letting the list grow in the browser. The page number and the
page size are part of the address (`?page=2`, `?size=50`), so a bookmark opens
the same view.

**Sorting** — clicking a column header sorts the **whole** view on the server
(ascending, descending, and a third click returns to the default order, newest
first). This works the same for id fields and attributes; columns without a
stored value (such as completeness) cannot be sorted — sorting them would only
reorder the visible page and mean something else once you step on. The sort is
part of the address as well and stays while you page.

While paging and sorting, the **column selection** stays as it is: which
attributes get a column is decided for the whole view, not by the page on
screen.

The tree has three views (toolbar above the tree):

| View | Shows |
| --- | --- |
| **Structure** | How objects belong together (product → assembly → part). |
| **Categories** | Categories and classification hierarchies. |
| **Collections** | Saved groupings of objects. |

For **Categories** you also choose:

- **Dimension** — which kind of assignment is counted and filtered. The choice
  only changes which assignments the tree takes into account.
- **Taxonomy** — which kind of node the tree is built from (your own categories
  or an imported classification). When a dimension is bound to one taxonomy (for
  example *Assembly group membership*), the application sets the tree itself and
  the field is locked — the two always match then.

## Creating objects

The **+** symbol above the tree creates a new object (in the *Structure* view).
In the dialog you pick the **object type** (*Product* by default), the **name**
— multilingual via the language icon — and the type's **business keys** (e.g.
`sku` for a product). A business key has to be unique per type and counts as a
required field; the core rejects a duplicate with a message.

The **Placement** line shows where the object lands: under the node selected in
the tree, or at the universal root without a selection. After creating it, the
[object form](/help?page=product-form) opens for the remaining details.

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

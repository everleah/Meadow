# Editing an object

The object form shows **one** object with all of its attributes and
relationships. Open it by double-clicking a row in the
[object list](/help?page=product-grid).

![The object form with its sections, pinned attributes and channels](/help-assets/product-form.png)

## Layout

- **Header** — name, type, status, completeness and the object's actions (e.g.
  *consolidate inheritance*).
- **Sections** — the attributes grouped by attribute group, followed by
  *Categories*, *Relations*, *Channels* and *Class features*. Clicking a heading
  expands or collapses a section.
- **Sidebar** — the attributes you pinned for quick access.
- **Jump menu** — jumps straight to a section.
- **Attribute search** — shows only the matching attributes.
- **Required fields only** — shows only what is still missing.

## Maintaining values

The attribute's type decides the input: text, number with unit, a choice from a
value list, date, URL (with preview and open button) or a range of two numbers.
Required fields are marked with `*`.

**Disabled or absent fields** have a reason: an attribute definition may not
apply to this object type at all, or the value is computed by a business rule.
Disabled fields state the reason as a hint.

## Multilingual values

When an attribute is multilingual, the field holds the current editing language.
The **language icon** next to it opens a dialog to maintain the same value in
every active data language.

If a value is missing in the selected language, Meadow shows the parent
language's value (the fallback) dimmed. As soon as you type, an own value is
created for that language; clearing it brings the fallback back.

## Inheritance

In the structure (assembly → part) shared values are maintained only once. An
inherited value is **dimmed** and carries an origin icon; the tooltip names the
object the value comes from.

The **value actions** menu next to an inheritable attribute offers:

- **Reset to inherited** — removes the own value so the parent's value applies
  again.
- **Set empty** — deliberately interrupts inheritance with an empty value.

On parent objects there is also **Consolidate inheritance**: when all direct
children agree on a value, it moves up and is removed from the children, which
then inherit it. The action shows a preview first and afterwards reports what was
promoted and what was skipped.

## Categories

The **Categories** section contains one block per kind of assignment (the
primary categorisation first, then further dimensions). The picker button opens
the dialog, where you choose one or more categories; every assignment appears as
a chip and can be removed with its ✕.

The dialog shows **the tree of that kind of assignment**: under *Assembly group
membership* you see the assembly groups, under *ETIM class membership* the
classes. When an assignment kind is not fixed (the primary *Categories*
assignment), you first choose the **taxonomy** (categories, assembly groups,
classes …) and then the node; the taxonomy the object already carries
assignments from is preselected.

The **Class features** section shows which attributes the classes assigned to
this object require. Required features count towards completeness, and missing
ones are marked accordingly.

## Relations and channels

- **Relations** — connects the object with other objects (accessories or
  alternative items, for example). Which relation types are allowed depends on
  the object types involved.
- **Picking a target** — the plus sign in the header row of a relation type opens
  the selection. In the *Search* field a **word beginning** is enough ("brake
  clean" finds "Brake cleaner"). The second tab shows **Structure** — the
  hierarchy, e.g. make → model → generation — or **List**: a page-by-page list of
  all allowed objects when the target type does not appear in the hierarchy (for
  example products, which hang below an assembly group). Types that are not
  allowed appear greyed out.
- **Channels** — releases the object for an output channel. The channel state is
  independent of the global status: an object may be globally released and still
  not published for a channel. When a rule demands a specific order, Meadow
  reports a rejected release as a notice.

## Saving

**Save** writes all changes; **Discard** restores the last saved state. Cleared
multilingual fields are genuinely removed, which is what lets inheritance apply
again.

During **sequential editing** (started from the object list) the header shows the
progress and offers **Save & next** — that is how you work through a selection
object by object without reopening the list.

## Read on

- [Object list (grid)](/help?page=product-grid)
- [Exporting data](/help?page=export)

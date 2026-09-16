# Demo Data

For demos, training sessions and load tests a Meadow instance can be filled with
a complete, synthetic dataset: vehicle master data, spare parts with variants,
three taxonomy axes, channel releases, collections, saved searches and business
rules. This chapter describes what the dataset contains and how to load and
remove it.

The demo data does **not** come from the user interface but from the generator
`scripts/demo_data.py` in the project directory. It is synthetic and
deterministic: the same seed produces the same dataset, and no real product data
is used.

## What the dataset contains

The dataset models a slice of an automotive accessories catalogue:

- **Vehicle master data** — makes, model series and generations with composition
  (`make → series → generation`), plus fitments (`fits_vehicle`).
- **Spare parts** — products with variants, dimensions, weights, materials,
  prices, GTINs and lifecycle states (draft through archived).
- **Assembly groups and part families** — its own taxonomy including sub-groups.
- **Class features** — a classification-standard binding (ETIM) so classes,
  features and missing mandatory values become visible.
- **Output channels, collections, saved searches and rules** — including
  releases, so export and rules can be tried out right away.
- **A few deliberately incomplete objects** — missing weight, material or GTIN,
  so the completeness display and its filters have something to show.

Three profiles are available: `tiny` (a few hundred objects, for screenshots),
`demo` (the default, a few thousand objects) and `perf` (a multiple of that, for
load tests).

## Loading

In the project directory, while the instance is running:

```bash
uv run python scripts/demo_data.py --import
```

The run first writes the files (`.tmp/demo-data/`) and then applies them: data
model, objects and relations, class features and finally the extras (releases,
collections, searches, rule activation). At the end it compares the numbers with
the instance. Another profile and seed:

```bash
uv run python scripts/demo_data.py --profile tiny --seed 7 --import
```

The import is repeatable: a second run **updates** existing objects instead of
duplicating them. Failed entries (a concurrent write, for example) are replayed
automatically at the end.

## Looking at it in the user interface

- **Dashboard** — number of objects per type, completeness, status distribution.
- **Object list** — filter, sort, choose columns, bulk actions; the taxonomy can
  be shown or hidden on the left.
- **Product form** — attributes, inheritance, relations, channel releases.
- **Collections and saved searches** — the demo data ships ready-made examples,
  such as a collection maintained by a filter.
- **Categories** — the taxonomy dimensions and their attributes.
- **Export** — write an example collection out as a BMEcat file.

An introduction to working with these pages is in
[Getting started](/help?page=getting-started); details are in the respective
chapters.

## Removing

The generator can remove its dataset completely:

```bash
uv run python scripts/demo_data.py --cleanup
```

It removes all objects **of that source** (including their subtrees) as well as
the collections and saved searches it created. Objects, collections and searches
from other sources — everything you created yourself in the interface — are left
untouched. For a clean rebuild:

```bash
uv run python scripts/demo_data.py --cleanup --import
```

Acceptance of the dataset is scripted as well and read-only:

```bash
uv run python scripts/validate_demo.py
```

Among other things it checks object counts, duplicate key values, channel
releases, rules, collections and searches, the composition, and — if the
standard resources are present — the validity of a BMEcat export.

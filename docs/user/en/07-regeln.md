# Business rules

Rules are short Python scripts that Meadow runs itself at certain moments: when
an object is saved, during an import, on a status change or when releasing to a
channel. They validate values, fill computed attributes, normalise input and
guard releases — the logic lives in the system instead of relying on everyone
remembering it.

![The rule list with filters and status columns](/help-assets/rules.png)

## What rules are for

- **Validate** — a value must have a format (article number, GTIN) or satisfy a
  rule ("sales price never below purchase price").
- **Compute** — an attribute is derived from other attributes instead of being
  maintained by hand.
- **Normalise** — input is brought into one shape (whitespace, letter case,
  units).
- **Guard releases** — a status or channel change is only allowed when certain
  conditions hold.

## Rule types

| Type | Effect |
| --- | --- |
| **Validation** | Reports errors (blocking the save) or warnings (a hint only). |
| **Computed** | Writes values through `result.set_value(...)` — only for attributes declared as *computed*. |
| **Normalisation** | Silently corrects incoming values instead of rejecting them. |
| **Workflow guard** | Decides a release through `result.allow(...)`. |
| **Bulk** | Runs from a bulk action, not on a single save. |

## State: draft, active, disabled

Only **active** rules run. New rules start as a *draft*: they are stored but
interfere with nothing. The buttons in the header activate or disable a rule;
deleting removes it for good. Every change raises the **version** and records
who changed it.

## Creating a rule

The editor has these fields:

- **Name** — unique, appears in the log and in the audit trail.
- **Type** — see the table above.
- **Description** — what the rule is good for. This is the target of
  *explaining* an existing rule.
- **Applies to object types** — empty means: **all** types.
- **Python script** — the rule itself.

## Contract & policy

Below the script the editor has an expandable **Contract & policy** area. It is
generated from the sandbox itself and shows exactly what a rule may use — it is
the most reliable cheat sheet you can get:

- **Entry point** — every rule defines exactly one function
  `run(obj, ctx, result)`.
- **`obj`** — the object as a read-only view: `obj["attributes"]` reads the
  attributes, `obj["id"]`, `obj["type"]`, `obj["status"]` the basic fields.
- **`ctx`** — the context of the call: `rule_type`, `object_type`, `mode`,
  `language`, `actor`, `channel`, `release_status`, `missing_features`.
- **`result`** — the allowed answers: `add_error`, `add_warning`, `set_value`,
  `allow` and `log`.
- **Available names** — the built-in functions, the modules a rule may import,
  and the limits (runtime, memory, script size).

The sandbox is strict: no file access, no network, no unlisted imports. An
example:

```python
def run(obj, ctx, result):
    attributes = obj["attributes"]
    net = attributes.get("net_price")
    if net is None:
        result.add_warning("net_price", "missing", "Net price is missing.")
        return
    if net <= 0:
        result.add_error("net_price", "range", "Net price must be positive.")
```

## Describing a rule instead of writing it

With the **AI assistance** switched on in the system settings, a **Create a
suggestion** button sits below the script. Describe in your own words what the
rule should do and pick a mode:

- **Write new** — a complete script grows out of the description.
- **Improve existing** — the script in the editor is the starting point.
- **Explain** — the other direction: an existing script becomes one sentence for
  the *Description* field.

The suggestion appears in the dialog and only reaches the editor through
**Apply** — and it is still saved through **Save** alone. Every suggestion passes
the same check as hand-written code; when the sandbox rejects it, the reason is
shown.

With the assistance switched off, the same spot says why: it is enabled under
*System settings*.

## Rule Lab: try before you save

The **Rule Lab** runs a rule without storing anything. You choose whether to
test against **a real object**, against **a selection of objects** or against a
hand-built **payload** (JSON with `type`, `status`, `attributes`). The result
shows errors, warnings, computed values, a guard's verdict and the log lines —
for hand-built payloads also *before* and *after* side by side.

The dry run is the fastest way to understand a rule somebody else wrote.

## Computed attributes

A computed attribute is **marked as computed** in the attribute definition; the
**computation rule** is the rule that writes the value. Without that link the
computation still works on save — the link is only needed for the automatic
recompute of inherited values, because that is how the rule is found. An
attribute that is **not** declared as computed must not be written by a rule:
Meadow rejects the save with a hint instead of silently overwriting a value.

## Read on

- [Editing an object](/help?page=product-form)
- [Importing data](/help?page=import)
- [Exporting data](/help?page=export)

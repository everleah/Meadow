# Rule context (generated)

**Generated file** — do not edit by hand. Source: `meadow/ai/policy.py` and
`meadow/ai/context.py` (which read the running rule sandbox and the script
contract). Regenerate with `uv run python scripts/ai_context.py`; the check
`uv run python scripts/ai_context.py --check` fails when this file is stale.

Background and staging: `plan/detail/ki-assistenz.md`.

## Script contract

```
Script contract — one function per rule:
  def run(obj, ctx, result): ...
  obj  : read-only view of the object: obj['id'], obj['type'], obj['status'],
         obj['attributes'] (own + inherited + resolved for the working language).
         Never mutate obj — write through result.
  ctx  : rule_type, object_type, language, mode, actor, channel, release_status, missing_features
  result helpers (the only way a rule produces effects):
    result.add_error(attr: str, code: str, message: str) -> None — Records a blocking validation error.
    result.add_warning(attr: str, code: str, message: str) -> None — Records a non-blocking validation warning.
    result.allow(allowed: bool, reason: str | None = None) -> None — Verdict for a status transition.
    result.log(message: str, level: str = 'INFO') -> None — Appends a log line to the evaluation result.
    result.set_value(attr: str, value: Any) -> None — Sets a computed (or normalized) attribute value.
```

## Sandbox policy

- **Available builtins** (101) — use only these:

  ```
  ArithmeticError, AssertionError, AttributeError, BaseException, BufferError, BytesWarning, DeprecationWarning, EOFError, Ellipsis, EnvironmentError, Exception, False, FloatingPointError, FutureWarning, GeneratorExit, IOError, ImportError, ImportWarning, IndentationError, IndexError, KeyError, KeyboardInterrupt, LookupError, MemoryError, NameError, None, NotImplementedError, OSError, OverflowError, PendingDeprecationWarning, ReferenceError, RuntimeError, RuntimeWarning, StopIteration, SyntaxError, SyntaxWarning, SystemError, SystemExit, TabError, True, TypeError, UnboundLocalError, UnicodeDecodeError, UnicodeEncodeError, UnicodeError, UnicodeTranslateError, UnicodeWarning, UserWarning, ValueError, Warning, ZeroDivisionError, __build_class__, __import__, _getattr_, abs, all, any, bool, bytes, callable, chr, complex, delattr, dict, divmod, enumerate, float, frozenset, getattr, hash, hex, id, int, isinstance, issubclass, len, list, math, max, min, oct, ord, pow, random, range, reorder, repr, reversed, round, same_type, set, setattr, slice, sorted, str, string, sum, test, tuple, whrandom, zip
  ```

- **Importable modules:** collections, datetime, decimal, json, math, re, statistics, typing, uuid
- **Not available** (compiles, fails at runtime): breakpoint, compile, dir, eval, exec, globals, hasattr, help, input, locals, memoryview, open, vars
- **Additionally injected:** (none)
- **Limits:** 65536 bytes per script, 1.5s wall clock, 256 MiB traced objects

## Example rules

The few-shot set shipped in `meadow/ai/examples.py` — every one of them is
compiled with the real policy by `tests/test_ai_examples.py`.

### `calc_gross_price` (computed)

```python
def run(obj, ctx, result):
    net = obj["attributes"].get("net_price")
    if isinstance(net, (int, float)):
        result.set_value("gross_price", round(net * 1.19, 2))
```

### `default_warranty` (computed)

```python
def run(obj, ctx, result):
    tier = obj["attributes"].get("quality_tier")
    result.set_value("warranty_months", 24 if tier in ("oe", "oes") else 12)
```

### `validate_gtin` (validation)

```python
def run(obj, ctx, result):
    gtin = obj["attributes"].get("gtin")
    if gtin is None:
        return
    digits = str(gtin)
    if len(digits) != 13 or not digits.isdigit():
        result.add_error("gtin", "invalid", "GTIN must have 13 digits")
        return
    total = sum(int(d) * (3 if index % 2 else 1) for index, d in enumerate(digits[:12]))
    if (10 - total % 10) % 10 != int(digits[12]):
        result.add_error("gtin", "checksum", "GTIN check digit is wrong")
```

### `validate_sku` (validation)

```python
def run(obj, ctx, result):
    sku = obj["attributes"].get("sku")
    if not isinstance(sku, str) or not sku.strip():
        result.add_error("sku", "missing", "Article number must be set")
        return
    if sku != sku.strip().upper():
        result.add_warning("sku", "format", "Article number is not upper case without spaces")
```

### `guard_publish_features` (workflow_guard)

```python
def run(obj, ctx, result):
    if ctx.actor == "system":
        result.allow(True, None)
        return
    missing = list(getattr(ctx, "missing_features", None) or ())
    if missing and obj.get("status") == "published":
        result.allow(False, "class features are missing: " + ", ".join(missing))
        return
    result.allow(True, None)
```

### `normalize_import` (normalization)

```python
def run(obj, ctx, result):
    sku = obj["attributes"].get("sku")
    if isinstance(sku, str):
        result.set_value("sku", sku.strip().upper())
    if obj["attributes"].get("product_line") is None:
        result.set_value("product_line", "pro")
```

## Attribute projection

A context lists the **own** attributes of one object type, never the whole
vocabulary: 17 463 definitions (8.35 MB) are installed on the dev instance, of
which 17 443 apply to `product` — their names alone are 174 654 characters.
Own means: without `reference_data` (the imported standard's feature
vocabulary). `meadow.ai.context.attribute_facts(vocabulary, object_type)`
does the projection; the measured example, `product`:

```
gross_price:number(computed), net_price:number, sku:text
```

The snippet above is a **sample** (the generator does not talk to a running
core); the live list is what the rule editor's contract panel shows.

"""Curated example rules — the few-shot set a prompt (and the panel) can show.

Six short rules, one per rule type family, all of them **covered by a test that compiles
them with the real sandbox policy** (`tests/test_ai_examples.py`). They are the style
reference: defensive against missing values, no side effects, English messages.

They deliberately live inside the package instead of being read from the demo catalogue
(`scripts/demo_data_catalog.json`): the frontend image copies `meadow/` and `docs/user/`
only, so a file under `scripts/` would be missing at runtime (checked in
`docker/Dockerfile.frontend`). The demo catalogue keeps its own rules for demo data; these
are maintained as the *example* set, and the compile test is what keeps them honest.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple


class ExampleRule(NamedTuple):
    """One example rule: its name, the rule type it demonstrates and its script."""

    name: str
    rule_type: str
    script: str


_COMPUTED_GROSS = """def run(obj, ctx, result):
    net = obj["attributes"].get("net_price")
    if isinstance(net, (int, float)):
        result.set_value("gross_price", round(net * 1.19, 2))
"""

_COMPUTED_WARRANTY = """def run(obj, ctx, result):
    tier = obj["attributes"].get("quality_tier")
    result.set_value("warranty_months", 24 if tier in ("oe", "oes") else 12)
"""

_VALIDATION_GTIN = """def run(obj, ctx, result):
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
"""

_VALIDATION_SKU = """import re

def run(obj, ctx, result):
    sku = obj["attributes"].get("sku")
    if not isinstance(sku, str) or not sku.strip():
        result.add_error("sku", "missing", "Article number must be set")
        return
    if not re.fullmatch(r"[A-Z0-9-]+", sku):
        result.add_error("sku", "format", "Article number may only contain A-Z, 0-9 and -")
"""

_WORKFLOW_GUARD = """def run(obj, ctx, result):
    if ctx.actor == "system":
        result.allow(True, None)
        return
    missing = list(getattr(ctx, "missing_features", None) or ())
    if missing and obj.get("status") == "published":
        result.allow(False, "class features are missing: " + ", ".join(missing))
        return
    result.allow(True, None)
"""

_NORMALIZATION_IMPORT = """def run(obj, ctx, result):
    sku = obj["attributes"].get("sku")
    if isinstance(sku, str):
        result.set_value("sku", sku.strip().upper())
    if obj["attributes"].get("product_line") is None:
        result.set_value("product_line", "pro")
"""

#: Query-DSL examples (stage 2): the shape the core's `SavedSearchQuery` accepts.
#: Kept as data, not as text — a test can parse them, and the prompt renders them
#: with `json.dumps` so the model sees exactly the format it has to produce.
SEARCH_EXAMPLES: tuple[dict[str, Any], ...] = (
    {
        "where": {
            "op": "and",
            "conditions": [
                {"field": "type", "operator": "eq", "value": "product"},
                {"field": "attributes.net_price", "operator": "between", "value": [10, 50]},
                {"field": "status", "operator": "eq", "value": "published"},
            ],
        },
        "sort": [{"field": "attributes.name.de", "order": "asc"}],
        "limit": 500,
    },
    {
        "where": {
            "op": "or",
            "conditions": [
                {"field": "attributes.gtin", "operator": "is_null"},
                {"field": "attributes.sku", "operator": "not_empty"},
            ],
        },
        "limit": 200,
    },
)


def search_examples(limit: int = 2) -> list[str]:
    """Example queries as formatted JSON (the search prompt's few-shot set)."""
    return [json.dumps(example, indent=2) for example in SEARCH_EXAMPLES[:limit]]


#: The shipped example rules, ordered by rule type as in the plan's table.
EXAMPLES: tuple[ExampleRule, ...] = (
    ExampleRule("calc_gross_price", "computed", _COMPUTED_GROSS),
    ExampleRule("default_warranty", "computed", _COMPUTED_WARRANTY),
    ExampleRule("validate_gtin", "validation", _VALIDATION_GTIN),
    ExampleRule("validate_sku", "validation", _VALIDATION_SKU),
    ExampleRule("guard_publish_features", "workflow_guard", _WORKFLOW_GUARD),
    ExampleRule("normalize_import", "normalization", _NORMALIZATION_IMPORT),
)


def examples_for(rule_type: str, limit: int = 3) -> list[ExampleRule]:
    """The example rules of one rule type (the prompt's few-shot set for that type).

    A rule type without examples returns an empty list; the caller then has to decide
    what to show (the panel says so explicitly instead of pretending).
    """
    return [example for example in EXAMPLES if example.rule_type == rule_type][:limit]

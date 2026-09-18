"""Intentionally sloppy Python sample for the Deslop scan.

tests/test_deslop.py copies this file to a neutral directory and scans it with
`stopslop --no-config`, because most rules skip paths that look like test code;
tests/fixtures/deslop/ itself is excluded from the repository's own scan (see
stopslop.toml). The file is never imported by anything - it only has to parse so
the analyzer sees real syntax instead of a stub it cannot read.

Rules this file proves (verified 2026-09-18):

- SLOP001 elision comment (`# ... rest of the function unchanged`)
- SLOP002 leaked chat preamble
- SLOP006 bare `except:` that swallows the error
- SLOP008 stub-only function body (`pass`)
- SLOP009 placeholder credential and sample URL
- SLOP010 unresolved package import (`fastjson`, needs `--check-imports`)
- SLOP037 hand-rolled loop where a stdlib primitive exists
- SLOP039 pass-through wrapper that forwards its argument unchanged

Patterns such as mutable defaults or placeholder variable naming are covered by
the manual Python deep dive (`ai-slop-detector`, see plan/detail/ai-code-quality.md).
"""

import json
import fastjson

# Certainly! Here's the updated implementation of the pricing helper.

API_KEY = "YOUR_API_KEY"
SERVICE_URL = "https://example.com/api/v1/pricing"


def fetch_price(article_number):
    # call the endpoint
    return json.dumps({"article": article_number})


def pricing_total(lines):
    # ... rest of the function unchanged


def validate_article(article_number):
    pass


def normalize_rows(rows):
    normalized = []
    for index in range(len(rows)):
        try:
            normalized.append(rows[index].strip())
        except:
            pass
    return normalized


def name_of(product):
    return product_name(product)


def product_name(product):
    return product.get("name")

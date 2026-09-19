"""Generate the rule-model reference from the runtime sources (`--check` guards drift).

Writes two files, both **derived** from code, never hand-edited:

* `docs/developer/rule-context.md` — the contract and the sandbox policy as readable
  documentation (what a rule author may use, with the generated signatures).
* `docs/developer/rule-context.json` — the same facts as a machine-readable manifest for
  tools (the later assistant service and any external consumer read this shape).

Both come from `meadow.ai.policy` and `meadow.ai.context`, which in turn read the running
sandbox and the script contract — so the reference cannot describe a policy the code does
not have. No generation timestamp on purpose: the file changes when the facts change, and
`git` records when (a timestamp would make `--check` fail on every run).

Usage (from the repository root, venv active):

    uv run python scripts/ai_context.py            # write the reference
    uv run python scripts/ai_context.py --check    # exit 1 when the files are stale
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # the script runs from the repository root
    sys.path.insert(0, str(ROOT))

from meadow.ai.context import (  # noqa: E402 - after the path fix above
    attribute_facts,
    attribute_names_text,
    contract_block,
    policy_block,
)
from meadow.ai.examples import EXAMPLES  # noqa: E402
from meadow.ai.policy import policy_facts  # noqa: E402

MARKDOWN = ROOT / "docs" / "developer" / "rule-context.md"
MANIFEST = ROOT / "docs" / "developer" / "rule-context.json"


def build_payload() -> dict:
    """The manifest: policy facts, the contract helpers and the example rules."""
    facts = policy_facts()
    facts["contract_block"] = contract_block()
    facts["policy_block"] = policy_block()
    facts["examples"] = [
        {"name": example.name, "rule_type": example.rule_type, "script": example.script}
        for example in EXAMPLES
    ]
    return facts


def render_markdown(payload: dict) -> str:
    """The human-readable twin of the manifest."""
    limits = payload["limits"]
    sample_names = attribute_names_text(attribute_facts(_dev_vocabulary_sample(), "product"))
    lines = [
        "# Rule context (generated)",
        "",
        "**Generated file** — do not edit by hand. Source: `meadow/ai/policy.py` and",
        "`meadow/ai/context.py` (which read the running rule sandbox and the script",
        "contract). Regenerate with `uv run python scripts/ai_context.py`; the check",
        "`uv run python scripts/ai_context.py --check` fails when this file is stale.",
        "",
        "Background and staging: `plan/detail/ki-assistenz.md`.",
        "",
        "## Script contract",
        "",
        "```",
        payload["contract_block"],
        "```",
        "",
        "## Sandbox policy",
        "",
        f"- **Available builtins** ({len(payload['builtins'])}) — use only these:",
        "",
        "  ```",
        "  " + ", ".join(payload["builtins"]),
        "  ```",
        "",
        f"- **Importable modules:** {', '.join(payload['available_imports'])}",
        "- **Not available** (compiles, fails at runtime): "
        f"{', '.join(payload['forbidden_helpers'])}",
        f"- **Additionally injected:** {', '.join(payload['injected_globals']) or '(none)'}",
        f"- **Limits:** {limits['max_source_bytes']:.0f} bytes per script, "
        f"{limits['timeout_seconds']}s wall clock, "
        f"{limits['max_memory_bytes'] / (1024 * 1024):.0f} MiB traced objects",
        "",
        "## Example rules",
        "",
        "The few-shot set shipped in `meadow/ai/examples.py` — every one of them is",
        "compiled with the real policy by `tests/test_ai_examples.py`.",
        "",
    ]
    for example in payload["examples"]:
        lines.extend(
            [
                f"### `{example['name']}` ({example['rule_type']})",
                "",
                "```python",
                example["script"].rstrip("\n"),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Attribute projection",
            "",
            "A context lists the **own** attributes of one object type, never the whole",
            "vocabulary: 17 463 definitions (8.35 MB) are installed on the dev instance, of",
            "which 17 443 apply to `product` — their names alone are 174 654 characters.",
            "Own means: without `reference_data` (the imported standard's feature",
            "vocabulary). `meadow.ai.context.attribute_facts(vocabulary, object_type)`",
            "does the projection; the measured example, `product`:",
            "",
            f"```\n{sample_names}\n```",
            "",
            "The snippet above is a **sample** (the generator does not talk to a running",
            "core); the live list is what the rule editor's contract panel shows.",
            "",
        ]
    )
    return "\n".join(lines)


def _dev_vocabulary_sample() -> list[dict]:
    """A tiny vocabulary sample for the Markdown example (no core access needed)."""
    return [
        {"name": "net_price", "type": "number", "applicable_types": ["product"]},
        {
            "name": "gross_price",
            "type": "number",
            "applicable_types": ["product"],
            "computed": True,
        },
        {"name": "sku", "type": "text", "applicable_types": ["product"]},
        {
            "name": "EF000123",
            "type": "number",
            "reference_data": True,
            "applicable_types": ["product"],
        },
    ]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    payload = build_payload()
    markdown = render_markdown(payload)
    manifest = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    targets = ((MARKDOWN, markdown), (MANIFEST, manifest))
    if check:
        stale = [
            path.relative_to(ROOT).as_posix()
            for path, content in targets
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print(f"stale: {', '.join(stale)} — run `uv run python scripts/ai_context.py`")
            return 1
        print("rule context is up to date")
        return 0

    for path, content in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()} ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

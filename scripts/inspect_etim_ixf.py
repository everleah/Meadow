"""Streaming reconnaissance of an ETIM IXF export (stdlib only).

Phase-1 spike tool for the BMEcat/ETIM work (see `plan/detail/bmecat-export.md`
§11.4/§11.6): it walks the (large) IXF document once, counts every top-level
section and its child elements and writes a structural skeleton plus one sample
item per section to a UTF-8 text file that can be searched with grep. Only ASCII
goes to the console, so the output stays readable on a cp1252 terminal.

Usage:
    uv run python scripts/inspect_etim_ixf.py <ixf-file> [report-file]
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_LIMIT = 4000


def local(tag: str) -> str:
    """Strips the `{namespace}` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


@dataclass
class Section:
    """One top-level element of the IXF document (e.g. `Units`)."""

    name: str
    count: int = 0
    item: str = ""
    children: Counter[str] = field(default_factory=Counter)
    grandchildren: Counter[str] = field(default_factory=Counter)
    sample: str = ""


def inspect(path: Path) -> tuple[str, str, OrderedDict[str, Section]]:
    """Walks the document once and returns (root tag, namespace, sections).

    Depth layout of an IXF document: 1 = `IXF`, 2 = section (`Units`, `Classes`,
    ...), 3 = item (`Unit`, `Class`, ...), 4 = item fields (`Code`, `Translations`,
    ...), 5 = fields of a field (`Translation`). Every item is cleared as soon as
    it ends, so memory stays flat.
    """
    sections: OrderedDict[str, Section] = OrderedDict()
    root_tag = ""
    namespace = ""
    stack: list[str] = []
    current: Section | None = None

    for event, elem in ET.iterparse(path, events=("start", "end")):
        tag = local(elem.tag)

        if event == "start":
            stack.append(tag)
            depth = len(stack)
            if depth == 1:
                root_tag = tag
                namespace = elem.tag[: -len(tag) - 2] if "}" in elem.tag else ""
            elif depth == 2:
                current = sections.setdefault(tag, Section(tag))
            elif depth == 3 and current is not None:
                current.count += 1
                current.item = tag
            elif depth == 4 and current is not None:
                current.children[tag] += 1
            elif depth == 5 and current is not None:
                current.grandchildren[f"{stack[-2]}>{tag}"] += 1
            continue

        stack.pop()
        depth = len(stack)
        if depth == 2 and current is not None:
            if not current.sample:
                current.sample = ET.tostring(elem, encoding="unicode")[:SAMPLE_LIMIT]
            elem.clear()
        elif depth == 1:
            elem.clear()

    return root_tag, namespace, sections


def render(root_tag: str, namespace: str, sections: OrderedDict[str, Section]) -> str:
    lines = [f"root: {root_tag}", f"namespace: {namespace}", ""]
    for section in sections.values():
        lines.append(f"## {section.name} (items: {section.count}, item element: {section.item})")
        children = ", ".join(f"{tag}={n}" for tag, n in section.children.most_common())
        lines.append(f"fields: {children}")
        nested = ", ".join(f"{tag}={n}" for tag, n in section.grandchildren.most_common(20))
        lines.append(f"nested: {nested}")
        lines.append(f"sample:\n{section.sample}")
        lines.append("")
    return "\n".join(lines)


def ascii_only(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: uv run python scripts/inspect_etim_ixf.py <ixf-file> [report-file]")
        return 2
    path = Path(argv[1])
    report = Path(argv[2]) if len(argv) > 2 else path.with_name("_ixf_structure.txt")
    root_tag, namespace, sections = inspect(path)
    report.write_text(render(root_tag, namespace, sections), encoding="utf-8")
    print(ascii_only(f"root={root_tag} namespace={namespace}"))
    for section in sections.values():
        print(
            ascii_only(
                f"  {section.name}: {section.count} x {section.item}, "
                f"fields={sorted(section.children)}"
            )
        )
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

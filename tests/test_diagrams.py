"""Contracts for the plan-derived architecture sources and portable graphics."""

import ast
import re
import struct
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
SOURCES = sorted(
    path
    for path in DIAGRAMS.glob("*.py")
    if not path.name.startswith("_") and path.name != "render.py"
)


def test_every_plan_document_has_one_documented_diagram() -> None:
    expected = {ROOT / "plan" / "meadow-pim-architektur.md"}
    expected.update(
        path for path in (ROOT / "plan" / "detail").glob("*.md") if path.name != "AGENTS.md"
    )
    covered: list[Path] = []
    index = (DIAGRAMS / "README.md").read_text(encoding="utf-8")
    for source in SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        match = re.search(r"Source: (plan/[\w./-]+\.md)", ast.get_docstring(tree) or "")
        assert match is not None, f"Missing source document: {source.name}"
        covered.append(ROOT / match.group(1))
        assert f"]({source.name})" in index
    assert set(covered) == expected
    assert len(covered) == len(expected), "Each plan document needs one focused view."


def test_generated_graphics_are_portable_png_svg_pairs() -> None:
    for source in SOURCES:
        png = DIAGRAMS / "rendered" / f"{source.stem}.png"
        svg = DIAGRAMS / "rendered" / f"{source.stem}.svg"
        content = png.read_bytes()
        assert content[:8] == b"\x89PNG\r\n\x1a\n", png.name
        width, height = struct.unpack(">II", content[16:24])
        assert width > 100 and height > 100, png.name
        tree = ElementTree.parse(svg)
        images = tree.findall(".//{http://www.w3.org/2000/svg}image")
        assert images, f"No icons in {svg.name}"
        for image in images:
            href = image.attrib["{http://www.w3.org/1999/xlink}href"]
            assert href.startswith("data:image/png;base64,"), f"External icon in {svg.name}"


def test_renderer_lists_all_sources_from_another_working_directory(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(DIAGRAMS / "render.py"), "--list"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [source.stem for source in SOURCES]

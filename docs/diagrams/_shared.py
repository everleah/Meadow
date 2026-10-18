"""Shared icons, visual conventions and self-contained diagram output."""

import base64
import mimetypes
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from diagrams import Cluster as Cluster
from diagrams import Diagram, Edge
from diagrams.generic.storage import Storage as ObjectStorage
from diagrams.onprem.client import Client as Browser
from diagrams.onprem.client import User as User
from diagrams.onprem.compute import Server as External
from diagrams.onprem.network import Traefik as Traefik
from diagrams.onprem.queue import Nats as Nats
from diagrams.programming.flowchart import Action as Operation
from diagrams.programming.flowchart import Database as Database
from diagrams.programming.flowchart import Decision as Decision
from diagrams.programming.flowchart import Document as Artifact
from diagrams.programming.language import Python as Python
from diagrams.programming.language import Rust as Core

__all__ = [
    "Artifact",
    "Browser",
    "Cluster",
    "Core",
    "Database",
    "Decision",
    "External",
    "Nats",
    "ObjectStorage",
    "Operation",
    "Python",
    "Traefik",
    "User",
    "diagram",
    "link",
]

OUTPUT = Path(__file__).resolve().parent / "rendered"
GREEN = "#15803d"
BLUE = "#0284c7"
AMBER = "#b45309"
SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"


def link(label: str, *, async_: bool = False, optional: bool = False) -> Edge:
    return Edge(
        label=label,
        color=AMBER if optional else BLUE if async_ else GREEN,
        style="dashed" if async_ or optional else "solid",
    )


def embed_icons(path: Path) -> None:
    tree = ElementTree.parse(path)
    for image in tree.findall(f".//{{{SVG}}}image"):
        attribute = f"{{{XLINK}}}href"
        icon = Path(image.attrib[attribute])
        mime, _ = mimetypes.guess_type(icon.name)
        if mime is None:
            raise ValueError(f"Unknown diagram icon format: {icon}")
        encoded = base64.b64encode(icon.read_bytes()).decode("ascii")
        image.set(attribute, f"data:{mime};base64,{encoded}")
    ElementTree.register_namespace("", SVG)
    ElementTree.register_namespace("xlink", XLINK)
    tree.write(path, encoding="utf-8", xml_declaration=True)


@contextmanager
def diagram(title: str, name: str, *, direction: str = "LR") -> Iterator[None]:
    if shutil.which("dot") is None:
        raise SystemExit("Graphviz 'dot' is missing from PATH. See docs/diagrams/README.md.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with Diagram(
        title,
        filename=str(OUTPUT / name),
        outformat=["png", "svg"],
        show=False,
        direction=direction,
        graph_attr={
            "bgcolor": "white",
            "fontname": "Arial",
            "fontsize": "20",
            "pad": "0.5",
            "nodesep": "0.65",
            "ranksep": "0.9",
            "dpi": "140",
            "splines": "spline",
        },
        node_attr={"fontname": "Arial", "fontsize": "12"},
        edge_attr={"fontname": "Arial", "fontsize": "11"},
    ):
        yield
    embed_icons(OUTPUT / f"{name}.svg")

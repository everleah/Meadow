"""Render all architecture views, or a selected subset, without application I/O."""

import argparse
import runpy
from pathlib import Path


def main() -> None:
    directory = Path(__file__).resolve().parent
    sources = {
        path.stem: path
        for path in sorted(directory.glob("*.py"))
        if not path.name.startswith("_") and path.name != "render.py"
    }
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=sorted(sources))
    parser.add_argument("--list", action="store_true", help="List available diagram names.")
    args = parser.parse_args()
    if args.list:
        print("\n".join(sources))
        return
    for name in args.only or sources:
        runpy.run_path(str(sources[name]), run_name="__main__")
        print(f"Rendered {name}.png and {name}.svg")


if __name__ == "__main__":
    main()

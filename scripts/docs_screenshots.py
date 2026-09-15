"""Generate the handbook screenshots for `docs/user/assets/`.

The handbook is Markdown; its images are **generated, not hand-made**: this script
drives a running local dev instance (core + frontend + infrastructure, e.g. after
`scripts/run-dev.ps1` and a started `pim-core`) with Playwright and writes the PNGs
the chapters reference as `/help-assets/<name>.png`.

Usage::

    uv run python scripts/docs_screenshots.py            # every shot
    uv run python scripts/docs_screenshots.py --list     # available shots
    uv run python scripts/docs_screenshots.py --only product-grid,product-form

Determinism rules (so a regenerated image means the same thing):

- fixed viewport and reduced motion — no half-finished animations in the image;
- the browser context carries the locale, and a **fresh** context has no stored
  preference, so the frontend picks it from `Accept-Language` (German by default);
- every shot waits for its own marker element instead of a blind sleep.

What the script does **not** do: seed data. It photographs whatever the instance
contains, so run it against a dev database that carries demo data — otherwise the
images show an empty tool and lose their point. The object shown in the form shot
is discovered through the core API (first object of `--object-type`).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "user" / "assets"


@dataclass(frozen=True)
class Shot:
    """One screenshot: where to go, what to wait for, what to call the file."""

    name: str
    path: str
    marker: str
    #: Milliseconds to let the page settle after the marker appeared.
    settle_ms: int = 1200
    #: `object` = filled in with the discovered object id at runtime.
    needs_object: bool = False


#: Curated set — few, stable images of the core working areas, in handbook order.
SHOTS: tuple[Shot, ...] = (
    Shot("dashboard", "/", ".q-header", settle_ms=2000),
    Shot("product-grid", "/products?type=product", ".ag-root-wrapper", settle_ms=2500),
    Shot("product-form", "/products/{object}", ".text-h5", settle_ms=2500, needs_object=True),
    Shot("import", "/import", ".q-card", settle_ms=1500),
    Shot("export", "/export", ".q-card", settle_ms=1500),
    Shot("help", "/help", ".help-body", settle_ms=1500),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the handbook screenshots.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--core-url", default="http://127.0.0.1:8080")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--locale", default="de-DE", help="Browser locale (Accept-Language).")
    parser.add_argument("--object-type", default="product", help="Type for the form shot.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--only", default="", help="Comma-separated shot names.")
    parser.add_argument("--list", action="store_true", help="List the shot names and exit.")
    return parser.parse_args()


def object_id(core_url: str, user: str, password: str, object_type: str) -> str:
    """Returns some object id of that type (the form shot photographs one object).

    Tries the plain list first (product-like types) and falls back to the category
    view — both are read-only lookups, the script never writes data.
    """
    with httpx.Client(timeout=10.0) as client:
        token = client.post(
            f"{core_url}/auth/login", json={"username": user, "password": password}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        query = f"?limit=1&type={object_type}"
        for extra in ("", "&ancestor=root"):
            objects = client.get(f"{core_url}/objects{query}{extra}", headers=headers).json()
            if isinstance(objects, list) and objects:
                return str(objects[0]["id"])
    raise SystemExit(
        f"No object of type {object_type!r} found — the form screenshot needs demo data. "
        "Import or create one first (see docs/user/en/04-import.md)."
    )


def shoot(args: argparse.Namespace, shots: list[Shot]) -> int:
    """Logs in once and writes every shot; returns the number of written files."""
    from playwright.sync_api import sync_playwright

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = (
        object_id(args.core_url, args.user, args.password, args.object_type)
        if any(shot.needs_object for shot in shots)
        else ""
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1600, "height": 950},
            locale=args.locale,
            reduced_motion="reduce",
            device_scale_factor=1,
        )
        page = context.new_page()
        try:
            page.goto(f"{args.base_url}/login", wait_until="domcontentloaded", timeout=30_000)
        except Exception as error:  # noqa: BLE001 - the message is the point
            raise SystemExit(
                f"Frontend not reachable at {args.base_url} ({error}).\n"
                "Start the stack first (scripts/run-dev.ps1, pim-core, python -m meadow.frontend)."
            ) from error

        # The login form is rendered client-side — wait for it before filling.
        page.wait_for_selector(".q-field input", timeout=30_000)
        fields = page.locator(".q-field input")
        if fields.count() >= 2:
            fields.nth(0).fill(args.user)
            fields.nth(1).fill(args.password)
            # No role/name lookup: the button label follows the UI language.
            page.locator(".q-card button").last.click()
            page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
            page.wait_for_timeout(2500)

        written = 0
        for shot in shots:
            path = shot.path.format(object=target)
            page.goto(f"{args.base_url}{path}", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector(shot.marker, timeout=30_000)
            page.wait_for_timeout(shot.settle_ms)
            file = out_dir / f"{shot.name}.png"
            page.screenshot(path=str(file))
            print(f"  {file.relative_to(REPO_ROOT)}  ({path})")
            written += 1
        context.close()
        browser.close()
    return written


def main() -> int:
    args = parse_args()
    if args.list:
        for shot in SHOTS:
            print(f"{shot.name:20} {shot.path}")
        return 0
    selected = [name.strip() for name in args.only.split(",") if name.strip()]
    shots = [shot for shot in SHOTS if not selected or shot.name in selected]
    unknown = set(selected) - {shot.name for shot in SHOTS}
    if unknown:
        raise SystemExit(f"Unknown shot(s): {', '.join(sorted(unknown))}")
    if not shots:
        raise SystemExit("No shots selected.")
    print(f"Writing {len(shots)} screenshot(s) to {args.out}")
    return 0 if shoot(args, shots) == len(shots) else 1


if __name__ == "__main__":
    sys.exit(main())

# meadow/frontend/static — Static Assets

## Purpose

Owns the static client assets served by the NiceGUI frontend (branding, imagery, and UI icons) for Meadow PIM. Visual direction: calm, professional "natural meadow" (soft green palette, minimal flat/line-art icons), tuned for a serious business PIM tool, not a lifestyle product.

## Ownership

- Owns all files under `meadow/frontend/static/`.
- `logos/` — brand assets: logo (SVG + PNG), favicon, transparent/app variants.
- `images/` — background / hero imagery (e.g. login background).
- `icons/` — UI icon set (currently empty; drop SVG line icons here).

## Local Contracts

- Asset source is Artlist (logos, backgrounds, icons); keep license/source notes in sync with any asset you add.
- Preferred formats:
  - Logo / icons: **SVG** (primary, vector) + **PNG** fallback for small raster contexts.
  - Backgrounds / photos: **WebP** (primary) + **PNG** fallback; always compress.
  - Favicon: `.ico` (or PNG at 32 / 64 / 128).
- Sizes:
  - Logo: SVG source plus raster at 512–1024 px; favicon 32 / 64 / 128.
  - Login background: 1920×1080 (and 2560×1440 for HiDPI) as WebP.
  - Navbar / drawer: use CSS gradients/colors, not a wide panorama image (readability + performance).
  - UI icons: 24×24 viewBox, stroke ~1.5–2 px, one consistent set (no mixed styles).
- Naming: lowercase, `meadow_<purpose>.<ext>` (e.g. `meadow_logo.svg`, `meadow_login_bg.webp`).
- Static routes needed by unauthenticated pages (e.g. login background) must be reachable without auth: add the asset path to `UNRESTRICTED_ROUTES` in `meadow/frontend/auth.py`.

## Work Guidance

- Keep assets lightweight; compress before committing (WebP / TinyPNG / SVGO).
- Organize by purpose in the subfolders above; do not scatter files at `static/` root.

## Verification

- None yet (no image linting). Manually verify assets load on the login page and in the drawer.

## Child DOX Index

_(none yet)_

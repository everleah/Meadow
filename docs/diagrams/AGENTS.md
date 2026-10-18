# diagrams — Architecture Views

## Purpose

Owns executable `diagrams` sources and their generated architecture graphics.

## Ownership

- `overview.py` visualizes only the subsystems and main relationships from `plan/meadow-pim-architektur.md`.
- Topic scripts visualize meaningful models, boundaries or workflows from `plan/detail/`.
- `_shared.py` owns icon aliases, layout, edge conventions and portable SVG generation.
- `render.py` renders all views or a selected subset; `rendered/` contains generated PNG/SVG pairs.
- `README.md` maps every planning document to a view and explains any deliberate omissions.

## Local Contracts

- Planning documents remain authoritative; diagrams are derived views, not new specifications.
- Keep the overview free of internal tables, endpoints and detailed workflows.
- Reuse the shared technology icons in every view. Logical entities and operations inside a cluster are not additional deployed services.
- Label optional or planned elements explicitly; do not present proposals as implemented behavior.
- Keep each script import-safe with a `build()` function and a main guard.
- Regenerate both graphics after changing a source; never edit generated images manually.
- SVG icons must be embedded so graphics work outside the generating checkout.

## Work Guidance

- Use German diagram labels for the German planning audience, English Python identifiers and docstrings.
- Keep views selective and readable; the index records their focus and source sections.
- Use the repository's `uv` development environment and a local Graphviz `dot` executable; no application services or credentials are needed.

## Verification

- `uv run python docs\diagrams\render.py` executes all sources and regenerates PNG/SVG pairs.
- `uv run ruff check docs\diagrams` and `uv run ruff format --check docs\diagrams`.
- `uv run mypy docs\diagrams`.
- `uv run pytest tests\test_diagrams.py` checks source coverage, the documented inventory and portable PNG/SVG artifacts without requiring Graphviz.

## Child DOX Index

- None. `rendered/` is generated output owned here.

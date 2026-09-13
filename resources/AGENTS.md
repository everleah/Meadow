# resources — Local Standard & Reference Data

## Purpose

Holds vendor standard artefacts that are needed for integration work but must **not** be versioned: BMEcat/ETIM schemas, guidelines and sample catalogues, the ETIM classification data export, and derived text dumps used for research.

## Ownership

- Owns all files under `resources/` **except** this file: `resources/*` is gitignored, `resources/AGENTS.md` is tracked (`!resources/AGENTS.md`).
- `resources/bmecat/` — the BMEcat standard: `bmecat_base_1_2.dtd` and `bmecat_new_catalog_1_2.{dtd,xsd}`, `bmecat_update_prices_1_2.{dtd,xsd}`, `bmecat_update_products_1_2.{dtd,xsd}` (1.2 Rev. A, 2003-05-09, © Fraunhofer IAO/Univ. Essen BLI) plus `bmecat_2005.xsd`, `bmecat_2005_t{nc,upro,upri}.dtd`, `bmecat_2005-2.xsd` (© BME e.V.) and the 2005.2 description PDFs (de/en).
- `resources/etim/` — the ETIM BMEcat profile: XSD **5.0.1**, Guideline **V5.0** (PDF + element-overview XLSX), official sample catalogues (ETIM 7–10), the ETIM **10.0 IXF** export and `_guideline_text.txt` (PDF text dump for searching).
- Planning counterpart: `plan/detail/bmecat-export.md` — §11 documents what was verified from these artefacts.
- Decisions (2026-09-12): the artefacts stay **local only**; no copies, excerpts or fixtures in the repository, and no test depends on them.

## Local Contracts

- Nothing here is part of the build, the test gates or a release artefact. Code and gates must work **without** these files.
- Licensing: BMEcat schemas are © BME e.V., the ETIM data is licensed by ETIM International (licence note in the IXF header). Never commit, redistribute or link these files from committed content, and never copy sizeable excerpts into `plan/` or `docs/`.
- Tests that need this data must be optional: skip when the file is absent — no hard dependency on `resources/`.
- Findings (element names, mandatory flags, rules) belong in `plan/detail/bmecat-export.md`, not in this file.

## Work Guidance

- Obtain files from the official sources (bme.de BMEcat downloads, ETIM International/CMT) and place them here with their original file names so provenance stays traceable: BMEcat material in `resources/bmecat/`, ETIM material in `resources/etim/`.
- `_guideline_text.txt` and `_ixf_structure.txt` are **derived** artefacts (created with `uv run --with pypdf` and `uv run python scripts/inspect_etim_ixf.py`) — regenerate instead of committing them.
- Inspect a new IXF export with `scripts/inspect_etim_ixf.py` first (sections, counts, fields, sample item), convert a class selection into importable documents with `scripts/etim_ixf_to_mef.py` and validate an exported product with `scripts/etim_export_spike.py` (`uv run --with lxml`, needs `etim/bmecat_etim_501.xsd`); record the findings in `plan/detail/bmecat-export.md` §11.
- To look up guideline rules, search the text dump (element name, `+Mandatory`, `FVALUE_DETAILS`, `FUNIT`, …) and check the structure against the XSD/DTD instead of assuming.
- Still to obtain: the BMEcat 1.2 specification PDF, the full IXF section list, the EuroCat "Syntax Binding".

## Verification

- No automated verification (data only, gitignored).
- Manual check after regenerating the dump: `Select-String -Path "resources/etim/_guideline_text.txt" -Pattern "Mandatory" | Measure-Object`.

## Child DOX Index

- No child AGENTS.md files are needed within `resources/`.

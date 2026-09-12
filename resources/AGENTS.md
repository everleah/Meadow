# resources — Local Standard & Reference Data

## Purpose

Holds vendor standard artefacts that are needed for integration work but must **not** be versioned: BMEcat/ETIM schemas, guidelines and sample catalogues, the ETIM classification data export, and derived text dumps used for research.

## Ownership

- Owns all files under `resources/`, which is **gitignored**.
- Current content: `resources/etim/` — ETIM BMEcat 5.0.1 XSD, ETIM BMEcat Guideline V5.0 (PDF + element-overview XLSX), official sample catalogues (ETIM 7–10), the ETIM 10.0 IXF export, the BMEcat 2005.2 XSD, and `_guideline_text.txt` (PDF text dump for searching).
- Planning counterpart: `plan/detail/bmecat-export.md` (§11 documents what was verified from these artefacts).

## Local Contracts

- Nothing here is part of the build, the test gates or a release artefact. Code and gates must work **without** these files.
- Licensing: BMEcat schemas are © BME e.V., the ETIM data is licensed by ETIM International (licence note in the IXF header). Never commit, redistribute or link these files from committed content, and never copy sizeable excerpts into `plan/` or `docs/`.
- Tests that need this data must be optional: skip when the file is absent — no hard dependency on `resources/`.
- Findings (element names, mandatory flags, rules) belong in `plan/detail/bmecat-export.md`, not in this file.

## Work Guidance

- Obtain files from the official sources (bme.de BMEcat downloads, ETIM International/CMT) and place them here with their original file names so provenance stays traceable.
- `_guideline_text.txt` is a derived artefact (created with `uv run --with pypdf`) — regenerate it instead of committing it.
- To look up guideline rules, search the text dump (element name, `+Mandatory`, `FVALUE_DETAILS`, `FUNIT`, …) and check the structure against the XSD instead of assuming.
- Still to obtain: BMEcat 1.2 DTD + specification, the full IXF section list, the EuroCat "Syntax Binding".

## Verification

- No automated verification (data only, gitignored).
- Manual check after regenerating the dump: `Select-String -Path "resources/etim/_guideline_text.txt" -Pattern "Mandatory" | Measure-Object`.

## Child DOX Index

- No child AGENTS.md files are needed within `resources/`.

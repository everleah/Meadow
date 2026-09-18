"""Invariants of the deliberate Deslop scan.

Rationale, boundaries and the repair workflow: plan/detail/ai-code-quality.md.
The configuration invariants always run. The scans drive the pinned external tool
(`stopslop`), so those tests skip when it is not installed - a contributor without
`cargo install stopslop --version 0.5.1 --locked` still gets the rest checked.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "deslop"
BASELINE = ROOT / ".stopslop-baseline.json"
STOPSLOP = shutil.which("stopslop")

# Neutral directory inside the repository (gitignored): the scan has to run there,
# not under `tests/`, because most rules skip paths that look like test code.
PROOF_DIR = ROOT / ".tmp" / "deslop" / "proof"

# Pinned tool version; the scripts and the documentation must agree on it.
PINNED_VERSION = "0.5.1"

# Rules the fixtures prove, verified against the pinned version on 2026-09-18.
# `SLOP010` (unresolved import) only runs with `--check-imports`.
PYTHON_FIXTURE_RULES = {
    "SLOP001",
    "SLOP002",
    "SLOP006",
    "SLOP008",
    "SLOP009",
    "SLOP010",
    "SLOP037",
    "SLOP039",
}
RUST_FIXTURE_RULES = {"SLOP001", "SLOP002", "SLOP008", "SLOP009", "SLOP039"}

requires_stopslop = pytest.mark.skipif(STOPSLOP is None, reason="stopslop is not installed")


def _scan(*arguments: str) -> tuple[list[dict], int]:
    """Run the pinned scan from the repository root and return findings + exit code."""
    assert STOPSLOP is not None, "stopslop is not installed"
    result = subprocess.run(
        [STOPSLOP, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(result.stdout or "[]"), result.returncode


def _load_config() -> dict:
    with (ROOT / "stopslop.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_config_keeps_the_artifact_rules_on():
    config = _load_config()
    # Silenced repository-wide: German technical prose punctuates with dashes, so
    # the rule flags our house style instead of an AI artifact.
    assert config["ignore"] == ["SLOP018"]
    # The artifact rules are what the style linters cannot see; they stay on.
    for rule in ("SLOP001", "SLOP002", "SLOP003", "SLOP004", "SLOP011", "SLOP012", "SLOP013"):
        assert rule not in config["ignore"]
    # No grandfather file (§4.4): every finding of the higher tier fails the scan.
    assert "baseline" not in config


def test_markdown_style_groups_are_silenced_but_artifacts_are_not():
    ignores = _load_config()["per-file-ignores"]
    style_groups = {"verbosity", "format", "rhetoric", "sourcing"}
    assert set(ignores["*.md"]) == style_groups
    assert set(ignores["**/*.md"]) == style_groups


def test_config_excludes_generated_and_vendored_paths():
    excluded = "\n".join(_load_config()["exclude"])
    for path in ("target", ".venv", ".tmp", "backups", "resources", "tests/fixtures/deslop"):
        assert path in excluded


def test_scripts_pin_the_same_tool_version_and_never_repair():
    shell = (ROOT / "scripts" / "deslop.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "deslop.ps1").read_text(encoding="utf-8")
    assert f'stopslop_version="{PINNED_VERSION}"' in shell
    assert f"$StopslopVersion = '{PINNED_VERSION}'" in powershell
    # The deep dive reports; repairs stay deliberate, so no auto-fix flag anywhere
    # (`--fix-plan` is our own mode name, hence the lookahead).
    assert not re.search(r"--fix(?![\w-])", shell)
    assert not re.search(r"--fix(?![\w-])", powershell)
    # The scan points back at the real gates instead of replacing them.
    assert "check.sh" in shell
    assert "check.ps1" in powershell


def test_no_findings_are_grandfathered():
    # Deliberate decision (plan/detail/ai-code-quality.md §4.4): the accepted set is
    # empty, so a finding fails the scan instead of sitting in a baseline. Bringing a
    # baseline back takes a config key, a committed file and a recorded reason.
    assert not BASELINE.exists()
    assert "baseline" not in _load_config()


@requires_stopslop
@pytest.mark.parametrize(
    ("fixture", "expected_rules"),
    [("artifacts.py", PYTHON_FIXTURE_RULES), ("artifacts.rs", RUST_FIXTURE_RULES)],
)
def test_fixture_artifacts_are_detected(fixture: str, expected_rules: set[str]):
    # Two constraints shape this test: the rules skip paths that look like test
    # code, and `SLOP010` resolves package imports against the project manifest, so
    # the fixture is scanned from a neutral directory inside the repository with
    # the repository config switched off.
    proof = PROOF_DIR / fixture
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_bytes((FIXTURES / fixture).read_bytes())
    try:
        findings, status = _scan("--no-config", "--check-imports", "--format", "json", str(proof))
    finally:
        proof.unlink()
    assert expected_rules <= {finding["code"] for finding in findings}
    assert status == 1


@requires_stopslop
def test_repository_scan_is_clean():
    # Nothing is grandfathered, so the repository is green only while there is no
    # finding of the higher tier at all - the fixture directory stays excluded.
    findings, status = _scan(".", "--format", "json")
    assert [finding for finding in findings if finding["tier"] == "A"] == []
    assert status == 0

"""Contracts of the production deployment (plan/detail/betrieb-deployment.md).

These checks are cheap and they protect the parts of a deployment that break
*silently*: a variable the compose reads but `.env.vps.example` never mentions
(the first deployment then starts with an empty database URL), a service without
log rotation (a small VPS fills its disk), a build context that drags `target/`
and `.env.vps` along, or a migration file whose name breaks the ordering of the
runner. Nothing here starts a container - the file-level invariants are what
kept the reference deployment from drifting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.vps.yml"
ENV_EXAMPLE = ROOT / ".env.vps.example"
DOCKERIGNORE = ROOT / ".dockerignore"
MIGRATIONS = ROOT / "migrations"
SCRIPTS = ROOT / "scripts"

VPS_SCRIPTS = (
    "build-images.sh",
    "deploy-vps-meadow.sh",
    "healthcheck-vps.sh",
    "run-migration.sh",
    "backup-vps-db.sh",
    "restore-vps-db.sh",
    "cleanup-backups.sh",
    "collect-vps-diagnostics.sh",
    "tail-vps-errors.sh",
    "bootstrap-vps-admin.sh",
)

_VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}")

# Settings the scripts read from `.env.vps` (everything else in `${...}` inside a
# script is its own local variable, e.g. `${STAMP}` in a file name).
SCRIPT_SETTINGS = (
    "SURREALDB_HTTP_URL",
    "RESTORE_CHUNK_BYTES",
    "AUTO_RUN_DB_MIGRATIONS",
    "AUTO_BASELINE_DB_MIGRATIONS",
    "RUN_POST_DEPLOY_HEALTHCHECK",
    "SURREALDB_NAMESPACE",
    "SURREALDB_DATABASE",
    "BACKUP_DIR",
    "BACKUP_RETENTION_DAYS",
    "TRAEFIK_NETWORK",
)

# Optional settings: commented out in the example on purpose, but they have to be
# discoverable there (a non-interactive registry login).
OPTIONAL_SETTINGS = ("DOCKER_REGISTRY_USER", "DOCKER_REGISTRY_PASSWORD")


def _compose() -> dict[str, Any]:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _services(document: dict[str, Any]) -> dict[str, Any]:
    services = document["services"]
    assert isinstance(services, dict)
    return services


def _env_names() -> set[str]:
    """Every variable name the example file sets (comments are not names)."""
    names: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            names.add(stripped.split("=", 1)[0].strip())
    return names


def _env_value(name: str) -> str:
    """The value the example file sets for `name`."""
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{name}="):
            return stripped.split("=", 1)[1].strip()
    raise AssertionError(f"{name} is not set in .env.vps.example")


def test_compose_reads_no_variable_that_the_example_does_not_document() -> None:
    """A documented variable nobody reads is dead; an undocumented one is a trap.

    The example is the only checklist a first deployment has, so both directions
    are checked: the compose and the settings the scripts read must name exactly
    the variables the file offers. `${VAR:-default}` needs no entry.
    """
    documented = _env_names()
    compose_text = COMPOSE.read_text(encoding="utf-8")
    undocumented = sorted(
        {
            match.group(1)
            for match in _VARIABLE.finditer(compose_text)
            if ":-" not in match.group(0) and match.group(1) not in documented
        }
    )
    assert undocumented == [], f"compose reads undefined variables: {undocumented}"

    used = {match.group(1) for match in _VARIABLE.finditer(compose_text)}
    example_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name in SCRIPT_SETTINGS:
        assert name in documented, f"{name} is read by a script but not documented"
    for name in OPTIONAL_SETTINGS:
        assert name in example_text, f"{name} is used by a script but not even mentioned"
    unused = sorted(
        name
        for name in documented
        if name not in used and name not in SCRIPT_SETTINGS and name not in OPTIONAL_SETTINGS
    )
    assert unused == [], f"documented but never read: {unused}"


def test_every_service_rotates_its_logs_and_restarts() -> None:
    """Plan §11.1: 10 MB per file, five files — otherwise the VPS disk fills up."""
    for name, service in _services(_compose()).items():
        assert service.get("restart") == "unless-stopped", name
        logging = service.get("logging")
        assert isinstance(logging, dict), f"{name} has no logging block"
        options = logging.get("options")
        assert isinstance(options, dict), f"{name} has no logging options"
        assert options.get("max-size") == "10m", name
        assert options.get("max-file") == "5", name


def test_the_long_running_services_carry_a_healthcheck() -> None:
    """`nats` has no shell in its image, so it is checked from the host instead."""
    services = _services(_compose())
    for name in ("surrealdb", "pim-core", "pim-frontend", "pim-worker", "object-storage"):
        assert "healthcheck" in services[name], name
    assert "healthcheck" not in services["nats"]


def test_the_database_is_reachable_for_the_migration_runner() -> None:
    """The runner uses `POST /sql`; loopback keeps it out of Traefik's reach."""
    services = _services(_compose())
    ports = services["surrealdb"]["ports"]
    assert isinstance(ports, list)
    assert any("127.0.0.1:8000:8000" in str(port) for port in ports)
    for name in ("pim-core",):
        assert "traefik" in services[name]["networks"]
    for name in ("surrealdb", "nats", "object-storage", "pim-worker"):
        assert "traefik" not in services[name]["networks"], name


def test_the_proxy_network_is_a_deployment_setting() -> None:
    """The file names the network by alias; its real name comes from `.env.vps`."""
    network = _compose()["networks"]["traefik"]
    assert network["external"] is True
    assert network["name"] == "${TRAEFIK_NETWORK}"


def test_the_export_bucket_reaches_frontend_and_worker() -> None:
    """`frontend/exports.py` imports the worker's storage module: both need S3."""
    services = _services(_compose())
    for name in ("pim-frontend", "pim-worker"):
        environment = services[name]["environment"]
        assert isinstance(environment, dict)
        for key in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET"):
            assert key in environment, f"{name} misses {key}"


def test_the_ingest_key_is_the_same_on_both_sides() -> None:
    """The core accepts `x-api-key` only when it is set; the worker writes with it."""
    services = _services(_compose())
    core = services["pim-core"]["environment"]
    worker = services["pim-worker"]["environment"]
    assert isinstance(core, dict) and isinstance(worker, dict)
    assert core["LOG_INGEST_API_KEY"] == worker["LOG_INGEST_API_KEY"] == "${LOG_INGEST_API_KEY}"


def test_the_registry_path_is_lowercase() -> None:
    """Forgejo rejects an image path with upper case, so the example stays lowercase."""
    registry = _env_value("PIM_REGISTRY")
    assert registry == registry.lower()
    assert "CHANGE_ME" not in registry
    assert "/" in registry


def test_the_build_context_excludes_the_heavy_and_the_secret() -> None:
    patterns = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for pattern in ("target", ".venv", ".env.vps", "resources", ".git"):
        assert pattern in patterns, pattern
    # The handbook ships with the frontend image, so it must stay included.
    assert "docs/*" in patterns and "!docs/user" in patterns


def test_migration_files_are_ordered_and_numbered_once() -> None:
    files = sorted(MIGRATIONS.glob("*.surql"))
    assert files, "the migration history needs its baseline"
    names = [path.name for path in files]
    assert names == sorted(names)
    numbers = []
    for name in names:
        match = re.fullmatch(r"(\d{3})_[a-z0-9_]+\.surql", name)
        assert match is not None, f"{name} does not follow NNN_snake_case.surql"
        numbers.append(match.group(1))
    assert len(set(numbers)) == len(numbers), "two migrations share a number"
    assert numbers[0] == "001"


def test_the_vps_scripts_are_strict_bash() -> None:
    for name in VPS_SCRIPTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash\n"), name
        assert "\nset -euo pipefail\n" in text, name
        anchored = '\ncd "$(dirname "$0")/.."\n'
        assert anchored in text, f"{name} does not anchor its working directory"


def test_the_windows_twin_offers_the_same_build() -> None:
    """The images are built on the workstation (`build-images.ps1`), pulled by the server."""
    script = (SCRIPTS / "build-images.ps1").read_text(encoding="utf-8")
    assert "PIM_REGISTRY" in script
    assert "docker" in script
    assert "'core', 'frontend', 'worker'" in script
    assert "pim-$service" in script, "the image name is built from the service list"


def test_the_deploy_script_runs_the_migrations_before_the_application() -> None:
    """Plan §6: migrations first, then the core — and never without the env file."""
    text = (SCRIPTS / "deploy-vps-meadow.sh").read_text(encoding="utf-8")
    assert text.index("run-migration.sh") < text.index("up -d --remove-orphans")
    assert "AUTO_RUN_DB_MIGRATIONS" in text
    assert ".env.vps not found" in text


def test_the_deploy_script_rejects_a_short_signing_secret() -> None:
    """The core and the frontend refuse a short secret; a restart loop is the worse report."""
    text = (SCRIPTS / "deploy-vps-meadow.sh").read_text(encoding="utf-8")
    assert "for name in JWT_SECRET UI_STORAGE_SECRET; do" in text
    assert '"${#value}" -lt 32' in text


def test_the_healthcheck_does_not_curl_an_unpublished_port() -> None:
    """The frontend has no published port, so it is asked inside its container."""
    text = (SCRIPTS / "healthcheck-vps.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        assert not (line.strip().startswith("curl") and "8081" in line), line
    assert "exec -T pim-frontend" in text


@pytest.mark.parametrize("needle", ["POST /auth/password", "auth/login"])
def test_the_bootstrap_script_uses_the_cores_own_password_flow(needle: str) -> None:
    """The core owns the password policy; the script must not write around it."""
    text = (SCRIPTS / "bootstrap-vps-admin.sh").read_text(encoding="utf-8")
    assert needle in text


def test_every_script_that_loads_the_env_file_strips_carriage_returns() -> None:
    """A `.env.vps` written on Windows turns every value into a parser error.

    Measured: with CRLF the SQL of the migration runner and the prelude of the
    restore were rejected with HTTP 400 (`DEFINE NAMESPACE ... meadow\r;`), while
    the shell silently carried the `\r` into headers and file names.
    """
    for name in VPS_SCRIPTS:
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        if ". ./.env.vps" in text or ". <(tr -d" in text:
            assert ". <(tr -d '\\r' <./.env.vps)" in text, f"{name} loads .env.vps with CR"
        assert ". ./.env.vps" not in text, name


def test_the_restore_does_not_use_surreal_import() -> None:
    """`surreal import` fails on every real export size and refuses >4 MB bodies.

    Measured against this image: 100 statements succeed in 3 s, a 0.6 MB batch
    (9 500 statements) fails after 32 s with `error sending request`, and the
    production-sized file never arrives. The restore replays chunks through
    `POST /sql` instead, which reports a status per statement.
    """
    text = (SCRIPTS / "restore-vps-db.sh").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "/surreal import" not in line, line
    assert "split -C" in text
    assert '"status":"ERR"' in text, "a failed statement has to stop the restore"

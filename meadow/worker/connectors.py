"""Connector configuration loader: reads persisted connectors from the core.

Connector configuration lives in the `connector` table (managed via the core's
`/connectors` API and later the admin UI). Environment variables only seed the
initial value; the database is the durable source of truth. The worker loads
enabled connectors through `GET /connectors` using the machine-to-machine API
key and can reload them at runtime (live reload).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from pydantic import ValidationError

from meadow.worker.http import loopback_host
from meadow.worker.webhooks import WebhookCoalesce, WebhookConfig, WebhookFilter

logger = logging.getLogger(__name__)

#: Connector type managed by the webhook outbound connector.
CONNECTOR_TYPE_WEBHOOK = "webhook"


async def fetch_webhook_configs(core_url: str, api_key: str | None) -> list[WebhookConfig]:
    """Loads all enabled `webhook` connectors from the core.

    Raises on transport/HTTP failure so the caller can decide whether to keep
    the current configuration (live reload) or fall back to the environment
    bootstrap (initial load). Returns an empty list when no connector exists.
    """
    core_url = loopback_host(core_url)
    url = f"{core_url.rstrip('/')}/connectors?type={CONNECTOR_TYPE_WEBHOOK}&enabled=true"
    headers = {"x-api-key": api_key} if api_key else {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        rows = response.json()

    configs: list[WebhookConfig] = []
    for row in rows:
        config = _webhook_config_from_row(row)
        if config is not None:
            configs.append(config)
    return configs


def env_bootstrap_config() -> WebhookConfig | None:
    """Builds the legacy environment-based webhook config, or `None` if unset.

    `WEBHOOK_URL` only bootstraps the initial value; the persisted `connector`
    table is the durable source of truth.
    """
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        return None
    return WebhookConfig(
        key="env",
        url=webhook_url,
        method=os.environ.get("WEBHOOK_METHOD", "POST"),
        headers=json.loads(os.environ.get("WEBHOOK_HEADERS", "{}")),
        filter=WebhookFilter(
            event_types=_csv(os.environ.get("WEBHOOK_FILTER_EVENT_TYPES")),
            object_types=_csv(os.environ.get("WEBHOOK_FILTER_OBJECT_TYPES")),
            attribute_changes=_csv(os.environ.get("WEBHOOK_FILTER_ATTRIBUTES")),
        ),
        coalesce=WebhookCoalesce(
            enabled=_env_bool(os.environ.get("WEBHOOK_COALESCE_ENABLED")),
            window_seconds=float(os.environ.get("WEBHOOK_COALESCE_WINDOW_S", "30")),
        ),
    )


async def load_webhook_configs(core_url: str, api_key: str | None) -> list[WebhookConfig]:
    """Loads webhook connectors; the database is authoritative, env bootstraps.

    Raises on transport failure (the live-reload caller keeps the current set
    on failure). When no connector exists in the database, the environment
    bootstrap config is returned instead.
    """
    configs = await fetch_webhook_configs(core_url, api_key)
    if configs:
        return configs
    bootstrap = env_bootstrap_config()
    return [bootstrap] if bootstrap else []


def _webhook_config_from_row(row: dict[str, Any]) -> WebhookConfig | None:
    """Builds a `WebhookConfig` from a `connector` record, or `None` if invalid."""
    key = str(row.get("name") or row.get("id") or "webhook")
    parameters = row.get("parameters")
    if not isinstance(parameters, dict):
        logger.warning("webhook connector %s has no parameters; skipped", key)
        return None
    url = parameters.get("url")
    if not isinstance(url, str) or not url.strip():
        logger.warning("webhook connector %s has no url; skipped", key)
        return None

    filter_params = parameters.get("filter")
    if not isinstance(filter_params, dict):
        filter_params = {}
    coalesce_params = parameters.get("coalesce")
    if not isinstance(coalesce_params, dict):
        coalesce_params = {}
    retry_params = parameters.get("retry_policy")
    if not isinstance(retry_params, dict):
        retry_params = {}

    payload: dict[str, Any] = {
        "key": key,
        "url": url,
        "method": parameters.get("method", "POST"),
        "headers": parameters.get("headers", {}),
        "filter": {
            "event_types": _string_list(filter_params.get("event_types")),
            "object_types": _string_list(filter_params.get("object_types")),
            "attribute_changes": _string_list(filter_params.get("attribute_changes")),
        },
        "coalesce": {
            "enabled": bool(coalesce_params.get("enabled", False)),
            "window_seconds": coalesce_params.get("window_seconds", 30.0),
        },
        "retry": retry_params,
        "timeout_s": parameters.get("timeout_s", 30.0),
    }
    try:
        return WebhookConfig.model_validate(payload)
    except ValidationError:  # malformed connector rows are skipped, not fatal
        logger.exception("invalid webhook connector %s; skipped", key)
        return None


def _string_list(value: Any) -> list[str]:
    """Normalizes a JSON array of strings (or a comma-separated string)."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [token.strip() for token in value.split(",") if token.strip()]
    return []


def _csv(value: str | None) -> list[str]:
    """Splits a comma-separated env value into a trimmed list (empty tokens dropped)."""
    if not value:
        return []
    return [token.strip() for token in value.split(",") if token.strip()]


def _env_bool(value: str | None) -> bool:
    """Parses a boolean environment variable."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}

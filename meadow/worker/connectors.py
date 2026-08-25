"""Connector configuration loader: reads persisted connectors from the core.

Connector configuration lives in the `connector` table (managed via the core's
`/connectors` API and later the admin UI). Environment variables only seed the
initial value; the database is the durable source of truth. The worker loads
enabled connectors at startup through `GET /connectors` using the machine-to-
machine API key.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from meadow.worker.webhooks import WebhookConfig

logger = logging.getLogger(__name__)

#: Connector type managed by the webhook outbound connector.
CONNECTOR_TYPE_WEBHOOK = "webhook"


async def fetch_webhook_configs(core_url: str, api_key: str | None) -> list[WebhookConfig]:
    """Loads all enabled `webhook` connectors from the core.

    Returns an empty list when the core is unreachable or no connectors are
    configured (the caller falls back to the environment-based bootstrap
    configuration).
    """
    url = f"{core_url.rstrip('/')}/connectors?type={CONNECTOR_TYPE_WEBHOOK}&enabled=true"
    headers = {"x-api-key": api_key} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            rows = response.json()
    except Exception:  # noqa: BLE001 - the worker still starts without connectors
        logger.exception("failed to load webhook connectors from %s", url)
        return []

    configs: list[WebhookConfig] = []
    for row in rows:
        config = _webhook_config_from_row(row)
        if config is not None:
            configs.append(config)
    return configs


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
        "timeout_s": parameters.get("timeout_s", 30.0),
    }
    try:
        return WebhookConfig.model_validate(payload)
    except Exception:  # noqa: BLE001 - malformed connector rows are skipped, not fatal
        logger.exception("invalid webhook connector %s; skipped", key)
        return None


def _string_list(value: Any) -> list[str]:
    """Normalizes a JSON array of strings (or a comma-separated string)."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [token.strip() for token in value.split(",") if token.strip()]
    return []

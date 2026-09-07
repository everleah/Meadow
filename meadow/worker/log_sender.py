"""Curated log sender: batches worker logs to the core's `/system/logs`.

The worker produces curated operational logs (rule failures, lifecycle events)
that belong in the core's `log_entry` table, visible in the admin UI. This
module buffers them (bounded, drop-oldest) and flushes them in batches,
non-blocking, while periodically refreshing the logging configuration
(`log_persist_level`, `log_persist_categories`) from `/system/settings`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

import httpx

from meadow.worker.http import loopback_host

logger = logging.getLogger(__name__)

LEVEL_ORDER: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

DEFAULT_MAX_BUFFER_SIZE = 1000
DEFAULT_FLUSH_INTERVAL_S = 5.0
DEFAULT_CONFIG_REFRESH_INTERVAL_S = 60.0


class LogSender:
    """Non-blocking, bounded buffer of curated logs, flushed in batches.

    Drops the oldest entry when the buffer is full. The persist level and the
    allowed categories are applied when enqueueing; they are refreshed from
    the core's `/system/settings` on a schedule.
    """

    def __init__(
        self,
        core_url: str,
        *,
        api_key: str | None = None,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        config_refresh_interval_s: float = DEFAULT_CONFIG_REFRESH_INTERVAL_S,
    ) -> None:
        self._core_url = loopback_host(core_url).rstrip("/")
        self._api_key = api_key
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_buffer_size)
        self._flush_interval_s = flush_interval_s
        self._config_refresh_interval_s = config_refresh_interval_s
        self._persist_level = "WARN"
        self._persist_categories = {"app"}
        self._client: httpx.AsyncClient | None = None

    def enqueue(
        self,
        *,
        service: str,
        level: str,
        category: str,
        message: str,
        trace_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Queues a curated log entry (non-blocking; drops the oldest when full)."""
        if not self._accepts(level, category):
            return
        self._buffer.append(
            {
                "service": service,
                "level": level,
                "category": category,
                "message": message,
                "trace_id": trace_id,
                "context": context,
            }
        )

    def apply_config(self, settings: dict[str, Any]) -> None:
        """Applies logging configuration read from `/system/settings`."""
        level = settings.get("log_persist_level")
        if isinstance(level, str) and level in LEVEL_ORDER:
            self._persist_level = level
        categories = settings.get("log_persist_categories")
        if isinstance(categories, list):
            self._persist_categories = {str(category) for category in categories}

    def _accepts(self, level: str, category: str) -> bool:
        if category not in self._persist_categories:
            return False
        return LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self._persist_level, 0)

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def run(self) -> None:
        """Flushes logs and refreshes configuration until cancelled."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            self._client = client
            since_flush = 0.0
            since_config = 0.0
            while True:
                await asyncio.sleep(1.0)
                since_flush += 1.0
                since_config += 1.0
                if since_config >= self._config_refresh_interval_s:
                    since_config = 0.0
                    await self._refresh_config()
                if since_flush >= self._flush_interval_s:
                    since_flush = 0.0
                    await self._flush()

    async def _flush(self) -> None:
        if not self._buffer or self._client is None:
            return
        entries = [self._buffer.popleft() for _ in range(len(self._buffer))]
        try:
            response = await self._client.post(
                f"{self._core_url}/system/logs",
                json=entries,
                headers=self._headers(),
            )
            if response.status_code >= 300:
                logger.warning(
                    "log flush rejected: %s %s",
                    response.status_code,
                    response.text[:200],
                )
        except httpx.HTTPError as exc:
            logger.warning("log flush failed: %s", exc)
            self._requeue(entries)
        except Exception as exc:  # noqa: BLE001 - never crash the flush loop
            logger.warning("log flush failed unexpectedly: %s", exc)
            self._requeue(entries)

    def _requeue(self, entries: list[dict[str, Any]]) -> None:
        """Puts entries back at the front, preserving FIFO order (drop-oldest)."""
        for entry in reversed(entries):
            self._buffer.appendleft(entry)

    async def _refresh_config(self) -> None:
        if self._client is None:
            return
        try:
            response = await self._client.get(
                f"{self._core_url}/system/settings",
                headers=self._headers(),
            )
            if response.status_code == 200:
                self.apply_config(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("log config refresh failed: %s", exc)


def default_sender(core_url: str, api_key: str | None) -> LogSender:
    """Builds a log sender with default settings (factory for tests/entry)."""
    return LogSender(core_url, api_key=api_key)


__all__ = ["LogSender", "LEVEL_ORDER", "default_sender"]

"""Entry point: `python -m meadow.worker` starts the rule-evaluation service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from meadow.worker.log_sender import LogSender
from meadow.worker.rules_service import serve

logging.basicConfig(level=logging.INFO)
# httpx logs every request at INFO; keep only warnings to avoid noise.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main() -> None:
    nats_url = os.environ.get("NATS_URL", "nats://localhost:4222")
    subject = os.environ.get("RULES_EVALUATE_SUBJECT", "rules.evaluate")
    compile_subject = os.environ.get("RULES_COMPILE_SUBJECT", "rules.compile")
    core_url = os.environ.get("PIM_CORE_URL", "http://localhost:8080")
    api_key = os.environ.get("LOG_INGEST_API_KEY") or None
    flush_interval = float(os.environ.get("LOG_FLUSH_INTERVAL_S", "5.0"))
    config_refresh = float(os.environ.get("LOG_CONFIG_REFRESH_INTERVAL_S", "60.0"))

    log_sender = LogSender(
        core_url,
        api_key=api_key,
        flush_interval_s=flush_interval,
        config_refresh_interval_s=config_refresh,
    )
    sender_task = asyncio.create_task(log_sender.run())
    try:
        await serve(nats_url, subject, compile_subject, log_sender=log_sender)
    finally:
        sender_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender_task


if __name__ == "__main__":
    asyncio.run(main())

"""Entry point: `python -m meadow.worker` starts the rule-evaluation and job services."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from meadow.worker.business_rules import build_business_rule_handler
from meadow.worker.connectors import load_webhook_configs
from meadow.worker.exports import build_export_handler
from meadow.worker.imports import build_import_handler
from meadow.worker.jobs import serve_jobs
from meadow.worker.log_sender import LogSender
from meadow.worker.rules_service import serve
from meadow.worker.webhooks import serve_webhooks_dynamic

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

    # Job consumer configuration.
    jobs_subject = os.environ.get("JOBS_SUBJECT", "jobs.*")
    jobs_stream = os.environ.get("JOBS_STREAM", "JOBS")
    jobs_durable = os.environ.get("JOBS_CONSUMER_DURABLE", "job-worker")
    jobs_ack_wait = float(os.environ.get("JOBS_ACK_WAIT_S", "300"))
    jobs_max_deliver = int(os.environ.get("JOBS_MAX_DELIVER", "5"))
    jobs_concurrency = int(os.environ.get("JOBS_CONCURRENCY", "4"))

    log_sender = LogSender(
        core_url,
        api_key=api_key,
        flush_interval_s=flush_interval,
        config_refresh_interval_s=config_refresh,
    )
    sender_task = asyncio.create_task(log_sender.run())
    rules_task = asyncio.create_task(
        serve(nats_url, subject, compile_subject, log_sender=log_sender)
    )
    jobs_task = asyncio.create_task(
        serve_jobs(
            nats_url,
            core_url,
            api_key=api_key,
            subject=jobs_subject,
            stream=jobs_stream,
            durable=jobs_durable,
            ack_wait=jobs_ack_wait,
            max_deliver=jobs_max_deliver,
            concurrency=jobs_concurrency,
            handlers={
                "business_rule": build_business_rule_handler(core_url, api_key=api_key),
                "inbound_import": build_import_handler(core_url, api_key=api_key),
                "outbound_export": build_export_handler(core_url, api_key=api_key),
            },
            log_sender=log_sender,
        )
    )

    # Webhook outbound connector: DB-backed config with an env fallback for
    # the initial value. The persisted `connector` table is the durable source
    # of truth; `WEBHOOK_URL` only bootstraps when no connector exists yet.
    # The consumer live-reloads on connector changes (no worker restart).
    webhook_reload = float(os.environ.get("WEBHOOK_RELOAD_INTERVAL_S", "30"))
    tasks: list[asyncio.Task] = [rules_task, jobs_task]
    tasks.append(
        asyncio.create_task(
            serve_webhooks_dynamic(
                nats_url,
                lambda: load_webhook_configs(core_url, api_key=api_key),
                reload_interval_s=webhook_reload,
                log_sender=log_sender,
            )
        )
    )

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in (sender_task, *tasks):
            task.cancel()
        for task in (sender_task, *tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    asyncio.run(main())

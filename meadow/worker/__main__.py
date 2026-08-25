"""Entry point: `python -m meadow.worker` starts the rule-evaluation and job services."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

from meadow.worker.business_rules import build_business_rule_handler
from meadow.worker.exports import build_export_handler
from meadow.worker.imports import build_import_handler
from meadow.worker.jobs import serve_jobs
from meadow.worker.log_sender import LogSender
from meadow.worker.rules_service import serve
from meadow.worker.webhooks import WebhookConfig, WebhookFilter, serve_webhooks

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

    # Webhook outbound connector: optional, enabled by `WEBHOOK_URL`.
    tasks: list[asyncio.Task] = [rules_task, jobs_task]
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        webhook_config = WebhookConfig(
            url=webhook_url,
            method=os.environ.get("WEBHOOK_METHOD", "POST"),
            headers=json.loads(os.environ.get("WEBHOOK_HEADERS", "{}")),
            filter=WebhookFilter(
                event_types=_csv(os.environ.get("WEBHOOK_FILTER_EVENT_TYPES")),
                object_types=_csv(os.environ.get("WEBHOOK_FILTER_OBJECT_TYPES")),
                attribute_changes=_csv(os.environ.get("WEBHOOK_FILTER_ATTRIBUTES")),
            ),
        )
        tasks.append(
            asyncio.create_task(serve_webhooks(nats_url, webhook_config, log_sender=log_sender))
        )

    try:
        await asyncio.gather(*tasks)
    finally:
        for task in (sender_task, *tasks):
            task.cancel()
        for task in (sender_task, *tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _csv(value: str | None) -> list[str]:
    """Splits a comma-separated env value into a trimmed list (empty tokens dropped)."""
    if not value:
        return []
    return [token.strip() for token in value.split(",") if token.strip()]


if __name__ == "__main__":
    asyncio.run(main())

"""Entry point: `python -m meadow.worker` starts the rule-evaluation service."""

from __future__ import annotations

import asyncio
import logging
import os

from meadow.worker.rules_service import serve

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    nats_url = os.environ.get("NATS_URL", "nats://localhost:4222")
    subject = os.environ.get("RULES_EVALUATE_SUBJECT", "rules.evaluate")
    compile_subject = os.environ.get("RULES_COMPILE_SUBJECT", "rules.compile")
    await serve(nats_url, subject, compile_subject)


if __name__ == "__main__":
    asyncio.run(main())

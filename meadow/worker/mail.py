"""Minimal SMTP sender for the worker.

Configuration is environment-driven so no credentials are persisted in the
database:

- ``SMTP_HOST`` — SMTP server host; when unset, sending is disabled.
- ``SMTP_PORT`` — SMTP port (default ``1025``, MailHog's default).
- ``SMTP_FROM`` — ``From`` address (default ``meadow@localhost``).
- ``SMTP_USERNAME`` / ``SMTP_PASSWORD`` — optional credentials.
- ``SMTP_STARTTLS`` — ``1``/``true`` to upgrade the connection with STARTTLS.

In local development the ``mailhog`` service in ``docker-compose.dev.yml``
catches all mail (SMTP on 1025, web UI/API on 8025).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

logger = logging.getLogger(__name__)

#: Split a `type/subtype` content type into the two MIME parts aiosmtplib needs.
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class SmtpConfig:
    """Resolved SMTP connection settings."""

    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = None
    starttls: bool = False

    @classmethod
    def from_env(cls) -> SmtpConfig | None:
        """Reads the SMTP settings from the environment (``None`` = disabled)."""
        host = os.environ.get("SMTP_HOST")
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "1025")),
            sender=os.environ.get("SMTP_FROM", "meadow@localhost"),
            username=os.environ.get("SMTP_USERNAME") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            starttls=os.environ.get("SMTP_STARTTLS", "").lower() in _TRUTHY,
        )


async def send_with_attachment(
    config: SmtpConfig,
    recipients: list[str],
    subject: str,
    body: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    """Sends a plain-text e-mail with one attachment.

    Raises on transport failure so the caller can record the error.
    """
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    maintype, _, subtype = content_type.partition("/")
    message.add_attachment(
        content,
        maintype=maintype or "application",
        subtype=subtype or "octet-stream",
        filename=filename,
    )
    await aiosmtplib.send(
        message,
        hostname=config.host,
        port=config.port,
        username=config.username,
        password=config.password,
        start_tls=config.starttls,
    )
    logger.info("sent export e-mail to %s", ", ".join(recipients))


__all__ = ["SmtpConfig", "send_with_attachment"]

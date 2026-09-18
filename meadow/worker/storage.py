"""S3/MinIO object storage for export results (environment-driven).

Large exports are archived as objects so `job_run.result` stays small; the core
then redirects the download endpoint to a presigned URL. Configuration:

- ``S3_ENDPOINT_URL`` — S3/MinIO endpoint (e.g. ``http://127.0.0.1:9000``).
- ``S3_ACCESS_KEY_ID`` / ``S3_SECRET_ACCESS_KEY`` — credentials.
- ``S3_BUCKET`` — target bucket (created on demand when missing).
- ``S3_REGION`` — region (default ``us-east-1``).
- ``S3_PRESIGN_EXPIRY`` — download URL lifetime in seconds (default 7 days,
  the SigV4 maximum).

When ``S3_ENDPOINT_URL`` or ``S3_BUCKET`` is unset, archiving is disabled and
the export result stays inline.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

#: SigV4 presigned URLs are valid for at most seven days.
_MAX_PRESIGN_SECONDS = 604800


@dataclass
class S3Config:
    """Resolved S3/MinIO connection settings."""

    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    presign_expiry: int = _MAX_PRESIGN_SECONDS

    @classmethod
    def from_env(cls) -> S3Config | None:
        """Reads the S3 settings from the environment (``None`` = disabled)."""
        endpoint = os.environ.get("S3_ENDPOINT_URL")
        bucket = os.environ.get("S3_BUCKET")
        if not endpoint or not bucket:
            return None
        expiry = int(os.environ.get("S3_PRESIGN_EXPIRY", str(_MAX_PRESIGN_SECONDS)))
        return cls(
            endpoint_url=endpoint,
            access_key=os.environ.get("S3_ACCESS_KEY_ID", ""),
            secret_key=os.environ.get("S3_SECRET_ACCESS_KEY", ""),
            bucket=bucket,
            region=os.environ.get("S3_REGION", "us-east-1"),
            presign_expiry=min(max(expiry, 60), _MAX_PRESIGN_SECONDS),
        )

    def _client(self):
        """Builds a path-style, SigV4 boto3 S3 client (MinIO-compatible)."""
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )


def _ensure_bucket_sync(config: S3Config, client) -> None:
    """Creates the bucket when the head request says it is missing or forbidden."""
    try:
        client.head_bucket(Bucket=config.bucket)
    except ClientError:  # create when missing/forbidden head
        try:
            client.create_bucket(Bucket=config.bucket)
        except ClientError as exc:  # may already exist / race
            logger.debug("create_bucket %s: %s", config.bucket, exc)


def _upload_sync(config: S3Config, key: str, content: bytes, content_type: str) -> None:
    client = config._client()
    _ensure_bucket_sync(config, client)
    client.put_object(Bucket=config.bucket, Key=key, Body=content, ContentType=content_type)


def _presign_sync(config: S3Config, key: str, content_type: str, download_name: str) -> str:
    client = config._client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": config.bucket,
            "Key": key,
            "ResponseContentType": content_type,
            "ResponseContentDisposition": f'attachment; filename="{download_name}"',
        },
        ExpiresIn=config.presign_expiry,
    )


async def archive_export(
    config: S3Config,
    key: str,
    content: bytes,
    content_type: str,
    download_name: str,
) -> dict[str, object]:
    """Uploads `content` and returns the archive metadata (incl. a presigned URL)."""
    await asyncio.to_thread(_upload_sync, config, key, content, content_type)
    url = await asyncio.to_thread(_presign_sync, config, key, content_type, download_name)
    return {
        "bucket": config.bucket,
        "key": key,
        "size": len(content),
        "url": url,
        "expires_in": config.presign_expiry,
    }


__all__ = ["S3Config", "archive_export"]

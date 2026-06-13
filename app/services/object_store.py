"""Lazy-init AWS S3 client for encrypted screenshot storage.

Feature-flagged via S3_ENABLED. When disabled every function is a no-op,
byte-for-byte preserving current filesystem-only behaviour.

Designed to never break the exam — all S3 calls are wrapped in try/except
with logging. The local write already succeeded before this runs.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

_log = logging.getLogger("object_store")


# ── Lazy client ──────────────────────────────────────────────────────────────

_client = None


def _make_client():
    """Build and return the boto3 S3 client. Idempotent via module cache."""
    global _client
    if _client is not None:
        return _client
    if not is_enabled():
        _client = False
        return _client

    try:
        import boto3
    except ImportError:
        # Optional dependency — if boto3 isn't installed, S3 degrades to
        # disabled rather than crashing the screenshot path.
        _log.warning("S3_ENABLED but boto3 is not installed — S3 disabled")
        _client = False
        return _client

    session = boto3.Session(
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        region_name=os.environ.get("S3_REGION", "ap-south-1"),
    )
    _client = session.client("s3")
    _log.info("S3 client initialized for bucket %s [%s]",
              os.environ.get("S3_BUCKET", ""), os.environ.get("S3_REGION", "ap-south-1"))
    return _client


def reset_client():
    """Clear the cached client (used in tests)."""
    global _client
    _client = None


# ── Public helpers ────────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """S3 feature-flag: true when S3_ENABLED env var is 1/true/yes."""
    return os.environ.get("S3_ENABLED", "").lower() in ("1", "true", "yes")


def _encryption_args() -> dict:
    """Server-side-encryption kwargs for put_object.

    Prefer SSE-KMS with our customer-managed key when ``S3_KMS_KEY_ID`` is set
    — that gives a CloudTrail decrypt audit trail and key control/revocation,
    the defensible choice for student face images under DPDP. Fall back to
    SSE-S3 (AES256) when no key is configured so the path still works.

    NOTE: an explicit ServerSideEncryption on the request OVERRIDES the bucket's
    default encryption, so this is the single place the screenshot encryption
    mode is actually decided. The app's IAM user therefore needs
    kms:GenerateDataKey (writes) + kms:Decrypt (reads) on the key.
    """
    kms_key = os.environ.get("S3_KMS_KEY_ID", "").strip()
    if kms_key:
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key}
    return {"ServerSideEncryption": "AES256"}


def upload_screenshot(s3_key: str, data: bytes, content_type: str = "image/jpeg") -> bool:
    """Upload *data* to S3 at *s3_key*, encrypted with SSE-KMS (if
    ``S3_KMS_KEY_ID`` is set) or SSE-S3 otherwise.

    Key scheme mirrors the local path: ``{teacher_id}/{roll}/{filename}``.
    Never raises — returns True on success, False + log on failure.
    """
    if not is_enabled():
        return False
    client = _make_client()
    if not client:
        return False
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        _log.warning("S3 enabled but S3_BUCKET not set — skipping upload")
        return False
    try:
        client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=data,
            ContentType=content_type,
            **_encryption_args(),
        )
        return True
    except Exception:
        _log.exception("S3 upload failed for key=%s", s3_key)
        return False


def fetch_screenshot(s3_key: str) -> Optional[bytes]:
    """Fetch *s3_key* from S3. Returns bytes or None on failure.

    Used as fallback when a screenshot is not found on local disk (S3 is
    the durable system-of-record, local filesystem is a short-lived cache).
    """
    if not is_enabled():
        return None
    client = _make_client()
    if not client:
        return None
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        _log.warning("S3 enabled but S3_BUCKET not set — skipping fetch")
        return None
    try:
        resp = client.get_object(Bucket=bucket, Key=s3_key)
        return resp["Body"].read()
    except Exception as exc:
        # boto3 raises ClientError for missing keys; any other
        # transport error (timeout, access-denied) is also caught here.
        exc_name = type(exc).__name__
        if exc_name == "ClientError":
            return None
        _log.exception("S3 fetch failed for key=%s (%s)", s3_key, exc_name)
        return None


def exists(s3_key: str) -> bool:
    """True if *s3_key* is present in the bucket. Never raises.

    Used by the local-cache sweep to confirm a durable S3 copy exists
    before evicting a local file — so a failed upload's only copy is
    never deleted before its retention period.
    """
    if not is_enabled():
        return False
    client = _make_client()
    if not client:
        return False
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return False
    try:
        client.head_object(Bucket=bucket, Key=s3_key)
        return True
    except Exception:
        return False


def delete_prefix(prefix: str) -> int:
    """Delete all objects under *prefix* (used during account deletion).

    Returns count of deleted objects or 0 on failure.
    """
    if not is_enabled():
        return 0
    client = _make_client()
    if not client:
        return 0
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return 0
    try:
        paginator = client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            keys = [{"Key": obj["Key"]} for obj in objects]
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
        return deleted
    except Exception:
        _log.exception("S3 delete_prefix failed for prefix=%s", prefix)
        return 0

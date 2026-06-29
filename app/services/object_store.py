"""Lazy-init AWS S3 client for encrypted screenshot storage.

Feature-flagged via S3_ENABLED. When disabled every function is a no-op,
byte-for-byte preserving current filesystem-only behaviour.

Designed to never break the exam — all S3 calls are wrapped in try/except
with logging. The local write already succeeded before this runs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

_log = logging.getLogger("object_store")

# A KMS key id is a UUID, a key ARN, or an alias ("alias/…"). Anything else
# (e.g. a 32-byte hex secret pasted into the wrong env var) makes S3 reject
# EVERY put_object with KMS.NotFoundException — silently breaking all evidence
# uploads. We validate the shape and fall back to SSE-S3 rather than fail open.
_KMS_KEY_RE = re.compile(
    r"^(?:"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # key UUID
    r"|arn:aws[\w-]*:kms:[^:]+:\d{12}:(?:key|alias)/.+"  # key/alias ARN
    r"|alias/.+"  # bare alias
    r")$"
)


def _kms_key_id_is_valid(key: str) -> bool:
    """True only for a plausibly-valid KMS key id (UUID / ARN / alias)."""
    return bool(_KMS_KEY_RE.match(key))


# ── Lazy client ──────────────────────────────────────────────────────────────

_client = None


def _make_client() -> Any:
    """Build and return the boto3 S3 client. Idempotent via module cache."""
    global _client
    if _client is not None:
        return _client  # type: ignore[unreachable]
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
    if kms_key and _kms_key_id_is_valid(kms_key):
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key}
    if kms_key:
        # Misconfigured key — don't fail every upload. Fall back to SSE-S3
        # (still encrypted at rest) and warn LOUDLY. Log only a short prefix so
        # we never echo a (possibly secret) value into logs/Sentry in full.
        _log.error(
            "S3_KMS_KEY_ID is not a valid KMS key id (prefix=%r…) — falling back "
            "to SSE-S3 (AES256). Set a real key UUID/ARN/alias or unset the var.",
            kms_key[:6],
        )
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


def fetch_screenshot(s3_key: str) -> bytes | None:
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

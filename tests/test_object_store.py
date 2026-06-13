"""Tests for app/services/object_store.py — S3 encrypted screenshot storage."""
import os
import pytest
from unittest.mock import MagicMock, patch

from app.services.object_store import (
    is_enabled,
    upload_screenshot,
    fetch_screenshot,
    delete_prefix,
    reset_client,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Clean S3 env before each test. delenv on ALL three vars — leaking
    S3_BUCKET/S3_REGION (via raw os.environ) into later test files flipped
    S3 'on' for unrelated tests and broke CI."""
    for var in ("S3_ENABLED", "S3_BUCKET", "S3_REGION"):
        monkeypatch.delenv(var, raising=False)
    reset_client()


@pytest.fixture
def mock_s3(monkeypatch):
    """Create a mock boto3 S3 client patched into object_store.

    Uses monkeypatch for env + the client patch so everything is auto-
    restored at test end — no raw os.environ writes that leak across files.
    """
    client = MagicMock()
    monkeypatch.setattr("app.services.object_store._make_client", lambda: client)
    monkeypatch.setenv("S3_ENABLED", "1")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("S3_REGION", "ap-south-1")
    reset_client()
    yield client
    reset_client()


# ── is_enabled ────────────────────────────────────────────────────────────────


class TestIsEnabled:
    def test_false_when_unset(self):
        assert is_enabled() is False

    def test_false_when_0(self, monkeypatch):
        monkeypatch.setenv("S3_ENABLED", "0")
        assert is_enabled() is False

    def test_false_when_empty(self, monkeypatch):
        monkeypatch.setenv("S3_ENABLED", "")
        assert is_enabled() is False

    def test_true_when_1(self, monkeypatch):
        monkeypatch.setenv("S3_ENABLED", "1")
        assert is_enabled() is True

    def test_true_when_true(self, monkeypatch):
        monkeypatch.setenv("S3_ENABLED", "true")
        assert is_enabled() is True

    def test_true_when_yes(self, monkeypatch):
        monkeypatch.setenv("S3_ENABLED", "yes")
        assert is_enabled() is True


# ── upload_screenshot ─────────────────────────────────────────────────────────


class TestUploadScreenshot:
    def test_noop_when_disabled(self):
        assert upload_screenshot("key", b"data") is False

    def test_noop_when_no_bucket(self, monkeypatch):
        monkeypatch.setenv("S3_ENABLED", "1")
        reset_client()
        # Deliberately leave S3_BUCKET unset
        assert upload_screenshot("key", b"data") is False

    def test_success(self, mock_s3):
        mock_s3.put_object.return_value = {}
        result = upload_screenshot("t1/roll/frame.jpg", b"\xff\xd8", "image/jpeg")
        assert result is True
        mock_s3.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="t1/roll/frame.jpg",
            Body=b"\xff\xd8",
            ContentType="image/jpeg",
            ServerSideEncryption="AES256",
        )

    def test_failure_logged(self, mock_s3):
        mock_s3.put_object.side_effect = Exception("Network error")
        result = upload_screenshot("t1/roll/frame.jpg", b"data")
        assert result is False

    def test_uses_sse_kms_when_key_configured(self, mock_s3, monkeypatch):
        # When S3_KMS_KEY_ID is set, the upload must use SSE-KMS with that key
        # (an explicit ServerSideEncryption overrides the bucket default, so
        # this is the only thing that makes the customer-managed key actually
        # encrypt the screenshots).
        key = "arn:aws:kms:ap-south-1:900360460966:key/abc-123"
        monkeypatch.setenv("S3_KMS_KEY_ID", key)
        mock_s3.put_object.return_value = {}
        assert upload_screenshot("t1/roll/frame.jpg", b"\xff\xd8") is True
        kwargs = mock_s3.put_object.call_args.kwargs
        assert kwargs["ServerSideEncryption"] == "aws:kms"
        assert kwargs["SSEKMSKeyId"] == key

    def test_falls_back_to_sse_s3_without_kms_key(self, mock_s3, monkeypatch):
        # No S3_KMS_KEY_ID → SSE-S3 (AES256), unchanged behaviour.
        monkeypatch.delenv("S3_KMS_KEY_ID", raising=False)
        mock_s3.put_object.return_value = {}
        assert upload_screenshot("t1/roll/frame.jpg", b"\xff\xd8") is True
        kwargs = mock_s3.put_object.call_args.kwargs
        assert kwargs["ServerSideEncryption"] == "AES256"
        assert "SSEKMSKeyId" not in kwargs


# ── fetch_screenshot ──────────────────────────────────────────────────────────


class TestFetchScreenshot:
    def test_noop_when_disabled(self):
        assert fetch_screenshot("key") is None

    def test_success(self, mock_s3):
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"image-data")}
        result = fetch_screenshot("t1/roll/frame.jpg")
        assert result == b"image-data"
        mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="t1/roll/frame.jpg")

    def test_not_found(self, mock_s3):
        mock_s3.get_object.side_effect = type("ClientError", (Exception,), {})()
        result = fetch_screenshot("t1/roll/gone.jpg")
        assert result is None

    def test_failure_logged(self, mock_s3):
        mock_s3.get_object.side_effect = Exception("Timeout")
        result = fetch_screenshot("t1/roll/frame.jpg")
        assert result is None


# ── delete_prefix ─────────────────────────────────────────────────────────────


class TestDeletePrefix:
    def test_noop_when_disabled(self):
        assert delete_prefix("t1/") == 0

    def test_deletes_objects(self, mock_s3):
        mock_s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "t1/roll/f1.jpg"}, {"Key": "t1/roll/f2.jpg"}]}
        ]
        result = delete_prefix("t1/")
        assert result == 2
        mock_s3.delete_objects.assert_called_once_with(
            Bucket="test-bucket",
            Delete={"Objects": [{"Key": "t1/roll/f1.jpg"}, {"Key": "t1/roll/f2.jpg"}]},
        )

    def test_empty_prefix(self, mock_s3):
        mock_s3.get_paginator.return_value.paginate.return_value = [{}]
        result = delete_prefix("t2/")
        assert result == 0

    def test_failure_logged(self, mock_s3):
        mock_s3.get_paginator.side_effect = Exception("API error")
        result = delete_prefix("t1/")
        assert result == 0

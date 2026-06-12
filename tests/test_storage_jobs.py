"""Tests for app/jobs/storage_jobs.py."""
import os
import tempfile
from unittest.mock import patch

import pytest

from app.jobs.storage_jobs import upload_screenshot_job


class TestUploadScreenshotJob:
    def test_uploads_from_disk(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            f.write(b"\xff\xd8")
            local_path = f.name
        try:
            with patch("app.services.object_store.upload_screenshot", return_value=True) as mock_up:
                result = upload_screenshot_job(s3_key="t1/r/f.jpg", local_path=local_path)
            assert result["ok"] is True
            assert result["s3_key"] == "t1/r/f.jpg"
            mock_up.assert_called_once_with(s3_key="t1/r/f.jpg", data=b"\xff\xd8", content_type="image/jpeg")
        finally:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    def test_file_not_found(self):
        result = upload_screenshot_job(s3_key="t1/r/gone.jpg", local_path="/nonexistent/file.jpg")
        assert result["ok"] is False
        assert result["s3_key"] == "t1/r/gone.jpg"

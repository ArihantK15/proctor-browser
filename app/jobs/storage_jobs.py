"""Storage-related background job functions (S3 upload, etc.)."""
import logging
import os

log = logging.getLogger(__name__)


def upload_screenshot_job(*, s3_key: str, local_path: str, content_type: str = "image/jpeg") -> dict:
    """Upload a screenshot file from *local_path* to S3 in the background.

    The caller (exam.py handler) has already written the file to local disk,
    so this job simply reads it back and ships it to S3.  S3 failure must
    never break the exam — the local file is already on disk.
    """
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        log.warning("[storage-job] local file gone before S3 upload: %s", local_path)
        return {"ok": False, "s3_key": s3_key}
    from ..services.object_store import upload_screenshot
    ok = upload_screenshot(s3_key=s3_key, data=data, content_type=content_type)
    if not ok:
        log.warning("[storage-job] S3 upload failed for key=%s", s3_key)
    # Best-effort cleanup: remove the local file after successful S3 upload
    if ok:
        try:
            os.remove(local_path)
        except OSError:
            pass
    return {"ok": ok, "s3_key": s3_key}

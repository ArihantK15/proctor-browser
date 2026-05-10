"""Background job definitions for RQ worker processes.

Adds Redis RQ as the async task layer.  When ``RQ_ENABLED=1`` in the
environment, long-running operations (email sending, PDF generation, …)
are enqueued to a Redis RQ worker instead of blocking the request handler.

When ``RQ_ENABLED`` is unset or ``0`` every ``enqueue_job`` call runs the
function synchronously — tests and local dev keep working without Redis.
"""

import os
import logging
from typing import Optional

log = logging.getLogger(__name__)


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _rq_enabled() -> bool:
    return os.environ.get("RQ_ENABLED", "").lower() in ("1", "true", "yes")


def enqueue_job(func, *args, **kwargs):
    """Enqueue *func(*args, **kwargs)* via Redis RQ.

    When ``RQ_ENABLED=1`` the function is pushed to the ``default`` queue
    and *None* is returned immediately.  Otherwise the function is called
    synchronously and its return value is passed through — this lets tests
    and local dev run without a running RQ worker.
    """
    if _rq_enabled():
        from rq import Queue
        from redis import Redis
        q = Queue("default", connection=Redis.from_url(_redis_url()))
        q.enqueue(func, *args, **kwargs)
        return None
    return func(*args, **kwargs)


# ─── Job functions ──────────────────────────────────────────────
# Each function is a plain callable that an RQ worker process can
# import and execute.  They live in ``app.jobs`` so the worker only
# needs ``from app.jobs import *`` to register every handler.

from . import emailer


def send_invite_email_job(
    *,
    to_email: str,
    to_name: str,
    exam_title: str,
    invite_url: str,
    download_url: str,
    roll_number: str,
    teacher_name: Optional[str] = None,
) -> dict:
    """Send a single invite email. Returns serializable result dict."""
    result = emailer.send_invite_email(
        to_email=to_email,
        to_name=to_name,
        exam_title=exam_title,
        invite_url=invite_url,
        download_url=download_url,
        roll_number=roll_number,
        teacher_name=teacher_name,
    )
    return {
        "ok": result.ok,
        "provider_msg_id": result.provider_msg_id,
        "error": result.error,
    }


def send_demo_request_notification_job(
    *,
    name: str,
    email: str,
    institution: str,
    role: str,
    message: str = "",
) -> dict:
    """Send demo request notification to super admin."""
    result = emailer.send_demo_request_notification(
        name=name, email=email, institution=institution,
        role=role, message=message,
    )
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }


def send_new_account_notification_job(
    *,
    account_type: str,
    name: str,
    email: str,
) -> dict:
    """Send new account notification to super admin."""
    result = emailer.send_new_account_notification(
        account_type=account_type, name=name, email=email,
    )
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }

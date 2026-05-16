"""Background job definitions for RQ worker processes.

When ``RQ_ENABLED=1`` long-running operations are enqueued to a Redis RQ
worker instead of blocking the request handler.  When disabled every
``enqueue_job`` call runs the function synchronously — tests and local
dev keep working without Redis.
"""
from .helpers import enqueue_job, _redis_url, _rq_enabled, _run_coro_in_sync
from .email_jobs import (
    send_invite_email_job,
    send_demo_request_notification_job,
    send_scorecard_email_job,
    send_org_invite_email_job,
    send_new_account_notification_job,
)
from .autosave_jobs import flush_autosave_job

__all__ = [
    "enqueue_job", "_redis_url", "_rq_enabled", "_run_coro_in_sync",
    "send_invite_email_job", "send_demo_request_notification_job",
    "send_scorecard_email_job", "send_org_invite_email_job",
    "send_new_account_notification_job",
    "flush_autosave_job",
]

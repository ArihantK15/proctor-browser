"""Background job definitions for RQ worker processes.

When ``RQ_ENABLED=1`` long-running operations are enqueued to a Redis RQ
worker instead of blocking the request handler.  When disabled every
``enqueue_job`` call runs the function synchronously — tests and local
dev keep working without Redis.
"""
from .helpers import enqueue_job, _redis_url, _rq_enabled, _run_coro_in_sync
from .email_jobs import (
    send_invite_email_job,
    send_cohort_link_email_job,
    send_demo_request_notification_job,
    send_scorecard_email_job,
    send_org_invite_email_job,
    send_new_account_notification_job,
    send_controller_breach_notification_job,
    send_data_subject_breach_notification_job,
    send_objection_to_controller_notice_job,
    send_guardian_consent_request_job,
)
from .autosave_jobs import flush_autosave_job
from .scoring_jobs import score_submission_job
from .lti_jobs import ags_grade_passback_job
from .storage_jobs import upload_screenshot_job

__all__ = [
    "enqueue_job",
    "send_invite_email_job", "send_cohort_link_email_job",
    "send_demo_request_notification_job",
    "send_scorecard_email_job", "send_org_invite_email_job",
    "send_new_account_notification_job",
    "send_controller_breach_notification_job",
    "send_data_subject_breach_notification_job",
    "send_objection_to_controller_notice_job",
    "send_guardian_consent_request_job",
    "flush_autosave_job",
    "score_submission_job",
    "upload_screenshot_job",
    "ags_grade_passback_job",
]

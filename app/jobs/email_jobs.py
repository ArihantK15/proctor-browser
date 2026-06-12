"""Email-related background job functions."""
import logging
from datetime import datetime, timezone
from typing import Optional

from .helpers import _run_coro_in_sync

log = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised by email jobs when the provider rejected the send.

    Raising (rather than returning ok=False) is what triggers RQ's
    retry policy. Without this, a transient provider hiccup silently
    consumed the only delivery attempt for an invite or scorecard —
    the student would never receive the email and ops had no signal.
    The RQ retry policy (default 3 attempts with 10/60/300s backoff)
    bounds the damage on permanent failures: after the final retry
    the job lands in the failed queue where ops can inspect it.
    """


def _maybe_raise(result, *, context: str) -> None:
    """Raise EmailDeliveryError when the provider call did not succeed."""
    if getattr(result, "ok", False):
        return
    err = getattr(result, "error", None) or "unknown email provider error"
    log.warning("[email-job] %s failed: %s", context, err)
    raise EmailDeliveryError(f"{context}: {err}")


def send_invite_email_job(
    *,
    to_email: str,
    to_name: str,
    exam_title: str,
    invite_url: str,
    download_url: str,
    roll_number: str,
    registration_url: Optional[str] = None,
    access_code: Optional[str] = None,
    exam_starts_at: Optional[str] = None,
    exam_ends_at: Optional[str] = None,
    custom_message: Optional[str] = None,
    teacher_name: Optional[str] = None,
) -> dict:
    from .. import emailer
    result = emailer.send_invite_email(
        to_email=to_email, to_name=to_name, exam_title=exam_title,
        invite_url=invite_url, download_url=download_url,
        roll_number=roll_number, registration_url=registration_url,
        access_code=access_code, exam_starts_at=exam_starts_at,
        exam_ends_at=exam_ends_at, custom_message=custom_message,
        teacher_name=teacher_name,
    )
    _maybe_raise(result, context=f"invite to {to_email}")
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }


def send_controller_breach_notification_job(
    *,
    to_email: str,
    to_name: str,
    org_name: str,
    description: str,
    data_categories: str,
    discovered_at: str,
) -> dict:
    from .. import emailer
    result = emailer.send_controller_breach_notification(
        to_email=to_email, to_name=to_name, org_name=org_name,
        description=description, data_categories=data_categories,
        discovered_at=discovered_at,
    )
    _maybe_raise(result, context=f"controller-breach-notice to {to_email}")
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }


def send_data_subject_breach_notification_job(
    *,
    to_email: str,
    to_name: str,
    description: str,
    data_categories: str,
) -> dict:
    from .. import emailer
    result = emailer.send_data_subject_breach_notification(
        to_email=to_email, to_name=to_name,
        description=description, data_categories=data_categories,
    )
    _maybe_raise(result, context=f"data-subject-breach-notice to {to_email}")
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }


def send_objection_to_controller_notice_job(
    *,
    to_email: str,
    to_name: str,
    org_name: str,
    user_type: str,
    grounds: str,
    scope: str,
) -> dict:
    from .. import emailer
    result = emailer.send_objection_to_controller_notice(
        to_email=to_email, to_name=to_name, org_name=org_name,
        user_type=user_type, grounds=grounds, scope=scope,
    )
    _maybe_raise(result, context=f"objection-notice to {to_email}")
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
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
    from .. import emailer
    result = emailer.send_demo_request_notification(
        name=name, email=email, institution=institution,
        role=role, message=message,
    )
    _maybe_raise(result, context=f"demo-request from {email}")
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }


def send_scorecard_email_job(
    *,
    session_key: str,
    teacher_id: str,
    email: str,
    full_name: str,
    teacher_name: str,
    custom_message: Optional[str] = None,
    resend_all: bool = False,
) -> dict:
    from ..services.scorecard import _build_scorecard_pdf

    async def _run():
        pdf_bytes, fname, summary = await _build_scorecard_pdf(session_key, teacher_id)
        from .. import emailer
        result = emailer.send_scorecard_email(
            to_email=email, to_name=full_name,
            exam_title=summary.get("exam_title") or "Exam",
            score=int(summary.get("score") or 0),
            total=int(summary.get("total") or 0),
            percentage=float(summary.get("percentage") or 0.0),
            passed=bool(summary.get("passed")),
            pdf_bytes=pdf_bytes, pdf_filename=fname,
            teacher_name=teacher_name, custom_message=custom_message,
        )
        if result.ok:
            from ..database import async_table as _atable
            await _atable("exam_sessions").update({
                "scorecard_email_msg_id": result.provider_msg_id,
                "scorecard_emailed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("session_key", session_key).eq("teacher_id", teacher_id).execute()
        _maybe_raise(result, context=f"scorecard {session_key} → {email}")
        return {
            "ok": result.ok, "provider_msg_id": result.provider_msg_id,
            "session_key": session_key, "error": result.error,
        }

    # Let EmailDeliveryError propagate so RQ retries; catch only the
    # truly-unexpected (PDF build crashed, DB write failed) and surface
    # those without retry to avoid burning attempts on a stuck bug.
    try:
        return _run_coro_in_sync(_run())
    except EmailDeliveryError:
        raise
    except Exception as e:
        log.exception("[scorecard-job] failed sid=%s", session_key)
        return {"ok": False, "error": str(e), "session_key": session_key}


def send_org_invite_email_job(
    *,
    to_email: str,
    invite_url: str,
    org_name: str,
    invited_by_name: str = "Your admin",
) -> dict:
    from .. import emailer
    result = emailer.send_org_invite_email(
        to_email=to_email, invite_url=invite_url,
        org_name=org_name, invited_by_name=invited_by_name,
    )
    _maybe_raise(result, context=f"org invite to {to_email}")
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
    from .. import emailer
    result = emailer.send_new_account_notification(
        account_type=account_type, name=name, email=email,
    )
    # Best-effort: don't raise. New-account notifications are an internal
    # ops convenience, not a user-facing delivery. Failing here would
    # consume retry budget on every signup.
    if not getattr(result, "ok", False):
        log.warning("[email-job] new-account-notification failed: %s",
                    getattr(result, "error", "unknown"))
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }

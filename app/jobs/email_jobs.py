"""Email-related background job functions."""
import logging
from datetime import datetime, timezone
from typing import Optional

from .helpers import _run_coro_in_sync

log = logging.getLogger(__name__)


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
    from .. import emailer
    result = emailer.send_demo_request_notification(
        name=name, email=email, institution=institution,
        role=role, message=message,
    )
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
        return {
            "ok": result.ok, "provider_msg_id": result.provider_msg_id,
            "session_key": session_key, "error": result.error,
        }

    try:
        return _run_coro_in_sync(_run())
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
    return {
        "ok": result.ok,
        "provider_msg_id": getattr(result, "provider_msg_id", None),
        "error": result.error,
    }

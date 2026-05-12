"""Exam reminder loop: sends 1h and 24h pre-exam email reminders.

Runs as a background task. Queries exams starting soon, finds unreminded
invites, and fires emails. Uses optimistic locking (is_(col, "null")) to
avoid sending duplicate reminders under concurrency.
"""

import asyncio
import os
from datetime import datetime, timezone, timedelta

from .database import supabase, async_table as _atable
from .constants import REMINDER_1H_WINDOW_MIN, REMINDER_24H_WINDOW_MIN
from .logger import get_logger

_dep_log = get_logger("reminders")


def _reminder_window(target_minutes: int, half_width_min: int):
    now = datetime.now(timezone.utc)
    centre = now + timedelta(minutes=target_minutes)
    return (centre - timedelta(minutes=half_width_min), centre + timedelta(minutes=half_width_min))


def _send_reminder_for_invite(inv: dict, exam_cfg: dict, hours_until: int) -> bool:
    from .emailer import send_exam_reminder
    col = "reminder_1h_at" if hours_until < 24 else "reminder_24h_at"
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        claim = (supabase.table("student_invites").update({col: now_iso}).eq("token", inv["token"]).is_(col, "null").execute())
    except Exception as e:
        _dep_log.warning("[reminders] claim failed token=%s err=%s", inv.get('token','?')[:8], e)
        return False
    if not claim.data:
        return False
    base = os.environ.get("INVITE_BASE_URL", "https://app.procta.net").rstrip("/")
    invite_url = f"{base}/invite/{inv['token']}"

    from .utils import fmt_ist
    starts_display = fmt_ist(exam_cfg.get("starts_at")) if exam_cfg.get("starts_at") else ""
    try:
        result = send_exam_reminder(to_email=inv["email"], to_name=inv.get("full_name") or "",
                                     exam_title=exam_cfg.get("exam_title") or "Your exam",
                                     invite_url=invite_url, roll_number=inv.get("roll_number") or "",
                                     hours_until=hours_until, exam_starts_at_display=starts_display,
                                     access_code=inv.get("access_code") or None)
    except Exception as e:
        _dep_log.error("[reminders] send raised: %s", e)
        result = None
    if result is None or not getattr(result, "ok", False):
        try:
            supabase.table("student_invites").update({col: None}).eq("token", inv["token"]).execute()
        except Exception:
            _dep_log.warning("[reminders] cleanup rollback failed for %s", inv.get('token','?')[:8])
        _dep_log.warning("[reminders] FAILED %dh reminder to=%s err=%r", hours_until, inv.get('email'), getattr(result,'error',None))
        return False
    _dep_log.info("[reminders] SENT %dh reminder to=%s exam=%s", hours_until, inv.get('email'), exam_cfg.get('exam_id') or '?')
    return True


async def _reminder_tick():
    from .models import InviteStatus

    buckets = [("reminder_1h_at", 60, REMINDER_1H_WINDOW_MIN, 1), ("reminder_24h_at", 24 * 60, REMINDER_24H_WINDOW_MIN, 24)]
    for col, target_min, half_width, hours_until in buckets:
        lo, hi = _reminder_window(target_min, half_width)
        try:
            exams_resp = await _atable("exam_config").select("exam_id,teacher_id,exam_title,starts_at,access_code,ends_at").gte("starts_at", lo.isoformat()).lte("starts_at", hi.isoformat()).execute()
        except Exception as e:
            _dep_log.warning("[reminders] exam query failed: %s", e)
            continue
        exams = exams_resp.data or []
        if not exams:
            continue
        for exam_cfg in exams:
            eid = exam_cfg.get("exam_id")
            if not eid:
                continue
            try:
                inv_resp = await _atable("student_invites").select("token,email,full_name,roll_number,access_code,exam_id,status").eq("exam_id", eid).is_(col, "null").in_("status", [InviteStatus.SENT, InviteStatus.OPENED, InviteStatus.ACCEPTED]).execute()
            except Exception as e:
                _dep_log.warning("[reminders] invites query failed exam=%s: %s", eid, e)
                continue
            for inv in (inv_resp.data or []):
                if not inv.get("email"):
                    continue
                try:
                    await asyncio.to_thread(_send_reminder_for_invite, inv, exam_cfg, hours_until)
                except Exception as e:
                    _dep_log.warning("[reminders] per-invite error: %s", e)


async def _reminder_loop():
    import asyncio as _asyncio
    import traceback as _tb
    while True:
        try:
            await _reminder_tick()
        except Exception as e:
            _dep_log.error("[reminders] tick crashed: %s", e)
            _dep_log.debug("[reminders] traceback: %s", _tb.format_exc())
        try:
            from .constants import REMINDER_POLL_SECONDS
            await _asyncio.sleep(REMINDER_POLL_SECONDS)
        except Exception as e:
            _dep_log.error("[reminders] sleep failed: %s", e)
            await _asyncio.sleep(60)

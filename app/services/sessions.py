"""Session liveness, clear-token helpers, and live-session payload building.

Extracted from app/dependencies.py.
"""

import logging
import time
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
import threading


from fastapi import HTTPException

from ..models import SessionStatus
from ..database import async_table as _atable
from ..constants import (
    _CLEAR_TOKEN_TTL, _CLEAR_ACTIVE_WINDOW,
    SCREENSHOTS_DIR, PLANS,
)
from ..utils import now_ist, fmt_ist, _safe_path_component
from .risk import _is_violation, _risk_label, _batch_risk_scores
from .calibration import parse_calibration_details, classify_calibration

logger = logging.getLogger(__name__)

try:
    from .. import cache as _cache
except Exception:
    _cache = None


# ─── ORG LIMITS ────────────────────────────────────────────────────

PLAN_LIMITS = {p: v["students"] for p, v in PLANS.items()}


async def check_org_limits(teacher: dict, delta: int = 0) -> dict:
    if teacher.get("org_role") == "superadmin":
        return {"max_students": 999999}
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated with this account")
    await _check_subscription_active(str(org_id))
    cache_key = f"org_limits:{org_id}"
    # Reads can use a short cache. Mutating capacity checks must hit
    # the DB because stale student counts would allow oversubscription.
    cached = _cache.get(cache_key) if _cache and delta == 0 else None
    if isinstance(cached, dict):
        org = cached.get("org") or {}
        current_count = int(cached.get("student_count") or 0)
        max_students = int(org.get("max_students", 30))
        if current_count + delta > max_students:
            raise HTTPException(
                status_code=403,
                detail=f"Student limit reached ({current_count}/{max_students}). Upgrade your plan."
            )
        return org
    try:
        org_result = await _atable("organizations").select("id,name,slug,max_students").eq("id", str(org_id)).single().execute()
        org = org_result.data if hasattr(org_result, 'data') else org_result[0]
        if not org or not isinstance(org, dict):
            raise HTTPException(status_code=404, detail="Organization not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Organization not found")
    try:
        count_result = await _atable("students").select("id", count="exact").eq("org_id", str(org_id)).execute()
        current_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
    except Exception:
        current_count = 0
    max_students = int(org.get("max_students", 30))
    if _cache:
        _cache.set(cache_key, {"org": org, "student_count": current_count}, ttl=60)
    if current_count + delta > max_students:
        raise HTTPException(
            status_code=403,
            detail=f"Student limit reached ({current_count}/{max_students}). Upgrade your plan."
        )
    return org


async def _check_subscription_active(org_id: str) -> None:
    """Raise 403 if the org's subscription has expired, been cancelled, or
    their trial period has ended.  Superadmin orgs bypass this check.
    Called from check_org_limits so every capacity-gated endpoint gets it."""
    sub = await get_org_subscription(org_id)
    if not sub:
        # No subscription row yet — newly created org still within trial window;
        # don't block until the row is present.
        return
    status = (sub.get("status") or "").lower()
    if status in ("expired", "cancelled"):
        period_end_raw = sub.get("current_period_end") or ""
        if period_end_raw:
            try:
                period_end_dt = datetime.fromisoformat(
                    str(period_end_raw).replace("Z", "+00:00")
                )
                if period_end_dt.tzinfo is None:
                    period_end_dt = period_end_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) <= period_end_dt:
                    return
            except Exception:
                logger.debug("sessions: billing period parse failed", exc_info=True)
        raise HTTPException(
            status_code=403,
            detail="Your subscription has expired. Please upgrade your plan to continue.",
        )
    if status == "trialing":
        trial_end_raw = sub.get("trial_end") or ""
        if trial_end_raw:
            try:
                trial_end_dt = datetime.fromisoformat(
                    str(trial_end_raw).replace("Z", "+00:00")
                )
                if trial_end_dt.tzinfo is None:
                    trial_end_dt = trial_end_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > trial_end_dt:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "Your free trial has ended. "
                            "Upgrade your plan to keep running exams."
                        ),
                    )
            except HTTPException:
                raise
            except Exception:
                logger.warning("malformed trial_end date for org %s: %s", org_id, trial_end_raw)
                pass  # don't block on bad date


async def get_org_subscription(org_id: str) -> dict | None:
    cache_key = f"org_subscription:{org_id}"
    cached = _cache.get(cache_key) if _cache else None
    if cached is not None:
        return cached or None
    try:
        # `max_students` lives on the `organizations` table, NOT on
        # `subscriptions` — selecting it here fired a Postgres ERROR
        # on every authenticated request (caught by the try/except
        # below, so the call returned None silently). Confirmed no
        # caller reads `sub["max_students"]` — they all read it from
        # the organizations table directly (see app/services/sessions.py:54,
        # app/routers/admin_org.py:39, etc.).
        result = await _atable("subscriptions").select(
            "id,org_id,plan,status,trial_end,current_period_start,current_period_end,razorpay_subscription_id"
        ).eq("org_id", str(org_id)).limit(1).execute()
        sub = (result.data or [None])[0]
        if _cache:
            _cache.set(cache_key, sub or {}, ttl=60)
        return sub
    except Exception:
        return None


# ─── SESSION LIVENESS ──────────────────────────────────────────────

def heartbeat_age_seconds(hb) -> float | None:
    if not hb:
        return None
    try:
        if isinstance(hb, datetime):
            dt = hb
        else:
            dt = datetime.fromisoformat(str(hb).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()


def derive_live_state(meta: dict) -> tuple[str, int | None]:
    status = (meta.get("status") or "").lower()
    if status in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED, SessionStatus.FORCE_SUBMITTED) or meta.get("submitted_at"):
        return "submitted", None
    age = heartbeat_age_seconds(meta.get("last_heartbeat"))
    if age is not None and age <= _CLEAR_ACTIVE_WINDOW:
        return "live", int(age)
    return "stale", (int(age) if age is not None else None)


# ─── CLEAR LIVE SESSION HELPERS ────────────────────────────────────

_CLEAR_TOKENS: dict[str, dict] = {}
_CLEAR_TOKENS_LOCK = threading.Lock()


def clear_token_issue(teacher_id: str) -> str:
    tok = _uuid.uuid4().hex
    payload = {"teacher_id": str(teacher_id)}
    if _cache:
        _cache.set(f"clear_token:{tok}", payload, ttl=_CLEAR_TOKEN_TTL)
    else:
        with _CLEAR_TOKENS_LOCK:
            _CLEAR_TOKENS[tok] = {**payload, "expires": time.time() + _CLEAR_TOKEN_TTL}
            now = time.time()
            stale = [k for k, v in _CLEAR_TOKENS.items() if v["expires"] < now]
            for k in stale:
                _CLEAR_TOKENS.pop(k, None)
    return tok


def session_is_active(row: dict) -> bool:
    hb = row.get("last_heartbeat")
    if not hb:
        return False
    try:
        if isinstance(hb, datetime):
            dt = hb
        else:
            dt = datetime.fromisoformat(str(hb).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    return age <= _CLEAR_ACTIVE_WINDOW


async def partition_live_sessions(teacher_id: str, exam_id: str | None = None, include_active: bool = False) -> tuple[list[dict], list[dict]]:
    tid = str(teacher_id)
    base = _atable("exam_sessions").select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id,exam_id").eq("teacher_id", tid).eq("status", SessionStatus.IN_PROGRESS)
    if exam_id:
        base = base.eq("exam_id", exam_id)
    result = await base.execute()
    rows = list(result.data or [])
    seen = {r["session_key"] for r in rows}

    async def _q_null():
        q = _atable("exam_sessions").select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id,exam_id").is_("teacher_id", "null").eq("status", SessionStatus.IN_PROGRESS)
        if exam_id:
            q = q.eq("exam_id", exam_id)
        return await q.execute()

    async def _q_empty():
        q = _atable("exam_sessions").select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id,exam_id").eq("teacher_id", "").eq("status", SessionStatus.IN_PROGRESS)
        if exam_id:
            q = q.eq("exam_id", exam_id)
        return await q.execute()

    for fetch_fn in [_q_null, _q_empty]:
        try:
            for r in ((await fetch_fn()).data or []):
                if r["session_key"] not in seen:
                    rows.append(r)
                    seen.add(r["session_key"])
        except Exception as e:
            logger.warning("[ClearLive] orphan query failed: %s", e)

    try:
        cutoff = (now_ist() - timedelta(hours=48)).isoformat()
        viol_teacher = await _atable("violations").select("session_key").eq("teacher_id", tid).gte("created_at", cutoff).execute()
        viol_orphan1 = await _atable("violations").select("session_key").is_("teacher_id", "null").gte("created_at", cutoff).execute()
        viol_orphan2 = await _atable("violations").select("session_key").eq("teacher_id", "").gte("created_at", cutoff).execute()
        all_viol_data = (viol_teacher.data or []) + (viol_orphan1.data or []) + (viol_orphan2.data or [])
        ghost_keys: set[str] = set()
        for v in all_viol_data:
            sk = v.get("session_key")
            if sk and sk not in seen:
                ghost_keys.add(sk)
        for sk in ghost_keys:
            rows.append({
                "session_key": sk,
                "roll_number": sk.split("_")[0] if "_" in sk else sk,
                "full_name": None, "started_at": None, "last_heartbeat": None,
                "teacher_id": tid, "_ghost": True,
            })
            seen.add(sk)
    except Exception as e:
        logger.warning("[ClearLive] violations ghost discovery failed: %s", e)

    active, stale = [], []
    for r in rows:
        if include_active:
            stale.append(r)
        else:
            (active if session_is_active(r) else stale).append(r)
    return active, stale


def clear_token_consume(token: str, teacher_id: str) -> bool:
    if _cache:
        rec = _cache.get(f"clear_token:{token}")
        if not rec or rec.get("teacher_id") != str(teacher_id):
            return False
        _cache.delete(f"clear_token:{token}")
        return True
    with _CLEAR_TOKENS_LOCK:
        rec = _CLEAR_TOKENS.pop(token, None)
        if not rec or rec["teacher_id"] != str(teacher_id) or rec["expires"] < time.time():
            return False
    return True


# ─── BUILD SESSIONS PAYLOAD ────────────────────────────────────────

async def build_sessions_payload(tid: str, exam_id: str = None,
                                 tids: list[str] | None = None) -> dict:
    """Build the Live-tab session payload. Filter precedence:
       tids (multi) > tid (single) > unfiltered.
    Org-admin/superadmin endpoints pass `tids` from
    app/auth/scope.py:scope_to_teacher_ids() so the caller's filter
    is enforced uniformly."""
    cutoff = (now_ist() - timedelta(hours=48)).isoformat()
    # `_apply_tids` collapses to `.eq()` for the single-teacher case so
    # plain-teacher callers (the overwhelming majority) and our existing
    # test stubs — which only mock `.eq()` not `.in_()` — keep working.
    def _apply_tids(q, tids):
        if tids is None:    return q
        if not tids:        return q.eq("teacher_id", "__none__")
        if len(tids) == 1:  return q.eq("teacher_id", str(tids[0]))
        return q.in_("teacher_id", tids)
    evts_query = _atable("violations").select("session_key,violation_type,severity,created_at,details").gte("created_at", cutoff)
    if tids is not None:
        evts_query = _apply_tids(evts_query, tids)
    elif tid:
        evts_query = evts_query.eq("teacher_id", str(tid))
    evts_result = await evts_query.order("created_at", desc=True).limit(2000).execute()
    events = evts_result.data or []

    sess_query = _atable("exam_sessions").select("session_key,status,risk_score,exam_id,last_heartbeat,started_at,submitted_at,room_cam_status")
    if tids is not None:
        sess_query = _apply_tids(sess_query, tids)
    elif tid:
        sess_query = sess_query.eq("teacher_id", str(tid))
    # NOTE: the LIVE view intentionally shows ALL of a teacher's active
    # sessions, regardless of the dashboard's selected exam. A proctor must
    # never miss a student who is currently testing just because that
    # student is enrolled in a different exam — or because the session row
    # has no exam_id at all (which the old `eq("exam_id", …)` filter silently
    # dropped, making live monitoring appear empty). The `exam_id` arg is
    # kept for signature/back-compat but deliberately NOT applied here;
    # per-exam scoping stays on the results/history endpoints. Tenant
    # isolation is unaffected — it comes from the teacher_id/tids filter above.
    sess_result = await sess_query.execute()
    sess_meta = {r["session_key"]: r for r in (sess_result.data or [])}
    submitted = {
        sk for sk, m in sess_meta.items()
        if (m.get("status") or "").lower() in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED, SessionStatus.FORCE_SUBMITTED) or m.get("submitted_at")
    }

    viol_by_session: dict[str, list[dict]] = {}
    for e in events:
        viol_by_session.setdefault(e["session_key"], []).append(e)

    batch_risks = _batch_risk_scores(viol_by_session)

    sessions: dict = {}
    for e in events:
        sk = e["session_key"]
        if sk not in sessions:
            meta = sess_meta.get(sk, {})
            cached_risk = meta.get("risk_score")
            if cached_risk is None and sk not in submitted:
                cached_risk, risk_label = batch_risks.get(sk, (None, None))
            else:
                risk_label = _risk_label(cached_risk) if cached_risk is not None else None
            live_state, hb_age = derive_live_state(meta)
            sessions[sk] = {
                "session_id": sk,
                "last_event": e["violation_type"],
                "last_severity": e["severity"],
                "room_cam_status": meta.get("room_cam_status", "disabled"),
                "last_seen": fmt_ist(e.get("created_at", "")),
                "details": e.get("details"),
                "submitted": sk in submitted,
                "live_state": live_state,
                "heartbeat_age_sec": hb_age,
                "risk_score": cached_risk,
                "risk_label": risk_label,
            }

    cal_tiers: dict[str, dict] = {}
    seen_cal_keys: set[str] = set()
    for e in events:
        if e["violation_type"] == "calibration_complete" and e["session_key"] not in seen_cal_keys:
            seen_cal_keys.add(e["session_key"])
            cal_tiers[e["session_key"]] = classify_calibration(parse_calibration_details(e.get("details")))

    for sk, sess in sessions.items():
        sess["calibration"] = cal_tiers.get(sk, {"tier": "missing", "reason": "No calibration recorded.", "ranges": None})

    # The live monitor's source of truth is exam_sessions, not violations.
    # A student can be actively testing before their first violation, or a
    # transient violation query can return empty while the session row remains.
    # Without this backfill the dashboard row appears, disappears on the next
    # refresh, then reappears only after reload/new events.
    for sk, meta in sess_meta.items():
        if sk in sessions:
            continue
        live_state, hb_age = derive_live_state(meta)
        if live_state == "submitted":
            continue
        sessions[sk] = {
            "session_id": sk,
            "last_event": "heartbeat" if meta.get("last_heartbeat") else "exam_started",
            "last_severity": "low",
            "room_cam_status": meta.get("room_cam_status", "disabled"),
            "last_seen": fmt_ist(meta.get("last_heartbeat") or meta.get("started_at")),
            "details": None,
            "submitted": False,
            "live_state": live_state,
            "heartbeat_age_sec": hb_age,
            "risk_score": meta.get("risk_score"),
            "risk_label": _risk_label(meta.get("risk_score")) if meta.get("risk_score") is not None else None,
            "calibration": cal_tiers.get(sk, {"tier": "missing", "reason": "No calibration recorded.", "ranges": None}),
        }

    active = [s for s in sessions.values() if s["live_state"] == "live"]
    return {"sessions": active, "all_sessions": list(sessions.values())}


# ─── SCREENSHOT HELPERS ────────────────────────────────────────────

def collect_session_screenshots(roll: str, teacher_id: str) -> dict[str, Path]:
    if not roll or not teacher_id:
        return {}
    student_dir = Path(SCREENSHOTS_DIR) / _safe_path_component(str(teacher_id)) / _safe_path_component(roll)
    if not student_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in sorted(student_dir.iterdir()):
        if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            out[f.name] = f
    return out


def match_screenshot_for_violation(violation: dict, screenshots: dict[str, Path]) -> Path | None:
    if not screenshots or not violation.get("created_at"):
        return None
    try:
        evt_ts = datetime.fromisoformat(str(violation["created_at"]).replace("Z", "+00:00")).astimezone(tz=timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        return None
    vtype = violation.get("violation_type", "")
    window_keys = {(evt_ts + timedelta(seconds=delta)).strftime("%Y%m%d_%H%M%S") for delta in range(-2, 3)}
    for fname, fpath in screenshots.items():
        if fname.startswith(f"evt_{vtype}_") and any(k in fname for k in window_keys):
            return fpath
    for fname, fpath in screenshots.items():
        if fname.startswith("evt_") and any(k in fname for k in window_keys):
            return fpath
    for fname, fpath in screenshots.items():
        # Exclude the phone-cam companion (room_*) — it is matched separately
        # by match_room_screenshot_for_violation; it must never stand in as
        # the PRIMARY frame.
        if not fname.startswith("room_") and any(k in fname for k in window_keys):
            return fpath
    return None


def match_room_screenshot_for_violation(violation: dict, screenshots: dict[str, Path]) -> Path | None:
    """The phone-cam companion (room_<type>_<ts>.jpg) saved at the same instant
    as the primary evt_ frame for a flag — so the PDF / live timeline can show
    both cameras side by side. Returns None when no phone was paired."""
    if not screenshots or not violation.get("created_at"):
        return None
    try:
        evt_ts = datetime.fromisoformat(str(violation["created_at"]).replace("Z", "+00:00")).astimezone(tz=timezone.utc).replace(tzinfo=timezone.utc)
    except Exception:
        return None
    vtype = violation.get("violation_type", "")
    window_keys = {(evt_ts + timedelta(seconds=delta)).strftime("%Y%m%d_%H%M%S") for delta in range(-2, 3)}
    for fname, fpath in screenshots.items():
        if fname.startswith(f"room_{vtype}_") and any(k in fname for k in window_keys):
            return fpath
    for fname, fpath in screenshots.items():
        if fname.startswith("room_") and any(k in fname for k in window_keys):
            return fpath
    return None


def cleanup_screenshots(stop_event=None):
    while True:
        if stop_event and stop_event.wait(3600):
            break
        try:
            cutoff = now_ist() - timedelta(hours=48)
            for student_dir in Path(SCREENSHOTS_DIR).iterdir():
                if student_dir.is_dir():
                    for f in student_dir.iterdir():
                        if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                            f.unlink()
        except Exception as e:
            logger.warning("[Cleanup] %s", e)

"""Data-access layer for session-related database queries.

Extracted from app/dependencies.py.
"""

import logging
from typing import Optional

from ..database import async_table as _atable
from ..models import SessionStatus
from ..services.risk import _is_violation
from ..services.calibration import parse_calibration_details, classify_calibration
from ..utils import now_ist

logger = logging.getLogger(__name__)


async def assert_session_owned(session_id: str, teacher_id: str) -> dict:
    from fastapi import HTTPException

    if not teacher_id:
        raise HTTPException(status_code=403, detail="Teacher context missing")
    tid_str = str(teacher_id)
    result = (await _atable("exam_sessions").select("*").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()).data
    if result:
        return result[0]
    bare = (await _atable("exam_sessions").select("*").eq("session_key", session_id).limit(1).execute()).data
    if bare:
        row = bare[0]
        row_tid = row.get("teacher_id")
        if row_tid in (None, ""):
            v_other = (await _atable("violations").select("teacher_id").eq("session_key", session_id).neq("teacher_id", tid_str).limit(1).execute()).data
            if not (v_other or []):
                return row
        raise HTTPException(status_code=404, detail="Session not found")
    v_mine = (await _atable("violations").select("session_key,teacher_id").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()).data
    if v_mine:
        return {
            "session_key": session_id,
            "teacher_id": tid_str,
            "roll_number": (session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]),
            "full_name": "",             "status": SessionStatus.IN_PROGRESS, "started_at": "", "submitted_at": "",
            "score": None, "total": None, "risk_score": None,
        }
    raise HTTPException(status_code=404, detail="Session not found")


async def violation_counts_by_session(session_keys: list[str]) -> dict[str, int]:
    if not session_keys:
        return {}
    counts: dict[str, int] = {}
    for i in range(0, len(session_keys), 200):
        chunk = session_keys[i:i + 200]
        viol_result = await _atable("violations").select("session_key,violation_type,severity").in_("session_key", chunk).execute()
        for v in (viol_result.data or []):
            if v["severity"] in ("high", "medium") and _is_violation(v["violation_type"]):
                counts[v["session_key"]] = counts.get(v["session_key"], 0) + 1
    return counts


async def calibration_tiers_by_session(session_keys: list[str], teacher_id: Optional[str] = None) -> dict[str, dict]:
    if not session_keys:
        return {}
    q = (_atable("violations").select("session_key,details").eq("violation_type", "calibration_complete").in_("session_key", session_keys))
    if teacher_id:
        q = q.eq("teacher_id", str(teacher_id))
    rows = (await q.execute()).data or []
    out: dict[str, dict] = {}
    for r in rows:
        sk = r.get("session_key")
        if not sk:
            continue
        out[sk] = classify_calibration(parse_calibration_details(r.get("details")))
    return out


async def check_group_access(roll_number: str, teacher_id: str, exam_id: str) -> bool:
    assignments = (await _atable("exam_group_assignments").select("group_id").eq("exam_id", exam_id).eq("teacher_id", teacher_id).execute()).data or []
    if not assignments:
        return True
    gids = [a["group_id"] for a in assignments]
    member = (await _atable("student_group_members").select("id").in_("group_id", gids).eq("roll_number", roll_number).eq("teacher_id", teacher_id).limit(1).execute()).data
    return bool(member)


async def fetch_all_results(teacher_id: str = None, exam_id: str = None, limit: int = 5000) -> list[dict]:
    from ..services.risk import _risk_label, compute_risk_score
    from ..utils import fmt_ist

    query = _atable("exam_sessions").select("*").eq("status", SessionStatus.COMPLETED)
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    sess_result = await query.order("submitted_at", desc=True).limit(limit).execute()
    sessions = sess_result.data or []
    sks = [s["session_key"] for s in sessions]
    vcounts = await violation_counts_by_session(sks)
    cal_tiers = await calibration_tiers_by_session(sks, teacher_id=teacher_id)
    return [{
        "session_id": s["session_key"],
        "roll_number": s["roll_number"],
        "full_name": s["full_name"],
        "email": s.get("email", ""),
        "score": s.get("score", 0),
        "total": s.get("total", 0),
        "percentage": s.get("percentage", 0.0),
        "time_taken_secs": s.get("time_taken_secs", 0),
        "submitted_at": fmt_ist(s.get("submitted_at", "")),
        "violation_count": vcounts.get(s["session_key"], 0),
        "risk_score": s.get("risk_score"),
        "risk_label": _risk_label(s["risk_score"]) if s.get("risk_score") is not None else None,
        "calibration": cal_tiers.get(s["session_key"], {"tier": "missing", "reason": "No calibration recorded.", "ranges": None}),
    } for s in sessions]


async def stream_csv_results(teacher_id: str = None, exam_id: str = None, max_rows: int = 5000):
    from ..utils import fmt_ist
    from ..services.risk import _risk_label

    try:
        batch_size = 500
        offset = 0
        header_written = False
        total_yielded = 0
        while total_yielded < max_rows:
            query = _atable("exam_sessions").select("*").eq("status", SessionStatus.COMPLETED)
            if teacher_id:
                query = query.eq("teacher_id", teacher_id)
            if exam_id:
                query = query.eq("exam_id", exam_id)
            sess_result = await query.order("submitted_at", desc=True).range(offset, offset + batch_size - 1).execute()
            batch = sess_result.data or []
            if not batch:
                break
            sks = [s["session_key"] for s in batch]
            vcounts = await violation_counts_by_session(sks)
            for s in batch:
                if total_yielded == 0 and not header_written:
                    header_written = True
                    yield "Timestamp,SessionID,RollNumber,FullName,Email,Score,Total,Percentage,TimeTaken,Violations,RiskScore,RiskLabel\n"
                row = [
                    fmt_ist(s.get("submitted_at", "")),
                    s["session_key"],
                    s["roll_number"],
                    s["full_name"],
                    s.get("email", ""),
                    s.get("score", 0),
                    s.get("total", 0),
                    f"{s.get('percentage', 0.0)}%",
                    f"{s.get('time_taken_secs', 0)}s",
                    vcounts.get(s["session_key"], 0),
                    s.get("risk_score", ""),
                    _risk_label(s["risk_score"]) if s.get("risk_score") is not None else "",
                ]
                yield ",".join(str(v).replace('"', '""') if isinstance(v, str) else str(v) for v in row) + "\n"
                total_yielded += 1
                if total_yielded >= max_rows:
                    return
            offset += batch_size
    except Exception as e:
        logger.error("[csv] stream failed: %s", e)
        yield f"\n# Error: {e}\n"

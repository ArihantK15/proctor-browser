"""Data-access layer for session-related database queries.

Extracted from app/dependencies.py.
"""

import logging
from typing import Optional

from ..database import async_table as _atable
from ..models import SessionStatus
from ..services.risk import _is_violation
from ..services.calibration import parse_calibration_details, classify_calibration

logger = logging.getLogger(__name__)


async def assert_session_owned(session_id: str, teacher_id: str) -> dict:
    from fastapi import HTTPException

    if not teacher_id:
        raise HTTPException(status_code=403, detail="Teacher context missing")
    tid_str = str(teacher_id)
    result = (await _atable("exam_sessions").select("session_key,teacher_id,exam_id,roll_number,full_name,status,started_at,submitted_at,score,total,percentage,risk_score,time_taken_secs,email,terminated_by,termination_reason_code,termination_reason_text,paused_secs_total,paused_at").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()).data
    if result:
        return result[0]
    bare = (await _atable("exam_sessions").select("session_key,teacher_id,exam_id,roll_number,full_name,status,started_at,submitted_at,score,total,percentage,risk_score,time_taken_secs,email,terminated_by,termination_reason_code,termination_reason_text,paused_secs_total,paused_at").eq("session_key", session_id).limit(1).execute()).data
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
            "score": None, "total": None, "risk_score": None, "exam_id": None,
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


async def calibration_tiers_by_session(session_keys: list[str], teacher_id: Optional[str] = None,
                                       teacher_ids: list[str] | None = None) -> dict[str, dict]:
    """Calibration tier per session. Scope filter precedence mirrors
    fetch_all_results: teacher_ids (org multi) > teacher_id (single) >
    unscoped. The session_keys constraint already bounds the result set;
    the teacher filter is a defensive narrowing. For org-admin roll-ups
    pass teacher_ids so co-teacher calibration rows aren't dropped."""
    if not session_keys:
        return {}
    q = (_atable("violations").select("session_key,details").eq("violation_type", "calibration_complete").in_("session_key", session_keys))
    if teacher_ids is not None:
        if not teacher_ids:
            q = q.eq("teacher_id", "__none__")
        elif len(teacher_ids) == 1:
            q = q.eq("teacher_id", str(teacher_ids[0]))
        else:
            q = q.in_("teacher_id", teacher_ids)
    elif teacher_id:
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
    """Exam access gate for both cohort axes (gap #59).

    An exam may be restricted to explicit student_groups AND/OR to derived
    batches (students.batch). Semantics:
      * NO group assignments AND NO batch assignments → open to everyone.
      * Otherwise the student may enter iff they are a member of an assigned
        group OR their students.batch matches an assigned batch.
    Batch membership is DERIVED from the students row, so a cohort gets standing
    access with no per-student member rows to maintain.
    """
    group_assignments = (await _atable("exam_group_assignments").select("group_id").eq("exam_id", exam_id).eq("teacher_id", teacher_id).execute()).data or []
    batch_assignments = (await _atable("exam_batch_assignments").select("batch").eq("exam_id", exam_id).eq("teacher_id", teacher_id).execute()).data or []

    # Unrestricted exam → open to everyone (unchanged default).
    if not group_assignments and not batch_assignments:
        return True

    # Group membership (explicit member rows).
    if group_assignments:
        gids = [a["group_id"] for a in group_assignments]
        member = (await _atable("student_group_members").select("id").in_("group_id", gids).eq("roll_number", roll_number).eq("teacher_id", teacher_id).limit(1).execute()).data
        if member:
            return True

    # Batch membership (derived from the student's cohort label). Matched
    # case-insensitively + whitespace-trimmed (casefold) so a label entered as
    # "CS-2024" on the assignment and "cs-2024 " on the student still matches —
    # a case/whitespace mismatch must never silently lock a cohort out.
    if batch_assignments:
        assigned = {(a.get("batch") or "").strip().casefold() for a in batch_assignments if (a.get("batch") or "").strip()}
        if assigned:
            srow = (await _atable("students").select("batch").eq("roll_number", roll_number).eq("teacher_id", teacher_id).limit(1).execute()).data
            student_batch = ((srow[0].get("batch") if srow else "") or "").strip().casefold()
            if student_batch and student_batch in assigned:
                return True

    return False


async def cohort_roll_numbers(teacher_ids: list[str], group_id: str | None = None,
                              batch: str | None = None) -> set[str] | None:
    """Resolve a group_id and/or batch label to matching roll_numbers.

    * group_id → query ``student_group_members`` for roll_numbers scoped to
      ``teacher_ids``.
    * batch    → query ``students`` for roll_numbers whose ``batch`` matches
      case/space-insensitively, scoped to ``teacher_ids``.

    Returns the union if both are given.  Returns ``None`` when neither cohort
    axis is provided — the caller should interpret None as "no filter" rather
    than "empty set".
    """
    if not group_id and not batch:
        return None
    result: set[str] = set()
    if group_id:
        rows = (await _atable("student_group_members")
                .select("roll_number")
                .eq("group_id", group_id)
                .in_("teacher_id", teacher_ids)
                .execute()).data or []
        result.update(r["roll_number"] for r in rows if r.get("roll_number"))
    if batch:
        folded = batch.strip().casefold()
        rows = (await _atable("students")
                .select("roll_number")
                .in_("teacher_id", teacher_ids)
                .execute()).data or []
        result.update(
            r["roll_number"] for r in rows
            if r.get("roll_number") and (r.get("batch") or "").strip().casefold() == folded
        )
    return result if result else {"__none__"}


async def fetch_all_results(teacher_id: str = None, exam_id: str = None, limit: int = 5000,
                            teacher_ids: list[str] | None = None,
                            roll_numbers: set[str] | None = None) -> list[dict]:
    """Fetch completed-session results. Filter precedence:
       teacher_ids (multi) > teacher_id (single) > unfiltered.
    teacher_ids is the org-scope path: pass the list of teachers the
    caller is authorised to see (from app/auth/scope.py)."""
    from ..services.risk import _risk_label, compute_risk_score
    from ..utils import fmt_ist

    query = _atable("exam_sessions").select("session_key,roll_number,full_name,email,score,total,percentage,time_taken_secs,submitted_at,risk_score").in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
    if teacher_ids is not None:
        # Empty list → match nothing (defensive: empty org). Single-element
        # list collapses to `.eq()` for test-stub compatibility (stubs
        # only mock `.eq()`).
        if not teacher_ids:
            query = query.eq("teacher_id", "__none__")
        elif len(teacher_ids) == 1:
            query = query.eq("teacher_id", str(teacher_ids[0]))
        else:
            query = query.in_("teacher_id", teacher_ids)
    elif teacher_id:
        query = query.eq("teacher_id", teacher_id)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    sess_result = await query.order("submitted_at", desc=True).limit(limit).execute()
    sessions = sess_result.data or []
    if roll_numbers is not None:
        sessions = [s for s in sessions if s.get("roll_number") in roll_numbers]
    sks = [s["session_key"] for s in sessions]
    vcounts = await violation_counts_by_session(sks)
    cal_tiers = await calibration_tiers_by_session(sks, teacher_id=teacher_id, teacher_ids=teacher_ids)
    return [{
        "session_id": s["session_key"],
        "roll_number": s["roll_number"],
        "full_name": s["full_name"],
        "email": s.get("email", ""),
        "score": s.get("score") or 0,
        "total": s.get("total") or 0,
        "percentage": s.get("percentage") or 0.0,
        "time_taken_secs": s.get("time_taken_secs") or 0,
        "submitted_at": fmt_ist(s.get("submitted_at") or ""),
        "violation_count": vcounts.get(s["session_key"], 0),
        "risk_score": s.get("risk_score"),
        "risk_label": _risk_label(s["risk_score"]) if s.get("risk_score") is not None else None,
        "calibration": cal_tiers.get(s["session_key"], {"tier": "missing", "reason": "No calibration recorded.", "ranges": None}),
    } for s in sessions]


async def stream_csv_results(teacher_id: str = None, exam_id: str = None, max_rows: int = 5000,
                             teacher_ids: list[str] | None = None,
                             roll_numbers: set[str] | None = None):
    from ..utils import fmt_ist
    from ..services.risk import _risk_label

    try:
        batch_size = 500
        offset = 0
        header_written = False
        total_yielded = 0
        while total_yielded < max_rows:
            query = _atable("exam_sessions").select("session_key,roll_number,full_name,email,score,total,percentage,time_taken_secs,submitted_at,risk_score").in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
            if teacher_ids is not None:
                if not teacher_ids:
                    query = query.eq("teacher_id", "__none__")
                elif len(teacher_ids) == 1:
                    query = query.eq("teacher_id", str(teacher_ids[0]))
                else:
                    query = query.in_("teacher_id", teacher_ids)
            elif teacher_id:
                query = query.eq("teacher_id", teacher_id)
            if exam_id:
                query = query.eq("exam_id", exam_id)
            sess_result = await query.order("submitted_at", desc=True).range(offset, offset + batch_size - 1).execute()
            batch = sess_result.data or []
            if not batch:
                break  # genuine end of data — terminate (not an infinite paginate)
            if roll_numbers is not None:
                batch = [s for s in batch if s.get("roll_number") in roll_numbers]
                if not batch:
                    offset += batch_size
                    continue  # this page had rows but none matched the cohort
            sks = [s["session_key"] for s in batch]
            vcounts = await violation_counts_by_session(sks)
            if not header_written:
                header_written = True
                yield "Timestamp,SessionID,RollNumber,FullName,Email,Score,Total,Percentage,TimeTaken,Violations,RiskScore,RiskLabel\n"
            buf: list[str] = []
            for s in batch:
                row = [
                    fmt_ist(s.get("submitted_at", "")),
                    s["session_key"],
                    s["roll_number"],
                    s["full_name"],
                    s.get("email") or "",
                    s.get("score") or 0,
                    s.get("total") or 0,
                    f"{s.get('percentage') or 0.0}%",
                    f"{s.get('time_taken_secs') or 0}s",
                    vcounts.get(s["session_key"], 0),
                    s.get("risk_score", ""),
                    _risk_label(s["risk_score"]) if s.get("risk_score") is not None else "",
                ]
                buf.append(",".join(str(v).replace('"', '""') if isinstance(v, str) else str(v) for v in row) + "\n")
                total_yielded += 1
                if total_yielded >= max_rows or len(buf) >= 100:
                    yield "".join(buf)
                    buf = []
                if total_yielded >= max_rows:
                    return
            if buf:
                yield "".join(buf)
            offset += batch_size
    except Exception as e:
        logger.error("[csv] stream failed: %s", e)
        yield f"\n# Error: {e}\n"

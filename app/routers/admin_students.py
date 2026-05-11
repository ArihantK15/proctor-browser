"""Students router. Extracted from admin.py."""

import logging
from fastapi import APIRouter, Request, HTTPException, Body
from ..dependencies import (
    require_admin, _atable, _cache, _load_exam_config, _get_access_code, _set_access_code,
    _violation_counts_by_session, generate_session_summary, _risk_label,
    fmt_ist, now_ist, SessionStatus, limiter, check_org_limits,
)
from ..models import BulkRegisterIn, AccessCodeIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

_BEHAVIORAL_PATTERNS = frozenset({
    "phone_consulting", "collaboration", "answer_memo",
    "note_reading", "sustained_offtask", "nervous_evasion",
})


@router.get("/api/v1/student-history/{roll_number}")
@limiter.limit("30/minute")
async def get_student_history(
    roll_number: str,
    request: Request,
    exam_id: str = None,
    page: int = 1,
    page_size: int = 50,
):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    roll = roll_number.strip().upper()
    if not roll:
        raise HTTPException(status_code=400, detail="roll_number is required")

    student_rows = (await _atable("students")
                    .select("roll_number,full_name,email,phone,teacher_id")
                    .eq("roll_number", roll)
                    .eq("teacher_id", tid)
                    .limit(1)
                    .execute()).data or []
    if not student_rows:
        raise HTTPException(status_code=404, detail="Student not found for this teacher")
    student = student_rows[0]

    sess_q = (_atable("exam_sessions")
              .select("session_key,exam_id,roll_number,full_name,email,"
                      "score,total,percentage,time_taken_secs,"
                      "status,started_at,submitted_at,risk_score")
              .eq("roll_number", roll)
              .eq("teacher_id", tid)
              .eq("status", SessionStatus.COMPLETED)
              .order("submitted_at", desc=True))
    if exam_id:
        sess_q = sess_q.eq("exam_id", exam_id)
    sessions = (await sess_q.execute()).data or []

    session_keys = [s["session_key"] for s in sessions]
    vcounts = await _violation_counts_by_session(session_keys)

    violations_by_session: dict[str, list[dict]] = {}
    if session_keys:
        all_viols = (await _atable("violations")
                     .select("session_key,violation_type,severity,created_at")
                     .eq("teacher_id", tid)
                     .in_("session_key", session_keys)
                     .execute()).data or []
        for v in all_viols:
            sk = v["session_key"]
            violations_by_session.setdefault(sk, []).append(v)

    exam_ids = list({s["exam_id"] for s in sessions if s.get("exam_id")})
    exam_titles: dict[str, str] = {}
    if exam_ids:
        configs = (await _atable("exam_config")
                   .select("exam_id,exam_title")
                   .eq("teacher_id", tid)
                   .in_("exam_id", exam_ids)
                   .execute()).data or []
        exam_titles = {c["exam_id"]: c.get("exam_title") or "Exam" for c in configs}

    history = []
    for s in sessions:
        sk = s["session_key"]
        viols = violations_by_session.get(sk, [])
        std_viols = [v for v in viols if v["violation_type"] not in _BEHAVIORAL_PATTERNS]
        behav_viols = [v for v in viols if v["violation_type"] in _BEHAVIORAL_PATTERNS]
        behav_patterns = list({v["violation_type"] for v in behav_viols})

        summary = generate_session_summary(viols, {
            "full_name": s.get("full_name", ""),
            "roll_number": s.get("roll_number", ""),
            "risk_score": s.get("risk_score"),
        })

        history.append({
            "session_id": sk,
            "exam_id": s.get("exam_id", ""),
            "exam_title": exam_titles.get(s.get("exam_id", ""), ""),
            "score": s.get("score", 0),
            "total": s.get("total", 0),
            "percentage": s.get("percentage", 0.0),
            "time_taken_secs": s.get("time_taken_secs", 0),
            "submitted_at": fmt_ist(s.get("submitted_at", "")),
            "started_at": fmt_ist(s.get("started_at", "")),
            "violation_count": vcounts.get(sk, 0),
            "standard_violations": len(std_viols),
            "behavioral_patterns": behav_patterns,
            "behavioral_violation_count": len(behav_viols),
            "risk_score": s.get("risk_score"),
            "risk_label": _risk_label(s["risk_score"]) if s.get("risk_score") is not None else None,
            "summary": summary,
        })

    start = (page - 1) * page_size
    end = start + page_size
    paginated = history[start:end]

    all_history = history
    scores = [h["percentage"] for h in all_history if h["percentage"] is not None]
    risk_scores = [h["risk_score"] for h in all_history if h["risk_score"] is not None]
    all_behav = set()
    for h in all_history:
        all_behav.update(h["behavioral_patterns"])

    aggregates = {
        "total_exams": len(all_history),
        "avg_percentage": round(sum(scores) / len(scores), 1) if scores else None,
        "highest_percentage": max(scores) if scores else None,
        "lowest_percentage": min(scores) if scores else None,
        "avg_risk_score": round(sum(risk_scores) / len(risk_scores)) if risk_scores else None,
        "highest_risk_score": max(risk_scores) if risk_scores else None,
        "total_violations": sum(h["violation_count"] for h in all_history),
        "total_behavioral_violations": sum(h["behavioral_violation_count"] for h in all_history),
        "behavioral_patterns_seen": sorted(all_behav),
    }

    return {
        "student": {
            "roll_number": student["roll_number"],
            "full_name": student.get("full_name", ""),
            "email": student.get("email", ""),
            "phone": student.get("phone", ""),
        },
        "aggregates": aggregates,
        "history": paginated,
        "page": page,
        "page_size": page_size,
        "total": len(all_history),
    }


@router.get("/api/v1/student-search")
@limiter.limit("30/minute")
async def search_students(request: Request, q: str = "", page: int = 1, page_size: int = 20):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    query = (_atable("students")
             .select("roll_number,full_name,email,phone,teacher_id")
             .eq("teacher_id", tid))
    if q:
        q_upper = q.strip().upper()
        query = query.or_(
            f"roll_number.ilike.*{q}*,full_name.ilike.*{q}*,email.ilike.*{q}*"
        )
    students = (await query.execute()).data or []
    if not students:
        return {"students": [], "page": page, "page_size": page_size, "total": 0}

    roll_numbers = [s["roll_number"] for s in students]

    all_sessions = (await _atable("exam_sessions")
                    .select("roll_number,session_key,percentage,risk_score,submitted_at,status")
                    .eq("teacher_id", tid)
                    .eq("status", SessionStatus.COMPLETED)
                    .in_("roll_number", roll_numbers)
                    .order("submitted_at", desc=True)
                    .execute()).data or []

    sessions_by_roll: dict[str, list[dict]] = {}
    for sess in all_sessions:
        sessions_by_roll.setdefault(sess["roll_number"], []).append(sess)

    result = []
    for s in students:
        roll = s["roll_number"]
        sess_list = sessions_by_roll.get(roll, [])
        last_exam = sess_list[0] if sess_list else None
        total_count = len(sess_list)
        percentages = [x["percentage"] for x in sess_list if x.get("percentage") is not None]
        avg_pct = round(sum(percentages) / len(percentages), 1) if percentages else None

        result.append({
            "roll_number": roll,
            "full_name": s.get("full_name", ""),
            "email": s.get("email", ""),
            "total_exams": total_count,
            "avg_percentage": avg_pct,
            "last_exam_date": fmt_ist(last_exam.get("submitted_at", "")) if last_exam else None,
            "last_exam_risk": last_exam.get("risk_score") if last_exam else None,
        })

    start = (page - 1) * page_size
    end = start + page_size
    return {
        "students": result[start:end],
        "page": page,
        "page_size": page_size,
        "total": len(result),
    }


@router.post("/api/v1/admin/register-students-bulk")
@limiter.limit("10/minute")
async def admin_bulk_register(request: Request, body: BulkRegisterIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    org_id = teacher.get("org_id")
    students = body.students
    if not students or not isinstance(students, list):
        raise HTTPException(status_code=400, detail="'students' must be a non-empty list")
    if len(students) > 500:
        raise HTTPException(status_code=400, detail="Max 500 students per batch")

    rows = []
    for s in students:
        roll = str(s.get("roll_number", "")).strip().upper()
        name = str(s.get("full_name", "")).strip()
        email = str(s.get("email", "")).strip().lower()
        phone = str(s.get("phone", "")).strip() or None
        if not roll or not name or not email:
            continue
        rows.append({
            "roll_number": roll,
            "full_name": name,
            "email": email,
            "phone": phone,
            "teacher_id": tid,
            "org_id": str(org_id) if org_id else None,
        })

    if not rows:
        raise HTTPException(status_code=400, detail="No valid students in payload")

    await check_org_limits(teacher, delta=len(rows))

    registered = 0
    skipped = 0
    for row in rows:
        try:
            await _atable("students").insert(row).execute()
            registered += 1
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                skipped += 1
            else:
                skipped += 1

    return {"registered": registered, "skipped": skipped, "total": len(rows)}


@router.get("/api/v1/admin/access-code")
@limiter.limit("60/minute")
async def get_access_code(request: Request):
    teacher = await require_admin(request)
    exam_id = request.query_params.get("exam_id")
    code = await _get_access_code(teacher["id"], exam_id=exam_id)
    return {"access_code": code, "enabled": bool(code)}


@router.post("/api/v1/admin/access-code")
@limiter.limit("10/minute")
async def set_access_code(request: Request, body: AccessCodeIn = Body(...)):
    teacher = await require_admin(request)
    exam_id = body.exam_id
    new_code = body.access_code.strip().upper()
    await _set_access_code(new_code, teacher["id"], exam_id=exam_id)
    if _cache:
        _cache.delete(f"exam_config:{teacher['id']}:{exam_id or '_'}")
    return {"access_code": new_code, "enabled": bool(new_code)}


@router.get("/api/v1/admin/registered-count")
@limiter.limit("60/minute")
async def registered_count(request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    query = _atable("students").select("roll_number", count="exact")
    if tid:
        query = query.eq("teacher_id", tid)
    result = await query.execute()
    return {"count": result.count if result.count is not None else len(result.data or [])}


__all__ = ["router"]

"""Students router. Extracted from admin.py."""

import csv
import io
import logging
from fastapi import APIRouter, Request, HTTPException, Body, UploadFile, File, Form
from fastapi.responses import PlainTextResponse
from ..auth import require_admin
from ..auth.scope import resolve_scope, scope_to_teacher_ids
from ..database import async_table as _atable
from .. import cache as _cache
from ..repositories.questions import load_exam_config as _load_exam_config, get_access_code as _get_access_code, set_access_code as _set_access_code
from ..repositories.sessions import violation_counts_by_session as _violation_counts_by_session
from ..services.risk import generate_session_summary, _risk_label
from ..utils import fmt_ist, now_ist
from ..models import SessionStatus
from ..limiter import limiter
from ..services.sessions import check_org_limits
from ..models import BulkRegisterIn, AccessCodeIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

_BEHAVIORAL_PATTERNS = frozenset({
    "phone_consulting", "collaboration", "answer_memo",
    "note_reading", "sustained_offtask", "nervous_evasion",
})

# CSV header alias mapping: each canonical key accepts 10+ common
# spellings so teachers can upload Google-Forms / Excel exports without
# renaming columns.
_HEADER_ALIASES: dict[str, set[str]] = {
    "roll_number": {
        "roll_number", "roll no", "rollno", "roll", "id",
        "student_id", "studentid", "roll#", "roll #",
        "enrollment", "enrollment_number", "enrollment no",
        "reg_no", "reg number", "reg", "registration",
    },
    "full_name": {
        "full_name", "fullname", "name",
        "student_name", "student name",
        "candidate name", "candidate_name",
        "first name", "first_name", "full name",
    },
    "email": {
        "email", "e-mail", "email address", "email_address",
        "mail", "student email", "student_email",
    },
    "phone": {
        "phone", "phone_number", "phonenumber",
        "mobile", "mobile_number", "mobilenumber",
        "contact", "contact_number", "contact no",
        "telephone", "phone no", "phone#", "phone #",
        "cell", "whatsapp",
    },
}


def _build_column_map(headers: list[str]) -> dict[str, str] | None:
    """Map CSV header names to canonical fields via _HEADER_ALIASES.

    Returns {canonical_key: actual_header} or None when required columns
    (roll_number, full_name, email) are missing.
    """
    col_map: dict[str, str] = {}
    for h in headers:
        hl = h.strip().lower()
        for canonical, aliases in _HEADER_ALIASES.items():
            if hl in aliases:
                col_map[canonical] = h
                break
    if "roll_number" not in col_map or "full_name" not in col_map or "email" not in col_map:
        return None
    return col_map


async def _process_student_rows(teacher: dict, rows: list[dict], dry_run: bool) -> dict:
    """Shared validation + registration logic used by both JSON and CSV endpoints.

    *rows* is a list of dicts each containing *roll_number*, *full_name*,
    *email*, and optionally *phone* keys.  Does NOT cap the input — callers
    are responsible for enforcing the 500-row limit before calling this.
    """
    tid = str(teacher["id"])
    org_id = teacher.get("org_id")

    validated: list[dict] = []
    invalid: list[dict] = []
    for s in rows:
        roll = str(s.get("roll_number", "")).strip().upper()
        name = str(s.get("full_name", "")).strip()
        email = str(s.get("email", "")).strip().lower()
        phone = str(s.get("phone", "")).strip() or None
        errors: list[str] = []
        if not roll:
            errors.append("missing roll_number")
        if not name:
            errors.append("missing full_name")
        elif len(name) > 200:
            errors.append("full_name exceeds 200 chars")
        if not email:
            errors.append("missing email")
        elif "@" not in email or "." not in email.split("@")[-1]:
            errors.append("invalid email format")
        if errors:
            invalid.append({"roll_number": roll or "(empty)", "errors": errors})
            continue
        validated.append({
            "roll_number": roll,
            "full_name": name,
            "email": email,
            "phone": phone,
            "teacher_id": tid,
            "org_id": str(org_id) if org_id else None,
        })

    from ..services.roll_formats import detect_dominant_format, format_label
    if validated:
        dominant_format, format_counts = detect_dominant_format(r["roll_number"] for r in validated)
    else:
        dominant_format, format_counts = "unknown", {}

    dominant_format_label = format_label(dominant_format) if dominant_format != "unknown" else "Unknown"

    if dry_run:
        result: dict = {
            "dry_run": True,
            "would_register": len(validated),
            "total": len(rows),
            "dominant_format": dominant_format,
            "dominant_format_label": dominant_format_label,
            "format_counts": format_counts,
        }
        if invalid:
            result["invalid"] = invalid
        return result

    if not validated:
        raise HTTPException(status_code=400, detail={"message": "No valid students in payload", "invalid": invalid})

    await check_org_limits(teacher, delta=len(validated))

    registered = 0
    skipped = 0
    for row in validated:
        try:
            result = await _atable("students").upsert(row, on_conflict="roll_number,teacher_id").execute()
            if result.data:
                registered += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1

    result = {
        "registered": registered,
        "skipped": skipped,
        "total": len(validated),
        "dominant_format": dominant_format,
        "dominant_format_label": dominant_format_label,
        "format_counts": format_counts,
    }
    if invalid:
        result["invalid"] = invalid
    return result


@router.get("/api/v1/admin/student-history")
@limiter.limit("30/minute")
async def list_student_history(request: Request, exam_id: str = None,
                               page: int = 1, page_size: int = 50):
    """List all students-in-scope with aggregate history stats.

    The React dashboard's HistoryPanel hits this for the directory view
    (then drills into /api/v1/student-history/{roll} for per-student
    detail). Scope respects the same teacher_id / org_id rules as the
    rest of the admin endpoints via resolve_scope().
    """
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    # Pull all completed sessions in scope, then bucket by roll_number.
    # Same scope-collapse trick used by build_sessions_payload etc.:
    # .eq() for single-teacher, .in_() for multi.
    sess_q = (_atable("exam_sessions")
              .select("session_key,roll_number,full_name,email,exam_id,"
                      "score,total,percentage,time_taken_secs,"
                      "submitted_at,risk_score,teacher_id")
              .in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
              .order("submitted_at", desc=True))
    if tids is not None:
        if not tids:
            sess_q = sess_q.eq("teacher_id", "__none__")
        elif len(tids) == 1:
            sess_q = sess_q.eq("teacher_id", str(tids[0]))
        else:
            sess_q = sess_q.in_("teacher_id", tids)
    if exam_id:
        sess_q = sess_q.eq("exam_id", exam_id)
    sessions = (await sess_q.limit(5000).execute()).data or []

    by_roll: dict[str, dict] = {}
    for s in sessions:
        roll = (s.get("roll_number") or "").upper()
        if not roll:
            continue
        agg = by_roll.setdefault(roll, {
            "roll_number":   roll,
            "full_name":     s.get("full_name") or "",
            "email":         s.get("email") or "",
            "teacher_id":    str(s.get("teacher_id") or ""),
            "total_exams":   0,
            "avg_percentage": 0.0,
            "_pct_sum":      0.0,
            "_pct_count":    0,
            "last_exam_at":  None,
            "last_exam_risk": None,
        })
        agg["total_exams"] += 1
        pct = s.get("percentage")
        if isinstance(pct, (int, float)):
            agg["_pct_sum"] += float(pct)
            agg["_pct_count"] += 1
        sub = s.get("submitted_at")
        if sub and (agg["last_exam_at"] is None or sub > agg["last_exam_at"]):
            agg["last_exam_at"] = sub
            agg["last_exam_risk"] = s.get("risk_score")

    rows = []
    for roll, agg in by_roll.items():
        if agg["_pct_count"]:
            agg["avg_percentage"] = round(agg["_pct_sum"] / agg["_pct_count"], 1)
        agg.pop("_pct_sum", None); agg.pop("_pct_count", None)
        agg["last_exam_at"] = fmt_ist(agg["last_exam_at"]) if agg["last_exam_at"] else ""
        rows.append(agg)
    # Default order: most-recently-active first.
    rows.sort(key=lambda r: r["last_exam_at"] or "", reverse=True)

    start = (page - 1) * page_size
    end = start + page_size
    return {
        "students": rows[start:end],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


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
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)
    roll = roll_number.strip().upper()
    if not roll:
        raise HTTPException(status_code=400, detail="roll_number is required")

    if tids is not None:
        student_q = (_atable("students")
                     .select("roll_number,full_name,email,phone,teacher_id")
                     .eq("roll_number", roll))
        student_q = student_q.in_("teacher_id", tids) if tids else student_q.eq("teacher_id", "__none__")
    else:
        student_q = (_atable("students")
                     .select("roll_number,full_name,email,phone,teacher_id")
                     .eq("roll_number", roll))
    student_rows = (await student_q.limit(1).execute()).data or []
    if not student_rows:
        raise HTTPException(status_code=404, detail="Student not found")
    student = student_rows[0]
    scoped_tid = str(student["teacher_id"])

    sess_q = (_atable("exam_sessions")
              .select("session_key,exam_id,roll_number,full_name,email,"
                      "score,total,percentage,time_taken_secs,"
                      "status,started_at,submitted_at,risk_score")
              .eq("roll_number", roll)
              .eq("teacher_id", scoped_tid)
              .in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
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
                     .eq("teacher_id", scoped_tid)
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
                   .eq("teacher_id", scoped_tid)
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
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    query = (_atable("students")
             .select("roll_number,full_name,email,phone,teacher_id"))
    if tids is not None:
        query = query.in_("teacher_id", tids) if tids else query.eq("teacher_id", "__none__")
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
                    .select("roll_number,session_key,percentage,risk_score,submitted_at,status,teacher_id")
                    .in_("status", [SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED])
                    .in_("roll_number", roll_numbers)
                    .order("submitted_at", desc=True)
                    .execute()).data or []
    if tids is not None:
        tid_set = set(tids)
        all_sessions = [s for s in all_sessions if str(s.get("teacher_id")) in tid_set]

    sessions_by_student: dict[tuple[str, str], list[dict]] = {}
    for sess in all_sessions:
        sessions_by_student.setdefault((str(sess.get("teacher_id")), sess["roll_number"]), []).append(sess)

    result = []
    for s in students:
        roll = s["roll_number"]
        sess_list = sessions_by_student.get((str(s.get("teacher_id")), roll), [])
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
    students = body.students
    if not students or not isinstance(students, list):
        raise HTTPException(status_code=400, detail="'students' must be a non-empty list")
    if len(students) > 500:
        raise HTTPException(status_code=400, detail="Max 500 students per batch")
    return await _process_student_rows(teacher, students, body.dry_run)


@router.post("/api/v1/admin/students/import-csv")
@limiter.limit("10/minute")
async def import_students_csv(
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Form(True),
):
    teacher = await require_admin(request)

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files accepted")

    raw = await file.read()
    if len(raw) > 1_048_576:  # 1 MB
        raise HTTPException(status_code=413, detail="File too large (max 1 MB)")

    text = raw.decode("utf-8-sig")

    try:
        dialect = csv.Sniffer().sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV appears empty or has no header row")

    col_map = _build_column_map(reader.fieldnames)
    if col_map is None:
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have at least roll_number, full_name, email columns. "
                   f"Found: {', '.join(reader.fieldnames)}",
        )

    students: list[dict[str, str]] = []
    for row in reader:
        student = {}
        for canonical, header in col_map.items():
            student[canonical] = row.get(header, "")
        students.append(student)

    if len(students) > 500:
        raise HTTPException(status_code=400, detail="Max 500 students per CSV")
    if not students:
        raise HTTPException(status_code=400, detail="CSV has no data rows")

    return await _process_student_rows(teacher, students, dry_run)


@router.get("/api/v1/admin/students/csv-template")
@limiter.limit("60/minute")
async def csv_template(request: Request):
    teacher = await require_admin(request)

    sample = (
        "roll_number,full_name,email,phone\n"
        "STU001,Alice Johnson,alice@example.com,9876543210\n"
        "STU002,Bob Smith,bob@example.com,9876543211\n"
    )
    return PlainTextResponse(
        content=sample,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=student_import_template.csv"},
    )


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

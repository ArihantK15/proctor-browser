"""Exams and groups router. Extracted from admin.py."""

import logging
from fastapi import APIRouter, Request, HTTPException, Body
from ..dependencies import (
    require_admin, _atable, _cache, _load_questions,
    _load_exam_config, _uuid, SessionStatus, generate_session_summary, limiter,
)
from ..models import (
    CreateExamIn, CreateGroupIn, RenameGroupIn,
    GroupMembersIn, ExamGroupAssignIn,
)

_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.get("/api/v1/admin/exams")
@limiter.limit("60/minute")
async def list_exams(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    result = await _atable("exam_config").select("*").eq("teacher_id", tid).execute()
    exams = result.data or []
    out = []
    for ex in exams:
        eid = ex.get("exam_id")
        qcount = 0
        scount = 0
        try:
            qr = await _atable("questions").select("question_id", count="exact")\
                .eq("teacher_id", tid).eq("exam_id", eid).execute()
            qcount = qr.count if qr.count is not None else len(qr.data or [])
        except Exception as e:
            logger.debug("Failed to count questions for exam %s: %s", eid, e)
        try:
            sr = await _atable("exam_sessions").select("session_key", count="exact")\
                .eq("teacher_id", tid).eq("exam_id", eid).execute()
            scount = sr.count if sr.count is not None else len(sr.data or [])
        except Exception as e:
            logger.debug("Failed to count sessions for exam %s: %s", eid, e)
        out.append({
            "exam_id":          eid,
            "exam_title":       ex.get("exam_title", "Exam"),
            "duration_minutes": ex.get("duration_minutes", 60),
            "starts_at":        ex.get("starts_at"),
            "ends_at":          ex.get("ends_at"),
            "access_code":      ex.get("access_code", ""),
            "question_count":   qcount,
            "session_count":    scount,
            "created_at":       ex.get("created_at", ""),
        })
    return {"exams": out}


@router.post("/api/v1/admin/exams")
@limiter.limit("10/hour")
async def create_exam(request: Request, body: CreateExamIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    title = body.exam_title.strip() or "New Exam"
    duration = body.duration_minutes
    exam_id = str(_uuid.uuid4())
    try:
        result = await _atable("exam_config").insert({
            "exam_id":          exam_id,
            "teacher_id":       tid,
            "exam_title":       title,
            "duration_minutes": duration,
        }).execute()
    except Exception as e:
        _admin_log.error("[CreateExam] DB error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create exam. Please try again.")
    row = result.data[0] if result.data else {}
    return {"exam_id": row.get("exam_id", exam_id), "exam_title": title, "duration_minutes": duration}


@router.delete("/api/v1/admin/exams/{exam_id}")
@limiter.limit("10/hour")
async def delete_exam(exam_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    check = await _atable("exam_config").select("exam_id")\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Exam not found")
    all_exams = await _atable("exam_config").select("exam_id")\
        .eq("teacher_id", tid).execute()
    if len(all_exams.data or []) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete your only exam")
    await _atable("questions").delete()\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    await _atable("exam_config").delete()\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
        _cache.delete(f"questions:{tid}:{exam_id or '_'}")
    return {"status": "deleted", "exam_id": exam_id}


@router.post("/api/v1/admin/exams/{exam_id}/duplicate")
@limiter.limit("10/hour")
async def duplicate_exam(exam_id: str, request: Request, body: dict = Body(default={})):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    src_q = (await _atable("exam_config").select("*")
             .eq("teacher_id", tid).eq("exam_id", exam_id).execute())
    if not src_q.data:
        raise HTTPException(status_code=404, detail="Exam not found")
    src = src_q.data[0]

    new_exam_id = str(_uuid.uuid4())
    src_title = src.get("exam_title") or "Exam"
    new_title = (str(body.get("new_title") or "").strip()
                 or f"{src_title} (copy)")

    COPYABLE = [
        "duration_minutes",
        "shuffle_questions", "shuffle_options",
    ]
    new_cfg = {
        "exam_id":    new_exam_id,
        "teacher_id": tid,
        "exam_title": new_title,
        "starts_at":  None,
        "ends_at":    None,
        "access_code": "",
    }
    for col in COPYABLE:
        if col in src and src[col] is not None:
            new_cfg[col] = src[col]

    try:
        await _atable("exam_config").insert(new_cfg).execute()
    except Exception as e:
        _admin_log.error("[DuplicateExam] config insert failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to clone config. Please try again.")

    try:
        qsrc = (await _atable("questions").select("*")
                .eq("teacher_id", tid).eq("exam_id", exam_id)
                .order("order_index").execute()).data or []
    except Exception as e:
        _admin_log.error("[DuplicateExam] question fetch failed: %s", e)
        try:
            await _atable("exam_config").delete()\
                .eq("teacher_id", tid).eq("exam_id", new_exam_id).execute()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to fetch source questions. Clone aborted.")

    questions_copied = 0
    if qsrc:
        new_rows = []
        for q in qsrc:
            row = dict(q)
            for k in ("id", "question_id", "created_at", "updated_at"):
                row.pop(k, None)
            row["exam_id"] = new_exam_id
            row["teacher_id"] = tid
            new_rows.append(row)
        try:
            for i in range(0, len(new_rows), 500):
                await _atable("questions").insert(new_rows[i:i+500]).execute()
                questions_copied += len(new_rows[i:i+500])
        except Exception as e:
            _admin_log.error("[DuplicateExam] question insert failed: %s", e)
            try:
                await _atable("exam_config").delete()\
                    .eq("teacher_id", tid).eq("exam_id", new_exam_id).execute()
                await _atable("questions").delete()\
                    .eq("teacher_id", tid).eq("exam_id", new_exam_id).execute()
            except Exception as rollback_err:
                logger.warning("Failed to rollback partial question clone: %s", rollback_err)
            raise HTTPException(status_code=500, detail=f"Failed to clone questions: {e}")

    if _cache:
        _cache.delete(f"exam_config:{tid}:{new_exam_id}")
        _cache.delete(f"questions:{tid}:{new_exam_id}")

    return {
        "status":           "duplicated",
        "source_exam_id":   exam_id,
        "exam_id":          new_exam_id,
        "exam_title":       new_title,
        "questions_copied": questions_copied,
    }


@router.get("/api/v1/admin/analytics")
@limiter.limit("20/minute")
async def get_analytics(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    exam_id = request.query_params.get("exam_id")

    cache_key = f"analytics:{tid}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached:
            return cached

    sess_q = _atable("exam_sessions")\
        .select("session_key,roll_number,full_name,score,total,percentage,time_taken_secs,risk_score,started_at")\
        .eq("status", SessionStatus.COMPLETED)
    if tid:
        sess_q = sess_q.eq("teacher_id", tid)
    if exam_id:
        sess_q = sess_q.eq("exam_id", exam_id)
    sessions = (await sess_q.execute()).data or []

    if not sessions:
        empty = {"exam_overview": {"count": 0}, "score_distribution": [],
                 "question_analysis": [], "violation_summary": {},
                 "risk_distribution": {"low": 0, "medium": 0, "high": 0}}
        if _cache:
            _cache.set(cache_key, empty, ttl=60)
        return empty

    count = len(sessions)
    pcts = [s.get("percentage") or 0 for s in sessions]
    times = [s.get("time_taken_secs") or 0 for s in sessions]
    scores = [s.get("score") or 0 for s in sessions]
    totals = [s.get("total") or 1 for s in sessions]
    avg_score = round(sum(scores) / count, 1)
    avg_pct = round(sum(pcts) / count, 1)
    sorted_times = sorted(t for t in times if t > 0)
    median_time = sorted_times[len(sorted_times)//2] if sorted_times else 0
    pass_count = sum(1 for p in pcts if p >= 40)
    overview = {
        "count": count,
        "avg_score": avg_score,
        "avg_total": round(sum(totals) / count, 1),
        "avg_percentage": avg_pct,
        "median_time_secs": median_time,
        "pass_rate": round(pass_count / count * 100, 1),
    }

    buckets = [0] * 10
    for p in pcts:
        idx = min(int(p // 10), 9)
        buckets[idx] += 1
    score_dist = [{"range": f"{i*10}-{i*10+10}%", "count": buckets[i]} for i in range(10)]

    questions = await _load_questions(tid, exam_id=exam_id)
    q_analysis = []
    if questions:
        skeys = [s["session_key"] for s in sessions]
        all_answers = {sk: {} for sk in skeys}
        for i in range(0, len(skeys), 50):
            chunk = skeys[i:i+50]
            ans_q = (_atable("answers")
                     .select("session_key,question_id,answer")
                     .in_("session_key", chunk))
            if tid:
                ans_q = ans_q.eq("teacher_id", tid)
            for r in (await ans_q.execute()).data or []:
                sk = r.get("session_key")
                qid = r.get("question_id")
                if sk and qid is not None:
                    all_answers.setdefault(sk, {})[qid] = r.get("answer")

        sorted_sess = sorted(sessions, key=lambda s: s.get("percentage") or 0)
        q1_cutoff = max(1, count // 4)
        bottom_keys = set(s["session_key"] for s in sorted_sess[:q1_cutoff])
        top_keys = set(s["session_key"] for s in sorted_sess[-q1_cutoff:])

        for q in questions:
            qid = str(q.get("question_id") or q.get("id", ""))
            correct = str(q.get("correct", ""))
            total_attempted = 0
            total_correct = 0
            top_correct = 0
            top_total = 0
            bottom_correct = 0
            bottom_total = 0
            for sk, ans_map in all_answers.items():
                if qid in ans_map:
                    total_attempted += 1
                    is_correct = ans_map[qid] == correct
                    if is_correct:
                        total_correct += 1
                    if sk in top_keys:
                        top_total += 1
                        if is_correct:
                            top_correct += 1
                    if sk in bottom_keys:
                        bottom_total += 1
                        if is_correct:
                            bottom_correct += 1
            difficulty = round(total_correct / max(total_attempted, 1) * 100, 1)
            top_rate = top_correct / max(top_total, 1)
            bottom_rate = bottom_correct / max(bottom_total, 1)
            discrimination = round(top_rate - bottom_rate, 2)
            q_analysis.append({
                "question_id": qid,
                "question": (q.get("question", "")[:80] + "...") if len(q.get("question", "")) > 80 else q.get("question", ""),
                "difficulty_pct": difficulty,
                "discrimination": discrimination,
                "attempted": total_attempted,
                "correct": total_correct,
            })

    viol_q = _atable("violations")\
        .select("violation_type,severity,session_key,created_at")
    if tid:
        viol_q = viol_q.eq("teacher_id", tid)
    viols = (await viol_q.execute()).data or []
    scored_viols = [v for v in viols if v.get("severity") in ("high", "medium")]

    type_counts = {}
    for v in scored_viols:
        vt = v["violation_type"]
        type_counts[vt] = type_counts.get(vt, 0) + 1
    viol_summary = {"by_type": type_counts, "total": len(scored_viols)}

    risk_dist = {"low": 0, "medium": 0, "high": 0}
    for s in sessions:
        rs = s.get("risk_score") or 0
        if rs <= 30:
            risk_dist["low"] += 1
        elif rs <= 60:
            risk_dist["medium"] += 1
        else:
            risk_dist["high"] += 1

    result = {
        "exam_overview": overview,
        "score_distribution": score_dist,
        "question_analysis": q_analysis,
        "violation_summary": viol_summary,
        "risk_distribution": risk_dist,
    }
    if _cache:
        _cache.set(cache_key, result, ttl=60)
    return result


@router.get("/api/v1/admin/groups")
@limiter.limit("60/minute")
async def list_groups(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    rows = (await _atable("student_groups")
            .select("*").eq("teacher_id", tid)
            .order("created_at").execute()).data or []
    counts: dict[str, int] = {}
    if rows:
        gids = [g["id"] for g in rows]
        members = (await _atable("student_group_members")
                   .select("group_id")
                   .in_("group_id", gids)
                   .eq("teacher_id", tid)
                   .limit(50000).execute()).data or []
        for m in members:
            gid = m.get("group_id")
            if gid:
                counts[gid] = counts.get(gid, 0) + 1
    for g in rows:
        g["member_count"] = counts.get(g["id"], 0)
    return rows


@router.post("/api/v1/admin/groups")
@limiter.limit("20/hour")
async def create_group(request: Request, body: CreateGroupIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    name = (body.group_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="group_name is required")
    try:
        row = (await _atable("student_groups")
               .insert({"teacher_id": tid, "group_name": name}).execute()).data
        return row[0] if row else {"ok": True}
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Group name already exists")
        raise


@router.put("/api/v1/admin/groups/{group_id}")
@limiter.limit("20/hour")
async def rename_group(group_id: str, request: Request, body: RenameGroupIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    name = body.group_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="group_name is required")
    result = (await _atable("student_groups")
              .update({"group_name": name})
              .eq("id", group_id).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Group not found")
    return result.data[0]


@router.delete("/api/v1/admin/groups/{group_id}")
@limiter.limit("20/hour")
async def delete_group(group_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    result = (await _atable("student_groups")
              .delete().eq("id", group_id).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"ok": True}


@router.get("/api/v1/admin/groups/{group_id}/members")
@limiter.limit("60/minute")
async def list_group_members(group_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    rows = (await _atable("student_group_members")
            .select("*").eq("group_id", group_id)
            .eq("teacher_id", tid).execute()).data or []
    if not rows:
        return []
    rolls = [r["roll_number"] for r in rows if r.get("roll_number")]
    if rolls:
        students = (await _atable("students")
                    .select("roll_number,email,full_name")
                    .eq("teacher_id", tid)
                    .in_("roll_number", rolls).execute()).data or []
        by_roll = {s["roll_number"]: s for s in students}
        for r in rows:
            s = by_roll.get(r.get("roll_number")) or {}
            r["email"] = s.get("email") or ""
            r["full_name"] = s.get("full_name") or ""
    return rows


@router.post("/api/v1/admin/groups/{group_id}/members")
@limiter.limit("20/minute")
async def add_group_members(group_id: str, request: Request, body: GroupMembersIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    grp = (await _atable("student_groups")
           .select("id").eq("id", group_id).eq("teacher_id", tid).execute()).data
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    rolls = body.roll_numbers
    if not rolls:
        raise HTTPException(status_code=400, detail="roll_numbers list is required")
    rows = [{"group_id": group_id, "roll_number": str(r).strip(), "teacher_id": tid}
            for r in rolls if str(r).strip()]
    if rows:
        await _atable("student_group_members").upsert(rows).execute()
    return {"added": len(rows)}


@router.delete("/api/v1/admin/groups/{group_id}/members")
@limiter.limit("20/minute")
async def remove_group_members(group_id: str, request: Request, body: GroupMembersIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    rolls = body.roll_numbers
    if not rolls:
        raise HTTPException(status_code=400, detail="roll_numbers list is required")
    for r in rolls:
        await _atable("student_group_members")\
            .delete().eq("group_id", group_id)\
            .eq("roll_number", str(r).strip())\
            .eq("teacher_id", tid).execute()
    return {"removed": len(rolls)}


@router.get("/api/v1/admin/exams/{exam_id}/groups")
@limiter.limit("60/minute")
async def list_exam_groups(exam_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    assignments = (await _atable("exam_group_assignments")
                   .select("group_id").eq("exam_id", exam_id)
                   .eq("teacher_id", tid).execute()).data or []
    if not assignments:
        return []
    gids = [a["group_id"] for a in assignments]
    groups = (await _atable("student_groups")
              .select("*").in_("id", gids).execute()).data or []
    return groups


@router.post("/api/v1/admin/exams/{exam_id}/groups")
@limiter.limit("20/minute")
async def assign_exam_groups(exam_id: str, request: Request, body: ExamGroupAssignIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    group_ids = body.group_ids
    if not group_ids:
        raise HTTPException(status_code=400, detail="group_ids list is required")
    rows = [{"exam_id": exam_id, "group_id": gid, "teacher_id": tid} for gid in group_ids]
    await _atable("exam_group_assignments").upsert(rows).execute()
    return {"assigned": len(rows)}


@router.delete("/api/v1/admin/exams/{exam_id}/groups/{group_id}")
@limiter.limit("20/hour")
async def unassign_exam_group(exam_id: str, group_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _atable("exam_group_assignments")\
        .delete().eq("exam_id", exam_id)\
        .eq("group_id", group_id)\
        .eq("teacher_id", tid).execute()
    return {"ok": True}


__all__ = ["router"]

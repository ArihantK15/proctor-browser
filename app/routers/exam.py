"""Student exam flow endpoints.

Extracted from main.py. All shared dependencies are imported from
the central ``dependencies`` module via relative imports.
"""

from ..dependencies import (
    supabase,
    _atable,
    _bus_async_publish,
    _cache,
    _HAS_REDIS,
    EventIn,
    ValidateIn,
    ResultIn,
    AnswerIn,
    BulkAnswerIn,
    FrameIn,
    IdVerifyIn,
    require_auth,
    create_token,
    fmt_ist,
    now_ist,
    _check_session_ownership,
    is_practice,
    _practice_validate_response,
    PRACTICE_QUESTIONS,
    _load_questions,
    _load_exam_config,
    _get_access_code,
    _check_group_access,
    _build_shuffle_view,
    _get_shuffle_flags,
    _recalculate_score,
    _canonicalise_student_answer,
    compute_risk_score,
    _assert_session_owned,
    SCREENSHOTS_DIR,
    get_logger,
    json,
    base64,
    time,
    os,
    Path,
    asyncio,
    datetime,
    timezone,
    HTTPException,
    Request,
    Body,
    limiter,
    SessionStatus,
    InviteStatus,
)

from fastapi import APIRouter

_MAX_FRAME_BASE64_LEN = 500_000  # ~375KB decoded, enough for a JPEG frame

BLOCKING_TYPES = {"vm_detected", "remote_desktop_detected", "vpn_detected",
                  "proxy_detected", "debugger_detected"}

router = APIRouter(prefix="")

# ─── STUDENT ENDPOINTS (require JWT) ─────────────────────────────

@router.post("/api/v1/validate-student")
@limiter.limit("300/minute")
async def validate_student(request: Request, body: ValidateIn):
    exam_id = body.exam_id

    # Practice sandbox: short-circuit before any DB lookups
    if is_practice(body.roll_number):
        return _practice_validate_response(body.roll_number.strip().upper())

    # Look up student first to get their teacher_id for config loading
    pre_check = await asyncio.to_thread(
        lambda: supabase.table("students")
            .select("teacher_id")
            .eq("roll_number", body.roll_number.strip().upper())
            .execute()
    )
    pre_tid = pre_check.data[0].get("teacher_id") if pre_check.data else None

    # Check exam time window using the student's teacher config
    config = await asyncio.to_thread(_load_exam_config, pre_tid, exam_id=exam_id)
    now_utc = datetime.now(timezone.utc)
    if config.get("starts_at"):
        starts = datetime.fromisoformat(str(config["starts_at"]).replace("Z", "+00:00"))
        if now_utc < starts:
            raise HTTPException(
                status_code=403,
                detail=f"The exam has not started yet. It begins at {fmt_ist(config['starts_at'])}.")
    if config.get("ends_at"):
        ends = datetime.fromisoformat(str(config["ends_at"]).replace("Z", "+00:00"))
        if now_utc > ends:
            raise HTTPException(
                status_code=403,
                detail=f"The exam window has closed. It ended at {fmt_ist(config['ends_at'])}.")

    # Look up student first (most common error = wrong roll number)
    result = await asyncio.to_thread(
        lambda: supabase.table("students")
            .select("*")
            .eq("roll_number", body.roll_number.strip().upper())
            .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="Roll number not found. Please complete registration first.")
    student = result.data[0]

    # Look up teacher's config for this student
    student_tid = student.get("teacher_id")

    # Check exam access code if configured
    current_code = await asyncio.to_thread(_get_access_code, student_tid, exam_id=exam_id)
    provided = (body.access_code or "").strip().upper()
    matched_invite_id = None
    if current_code:
        shared_ok = bool(provided) and provided == current_code
        invite_ok = False
        if not shared_ok and provided and student_tid:
            inv_q = (supabase.table("student_invites")
                     .select("id,access_code,status,expires_at,exam_id")
                     .eq("teacher_id", str(student_tid))
                     .eq("roll_number", student["roll_number"]))
            if exam_id:
                inv_q = inv_q.eq("exam_id", exam_id)
            inv_result = await asyncio.to_thread(inv_q.execute)
            for inv in (inv_result.data or []):
                code = (inv.get("access_code") or "").upper()
                if not code or code != provided:
                    continue
                if (inv.get("status") or "") == InviteStatus.REVOKED:
                    continue
                exp = inv.get("expires_at")
                if exp:
                    try:
                        if datetime.now(timezone.utc) > datetime.fromisoformat(
                                str(exp).replace("Z", "+00:00")):
                            continue
                    except Exception:
                        pass
                invite_ok = True
                matched_invite_id = inv["id"]
                break
        if not (shared_ok or invite_ok):
            raise HTTPException(
                status_code=403,
                detail="Invalid exam access code. Ask your examiner for the correct code.")
    # Check group-based access restrictions
    if exam_id and student_tid:
        if not await asyncio.to_thread(_check_group_access, student["roll_number"], str(student_tid), exam_id):
            raise HTTPException(
                status_code=403,
                detail="You are not in a group assigned to this exam. Contact your teacher.")

    def _check_completed():
        completed_query = supabase.table("exam_sessions").select("session_key")\
            .eq("roll_number", student["roll_number"])\
            .eq("status", SessionStatus.COMPLETED)
        if student_tid:
            completed_query = completed_query.eq("teacher_id", str(student_tid))
        if exam_id:
            completed_query = completed_query.eq("exam_id", exam_id)
        return completed_query.execute()

    completed = await asyncio.to_thread(_check_completed)
    if completed.data:
        raise HTTPException(
            status_code=403,
            detail="You have already submitted this exam.")

    # Also check for in-progress sessions to prevent duplicate tokens
    def _check_in_progress():
        in_progress_query = supabase.table("exam_sessions").select("session_key,status")\
            .eq("roll_number", student["roll_number"])\
            .eq("status", SessionStatus.IN_PROGRESS)
        if student_tid:
            in_progress_query = in_progress_query.eq("teacher_id", str(student_tid))
        if exam_id:
            in_progress_query = in_progress_query.eq("exam_id", exam_id)
        return in_progress_query.execute()

    in_progress = await asyncio.to_thread(_check_in_progress)
    if in_progress.data:
        # Student already has an active session
        existing_key = in_progress.data[0]["session_key"]
        return {
            "valid":       True,
            "full_name":   student["full_name"],
            "email":       student.get("email", ""),
            "phone":       student.get("phone", ""),
            "roll_number": student["roll_number"],
            "token":       create_token(student["roll_number"], student_tid, exam_id=exam_id),
            "existing_session": existing_key,
        }

    # Mark the invite as accepted
    if matched_invite_id:
        try:
            await asyncio.to_thread(
                lambda: supabase.table("student_invites").update({
                    "status": InviteStatus.ACCEPTED,
                    "accepted_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", matched_invite_id).execute()
            )
        except Exception as e:
            print(f"[invites] accept-mark failed: {e}")

    return {
        "valid":       True,
        "full_name":   student["full_name"],
        "email":       student.get("email", ""),
        "phone":       student.get("phone", ""),
        "roll_number": student["roll_number"],
        "token":       create_token(student["roll_number"], student_tid, exam_id=exam_id),
    }


@router.get("/api/v1/questions")
async def get_questions(request: Request):
    # Practice mode: serve the canned mock question set
    sid = (request.query_params.get("session_id") or "").strip()
    if is_practice(sid):
        return {"questions": PRACTICE_QUESTIONS, "practice": True}

    claims = require_auth(request)
    tid = claims.get("tid")
    eid = claims.get("eid")
    questions = await asyncio.to_thread(_load_questions, tid, exam_id=eid)
    if not questions:
        raise HTTPException(status_code=404, detail="Questions not found")
    config = await asyncio.to_thread(_load_exam_config, tid, exam_id=eid)

    # Deterministic per-session shuffle
    session_id = (request.query_params.get("session_id") or "").strip()
    shuffle_q, shuffle_o = _get_shuffle_flags(config)
    shuffled, _ = _build_shuffle_view(
        questions, session_id, str(tid or ""),
        shuffle_q=shuffle_q, shuffle_o=shuffle_o)

    # Strip correct answers — students must never see them
    safe_questions = [{k: v for k, v in q.items() if k != "correct"} for q in shuffled]
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "duration_minutes": config.get("duration_minutes"),
        "questions": safe_questions,
    }


@router.get("/api/v1/check-session/{roll_number}")
def check_session(roll_number: str, request: Request):
    """Check if student has an in-progress session to resume."""
    # Practice mode never has a resumable session
    if is_practice(roll_number):
        return {"exists": False}

    claims = require_auth(request)
    if claims.get("roll") != roll_number:
        raise HTTPException(status_code=403, detail="Access denied")
    tid = claims.get("tid")
    eid = claims.get("eid")
    sess_query = supabase.table("exam_sessions").select("*")\
        .eq("roll_number", roll_number)\
        .eq("status", SessionStatus.IN_PROGRESS)
    if tid:
        sess_query = sess_query.eq("teacher_id", str(tid))
    if eid:
        sess_query = sess_query.eq("exam_id", eid)
    result = sess_query.order("started_at", desc=True).limit(1).execute()
    if not result.data:
        return {"exists": False}
    session = result.data[0]
    ans_query = supabase.table("answers").select("*")\
        .eq("session_key", session["session_key"])
    if tid:
        ans_query = ans_query.eq("teacher_id", str(tid))

    answers = ans_query.execute()

    # Build reverse map for shuffled options
    session_key = session["session_key"]
    config = _load_exam_config(str(tid or ""), exam_id=eid)
    shuffle_q, shuffle_o = _get_shuffle_flags(config)
    reverse: dict[str, dict[str, str]] = {}
    if shuffle_o:
        try:
            questions = _load_questions(str(tid or ""), exam_id=eid)
            _, label_maps = _build_shuffle_view(
                questions, session_key, str(tid or ""),
                shuffle_q=shuffle_q, shuffle_o=shuffle_o)
            for qid, qmap in label_maps.items():
                reverse[qid] = {orig: disp for disp, orig in qmap.items()}
        except Exception as e:
            print(f"[Resume] reverse map failed: {e}")

    resumed = {}
    for r in (answers.data or []):
        qid = str(r["question_id"])
        canonical = str(r["answer"])
        disp = reverse.get(qid, {}).get(canonical, canonical)
        resumed[qid] = disp

    return {
        "exists":      True,
        "session_key": session_key,
        "answer_count": len(resumed),
        "answers":     resumed,
        "started_at":  session.get("started_at"),
    }


# ── INTEGRITY REPORT (pre-exam security check) ──────────────────

@router.post("/api/v1/integrity-report")
@limiter.limit("10/minute")
async def integrity_report(request: Request):
    """Accept a batch of integrity flags from the Electron client."""
    body = await request.json()
    session_id = body.get("session_id", "")
    # Practice sandbox: don't enforce integrity blocks
    if is_practice(session_id):
        return {"allowed": True, "blocked_reasons": []}

    claims = require_auth(request)
    flags = body.get("flags", [])
    _check_session_ownership(claims, session_id)
    tid = claims.get("tid")

    blocked_reasons = []
    for f in flags:
        ftype = f.get("type", "")
        sev = f.get("severity", "low")
        details = f.get("details", "")
        # Log each flag as a violation
        viol_row = {
            "session_key":    session_id,
            "violation_type": ftype,
            "severity":       sev,
            "details":        f"[Integrity] {details}",
        }
        if tid:
            viol_row["teacher_id"] = tid
        await _atable("violations").insert(viol_row).execute()
        # Check if this flag should block exam start
        if ftype in BLOCKING_TYPES and sev == "high":
            blocked_reasons.append(f"{ftype}: {details}")

    # Publish summary to teacher dashboard
    if tid and flags:
        summary = {
            "type": "integrity_report",
            "severity": "high" if blocked_reasons else "medium",
            "session_id": session_id,
            "details": f"{len(flags)} integrity flag(s), {len(blocked_reasons)} blocking",
            "flags": [{"type": f.get("type"), "severity": f.get("severity"),
                       "details": f.get("details")} for f in flags],
        }
        await _bus_async_publish(f"sessions:{tid}", {**summary, "kind": "violation"})

    allowed = len(blocked_reasons) == 0
    return {"allowed": allowed, "blocked_reasons": blocked_reasons,
            "flags_received": len(flags)}


@router.post("/api/v1/event")
@limiter.limit("600/minute")
async def log_event(event: EventIn, request: Request):
    # Practice sandbox: log to stdout only
    if is_practice(event.session_id):
        return {"status": "logged", "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    get_logger(event.session_id).info(
        f"[{event.severity.upper()}] {event.event_type} | {event.details}")

    # When exam starts, create in-progress session record
    if event.event_type == "exam_started":
        row = {
            "session_key": event.session_id,
            "roll_number": event.session_id.rsplit("_", 1)[0],
            "status":      SessionStatus.IN_PROGRESS,
            "started_at":  now_ist().isoformat(),
        }
        if tid:
            row["teacher_id"] = tid
        if eid:
            row["exam_id"] = eid
        await _atable("exam_sessions").upsert(row).execute()

    # Alert on submission failure
    if event.event_type == "submit_failed":
        print(f"[ALERT] SUBMIT FAILED for session {event.session_id} "
              f"— use /api/v1/admin-submit/{event.session_id} to recover")

    viol_row = {
        "session_key":    event.session_id,
        "violation_type": event.event_type,
        "severity":       event.severity,
        "details":        event.details,
    }
    if tid:
        viol_row["teacher_id"] = tid
    await _atable("violations").insert(viol_row).execute()

    # Invalidate cached risk score for this session
    if _cache:
        _cache.delete(f"risk_score:{event.session_id}")

    # Publish to Redis for SSE subscribers
    evt_payload = {"type": event.event_type, "severity": event.severity,
                   "details": event.details, "session_id": event.session_id}
    if tid:
        await _bus_async_publish(f"events:{tid}:{event.session_id}", evt_payload)
        await _bus_async_publish(f"sessions:{tid}", {**evt_payload, "kind": "violation"})

    return {"status": "logged"}


@router.post("/api/v1/heartbeat")
async def heartbeat(event: EventIn, request: Request):
    # Practice sandbox: don't track heartbeats
    if is_practice(event.session_id):
        return {"ok": True, "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")

    # Check if session already exists and is completed
    existing = await _atable("exam_sessions").select("status")\
        .eq("session_key", event.session_id).execute()

    if existing.data and existing.data[0].get("status") == SessionStatus.COMPLETED:
        return {"ok": True}

    if existing.data:
        # Session exists — UPDATE only heartbeat + status
        await _atable("exam_sessions").eq("session_key", event.session_id)\
            .update({
                "last_heartbeat": now_ist().isoformat(),
                "status":         SessionStatus.IN_PROGRESS,
            }).execute()
    else:
        # No session row yet — INSERT
        row = {
            "session_key":    event.session_id,
            "roll_number":    event.session_id.rsplit("_", 1)[0],
            "last_heartbeat": now_ist().isoformat(),
            "status":         SessionStatus.IN_PROGRESS,
        }
        if tid:
            row["teacher_id"] = tid
        if eid:
            row["exam_id"] = eid
        await _atable("exam_sessions").upsert(row).execute()

    # Publish heartbeat to dashboard SSE
    if tid:
        await _bus_async_publish(f"sessions:{tid}", {"kind": "heartbeat",
                     "session_id": event.session_id})

    return {"ok": True}


@router.post("/api/v1/save-answer")
async def save_answer(body: AnswerIn, request: Request):
    # Practice sandbox: pretend the save succeeded
    if is_practice(body.session_id):
        return {"status": "saved", "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    canonical = await asyncio.to_thread(
        _canonicalise_student_answer,
        body.session_id, str(tid or ""), str(body.question_id), str(body.answer),
        eid)
    row = {
        "session_key":  body.session_id,
        "question_id":  body.question_id,
        "answer":       canonical,
    }
    if tid:
        row["teacher_id"] = tid
    if eid:
        row["exam_id"] = eid
    await _atable("answers").upsert(row).execute()
    return {"status": "saved"}


@router.post("/api/v1/save-answers-bulk")
async def save_answers_bulk(body: BulkAnswerIn, request: Request):
    """Periodic bulk save of all answers — safety net for failed individual saves."""
    if is_practice(body.session_id):
        return {"status": "saved", "saved": len(body.answers or {}), "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    if not body.answers:
        return {"status": "empty", "saved": 0}
    tid = claims.get("tid")
    eid = claims.get("eid")
    def _build_records():
        recs = []
        for qid, ans in body.answers.items():
            canonical = _canonicalise_student_answer(
                body.session_id, str(tid or ""), str(qid), str(ans),
                eid)
            rec = {"session_key": body.session_id,
                   "question_id": str(qid),
                   "answer":      canonical}
            if tid:
                rec["teacher_id"] = tid
            if eid:
                rec["exam_id"] = eid
            recs.append(rec)
        return recs
    records = await asyncio.to_thread(_build_records)
    await _atable("answers").upsert(records).execute()
    return {"status": "saved", "saved": len(records)}


@router.post("/api/v1/submit-exam")
@limiter.limit("60/minute")
async def submit_exam(result: ResultIn, request: Request):
    # Practice sandbox: grade against the in-memory PRACTICE_QUESTIONS
    if is_practice(result.session_id):
        correct = sum(
            1 for q in PRACTICE_QUESTIONS
            if str(result.answers.get(str(q["question_id"]),
                                      result.answers.get(str(q["id"]), ""))).upper()
               == str(q["correct"]).upper()
        )
        total = len(PRACTICE_QUESTIONS)
        pct = round((correct / max(total, 1)) * 100, 1)
        return {
            "status": SessionStatus.SUBMITTED,
            "score": correct,
            "total": total,
            "percentage": pct,
            "passed": True,
            "practice": True,
        }

    claims = require_auth(request)
    _check_session_ownership(claims, result.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    now = now_ist()

    # SECURITY: Use JWT roll, not client-supplied fields (IDOR prevention)
    jwt_roll = claims.get("roll", "")
    trusted_roll = jwt_roll.upper()

    # Guard: Block re-submission of already-completed sessions
    existing = await _atable("exam_sessions").select("status")\
        .eq("session_key", result.session_id).execute()
    if existing.data and existing.data[0].get("status") == SessionStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Exam already submitted")

    # Phase 1: Score + config in parallel
    score_fut = asyncio.to_thread(
        _recalculate_score, result.session_id, result.answers,
        teacher_id=tid, exam_id=eid)
    config_fut = asyncio.to_thread(_load_exam_config, teacher_id=tid, exam_id=eid)
    try:
        (server_score, server_total), config = await asyncio.gather(score_fut, config_fut)
    except RuntimeError as e:
        print(f"[SUBMIT] Score calculation failed for {result.session_id}: {e}")
        raise HTTPException(status_code=503,
                            detail="Score calculation temporarily unavailable. Please retry.")

    if server_score == 0 and server_total == 0:
        print(f"[WARN] Score recalculation returned 0/0 for {result.session_id}")

    pct = round((server_score / max(server_total, 1)) * 100, 1)

    # Phase 2: Session upsert + submission log + time check in parallel
    session_row = {
        "session_key":     result.session_id,
        "roll_number":     trusted_roll,
        "full_name":       result.full_name,
        "email":           result.email,
        "score":           server_score,
        "total":           server_total,
        "percentage":      pct,
        "time_taken_secs": result.time_taken_secs,
        "status":          SessionStatus.COMPLETED,
        "submitted_at":    now.isoformat(),
    }
    if tid:
        session_row["teacher_id"] = tid
    if eid:
        session_row["exam_id"] = eid

    submit_viol = {
        "session_key":    result.session_id,
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        f"Score:{server_score}/{server_total} ({pct}%)",
    }
    if tid:
        submit_viol["teacher_id"] = tid

    parallel_ops = [
        _atable("exam_sessions").upsert(session_row).execute(),
        _atable("violations").insert(submit_viol).execute(),
    ]

    # Time exceeded check
    allowed_secs = config.get("duration_minutes", 60) * 60
    if result.time_taken_secs > allowed_secs + 120:
        time_viol = {
            "session_key":    result.session_id,
            "violation_type": "time_exceeded",
            "severity":       "high",
            "details":        f"Submitted {result.time_taken_secs - allowed_secs}s past time limit",
        }
        if tid:
            time_viol["teacher_id"] = tid
        parallel_ops.append(_atable("violations").insert(time_viol).execute())

    # Execute parallel ops
    results = await asyncio.gather(*parallel_ops, return_exceptions=True)
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"[SUBMIT] Phase 2 op {i} failed for {result.session_id}: {r}")
            if i == 0:
                raise HTTPException(status_code=500,
                                    detail="Failed to save exam submission. Please retry.")

    # Phase 3: Risk score
    risk = await asyncio.to_thread(compute_risk_score, result.session_id, teacher_id=tid)
    upd = _atable("exam_sessions").eq("session_key", result.session_id)
    if tid:
        upd = upd.eq("teacher_id", str(tid))
    await upd.update({"risk_score": risk["risk_score"]}).execute()

    get_logger(result.session_id).info(
        f"[SUBMIT] {trusted_roll} score:{server_score}/{server_total} "
        f"risk:{risk['risk_score']}/100")

    # Publish submission to dashboard SSE
    if tid:
        asyncio.create_task(_bus_async_publish(f"sessions:{tid}", {"kind": "submitted",
                     "session_id": result.session_id,
                     "score": server_score, "total": server_total}))

    return {"status": SessionStatus.SUBMITTED, "score": server_score,
            "total": server_total, "percentage": pct,
            "risk_score": risk["risk_score"], "risk_label": risk["label"]}


@router.post("/api/v1/analyze-frame")
def analyze_frame(data: FrameIn, request: Request):
    # Practice sandbox: don't run face detection or save screenshots
    if is_practice(data.session_id):
        return {"status": "ok", "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, data.session_id)
    tid = claims.get("tid")

    # Size limit: reject oversized payloads
    if len(data.frame) > _MAX_FRAME_BASE64_LEN:
        raise HTTPException(status_code=413,
                            detail=f"Frame too large ({len(data.frame)} chars). Max {_MAX_FRAME_BASE64_LEN}.")

    # Sanitize roll/tid for path safety
    raw_roll = data.session_id.rsplit("_", 1)[0] if "_" in data.session_id \
               else data.session_id[:20]
    roll = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw_roll)[:40]
    safe_tid = "".join(c if c.isalnum() or c in "_-" else "_" for c in (tid or ""))[:40]

    if safe_tid:
        student_dir = os.path.join(SCREENSHOTS_DIR, safe_tid, roll)
    else:
        student_dir = os.path.join(SCREENSHOTS_DIR, roll)

    # Verify resolved path is under SCREENSHOTS_DIR
    real_dir = os.path.realpath(student_dir)
    real_base = os.path.realpath(SCREENSHOTS_DIR)
    if not real_dir.startswith(real_base + os.sep) and real_dir != real_base:
        raise HTTPException(status_code=400, detail="Invalid session identifier")

    try:
        os.makedirs(student_dir, exist_ok=True)
        ts = now_ist().strftime("%Y%m%d_%H%M%S")
        if data.event_type:
            safe_label = "".join(
                c if c.isalnum() or c in "_-" else "_"
                for c in data.event_type
            )[:32]
            fname = f"evt_{safe_label}_{ts}.jpg"
        else:
            fname = f"frame_{ts}.jpg"
        fpath = os.path.join(student_dir, fname)
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(data.frame))
    except Exception as e:
        print(f"[Frame] Error saving frame for {data.session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save frame")
    return {"status": "received"}


@router.post("/api/v1/id-verification")
def id_verification(data: IdVerifyIn, request: Request):
    """Store selfie + ID photos and create a pending verification for teacher review."""
    # Practice sandbox: pretend the verification was filed
    if is_practice(data.session_id):
        return {"status": SessionStatus.SUBMITTED, "verification_id": "practice", "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, data.session_id)
    tid = claims.get("tid")

    # Size guard
    for field_name, field_val in [("selfie_frame", data.selfie_frame), ("id_frame", data.id_frame)]:
        if len(field_val) > _MAX_FRAME_BASE64_LEN:
            raise HTTPException(status_code=413, detail=f"{field_name} too large")

    # Sanitize roll for filesystem safety
    raw_roll = data.roll_number.strip().upper() or "UNKNOWN"
    roll = "".join(c if c.isalnum() or c in "_-" else "_" for c in raw_roll)[:40]
    safe_tid = "".join(c if c.isalnum() or c in "_-" else "_" for c in (tid or ""))[:40]

    if safe_tid:
        student_dir = os.path.join(SCREENSHOTS_DIR, safe_tid, roll)
    else:
        student_dir = os.path.join(SCREENSHOTS_DIR, roll)

    # Path traversal guard
    real_dir = os.path.realpath(student_dir)
    real_base = os.path.realpath(SCREENSHOTS_DIR)
    if not real_dir.startswith(real_base + os.sep) and real_dir != real_base:
        raise HTTPException(status_code=400, detail="Invalid roll number")

    try:
        os.makedirs(student_dir, exist_ok=True)
        ts = now_ist().strftime("%Y%m%d_%H%M%S")

        selfie_fname = f"id_selfie_{ts}.jpg"
        id_fname     = f"id_card_{ts}.jpg"
        with open(os.path.join(student_dir, selfie_fname), "wb") as f:
            f.write(base64.b64decode(data.selfie_frame))
        with open(os.path.join(student_dir, id_fname), "wb") as f:
            f.write(base64.b64decode(data.id_frame))
    except Exception as e:
        print(f"[ID Verify] File save error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save verification images")

    try:
        detail_obj = {
            "status":       "pending",
            "selfie_file":  selfie_fname,
            "id_file":      id_fname,
            "roll_number":  roll,
            "full_name":    data.full_name,
            "exam_id":      claims.get("eid") or "",
        }
        viol_row = {
            "session_key":    data.session_id,
            "violation_type": "id_verification",
            "severity":       "low",
            "details":        json.dumps(detail_obj),
        }
        if tid:
            viol_row["teacher_id"] = str(tid)
        supabase.table("violations").insert(viol_row).execute()
    except Exception as e:
        print(f"[ID Verify] DB error: {e}")

    return {"status": "received"}


@router.get("/api/v1/id-verification/status")
def id_verification_status(request: Request, session_id: str = ""):
    """Student polls this to check if teacher has approved/retake/rejected."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    # Practice sandbox: auto-approve
    if is_practice(session_id):
        return {"status": "approved", "practice": True}

    claims = require_auth(request)
    _check_session_ownership(claims, session_id)
    result = supabase.table("violations")\
        .select("details")\
        .eq("session_key", session_id)\
        .eq("violation_type", "id_verification")\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    if not result.data:
        return {"status": "not_found"}
    raw = result.data[0].get("details", "")
    try:
        obj = json.loads(raw)
        return {"status": obj.get("status", "pending")}
    except Exception:
        return {"status": "pending"}


@router.get("/api/v1/events/{session_id}")
def get_events(session_id: str, request: Request):
    claims = require_auth(request)
    # Ownership check
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    tid = claims.get("tid")
    query = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)
    if tid:
        query = query.eq("teacher_id", str(tid))
    result = query.order("created_at").execute()
    events = result.data or []
    return {
        "session_id": session_id,
        "total":      len(events),
        "events": [
            {
                "id":        e.get("id") or ts_to_id(e.get("created_at", "")),
                "type":      e["violation_type"],
                "severity":  e["severity"],
                "timestamp": fmt_ist(e.get("created_at", "")),
                "details":   e.get("details"),
            }
            for e in events
        ],
    }

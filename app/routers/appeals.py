"""Student appeals — dispute violations or grades."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict

from ..auth import require_admin, require_student_account
from ..database import async_table as _atable
from ..limiter import limiter

_log = logging.getLogger("appeals")

router = APIRouter(prefix="/api/v1", tags=["appeals"])


class AppealIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_key: str
    appeal_type: str  # 'violation' | 'grade' | 'other'
    description: str


class AppealResolveIn(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str  # 'accepted' | 'rejected'
    teacher_note: str = ""


# ── Student: submit an appeal ──────────────────────────────────────


@router.post("/student/appeal")
@limiter.limit("5/hour")
async def submit_appeal(body: AppealIn, request: Request):
    """Submit a dispute for a violation or grade."""
    account = await require_student_account(request)
    student_id = str(account.get("id", ""))
    student_email = account.get("email", "")

    # Verify the session belongs to this student
    session = await _atable("exam_sessions").select("*").eq("session_key", body.session_key).limit(1).execute()
    if not session.data:
        raise HTTPException(status_code=404, detail="Session not found")
    s = session.data[0]
    if str(s.get("student_id", "")).upper() != student_id.upper() and \
       str(s.get("email", "")).lower() != student_email.lower():
        raise HTTPException(status_code=403, detail="Session does not belong to you")

    teacher_id = s.get("teacher_id", "")
    exam_id = s.get("exam_id", "")

    await _atable("appeals").insert({
        "session_key":  body.session_key,
        "student_id":   student_id,
        "email":        student_email,
        "exam_id":      exam_id,
        "teacher_id":   teacher_id,
        "appeal_type":  body.appeal_type,
        "description":  body.description[:1000],
        "status":       "pending",
    }).execute()

    return {"status": "submitted", "message": "Your appeal has been submitted for teacher review."}


# ── Student: read back appeal statuses ────────────────────────────


@router.get("/student/appeals")
@limiter.limit("20/minute")
async def list_student_appeals(request: Request):
    """Return all appeals submitted by the logged-in student, newest first."""
    account = await require_student_account(request)
    student_id = str(account.get("id", ""))

    result = await _atable("appeals")\
        .select("id,session_key,exam_id,appeal_type,description,status,teacher_note,created_at,resolved_at")\
        .eq("student_id", student_id)\
        .order("created_at", desc=True)\
        .limit(50)\
        .execute()
    return {"appeals": result.data or []}


# ── Teacher: list appeals ──────────────────────────────────────────


@router.get("/admin/appeals")
@limiter.limit("30/minute")
async def list_appeals(request: Request, exam_id: str = None, status: str = None):
    """List appeals for the teacher's exams."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    q = _atable("appeals").select("*").eq("teacher_id", tid)
    if exam_id:
        q = q.eq("exam_id", exam_id)
    if status:
        q = q.eq("status", status)
    q = q.order("created_at", desc=True)

    result = await q.execute()
    return {"appeals": result.data or []}


# ── Teacher: resolve an appeal ─────────────────────────────────────


@router.post("/admin/appeals/{appeal_id}/resolve")
@limiter.limit("30/minute")
async def resolve_appeal(appeal_id: str, body: AppealResolveIn, request: Request):
    """Accept or reject a student appeal."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    appeal = await _atable("appeals").select("*").eq("id", appeal_id).eq("teacher_id", tid).limit(1).execute()
    if not appeal.data:
        raise HTTPException(status_code=404, detail="Appeal not found")

    now = datetime.now(timezone.utc).isoformat()
    await _atable("appeals").update({
        "status": body.status,
        "teacher_note": body.teacher_note[:500],
        "resolved_at": now,
    }).eq("id", appeal_id).execute()

    return {"status": "resolved"}

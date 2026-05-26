"""Invites and templates router. Extracted from admin.py."""

import logging
import uuid as _uuid
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Body
from ..auth import require_admin
from ..database import async_table as _atable
from .. import cache as _cache
from ..repositories.questions import load_exam_config as _load_exam_config, load_questions as _load_questions
from ..invites import _get_invite_base_url, _new_invite_token, _claim_and_bump_cap
from ..utils import now_ist, fmt_ist
from ..models import InviteStatus
from ..limiter import limiter
from ..jobs import enqueue_job, send_invite_email_job
from ..models import SendInvitesBody, SaveTemplateIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.post("/api/v1/admin/invites/send")
@limiter.limit("5/minute")
async def send_invites(body: SendInvitesBody, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    # Idempotency check
    if body.idempotency_key:
        from ..services.idempotency import check_idempotency, mark_idempotent, idempotency_key as _idk
        _k = _idk("invite-send", tid, body.idempotency_key)
        cached = await check_idempotency(_k)
        if cached:
            return cached

    base_url = _get_invite_base_url()

    ok, remaining = await _claim_and_bump_cap(tid, len(body.recipients))
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f"Daily cap exceeded. {remaining} remaining, {len(body.recipients)} requested."
        )

    exam_cfg = (await _atable("exam_config")
                .select("*")
                .eq("teacher_id", tid).eq("exam_id", body.exam_id).execute()).data
    exam_title = exam_cfg[0].get("exam_title", body.exam_id) if exam_cfg else body.exam_id

    results = {"sent": 0, "failed": 0, "skipped": 0}
    for rec in body.recipients:
        token = _new_invite_token()
        invite_url = f"{base_url}/invite/{token}"
        download_url = f"{base_url}/download"

        invite_row = {
            "id": _uuid.uuid4(),
            "teacher_id": tid,
            "email": rec.email.strip().lower(),
            "full_name": rec.full_name,
            "roll_number": rec.roll_number.strip().upper(),
            "exam_id": body.exam_id,
            "token": token,
            "status": InviteStatus.SENT,
            "sent_at": now_ist().isoformat(),
            "access_code": None,
            "custom_message": body.custom_message,
        }

        (await _atable("student_invites")
         .upsert(invite_row, on_conflict="teacher_id,email,exam_id")
         .execute())

        send_result = enqueue_job(
            send_invite_email_job,
            to_email=rec.email,
            to_name=rec.full_name,
            exam_title=exam_title,
            invite_url=invite_url,
            download_url=download_url,
            roll_number=rec.roll_number,
            teacher_name=teacher.get("email"),
        )
        if send_result is None:
            results["sent"] += 1
        elif send_result.get("ok"):
            (await _atable("student_invites")
             .update({"provider_msg_id": send_result["provider_msg_id"]})
             .eq("teacher_id", tid).eq("email", rec.email.strip().lower())
             .eq("exam_id", body.exam_id).execute())
            results["sent"] += 1
        else:
            results["failed"] += 1

    if body.idempotency_key and tid:
        try:
            from ..services.idempotency import mark_idempotent
            await mark_idempotent(_k, results)
        except Exception:
            pass

    return results


@router.get("/api/v1/admin/invites")
@limiter.limit("30/minute")
async def list_invites(request: Request, exam_id: Optional[str] = None):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    base_url = _get_invite_base_url()

    query = (_atable("student_invites")
             .select("*")
             .eq("teacher_id", tid)
             .order("sent_at", desc=True))
    if exam_id:
        query = query.eq("exam_id", exam_id)
    result = await query.execute()

    invites = []
    for row in result.data or []:
        token = row.get("token") or ""
        invites.append({
            "id": row.get("id"),
            "email": row.get("email"),
            "full_name": row.get("full_name"),
            "roll_number": row.get("roll_number"),
            "exam_id": row.get("exam_id"),
            "token_prefix": token[:8] if token else "",
            "status": row.get("status"),
            "invite_url": f"{base_url}/invite/{token}" if token else "",
            "sent_at": row.get("sent_at"),
            "opened_at": row.get("opened_at"),
            "bounced_at": row.get("bounced_at"),
            "provider_msg_id": row.get("provider_msg_id"),
        })
    return {"invites": invites, "total": len(invites)}


@router.delete("/api/v1/admin/invites/{invite_id}")
@limiter.limit("20/hour")
async def revoke_invite(invite_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    result = (await _atable("student_invites")
              .select("id,teacher_id,status")
              .eq("id", invite_id).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Invite not found")
    if result.data[0].get("teacher_id") != tid:
        raise HTTPException(status_code=403, detail="Not your invite")

    (await _atable("student_invites")
     .update({"status": InviteStatus.REVOKED, "revoked_at": now_ist().isoformat()})
     .eq("id", invite_id).execute())
    return {"ok": True, "invite_id": invite_id}


@router.post("/api/v1/templates")
@limiter.limit("10/hour")
async def save_template(request: Request, body: SaveTemplateIn):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    cfg = await _load_exam_config(tid, exam_id=body.exam_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Exam not found")

    template_data = {
        "exam_title": cfg.get("exam_title") or cfg.get("title") or "Exam",
        "duration_minutes": cfg.get("duration_minutes", 60),
        "access_code": cfg.get("access_code", ""),
        "shuffle_questions": cfg.get("shuffle_questions", False),
        "shuffle_options": cfg.get("shuffle_options", False),
    }

    questions = []
    if body.include_questions:
        questions = await _load_questions(tid, exam_id=body.exam_id)

    row = {
        "teacher_id": tid,
        "template_name": body.template_name.strip(),
        "exam_title": template_data["exam_title"],
        "duration_minutes": template_data["duration_minutes"],
        "access_code": template_data["access_code"],
        "shuffle_questions": template_data["shuffle_questions"],
        "shuffle_options": template_data["shuffle_options"],
        "questions": questions,
    }
    result = (await _atable("exam_templates")
              .insert(row)
              .execute())
    template_id = result.data[0]["id"] if result.data else None
    return {"ok": True, "template_id": template_id, "questions_count": len(questions)}


@router.get("/api/v1/templates")
@limiter.limit("60/minute")
async def list_templates(request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    result = (await _atable("exam_templates")
              .select("id,template_name,exam_title,duration_minutes,access_code,"
                      "shuffle_questions,shuffle_options,created_at,questions")
              .eq("teacher_id", tid)
              .order("created_at", desc=True)
              .execute())
    templates = []
    for t in (result.data or []):
        templates.append({
            "id": t["id"],
            "template_name": t.get("template_name", ""),
            "exam_title": t.get("exam_title", ""),
            "duration_minutes": t.get("duration_minutes", 60),
            "access_code_required": bool(t.get("access_code", "").strip()),
            "shuffle_questions": t.get("shuffle_questions", False),
            "shuffle_options": t.get("shuffle_options", False),
            "questions_count": len(t.get("questions") or []),
            "created_at": fmt_ist(t.get("created_at", "")),
        })
    return {"templates": templates}


@router.post("/api/v1/templates/{template_id}/create-exam")
@limiter.limit("10/hour")
async def create_exam_from_template(template_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    t_result = (await _atable("exam_templates")
                .select("*")
                .eq("id", template_id)
                .eq("teacher_id", tid)
                .execute())
    if not t_result.data:
        raise HTTPException(status_code=404, detail="Template not found")
    tmpl = t_result.data[0]

    import uuid as _uuid_mod
    new_exam_id = str(_uuid_mod.uuid4())

    config_row = {
        "exam_id": new_exam_id,
        "teacher_id": tid,
        "exam_title": tmpl.get("exam_title", "New Exam"),
        "duration_minutes": tmpl.get("duration_minutes", 60),
        "access_code": tmpl.get("access_code", ""),
        "shuffle_questions": tmpl.get("shuffle_questions", False),
        "shuffle_options": tmpl.get("shuffle_options", False),
    }
    (await _atable("exam_config")
     .insert(config_row)
     .execute())

    questions = tmpl.get("questions") or []
    if questions:
        for q in questions:
            q["id"] = str(_uuid_mod.uuid4())
        (await _atable("questions")
         .insert(questions)
         .execute())

    return {
        "ok": True,
        "exam_id": new_exam_id,
        "exam_title": config_row["exam_title"],
        "questions_copied": len(questions),
    }


@router.delete("/api/v1/templates/{template_id}")
@limiter.limit("20/hour")
async def delete_template(template_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    result = (await _atable("exam_templates")
              .delete()
              .eq("id", template_id)
              .eq("teacher_id", tid)
              .execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True}


__all__ = ["router"]

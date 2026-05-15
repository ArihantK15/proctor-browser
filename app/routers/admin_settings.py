"""Settings router — schedule, shuffle, and proctoring sensitivity."""
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, ConfigDict

from ..auth import require_admin
from ..repositories.questions import load_exam_config as _load_exam_config
from ..database import async_table as _atable
from .. import cache as _cache
from ..limiter import limiter
from ..models import ScheduleIn, ShuffleIn

router = APIRouter(prefix="")


@router.get("/api/v1/admin/exam-schedule")
@limiter.limit("60/minute")
async def admin_get_schedule(request: Request):
    teacher = await require_admin(request)
    exam_id = request.query_params.get("exam_id")
    config = await _load_exam_config(teacher["id"], exam_id=exam_id)
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "starts_at":  config.get("starts_at"),
        "ends_at":    config.get("ends_at"),
    }


@router.post("/api/v1/admin/exam-schedule")
@limiter.limit("20/minute")
async def admin_set_schedule(request: Request, body: ScheduleIn = Body(...)):
    teacher = await require_admin(request)
    tid = teacher["id"]
    exam_id = body.exam_id

    update = {}
    if body.starts_at is not None:
        update["starts_at"] = body.starts_at
    if body.ends_at is not None:
        update["ends_at"] = body.ends_at
    if update:
        await _atable("exam_config").update(update)\
            .eq("teacher_id", tid).eq("exam_id", exam_id).execute()

    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id}")
    return {
        "status":    "updated",
        "starts_at": body.starts_at,
        "ends_at":   body.ends_at,
    }


@router.get("/api/v1/admin/shuffle-config")
@limiter.limit("60/minute")
async def admin_get_shuffle(request: Request):
    teacher = await require_admin(request)
    exam_id = request.query_params.get("exam_id")
    config = await _load_exam_config(teacher["id"], exam_id=exam_id)
    sq, so = config.get("shuffle_questions", True), config.get("shuffle_options", True)
    return {"shuffle_questions": sq, "shuffle_options": so, "phone_camera_enabled": config.get("phone_camera_enabled", False)}


@router.post("/api/v1/admin/shuffle-config")
@limiter.limit("20/minute")
async def admin_set_shuffle(request: Request, body: ShuffleIn = Body(...)):
    teacher = await require_admin(request)
    tid = teacher["id"]
    exam_id = body.exam_id
    fields: dict = {}
    if body.shuffle_questions is not None:
        fields["shuffle_questions"] = body.shuffle_questions
    if body.shuffle_options is not None:
        fields["shuffle_options"] = body.shuffle_options
    if not fields:
        raise HTTPException(status_code=400, detail="No shuffle fields provided")
    if tid and exam_id:
        await _atable("exam_config").update(fields)\
            .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    else:
        update = {**({"teacher_id": tid} if tid else {"id": 1}), **fields}
        await _atable("exam_config").upsert(update).execute()
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
    return {
        "status": "updated",
        "shuffle_questions": fields.get("shuffle_questions"),
        "shuffle_options":   fields.get("shuffle_options"),
    }


class SensitivityIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id: str
    proctoring_sensitivity: Optional[str] = None


@router.get("/api/v1/admin/proctoring-sensitivity")
@limiter.limit("60/minute")
async def admin_get_sensitivity(request: Request):
    teacher = await require_admin(request)
    exam_id = request.query_params.get("exam_id")
    config = await _load_exam_config(teacher["id"], exam_id=exam_id)
    return {
        "proctoring_sensitivity": config.get("proctoring_sensitivity", "balanced"),
    }


@router.post("/api/v1/admin/proctoring-sensitivity")
@limiter.limit("20/minute")
async def admin_set_sensitivity(request: Request, body: SensitivityIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    exam_id = body.exam_id
    value = body.proctoring_sensitivity or "balanced"
    valid = {"strict", "balanced", "lenient"}
    if value not in valid:
        raise HTTPException(status_code=400, detail=f"Must be one of: {', '.join(sorted(valid))}")
    await _atable("exam_config").update({"proctoring_sensitivity": value})\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id}")
    return {"status": "updated", "proctoring_sensitivity": value}


__all__ = ["router"]

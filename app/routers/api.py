"""Public REST API and API key management endpoints.

Key management  — admin-only, under the Settings panel.
Public API      — X-API-Key auth, programmatic access to exam data.
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from ..limiter import limiter

from ..auth.api_auth import generate_api_key, revoke_api_key, list_api_keys, authenticate_api_key
from ..auth.admin_auth import require_admin
from ..models.api_key import ApiKeyCreate, ApiKeyOut, ApiKeyCreated
from ..database import async_table as _atable

router = APIRouter(prefix="/api/v1", tags=["api"])


# ─── API Key Management (admin-only) ──────────────────────────────

@router.post("/admin/api-keys", response_model=ApiKeyCreated)
async def create_api_key(body: ApiKeyCreate, teacher=Depends(require_admin)):
    key_id, raw_key = await generate_api_key(str(teacher["id"]), body.name)
    return {"id": key_id, "name": body.name, "key": raw_key}


@router.get("/admin/api-keys", response_model=list[ApiKeyOut])
async def get_api_keys(teacher=Depends(require_admin)):
    keys = await list_api_keys(str(teacher["id"]))
    return [ApiKeyOut(
        id=k["id"], name=k["name"], key_prefix=k.get("key_prefix", ""),
        created_at=k.get("created_at"), last_used_at=k.get("last_used_at"),
        is_active=k.get("is_active", True),
    ) for k in keys]


@router.delete("/admin/api-keys/{key_id}")
async def delete_api_key(key_id: str, teacher=Depends(require_admin)):
    ok = await revoke_api_key(key_id, str(teacher["id"]))
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "revoked"}


# ─── Public REST API (X-API-Key auth) ─────────────────────────────

async def _require_api(request: Request) -> str:
    return await authenticate_api_key(request)


@router.get("/exams")
@limiter.limit("30/minute")
async def api_list_exams(request: Request, tid: str = Depends(_require_api)):
    result = await _atable("exam_configs").select(
        "id,exam_title,starts_at,ends_at,duration_minutes,access_code,created_at"
    ).eq("teacher_id", tid).order("created_at", desc=True).execute()
    return result.data or []


@router.get("/exams/{exam_id}")
@limiter.limit("30/minute")
async def api_get_exam(exam_id: str, request: Request, tid: str = Depends(_require_api)):
    result = await _atable("exam_configs").select("*")\
        .eq("id", exam_id).eq("teacher_id", tid).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Exam not found")
    return result.data[0]


@router.get("/exams/{exam_id}/students")
@limiter.limit("30/minute")
async def api_exam_students(exam_id: str, request: Request, tid: str = Depends(_require_api)):
    result = await _atable("students").select("*")\
        .eq("teacher_id", tid).execute()
    return result.data or []


@router.get("/exams/{exam_id}/sessions")
@limiter.limit("30/minute")
async def api_exam_sessions(exam_id: str, request: Request, tid: str = Depends(_require_api)):
    result = await _atable("exam_sessions").select("*")\
        .eq("exam_id", exam_id).eq("teacher_id", tid)\
        .order("started_at", desc=True).execute()
    return result.data or []


@router.get("/exams/{exam_id}/sessions/{session_key}")
@limiter.limit("30/minute")
async def api_session_detail(exam_id: str, session_key: str, request: Request, tid: str = Depends(_require_api)):
    result = await _atable("exam_sessions").select("*")\
        .eq("session_key", session_key).eq("teacher_id", tid).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")
    return result.data[0]


@router.get("/students/{roll_number}")
@limiter.limit("30/minute")
async def api_get_student(roll_number: str, request: Request, tid: str = Depends(_require_api)):
    result = await _atable("students").select("*")\
        .eq("roll_number", roll_number.strip().upper()).eq("teacher_id", tid).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Student not found")
    return result.data[0]

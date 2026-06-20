"""Settings router — schedule, shuffle, and proctoring sensitivity."""
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, ConfigDict

from ..auth import require_admin
from ..repositories.questions import load_exam_config as _load_exam_config
from ..database import async_table as _atable
from .. import cache as _cache
from ..limiter import limiter
from ..models import ScheduleIn, ShuffleIn, SessionStatus

# Sessions in one of these states have a finished attempt on record — they show
# as "completed" to the student and won't retake on a reschedule unless the
# teacher resets them. Mirrors the "done" set in student_exams' status logic.
_ATTEMPTED_STATUSES = [
    SessionStatus.COMPLETED, SessionStatus.SUBMITTED, SessionStatus.FORCE_SUBMITTED,
]
from ..services.false_positive import normalize_sensitivity, SENSITIVITY_PRESETS

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
        "early_join_minutes": config.get("early_join_minutes", 15),
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
    if body.early_join_minutes is not None:
        # Clamp to the same 0..240 range the DB CHECK enforces.
        update["early_join_minutes"] = max(0, min(int(body.early_join_minutes), 240))
    if update:
        await _atable("exam_config").update(update)\
            .eq("teacher_id", tid).eq("exam_id", exam_id).execute()

    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id}")

    # If students have already submitted this exam, a reschedule alone won't let
    # them retake — their finished session still reads as "completed". Surface
    # the count so the dashboard can OFFER a one-click reset (never forced). Only
    # relevant when the schedule actually changed; a no-op save warns about
    # nothing. Best-effort: a count hiccup must not fail the save.
    attempted_count = 0
    if update:
        try:
            done = await _atable("exam_sessions").select("session_key", count="exact")\
                .eq("teacher_id", str(tid)).eq("exam_id", exam_id)\
                .in_("status", _ATTEMPTED_STATUSES).execute()
            attempted_count = done.count or 0
        except Exception:
            attempted_count = 0

    return {
        "status":    "updated",
        "starts_at": body.starts_at,
        "ends_at":   body.ends_at,
        "early_join_minutes": update.get("early_join_minutes", body.early_join_minutes),
        "attempted_count": attempted_count,
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
    sensitivity = normalize_sensitivity(config.get("proctoring_sensitivity"))
    return {
        "proctoring_sensitivity": sensitivity,
        "profile": SENSITIVITY_PRESETS[sensitivity],
        "presets": SENSITIVITY_PRESETS,
    }


@router.post("/api/v1/admin/proctoring-sensitivity")
@limiter.limit("20/minute")
async def admin_set_sensitivity(request: Request, body: SensitivityIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    exam_id = body.exam_id
    raw_value = (body.proctoring_sensitivity or "balanced").strip().lower()
    if raw_value not in SENSITIVITY_PRESETS:
        valid = ", ".join(sorted(SENSITIVITY_PRESETS))
        raise HTTPException(status_code=400, detail=f"Must be one of: {valid}")
    value = normalize_sensitivity(raw_value)
    await _atable("exam_config").update({"proctoring_sensitivity": value})\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id}")
    return {
        "status": "updated",
        "proctoring_sensitivity": value,
        "profile": SENSITIVITY_PRESETS[value],
    }


# ─── Audio keyword detection (phase 75) ──────────────────────────────
#
# Per-exam keyword list extending the built-in defaults shipped with
# the proctor daemon. Built-in defaults cover common cheat phrases
# ("option a", "the answer is", etc.); teachers can add exam-specific
# phrases ("periodic table", "newton's third law", etc.). Capped at 50
# entries × 80 chars each to bound storage + DB row size.

_AUDIO_LANGS = ("en", "hi", "en+hi")
_MAX_KEYWORDS = 50
_MAX_KEYWORD_LEN = 80
_MIN_KEYWORD_LEN = 2


class AudioKeywordsIn(BaseModel):
    model_config = ConfigDict(strict=True)
    exam_id:                  str
    audio_keywords:           Optional[list[str]] = None  # None = clear back to defaults
    audio_keywords_language:  Optional[str] = None        # one of _AUDIO_LANGS


def _normalise_keywords(raw) -> list[str]:
    """Strip, dedupe (case-insensitive), drop too-short / too-long, cap
    at _MAX_KEYWORDS. Returns the cleaned list; raises HTTPException
    when an individual entry is rejected so the teacher gets a useful
    error instead of silent truncation."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise HTTPException(status_code=400,
                            detail="audio_keywords must be a list of strings")
    seen: set[str] = set()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise HTTPException(status_code=400,
                                detail="Each keyword must be a string")
        s = entry.strip()
        if not s:
            continue
        if len(s) < _MIN_KEYWORD_LEN:
            raise HTTPException(status_code=400,
                                detail=f"Keyword '{s}' is too short (min {_MIN_KEYWORD_LEN} chars)")
        if len(s) > _MAX_KEYWORD_LEN:
            raise HTTPException(status_code=400,
                                detail=f"Keyword too long (max {_MAX_KEYWORD_LEN} chars)")
        lower = s.lower()
        if lower in seen:
            continue
        seen.add(lower)
        out.append(s)
        if len(out) > _MAX_KEYWORDS:
            raise HTTPException(status_code=400,
                                detail=f"Too many keywords (max {_MAX_KEYWORDS})")
    return out


@router.get("/api/v1/admin/audio-keywords")
@limiter.limit("60/minute")
async def admin_get_audio_keywords(request: Request):
    teacher = await require_admin(request)
    exam_id = request.query_params.get("exam_id")
    config = await _load_exam_config(teacher["id"], exam_id=exam_id)
    raw = config.get("audio_keywords")
    keywords: list[str] = []
    if raw:
        try:
            import json as _json
            parsed = _json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                keywords = [str(k) for k in parsed]
        except (ValueError, TypeError):
            keywords = []
    lang = (config.get("audio_keywords_language") or "en").strip()
    if lang not in _AUDIO_LANGS:
        lang = "en"
    return {
        "audio_keywords":          keywords,
        "audio_keywords_language": lang,
        "supported_languages":     list(_AUDIO_LANGS),
        "max_keywords":            _MAX_KEYWORDS,
        "max_keyword_length":      _MAX_KEYWORD_LEN,
    }


@router.post("/api/v1/admin/audio-keywords")
@limiter.limit("20/minute")
async def admin_set_audio_keywords(request: Request, body: AudioKeywordsIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    exam_id = body.exam_id
    # Language
    lang = (body.audio_keywords_language or "en").strip()
    if lang not in _AUDIO_LANGS:
        raise HTTPException(status_code=400,
                            detail=f"audio_keywords_language must be one of: {', '.join(_AUDIO_LANGS)}")
    # Keywords — None / empty means "clear back to defaults"
    cleaned = _normalise_keywords(body.audio_keywords) if body.audio_keywords else []
    import json as _json
    update = {
        "audio_keywords":          _json.dumps(cleaned) if cleaned else None,
        "audio_keywords_language": lang,
    }
    await _atable("exam_config").update(update)\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id}")
    return {
        "status":                  "updated",
        "audio_keywords":          cleaned,
        "audio_keywords_language": lang,
    }


# ─── Per-org MFA enforcement (gap #20) ───────────────────────────────
#
# Org-level toggle that forces every member through the email-OTP 2FA
# step at login (see enforcement in app/routers/auth.py). Admin/superadmin
# only — a regular teacher must not be able to weaken or strengthen the
# org's security posture. Writes to organizations.require_2fa (phase103).


class Require2faIn(BaseModel):
    model_config = ConfigDict(strict=True)
    require_2fa: bool


@router.get("/api/v1/admin/require-2fa")
@limiter.limit("60/minute")
async def admin_get_require_2fa(request: Request):
    teacher = await require_admin(request)
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")
    row = (await _atable("organizations").select("require_2fa")
           .eq("id", str(org_id)).limit(1).execute()).data
    enabled = bool(row and row[0].get("require_2fa"))
    return {"require_2fa": enabled}


@router.post("/api/v1/admin/require-2fa")
@limiter.limit("20/minute")
async def admin_set_require_2fa(request: Request, body: Require2faIn = Body(...)):
    teacher = await require_admin(request)
    if teacher.get("org_role") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only admins can change the MFA policy")
    org_id = teacher.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization associated")
    await _atable("organizations").update({"require_2fa": body.require_2fa})\
        .eq("id", str(org_id)).execute()
    return {"status": "updated", "require_2fa": body.require_2fa}


__all__ = ["router"]

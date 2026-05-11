"""Live view router — teacher live camera feed endpoints."""
import base64
import logging

from fastapi import APIRouter, Request
from starlette.responses import Response

from ..dependencies import (
    require_admin, _assert_session_owned, _cache, limiter, now_ist,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/start")
@limiter.limit("30/minute")
async def live_view_start(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.set(f"liveview:{session_id}", {"tid": tid, "started_at": now_ist().isoformat()},
                   ttl=60)
    return {"ok": True, "session_id": session_id, "ttl_sec": 60}


@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/keepalive")
@limiter.limit("60/minute")
async def live_view_keepalive(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.set(f"liveview:{session_id}", {"tid": tid, "renewed_at": now_ist().isoformat()},
                   ttl=60)
    return {"ok": True}


@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/stop")
@limiter.limit("30/minute")
async def live_view_stop(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.delete(f"liveview:{session_id}")
        _cache.delete(f"liveframe:{session_id}")
    return {"ok": True}


@router.get("/api/v1/admin/sessions/{session_id:path}/live-frame")
@limiter.limit("30/minute")
async def live_view_frame(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if not _cache:
        return Response(status_code=204)
    payload = _cache.get(f"liveframe:{session_id}")
    if not payload or not isinstance(payload, dict):
        return Response(status_code=204)

    jpeg = payload.get("jpeg_bytes")
    if jpeg:
        return Response(content=jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store, max-age=0"})

    b64 = payload.get("jpeg_b64")
    if not b64:
        return Response(status_code=204)
    try:
        jpeg = base64.b64decode(b64)
    except Exception:
        return Response(status_code=204)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store, max-age=0"})


@router.post("/api/v1/admin/sessions/{session_id:path}/live-view/force-stop")
@limiter.limit("10/minute")
async def live_view_force_stop(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.delete(f"liveview:{session_id}")
        _cache.delete(f"liveframe:{session_id}")
    return {"ok": True}

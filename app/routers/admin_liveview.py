"""Live view router — teacher live camera feed endpoints."""
import base64
import logging

from fastapi import APIRouter, Request
from starlette.responses import Response

from ..auth import require_admin
from ..repositories.sessions import assert_session_owned as _assert_session_owned
from .. import cache as _cache
from ..limiter import limiter
from ..utils import now_ist

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
                        headers={"Cache-Control": "no-store, max-age=0",
                                 "X-Content-Type-Options": "nosniff"})

    b64 = payload.get("jpeg_b64")
    if not b64:
        return Response(status_code=204)
    try:
        jpeg = base64.b64decode(b64)
    except Exception:
        return Response(status_code=204)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store, max-age=0",
                             "X-Content-Type-Options": "nosniff"})


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


# ─── ROOM CAMERA (phone) ────────────────────────────────────────


@router.post("/api/v1/admin/sessions/{session_id:path}/room-cam/start")
@limiter.limit("30/minute")
async def room_cam_start(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)

    # Audit log: if viewing post-exam room cam, record the access
    from ..database import async_table as _atable
    sess = await _atable("exam_sessions").select("status").eq("session_key", session_id).limit(1).execute()
    sess_data = sess.data[0] if sess.data else {}
    if sess_data.get("status") in ("completed", "submitted", "force_submitted"):
        await _atable("violations").insert({
            "session_key": session_id,
            "teacher_id": tid,
            "violation_type": "room_cam_post_exam_viewed",
            "severity": "low",
            "details": f"Teacher {tid} viewed post-exam room camera footage",
        }).execute()
        logger.info("[audit] teacher=%s viewed post-exam room cam session=%s", tid, session_id)

    if _cache:
        _cache.set(f"roomcam:{session_id}", {"tid": tid, "started_at": now_ist().isoformat()}, ttl=60)
    return {"ok": True, "session_id": session_id, "ttl_sec": 60}


@router.post("/api/v1/admin/sessions/{session_id:path}/room-cam/keepalive")
@limiter.limit("60/minute")
async def room_cam_keepalive(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.set(f"roomcam:{session_id}", {"tid": tid, "renewed_at": now_ist().isoformat()}, ttl=60)
    return {"ok": True}


@router.post("/api/v1/admin/sessions/{session_id:path}/room-cam/stop")
@limiter.limit("30/minute")
async def room_cam_stop(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if _cache:
        _cache.delete(f"roomcam:{session_id}")
        _cache.delete(f"roomframe:{session_id}")
    return {"ok": True}


@router.get("/api/v1/admin/sessions/{session_id:path}/room-cam/frame")
@limiter.limit("30/minute")
async def room_cam_frame(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    if not _cache:
        return Response(status_code=204)
    payload = _cache.get(f"roomframe:{session_id}")
    if not payload or not isinstance(payload, dict):
        return Response(status_code=204)
    jpeg = payload.get("jpeg_bytes")
    if jpeg:
        return Response(content=jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store, max-age=0",
                                 "X-Content-Type-Options": "nosniff"})
    return Response(status_code=204)


@router.post("/api/v1/admin/sessions/{session_id:path}/room-cam/approve")
@limiter.limit("30/minute")
async def room_cam_approve(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    from ..database import async_table as _atable
    await _atable("exam_sessions").update({
        "room_cam_status": "approved",
        "room_cam_approved_at": now_ist().isoformat(),
    }).eq("session_key", session_id).execute()
    return {"ok": True, "status": "approved"}


@router.post("/api/v1/admin/sessions/{session_id:path}/room-cam/reject")
@limiter.limit("30/minute")
async def room_cam_reject(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    from ..database import async_table as _atable
    await _atable("exam_sessions").update({
        "room_cam_status": "rejected",
    }).eq("session_key", session_id).execute()
    return {"ok": True, "status": "rejected"}


@router.get("/api/v1/admin/sessions/{session_id:path}/room-cam/status")
@limiter.limit("30/minute")
async def room_cam_status(session_id: str, request: Request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _assert_session_owned(session_id, tid)
    from ..database import async_table as _atable
    row = await _atable("exam_sessions").select("room_cam_status,room_cam_approved_at").eq("session_key", session_id).limit(1).execute()
    data = row.data[0] if row.data else {}
    return {"status": data.get("room_cam_status", "disabled"), "approved_at": data.get("room_cam_approved_at")}

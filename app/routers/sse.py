import asyncio
import base64
import json
import time
from fastapi import APIRouter, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
from jose import jwt, JWTError
from pydantic import BaseModel, ConfigDict

from ..dependencies import (
    supabase, _bus_subscribe, _HAS_REDIS, _get_teacher_by_id,
    require_admin, verify_admin_token,
    _build_sessions_payload, _cache, _bus_async_publish,
    SECRET_KEY, require_auth, now_ist
)

router = APIRouter(prefix="")


def _store_live_frame(session_id: str, jpeg_bytes: bytes):
    """Store live frame using Redis LRU-capped cache if available."""
    if _cache and hasattr(_cache, 'set_live_frame'):
        _cache.set_live_frame(session_id, jpeg_bytes, ttl=10)


# ─── LEGACY HTTP LIVE-FRAME (v2.2.0 backward compat) ──────────────

class LiveFrameIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    jpeg_b64: str


@router.post("/api/v1/proctor/live-frame")
async def upload_live_frame_http(body: LiveFrameIn):
    """Legacy endpoint: proctor.py POSTs a base64 JPEG every ~1.5s.

    Stored in Redis via LRU-capped cache so the teacher dashboard
    can poll via GET /api/v1/admin/sessions/{sid}/live-frame.
    v2.2.0 clients still use this — new clients prefer WS binary.
    """
    try:
        jpeg_bytes = base64.b64decode(body.jpeg_b64)
    except Exception:
        return Response(status_code=400)

    _store_live_frame(body.session_id, jpeg_bytes)

    # Notify subscribed dashboards via Redis pub/sub
    if _HAS_REDIS:
        await _bus_async_publish(
            f"liveframe:{body.session_id}",
            {"session_id": body.session_id, "at": time.time()},
        )

    return Response(status_code=204)


# ─── WEBSOCKET BINARY LIVE-FEED ───────────────────────────────────

_ws_clients: dict[str, list[WebSocket]] = {}
_ws_lock = asyncio.Lock()
_ws_conn_count: dict[str, int] = {}
MAX_WS_PER_SESSION = 3
MAX_WS_MSG_BYTES = 200 * 1024  # 200 KB — enough for HD JPEG


async def _ws_subscribe(session_id: str, ws: WebSocket):
    async with _ws_lock:
        _ws_clients.setdefault(session_id, []).append(ws)


async def _ws_unsubscribe(session_id: str, ws: WebSocket):
    async with _ws_lock:
        clients = _ws_clients.get(session_id, [])
        if ws in clients:
            clients.remove(ws)
        if not clients:
            _ws_clients.pop(session_id, None)
        # Also decrement connection counter
        cnt = _ws_conn_count.get(session_id, 0)
        if cnt > 0:
            _ws_conn_count[session_id] = cnt - 1
        if _ws_conn_count.get(session_id, 0) <= 0:
            _ws_conn_count.pop(session_id, None)


async def _ws_broadcast(session_id: str, frame_bytes: bytes):
    async with _ws_lock:
        clients = list(_ws_clients.get(session_id, []))
    dead = []
    for c in clients:
        try:
            await c.send_bytes(frame_bytes)
        except Exception:
            dead.append(c)
    # We already know the session_id — only clean up from it
    if dead:
        async with _ws_lock:
            clients = _ws_clients.get(session_id, [])
            for c in dead:
                try:
                    clients.remove(c)
                except ValueError:
                    pass
            if not clients:
                _ws_clients.pop(session_id, None)


_WS_CLEANUP_STARTED = False

async def _ws_ensure_cleanup():
    global _WS_CLEANUP_STARTED
    if not _WS_CLEANUP_STARTED:
        _WS_CLEANUP_STARTED = True
        asyncio.create_task(_ws_cleanup())


_WS_STALE_SEC = 30

async def _ws_cleanup():
    """Periodic cleanup: send a small text ping to detect dead clients."""
    while True:
        await asyncio.sleep(_WS_STALE_SEC)
        async with _ws_lock:
            for sid in list(_ws_clients.keys()):
                dead = []
                for c in _ws_clients[sid]:
                    try:
                        await c.send_text('{"_":"ping"}')
                    except Exception:
                        dead.append(c)
                for c in dead:
                    _ws_clients[sid].remove(c)
                if not _ws_clients[sid]:
                    _ws_clients.pop(sid, None)


@router.websocket("/ws/v1/live-frame/{session_id}")
async def ws_live_frame(websocket: WebSocket, session_id: str):
    """WebSocket binary live-feed.

    proctor.py opens this WS and sends raw JPEG bytes (binary frames).
    The dashboard can open a parallel WS reader on the same session_id
    to receive frames in real-time with no base64 overhead.

    Auth: the proctor sends a short JSON handshake first:
        {"token": "<jwt>"}
    After handshake, all frames are raw binary JPEG.
    """
    await websocket.accept()

    # Limit concurrent connections per session
    async with _ws_lock:
        cnt = _ws_conn_count.get(session_id, 0)
        if cnt >= MAX_WS_PER_SESSION:
            await websocket.close(code=4002, reason="max_connections_reached")
            return
        _ws_conn_count[session_id] = cnt + 1

    await _ws_ensure_cleanup()

    try:
        auth_msg = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        data = json.loads(auth_msg)
        token = data.get("token", "")
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except (asyncio.TimeoutError, json.JSONDecodeError, JWTError, KeyError):
        await websocket.close(code=4001, reason="auth_failed")
        return

    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        await websocket.close(code=4003, reason="access_denied")
        return

    await _ws_subscribe(session_id, websocket)

    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) > MAX_WS_MSG_BYTES:
                continue  # silently drop oversized frames
            _store_live_frame(session_id, data)
            await _ws_broadcast(session_id, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await _ws_unsubscribe(session_id, websocket)

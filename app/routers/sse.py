import asyncio
import base64
import json
import logging
import time
from fastapi import APIRouter, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
from jose import jwt, JWTError
from pydantic import BaseModel, ConfigDict

from ..dependencies import (
    supabase, _bus_subscribe, _HAS_REDIS, _get_teacher_by_id,
    require_admin, verify_admin_token,
    _build_sessions_payload, _cache, _bus_async_publish,
    SECRET_KEY, require_auth, now_ist, fmt_ist, SessionStatus,
    VIOLATION_WEIGHTS, _CRITICAL_TYPES,
)

logger = logging.getLogger(__name__)

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
        return Response(status_code=400, content="Invalid base64")

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
            dead.append(c)  # Client disconnected — will be cleaned up
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
                        dead.append(c)  # Client disconnected
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
    except Exception as e:
        logger.debug("WebSocket live-frame error: %s", e)
    finally:
        await _ws_unsubscribe(session_id, websocket)


# ─── SSE SESSIONS STREAM (teacher dashboard live updates) ────────


@router.get("/api/v1/sse/sessions")
async def sse_sessions(request: Request):
    """Server-Sent Events stream for the teacher dashboard.

    Sends:
    - `init`: Current live sessions snapshot on connect
    - `update`: Session change (violation, heartbeat, status change)
    - `alert`: High-severity violation that needs immediate attention
    - `refresh`: Full refresh signal
    - `ping`: Keepalive every 15s

    Auth: token query parameter (JWT from login).
    """
    token = request.query_params.get("token", "")
    if not token:
        return Response(status_code=401, content="Missing token")
    try:
        teacher = await verify_admin_token(token)
    except HTTPException:
        return Response(status_code=401, content="Invalid token")

    teacher_id = str(teacher["id"])

    async def event_stream():
        alert_channel = f"alerts:{teacher_id}"
        events_channel = f"sessions:{teacher_id}"

        # Send initial snapshot
        try:
            sessions_payload = await _build_sessions_payload(teacher_id)
            yield f"event: init\ndata: {json.dumps({'sessions': sessions_payload['sessions']})}\n\n"
        except Exception:
            pass  # Failed to build initial snapshot — stream will still work with updates

        # Start async generators for each channel
        async def _channel_reader(channel: str, evt_type: str, queue: asyncio.Queue):
            if not _HAS_REDIS:
                return
            try:
                from app.event_bus import subscribe
                async for msg in subscribe(channel, keepalive_sec=30):
                    if msg.get("_keepalive"):
                        continue
                    await queue.put((evt_type, msg))
            except Exception:
                pass  # Redis subscription failed — stream continues without real-time updates

        queue: asyncio.Queue = asyncio.Queue()
        tasks = [
            asyncio.create_task(_channel_reader(alert_channel, "alert", queue)),
            asyncio.create_task(_channel_reader(events_channel, "update", queue)),
        ]

        try:
            while True:
                try:
                    evt_type, data = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield f"event: {evt_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: refresh\ndata: {{\"ts\": {time.time()}}}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

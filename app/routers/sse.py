import asyncio
import base64
import json
import logging
import time
from fastapi import APIRouter, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
import jwt
from jwt.exceptions import InvalidTokenError as JWTError
from pydantic import BaseModel, ConfigDict

from ..auth import (
    require_admin, verify_admin_token, verify_student_token, _get_teacher_by_id, require_auth,
)
from ..database import supabase
from ..event_bus import subscribe as _bus_subscribe, _HAS_REDIS, async_publish as _bus_async_publish
from ..services.sessions import build_sessions_payload as _build_sessions_payload
from .. import cache as _cache
from ..constants import SECRET_KEY, _CRITICAL_TYPES
from ..utils import now_ist, fmt_ist
from ..models import SessionStatus
from ..services.risk import VIOLATION_WEIGHTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

# ─── SHORT-LIVED CONNECTION TOKENS (avoid JWT in URL query params) ─
_connect_tokens: dict[str, str] = {}  # token -> teacher_id
_connect_tokens_lock = asyncio.Lock()

@router.post("/api/v1/sse/connect-token")
async def sse_connect_token(request: Request):
    """Exchange a valid auth token for a short-lived (30s) connection token.

    Use this instead of putting the main JWT in SSE/WebSocket query params
    which get logged by proxies. The connection token is single-use and
    expires quickly, minimizing exposure risk.
    """
    teacher = await require_admin(request)
    token = base64.urlsafe_b64encode(time.monotonic_ns().to_bytes(8, 'big')).decode()
    async with _connect_tokens_lock:
        _connect_tokens[token] = str(teacher["id"])
    async def _cleanup():
        await asyncio.sleep(30)
        async with _connect_tokens_lock:
            _connect_tokens.pop(token, None)
    asyncio.create_task(_cleanup())
    return {"connect_token": token}

def _connect_tokens_lock_sync():
    """Sync version for use in sync contexts if needed."""
    pass


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
        logger.warning("[sse] live-frame b64 decode failed", exc_info=True)
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
_ws_room_conns: dict[str, WebSocket] = {}
_last_room_frame: dict[str, float] = {}
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
        task = asyncio.create_task(_ws_cleanup_loop())
        task.add_done_callback(_log_task_failure)


def _log_task_failure(task):
    if not task.cancelled() and task.exception():
        logger.error("[ws_cleanup] task died: %s", task.exception())


async def _ws_cleanup_loop():
    """Background task: periodic cleanup with crash recovery."""
    while True:
        try:
            await _ws_cleanup()
        except Exception as e:
            logger.error("[ws_cleanup] iteration failed: %s", e)
            await asyncio.sleep(5)


_WS_STALE_SEC = 30

async def _ws_cleanup():
    """Single-pass cleanup: send a small text ping to detect dead clients."""
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

    # Limit concurrent connections per session (AFTER auth succeeds)
    async with _ws_lock:
        cnt = _ws_conn_count.get(session_id, 0)
        if cnt >= MAX_WS_PER_SESSION:
            await websocket.close(code=4002, reason="max_connections_reached")
            return
        _ws_conn_count[session_id] = cnt + 1

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


# ─── ROOM CAMERA WEBSOCKET (student phone) ──────────────────────


@router.websocket("/ws/v1/room-frame/{session_id}")
async def ws_room_frame(websocket: WebSocket, session_id: str):
    """WebSocket binary room-camera feed from the student's phone.

    Auth: token via Sec-WebSocket-Protocol subprotocol (time-bound
    room-cam JWT, scope='room-cam').  Only ONE phone WS per session
    is allowed — a new connection kills the old one.

    The phone sends:
      - Binary frames → raw JPEG bytes (stored in Redis)
      - JSON text frames → {"type": "heartbeat"}
    """
    sp = websocket.headers.get("sec-websocket-protocol", "")
    token = sp.split(",")[0].strip() if sp else ""

    await websocket.accept(subprotocol=token or None)

    # Validate room-cam token
    try:
        claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        if claims.get("scope") != "room-cam":
            await websocket.close(code=4001, reason="invalid_scope")
            return
        if claims.get("sid") != session_id:
            await websocket.close(code=4003, reason="access_denied")
            return
    except (JWTError, Exception):
        await websocket.close(code=4001, reason="auth_failed")
        return

    # Connection singleton — close any existing phone WS for this session
    async with _ws_lock:
        old = _ws_room_conns.get(session_id)
        if old:
            try:
                await old.close(code=4000, reason="replaced")
            except Exception:
                pass
        _ws_room_conns[session_id] = websocket

    # Update DB: mark room cam as active
    try:
        from ..database import async_table as _atable
        await _atable("exam_sessions").update({"room_cam_status": "pending"})\
            .eq("session_key", session_id).execute()
    except Exception:
        pass

    _last_room_frame: dict[str, float] = {}

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.receive":
                if "bytes" in msg:
                    data = msg["bytes"]
                    if len(data) > MAX_WS_MSG_BYTES:
                        continue
                    _store_room_frame(session_id, data)
                    _last_room_frame[session_id] = time.time()
                elif "text" in msg:
                    try:
                        payload = json.loads(msg["text"])
                        if payload.get("type") == "heartbeat":
                            _last_room_frame[session_id] = time.time()
                    except Exception:
                        pass
            elif msg.get("type") in ("websocket.disconnect",):
                break
    except Exception:
        pass
    finally:
        async with _ws_lock:
            if _ws_room_conns.get(session_id) is websocket:
                _ws_room_conns.pop(session_id, None)
        _last_room_frame.pop(session_id, None)
        # Mark room cam as offline in DB
        try:
            from ..database import async_table as _atable
            await _atable("exam_sessions").update({"room_cam_status": "disabled"})\
                .eq("session_key", session_id).execute()
        except Exception:
            pass


def _store_room_frame(session_id: str, jpeg_bytes: bytes):
    """Store a room camera frame in the cache (backed by Redis)."""
    from .. import cache as _cache
    if _cache:
        try:
            _cache.set_room_frame(session_id, jpeg_bytes, ttl=10)
        except Exception:
            pass


@router.get("/api/v1/proctor/control/{session_id}")
async def proctor_control(session_id: str, request: Request):
    """Pinged by proctor.py every 2s. Returns {"live_view": bool}
    indicating whether a teacher has activated the live camera view
    for this session via POST .../live-view/start.

    The student's JWT must be in the Authorization header. The roll
    number embedded in the JWT must match the session_id prefix.
    """
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if not token:
        return Response(status_code=401, content="Missing Authorization header")
    try:
        claims = verify_student_token(token)
    except HTTPException:
        return Response(status_code=401, content="Invalid token")

    session_roll = session_id.rsplit("_", 1)[0].upper()
    if (claims.get("roll") or "").upper() != session_roll:
        return Response(status_code=403, content="Access denied")

    active = False
    if _cache:
        try:
            val = _cache.get(f"liveview:{session_id}")
            active = val is not None
        except Exception:
            pass

    return {"live_view": active}


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

    Auth: `token` query parameter — accepts either the main JWT (for
    backward compat) or a short-lived connect token from
    POST /api/v1/sse/connect-token.
    """
    token = request.query_params.get("token", "")
    if not token:
        return Response(status_code=401, content="Missing token")

    teacher_id = None
    # Try connect token first
    async with _connect_tokens_lock:
        teacher_id = _connect_tokens.pop(token, None)
    if teacher_id:
        teacher = await _get_teacher_by_id(teacher_id)
        if not teacher:
            return Response(status_code=401, content="Invalid token")
    else:
        # Fallback: accept main JWT for backward compat
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
        except Exception as e:
            logger.warning("[sse_sessions] initial snapshot failed: %s", e)

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
            except Exception as e:
                logger.warning("[sse_sessions] subscription failed channel=%s: %s", channel, e)

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

import asyncio
import base64
import hashlib
import inspect
import io
import json
import logging
import time
from PIL import Image
from fastapi import APIRouter, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
import jwt
from jwt.exceptions import InvalidTokenError as JWTError
from pydantic import BaseModel, ConfigDict

from ..auth import (
    require_admin, verify_admin_token, verify_student_token, _get_teacher_by_id, require_auth,
)
from ..database import supabase, async_table as _atable
from ..event_bus import subscribe as _bus_subscribe, _HAS_REDIS, async_publish as _bus_async_publish
from ..services.sessions import build_sessions_payload as _build_sessions_payload
from .. import cache as _cache
from ..constants import EXAM_TOKEN_SIGNING_KEYS, ROOM_CAM_SIGNING_KEYS, _CRITICAL_TYPES
from ..limiter import _ws_client_ip, ws_rate_limiter
from ..auth.tokens import _decode_token
from ..utils import now_ist, fmt_ist
from ..models import SessionStatus
from ..services.risk import VIOLATION_WEIGHTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


def _realtime_available() -> bool:
    """True when the live SSE pub/sub path is actually usable. `_HAS_REDIS`
    only means the redis library imported — a configured-but-DOWN Redis still
    kills real-time push/alerts/camera. cache._r_healthy reflects runtime
    reachability and the connect-token read on this same request just
    refreshed it, so this is a cheap, non-blocking, accurate signal."""
    if not _HAS_REDIS:
        return False
    try:
        return bool(getattr(_cache, "_r_healthy", False))
    except Exception:
        return False


def _sessions_event_data(snap: dict, *, with_ts: bool = False) -> dict:
    """Shape the init / refresh SSE payload from a build_sessions_payload()
    result. BOTH events MUST carry `sessions` + `all_sessions`: the dashboard's
    refresh handler renders straight from them, so a payload-less refresh tick
    blanked the live table every 5s (the 'sessions vanish after 5s' bug).
    Centralising the shape keeps init and refresh from drifting apart again."""
    data = {
        "sessions": snap.get("sessions", {}),
        "all_sessions": snap.get("all_sessions", []),
        "realtime": "live" if _realtime_available() else "degraded",
    }
    if with_ts:
        data["ts"] = time.time()
    return data


# ─── SHORT-LIVED CONNECTION TOKENS (avoid JWT in URL query params) ─
#
# Stored in Redis so all uvicorn workers see the same token pool.
# Previously this was an in-memory dict per process, which meant
# UVICORN_WORKERS=4 caused random 401s: token issued by worker A,
# EventSource open hit worker B which had no entry → "Invalid token".
# Falls back to the in-process dict only when Redis is unreachable
# (local dev). Single-use via DEL on read.
_CONNECT_TOKEN_TTL_SECONDS = 30
_connect_tokens: dict[str, str] = {}  # legacy fallback if Redis is down
_connect_tokens_lock = asyncio.Lock()


def _ct_key(token: str) -> str:
    return f"sse_ct:{token}"


async def _store_connect_token(token: str, teacher_id: str) -> None:
    if _cache is not None:
        try:
            _cache.set(_ct_key(token), str(teacher_id), ttl=_CONNECT_TOKEN_TTL_SECONDS)
            return
        except Exception as e:
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak
            # `e` is the Redis connection failure, not the token value.
            logger.warning("[sse] redis store fallback: %s", e)
    async with _connect_tokens_lock:
        _connect_tokens[token] = str(teacher_id)
        async def _cleanup():
            await asyncio.sleep(_CONNECT_TOKEN_TTL_SECONDS)
            async with _connect_tokens_lock:
                _connect_tokens.pop(token, None)
        asyncio.create_task(_cleanup())


async def _consume_connect_token(token: str) -> str | None:
    """Single-use lookup: returns teacher_id and deletes the entry.

    Uses Redis GETDEL (atomic get-and-delete, Redis ≥ 6.2) so two
    concurrent EventSource opens can't both consume the same token — the
    same single-use guarantee the LTI nonce path relies on. Falls back to
    GET-then-DELETE on older Redis, then to the in-process dict.
    """
    if _cache is not None:
        key = _ct_key(token)
        try:
            r = _cache._client()
        except Exception:
            r = None
        if r is not None:
            try:
                raw = r.getdel(key)
                if not raw:
                    return None
                # cache.set() stores json.dumps(value), so the raw entry is
                # JSON text ('"<tid>"'); decode it the same way cache.get()
                # would rather than returning the quoted string.
                try:
                    import json as _json
                    return str(_json.loads(raw))
                except (ValueError, TypeError):
                    return str(raw)
            except Exception:
                # Older Redis without GETDEL, or a transient error — fall
                # back to non-atomic GET+DELETE (bounded by the 30s TTL).
                try:
                    tid = _cache.get(key)
                    if tid:
                        try:
                            _cache.delete(key)
                        except Exception:
                            logger.debug("sse: connect-token cache delete failed", exc_info=True)
                        return str(tid)
                except Exception as e:
                    # nosemgrep: python.lang.security.audit.logging.logger-credential-leak
                    # `e` is the Redis read failure, not the token value.
                    logger.warning("[sse] redis read fallback: %s", e)
    async with _connect_tokens_lock:
        return _connect_tokens.pop(token, None)


@router.post("/api/v1/sse/connect-token")
async def sse_connect_token(request: Request):
    """Exchange a valid auth token for a short-lived (30s) connection token.

    Use this instead of putting the main JWT in SSE/WebSocket query params
    which get logged by proxies. The connection token is single-use and
    expires quickly, minimizing exposure risk.
    """
    teacher = await require_admin(request)
    token = base64.urlsafe_b64encode(time.monotonic_ns().to_bytes(8, 'big')).decode()
    await _store_connect_token(token, str(teacher["id"]))
    return {"connect_token": token}


def _recompress_jpeg(jpeg_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(jpeg_bytes))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=60, optimize=True)
    return buf.getvalue()


def _evict_live_frame_ts(now: float | None = None) -> None:
    now = now or time.time()
    if not hasattr(_evict_live_frame_ts, "_last_cleanup"):
        _evict_live_frame_ts._last_cleanup = 0
    if now - _evict_live_frame_ts._last_cleanup < 60 and len(_last_live_frame_ts) < 1000:
        return
    _evict_live_frame_ts._last_cleanup = now
    cutoff = now - 300
    for sid, last in list(_last_live_frame_ts.items()):
        if last < cutoff:
            _last_live_frame_ts.pop(sid, None)


async def _assert_exam_ws_session_access(claims: dict, session_id: str) -> None:
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        executed = _atable("exam_sessions").select(
            "session_key,roll_number,teacher_id,exam_id,student_id"
        ).eq("session_key", session_id).limit(1).execute()
        row = await executed if inspect.isawaitable(executed) else executed
    except Exception:
        return
    if isinstance(getattr(row, "data", None), list) and row.data:
        sess = row.data[0]
        if not isinstance(sess, dict):
            return
        row_roll = sess.get("roll_number")
        if row_roll and str(row_roll).upper() != str(claims.get("roll") or "").upper():
            raise HTTPException(status_code=403, detail="Access denied")
        for claim_key, row_key in (("tid", "teacher_id"), ("eid", "exam_id"), ("sid", "student_id")):
            claim_val = claims.get(claim_key)
            row_val = sess.get(row_key)
            if claim_val and row_val and str(claim_val) != str(row_val):
                raise HTTPException(status_code=403, detail="Access denied")
        return


async def _store_live_frame(session_id: str, jpeg_bytes: bytes) -> bool:
    """Store live frame using Redis LRU-capped cache if available.
    Returns True if frame was accepted, False if rate-limited.
    Rate-limited to 5 FPS per session to cap bandwidth.
    Re-compresses JPEG to quality 60 to save ~35% egress bandwidth.
    """
    now = time.time()
    _evict_live_frame_ts(now)
    last = _last_live_frame_ts.get(session_id, 0)
    if now - last < _LIVE_FRAME_INTERVAL:
        return False
    _last_live_frame_ts[session_id] = now

    # Re-compress JPEG at quality 60 — visually fine on dashboard tiles
    try:
        jpeg_bytes = await asyncio.to_thread(_recompress_jpeg, jpeg_bytes)
    except Exception:
        logger.debug("sse: jpeg recompress failed", exc_info=True)

    if _cache and hasattr(_cache, 'set_live_frame'):
        await asyncio.to_thread(_cache.set_live_frame, session_id, jpeg_bytes, 10)
    return True


# ─── LEGACY HTTP LIVE-FRAME (v2.2.0 backward compat) ──────────────

class LiveFrameIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    jpeg_b64: str


@router.post("/api/v1/proctor/live-frame")
async def upload_live_frame_http(body: LiveFrameIn, request: Request):
    """Legacy endpoint: proctor.py POSTs a base64 JPEG every ~1.5s.

    Stored in Redis via LRU-capped cache so the teacher dashboard
    can poll via GET /api/v1/admin/sessions/{sid}/live-frame.
    v2.2.0 clients still use this — new clients prefer WS binary.

    AUTH: the proctor app already sends the student's exam bearer token on
    every request, so we verify it (and that it matches this session) — the
    WS path does the same. Without this anyone who knows a session_id could
    inject/forge camera frames into a live proctoring session.
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token:
        return Response(status_code=401, content="Missing token")
    try:
        claims = _decode_token(token, EXAM_TOKEN_SIGNING_KEYS)
        await _assert_exam_ws_session_access(claims, body.session_id)
    except HTTPException:
        return Response(status_code=403, content="Access denied")
    except Exception:
        return Response(status_code=401, content="Invalid token")

    try:
        jpeg_bytes = base64.b64decode(body.jpeg_b64)
    except Exception:
        logger.warning("[sse] live-frame b64 decode failed", exc_info=True)
        return Response(status_code=400, content="Invalid base64")

    await _store_live_frame(body.session_id, jpeg_bytes)

    # Notify subscribed dashboards via Redis pub/sub
    if _HAS_REDIS:
        await _bus_async_publish(
            f"{_cache.LIVEFRAME_PREFIX}{body.session_id}",
            {"session_id": body.session_id, "at": time.time()},
        )

    return Response(status_code=204)


# ─── WEBSOCKET BINARY LIVE-FEED ───────────────────────────────────

_ws_clients: dict[str, list[WebSocket]] = {}
_ws_lock = asyncio.Lock()
_ws_conn_count: dict[str, int] = {}
_ws_room_conns: dict[str, WebSocket] = {}
_last_room_frame: dict[str, float] = {}
_last_live_frame_ts: dict[str, float] = {}
MAX_WS_PER_SESSION = 3


async def close_room_cam_ws(session_id: str, code: int = 4004, reason: str = "session_ended"):
    """Force-close the phone's room-cam WS for a session (e.g. on panic exit,
    where the session may not be terminal yet). The phone stops reconnecting on
    the 4004 code; the WS receive loop's finally block marks the cam offline."""
    ws = _ws_room_conns.get(session_id)
    if ws is None:
        return
    try:
        await ws.close(code=code, reason=reason)
    except Exception:
        logger.debug("close_room_cam_ws failed", exc_info=True)
MAX_WS_MSG_BYTES = 200 * 1024  # 200 KB — laptop cam JPEG
MAX_ROOM_FRAME_BYTES = 400 * 1024  # 400 KB — phone cam (higher res)

_LIVE_FRAME_INTERVAL = 0.2  # 200ms → 5 FPS max per-session


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
            _last_live_frame_ts.pop(session_id, None)


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
            # NOTE: do NOT touch _ws_conn_count here. Every connection's own
            # handler finally calls _ws_unsubscribe exactly once — that is the
            # single owner of the counter. A dead socket detected here will also
            # hit that finally, so decrementing in both places double-counts and
            # drifts the per-session count below reality (which would let
            # MAX_WS_PER_SESSION be exceeded and prematurely evict the live-frame
            # timestamp). Pruning the clients list above is enough to stop
            # sending to the dead socket immediately.


_WS_CLEANUP_STARTED = False

async def _ws_ensure_cleanup():
    global _WS_CLEANUP_STARTED
    if not _WS_CLEANUP_STARTED:
        _WS_CLEANUP_STARTED = True
        task = asyncio.create_task(_ws_cleanup_loop())
        task.add_done_callback(_log_task_failure)


# ─── ROOM CAMERA OFFLINE DETECTION ──────────────────────────────

_ROOM_CAM_OFFLINE_STARTED = False
_ROOM_CAM_OFFLINE_TIMEOUT = 20  # seconds without heartbeat → offline
_ROOM_CAM_OFFLINE_FIRED: set[str] = set()  # session_ids already flagged


def _room_cam_ensure_offline_detection():
    global _ROOM_CAM_OFFLINE_STARTED
    if not _ROOM_CAM_OFFLINE_STARTED:
        _ROOM_CAM_OFFLINE_STARTED = True
        task = asyncio.create_task(_room_cam_offline_loop())
        task.add_done_callback(_log_task_failure)


async def _room_cam_offline_loop():
    """Background task: detect room camera offline and fire violations."""
    while True:
        try:
            await _room_cam_offline_check()
        except Exception as e:
            logger.error("[room_cam_offline] check failed: %s", e)
        await asyncio.sleep(10)


async def _room_cam_offline_check():
    """Single pass: check all tracked room cam sessions for heartbeat timeout."""
    now = time.time()
    stale_threshold = now - 300  # 5 minutes — cleanup stale in-memory entries

    for sid, last_beat in list(_last_room_frame.items()):
        if last_beat < stale_threshold:
            _last_room_frame.pop(sid, None)
            _ROOM_CAM_OFFLINE_FIRED.discard(sid)
            # Drop only THIS stale session's Redis keys. The previous code
            # pattern-deleted roomframe:* / roomcam:* wholesale every 5 min,
            # which wiped the live frames of every concurrent exam (a
            # sub-second blank on every room-cam tile). Frames already self-
            # expire on a ~10s TTL and main.py's daily sweep is the FERPA/DPDP
            # backstop, so scoped per-session deletion is all that's needed.
            try:
                from .. import cache as _cache
                if _cache:
                    await _cache.adelete(f"{_cache.ROOMFRAME_PREFIX}{sid}")
                    await _cache.adelete(f"{_cache.ROOMCAM_PREFIX}{sid}")
            except Exception as e:
                logger.debug("[room_cam] stale key delete failed for %s: %s", sid, e)
            continue
        if now - last_beat < _ROOM_CAM_OFFLINE_TIMEOUT:
            _ROOM_CAM_OFFLINE_FIRED.discard(sid)
            continue
        if sid in _ROOM_CAM_OFFLINE_FIRED:
            continue  # already flagged
        _ROOM_CAM_OFFLINE_FIRED.add(sid)

        # Fire violation event — but first look up the session's teacher_id
        # so the UPDATE and the violations.insert both carry the tenant.
        # Without this the offline marker would write rows with no
        # teacher_id (audit-trail gap) and could touch a same-session_key
        # row that belongs to a different teacher if such a thing ever
        # existed (FK-violating data; defense-in-depth).
        try:
            from ..database import async_table as _atable
            row = (await _atable("exam_sessions")
                   .select("teacher_id")
                   .eq("session_key", sid)
                   .limit(1).execute()).data or []
            sess_tid = str(row[0].get("teacher_id") or "") if row else ""
            viol_row = {
                "session_key": sid,
                "violation_type": "room_cam_offline",
                "severity": "medium",
                "details": "Room camera disconnected for >20s — phone may have gone offline",
            }
            if sess_tid:
                viol_row["teacher_id"] = sess_tid
            await _atable("violations").insert(viol_row).execute()
            upd = _atable("exam_sessions").update({"room_cam_status": "offline"})\
                .eq("session_key", sid)
            if sess_tid:
                upd = upd.eq("teacher_id", sess_tid)
            await upd.execute()
            logger.warning("[room_cam_offline] session=%s marked offline", sid)
        except Exception as e:
            logger.error("[room_cam_offline] failed to record violation for %s: %s", sid, e)


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
            clients = _ws_clients.get(sid, [])
            dead = []
            for c in list(clients):
                try:
                    await c.send_text('{"_":"ping"}')
                except Exception:
                    dead.append(c)  # Client disconnected
            for c in dead:
                clients.remove(c)
            # Do NOT decrement _ws_conn_count here. As in _ws_broadcast, each
            # connection's own handler finally calls _ws_unsubscribe exactly
            # once — the single owner of the counter. A socket pruned here will
            # also hit that finally, so decrementing in both places double-counts
            # and drifts the per-session count below reality (leaking the
            # MAX_WS_PER_SESSION cap). Pruning the clients list is enough to stop
            # pinging the dead socket.
            if not clients:
                _ws_clients.pop(sid, None)
                _ws_conn_count.pop(sid, None)
                _last_live_frame_ts.pop(sid, None)


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

    client_ip = _ws_client_ip(websocket)
    if not await ws_rate_limiter.check_and_increment(client_ip):
        await websocket.close(code=4002, reason="rate_limited")
        return

    try:
        auth_msg = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        data = json.loads(auth_msg)
        token = data.get("token", "")
        claims = _decode_token(token, EXAM_TOKEN_SIGNING_KEYS)
    except (asyncio.TimeoutError, json.JSONDecodeError, JWTError, KeyError):
        await ws_rate_limiter.decrement(client_ip)
        await websocket.close(code=4001, reason="auth_failed")
        return

    try:
        await _assert_exam_ws_session_access(claims, session_id)
    except HTTPException:
        await ws_rate_limiter.decrement(client_ip)
        await websocket.close(code=4003, reason="access_denied")
        return

    # Limit concurrent connections per session (AFTER auth succeeds)
    async with _ws_lock:
        cnt = _ws_conn_count.get(session_id, 0)
        if cnt >= MAX_WS_PER_SESSION:
            await ws_rate_limiter.decrement(client_ip)
            await websocket.close(code=4002, reason="max_connections_reached")
            return
        _ws_conn_count[session_id] = cnt + 1

    await _ws_subscribe(session_id, websocket)

    try:
        while True:
            data = await websocket.receive_bytes()
            if len(data) > MAX_WS_MSG_BYTES:
                continue  # silently drop oversized frames
            if await _store_live_frame(session_id, data):
                await _ws_broadcast(session_id, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WebSocket live-frame error: %s", e)
    finally:
        await ws_rate_limiter.decrement(client_ip)
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
    offered = sp.split(",")[0].strip() if sp else ""
    token = offered[7:] if offered.startswith("bearer.") else offered

    # Echo the EXACT subprotocol the client offered (e.g. "bearer.<jwt>"). A
    # server-selected subprotocol MUST be one the client sent — accepting with
    # the *stripped* token (a value never offered) makes the browser fail the
    # handshake, so the phone could never connect (endless "reconnecting…",
    # room_cam_status never went pending, nothing on the teacher dashboard).
    await websocket.accept(subprotocol=offered or None)

    client_ip = _ws_client_ip(websocket)
    if not await ws_rate_limiter.check_and_increment(client_ip):
        await websocket.close(code=4002, reason="rate_limited")
        return

    # Validate room-cam token
    try:
        claims = _decode_token(token, ROOM_CAM_SIGNING_KEYS)
        if claims.get("scope") != "room-cam":
            await ws_rate_limiter.decrement(client_ip)
            await websocket.close(code=4001, reason="invalid_scope")
            return
        if claims.get("sid") != session_id:
            await ws_rate_limiter.decrement(client_ip)
            await websocket.close(code=4003, reason="access_denied")
            return
    except Exception:
        await ws_rate_limiter.decrement(client_ip)
        await websocket.close(code=4001, reason="auth_failed")
        return

    # Connection singleton — close any existing phone WS for this session
    async with _ws_lock:
        old = _ws_room_conns.get(session_id)
        if old:
            try:
                await old.close(code=4000, reason="replaced")
            except Exception as _re:
                logger.warning("[room_cam] cleanup close failed: %s", _re)
        _ws_room_conns[session_id] = websocket

    # Start the room camera offline detection background loop
    _room_cam_ensure_offline_detection()

    # Update DB: mark room cam as active. Look the teacher_id up once
    # from the session row so this UPDATE and the matching "offline"
    # transition on disconnect (below) both carry tenant scope.
    sess_teacher_id = ""
    try:
        from ..database import async_table as _atable
        sess_row = (await _atable("exam_sessions")
                    .select("teacher_id")
                    .eq("session_key", session_id)
                    .limit(1).execute()).data or []
        sess_teacher_id = str(sess_row[0].get("teacher_id") or "") if sess_row else ""
        upd = _atable("exam_sessions").update({"room_cam_status": "pending"})\
            .eq("session_key", session_id)
        if sess_teacher_id:
            upd = upd.eq("teacher_id", sess_teacher_id)
        await upd.execute()
    except Exception:
        logger.warning("sse: room_cam_status='pending' update failed", exc_info=True)


    _ROOM_TERMINAL = {"submitted", "force_submitted", "abandoned", "rejected", "completed"}
    _frame_since_check = 0
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.receive":
                if "bytes" in msg:
                    data = msg["bytes"]
                    if len(data) > MAX_ROOM_FRAME_BYTES:
                        continue
                    await _store_room_frame(session_id, data)
                    _last_room_frame[session_id] = time.time()
                    # Stop the room cam once the exam is over (submit/force-submit/
                    # abandon). Frames arrive ~1/s, so checking every 3rd closes
                    # the phone stream within a few seconds of exam end. The phone
                    # stops reconnecting on the 4004 close code.
                    _frame_since_check += 1
                    if _frame_since_check >= 3:
                        _frame_since_check = 0
                        try:
                            from ..database import async_table as _atbl
                            srow = (await _atbl("exam_sessions").select("status")
                                    .eq("session_key", session_id).limit(1).execute()).data or []
                            if srow and srow[0].get("status") in _ROOM_TERMINAL:
                                await websocket.close(code=4004, reason="session_ended")
                                break
                        except Exception:
                            logger.debug("sse: room-cam terminal check failed", exc_info=True)
                elif "text" in msg:
                    try:
                        payload = json.loads(msg["text"])
                        if payload.get("type") == "heartbeat":
                            _last_room_frame[session_id] = time.time()
                    except Exception:
                        logger.debug("sse: ws heartbeat parse failed", exc_info=True)
            elif msg.get("type") in ("websocket.disconnect",):
                break
    except Exception:
        logger.debug("sse: ws receive loop terminated", exc_info=True)
    finally:
        await ws_rate_limiter.decrement(client_ip)
        async with _ws_lock:
            if _ws_room_conns.get(session_id) is websocket:
                _ws_room_conns.pop(session_id, None)
        _last_room_frame.pop(session_id, None)
        # Clean up per-session frame meta from function attribute
        if hasattr(_store_room_frame, "_frame_meta"):
            _store_room_frame._frame_meta.pop(session_id, None)
        if hasattr(_store_room_frame, "_last_ts"):
            _store_room_frame._last_ts.pop(session_id, None)
        _last_live_frame_ts.pop(session_id, None)
        # Mark room cam as offline in DB. sess_teacher_id was captured
        # on the connect side; if it was unavailable then, the WHERE
        # falls back to session_key alone.
        try:
            from ..database import async_table as _atable
            upd = _atable("exam_sessions").update({"room_cam_status": "offline"})\
                .eq("session_key", session_id)
            if sess_teacher_id:
                upd = upd.eq("teacher_id", sess_teacher_id)
            await upd.execute()
        except Exception:
            logger.warning("sse: room_cam_status='offline' update failed", exc_info=True)


async def _store_room_frame(session_id: str, jpeg_bytes: bytes):
    """Store a room camera frame in the cache (backed by Redis).

    Also runs basic validation on the first few frames.
    """
    if len(jpeg_bytes) < 500:
        return
    if not jpeg_bytes.startswith(b'\xff\xd8\xff'):
        return

    # Track per-session frame count for debugging (cleaned up on disconnect)
    if not hasattr(_store_room_frame, "_frame_meta"):
        _store_room_frame._frame_meta: dict[str, dict] = {}
    meta = _store_room_frame._frame_meta.setdefault(session_id, {"count": 0})
    meta["count"] += 1

    # Rate limit: max 2 FPS per session
    if not hasattr(_store_room_frame, "_last_ts"):
        _store_room_frame._last_ts: dict[str, float] = {}
    now = time.time()
    if now - _store_room_frame._last_ts.get(session_id, 0) < 0.5:
        return
    _store_room_frame._last_ts[session_id] = now

    # Match the live-frame recompress pipeline (perf audit #2): mobile
    # phone-cam uploads can be 200-500 KB raw; recompressing to JPEG
    # quality 60 brings them to ~50-80 KB. At 2 FPS × 3500 concurrent
    # sessions = 7000 frames/s of bandwidth saved over the wire +
    # ~40 % Redis storage cut per cached frame. Same _recompress_jpeg
    # helper used for live frames.
    try:
        jpeg_bytes = await asyncio.to_thread(_recompress_jpeg, jpeg_bytes)
    except Exception:
        logger.debug("sse: room-frame jpeg recompress failed", exc_info=True)

    from .. import cache as _cache
    if _cache:
        try:
            _cache.set_room_frame(session_id, jpeg_bytes, ttl=10)
        except Exception:
            logger.debug("sse: set_room_frame cache write failed", exc_info=True)


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

    try:
        await _assert_exam_ws_session_access(claims, session_id)
    except HTTPException:
        return Response(status_code=403, content="Access denied")

    active = False
    if _cache:
        try:
            val = _cache.get(f"liveview:{session_id}")
            active = val is not None
        except Exception:
            logger.debug("sse: liveview cache read failed", exc_info=True)

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
    # Try connect token first (Redis-backed; falls back to per-process dict)
    teacher_id = await _consume_connect_token(token)
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
    try:
        max_seconds = min(max(int(request.query_params.get("max_seconds", "0")), 0), 300)
    except ValueError:
        max_seconds = 0
    exam_id = request.query_params.get("exam_id") or None

    async def event_stream():
        alert_channel = f"alerts:{teacher_id}"
        events_channel = f"sessions:{teacher_id}"
        stream_started = time.time()

        # Send initial snapshot. `realtime` tells the dashboard whether the
        # live pub/sub path is actually working: without a reachable Redis the
        # channel readers below no-op and the stream silently collapses to a
        # 5s refresh tick (no push, no alerts, no camera). The dashboard shows
        # a "real-time degraded" banner when this is 'degraded' instead of
        # leaving the teacher to think monitoring is live when it isn't.
        try:
            sessions_payload = await _build_sessions_payload(teacher_id, exam_id=exam_id)
            yield (
                "event: init\n"
                "data: " + json.dumps(_sessions_event_data(sessions_payload)) + "\n\n"
            )
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
                elapsed = time.time() - stream_started
                if max_seconds and elapsed >= max_seconds:
                    yield f"event: close\ndata: {{\"reason\": \"max_seconds\", \"ts\": {time.time()}}}\n\n"
                    return
                try:
                    wait_timeout = 5.0
                    if max_seconds:
                        wait_timeout = min(wait_timeout, max(0.1, max_seconds - elapsed))
                    evt_type, data = await asyncio.wait_for(queue.get(), timeout=wait_timeout)
                    yield f"event: {evt_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # No pub/sub event for `wait_timeout`s → emit a FULL snapshot,
                    # not a bare ping. The dashboard's 'refresh' handler renders
                    # straight from data.sessions / data.all_sessions, so a
                    # payload-less tick made it render an EMPTY list every 5s —
                    # live sessions flashed in on `init`, then vanished until a
                    # manual refresh (and re-vanished on the next tick). Sending
                    # the current snapshot fixes that AND makes the degraded
                    # (no-Redis) path a real 5s poll-over-SSE instead of a no-op.
                    try:
                        snap = await _build_sessions_payload(teacher_id, exam_id=exam_id)
                        yield (
                            "event: refresh\n"
                            "data: " + json.dumps(_sessions_event_data(snap, with_ts=True)) + "\n\n"
                        )
                    except Exception as e:
                        logger.warning("[sse_sessions] refresh snapshot failed: %s", e)
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

import asyncio
import logging
import uuid as _uuid
import time
from datetime import datetime, timezone
from collections import deque

from fastapi import WebSocket

from ..constants import CHAT_HISTORY_LIMIT

logger = logging.getLogger(__name__)

try:
    from .. import cache as _cache
except Exception:
    _cache = None


class ChatHub:
    MAX_TEACHER_SOCKETS_PER_TENANT = 50
    GLOBAL_MAX_CONNECTIONS = 500
    IDLE_TIMEOUT_SECONDS = 1800
    HEARTBEAT_INTERVAL = 30
    HEARTBEAT_TIMEOUT = 60
    CLEANUP_INTERVAL = 30
    STUDENT_META_TTL_SECONDS = 4 * 3600

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.student_conns: dict[str, WebSocket] = {}
        self.teacher_conns: dict[str, set[WebSocket]] = {}
        self.threads: dict[str, dict[str, deque]] = {}
        self.student_meta: dict[str, dict] = {}
        self.teacher_last_seen: dict[str, dict[WebSocket, float]] = {}
        self._last_pong: dict[WebSocket, float] = {}
        self._cleanup_task: asyncio.Task | None = None

    def _evict_stale_meta(self) -> None:
        now = datetime.now(timezone.utc)
        stale = []
        for sid, meta in self.student_meta.items():
            joined = meta.get("joined_at")
            if joined:
                try:
                    joined_dt = datetime.fromisoformat(joined)
                    diff = (now - joined_dt).total_seconds()
                    if diff > self.STUDENT_META_TTL_SECONDS:
                        stale.append(sid)
                except Exception:
                    logger.warning("chat: bad joined_at timestamp for %s", sid)
                    stale.append(sid)
        for sid in stale:
            self.student_meta.pop(sid, None)
            self.student_conns.pop(sid, None)

    def _thread(self, teacher_id: str, session_id: str) -> deque:
        t = self.threads.setdefault(teacher_id, {})
        return t.setdefault(session_id, deque(maxlen=CHAT_HISTORY_LIMIT))

    def _make_msg(self, *, sender: str, session_id: str, text: str,
                  kind: str = "msg") -> dict:
        return {
            "type": kind,
            "id": _uuid.uuid4().hex,
            "session_id": session_id,
            "sender": sender,
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    async def _safe_send(self, ws: WebSocket, payload: dict) -> bool:
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            return False

    async def register_student(self, *, session_id: str, teacher_id: str,
                               roll: str, name: str, ws: WebSocket) -> None:
        self._start_cleanup()
        async with self._lock:
            if self._global_connection_count() >= self.GLOBAL_MAX_CONNECTIONS:
                oldest_sid = None
                oldest_ts = float('inf')
                for sid, m in self.student_meta.items():
                    last_seen = m.get("last_seen")
                    if last_seen:
                        try:
                            ts = datetime.fromisoformat(last_seen).timestamp()
                            if ts < oldest_ts:
                                oldest_ts = ts
                                oldest_sid = sid
                        except Exception:
                            logger.warning("chat: bad last_seen timestamp for %s", sid)
                if oldest_sid:
                    old_ws = self.student_conns.pop(oldest_sid, None)
                    self.student_meta.pop(oldest_sid, None)
                    if old_ws:
                        try:
                            await old_ws.close(code=4002, reason="global_cap")
                        except Exception:
                            logger.debug("[chat] ws exception")
            old = self.student_conns.get(session_id)
            if old is not None and old is not ws:
                try:
                    await old.close(code=4000)
                except Exception:
                    logger.debug("[chat] ws exception")
            self.student_conns[session_id] = ws
            self.student_meta[session_id] = {
                "roll": roll,
                "name": name,
                "teacher_id": teacher_id,
                "joined_at": datetime.now(timezone.utc).isoformat(),
            }
            self._thread(teacher_id, session_id)

        await self._notify_teachers_presence(teacher_id, session_id, online=True)

    async def unregister_student(self, session_id: str) -> None:
        async with self._lock:
            meta = self.student_meta.pop(session_id, None)
            self.student_conns.pop(session_id, None)
        if meta:
            await self._notify_teachers_presence(
                meta["teacher_id"], session_id, online=False)

    async def student_send(self, session_id: str, text: str) -> dict | None:
        meta = self.student_meta.get(session_id)
        if not meta:
            return None
        meta["last_seen"] = datetime.now(timezone.utc).isoformat()
        msg = self._make_msg(sender="student", session_id=session_id, text=text)
        msg["roll"] = meta["roll"]
        msg["name"] = meta["name"]
        self._thread(meta["teacher_id"], session_id).append(msg)

        student_ws = self.student_conns.get(session_id)
        if student_ws is not None:
            await self._safe_send(student_ws, msg)

        await self._fanout_teachers(meta["teacher_id"], msg)
        return msg

    async def register_teacher(self, teacher_id: str, ws: WebSocket) -> None:
        self._start_cleanup()
        async with self._lock:
            conns = self.teacher_conns.setdefault(teacher_id, set())
            if len(conns) >= self.MAX_TEACHER_SOCKETS_PER_TENANT:
                oldest = next(iter(conns))
                conns.discard(oldest)
                by_sock = self.teacher_last_seen.get(teacher_id)
                if by_sock is not None:
                    by_sock.pop(oldest, None)
                try:
                    await oldest.close(code=4000, reason="too_many_tabs")
                except Exception:
                    logger.debug("[chat] ws exception")
            if self._global_connection_count() >= self.GLOBAL_MAX_CONNECTIONS:
                oldest_tid = None
                oldest_ws = None
                oldest_ts = time.monotonic()
                for tid, by_sock in self.teacher_last_seen.items():
                    for sock, last in by_sock.items():
                        if last < oldest_ts:
                            oldest_ts = last
                            oldest_tid = tid
                            oldest_ws = sock
                if oldest_ws is not None:
                    pts = self.teacher_conns.get(oldest_tid)
                    if pts:
                        pts.discard(oldest_ws)
                    by_sock = self.teacher_last_seen.get(oldest_tid)
                    if by_sock is not None:
                        by_sock.pop(oldest_ws, None)
                        if not by_sock:
                            self.teacher_last_seen.pop(oldest_tid, None)
                    try:
                        await oldest_ws.close(code=4002, reason="global_cap")
                    except Exception:
                        logger.debug("[chat] ws exception")
            conns.add(ws)
            self.teacher_last_seen.setdefault(teacher_id, {})[ws] = time.monotonic()
            self._evict_stale_meta()

        roster_sessions = []
        for sid, meta in self.student_meta.items():
            if meta.get("teacher_id") != teacher_id:
                continue
            history = list(self._thread(teacher_id, sid))
            roster_sessions.append({
                "session_id": sid,
                "roll": meta["roll"],
                "name": meta["name"],
                "online": sid in self.student_conns,
                "joined_at": meta.get("joined_at"),
                "history": history,
            })
        await self._safe_send(ws, {
            "type": "roster",
            "sessions": roster_sessions,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    async def unregister_teacher(self, teacher_id: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self.teacher_conns.get(teacher_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    self.teacher_conns.pop(teacher_id, None)
            by_sock = self.teacher_last_seen.get(teacher_id)
            if by_sock is not None:
                by_sock.pop(ws, None)
                if not by_sock:
                    self.teacher_last_seen.pop(teacher_id, None)

    async def teacher_send(self, teacher_id: str, session_id: str,
                           text: str) -> dict | None:
        meta = self.student_meta.get(session_id)
        if not meta or meta.get("teacher_id") != teacher_id:
            return None
        msg = self._make_msg(sender="teacher", session_id=session_id, text=text)
        self._thread(teacher_id, session_id).append(msg)

        student_ws = self.student_conns.get(session_id)
        if student_ws is not None:
            await self._safe_send(student_ws, msg)

        await self._fanout_teachers(teacher_id, msg)
        return msg

    async def teacher_broadcast(self, teacher_id: str, text: str) -> int:
        msg = self._make_msg(
            sender="teacher", session_id="*", text=text, kind="broadcast")
        delivered = 0
        targets: list[tuple[str, WebSocket]] = []
        async with self._lock:
            for sid, m in self.student_meta.items():
                if m.get("teacher_id") != teacher_id:
                    continue
                ws = self.student_conns.get(sid)
                if ws is not None:
                    targets.append((sid, ws))

        for sid, ws in targets:
            per_msg = dict(msg)
            per_msg["session_id"] = sid
            self._thread(teacher_id, sid).append(per_msg)
            if await self._safe_send(ws, per_msg):
                delivered += 1

        teacher_view = dict(msg)
        teacher_view["delivered"] = delivered
        await self._fanout_teachers(teacher_id, teacher_view)
        return delivered

    async def _fanout_teachers(self, teacher_id: str, payload: dict) -> None:
        dead: list[WebSocket] = []
        conns = list(self.teacher_conns.get(teacher_id, ()))
        for ws in conns:
            self._update_teacher_activity(teacher_id, ws)
            if not await self._safe_send(ws, payload):
                dead.append(ws)
        if dead:
            async with self._lock:
                s = self.teacher_conns.get(teacher_id)
                if s is not None:
                    for ws in dead:
                        s.discard(ws)
                    if not s:
                        self.teacher_conns.pop(teacher_id, None)
                by_sock = self.teacher_last_seen.get(teacher_id)
                if by_sock is not None:
                    for ws in dead:
                        by_sock.pop(ws, None)
                    if not by_sock:
                        self.teacher_last_seen.pop(teacher_id, None)

    async def _notify_teachers_presence(self, teacher_id: str,
                                        session_id: str, *, online: bool) -> None:
        meta = self.student_meta.get(session_id) or {}
        await self._fanout_teachers(teacher_id, {
            "type": "presence",
            "session_id": session_id,
            "roll": meta.get("roll") or "",
            "name": meta.get("name") or "",
            "online": online,
        })

    def _global_connection_count(self) -> int:
        return len(self.student_conns) + sum(len(s) for s in self.teacher_conns.values())

    def _update_teacher_activity(self, teacher_id: str, ws: WebSocket) -> None:
        by_sock = self.teacher_last_seen.get(teacher_id)
        if by_sock is not None:
            by_sock[ws] = time.monotonic()

    def record_pong(self, ws: WebSocket) -> None:
        self._last_pong[ws] = time.monotonic()

    def _start_cleanup(self) -> None:
        if self._cleanup_task is not None:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                await self._send_heartbeats()
                await self._cleanup_idle()
        except asyncio.CancelledError:
            pass

    async def _send_heartbeats(self) -> None:
        now = time.monotonic()
        deadline = now - self.HEARTBEAT_TIMEOUT
        async with self._lock:
            for sid, ws in list(self.student_conns.items()):
                last = self._last_pong.get(ws, 0.0)
                if last < deadline:
                    try:
                        await ws.close(code=4001, reason="heartbeat_timeout")
                    except Exception:
                        logger.debug("[chat] ws exception")
                    self.student_conns.pop(sid, None)
                    self.student_meta.pop(sid, None)
                    self._last_pong.pop(ws, None)
                else:
                    if not await self._safe_send(ws, {"type": "ping"}):
                        self.student_conns.pop(sid, None)
                        self.student_meta.pop(sid, None)
                        self._last_pong.pop(ws, None)

            for tid, conns in list(self.teacher_conns.items()):
                dead: list[WebSocket] = []
                for ws in list(conns):
                    last = self._last_pong.get(ws, 0.0)
                    if last < deadline:
                        dead.append(ws)
                    else:
                        if not await self._safe_send(ws, {"type": "ping"}):
                            dead.append(ws)
                for ws in dead:
                    try:
                        await ws.close(code=4001, reason="heartbeat_timeout")
                    except Exception:
                        logger.debug("[chat] ws exception")
                    conns.discard(ws)
                    self._last_pong.pop(ws, None)
                    by_sock = self.teacher_last_seen.get(tid)
                    if by_sock is not None:
                        by_sock.pop(ws, None)
                if not conns:
                    self.teacher_conns.pop(tid, None)
                    self.teacher_last_seen.pop(tid, None)

    async def _cleanup_idle(self) -> None:
        now = time.monotonic()
        cutoff = now - self.IDLE_TIMEOUT_SECONDS
        async with self._lock:
            idle_students = []
            for sid, ws in self.student_conns.items():
                meta = self.student_meta.get(sid)
                last_seen = meta.get("last_seen") if meta else None
                if last_seen:
                    try:
                        dt = datetime.fromisoformat(last_seen)
                        if (datetime.now(timezone.utc) - dt).total_seconds() > self.IDLE_TIMEOUT_SECONDS:
                            idle_students.append((sid, ws))
                    except Exception:
                        logger.warning("chat: bad last_seen for idle check on %s", sid)
                        idle_students.append((sid, ws))
            for sid, ws in idle_students:
                try:
                    await ws.close(code=4001, reason="idle_timeout")
                except Exception:
                    logger.debug("[chat] ws exception")
                self.student_conns.pop(sid, None)
                self.student_meta.pop(sid, None)

            idle_teachers: list[tuple[str, WebSocket]] = []
            for tid, by_sock in self.teacher_last_seen.items():
                for ws, last in list(by_sock.items()):
                    if last < cutoff:
                        idle_teachers.append((tid, ws))
            for tid, ws in idle_teachers:
                try:
                    await ws.close(code=4001, reason="idle_timeout")
                except Exception:
                    logger.debug("[chat] ws exception")
                conns = self.teacher_conns.get(tid)
                if conns:
                    conns.discard(ws)
                by_sock = self.teacher_last_seen.get(tid)
                if by_sock is not None:
                    by_sock.pop(ws, None)
                    if not by_sock:
                        self.teacher_last_seen.pop(tid, None)

            over = self._global_connection_count() - self.GLOBAL_MAX_CONNECTIONS
            if over <= 0:
                return

            candidates: list[tuple[float, str, str, WebSocket]] = []
            for sid, ws in self.student_conns.items():
                meta = self.student_meta.get(sid, {})
                last_seen = meta.get("last_seen")
                ts = 0.0
                if last_seen:
                    try:
                        ts = datetime.fromisoformat(last_seen).timestamp()
                    except Exception:
                        logger.warning("chat: bad last_seen for global-cap candidate %s", sid)
                candidates.append((ts, "student", sid, ws))
            for tid, by_sock in self.teacher_last_seen.items():
                for ws, last in by_sock.items():
                    candidates.append((last, "teacher", tid, ws))

            candidates.sort(key=lambda x: x[0])
            to_evict = candidates[:over]
            for _ts, kind, kid, ws in to_evict:
                try:
                    await ws.close(code=4002, reason="global_cap")
                except Exception:
                    logger.debug("[chat] ws exception")
                if kind == "student":
                    self.student_conns.pop(kid, None)
                    self.student_meta.pop(kid, None)
                else:
                    conns = self.teacher_conns.get(kid)
                    if conns:
                        conns.discard(ws)
                    by_sock = self.teacher_last_seen.get(kid)
                    if by_sock is not None:
                        by_sock.pop(ws, None)
                        if not by_sock:
                            self.teacher_last_seen.pop(kid, None)

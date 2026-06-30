import asyncio
import logging
import uuid as _uuid
import time
from datetime import datetime, timezone
from collections import deque
from typing import Any

from fastapi import WebSocket

from ..constants import CHAT_HISTORY_LIMIT

logger = logging.getLogger(__name__)

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
        self.threads: dict[str, dict[str, deque[dict[str, Any]]]] = {}
        self.student_meta: dict[str, dict[str, Any]] = {}
        self.teacher_last_seen: dict[str, dict[WebSocket, float]] = {}
        self._last_pong: dict[WebSocket, float] = {}
        self._cleanup_task: asyncio.Task[Any] | None = None

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

    def _thread(self, teacher_id: str, session_id: str) -> deque[dict[str, Any]]:
        t = self.threads.setdefault(teacher_id, {})
        return t.setdefault(session_id, deque(maxlen=CHAT_HISTORY_LIMIT))

    def _make_msg(self, *, sender: str, session_id: str, text: str,
                  kind: str = "msg") -> dict[str, Any]:
        return {
            "type": kind,
            "id": _uuid.uuid4().hex,
            "session_id": session_id,
            "sender": sender,
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    async def _safe_send(self, ws: WebSocket, payload: dict[str, Any]) -> bool:
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            return False

    async def register_student(self, *, session_id: str, teacher_id: str,
                               roll: str, name: str, ws: WebSocket) -> None:
        self._start_cleanup()
        to_close: list[WebSocket] = []
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
                        to_close.append(old_ws)
            old = self.student_conns.get(session_id)
            if old is not None and old is not ws:
                to_close.append(old)
            self.student_conns[session_id] = ws
            self.student_meta[session_id] = {
                "roll": roll,
                "name": name,
                "teacher_id": teacher_id,
                "joined_at": datetime.now(timezone.utc).isoformat(),
            }
            # Seed liveness so the first heartbeat sweep (≤30s away) doesn't
            # immediately reap a brand-new socket — without this the default
            # 0.0 is always < deadline once the server has been up 60s, which
            # closed every chat connection ~30s after it connected.
            self._last_pong[ws] = time.monotonic()
            self._thread(teacher_id, session_id)

        for old_ws in to_close:
            try:
                await old_ws.close(code=4000)
            except Exception:
                logger.debug("[chat] ws exception")
        await self._notify_teachers_presence(teacher_id, session_id, online=True)

    async def unregister_student(self, session_id: str) -> None:
        async with self._lock:
            meta = self.student_meta.pop(session_id, None)
            ws = self.student_conns.pop(session_id, None)
            if ws is not None:
                self._last_pong.pop(ws, None)
        if meta:
            await self._notify_teachers_presence(
                meta["teacher_id"], session_id, online=False)

    async def student_send(self, session_id: str, text: str) -> dict[str, Any] | None:
        async with self._lock:
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
        to_close: list[tuple[WebSocket, int, str]] = []
        async with self._lock:
            conns = self.teacher_conns.setdefault(teacher_id, set())
            if len(conns) >= self.MAX_TEACHER_SOCKETS_PER_TENANT:
                oldest = next(iter(conns))
                conns.discard(oldest)
                by_sock = self.teacher_last_seen.get(teacher_id)
                if by_sock is not None:
                    by_sock.pop(oldest, None)
                to_close.append((oldest, 4000, "too_many_tabs"))
            if self._global_connection_count() >= self.GLOBAL_MAX_CONNECTIONS:
                oldest_tid = None
                oldest_ws = None
                oldest_ts = time.monotonic()
                oldest_tid = ""
                for tid, by_sock in self.teacher_last_seen.items():
                    for sock, last in by_sock.items():
                        if last < oldest_ts:
                            oldest_ts = last
                            oldest_tid = tid
                            oldest_ws = sock
                if oldest_ws is not None and oldest_tid:
                    pts = self.teacher_conns.get(oldest_tid)
                    if pts:
                        pts.discard(oldest_ws)
                    by_sock = self.teacher_last_seen.get(oldest_tid)
                    if by_sock is not None:
                        by_sock.pop(oldest_ws, None)
                        if not by_sock:
                            self.teacher_last_seen.pop(oldest_tid, None)
                    to_close.append((oldest_ws, 4002, "global_cap"))
            conns.add(ws)
            self.teacher_last_seen.setdefault(teacher_id, {})[ws] = time.monotonic()
            # Seed liveness (see register_student) so the heartbeat sweep
            # doesn't reap this socket before its first pong lands.
            self._last_pong[ws] = time.monotonic()
            self._evict_stale_meta()

        for old_ws, code, reason in to_close:
            try:
                await old_ws.close(code=code, reason=reason)
            except Exception:
                logger.debug("[chat] ws exception")

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
            self._last_pong.pop(ws, None)

    async def teacher_send(self, teacher_id: str, session_id: str,
                           text: str, *, kind: str = "msg",
                           extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Send a message or directive from a teacher to a single
        student session.

        kind == "msg"                — normal chat message (default).
        kind == "system_warning"     — amber-bordered warning banner.
        kind == "pause_directive"    — student renderer drops the
                                       pause overlay.
        kind == "resume_directive"   — student renderer dismisses the
                                       pause overlay.
        kind == "terminate_directive" — student renderer shows the
                                        terminal "exam ended" screen.

        `extra` is a dict merged into the outgoing payload so callers
        can attach structured fields (chip_code, reason_code, etc.)
        without breaking the `text` contract used by the chat history.
        """
        async with self._lock:
            meta = self.student_meta.get(session_id)
            if not meta or meta.get("teacher_id") != teacher_id:
                return None
            msg = self._make_msg(sender="teacher", session_id=session_id,
                                 text=text, kind=kind)
            if extra:
                for k, v in extra.items():
                    if k not in msg:  # never let extras overwrite core fields
                        msg[k] = v
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

    async def _fanout_teachers(self, teacher_id: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        async with self._lock:
            conns = list(self.teacher_conns.get(teacher_id, ()))
            for ws in conns:
                by_sock = self.teacher_last_seen.get(teacher_id)
                if by_sock is not None:
                    by_sock[ws] = time.monotonic()
        for ws in conns:
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

    async def record_pong(self, ws: WebSocket) -> None:
        async with self._lock:
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

        # Phase 1: collect stale and active under lock
        stale_students: list[tuple[str, WebSocket]] = []
        active_students: list[tuple[str, WebSocket]] = []
        stale_teachers: list[tuple[str, WebSocket]] = []
        active_teachers: list[tuple[str, WebSocket]] = []
        async with self._lock:
            for sid, ws in list(self.student_conns.items()):
                last = self._last_pong.get(ws, 0.0)
                if last < deadline:
                    stale_students.append((sid, ws))
                else:
                    active_students.append((sid, ws))
            for tid, conns in list(self.teacher_conns.items()):
                for ws in list(conns):
                    last = self._last_pong.get(ws, 0.0)
                    if last < deadline:
                        stale_teachers.append((tid, ws))
                    else:
                        active_teachers.append((tid, ws))

        # Phase 2: close stale connections (I/O outside lock)
        for _sid, ws in stale_students:
            try:
                await ws.close(code=4001, reason="heartbeat_timeout")
            except Exception:
                logger.debug("[chat] ws exception")
        for _tid, ws in stale_teachers:
            try:
                await ws.close(code=4001, reason="heartbeat_timeout")
            except Exception:
                logger.debug("[chat] ws exception")

        # Phase 3: ping active connections (I/O outside lock)
        failed_students: list[tuple[str, WebSocket]] = []
        failed_teachers: list[tuple[str, WebSocket]] = []
        for sid, ws in active_students:
            if not await self._safe_send(ws, {"type": "ping"}):
                failed_students.append((sid, ws))
        for tid, ws in active_teachers:
            if not await self._safe_send(ws, {"type": "ping"}):
                failed_teachers.append((tid, ws))

        # Phase 4: clean up dicts inside lock
        async with self._lock:
            for sid, ws in stale_students + failed_students:
                self.student_conns.pop(sid, None)
                self.student_meta.pop(sid, None)
                self._last_pong.pop(ws, None)
            for tid, ws in stale_teachers + failed_teachers:
                _conns = self.teacher_conns.get(tid)
                if _conns:
                    _conns.discard(ws)
                self._last_pong.pop(ws, None)
                _by_sock = self.teacher_last_seen.get(tid)
                if _by_sock is not None:
                    _by_sock.pop(ws, None)
                    if not _by_sock:
                        self.teacher_last_seen.pop(tid, None)
                if _conns is not None and not _conns:
                    self.teacher_conns.pop(tid, None)

    async def _cleanup_idle(self) -> None:
        now = time.monotonic()
        cutoff = now - self.IDLE_TIMEOUT_SECONDS

        # Phase 1: collect candidates under lock
        idle_students: list[tuple[str, WebSocket]] = []
        idle_teachers: list[tuple[str, WebSocket]] = []
        async with self._lock:
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
            for tid, by_sock in self.teacher_last_seen.items():
                for ws, last in list(by_sock.items()):
                    if last < cutoff:
                        idle_teachers.append((tid, ws))

        # Phase 2: close idle connections (I/O outside lock)
        for sid, ws in idle_students:
            try:
                await ws.close(code=4001, reason="idle_timeout")
            except Exception:
                logger.debug("[chat] ws exception")
        for tid, ws in idle_teachers:
            try:
                await ws.close(code=4001, reason="idle_timeout")
            except Exception:
                logger.debug("[chat] ws exception")

        # Phase 3: remove idle entries from dicts under lock
        async with self._lock:
            for sid, ws in idle_students:
                self.student_conns.pop(sid, None)
                self.student_meta.pop(sid, None)
            for tid, ws in idle_teachers:
                _conns2 = self.teacher_conns.get(tid)
                if _conns2:
                    _conns2.discard(ws)
                _by_sock2 = self.teacher_last_seen.get(tid)
                if _by_sock2 is not None:
                    _by_sock2.pop(ws, None)
                    if not _by_sock2:
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

        # Phase 4: close evicted connections (I/O outside lock)
        for _ts, kind, kid, ws in to_evict:
            try:
                await ws.close(code=4002, reason="global_cap")
            except Exception:
                logger.debug("[chat] ws exception")

        # Phase 5: remove evicted entries from dicts under lock
        async with self._lock:
            for _ts, kind, kid, ws in to_evict:
                if kind == "student":
                    self.student_conns.pop(kid, None)
                    self.student_meta.pop(kid, None)
                else:
                    _conns3 = self.teacher_conns.get(kid)
                    if _conns3:
                        _conns3.discard(ws)
                    _by_sock3 = self.teacher_last_seen.get(kid)
                    if _by_sock3 is not None:
                        _by_sock3.pop(ws, None)
                        if not _by_sock3:
                            self.teacher_last_seen.pop(kid, None)
                    if _conns3 is not None and not _conns3:
                        self.teacher_conns.pop(kid, None)

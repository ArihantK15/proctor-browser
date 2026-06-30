"""WebSocket chat endpoints: student and teacher chat connections."""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from ..auth import (
    _get_teacher_by_id, require_auth, verify_student_token, verify_admin_token,
)
from ..database import async_table as _atable
from ..constants import CHAT_MAX_TEXT_LEN
from ..limiter import _ws_client_ip, ws_rate_limiter
from ..models import SessionStatus, RESULT_STATUSES
from ..utils import now_ist
from ..services.chat import ChatHub
from ..db_context import system_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

chat_hub = ChatHub()


async def _chat_verify_session_owned(session_id: str, teacher_id: str, roll: str):
    """Verify the session exists, belongs to the teacher, and matches the roll number."""
    rows = (await _atable("exam_sessions")
            .select("session_key,roll_number,status,teacher_id")
            .eq("session_key", session_id)
            .eq("teacher_id", str(teacher_id))
            .execute()).data or []
    if not rows:
        return None
    row = rows[0]
    if str(row.get("teacher_id") or "") != str(teacher_id or ""):
        return None
    if (row.get("roll_number") or "").upper() != (roll or "").upper():
        return None
    if row.get("status") in RESULT_STATUSES:
        return None
    return row


@router.websocket("/ws/chat/student")
async def ws_chat_student(ws: WebSocket):
    """Student end of the chat. Token via Sec-WebSocket-Protocol (subprotocol header)."""
    # Extract subprotocol from handshake headers
    sp = ws.headers.get("sec-websocket-protocol", "")
    token = sp.split(",")[0].strip() if sp else ""

    client_ip = _ws_client_ip(ws)
    if not await ws_rate_limiter.check_and_increment(client_ip):
        await ws.close(code=4400, reason="rate_limited")
        return

    try:
        session_id = (ws.query_params.get("session_id") or "").strip()
        if not session_id:
            await ws.close(code=4400); return
        try:
            payload = verify_student_token(token)
        except HTTPException:
            await ws.close(code=4401); return

        roll = (payload.get("roll") or "").upper()
        tid = payload.get("tid")
        if not roll or not tid:
            await ws.close(code=4401); return

        # WebSocket handlers run OUTSIDE the HTTP middleware that sets the RLS
        # session context (db_context ContextVar), so under the live procta_app
        # cutover these reads have NO app.* context and every student-scoped policy
        # denies → exam_sessions read empties → 4403 → student stuck "reconnecting",
        # teacher sees "no online students". The token was already verified and the
        # ownership is re-checked inside _chat_verify_session_owned, so a system_context
        # read here is safe (it's the email_otps pattern for non-HTTP entry points).
        with system_context():
            sess_row = await _chat_verify_session_owned(session_id, tid, roll)
        if not sess_row:
            await ws.close(code=4403); return

        await ws.accept(subprotocol=token or None)

        with system_context():
            student_result = await _atable("students") \
                .select("full_name") \
                .eq("roll_number", roll) \
                .eq("teacher_id", str(tid)) \
                .execute()
        name = (student_result.data[0]["full_name"]
                if student_result.data else roll)

        await chat_hub.register_student(
            session_id=session_id, teacher_id=str(tid),
            roll=roll, name=name, ws=ws)

        history = list(chat_hub._thread(str(tid), session_id))
        await ws.send_json({
            "type": "history",
            "session_id": session_id,
            "messages": history,
        })

        while True:
            raw = await ws.receive_text()
            # Any inbound frame proves the socket is alive — refresh liveness so
            # the heartbeat sweep doesn't reap an actively-used connection.
            await chat_hub.record_pong(ws)
            try:
                data = json.loads(raw)
            except Exception:
                continue  # Invalid JSON from student — ignore
            mtype = data.get("type", "msg")
            if mtype == "pong":
                continue  # liveness already recorded above
            if mtype == "reauth":
                new_token = str(data.get("token", "")).strip()
                try:
                    new_payload = verify_student_token(new_token)
                    new_roll = (new_payload.get("roll") or "").upper()
                    new_tid = new_payload.get("tid")
                    if new_roll and new_tid and str(new_tid) == str(tid):
                        await ws.send_json({"type": "reauth_ok", "exp": new_payload.get("exp")})
                    else:
                        await ws.send_json({"type": "reauth_failed", "reason": "invalid"})
                except HTTPException:
                    await ws.send_json({"type": "reauth_failed", "reason": "invalid_token"})
                continue
            if mtype != "msg":
                continue
            text = str(data.get("text", "")).strip()
            if not text:
                continue
            if len(text) > CHAT_MAX_TEXT_LEN:
                text = text[:CHAT_MAX_TEXT_LEN]
            await chat_hub.student_send(session_id, text)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("[ws_chat_student] error: %s", e)
    finally:
        await ws_rate_limiter.decrement(client_ip)
        sid = (ws.query_params.get("session_id") or "").strip()
        if sid:
            await chat_hub.unregister_student(sid)


@router.websocket("/ws/chat/teacher")
async def ws_chat_teacher(ws: WebSocket):
    """Teacher end of the chat.  Auth via Sec-WebSocket-Protocol subprotocol
    header (set by `new WebSocket(url, [token])`) or HttpOnly cookie.
    First-message and query-param auth are intentionally not accepted; auth
    must be proven before the WebSocket upgrade is accepted.
    """
    sp = ws.headers.get("sec-websocket-protocol", "")
    subproto_first = sp.split(",")[0].strip() if sp else ""

    client_ip = _ws_client_ip(ws)
    if not await ws_rate_limiter.check_and_increment(client_ip):
        await ws.close(code=4400, reason="rate_limited")
        return

    teacher_id = None
    teacher = None
    try:
        subproto = subproto_first
        if subproto and not subproto.startswith("websocket"):
            try:
                teacher = await verify_admin_token(subproto)
            except HTTPException:
                pass
        if not teacher:
            cookie_token = ws.cookies.get("procta_access", "")
            if cookie_token:
                try:
                    teacher = await verify_admin_token(cookie_token)
                except HTTPException:
                    pass
        if not teacher:
            await ws.close(code=4401); return
        # Superadmin is monitor-only — it must not send chat (a teacher↔student
        # channel). Reject the connection outright; it has no business here.
        if str(teacher.get("org_role") or "").lower() == "superadmin":
            await ws.close(code=4403, reason="monitor_only"); return
        teacher_id = str(teacher["id"])

        await ws.accept(subprotocol=subproto_first or None)
        await chat_hub.register_teacher(teacher_id, ws)

        while True:
            raw = await ws.receive_text()
            # Any inbound frame proves the socket is alive — refresh liveness so
            # the heartbeat sweep doesn't reap an actively-used connection.
            await chat_hub.record_pong(ws)
            try:
                data = json.loads(raw)
            except Exception:
                continue  # Invalid JSON from teacher — ignore
            mtype = data.get("type", "msg")
            if mtype == "pong":
                continue  # liveness already recorded above
            if mtype == "reauth":
                new_token = str(data.get("token", "")).strip()
                try:
                    new_teacher = await verify_admin_token(new_token)
                    if str(new_teacher["id"]) == teacher_id:
                        await ws.send_json({"type": "reauth_ok", "exp": new_teacher.get("exp")})
                    else:
                        await ws.send_json({"type": "reauth_failed", "reason": "mismatch"})
                except HTTPException:
                    await ws.send_json({"type": "reauth_failed", "reason": "invalid_token"})
                continue
            text = str(data.get("text", "")).strip()
            if not text:
                continue
            if len(text) > CHAT_MAX_TEXT_LEN:
                text = text[:CHAT_MAX_TEXT_LEN]
            if mtype == "msg":
                target_sid = str(data.get("session_id", "")).strip()
                if not target_sid:
                    continue
                await chat_hub.teacher_send(teacher_id, target_sid, text)
            elif mtype == "broadcast":
                await chat_hub.teacher_broadcast(teacher_id, text)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("[ws_chat_teacher] error: %s", e)
    finally:
        await ws_rate_limiter.decrement(client_ip)
        if teacher_id is not None:
            await chat_hub.unregister_teacher(teacher_id, ws)

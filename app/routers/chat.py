"""WebSocket chat endpoints: student and teacher chat connections."""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from ..dependencies import (
    supabase,
    _get_teacher_by_id,
    require_auth,
    verify_student_token,
    verify_admin_token,
    CHAT_MAX_TEXT_LEN,
    ChatHub,
    SessionStatus,
)

router = APIRouter(prefix="")

chat_hub = ChatHub()


def _chat_verify_session_owned(session_id: str, teacher_id: str, roll: str):
    """Verify the session exists, belongs to the teacher, and matches the roll number."""
    rows = (supabase.table("exam_sessions")
            .select("id,session_key,roll_number,status,teacher_id")
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
    if row.get("status") == SessionStatus.COMPLETED:
        return None
    return row


@router.websocket("/ws/chat/student")
async def ws_chat_student(ws: WebSocket):
    """Student end of the chat.  Query params: token, session_id."""
    await ws.accept()
    try:
        token = ws.query_params.get("token") or ""
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

        sess_row = _chat_verify_session_owned(session_id, tid, roll)
        if not sess_row:
            await ws.close(code=4403); return

        student_result = supabase.table("students") \
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
            try:
                data = json.loads(raw)
            except Exception:
                continue
            mtype = data.get("type", "msg")
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
        print(f"[ws_chat_student] error: {e}")
    finally:
        sid = (ws.query_params.get("session_id") or "").strip()
        if sid:
            await chat_hub.unregister_student(sid)


@router.websocket("/ws/chat/teacher")
async def ws_chat_teacher(ws: WebSocket):
    """Teacher end of the chat.  Query param: token."""
    await ws.accept()
    teacher_id = None
    try:
        token = ws.query_params.get("token") or ""
        try:
            teacher = verify_admin_token(token)
        except HTTPException:
            await ws.close(code=4401); return
        teacher_id = str(teacher["id"])

        await chat_hub.register_teacher(teacher_id, ws)

        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except Exception:
                continue
            mtype = data.get("type", "msg")
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
        print(f"[ws_chat_teacher] error: {e}")
    finally:
        if teacher_id is not None:
            await chat_hub.unregister_teacher(teacher_id, ws)

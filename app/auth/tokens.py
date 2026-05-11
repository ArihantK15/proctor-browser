"""JWT issue and verify helpers."""
import re
from datetime import datetime, timezone, timedelta

from fastapi import Request, HTTPException
from jose import jwt, JWTError

from ..constants import SECRET_KEY, TOKEN_TTL_HOURS, ADMIN_TOKEN_TTL_HOURS, STUDENT_AUTH_TTL_HOURS


def create_token(roll_number: str, teacher_id: str = None, exam_id: str = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {"roll": roll_number, "exp": now + timedelta(hours=TOKEN_TTL_HOURS), "iat": now}
    if teacher_id:
        payload["tid"] = teacher_id
    if exam_id:
        payload["eid"] = exam_id
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def require_auth(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        return jwt.decode(auth[7:], SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def verify_student_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "student_account":
        raise HTTPException(status_code=403, detail="Student access required")
    return payload


def issue_admin_token(teacher: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "tid": str(teacher["id"]), "email": teacher.get("email", ""),
        "role": "teacher", "iat": now, "exp": now + timedelta(hours=ADMIN_TOKEN_TTL_HOURS),
    }
    org_id = teacher.get("org_id")
    if org_id:
        payload["org_id"] = str(org_id)
    org_role = teacher.get("org_role", "teacher")
    payload["org_role"] = org_role
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def issue_student_auth_token(account: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sid": str(account["id"]), "email": account.get("email", ""),
        "role": "student_account", "iat": now,
        "exp": now + timedelta(hours=STUDENT_AUTH_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def _check_session_ownership(claims: dict, session_id: str) -> None:
    parts = session_id.rsplit("_", 1)
    session_roll = parts[0].upper() if parts else ""
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    if len(parts) > 1 and re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', parts[1], re.I):
        session_tid = parts[1]
        claims_tid = str(claims.get("tid", ""))
        if session_tid and claims_tid and session_tid != claims_tid:
            raise HTTPException(status_code=403, detail="Access denied")

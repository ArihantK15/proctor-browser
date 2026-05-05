"""Shared dependencies: config, auth helpers, Pydantic models, utilities.

Extracted from main.py so that all routers can import from a single place
without circular dependencies.
"""
import asyncio
import csv
import io
import json
import base64
import math
import random
import hashlib
import threading
import time
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import uuid as _uuid
from collections import deque

from fastapi import Request, HTTPException, Body, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from jose import jwt, JWTError

from enum import StrEnum

from .database import supabase, async_table as _atable
from .logger import get_logger


# ─── Domain enums (string-backed for DB compatibility) ────────────

class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SUBMITTED = "submitted"
    FORCE_SUBMITTED = "force_submitted"
    ABANDONED = "abandoned"
    REJECTED = "rejected"


class InviteStatus(StrEnum):
    SENT = "sent"
    OPENED = "opened"
    CLICKED = "clicked"
    ACCEPTED = "accepted"
    BOUNCED = "bounced"
    FAILED = "failed"
    REVOKED = "revoked"
    QUEUED = "queued"


class VerificationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

# ─── Redis/event bus imports (defensive) ──────────────────────────
import logging as _logging
_boot_log = _logging.getLogger("boot")
try:
    from .event_bus import publish as _bus_publish, async_publish as _bus_async_publish, subscribe as _bus_subscribe
    _HAS_REDIS = True
except Exception as _e:
    _HAS_REDIS = False
    _boot_log.warning(
        "event_bus import failed (%s) — falling back to in-memory pub/sub.", _e)
    def _bus_publish(*a, **kw): pass
    async def _bus_async_publish(*a, **kw): pass

try:
    from . import cache as _cache
except Exception as _e:
    _cache = None
    _boot_log.warning("cache import failed (%s) — running without Redis cache.", _e)

# ─── CONFIG ───────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def fmt_ist(ts_str):
    if not ts_str:
        return ""
    try:
        if isinstance(ts_str, datetime):
            dt = ts_str
        else:
            dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    except Exception:
        return str(ts_str)

SECRET_KEY        = os.environ["SUPABASE_JWT_SECRET"]
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "").strip().lower()
SCREENSHOTS_DIR   = os.getenv("SCREENSHOTS_DIR", "/app/screenshots")
QUESTION_IMG_DIR  = os.getenv("QUESTION_IMG_DIR", "/app/question_images")
STATIC_DIR        = Path(__file__).parent / "static"
DOWNLOAD_MAC_ARM  = os.getenv("DOWNLOAD_MAC_ARM", "")
DOWNLOAD_MAC_X64  = os.getenv("DOWNLOAD_MAC_X64", "")
DOWNLOAD_WIN      = os.getenv("DOWNLOAD_WIN", "")

# CORS allowed origins. Default includes Electron file:// origin and
# localhost for dev. Set CORS_ALLOWED_ORIGINS to a comma-separated list
# in prod to restrict to known domains.
_CORS_RAW = os.getenv("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOWED_ORIGINS = [o.strip() for o in _CORS_RAW.split(",") if o.strip()] if _CORS_RAW else [
    "file://",
    "http://localhost",
    "http://localhost:5173",   # Vite dev server
    "https://app.procta.net",
]
RELEASE_REPO      = os.getenv("RELEASE_REPO", "ArihantK15/proctor-browser")
RELEASE_TTL_SEC   = int(os.getenv("RELEASE_TTL_SEC", "600"))
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
TOKEN_TTL_HOURS   = 10
ADMIN_TOKEN_TTL_HOURS = 12
STUDENT_AUTH_TTL_HOURS = 12
_LOADTEST_SECRET  = os.environ.get("LOADTEST_SECRET", "")

os.makedirs(SCREENSHOTS_DIR,  exist_ok=True)
os.makedirs(QUESTION_IMG_DIR, exist_ok=True)

# ─── JWT AUTH HELPERS ─────────────────────────────────────────────
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
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")

# ─── Teacher lookup cache ─────────────────────────────────────────
_teacher_cache = {}
_teacher_cache_ttl = {}
_teacher_cache_lock = threading.Lock()

def _get_teacher_by_id(teacher_id: str) -> dict | None:
    if not teacher_id:
        return None
    if _cache:
        cached = _cache.get(f"teacher:{teacher_id}")
        if cached:
            return cached
    else:
        now = time.time()
        with _teacher_cache_lock:
            if teacher_id in _teacher_cache and _teacher_cache_ttl.get(teacher_id, 0) > now:
                return _teacher_cache[teacher_id]
    result = supabase.table("teachers").select("*").eq("id", str(teacher_id)).execute()
    if not result.data:
        return None
    teacher = result.data[0]
    if _cache:
        _cache.set(f"teacher:{teacher_id}", teacher, ttl=60)
    else:
        now = time.time()
        with _teacher_cache_lock:
            _teacher_cache[teacher_id] = teacher
            _teacher_cache_ttl[teacher_id] = now + 60
    return teacher

def _get_teacher_by_uid(uid: str) -> dict | None:
    if not uid:
        return None
    result = supabase.table("teachers").select("*").eq("supabase_uid", str(uid)).execute()
    if not result.data:
        return None
    return result.data[0]

def issue_admin_token(teacher: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {"tid": str(teacher["id"]), "email": teacher.get("email", ""),
               "role": "teacher", "iat": now, "exp": now + timedelta(hours=ADMIN_TOKEN_TTL_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_admin_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"],
                             options={"verify_aud": False, "require": ["exp", "tid"]})
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "teacher":
        raise HTTPException(status_code=403, detail="Not a teacher token")
    tid = payload.get("tid")
    teacher = _get_teacher_by_id(tid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found")
    return teacher

def require_admin(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return verify_admin_token(auth[7:])

# ─── Student-account (dashboard) auth ────────────────────────────
_student_acct_cache = {}
_student_acct_cache_ttl = {}
_student_acct_cache_lock = threading.Lock()

def _get_student_account_by_id(account_id: str) -> dict | None:
    if not account_id:
        return None
    now = time.time()
    with _student_acct_cache_lock:
        if account_id in _student_acct_cache and _student_acct_cache_ttl.get(account_id, 0) > now:
            return _student_acct_cache[account_id]
    result = supabase.table("student_accounts").select("*").eq("id", str(account_id)).execute()
    if not result.data:
        return None
    acct = result.data[0]
    with _student_acct_cache_lock:
        _student_acct_cache[account_id] = acct
        _student_acct_cache_ttl[account_id] = now + 60
    return acct

def _get_student_account_by_uid(uid: str) -> dict | None:
    if not uid:
        return None
    result = supabase.table("student_accounts").select("*").eq("supabase_uid", str(uid)).execute()
    if not result.data:
        return None
    return result.data[0]

def issue_student_auth_token(account: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sid": str(account["id"]), "email": account.get("email", ""),
               "role": "student_account", "iat": now, "exp": now + timedelta(hours=STUDENT_AUTH_TTL_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_student_auth_token(token: str) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"],
                             options={"verify_aud": False, "require": ["exp", "sid"]})
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("role") != "student_account":
        raise HTTPException(status_code=403, detail="Not a student token")
    sid = payload.get("sid")
    account = _get_student_account_by_id(sid)
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")
    return account

def require_student_account(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return verify_student_auth_token(auth[7:])

# ─── RATE LIMITER ─────────────────────────────────────────────────
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

def _rate_limit_key(request: Request) -> str:
    if _LOADTEST_SECRET and request.headers.get("X-Loadtest-Key") == _LOADTEST_SECRET:
        return f"loadtest-{id(request)}"
    return get_remote_address(request)

limiter = Limiter(key_func=_rate_limit_key)

# ─── PYDANTIC MODELS ──────────────────────────────────────────────
class EventIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    event_type: str
    severity:   str
    details:    Optional[str] = None

class RegisterIn(BaseModel):
    model_config = ConfigDict(strict=True)
    full_name:   str
    roll_number: str
    email:       str
    phone:       Optional[str] = None
    teacher_id:  Optional[str] = None

class ValidateIn(BaseModel):
    model_config = ConfigDict(strict=True)
    roll_number: str
    access_code: Optional[str] = None
    exam_id: Optional[str] = None

class ResultIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id:      str
    roll_number:     str
    full_name:       str
    email:           str
    time_taken_secs: int
    answers:         dict = {}
    score:           int  = 0
    total:           int  = 0
    violations:      list = []

class AnswerIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id:  str
    question_id: str
    answer:      str

class BulkAnswerIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    answers:    dict

class FrameIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id: str
    frame:      str
    timestamp:  str
    event_type: Optional[str] = None

class IdVerifyIn(BaseModel):
    model_config = ConfigDict(strict=True)
    session_id:   str
    roll_number:  str
    selfie_frame: str
    id_frame:     str
    full_name:    str = ""
    timestamp:    str = ""

class IdDecisionIn(BaseModel):
    model_config = ConfigDict(strict=True)
    violation_id: int
    session_key:  str
    decision:     str

class TeacherSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str

class TeacherLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str

class RefreshIn(BaseModel):
    model_config = ConfigDict(strict=True)
    refresh_token: str

class StudentSignupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:     str
    password:  str
    full_name: str

class StudentLoginIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email:    str
    password: str

class PasswordResetIn(BaseModel):
    model_config = ConfigDict(strict=True)
    email: str

# ─── HELPERS ──────────────────────────────────────────────────────
def _xlsx_safe(v):
    if isinstance(v, str) and v and v[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v

def _safe_filename(s: str, fallback: str = "file") -> str:
    if not s:
        return fallback
    cleaned = "".join(c for c in str(s) if c.isalnum() or c in "-_.")[:80]
    return cleaned or fallback

def _safe_path_component(s: str, fallback: str = "path") -> str:
    """Strip directory traversal, keep only safe chars.  Use for URL
    params that become path segments."""
    if not s:
        return fallback
    return _safe_filename(Path(str(s)).name, fallback)

def _assert_within_directory(path: Path, base: Path) -> None:
    """Raise ValueError if *path* is not a descendant of *base*."""
    path.resolve().relative_to(base.resolve())

def _html_escape(s) -> str:
    """Escape user data for safe embedding in HTML content."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def ts_to_id(ts_str: str) -> int:
    try:
        dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0

# ─── PRACTICE MODE ────────────────────────────────────────────────
PRACTICE_PREFIX = "PRACTICE_"

def is_practice(identifier: Optional[str]) -> bool:
    return bool(identifier) and str(identifier).startswith(PRACTICE_PREFIX)

PRACTICE_QUESTIONS: list[dict] = [
    {"id": 1, "question_id": 1,
     "question": "This is a practice exam to test your setup. Pick any answer to continue.",
     "question_type": "mcq_single",
     "options": {"A": "I can see this question and the camera light is on.", "B": "I cannot see the camera preview.", "C": "I am unsure.", "D": "Skip"},
     "correct": "A", "image_url": ""},
    {"id": 2, "question_id": 2,
     "question": "Try clicking outside the exam window. The system should warn you. Did the warning appear?",
     "question_type": "mcq_single",
     "options": {"A": "Yes — a warning banner appeared.", "B": "No — nothing happened.", "C": "I did not try this.", "D": "I am not sure."},
     "correct": "A", "image_url": ""},
    {"id": 3, "question_id": 3,
     "question": "When you submit this practice exam, your real answers will not be graded or saved. Ready to submit?",
     "question_type": "mcq_single",
     "options": {"A": "Yes — submit and finish the practice run.", "B": "Not yet, I want to review.", "C": "Skip submission.", "D": "Other."},
     "correct": "A", "image_url": ""},
]

def _practice_validate_response(roll_number: str) -> dict:
    return {"valid": True, "full_name": "Practice Student", "email": "", "phone": "",
            "roll_number": roll_number, "token": "", "practice": True}

# ─── CALIBRATION QUALITY ─────────────────────────────────────────
_CAL_TIGHT_GAZE = 0.10
_CAL_LOOSE_GAZE = 0.50
_CAL_TIGHT_HEAD = 8.0
_CAL_LOOSE_HEAD = 30.0

def _parse_calibration_details(details: str) -> Optional[dict]:
    if not details:
        return None
    s = str(details).strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
            if isinstance(d, dict) and "gaze_yaw_range" in d:
                return {
                    "gaze_yaw_range":   float(d.get("gaze_yaw_range") or 0),
                    "gaze_pitch_range": float(d.get("gaze_pitch_range") or 0),
                    "head_yaw_range":   float(d.get("head_yaw_range") or 0),
                    "head_pitch_range": float(d.get("head_pitch_range") or 0),
                    "gaze_yaw":         float(d.get("gaze_yaw") or 0),
                    "gaze_pitch":       float(d.get("gaze_pitch") or 0),
                    "head_yaw":         float(d.get("head_yaw") or 0),
                    "head_pitch":       float(d.get("head_pitch") or 0),
                }
        except Exception:
            pass
    m_g = re.search(r"range\s+gaze:\s*±\(([\d.\-]+)\s*,\s*([\d.\-]+)\)", s)
    m_h = re.search(r"head:\s*±\(([\d.\-]+)°?\s*,\s*([\d.\-]+)°?\)", s)
    m_b = re.search(r"bias\s+gaze:\(([\d.\-]+)\s*,\s*([\d.\-]+)\)", s)
    if not (m_g and m_h):
        return None
    out = {"gaze_yaw_range": float(m_g.group(1)), "gaze_pitch_range": float(m_g.group(2)),
           "head_yaw_range": float(m_h.group(1)), "head_pitch_range": float(m_h.group(2))}
    if m_b:
        out["gaze_yaw"] = float(m_b.group(1))
        out["gaze_pitch"] = float(m_b.group(2))
    out.setdefault("gaze_yaw", 0.0)
    out.setdefault("gaze_pitch", 0.0)
    out.setdefault("head_yaw", 0.0)
    out.setdefault("head_pitch", 0.0)
    return out

def _classify_calibration(parsed: Optional[dict]) -> dict:
    if not parsed:
        return {"tier": "missing", "reason": "No calibration recorded.", "ranges": None}
    g_yaw, g_pitch = parsed["gaze_yaw_range"], parsed["gaze_pitch_range"]
    h_yaw, h_pitch = parsed["head_yaw_range"], parsed["head_pitch_range"]
    if min(g_yaw, g_pitch) < _CAL_TIGHT_GAZE or min(h_yaw, h_pitch) < _CAL_TIGHT_HEAD:
        return {"tier": "tight", "reason": f"Narrow range — student barely moved (gaze yaw ±{g_yaw:.2f} rad, head yaw ±{h_yaw:.0f}°).", "ranges": parsed}
    if max(g_yaw, g_pitch) > _CAL_LOOSE_GAZE or max(h_yaw, h_pitch) > _CAL_LOOSE_HEAD:
        return {"tier": "loose", "reason": f"Wide range — student moved more than expected (gaze yaw ±{g_yaw:.2f} rad, head yaw ±{h_yaw:.0f}°).", "ranges": parsed}
    return {"tier": "normal", "reason": "Calibration within typical envelope.", "ranges": parsed}

def get_calibration_quality(session_id: str, teacher_id: Optional[str] = None) -> dict:
    cache_key = f"cal_quality:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
    q = (supabase.table("violations").select("details").eq("session_key", session_id)
         .eq("violation_type", "calibration_complete").order("id", desc=True).limit(1))
    if teacher_id:
        q = q.eq("teacher_id", str(teacher_id))
    rows = (q.execute()).data or []
    parsed = _parse_calibration_details(rows[0]["details"]) if rows else None
    out = _classify_calibration(parsed)
    if _cache:
        try:
            _cache.set(cache_key, out, ex=300)
        except Exception:
            pass
    return out

# ─── VIOLATION FILTERING ──────────────────────────────────────────
_NON_VIOLATION_TYPES = {
    "exam_submitted", "enrollment_started", "enrollment_complete",
    "exam_started", "submit_failed", "answer_selected", "session_ended",
    "face_enrolled", "heartbeat", "id_verification", "id_verification_captured",
    "calibration_started", "calibration_complete", "calibration_timeout",
}

def _is_violation(vtype: str) -> bool:
    return vtype not in _NON_VIOLATION_TYPES

# ─── QUESTION/CONFIG LOADING ──────────────────────────────────────
def _load_questions(teacher_id: str = None, exam_id: str = None) -> list[dict]:
    cache_key = f"questions:{teacher_id or '_'}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
    try:
        query = supabase.table("questions").select("*")
        if teacher_id:
            query = query.eq("teacher_id", teacher_id)
        if exam_id:
            query = query.eq("exam_id", exam_id)
        result = query.order("question_id").execute()
        rows = result.data or []
    except Exception as e:
        print(f"[Questions] select(*) failed, falling back: {e}")
        query = supabase.table("questions").select("question_id,question,options,correct")
        if teacher_id:
            query = query.eq("teacher_id", teacher_id)
        rows = (query.order("question_id").execute().data or [])
    out = []
    for q in rows:
        qtype = (q.get("question_type") or "mcq_single").strip().lower()
        if qtype not in ("mcq_single", "mcq_multi", "true_false"):
            qtype = "mcq_single"
        out.append({"id": str(q["question_id"]), "question": q.get("question", "") or "",
                     "options": q.get("options") or {}, "correct": str(q.get("correct") or ""),
                     "question_type": qtype, "image_url": q.get("image_url") or ""})
    if _cache and out:
        _cache.set(cache_key, out, ttl=300)
    return out

def _load_exam_config(teacher_id: str = None, exam_id: str = None) -> dict:
    cache_key = f"exam_config:{teacher_id or '_'}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
    query = supabase.table("exam_config").select("*")
    if exam_id:
        query = query.eq("exam_id", exam_id)
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    result = query.execute()
    if result.data:
        if _cache:
            _cache.set(cache_key, result.data[0], ttl=300)
        return result.data[0]
    return {"exam_title": "Exam", "duration_minutes": 60, "access_code": "",
            "starts_at": None, "ends_at": None,
            "shuffle_questions": True, "shuffle_options": True}

def _get_access_code(teacher_id: str = None, exam_id: str = None) -> str:
    try:
        config = _load_exam_config(teacher_id, exam_id=exam_id)
        code = config.get("access_code", "")
        if code:
            return str(code).strip().upper()
    except Exception:
        pass
    return os.getenv("EXAM_ACCESS_CODE", "").strip().upper()

def _set_access_code(code: str, teacher_id: str = None, exam_id: str = None):
    if teacher_id and exam_id:
        supabase.table("exam_config").update({"access_code": code}).eq("teacher_id", teacher_id).eq("exam_id", exam_id).execute()
    elif teacher_id:
        supabase.table("exam_config").upsert({"teacher_id": teacher_id, "access_code": code}).execute()
    else:
        supabase.table("exam_config").upsert({"id": 1, "access_code": code}).execute()

# ─── ANSWER/SCORING HELPERS ───────────────────────────────────────
def _normalise_answer_set(ans: str) -> set[str]:
    if ans is None:
        return set()
    return {s.strip().upper() for s in str(ans).split(",") if s.strip()}

def _answers_match(student_ans: str, correct_ans: str) -> bool:
    return _normalise_answer_set(student_ans) == _normalise_answer_set(correct_ans)

def _translate_student_answer(session_id: str, teacher_id: str, question_id: str, student_label: str, exam_id: str = None) -> str:
    try:
        if not student_label:
            return student_label
        config = _load_exam_config(teacher_id, exam_id=exam_id)
        shuffle_q, shuffle_o = _get_shuffle_flags(config)
        if not shuffle_o:
            return student_label
        questions = _load_questions(teacher_id, exam_id=exam_id)
        if not questions:
            return student_label
        _, label_maps = _build_shuffle_view(questions, session_id, teacher_id, shuffle_q=shuffle_q, shuffle_o=shuffle_o)
        qmap = label_maps.get(str(question_id))
        if not qmap:
            return student_label
        return qmap.get(str(student_label), student_label)
    except Exception as e:
        print(f"[Shuffle] translate failed q={question_id} s={student_label}: {e}")
        return student_label

def _canonicalise_student_answer(session_id: str, teacher_id: str, question_id: str, raw: str, exam_id: str = None) -> str:
    if not str(raw or ""):
        return ""
    try:
        qs = _load_questions(teacher_id, exam_id=exam_id) or []
        qmeta = next((q for q in qs if str(q.get("id")) == str(question_id)), None)
        if qmeta and str(qmeta.get("question_type") or "").lower() == "short_answer":
            return str(raw)
    except Exception:
        pass
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return ""
    translated = [_translate_student_answer(session_id, str(teacher_id or ""), str(question_id), p, exam_id=exam_id) for p in parts]
    return ",".join(sorted(translated))

def _recalculate_score(session_id: str, payload_answers: dict, teacher_id: str = None, exam_id: str = None) -> tuple[int, int]:
    last_err = None
    for attempt in range(2):
        try:
            questions = _load_questions(teacher_id, exam_id=exam_id)
            auto_qs = [q for q in questions if str(q.get("question_type") or "mcq_single").lower() != "short_answer"]
            total = len(auto_qs)
            saved = supabase.table("answers").select("question_id,answer").eq("session_key", session_id).execute()
            ans_map = {str(r["question_id"]): str(r["answer"]) for r in (saved.data or [])}
            for qid, ans in (payload_answers or {}).items():
                ans_map[str(qid)] = _canonicalise_student_answer(session_id, str(teacher_id or ""), str(qid), str(ans))
            score = sum(1 for q in auto_qs if _answers_match(ans_map.get(str(q["id"]), ""), str(q["correct"])))
            return score, total
        except Exception as e:
            last_err = e
            print(f"[Score] Recalculation attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(0.3)
    raise RuntimeError(f"Score recalculation failed after 2 attempts: {last_err}")

# ─── SHUFFLE HELPERS ──────────────────────────────────────────────
def _shuffle_seed(session_id: str, teacher_id: str) -> int:
    basis = f"{teacher_id or ''}::{session_id or ''}"
    return int(hashlib.sha256(basis.encode()).hexdigest(), 16) % (2**32)

def _build_shuffle_view(questions: list[dict], session_id: str, teacher_id: str, *, shuffle_q: bool, shuffle_o: bool) -> tuple[list[dict], dict[str, dict[str, str]]]:
    rng = random.Random(_shuffle_seed(session_id, teacher_id))
    q_iter = list(questions)
    if shuffle_q:
        rng.shuffle(q_iter)
    student_qs: list[dict] = []
    label_maps: dict[str, dict[str, str]] = {}
    for q in q_iter:
        qid = str(q.get("id"))
        opts = q.get("options", {}) or {}
        orig_keys = list(opts.keys())
        qtype = str(q.get("question_type") or "mcq_single").lower()
        tf_keys = set(orig_keys) == {"True", "False"}
        can_shuffle_opts = shuffle_o and len(orig_keys) > 1 and qtype != "true_false" and not tf_keys
        if can_shuffle_opts:
            perm = list(orig_keys)
            rng.shuffle(perm)
            new_opts = {orig_keys[i]: opts[perm[i]] for i in range(len(orig_keys))}
            label_maps[qid] = {orig_keys[i]: perm[i] for i in range(len(orig_keys))}
            q = {**q, "options": new_opts}
        else:
            label_maps[qid] = {k: k for k in orig_keys}
        student_qs.append(q)
    return student_qs, label_maps

def _get_shuffle_flags(config: dict) -> tuple[bool, bool]:
    sq = config.get("shuffle_questions")
    so = config.get("shuffle_options")
    if sq is None:
        sq = True
    if so is None:
        so = True
    return bool(sq), bool(so)

# ─── SESSION OWNERSHIP ────────────────────────────────────────────
def _check_session_ownership(claims: dict, session_id: str):
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")

def _assert_session_owned(session_id: str, teacher_id: str) -> dict:
    if not teacher_id:
        raise HTTPException(status_code=403, detail="Teacher context missing")
    tid_str = str(teacher_id)
    result = supabase.table("exam_sessions").select("*").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()
    if result.data:
        return result.data[0]
    bare = supabase.table("exam_sessions").select("*").eq("session_key", session_id).limit(1).execute()
    if bare.data:
        row = bare.data[0]
        row_tid = row.get("teacher_id")
        if row_tid in (None, ""):
            v_other = supabase.table("violations").select("teacher_id").eq("session_key", session_id).neq("teacher_id", tid_str).limit(1).execute()
            if not (v_other.data or []):
                return row
        raise HTTPException(status_code=404, detail="Session not found")
    v_mine = supabase.table("violations").select("session_key,teacher_id").eq("session_key", session_id).eq("teacher_id", tid_str).limit(1).execute()
    if v_mine.data:
        return {"session_key": session_id, "teacher_id": tid_str,
                "roll_number": (session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]),
                "full_name": "", "status": SessionStatus.IN_PROGRESS, "started_at": "", "submitted_at": "",
                "score": None, "total": None, "risk_score": None}
    raise HTTPException(status_code=404, detail="Session not found")

# ─── RISK SCORING ─────────────────────────────────────────────────
VIOLATION_WEIGHTS: dict[str, float] = {
    "wrong_person": 30, "multiple_faces": 20, "face_missing": 15,
    "cheat_object_detected": 25, "window_focus_lost": 18, "tab_hidden": 15,
    "shortcut_blocked": 12, "gaze_away": 8, "head_turned": 8, "eyes_closed": 5,
    "voice_detected": 10, "time_exceeded": 15, "vm_detected": 20,
    "remote_desktop_detected": 22, "screen_share_detected": 12, "multiple_monitors": 8,
    "calibration_abort": 35,
    "phone_consulting": 32, "collaboration": 30, "answer_memo": 28,
    "note_reading": 25, "sustained_offtask": 15, "nervous_evasion": 12,
}
_SATURATION_K = 5
_BASELINE_DURATION_MINS = 30
_DEFAULT_WEIGHT_HIGH = 10
_DEFAULT_WEIGHT_MED  = 5
_SEVERITY_MULTIPLIER = {"high": 1.0, "medium": 0.4}
RISK_LABELS = [(15, "Low Risk"), (40, "Moderate Risk"), (70, "High Risk"), (100, "Critical Risk")]

def _risk_label(score: int) -> str:
    for threshold, label in RISK_LABELS:
        if score <= threshold:
            return label
    return "Critical Risk"

def compute_risk_score(session_id: str, teacher_id: str | None = None) -> dict:
    cache_key = f"risk_score:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached:
            return cached
    query = supabase.table("violations").select("violation_type,severity,created_at").eq("session_key", session_id)
    if teacher_id:
        query = query.eq("teacher_id", str(teacher_id))
    viol_result = query.order("created_at").execute()
    rows = viol_result.data or []
    scored = [r for r in rows if _is_violation(r["violation_type"]) and r["severity"] in ("high", "medium")]
    if not scored:
        return {"risk_score": 0, "label": "Low Risk", "duration_minutes": 0, "breakdown": {}}
    def _parse_ts(ts_str):
        try:
            return datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0
    timestamps = [_parse_ts(r["created_at"]) for r in rows if r.get("created_at")]
    timestamps = [t for t in timestamps if t > 0]
    duration_mins = (max(timestamps) - min(timestamps)) / 60.0 if len(timestamps) >= 2 else 1.0
    counts: dict[tuple[str, str], int] = {}
    for r in scored:
        key = (r["violation_type"], r["severity"])
        counts[key] = counts.get(key, 0) + 1
    breakdown: dict[str, dict] = {}
    raw_sum = 0.0
    log_sat = math.log(1 + _SATURATION_K)
    for (vtype, sev), n in counts.items():
        weight = VIOLATION_WEIGHTS.get(vtype)
        if weight is None:
            weight = (_DEFAULT_WEIGHT_HIGH if sev == "high" else _DEFAULT_WEIGHT_MED)
        sev_mult = _SEVERITY_MULTIPLIER.get(sev, 0.4)
        contribution = weight * sev_mult * min(1.0, math.log(1 + n) / log_sat)
        raw_sum += contribution
        if vtype not in breakdown:
            breakdown[vtype] = {"count": 0, "contribution": 0.0}
        breakdown[vtype]["count"] += n
        breakdown[vtype]["contribution"] = round(breakdown[vtype]["contribution"] + contribution, 1)
    duration_factor = _BASELINE_DURATION_MINS / max(duration_mins, 5.0)
    normalized = raw_sum * duration_factor
    risk_score = min(100, round(normalized))
    result = {"risk_score": risk_score, "label": _risk_label(risk_score),
              "duration_minutes": round(duration_mins, 1), "breakdown": breakdown}
    if _cache:
        _cache.set(cache_key, result, ttl=30)
    return result

# ─── SCREENSHOT HELPERS ───────────────────────────────────────────
def _collect_session_screenshots(roll: str, teacher_id: str) -> dict[str, Path]:
    if not roll or not teacher_id:
        return {}
    student_dir = Path(SCREENSHOTS_DIR) / _safe_path_component(str(teacher_id)) / _safe_path_component(roll)
    if not student_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in sorted(student_dir.iterdir()):
        if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            out[f.name] = f
    return out

def _match_screenshot_for_violation(violation: dict, screenshots: dict[str, Path]) -> Path | None:
    if not screenshots or not violation.get("created_at"):
        return None
    try:
        evt_ts = datetime.fromisoformat(str(violation["created_at"]).replace("Z", "+00:00")).astimezone(IST)
    except Exception:
        return None
    vtype = violation.get("violation_type", "")
    window_keys = {(evt_ts + timedelta(seconds=delta)).strftime("%Y%m%d_%H%M%S") for delta in range(-2, 3)}
    for fname, fpath in screenshots.items():
        if fname.startswith(f"evt_{vtype}_") and any(k in fname for k in window_keys):
            return fpath
    for fname, fpath in screenshots.items():
        if fname.startswith("evt_") and any(k in fname for k in window_keys):
            return fpath
    for fname, fpath in screenshots.items():
        if any(k in fname for k in window_keys):
            return fpath
    return None

# ─── BULK QUERY HELPERS ───────────────────────────────────────────
def _violation_counts_by_session(session_keys: list[str]) -> dict[str, int]:
    if not session_keys:
        return {}
    counts: dict[str, int] = {}
    for i in range(0, len(session_keys), 200):
        chunk = session_keys[i:i + 200]
        viol_result = supabase.table("violations").select("session_key,violation_type,severity").in_("session_key", chunk).execute()
        for v in (viol_result.data or []):
            if v["severity"] in ("high", "medium") and _is_violation(v["violation_type"]):
                counts[v["session_key"]] = counts.get(v["session_key"], 0) + 1
    return counts

def _calibration_tiers_by_session(session_keys: list[str], teacher_id: Optional[str] = None) -> dict[str, dict]:
    if not session_keys:
        return {}
    q = (supabase.table("violations").select("session_key,details").eq("violation_type", "calibration_complete").in_("session_key", session_keys))
    if teacher_id:
        q = q.eq("teacher_id", str(teacher_id))
    rows = (q.execute()).data or []
    out: dict[str, dict] = {}
    for r in rows:
        sk = r.get("session_key")
        if not sk:
            continue
        out[sk] = _classify_calibration(_parse_calibration_details(r.get("details")))
    return out

# ─── INTEGRITY ────────────────────────────────────────────────────
BLOCKING_TYPES = {"vm_detected", "remote_desktop_detected", "vpn_detected", "proxy_detected", "debugger_detected"}

# ─── GROUP ACCESS ─────────────────────────────────────────────────
def _check_group_access(roll_number: str, teacher_id: str, exam_id: str) -> bool:
    assignments = (supabase.table("exam_group_assignments").select("group_id").eq("exam_id", exam_id).eq("teacher_id", teacher_id).execute()).data or []
    if not assignments:
        return True
    gids = [a["group_id"] for a in assignments]
    member = (supabase.table("student_group_members").select("id").in_("group_id", gids).eq("roll_number", roll_number).eq("teacher_id", teacher_id).limit(1).execute()).data
    return bool(member)

# ─── INVITE HELPERS ───────────────────────────────────────────────
import secrets as _secrets
INVITE_DAILY_CAP = int(os.environ.get("INVITE_DAILY_CAP", "500"))

def _get_invite_base_url() -> str:
    return os.environ.get("INVITE_BASE_URL", "").rstrip("/") or "https://app.procta.net"

def _new_invite_token() -> str:
    return _secrets.token_urlsafe(32)

def _new_access_code(length: int = 6) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(_secrets.choice(alphabet) for _ in range(length))

def _check_daily_cap(teacher_id: str, batch_size: int) -> tuple[bool, int]:
    from datetime import date as _date
    today = _date.today().isoformat()
    row = (supabase.table("invite_send_counters").select("count").eq("teacher_id", teacher_id).eq("day", today).execute()).data
    used = (row[0]["count"] if row else 0)
    remaining = INVITE_DAILY_CAP - used
    return (batch_size <= remaining, max(remaining, 0))

def _bump_daily_cap(teacher_id: str, delta: int = 1) -> None:
    from datetime import date as _date
    today = _date.today().isoformat()
    try:
        existing = (supabase.table("invite_send_counters").select("count").eq("teacher_id", teacher_id).eq("day", today).execute()).data
        if existing:
            supabase.table("invite_send_counters").update({"count": existing[0]["count"] + delta}).eq("teacher_id", teacher_id).eq("day", today).execute()
        else:
            supabase.table("invite_send_counters").insert({"teacher_id": teacher_id, "day": today, "count": delta}).execute()
    except Exception as e:
        print(f"[invites] cap bump failed: {e}")

# ─── CLEAR LIVE SESSION HELPERS ──────────────────────────────────
_CLEAR_TOKENS: dict[str, dict] = {}
_CLEAR_TOKEN_TTL = 60
_CLEAR_ACTIVE_WINDOW = 120

def _clear_token_issue(teacher_id: str) -> str:
    tok = _uuid.uuid4().hex
    payload = {"teacher_id": str(teacher_id)}
    if _cache:
        _cache.set(f"clear_token:{tok}", payload, ttl=_CLEAR_TOKEN_TTL)
    else:
        _CLEAR_TOKENS[tok] = {**payload, "expires": time.time() + _CLEAR_TOKEN_TTL}
        now = time.time()
        stale = [k for k, v in _CLEAR_TOKENS.items() if v["expires"] < now]
        for k in stale:
            _CLEAR_TOKENS.pop(k, None)
    return tok

def _session_is_active(row: dict) -> bool:
    hb = row.get("last_heartbeat")
    if not hb:
        return False
    try:
        if isinstance(hb, datetime):
            dt = hb
        else:
            dt = datetime.fromisoformat(str(hb).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    return age <= _CLEAR_ACTIVE_WINDOW

def _partition_live_sessions(teacher_id: str, exam_id: str | None = None, include_active: bool = False) -> tuple[list[dict], list[dict]]:
    tid = str(teacher_id)
    base = supabase.table("exam_sessions").select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id,exam_id").eq("teacher_id", tid).eq("status", SessionStatus.IN_PROGRESS)
    if exam_id:
        base = base.eq("exam_id", exam_id)
    result = base.execute()
    rows = list(result.data or [])
    seen = {r["session_key"] for r in rows}
    def _q_null():
        q = supabase.table("exam_sessions").select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id,exam_id").is_("teacher_id", "null").eq("status", SessionStatus.IN_PROGRESS)
        if exam_id:
            q = q.eq("exam_id", exam_id)
        return q.execute()
    def _q_empty():
        q = supabase.table("exam_sessions").select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id,exam_id").eq("teacher_id", "").eq("status", SessionStatus.IN_PROGRESS)
        if exam_id:
            q = q.eq("exam_id", exam_id)
        return q.execute()
    for fetch_fn in [_q_null, _q_empty]:
        try:
            for r in (fetch_fn().data or []):
                if r["session_key"] not in seen:
                    rows.append(r)
                    seen.add(r["session_key"])
        except Exception as e:
            print(f"[ClearLive] orphan query failed: {e}")
    try:
        cutoff = (now_ist() - timedelta(hours=48)).isoformat()
        viol_teacher = supabase.table("violations").select("session_key").eq("teacher_id", tid).gte("created_at", cutoff).execute()
        viol_orphan1 = supabase.table("violations").select("session_key").is_("teacher_id", "null").gte("created_at", cutoff).execute()
        viol_orphan2 = supabase.table("violations").select("session_key").eq("teacher_id", "").gte("created_at", cutoff).execute()
        all_viol_data = (viol_teacher.data or []) + (viol_orphan1.data or []) + (viol_orphan2.data or [])
        ghost_keys: set[str] = set()
        for v in all_viol_data:
            sk = v.get("session_key")
            if sk and sk not in seen:
                ghost_keys.add(sk)
        for sk in ghost_keys:
            rows.append({"session_key": sk, "roll_number": sk.split("_")[0] if "_" in sk else sk,
                         "full_name": None, "started_at": None, "last_heartbeat": None,
                         "teacher_id": tid, "_ghost": True})
            seen.add(sk)
    except Exception as e:
        print(f"[ClearLive] violations ghost discovery failed: {e}")
    active, stale = [], []
    for r in rows:
        if include_active:
            stale.append(r)
        else:
            (active if _session_is_active(r) else stale).append(r)
    return active, stale

def _clear_token_consume(token: str, teacher_id: str) -> bool:
    if _cache:
        rec = _cache.get(f"clear_token:{token}")
        if not rec or rec.get("teacher_id") != str(teacher_id):
            return False
        _cache.delete(f"clear_token:{token}")
        return True
    else:
        rec = _CLEAR_TOKENS.pop(token, None)
        if not rec or rec["teacher_id"] != str(teacher_id) or rec["expires"] < time.time():
            return False
        return True

# ─── SESSION LIVENESS ─────────────────────────────────────────────
def _heartbeat_age_seconds(hb) -> float | None:
    if not hb:
        return None
    try:
        if isinstance(hb, datetime):
            dt = hb
        else:
            dt = datetime.fromisoformat(str(hb).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()

def _derive_live_state(meta: dict) -> tuple[str, int | None]:
    status = (meta.get("status") or "").lower()
    if status in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED) or meta.get("submitted_at"):
        return "submitted", None
    age = _heartbeat_age_seconds(meta.get("last_heartbeat"))
    if age is not None and age <= _CLEAR_ACTIVE_WINDOW:
        return "live", int(age)
    return "stale", (int(age) if age is not None else None)

def _build_sessions_payload(tid: str, exam_id: str = None) -> dict:
    cutoff = (now_ist() - timedelta(hours=48)).isoformat()
    evts_query = supabase.table("violations").select("session_key,violation_type,severity,created_at,details").gte("created_at", cutoff)
    if tid:
        evts_query = evts_query.eq("teacher_id", str(tid))
    evts_result = evts_query.order("created_at", desc=True).execute()
    events = evts_result.data or []
    sess_query = supabase.table("exam_sessions").select("session_key,status,risk_score,exam_id,last_heartbeat,started_at,submitted_at")
    if tid:
        sess_query = sess_query.eq("teacher_id", str(tid))
    if exam_id:
        sess_query = sess_query.eq("exam_id", exam_id)
    sess_result = sess_query.execute()
    sess_meta = {r["session_key"]: r for r in (sess_result.data or [])}
    submitted = {sk for sk, m in sess_meta.items() if (m.get("status") or "").lower() in (SessionStatus.COMPLETED, SessionStatus.SUBMITTED) or m.get("submitted_at")}

    # ── Batch risk scores from in-memory violations (no per-session DB hits) ──
    viol_by_session: dict[str, list[dict]] = {}
    for e in events:
        viol_by_session.setdefault(e["session_key"], []).append(e)

    def _batch_risk_scores():
        """Compute risk scores for all sessions from the already-fetched violations."""
        scores: dict[str, tuple[int | None, str | None]] = {}
        for sk, rows in viol_by_session.items():
            scored = [r for r in rows if _is_violation(r["violation_type"]) and r["severity"] in ("high", "medium")]
            if not scored:
                scores[sk] = (0, "Low Risk")
                continue
            try:
                timestamps = []
                for r in rows:
                    ts = r.get("created_at")
                    if ts:
                        try:
                            timestamps.append(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
                        except Exception:
                            pass
                timestamps = [t for t in timestamps if t > 0]
                duration_mins = (max(timestamps) - min(timestamps)) / 60.0 if len(timestamps) >= 2 else 1.0
            except Exception:
                duration_mins = 1.0

            counts: dict[tuple[str, str], int] = {}
            for r in scored:
                key = (r["violation_type"], r["severity"])
                counts[key] = counts.get(key, 0) + 1

            raw_sum = 0.0
            log_sat = math.log(1 + _SATURATION_K)
            for (vtype, sev), n in counts.items():
                weight = VIOLATION_WEIGHTS.get(vtype)
                if weight is None:
                    weight = (_DEFAULT_WEIGHT_HIGH if sev == "high" else _DEFAULT_WEIGHT_MED)
                sev_mult = _SEVERITY_MULTIPLIER.get(sev, 0.4)
                raw_sum += weight * sev_mult * min(1.0, math.log(1 + n) / log_sat)

            duration_factor = _BASELINE_DURATION_MINS / max(duration_mins, 5.0)
            risk_score = min(100, round(raw_sum * duration_factor))
            scores[sk] = (risk_score, _risk_label(risk_score))
        return scores

    batch_risks = _batch_risk_scores()

    sessions: dict = {}
    for e in events:
        sk = e["session_key"]
        if exam_id and sk not in sess_meta:
            continue
        if sk not in sessions:
            meta = sess_meta.get(sk, {})
            cached_risk = meta.get("risk_score")
            if cached_risk is None and sk not in submitted:
                cached_risk, risk_label = batch_risks.get(sk, (None, None))
            else:
                risk_label = _risk_label(cached_risk) if cached_risk is not None else None
            live_state, hb_age = _derive_live_state(meta)
            sessions[sk] = {"session_id": sk, "last_event": e["violation_type"], "last_severity": e["severity"],
                            "last_seen": fmt_ist(e.get("created_at", "")), "details": e.get("details"),
                            "submitted": sk in submitted, "live_state": live_state, "heartbeat_age_sec": hb_age,
                            "risk_score": cached_risk, "risk_label": risk_label}

    # ── Batch calibration tiers from in-memory violations ──
    cal_tiers: dict[str, dict] = {}
    seen_cal_keys: set[str] = set()
    for e in events:
        if e["violation_type"] == "calibration_complete" and e["session_key"] not in seen_cal_keys:
            seen_cal_keys.add(e["session_key"])
            cal_tiers[e["session_key"]] = _classify_calibration(_parse_calibration_details(e.get("details")))

    for sk, sess in sessions.items():
        sess["calibration"] = cal_tiers.get(sk, {"tier": "missing", "reason": "No calibration recorded.", "ranges": None})
    active = [s for s in sessions.values() if s["live_state"] == "live"]
    return {"sessions": active, "all_sessions": list(sessions.values())}

def _fetch_all_results(teacher_id: str = None, exam_id: str = None) -> list[dict]:
    query = supabase.table("exam_sessions").select("*").eq("status", SessionStatus.COMPLETED)
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    sess_result = query.order("submitted_at", desc=True).execute()
    sessions = sess_result.data or []
    sks = [s["session_key"] for s in sessions]
    vcounts = _violation_counts_by_session(sks)
    cal_tiers = _calibration_tiers_by_session(sks, teacher_id=teacher_id)
    return [{"session_id": s["session_key"], "roll_number": s["roll_number"], "full_name": s["full_name"],
             "email": s.get("email", ""), "score": s.get("score", 0), "total": s.get("total", 0),
             "percentage": s.get("percentage", 0.0), "time_taken_secs": s.get("time_taken_secs", 0),
             "submitted_at": fmt_ist(s.get("submitted_at", "")), "violation_count": vcounts.get(s["session_key"], 0),
             "risk_score": s.get("risk_score"), "risk_label": _risk_label(s["risk_score"]) if s.get("risk_score") is not None else None,
             "calibration": cal_tiers.get(s["session_key"], {"tier": "missing", "reason": "No calibration recorded.", "ranges": None})}
            for s in sessions]

# ─── REMINDER LOOP ────────────────────────────────────────────────
REMINDER_POLL_SECONDS = int(os.environ.get("REMINDER_POLL_SECONDS", "300"))
REMINDER_1H_WINDOW_MIN  = 10
REMINDER_24H_WINDOW_MIN = 20

def _reminder_window(target_minutes: int, half_width_min: int):
    now = datetime.now(timezone.utc)
    centre = now + timedelta(minutes=target_minutes)
    return (centre - timedelta(minutes=half_width_min), centre + timedelta(minutes=half_width_min))

def _send_reminder_for_invite(inv: dict, exam_cfg: dict, hours_until: int) -> bool:
    from emailer import send_exam_reminder
    col = "reminder_1h_at" if hours_until < 24 else "reminder_24h_at"
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        claim = (supabase.table("student_invites").update({col: now_iso}).eq("token", inv["token"]).is_(col, "null").execute())
    except Exception as e:
        print(f"[reminders] claim failed token={inv.get('token','?')[:8]} err={e}", flush=True)
        return False
    if not claim.data:
        return False
    base = os.environ.get("INVITE_BASE_URL", "https://app.procta.net").rstrip("/")
    invite_url = f"{base}/invite/{inv['token']}"
    starts_display = fmt_ist(exam_cfg.get("starts_at")) if exam_cfg.get("starts_at") else ""
    try:
        result = send_exam_reminder(to_email=inv["email"], to_name=inv.get("full_name") or "",
                                     exam_title=exam_cfg.get("exam_title") or "Your exam",
                                     invite_url=invite_url, roll_number=inv.get("roll_number") or "",
                                     hours_until=hours_until, exam_starts_at_display=starts_display,
                                     access_code=inv.get("access_code") or None)
    except Exception as e:
        print(f"[reminders] send raised: {e}", flush=True)
        result = None
    if result is None or not getattr(result, "ok", False):
        try:
            supabase.table("student_invites").update({col: None}).eq("token", inv["token"]).execute()
        except Exception:
            pass
        print(f"[reminders] FAILED {hours_until}h reminder to={inv.get('email')} err={getattr(result,'error',None)!r}", flush=True)
        return False
    print(f"[reminders] SENT {hours_until}h reminder to={inv.get('email')} exam={exam_cfg.get('exam_id') or '?'}", flush=True)
    return True

async def _reminder_tick():
    buckets = [("reminder_1h_at", 60, REMINDER_1H_WINDOW_MIN, 1), ("reminder_24h_at", 24 * 60, REMINDER_24H_WINDOW_MIN, 24)]
    for col, target_min, half_width, hours_until in buckets:
        lo, hi = _reminder_window(target_min, half_width)
        try:
            exams_resp = (supabase.table("exam_config").select("exam_id,teacher_id,exam_title,starts_at,access_code,ends_at")
                          .gte("starts_at", lo.isoformat()).lte("starts_at", hi.isoformat()).execute())
        except Exception as e:
            print(f"[reminders] exam query failed: {e}", flush=True)
            continue
        exams = exams_resp.data or []
        if not exams:
            continue
        for exam_cfg in exams:
            eid = exam_cfg.get("exam_id")
            if not eid:
                continue
            try:
                inv_resp = (supabase.table("student_invites").select("token,email,full_name,roll_number,access_code,exam_id,status")
                            .eq("exam_id", eid).is_(col, "null").in_("status", [InviteStatus.SENT, InviteStatus.OPENED, InviteStatus.ACCEPTED]).execute())
            except Exception as e:
                print(f"[reminders] invites query failed exam={eid}: {e}", flush=True)
                continue
            for inv in (inv_resp.data or []):
                if not inv.get("email"):
                    continue
                try:
                    _send_reminder_for_invite(inv, exam_cfg, hours_until)
                except Exception as e:
                    print(f"[reminders] per-invite error: {e}", flush=True)

async def _reminder_loop():
    import asyncio as _asyncio
    import traceback as _tb
    while True:
        try:
            await _reminder_tick()
        except Exception as e:
            print(f"[reminders] tick crashed: {e}", flush=True)
            _tb.print_exc()
        await _asyncio.sleep(REMINDER_POLL_SECONDS)

# ─── DOWNLOAD/RELEASE CACHE ──────────────────────────────────────
import httpx
_RELEASE_CACHE: dict = {"mac_arm": "", "mac_x64": "", "win": "", "tag": ""}
_RELEASE_CACHE_EXPIRES: float = 0.0
_RELEASE_CACHE_LOCK = __import__("asyncio").Lock()

def _match_mac_arm64(name: str) -> bool:
    return name.lower().endswith("-arm64.dmg")

def _match_mac_x64(name: str) -> bool:
    n = name.lower()
    return n.endswith(".dmg") and "-arm64" not in n and "-mac" not in n.replace("-macos", "")

def _match_win(name: str) -> bool:
    n = name.lower()
    return n.endswith(".exe") and "setup" in n

async def _refresh_release_cache() -> None:
    global _RELEASE_CACHE, _RELEASE_CACHE_EXPIRES
    url = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "procta-backend"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            if r.status_code != 200:
                print(f"[Release] GitHub API returned {r.status_code}: {r.text[:200]}")
                _RELEASE_CACHE_EXPIRES = time.time() + 60
                return
            data = r.json()
    except Exception as e:
        print(f"[Release] Fetch failed: {e}")
        _RELEASE_CACHE_EXPIRES = time.time() + 60
        return
    assets = data.get("assets", []) or []
    tag = data.get("tag_name", "")
    found = {"mac_arm": "", "mac_x64": "", "win": ""}
    for a in assets:
        name = a.get("name", "") or ""
        url_ = a.get("browser_download_url", "") or ""
        if not url_:
            continue
        if not found["mac_arm"] and _match_mac_arm64(name):
            found["mac_arm"] = url_
        elif not found["mac_x64"] and _match_mac_x64(name):
            found["mac_x64"] = url_
        elif not found["win"] and _match_win(name):
            found["win"] = url_
    _RELEASE_CACHE = {**found, "tag": tag}
    _RELEASE_CACHE_EXPIRES = time.time() + RELEASE_TTL_SEC
    print(f"[Release] Auto-discovered {tag}: mac_arm={'✓' if found['mac_arm'] else '✗'} mac_x64={'✓' if found['mac_x64'] else '✗'} win={'✓' if found['win'] else '✗'}")

async def _resolve_release_asset(key: str) -> str:
    if time.time() >= _RELEASE_CACHE_EXPIRES:
        async with _RELEASE_CACHE_LOCK:
            if time.time() >= _RELEASE_CACHE_EXPIRES:
                await _refresh_release_cache()
    return _RELEASE_CACHE.get(key, "") or ""

async def _download_redirect(env_url: str, release_key: str, fallback_path: str, fallback_name: str):
    if env_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=env_url)
    auto = await _resolve_release_asset(release_key)
    if auto:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=auto)
    if os.path.exists(fallback_path):
        from fastapi.responses import FileResponse
        return FileResponse(fallback_path, filename=fallback_name, media_type="application/octet-stream")
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Installer not available")

# ─── INVITE LANDING HTML ──────────────────────────────────────────
def _render_invite_error(msg: str) -> str:
    safe = _html_escape(msg)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Procta invite</title>
<style>body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;
min-height:100vh;padding:24px}}
.card{{background:#1e293b;border-radius:16px;padding:40px;max-width:480px;text-align:center;
border:1px solid #334155}}
h1{{color:#f87171;margin:0 0 16px 0;font-size:24px}}
p{{color:#94a3b8;line-height:1.6;margin:0}}</style></head>
<body><div class="card"><h1>Invite unavailable</h1><p>{safe}</p></div></body></html>"""

def _render_invite_landing(*, token, full_name, exam_title, roll_number, access_code, starts_at, ends_at) -> str:
    _e = _html_escape
    code_block = ""
    if access_code:
        code_block = f'''
      <div class="field">
        <div class="lbl">Access code</div>
        <div class="val"><code>{_e(access_code)}</code>
          <button class="copy" onclick="copyVal('{_e(access_code)}', this)">Copy</button></div>
      </div>'''
    time_block = ""
    if starts_at:
        time_block += f'<div class="meta"><b>Starts:</b> {_e(starts_at)}</div>'
    if ends_at:
        time_block += f'<div class="meta"><b>Closes:</b> {_e(ends_at)}</div>'
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>{_e(exam_title)} — Procta invite</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0f172a;color:#e2e8f0;min-height:100vh;padding:24px}}
.wrap{{max-width:640px;margin:0 auto}}
.hero{{background:linear-gradient(135deg,#10b981,#3b82f6);border-radius:20px;padding:36px;margin-bottom:16px}}
.brand{{color:#fff;font-size:12px;letter-spacing:2px;font-weight:700;opacity:.9}}
.title{{color:#fff;font-size:28px;font-weight:700;margin-top:8px;line-height:1.2}}
.subtitle{{color:#e0f2fe;font-size:15px;margin-top:8px}}
.card{{background:#1e293b;border-radius:16px;padding:24px;border:1px solid #334155;margin-bottom:16px}}
h2{{margin:0 0 16px 0;font-size:16px;color:#e2e8f0;font-weight:600}}
.field{{margin:12px 0}}
.lbl{{font-size:12px;color:#94a3b8;margin-bottom:4px}}
.val{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
code{{background:#0f172a;padding:6px 12px;border-radius:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:15px;color:#10b981;font-weight:600;border:1px solid #334155}}
.copy{{background:#334155;color:#e2e8f0;border:none;padding:6px 10px;border-radius:6px;
cursor:pointer;font-size:12px;font-weight:600}}
.copy:hover{{background:#475569}}
.copy.ok{{background:#10b981}}
.meta{{font-size:13px;color:#94a3b8;margin:6px 0}}
.dlbtn{{display:inline-block;background:#10b981;color:#fff;text-decoration:none;padding:14px 28px;
border-radius:10px;font-weight:600;margin:8px 4px 8px 0;transition:transform .1s}}
.dlbtn:hover{{transform:translateY(-1px)}}
.dlbtn.alt{{background:#475569}}
.step{{counter-increment:step;display:flex;gap:12px;align-items:flex-start;margin:14px 0}}
.step::before{{content:counter(step);flex:0 0 28px;height:28px;border-radius:50%;background:#10b981;
color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;font-size:14px}}
.steps{{counter-reset:step;padding:0}}
.step-body{{flex:1}}
.step-title{{font-weight:600;color:#e2e8f0;margin-bottom:2px}}
.step-desc{{font-size:13px;color:#94a3b8;line-height:1.5}}
.notice{{display:flex;gap:14px;align-items:flex-start;background:rgba(245,158,11,.08);
border:1px solid rgba(245,158,11,.35);border-radius:14px;padding:16px 20px;margin-bottom:16px}}
.notice .icon{{flex:0 0 28px;height:28px;border-radius:50%;background:#f59e0b;color:#1f2937;
font-weight:800;display:flex;align-items:center;justify-content:center;font-size:16px}}
.notice .body{{flex:1}}
.notice .t{{color:#fbbf24;font-weight:700;font-size:14px;margin-bottom:4px;letter-spacing:.02em}}
.notice .d{{color:#fde68a;font-size:13px;line-height:1.55}}
footer{{text-align:center;color:#64748b;font-size:12px;margin-top:20px}}
</style></head><body><div class="wrap">
  <div class="hero">
    <div class="brand">PROCTA · EXAM INVITE</div>
    <div class="title">{_e(exam_title)}</div>
    <div class="subtitle">Hi {_e(full_name)} — here's everything you need to get started.</div>
  </div>
  <div class="notice">
    <div class="icon">!</div>
    <div class="body">
      <div class="t">Desktop or laptop only</div>
      <div class="d">Procta runs as a secure desktop app on <b>Windows</b> and <b>macOS</b>.
        You can't take the exam on a phone or tablet. A mobile app is on the way —
        for now, open this invite on the computer you'll take the exam on.</div>
    </div>
  </div>
  <div class="card" id="app-launch-card" style="text-align:center">
    <h2 style="margin-bottom:6px">Already installed Procta?</h2>
    <p style="color:#94a3b8;font-size:13px;margin:0 0 14px 0">
      Skip the download — open this invite directly in your installed app.
    </p>
    <a id="open-in-app" class="dlbtn" href="#"
       onclick="openInApp(event); return false;"
       data-token="{_e(token)}"
       style="background:#1e293b;border:1px solid #334155;color:#e2e8f0">
      Open in Procta app
    </a>
    <p style="color:#64748b;font-size:11px;margin:14px 0 0 0;line-height:1.5">
      Don't have it installed? Skip this and use the download buttons below.
    </p>
  </div>
  <div class="card">
    <h2>Your credentials</h2>
    <div class="field">
      <div class="lbl">Roll number</div>
      <div class="val"><code>{_e(roll_number)}</code>
        <button class="copy" onclick="copyVal('{_e(roll_number)}', this)">Copy</button></div>
    </div>
    {code_block}
    {time_block}
  </div>
  <div class="card">
    <h2>Download Procta</h2>
    <div id="dlbtns">
      <a id="primary-dl" class="dlbtn" href="/download/win">Download (detecting OS…)</a>
    </div>
    <div style="margin-top:12px;font-size:13px">
      <a class="dlbtn alt" href="/download/mac">macOS (Apple Silicon)</a>
      <a class="dlbtn alt" href="/download/mac-x64">macOS (Intel)</a>
      <a class="dlbtn alt" href="/download/win">Windows</a>
    </div>
  </div>
  <div class="card">
    <h2>How to take the exam</h2>
    <div class="steps">
      <div class="step"><div class="step-body"><div class="step-title">Install Procta</div>
        <div class="step-desc">Run the installer you just downloaded.</div></div></div>
      <div class="step"><div class="step-body"><div class="step-title">Launch and sign in</div>
        <div class="step-desc">Enter the roll number{' and access code' if access_code else ''} shown above.</div></div></div>
      <div class="step"><div class="step-body"><div class="step-title">Take the exam</div>
        <div class="step-desc">When the exam window opens your questions appear.</div></div></div>
    </div>
  </div>
  <footer>Questions? Reply to the email you got this link from.</footer>
</div>
<script>
(function(){{
  var ua = (navigator.userAgent || '').toLowerCase();
  var btn = document.getElementById('primary-dl');
  if(!btn) return;
  if(ua.indexOf('mac') !== -1){{
    btn.href = '/download/mac';
    btn.textContent = 'Download for macOS';
  }} else if(ua.indexOf('win') !== -1){{
    btn.href = '/download/win';
    btn.textContent = 'Download for Windows';
  }} else {{
    btn.textContent = 'Download installer';
  }}
}})();
function copyVal(v, btn){{
  navigator.clipboard.writeText(v).then(function(){{
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('ok');
    setTimeout(function(){{ btn.textContent = orig; btn.classList.remove('ok'); }}, 1500);
  }});
}}
function openInApp(e){{
  var btn = document.getElementById('open-in-app');
  var token = btn ? btn.getAttribute('data-token') : '';
  if(!token) return;
  var launched = false;
  function markLaunched(){{ launched = true; }}
  window.addEventListener('blur', markLaunched, {{once:true}});
  document.addEventListener('visibilitychange', function h(){{
    if(document.hidden) markLaunched();
  }}, {{once:true}});
  var url = 'procta://invite/' + encodeURIComponent(token);
  try {{
    var f = document.createElement('iframe');
    f.style.display = 'none';
    f.src = url;
    document.body.appendChild(f);
    setTimeout(function(){{ try {{ f.remove(); }} catch(_){{}} }}, 2000);
  }} catch(_) {{}}
}}
</script>
</body></html>"""


CHAT_MAX_TEXT_LEN = 2000
CHAT_HISTORY_LIMIT = 50


class ChatHub:
    """In-memory hub for student-teacher chat sockets.

    Thread-safety note: FastAPI websockets run on the asyncio event loop, so
    all access happens on a single thread.  We still keep an asyncio.Lock for
    operations that fan out to multiple sockets, to avoid interleaving sends.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # session_id -> WebSocket (one active student socket per session)
        self.student_conns: dict[str, WebSocket] = {}
        # teacher_id -> set[WebSocket] (a teacher may have multiple tabs open)
        self.teacher_conns: dict[str, set[WebSocket]] = {}
        # teacher_id -> {session_id: deque[msg]}
        self.threads: dict[str, dict[str, deque]] = {}
        # session_id -> {roll, name, teacher_id, joined_at}
        self.student_meta: dict[str, dict] = {}

    # ── helpers ────────────────────────────────────────────────
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

    # ── student side ───────────────────────────────────────────
    async def register_student(self, *, session_id: str, teacher_id: str,
                               roll: str, name: str, ws: WebSocket) -> None:
        async with self._lock:
            old = self.student_conns.get(session_id)
            if old is not None and old is not ws:
                try:
                    await old.close(code=4000)
                except Exception:
                    pass
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

    async def student_send(self, session_id: str, text: str) -> Optional[dict]:
        meta = self.student_meta.get(session_id)
        if not meta:
            return None
        msg = self._make_msg(sender="student", session_id=session_id, text=text)
        msg["roll"] = meta["roll"]
        msg["name"] = meta["name"]
        self._thread(meta["teacher_id"], session_id).append(msg)

        student_ws = self.student_conns.get(session_id)
        if student_ws is not None:
            await self._safe_send(student_ws, msg)

        await self._fanout_teachers(meta["teacher_id"], msg)
        return msg

    # ── teacher side ───────────────────────────────────────────
    async def register_teacher(self, teacher_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self.teacher_conns.setdefault(teacher_id, set()).add(ws)

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

    async def teacher_send(self, teacher_id: str, session_id: str,
                           text: str) -> Optional[dict]:
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
        """Send a broadcast to every online student under this teacher.

        Returns the number of students the broadcast was delivered to.
        """
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

    # ── fan-out helpers ────────────────────────────────────────
    async def _fanout_teachers(self, teacher_id: str, payload: dict) -> None:
        dead: list[WebSocket] = []
        conns = list(self.teacher_conns.get(teacher_id, ()))
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


def _cleanup_screenshots():
    """Background thread that purges screenshots older than 7 days."""
    while True:
        time.sleep(3600)
        cutoff = now_ist() - timedelta(days=7)
        try:
            for student_dir in Path(SCREENSHOTS_DIR).iterdir():
                if student_dir.is_dir():
                    for f in student_dir.iterdir():
                        if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                            f.unlink()
        except Exception as e:
            print(f"[Cleanup] {e}")


# ─── RETRY UTILITY ────────────────────────────────────────────────
def with_retry(retries: int = 3, backoff_base: float = 0.5,
               retry_on: tuple = (Exception,)):
    """Decorator that retries a function with exponential backoff.

    Usage:
        @with_retry(retries=3)
        def fetch_questions():
            return supabase.table("questions").select("*").execute()

    Retries on network errors, timeouts, and HTTP 5xx responses.
    Does NOT retry on HTTP 4xx (client errors are not transient).
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(retries):
                try:
                    return fn(*args, **kwargs)
                except retry_on as e:
                    last_exc = e
                    # Don't retry client errors (4xx)
                    if hasattr(e, "status_code") and 400 <= e.status_code < 500:
                        raise
                    if attempt < retries - 1:
                        wait = backoff_base * (2 ** attempt)
                        time.sleep(wait)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


async def with_retry_async(retries: int = 3, backoff_base: float = 0.5,
                           retry_on: tuple = (Exception,)):
    """Async version of with_retry for use with async database calls."""
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(retries):
                try:
                    return await fn(*args, **kwargs)
                except retry_on as e:
                    last_exc = e
                    if hasattr(e, "status_code") and 400 <= e.status_code < 500:
                        raise
                    if attempt < retries - 1:
                        wait = backoff_base * (2 ** attempt)
                        await asyncio.sleep(wait)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator

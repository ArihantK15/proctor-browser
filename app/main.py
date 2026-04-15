import os
import csv
import io
import json
import base64
import math
import random
import hashlib
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import asyncio
import uuid as _uuid
from collections import deque
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from jose import jwt, JWTError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import supabase, async_table as _atable
from logger import get_logger
try:
    from event_bus import publish as _bus_publish, async_publish as _bus_async_publish, subscribe as _bus_subscribe
    _HAS_REDIS = True
except Exception:
    _HAS_REDIS = False
    def _bus_publish(*a, **kw): pass
    async def _bus_async_publish(*a, **kw): pass

try:
    import cache as _cache
except Exception:
    _cache = None

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

SECRET_KEY       = os.environ["SUPABASE_JWT_SECRET"]
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "").strip().lower()
SCREENSHOTS_DIR  = os.getenv("SCREENSHOTS_DIR", "/app/screenshots")
QUESTION_IMG_DIR = os.getenv("QUESTION_IMG_DIR", "/app/question_images")
DOWNLOAD_MAC_ARM = os.getenv("DOWNLOAD_MAC_ARM", "")
DOWNLOAD_MAC_X64 = os.getenv("DOWNLOAD_MAC_X64", "")
DOWNLOAD_WIN     = os.getenv("DOWNLOAD_WIN", "")
TOKEN_TTL_HOURS  = 10
ADMIN_TOKEN_TTL_HOURS = 12
STUDENT_AUTH_TTL_HOURS = 12  # student dashboard session (not the exam JWT)

os.makedirs(SCREENSHOTS_DIR,  exist_ok=True)
os.makedirs(QUESTION_IMG_DIR, exist_ok=True)

# ─── JWT ──────────────────────────────────────────────────────────
def create_token(roll_number: str, teacher_id: str = None, exam_id: str = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "roll": roll_number,
        "exp":  now + timedelta(hours=TOKEN_TTL_HOURS),
        "iat":  now,
    }
    if teacher_id:
        payload["tid"] = teacher_id
    if exam_id:
        payload["eid"] = exam_id
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def require_auth(request: Request) -> dict:
    """Student JWT auth — required for all exam endpoints."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        return jwt.decode(auth[7:], SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# ─── Teacher lookup cache (Redis-backed, falls back to in-process) ─
import threading as _threading
_teacher_cache = {}  # fallback: teacher_id -> teacher dict
_teacher_cache_ttl = {}  # fallback: teacher_id -> expiry timestamp
_teacher_cache_lock = _threading.Lock()

def _get_teacher_by_id(teacher_id: str) -> dict | None:
    """Look up teacher by our internal id, with 60s cache (Redis or in-process)."""
    if not teacher_id:
        return None
    # Try Redis first
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
    """Look up teacher by Supabase Auth UID. Used only at login/refresh time."""
    if not uid:
        return None
    result = supabase.table("teachers").select("*").eq("supabase_uid", str(uid)).execute()
    if not result.data:
        return None
    return result.data[0]

def issue_admin_token(teacher: dict) -> str:
    """Issue a strictly-verified HS256 admin JWT for a teacher."""
    now = datetime.now(timezone.utc)
    payload = {
        "tid":   str(teacher["id"]),
        "email": teacher.get("email", ""),
        "role":  "teacher",
        "iat":   now,
        "exp":   now + timedelta(hours=ADMIN_TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_admin_token(token: str) -> dict:
    """Strictly verify an admin HS256 token and return the teacher dict.

    Raises HTTPException on any failure. Shared by REST (require_admin) and WS.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=["HS256"],
            options={"verify_aud": False, "require": ["exp", "tid"]},
        )
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


def verify_student_token(token: str) -> dict:
    """Verify a student JWT. Returns decoded payload on success."""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise HTTPException(status_code=401, detail="Token expired")
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(request: Request) -> dict:
    """Teacher JWT auth — returns teacher dict with 'id' key.

    Verifies our own HS256 admin tokens issued by issue_admin_token().
    No fallbacks: every accepted token must be strictly signed by our SECRET_KEY.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return verify_admin_token(auth[7:])


# ─── Student-account (dashboard) auth ────────────────────────────
# NOTE: these JWTs gate the student web dashboard (listing exams, practice,
# profile). They are DIFFERENT from the exam JWT issued by create_token() —
# that one has a `roll` claim and is what the Electron app presents while an
# exam is in progress. A student_account JWT cannot take an exam; it can
# only list/enroll. Exam entry still goes through /api/validate-student
# which mints a fresh short-lived exam token.

_student_acct_cache = {}
_student_acct_cache_ttl = {}
_student_acct_cache_lock = _threading.Lock()


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
    """Issue an HS256 JWT for the student web dashboard."""
    now = datetime.now(timezone.utc)
    payload = {
        "sid":   str(account["id"]),
        "email": account.get("email", ""),
        "role":  "student_account",
        "iat":   now,
        "exp":   now + timedelta(hours=STUDENT_AUTH_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_student_auth_token(token: str) -> dict:
    """Strictly verify a student-account token and return the account dict."""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=["HS256"],
            options={"verify_aud": False, "require": ["exp", "sid"]},
        )
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
    """Student-dashboard JWT auth — returns student_account dict."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    return verify_student_auth_token(auth[7:])

# ─── RATE LIMITER ─────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ─── APP ──────────────────────────────────────────────────────────
app = FastAPI(title="AI Proctor Server")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # JWT auth provides security; CORS can't help with file:// origin
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── GLOBAL ERROR HANDLER ────────────────────────────────────────
import traceback as _tb
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

@app.exception_handler(Exception)
async def _global_exception_handler(request: StarletteRequest, exc: Exception):
    print(f"[UNHANDLED] {request.method} {request.url.path}: {exc}")
    _tb.print_exc()
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# ─── STATIC FILES & ADMIN DASHBOARD ──────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard():
    html_path = STATIC_DIR / "dashboard.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return HTMLResponse(html_path.read_text())


# ─── STUDENT DOWNLOAD PAGE ────────────────────────────────────────
@app.get("/download", response_class=HTMLResponse)
def download_page():
    """Auto-detect OS and offer the right installer."""
    html_path = STATIC_DIR / "download.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Download page not found")
    return HTMLResponse(html_path.read_text())

# ─── STUDENT REGISTRATION PAGE ───────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
def register_page():
    """Self-registration page for students before exam day."""
    html_path = STATIC_DIR / "register.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Registration page not found")
    return HTMLResponse(html_path.read_text())


# ─── STUDENT WEB DASHBOARD ───────────────────────────────────────
@app.get("/student", response_class=HTMLResponse)
def student_page():
    """Student-facing dashboard: upcoming exams, practice, profile.

    This is the web home for students between exams. The browser lock
    (Electron app) is NOT required to view this page — it only locks
    down when the student actually starts an exam.
    """
    html_path = STATIC_DIR / "student.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Student dashboard not found")
    return HTMLResponse(html_path.read_text())


# ─── TEACHER AUTH ────────────────────────────────────────────────

class TeacherSignupIn(BaseModel):
    email:     str
    password:  str
    full_name: str

class TeacherLoginIn(BaseModel):
    email:    str
    password: str

class RefreshIn(BaseModel):
    refresh_token: str

class StudentSignupIn(BaseModel):
    email:     str
    password:  str
    full_name: str

class StudentLoginIn(BaseModel):
    email:    str
    password: str

@app.post("/api/auth/signup")
@limiter.limit("5/hour")
async def teacher_signup(body: TeacherSignupIn, request: Request):
    """Create a new teacher account via Supabase Auth."""
    email = body.email.strip().lower()
    name = body.full_name.strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check if teacher already exists in our table
    existing = supabase.table("teachers").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    try:
        # Create Supabase Auth user (email_confirm=True skips verification for v1)
        auth_resp = supabase.auth.admin.create_user({
            "email": email,
            "password": body.password,
            "email_confirm": True,
        })
        supabase_uid = auth_resp.user.id
    except Exception as e:
        err_msg = str(e).lower()
        if "already registered" in err_msg or "duplicate" in err_msg:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        print(f"[TeacherSignup] Supabase Auth error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create account")

    # Insert teacher record
    teacher_row = {
        "email": email,
        "full_name": name,
        "supabase_uid": str(supabase_uid),
    }
    try:
        result = supabase.table("teachers").insert(teacher_row).execute()
        teacher = result.data[0]
    except Exception as e:
        print(f"[TeacherSignup] DB insert error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create teacher record")

    # Create default exam_config for this teacher
    try:
        supabase.table("exam_config").insert({
            "teacher_id": teacher["id"],
            "exam_title": "Exam",
            "duration_minutes": 60,
        }).execute()
    except Exception:
        pass  # Non-fatal — teacher can set this later

    print(f"[TeacherSignup] {name} <{email}> created")
    return {"teacher_id": teacher["id"], "email": email, "full_name": name}


@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def teacher_login(body: TeacherLoginIn, request: Request):
    """Log in a teacher via Supabase Auth, return JWT tokens."""
    email = body.email.strip().lower()
    try:
        auth_resp = supabase.auth.sign_in_with_password({
            "email": email,
            "password": body.password,
        })
    except Exception as e:
        print(f"[TeacherLogin] Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase_uid = str(auth_resp.user.id)
    teacher = _get_teacher_by_uid(supabase_uid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found. Please sign up first.")

    return {
        "access_token": issue_admin_token(teacher),
        "refresh_token": auth_resp.session.refresh_token,
        "teacher": {
            "id": teacher["id"],
            "email": teacher["email"],
            "full_name": teacher["full_name"],
        },
    }


@app.get("/api/auth/me")
async def teacher_me(request: Request):
    """Get current teacher profile from Bearer token."""
    teacher = require_admin(request)
    return {
        "id": teacher["id"],
        "email": teacher["email"],
        "full_name": teacher["full_name"],
    }


@app.post("/api/auth/refresh")
async def teacher_refresh(body: RefreshIn, request: Request):
    """Refresh an expired teacher access token via Supabase refresh token.

    The Supabase refresh token is the only credential the client retains
    long-term; we re-validate it via Supabase, look up the teacher, and
    issue a fresh HS256 admin token signed by us.
    """
    try:
        auth_resp = supabase.auth.refresh_session(body.refresh_token)
    except Exception as e:
        print(f"[TeacherRefresh] Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise HTTPException(status_code=401, detail="Invalid refresh response")

    supabase_uid = str(auth_resp.user.id)
    teacher = _get_teacher_by_uid(supabase_uid)
    if not teacher:
        raise HTTPException(status_code=403, detail="Teacher account not found")

    return {
        "access_token":  issue_admin_token(teacher),
        "refresh_token": auth_resp.session.refresh_token,
    }


class PasswordResetIn(BaseModel):
    email: str

@app.post("/api/auth/password-reset")
@limiter.limit("3/minute")
async def teacher_password_reset(body: PasswordResetIn, request: Request):
    """Send a password reset email via Supabase Auth."""
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        supabase.auth.reset_password_for_email(email)
    except Exception as e:
        print(f"[PasswordReset] Error for {email}: {e}")
        # Don't reveal whether the email exists or not
    return {"status": "ok", "message": "If that email is registered, a reset link has been sent."}


# ─── STUDENT DASHBOARD AUTH ──────────────────────────────────────

@app.get("/api/student/account-exists")
@limiter.limit("20/minute")
async def student_account_exists(request: Request, email: str = ""):
    """Check if a student dashboard account exists for this email."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return {"exists": False}
    result = supabase.table("student_accounts")\
        .select("id", count="exact")\
        .eq("email", email)\
        .execute()
    return {"exists": (result.count or 0) > 0}


@app.post("/api/student/auth/signup")
@limiter.limit("5/hour")
async def student_signup(body: StudentSignupIn, request: Request):
    """Create a new student dashboard account via Supabase Auth.

    After creating the auth user + student_accounts row, we auto-link any
    pre-existing per-teacher `students` enrollments that match the email
    so the student immediately sees their upcoming exam(s) on first login.
    """
    email = body.email.strip().lower()
    name = body.full_name.strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = supabase.table("student_accounts").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    try:
        auth_resp = supabase.auth.admin.create_user({
            "email": email,
            "password": body.password,
            "email_confirm": True,
        })
        supabase_uid = auth_resp.user.id
    except Exception as e:
        err_msg = str(e).lower()
        if "already registered" in err_msg or "duplicate" in err_msg:
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        print(f"[StudentSignup] Supabase Auth error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create account")

    try:
        result = supabase.table("student_accounts").insert({
            "email":        email,
            "full_name":    name,
            "supabase_uid": str(supabase_uid),
        }).execute()
        account = result.data[0]
    except Exception as e:
        print(f"[StudentSignup] DB insert error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create student record")

    # Auto-link any existing enrollments by matching email (case-insensitive).
    try:
        supabase.table("students")\
            .update({"account_id": account["id"]})\
            .eq("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception as e:
        print(f"[StudentSignup] Auto-link warning: {e}")

    print(f"[StudentSignup] {name} <{email}> created")
    return {
        "account_id": account["id"],
        "email":      email,
        "full_name":  name,
    }


@app.post("/api/student/auth/login")
@limiter.limit("10/minute")
async def student_login(body: StudentLoginIn, request: Request):
    email = body.email.strip().lower()
    try:
        auth_resp = supabase.auth.sign_in_with_password({
            "email": email,
            "password": body.password,
        })
    except Exception as e:
        print(f"[StudentLogin] Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    supabase_uid = str(auth_resp.user.id)
    account = _get_student_account_by_uid(supabase_uid)
    if not account:
        raise HTTPException(
            status_code=403,
            detail="No student account found for this login. Please sign up first.")

    # Opportunistic auto-link on every login in case the student was
    # registered by a teacher AFTER they created their account.
    try:
        supabase.table("students")\
            .update({"account_id": account["id"]})\
            .eq("email", email)\
            .is_("account_id", "null")\
            .execute()
    except Exception:
        pass

    return {
        "access_token":  issue_student_auth_token(account),
        "refresh_token": auth_resp.session.refresh_token,
        "account": {
            "id":        account["id"],
            "email":     account["email"],
            "full_name": account["full_name"],
        },
    }


@app.get("/api/student/auth/me")
async def student_me(request: Request):
    account = require_student_account(request)
    return {
        "id":        account["id"],
        "email":     account["email"],
        "full_name": account["full_name"],
    }


@app.post("/api/student/auth/refresh")
async def student_refresh(body: RefreshIn, request: Request):
    try:
        auth_resp = supabase.auth.refresh_session(body.refresh_token)
    except Exception as e:
        print(f"[StudentRefresh] Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if not auth_resp or not auth_resp.user or not auth_resp.session:
        raise HTTPException(status_code=401, detail="Invalid refresh response")

    account = _get_student_account_by_uid(str(auth_resp.user.id))
    if not account:
        raise HTTPException(status_code=403, detail="Student account not found")

    return {
        "access_token":  issue_student_auth_token(account),
        "refresh_token": auth_resp.session.refresh_token,
    }


@app.get("/api/student/exams")
async def student_exams(request: Request):
    """List the signed-in student's upcoming / active / completed exams.

    Joins student_accounts → students (per-teacher enrollments) → exam_config
    and exam_sessions to produce a single flat list. Phase 1 has one
    exam_config per teacher, so a student sees at most one exam per teacher
    they are enrolled with — multi-exam per teacher is Phase 6.
    """
    account = require_student_account(request)

    # 1. enrollments for this account
    enroll_resp = supabase.table("students")\
        .select("roll_number, teacher_id, full_name")\
        .eq("account_id", account["id"])\
        .execute()
    enrollments = enroll_resp.data or []

    out = []
    for e in enrollments:
        tid = e.get("teacher_id")
        if not tid:
            continue
        # teacher name for display
        teacher = _get_teacher_by_id(tid) or {}

        # Load ALL exam configs for this teacher (multi-exam)
        all_cfg_resp = supabase.table("exam_config")\
            .select("*")\
            .eq("teacher_id", tid)\
            .execute()
        all_cfgs = all_cfg_resp.data or []
        if not all_cfgs:
            # Fallback: single legacy config
            all_cfgs = [_load_exam_config(teacher_id=tid) or {}]

        for cfg in all_cfgs:
            exam_id = cfg.get("exam_id")

            # most recent session status for this student+exam (if any)
            sess_q = supabase.table("exam_sessions")\
                .select("session_key,status,started_at,submitted_at")\
                .eq("teacher_id", tid)\
                .eq("roll_number", e["roll_number"])
            if exam_id:
                sess_q = sess_q.eq("exam_id", exam_id)
            sess_resp = sess_q.order("started_at", desc=True).limit(1).execute()
            sess = (sess_resp.data or [{}])[0]

            now_utc = datetime.now(timezone.utc)
            starts_raw = cfg.get("starts_at")
            ends_raw   = cfg.get("ends_at")
            window = "open"  # default: no schedule → always open
            if starts_raw:
                starts_at = datetime.fromisoformat(str(starts_raw).replace("Z", "+00:00"))
                if now_utc < starts_at:
                    window = "upcoming"
            if ends_raw:
                ends_at = datetime.fromisoformat(str(ends_raw).replace("Z", "+00:00"))
                if now_utc > ends_at:
                    window = "closed"

            if sess.get("status") == "completed":
                status = "completed"
            elif sess.get("status") == "in_progress":
                status = "in_progress"
            else:
                status = window  # upcoming / open / closed

            out.append({
                "teacher_id":       tid,
                "exam_id":          exam_id,
                "teacher_name":     teacher.get("full_name", ""),
                "roll_number":      e["roll_number"],
                "exam_title":       cfg.get("exam_title", "Exam"),
                "duration_minutes": cfg.get("duration_minutes", 60),
                "starts_at":        cfg.get("starts_at"),
                "ends_at":          cfg.get("ends_at"),
                "access_code_required": bool(_get_access_code(tid, exam_id=exam_id)),
                "status":           status,
                "submitted_at":     sess.get("submitted_at"),
            })

    # sort: active first, then upcoming by start time, then completed
    def _sort_key(r):
        rank = {"in_progress": 0, "open": 1, "upcoming": 2, "closed": 3, "completed": 4}
        return (rank.get(r["status"], 5), r.get("starts_at") or "")
    out.sort(key=_sort_key)

    return {"exams": out}


# ─── BACKGROUND: SCREENSHOT CLEANUP ──────────────────────────────
def _cleanup_screenshots():
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

threading.Thread(target=_cleanup_screenshots, daemon=True).start()

# ─── MODELS ───────────────────────────────────────────────────────
class EventIn(BaseModel):
    session_id: str
    event_type: str
    severity:   str
    details:    Optional[str] = None

class RegisterIn(BaseModel):
    full_name:   str
    roll_number: str
    email:       str
    phone:       Optional[str] = None
    teacher_id:  Optional[str] = None

class ValidateIn(BaseModel):
    roll_number: str
    access_code: Optional[str] = None
    exam_id: Optional[str] = None

class ResultIn(BaseModel):
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
    session_id:  str
    question_id: str
    answer:      str

class BulkAnswerIn(BaseModel):
    session_id: str
    answers:    dict  # {question_id: answer, ...}

class FrameIn(BaseModel):
    session_id: str
    frame:      str
    timestamp:  str
    # Optional violation type so the local proctor can push evidence frames
    # that the forensics timeline can pair with their matching event row.
    # If absent we save the frame as a generic "frame_*" snapshot like before.
    event_type: Optional[str] = None

# ─── HELPERS ──────────────────────────────────────────────────────
def ts_to_id(ts_str: str) -> int:
    try:
        dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0

_NON_VIOLATION_TYPES = {
    "exam_submitted", "enrollment_started", "enrollment_complete",
    "exam_started", "submit_failed", "answer_selected", "session_ended",
    "face_enrolled", "heartbeat", "id_verification", "id_verification_captured",
    "calibration_started", "calibration_complete", "calibration_timeout",
}

def _is_violation(vtype: str) -> bool:
    return vtype not in _NON_VIOLATION_TYPES

def _load_questions(teacher_id: str = None, exam_id: str = None) -> list[dict]:
    """Load questions from Supabase, scoped to teacher and optionally exam.

    Uses ``select('*')`` so newly-added columns (``question_type``,
    ``image_url``) are surfaced automatically without a migration step.
    Unknown-column errors from older DBs fall back to the legacy shape.
    Returned dicts are normalised to always include:
        id, question, options, correct, question_type, image_url
    Results are cached in Redis for 300s.
    """
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
        query = supabase.table("questions").select(
            "question_id,question,options,correct")
        if teacher_id:
            query = query.eq("teacher_id", teacher_id)
        rows = (query.order("question_id").execute().data or [])

    out = []
    for q in rows:
        qtype = (q.get("question_type") or "mcq_single").strip().lower()
        if qtype not in ("mcq_single", "mcq_multi", "true_false"):
            qtype = "mcq_single"
        out.append({
            "id":            str(q["question_id"]),
            "question":      q.get("question", "") or "",
            "options":       q.get("options") or {},
            "correct":       str(q.get("correct") or ""),
            "question_type": qtype,
            "image_url":     q.get("image_url") or "",
        })
    if _cache and out:
        _cache.set(cache_key, out, ttl=300)
    return out

def _load_exam_config(teacher_id: str = None, exam_id: str = None) -> dict:
    """Load exam config from Supabase, scoped to teacher and optionally exam.

    Uses select('*') so newly-added columns (e.g. shuffle_questions /
    shuffle_options) are picked up automatically without code changes.
    Results are cached in Redis for 300s to avoid repeated DB calls.
    """
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
    elif not exam_id:
        query = query.eq("id", 1)  # legacy singleton fallback
    result = query.execute()
    if result.data:
        if _cache:
            _cache.set(cache_key, result.data[0], ttl=300)
        return result.data[0]
    default = {"exam_title": "Exam", "duration_minutes": 60, "access_code": "",
               "starts_at": None, "ends_at": None,
               "shuffle_questions": True, "shuffle_options": True}
    return default

def _get_access_code(teacher_id: str = None, exam_id: str = None) -> str:
    """Load the current exam access code from Supabase."""
    try:
        config = _load_exam_config(teacher_id, exam_id=exam_id)
        code = config.get("access_code", "")
        if code:
            return str(code).strip().upper()
    except Exception:
        pass
    return os.getenv("EXAM_ACCESS_CODE", "").strip().upper()

def _set_access_code(code: str, teacher_id: str = None, exam_id: str = None):
    """Persist access code to Supabase exam_config table."""
    if teacher_id and exam_id:
        supabase.table("exam_config").update({
            "access_code": code,
        }).eq("teacher_id", teacher_id).eq("exam_id", exam_id).execute()
    elif teacher_id:
        supabase.table("exam_config").upsert({
            "teacher_id": teacher_id,
            "access_code": code,
        }).execute()
    else:
        supabase.table("exam_config").upsert({
            "id": 1,
            "access_code": code,
        }).execute()


def _normalise_answer_set(ans: str) -> set[str]:
    """Parse an answer string like "A" or "A,C" into a normalised set.

    Used by both the grader and the admin answer-review view so a
    multi-correct question ("A,C") compares equal regardless of whether
    the student's selections were saved as "C,A", "A, C", etc.
    """
    if ans is None:
        return set()
    return {s.strip().upper() for s in str(ans).split(",") if s.strip()}


def _answers_match(student_ans: str, correct_ans: str) -> bool:
    """Return True iff the student's answer set equals the correct set."""
    return _normalise_answer_set(student_ans) == _normalise_answer_set(correct_ans)


def _recalculate_score(session_id: str, payload_answers: dict, teacher_id: str = None, exam_id: str = None) -> tuple[int, int]:
    """Calculate score server-side from Supabase questions + saved answers.

    Answers saved via /api/save-answer* are already translated to the
    canonical option keys at write time.  Fresh answers arriving in the
    submit payload are still in student-facing label space, so we
    translate them here before merging. Multi-correct answers are stored
    as comma-separated canonical keys (e.g. "A,C") and matched by set.
    """
    try:
        questions = _load_questions(teacher_id, exam_id=exam_id)
        total = len(questions)
        # DB answers are already canonical
        saved = supabase.table("answers").select("question_id,answer")\
            .eq("session_key", session_id).execute()
        ans_map = {str(r["question_id"]): str(r["answer"]) for r in (saved.data or [])}
        # Payload answers are student-facing → translate to canonical.
        # Multi-select answers arrive as "A,C" — translate each label.
        for qid, ans in (payload_answers or {}).items():
            ans_map[str(qid)] = _canonicalise_student_answer(
                session_id, str(teacher_id or ""), str(qid), str(ans))
        score = sum(1 for q in questions
                    if _answers_match(ans_map.get(q["id"], ""), q["correct"]))
        return score, total
    except Exception as e:
        print(f"[Score] Recalculation failed: {e}")
        return 0, 0  # fail-safe: never trust client score

# ─── BEHAVIORAL RISK SCORING ─────────────────────────────────────
# Computes a 0–100 risk score from violation history.
# Formula: log-saturating per-type weights, duration-normalized.

VIOLATION_WEIGHTS: dict[str, float] = {
    # Identity violations — highest weights
    "wrong_person":           30,
    "multiple_faces":         20,
    "face_missing":           15,
    # Cheating aids
    "cheat_object_detected":  25,
    # App evasion
    "window_focus_lost":      18,
    "tab_hidden":             15,
    "shortcut_blocked":       12,
    # Attention drift
    "gaze_away":               8,
    "head_turned":             8,
    "eyes_closed":             5,
    # Communication
    "voice_detected":         10,
    # Procedural
    "time_exceeded":          15,
    # Integrity (VM/environment)
    "vm_detected":            20,
    "remote_desktop_detected": 22,
    "screen_share_detected":  12,
    "multiple_monitors":       8,
}
_SATURATION_K = 5           # 5 occurrences ≈ full weight for that type
_BASELINE_DURATION_MINS = 30  # normalization baseline
_DEFAULT_WEIGHT_HIGH = 10   # fallback for unknown high-severity types
_DEFAULT_WEIGHT_MED  = 5    # fallback for unknown medium-severity types
# Per-severity multiplier applied to the per-type weight. Without this a
# fidgety honest student (lots of "medium" gaze_away after the calibration
# tier split) racks up the same score as a clear cheater (extreme gaze).
# 0.4 was picked so 3 medium events ≈ 1 high event of the same type.
_SEVERITY_MULTIPLIER = {"high": 1.0, "medium": 0.4}

RISK_LABELS = [
    (15,  "Low Risk"),
    (40,  "Moderate Risk"),
    (70,  "High Risk"),
    (100, "Critical Risk"),
]

def _risk_label(score: int) -> str:
    for threshold, label in RISK_LABELS:
        if score <= threshold:
            return label
    return "Critical Risk"


def _collect_session_screenshots(roll: str, teacher_id: str) -> dict[str, Path]:
    """Return {filename: absolute_path} for every screenshot belonging to a
    student session, scoped to the requesting teacher's evidence directory.
    Used by both the forensics timeline endpoint and the PDF export so they
    pair violations with screenshots identically.
    """
    if not roll or not teacher_id:
        return {}
    student_dir = Path(SCREENSHOTS_DIR) / str(teacher_id) / roll
    if not student_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in sorted(student_dir.iterdir()):
        if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            out[f.name] = f
    return out


def _match_screenshot_for_violation(
    violation: dict, screenshots: dict[str, Path]
) -> Path | None:
    """Pick the best screenshot for a violation row using a 3-pass match:
       1. evt_<violation_type>_<ts> within ±2s of the event timestamp
       2. any evt_* within the same window
       3. any frame_* within the same window (legacy renderer reference)
    Returns the absolute path or None if no plausible screenshot exists.
    """
    if not screenshots or not violation.get("created_at"):
        return None
    try:
        evt_ts = datetime.fromisoformat(
            str(violation["created_at"]).replace("Z", "+00:00")
        ).astimezone(IST)
    except Exception:
        return None
    vtype = violation.get("violation_type", "")
    window_keys = {
        (evt_ts + timedelta(seconds=delta)).strftime("%Y%m%d_%H%M%S")
        for delta in range(-2, 3)
    }
    # Pass 1
    for fname, fpath in screenshots.items():
        if fname.startswith(f"evt_{vtype}_") and any(k in fname for k in window_keys):
            return fpath
    # Pass 2
    for fname, fpath in screenshots.items():
        if fname.startswith("evt_") and any(k in fname for k in window_keys):
            return fpath
    # Pass 3
    for fname, fpath in screenshots.items():
        if any(k in fname for k in window_keys):
            return fpath
    return None


def _assert_session_owned(session_id: str, teacher_id: str) -> dict:
    """Verify a session belongs to the given teacher.

    Returns the session row, or raises 404 if it does not exist or belongs
    to another teacher. Use this on every endpoint that takes a session_id
    as a path parameter to prevent cross-teacher data leaks.

    Falls back to a violations-table check for in-progress sessions whose
    exam_sessions row hasn't been backfilled with teacher_id yet — this was
    breaking the forensics timeline button on the live tab right after the
    multi-tenant migration.
    """
    if not teacher_id:
        raise HTTPException(status_code=403, detail="Teacher context missing")
    tid_str = str(teacher_id)

    # Strict path: session_key + teacher_id match.
    result = supabase.table("exam_sessions")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", tid_str)\
        .limit(1)\
        .execute()
    if result.data:
        print(f"[OWN] strict hit  sid={session_id} tid={tid_str}")
        return result.data[0]

    # Fallback 1: row exists but teacher_id is NULL. Authorise via the
    # violations table — if every violation on this session belongs to the
    # requesting teacher, this is their session.
    bare = supabase.table("exam_sessions")\
        .select("*")\
        .eq("session_key", session_id)\
        .limit(1)\
        .execute()
    if bare.data:
        row = bare.data[0]
        row_tid = row.get("teacher_id")
        if row_tid in (None, ""):
            v_other = supabase.table("violations")\
                .select("teacher_id")\
                .eq("session_key", session_id)\
                .neq("teacher_id", tid_str)\
                .limit(1)\
                .execute()
            if not (v_other.data or []):
                print(f"[OWN] fallback1 (null tid) sid={session_id} tid={tid_str}")
                return row
            print(f"[OWN] DENY fallback1 — other teacher's violations exist "
                  f"sid={session_id} tid={tid_str}")
        else:
            print(f"[OWN] DENY strict — row owned by another teacher "
                  f"sid={session_id} req_tid={tid_str} row_tid={row_tid}")
        raise HTTPException(status_code=404, detail="Session not found")

    # Fallback 2: no exam_sessions row at all (very early in-progress).
    # Allow timeline if violations exist and all belong to this teacher.
    v_mine = supabase.table("violations")\
        .select("session_key,teacher_id")\
        .eq("session_key", session_id)\
        .eq("teacher_id", tid_str)\
        .limit(1)\
        .execute()
    if v_mine.data:
        print(f"[OWN] fallback2 (no session row, violations match) "
              f"sid={session_id} tid={tid_str}")
        return {
            "session_key": session_id,
            "teacher_id":  tid_str,
            "roll_number": (session_id.rsplit("_", 1)[0] if "_" in session_id
                            else session_id[:20]),
            "full_name":   "",
            "status":      "in_progress",
            "started_at":  "",
            "submitted_at": "",
            "score":       None,
            "total":       None,
            "risk_score":  None,
        }

    print(f"[OWN] DENY no match anywhere  sid={session_id} tid={tid_str}")
    raise HTTPException(status_code=404, detail="Session not found")


def compute_risk_score(session_id: str, teacher_id: str | None = None) -> dict:
    """Compute behavioral risk score (0–100) from the violations table.

    Returns dict with risk_score, label, duration_minutes, and per-type
    breakdown.  Safe to call for in-progress or completed sessions.
    When teacher_id is provided, the violations query is scoped to it.
    Results are cached in Redis for 30s to avoid N+1 queries on /sessions.
    """
    # Check Redis cache first
    cache_key = f"risk_score:{session_id}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached:
            return cached

    query = supabase.table("violations")\
        .select("violation_type,severity,created_at")\
        .eq("session_key", session_id)
    if teacher_id:
        query = query.eq("teacher_id", str(teacher_id))
    viol_result = query.order("created_at").execute()
    rows = viol_result.data or []

    # Filter to actual scorable violations
    scored = [r for r in rows
              if _is_violation(r["violation_type"])
              and r["severity"] in ("high", "medium")]

    if not scored:
        return {"risk_score": 0, "label": "Low Risk",
                "duration_minutes": 0, "breakdown": {}}

    # ── Duration from first to last event ────────────────────────────
    def _parse_ts(ts_str):
        try:
            return datetime.fromisoformat(
                str(ts_str).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0

    timestamps = [_parse_ts(r["created_at"]) for r in rows if r.get("created_at")]
    timestamps = [t for t in timestamps if t > 0]
    if len(timestamps) >= 2:
        duration_mins = (max(timestamps) - min(timestamps)) / 60.0
    else:
        duration_mins = 1.0  # single event — assume minimal duration

    # ── Count occurrences per (type, severity) ───────────────────────
    # Splitting by severity lets us downweight the new "medium" tier
    # (calibrated soft gaze/head flags) without losing the heavy hit
    # for an "extreme" gaze that landed under the same vtype.
    counts: dict[tuple[str, str], int] = {}
    for r in scored:
        key = (r["violation_type"], r["severity"])
        counts[key] = counts.get(key, 0) + 1

    # ── Compute per-(type,severity) contribution with log saturation ─
    breakdown: dict[str, dict] = {}
    raw_sum = 0.0
    log_sat = math.log(1 + _SATURATION_K)

    for (vtype, sev), n in counts.items():
        weight = VIOLATION_WEIGHTS.get(vtype)
        if weight is None:
            weight = (_DEFAULT_WEIGHT_HIGH if sev == "high"
                      else _DEFAULT_WEIGHT_MED)
        sev_mult = _SEVERITY_MULTIPLIER.get(sev, 0.4)
        contribution = weight * sev_mult * min(1.0, math.log(1 + n) / log_sat)
        raw_sum += contribution
        # Merge medium + high under the same vtype in the breakdown so
        # the dashboard's bar chart doesn't grow a duplicate row.
        if vtype not in breakdown:
            breakdown[vtype] = {"count": 0, "contribution": 0.0}
        breakdown[vtype]["count"]        += n
        breakdown[vtype]["contribution"] = round(
            breakdown[vtype]["contribution"] + contribution, 1)

    # ── Duration normalization ───────────────────────────────────────
    duration_factor = _BASELINE_DURATION_MINS / max(duration_mins, 5.0)
    normalized = raw_sum * duration_factor
    risk_score = min(100, round(normalized))

    result = {
        "risk_score":       risk_score,
        "label":            _risk_label(risk_score),
        "duration_minutes": round(duration_mins, 1),
        "breakdown":        breakdown,
    }
    # Cache for 30s — invalidated on new violation in log_event()
    if _cache:
        _cache.set(cache_key, result, ttl=30)
    return result


# ─── PUBLIC ENDPOINTS ─────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "AI Proctor Server running"}

@app.get("/sitemap.xml", response_class=HTMLResponse)
def sitemap():
    fpath = os.path.join(os.path.dirname(__file__), "static", "sitemap.xml")
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="sitemap.xml not found")
    with open(fpath) as f:
        content = f.read()
    from starlette.responses import Response
    return Response(content=content, media_type="application/xml")

@app.get("/robots.txt", response_class=HTMLResponse)
def robots_txt():
    content = (
        "User-agent: *\n"
        "Allow: /download\n"
        "Allow: /dashboard\n"
        "Disallow: /api/\n"
        "Disallow: /register\n"
        "Disallow: /student\n"
        "Disallow: /static/\n"
        "\n"
        "Sitemap: https://app.procta.net/sitemap.xml\n"
    )
    from starlette.responses import Response
    return Response(content=content, media_type="text/plain")

@app.get("/health")
def health():
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "ok": True,
            "memory_used_mb":  round(mem.used / 1024 / 1024),
            "memory_total_mb": round(mem.total / 1024 / 1024),
            "memory_percent":  mem.percent,
        }
    except ImportError:
        return {"ok": True}

@app.post("/api/register-student")
@limiter.limit("5/minute")
def register_student(request: Request, body: RegisterIn):
    """Public self-registration for students before exam day."""
    roll = body.roll_number.strip().upper()
    name = body.full_name.strip()
    email = body.email.strip().lower()
    phone = (body.phone or "").strip() or None

    if not roll:
        raise HTTPException(status_code=400, detail="Roll number is required")
    if not name:
        raise HTTPException(status_code=400, detail="Full name is required")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not body.teacher_id:
        raise HTTPException(
            status_code=400,
            detail="This registration link is missing the teacher identifier. Ask your examiner for the correct link.")

    # Validate that the teacher_id corresponds to a real teacher
    teacher = _get_teacher_by_id(body.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Unknown teacher")
    teacher_id = str(teacher["id"])

    # Uniqueness is per-teacher: same roll number can exist under different teachers
    existing = supabase.table("students")\
        .select("roll_number")\
        .eq("roll_number", roll)\
        .eq("teacher_id", teacher_id)\
        .execute()
    if existing.data:
        raise HTTPException(
            status_code=409,
            detail="This roll number is already registered. If this is a mistake, contact your examiner.")

    row = {
        "roll_number": roll,
        "full_name":   name,
        "email":       email,
        "phone":       phone,
        "teacher_id":  teacher_id,
    }
    try:
        supabase.table("students").insert(row).execute()
    except Exception as e:
        # Catch PK violation from race condition
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="This roll number is already registered.")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    return {"status": "registered", "roll_number": roll, "full_name": name}


@app.post("/api/admin/register-students-bulk")
def admin_bulk_register(request: Request, body: dict = Body(...)):
    """Admin-only bulk student registration (no rate limit)."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    students = body.get("students", [])
    if not students or not isinstance(students, list):
        raise HTTPException(status_code=400, detail="'students' must be a non-empty list")
    if len(students) > 500:
        raise HTTPException(status_code=400, detail="Max 500 students per batch")

    rows = []
    for s in students:
        roll = str(s.get("roll_number", "")).strip().upper()
        name = str(s.get("full_name", "")).strip()
        email = str(s.get("email", "")).strip().lower()
        phone = str(s.get("phone", "")).strip() or None
        if not roll or not name or not email:
            continue
        rows.append({
            "roll_number": roll,
            "full_name": name,
            "email": email,
            "phone": phone,
            "teacher_id": tid,
        })

    if not rows:
        raise HTTPException(status_code=400, detail="No valid students in payload")

    registered = 0
    skipped = 0
    for row in rows:
        try:
            supabase.table("students").insert(row).execute()
            registered += 1
        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                skipped += 1
            else:
                skipped += 1

    return {"registered": registered, "skipped": skipped, "total": len(rows)}


@app.get("/api/exam-schedule")
def get_public_schedule(t: str = None):
    """Public endpoint — returns exam title and schedule for download/register pages."""
    config = _load_exam_config(teacher_id=t)
    return {
        "exam_title":  config.get("exam_title", "Exam"),
        "duration_minutes": config.get("duration_minutes", 60),
        "starts_at":   config.get("starts_at"),
        "ends_at":     config.get("ends_at"),
    }

@app.post("/api/validate-student")
@limiter.limit("10/minute")
def validate_student(request: Request, body: ValidateIn):
    exam_id = body.exam_id  # optional — set when multi-exam

    # Look up student first to get their teacher_id for config loading
    pre_check = supabase.table("students")\
        .select("teacher_id")\
        .eq("roll_number", body.roll_number.strip().upper())\
        .execute()
    pre_tid = pre_check.data[0].get("teacher_id") if pre_check.data else None

    # Check exam time window using the student's teacher config
    config = _load_exam_config(pre_tid, exam_id=exam_id)
    now_utc = datetime.now(timezone.utc)
    if config.get("starts_at"):
        starts = datetime.fromisoformat(str(config["starts_at"]).replace("Z", "+00:00"))
        if now_utc < starts:
            raise HTTPException(
                status_code=403,
                detail=f"The exam has not started yet. It begins at {fmt_ist(config['starts_at'])}.")
    if config.get("ends_at"):
        ends = datetime.fromisoformat(str(config["ends_at"]).replace("Z", "+00:00"))
        if now_utc > ends:
            raise HTTPException(
                status_code=403,
                detail=f"The exam window has closed. It ended at {fmt_ist(config['ends_at'])}.")

    # Look up student first (most common error = wrong roll number)
    result = supabase.table("students")\
        .select("*")\
        .eq("roll_number", body.roll_number.strip().upper())\
        .execute()
    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="Roll number not found. Please complete registration first.")
    student = result.data[0]

    # Look up teacher's config for this student
    student_tid = student.get("teacher_id")

    # Check exam access code if configured (loaded from Supabase, persists across restarts)
    current_code = _get_access_code(student_tid, exam_id=exam_id)
    if current_code:
        if not body.access_code or body.access_code.strip().upper() != current_code:
            raise HTTPException(
                status_code=403,
                detail="Invalid exam access code. Ask your examiner for the correct code.")
    # Check group-based access restrictions
    if exam_id and student_tid:
        if not _check_group_access(student["roll_number"], str(student_tid), exam_id):
            raise HTTPException(
                status_code=403,
                detail="You are not in a group assigned to this exam. Contact your teacher.")

    completed_query = supabase.table("exam_sessions").select("session_key")\
        .eq("roll_number", student["roll_number"])\
        .eq("status", "completed")
    if student_tid:
        completed_query = completed_query.eq("teacher_id", str(student_tid))
    if exam_id:
        completed_query = completed_query.eq("exam_id", exam_id)
    completed = completed_query.execute()
    if completed.data:
        raise HTTPException(
            status_code=403,
            detail="You have already submitted this exam.")
    return {
        "valid":       True,
        "full_name":   student["full_name"],
        "email":       student.get("email", ""),
        "phone":       student.get("phone", ""),
        "roll_number": student["roll_number"],
        "token":       create_token(student["roll_number"], student_tid, exam_id=exam_id),
    }

# ─── PUBLIC: INSTALLER DOWNLOADS ─────────────────────────────────
@app.get("/download/mac")
def download_mac():
    if DOWNLOAD_MAC_ARM:
        return RedirectResponse(url=DOWNLOAD_MAC_ARM)
    path = "/app/downloads/ProctorBrowser-arm64.dmg"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Installer not found")
    return FileResponse(path, filename="ProctorBrowser-arm64.dmg",
                        media_type="application/octet-stream")

@app.get("/download/mac-x64")
def download_mac_x64():
    if DOWNLOAD_MAC_X64:
        return RedirectResponse(url=DOWNLOAD_MAC_X64)
    path = "/app/downloads/ProctorBrowser-x64.dmg"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Installer not found")
    return FileResponse(path, filename="ProctorBrowser-x64.dmg",
                        media_type="application/octet-stream")

@app.get("/download/win")
def download_win():
    if DOWNLOAD_WIN:
        return RedirectResponse(url=DOWNLOAD_WIN)
    path = "/app/downloads/ProctorBrowser-Setup.exe"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Installer not found")
    return FileResponse(path, filename="ProctorBrowser-Setup.exe",
                        media_type="application/octet-stream")

def _shuffle_seed(session_id: str, teacher_id: str) -> int:
    """Derive a deterministic 32-bit seed from (session_id, teacher_id).

    Using session_id (not roll) means a resumed exam keeps the same shuffle,
    and two exams from the same student get different shuffles. Mixing in
    teacher_id prevents cross-tenant seed collisions.
    """
    basis = f"{teacher_id or ''}::{session_id or ''}"
    return int(hashlib.sha256(basis.encode()).hexdigest(), 16) % (2**32)


def _build_shuffle_view(questions: list[dict], session_id: str,
                        teacher_id: str, *, shuffle_q: bool,
                        shuffle_o: bool) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """Build the per-student question view and the label translation map.

    Returns (student_questions, label_maps) where:
      - student_questions is the list the student will see (order + options).
      - label_maps[question_id][display_label] = original_label, used to
        translate answers back at save/grade time.

    Option shuffling: the display labels remain "A","B","C",... but the
    VALUES under them are a permutation of the originals. That way, two
    students sitting next to each other cannot share "the answer is B".
    """
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

        # Never shuffle options for True/False questions — swapping the
        # two labels would show the text "False" beside the key "True"
        # and vice versa, which makes no sense to a student.
        qtype = str(q.get("question_type") or "mcq_single").lower()
        tf_keys = set(orig_keys) == {"True", "False"}
        can_shuffle_opts = shuffle_o and len(orig_keys) > 1 and qtype != "true_false" and not tf_keys
        if can_shuffle_opts:
            perm = list(orig_keys)
            rng.shuffle(perm)
            # display_label i is shown the text from orig_keys[perm[i]]
            new_opts = {orig_keys[i]: opts[perm[i]] for i in range(len(orig_keys))}
            label_maps[qid] = {orig_keys[i]: perm[i] for i in range(len(orig_keys))}
            q = {**q, "options": new_opts}
        else:
            label_maps[qid] = {k: k for k in orig_keys}

        student_qs.append(q)

    return student_qs, label_maps


def _get_shuffle_flags(config: dict) -> tuple[bool, bool]:
    """Read shuffle toggles from an exam_config row with safe defaults."""
    sq = config.get("shuffle_questions")
    so = config.get("shuffle_options")
    if sq is None:
        sq = True
    if so is None:
        so = True
    return bool(sq), bool(so)


def _translate_student_answer(session_id: str, teacher_id: str,
                              question_id: str, student_label: str,
                              exam_id: str = None) -> str:
    """Map a student-facing answer label back to the original option key.

    Re-derives the deterministic shuffle from (session_id, teacher_id) and
    the current question set + config, then looks up the display→original
    mapping. On any failure, returns the student label unchanged so we
    never break grading for edge cases — the worst that happens is a
    student gets marked incorrectly for a single shuffled question, which
    will surface in QA.
    """
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
        _, label_maps = _build_shuffle_view(
            questions, session_id, teacher_id,
            shuffle_q=shuffle_q, shuffle_o=shuffle_o)
        qmap = label_maps.get(str(question_id))
        if not qmap:
            return student_label
        return qmap.get(str(student_label), student_label)
    except Exception as e:
        print(f"[Shuffle] translate failed q={question_id} s={student_label}: {e}")
        return student_label


# ─── STUDENT ENDPOINTS (require JWT) ─────────────────────────────
@app.get("/api/questions")
def get_questions(request: Request):
    claims = require_auth(request)
    tid = claims.get("tid")
    eid = claims.get("eid")
    questions = _load_questions(tid, exam_id=eid)
    if not questions:
        raise HTTPException(status_code=404, detail="Questions not found")
    config = _load_exam_config(tid, exam_id=eid)

    # Deterministic per-session shuffle — same session always gets the same
    # view, but two different students see different question/option orders.
    session_id = (request.query_params.get("session_id") or "").strip()
    shuffle_q, shuffle_o = _get_shuffle_flags(config)
    shuffled, _ = _build_shuffle_view(
        questions, session_id, str(tid or ""),
        shuffle_q=shuffle_q, shuffle_o=shuffle_o)

    # Strip correct answers — students must never see them
    safe_questions = [{k: v for k, v in q.items() if k != "correct"} for q in shuffled]
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "duration_minutes": config.get("duration_minutes"),
        "questions": safe_questions,
    }

@app.get("/api/check-session/{roll_number}")
def check_session(roll_number: str, request: Request):
    """Check if student has an in-progress session to resume."""
    claims = require_auth(request)
    if claims.get("roll") != roll_number:
        raise HTTPException(status_code=403, detail="Access denied")
    tid = claims.get("tid")
    eid = claims.get("eid")
    sess_query = supabase.table("exam_sessions").select("*")\
        .eq("roll_number", roll_number)\
        .eq("status", "in_progress")
    if tid:
        sess_query = sess_query.eq("teacher_id", str(tid))
    if eid:
        sess_query = sess_query.eq("exam_id", eid)
    result = sess_query.order("started_at", desc=True).limit(1).execute()
    if not result.data:
        return {"exists": False}
    session = result.data[0]
    ans_query = supabase.table("answers").select("*")\
        .eq("session_key", session["session_key"])
    if tid:
        ans_query = ans_query.eq("teacher_id", str(tid))
    answers = ans_query.execute()

    # Answers are stored in canonical form.  Build the reverse map so the
    # resumed student sees the correct option highlighted in their own
    # (shuffled) view.
    session_key = session["session_key"]
    config = _load_exam_config(str(tid or ""), exam_id=eid)
    shuffle_q, shuffle_o = _get_shuffle_flags(config)
    reverse: dict[str, dict[str, str]] = {}
    if shuffle_o:
        try:
            questions = _load_questions(str(tid or ""), exam_id=eid)
            _, label_maps = _build_shuffle_view(
                questions, session_key, str(tid or ""),
                shuffle_q=shuffle_q, shuffle_o=shuffle_o)
            for qid, qmap in label_maps.items():
                # qmap: display_label -> original; we need original -> display
                reverse[qid] = {orig: disp for disp, orig in qmap.items()}
        except Exception as e:
            print(f"[Resume] reverse map failed: {e}")

    resumed = {}
    for r in (answers.data or []):
        qid = str(r["question_id"])
        canonical = str(r["answer"])
        disp = reverse.get(qid, {}).get(canonical, canonical)
        resumed[qid] = disp

    return {
        "exists":      True,
        "session_key": session_key,
        "answer_count": len(resumed),
        "answers":     resumed,
        "started_at":  session.get("started_at"),
    }

def _check_session_ownership(claims: dict, session_id: str):
    """Raise 403 if the JWT roll doesn't match the session's roll prefix."""
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")


@app.post("/event")
@limiter.limit("120/minute")
async def log_event(event: EventIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    get_logger(event.session_id).info(
        f"[{event.severity.upper()}] {event.event_type} | {event.details}")

    # When exam starts, create in-progress session record
    if event.event_type == "exam_started":
        row = {
            "session_key": event.session_id,
            "roll_number": event.session_id.rsplit("_", 1)[0],
            "status":      "in_progress",
            "started_at":  now_ist().isoformat(),
        }
        if tid:
            row["teacher_id"] = tid
        if eid:
            row["exam_id"] = eid
        await _atable("exam_sessions").upsert(row).execute()

    # Alert on submission failure
    if event.event_type == "submit_failed":
        print(f"[ALERT] SUBMIT FAILED for session {event.session_id} "
              f"— use /api/admin-submit/{event.session_id} to recover")

    viol_row = {
        "session_key":    event.session_id,
        "violation_type": event.event_type,
        "severity":       event.severity,
        "details":        event.details,
    }
    if tid:
        viol_row["teacher_id"] = tid
    await _atable("violations").insert(viol_row).execute()

    # Invalidate cached risk score for this session
    if _cache:
        _cache.delete(f"risk_score:{event.session_id}")

    # Publish to Redis for SSE subscribers
    evt_payload = {"type": event.event_type, "severity": event.severity,
                   "details": event.details, "session_id": event.session_id}
    if tid:
        await _bus_async_publish(f"events:{tid}:{event.session_id}", evt_payload)
        await _bus_async_publish(f"sessions:{tid}", {**evt_payload, "kind": "violation"})

    return {"status": "logged"}

@app.post("/heartbeat")
async def heartbeat(event: EventIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    row = {
        "session_key":    event.session_id,
        "roll_number":    event.session_id.rsplit("_", 1)[0],
        "last_heartbeat": now_ist().isoformat(),
        "status":         "in_progress",
    }
    if tid:
        row["teacher_id"] = tid
    if eid:
        row["exam_id"] = eid
    await _atable("exam_sessions").upsert(row).execute()

    # Publish heartbeat to dashboard SSE
    if tid:
        await _bus_async_publish(f"sessions:{tid}", {"kind": "heartbeat",
                     "session_id": event.session_id})

    return {"ok": True}

def _canonicalise_student_answer(session_id: str, teacher_id: str,
                                  question_id: str, raw: str,
                                  exam_id: str = None) -> str:
    """Translate a (possibly multi-select) student answer into canonical form.

    Multi-select answers arrive as comma-separated student-facing labels
    like ``"A,C"``. We split, translate each label through the shuffle
    map, then return the sorted comma-joined canonical string so grading
    can compare as a set.
    """
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return ""
    translated = [
        _translate_student_answer(session_id, str(teacher_id or ""),
                                  str(question_id), p, exam_id=exam_id)
        for p in parts
    ]
    return ",".join(sorted(translated))


@app.post("/api/save-answer")
async def save_answer(body: AnswerIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    canonical = await asyncio.to_thread(
        _canonicalise_student_answer,
        body.session_id, str(tid or ""), str(body.question_id), str(body.answer),
        exam_id=eid)
    row = {
        "session_key":  body.session_id,
        "question_id":  body.question_id,
        "answer":       canonical,
    }
    if tid:
        row["teacher_id"] = tid
    if eid:
        row["exam_id"] = eid
    await _atable("answers").upsert(row).execute()
    return {"status": "saved"}

@app.post("/api/save-answers-bulk")
async def save_answers_bulk(body: BulkAnswerIn, request: Request):
    """Periodic bulk save of all answers — safety net for failed individual saves."""
    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    if not body.answers:
        return {"status": "empty", "saved": 0}
    tid = claims.get("tid")
    eid = claims.get("eid")
    def _build_records():
        recs = []
        for qid, ans in body.answers.items():
            canonical = _canonicalise_student_answer(
                body.session_id, str(tid or ""), str(qid), str(ans),
                exam_id=eid)
            rec = {"session_key": body.session_id,
                   "question_id": str(qid),
                   "answer":      canonical}
            if tid:
                rec["teacher_id"] = tid
            if eid:
                rec["exam_id"] = eid
            recs.append(rec)
        return recs
    records = await asyncio.to_thread(_build_records)
    await _atable("answers").upsert(records).execute()
    return {"status": "saved", "saved": len(records)}

@app.post("/api/submit-exam")
@limiter.limit("10/minute")
async def submit_exam(result: ResultIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, result.session_id)
    tid = claims.get("tid")
    eid = claims.get("eid")
    now = now_ist()

    # Server-side scoring — never trust client score (sync helper, mostly cached)
    server_score, server_total = await asyncio.to_thread(
        _recalculate_score, result.session_id, result.answers,
        teacher_id=tid, exam_id=eid)
    if server_score == 0 and server_total == 0:
        print(f"[WARN] Score recalculation returned 0/0 for {result.session_id} — check Supabase questions table")

    pct = round((server_score / max(server_total, 1)) * 100, 1)

    session_row = {
        "session_key":     result.session_id,
        "roll_number":     result.roll_number,
        "full_name":       result.full_name,
        "email":           result.email,
        "score":           server_score,
        "total":           server_total,
        "percentage":      pct,
        "time_taken_secs": result.time_taken_secs,
        "status":          "completed",
        "submitted_at":    now.isoformat(),
    }
    if tid:
        session_row["teacher_id"] = tid
    if eid:
        session_row["exam_id"] = eid
    await _atable("exam_sessions").upsert(session_row).execute()

    # Check time exceeded
    try:
        config = await asyncio.to_thread(_load_exam_config, teacher_id=tid, exam_id=eid)
        allowed_secs = config.get("duration_minutes", 60) * 60
        if result.time_taken_secs > allowed_secs + 120:  # 2 min grace
            viol = {
                "session_key":    result.session_id,
                "violation_type": "time_exceeded",
                "severity":       "high",
                "details":        f"Submitted {result.time_taken_secs - allowed_secs}s past time limit",
            }
            if tid:
                viol["teacher_id"] = tid
            await _atable("violations").insert(viol).execute()
    except Exception as e:
        print(f"[TimeCheck] {e}")

    # Log submission
    submit_viol = {
        "session_key":    result.session_id,
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        f"Score:{server_score}/{server_total} ({pct}%)",
    }
    if tid:
        submit_viol["teacher_id"] = tid
    await _atable("violations").insert(submit_viol).execute()

    # Cache behavioral risk score
    risk = await asyncio.to_thread(compute_risk_score, result.session_id, teacher_id=tid)
    upd = _atable("exam_sessions").eq("session_key", result.session_id)
    if tid:
        upd = upd.eq("teacher_id", str(tid))
    await upd.update({"risk_score": risk["risk_score"]}).execute()

    get_logger(result.session_id).info(
        f"[SUBMIT] {result.roll_number} score:{server_score}/{server_total} "
        f"risk:{risk['risk_score']}/100")

    # Publish submission to dashboard SSE
    if tid:
        await _bus_async_publish(f"sessions:{tid}", {"kind": "submitted",
                     "session_id": result.session_id,
                     "score": server_score, "total": server_total})

    return {"status": "submitted", "score": server_score,
            "total": server_total, "percentage": pct,
            "risk_score": risk["risk_score"], "risk_label": risk["label"]}

@app.post("/api/analyze-frame")
def analyze_frame(data: FrameIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, data.session_id)
    try:
        tid = claims.get("tid")
        roll = data.session_id.rsplit("_", 1)[0] if "_" in data.session_id \
               else data.session_id[:20]
        # Scope screenshots under teacher_id to avoid roll collisions across teachers
        if tid:
            student_dir = os.path.join(SCREENSHOTS_DIR, tid, roll)
        else:
            student_dir = os.path.join(SCREENSHOTS_DIR, roll)
        os.makedirs(student_dir, exist_ok=True)
        ts    = now_ist().strftime("%Y%m%d_%H%M%S")
        # Prefix evidence frames with the violation type so the forensics
        # timeline can pair them with their matching event row. Plain
        # renderer reference frames continue to use the "frame_" prefix.
        if data.event_type:
            safe_label = "".join(
                c if c.isalnum() or c in "_-" else "_"
                for c in data.event_type
            )[:32]
            fname = f"evt_{safe_label}_{ts}.jpg"
        else:
            fname = f"frame_{ts}.jpg"
        fpath = os.path.join(student_dir, fname)
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(data.frame))
    except Exception as e:
        print(f"[Frame] {e}")
    return {"status": "received"}

class IdVerifyIn(BaseModel):
    session_id:   str
    roll_number:  str
    selfie_frame: str            # base64 student face photo
    id_frame:     str            # base64 ID card photo
    full_name:    str = ""       # for dashboard display
    timestamp:    str = ""

@app.post("/api/id-verification")
def id_verification(data: IdVerifyIn, request: Request):
    """Store selfie + ID photos and create a pending verification for teacher review."""
    claims = require_auth(request)
    _check_session_ownership(claims, data.session_id)
    try:
        tid = claims.get("tid")
        roll = data.roll_number.strip().upper() or "UNKNOWN"
        if tid:
            student_dir = os.path.join(SCREENSHOTS_DIR, tid, roll)
        else:
            student_dir = os.path.join(SCREENSHOTS_DIR, roll)
        os.makedirs(student_dir, exist_ok=True)
        ts = now_ist().strftime("%Y%m%d_%H%M%S")

        selfie_fname = f"id_selfie_{ts}.jpg"
        id_fname     = f"id_card_{ts}.jpg"
        with open(os.path.join(student_dir, selfie_fname), "wb") as f:
            f.write(base64.b64decode(data.selfie_frame))
        with open(os.path.join(student_dir, id_fname), "wb") as f:
            f.write(base64.b64decode(data.id_frame))

        detail_obj = {
            "status":       "pending",
            "selfie_file":  selfie_fname,
            "id_file":      id_fname,
            "roll_number":  roll,
            "full_name":    data.full_name,
        }
        viol_row = {
            "session_key":    data.session_id,
            "violation_type": "id_verification",
            "severity":       "low",
            "details":        json.dumps(detail_obj),
        }
        if tid:
            viol_row["teacher_id"] = str(tid)
        supabase.table("violations").insert(viol_row).execute()
    except Exception as e:
        print(f"[ID Verify] {e}")
    return {"status": "received"}


@app.get("/api/id-verification/status")
def id_verification_status(request: Request, session_id: str = ""):
    """Student polls this to check if teacher has approved/retake/rejected."""
    claims = require_auth(request)
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    _check_session_ownership(claims, session_id)
    import json as _json
    result = supabase.table("violations")\
        .select("details")\
        .eq("session_key", session_id)\
        .eq("violation_type", "id_verification")\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    if not result.data:
        return {"status": "not_found"}
    raw = result.data[0].get("details", "")
    try:
        obj = json.loads(raw)
        return {"status": obj.get("status", "pending")}
    except Exception:
        return {"status": "pending"}


@app.get("/api/admin/pending-verifications")
def pending_verifications(request: Request, exam_id: str = None):
    """Return all pending ID verifications for this teacher."""
    teacher = require_admin(request)
    tid = teacher["id"]
    import json as _json
    query = supabase.table("violations")\
        .select("*")\
        .eq("teacher_id", str(tid))\
        .eq("violation_type", "id_verification")\
        .order("created_at", desc=True)
    # When exam_id is set, only show verifications for sessions in that exam
    exam_session_keys = None
    if exam_id:
        es = supabase.table("exam_sessions").select("session_key")\
            .eq("teacher_id", str(tid)).eq("exam_id", exam_id).execute()
        exam_session_keys = {r["session_key"] for r in (es.data or [])}
    result = query.execute()
    pending = []
    for row in (result.data or []):
        try:
            obj = json.loads(row.get("details", "{}"))
        except Exception:
            continue
        if obj.get("status") != "pending":
            continue
        # Filter by exam if requested
        if exam_session_keys is not None and row.get("session_key") not in exam_session_keys:
            continue
        roll = obj.get("roll_number", "")
        pending.append({
            "id":           row.get("id"),
            "session_key":  row.get("session_key"),
            "roll_number":  roll,
            "full_name":    obj.get("full_name", ""),
            "selfie_url":   f"/api/admin/screenshot/{roll}/{obj['selfie_file']}"
                            if obj.get("selfie_file") else None,
            "id_url":       f"/api/admin/screenshot/{roll}/{obj['id_file']}"
                            if obj.get("id_file") else None,
            "created_at":   fmt_ist(row.get("created_at", "")),
        })
    return {"pending": pending}


class IdDecisionIn(BaseModel):
    violation_id: int
    session_key:  str
    decision:     str  # "approved" | "retake" | "rejected"

@app.post("/api/admin/id-decision")
def id_decision(data: IdDecisionIn, request: Request):
    """Teacher approves, requests retake, or rejects a student's ID."""
    teacher = require_admin(request)
    tid = teacher["id"]
    if data.decision not in ("approved", "retake", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    import json as _json
    # Fetch the existing row
    result = supabase.table("violations")\
        .select("*")\
        .eq("id", data.violation_id)\
        .eq("teacher_id", str(tid))\
        .limit(1)\
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Verification not found")
    row = result.data[0]
    # Update the details JSON with the decision
    try:
        obj = json.loads(row.get("details", "{}"))
    except Exception:
        obj = {}
    obj["status"] = data.decision
    obj["decided_by"] = teacher.get("full_name", teacher.get("email", ""))
    obj["decided_at"] = now_ist().isoformat()
    supabase.table("violations")\
        .update({"details": json.dumps(obj)})\
        .eq("id", data.violation_id)\
        .execute()

    # If rejected, also log a high-severity event so it shows in the timeline
    # and invalidate the session so the student cannot proceed.
    if data.decision == "rejected":
        reject_row = {
            "session_key":    data.session_key,
            "violation_type": "id_rejected",
            "severity":       "high",
            "details":        f"Teacher rejected student identity — "
                              f"decided by {obj['decided_by']}",
        }
        if tid:
            reject_row["teacher_id"] = str(tid)
        supabase.table("violations").insert(reject_row).execute()
        # Invalidate risk score cache for this session
        if _cache:
            _cache.delete(f"risk_score:{data.session_key}")
        # Mark session as rejected so the student can't re-enter
        try:
            supabase.table("exam_sessions").update({
                "status":       "rejected",
                "submitted_at": now_ist().isoformat(),
            }).eq("session_key", data.session_key).execute()
        except Exception:
            pass  # best-effort — client-side exit already handles this

    return {"status": "ok", "decision": data.decision}

@app.get("/events/{session_id}")
def get_events(session_id: str, request: Request):
    claims = require_auth(request)
    # Ownership check: session_id is "{roll_number}_..." — student may only
    # read their own events. Admins use the admin endpoints instead.
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    tid = claims.get("tid")
    query = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)
    if tid:
        query = query.eq("teacher_id", str(tid))
    result = query.order("created_at").execute()
    events = result.data or []
    return {
        "session_id": session_id,
        "total":      len(events),
        "events": [
            {
                "id":        e.get("id") or ts_to_id(e.get("created_at", "")),
                "type":      e["violation_type"],
                "severity":  e["severity"],
                "timestamp": fmt_ist(e.get("created_at", "")),
                "details":   e.get("details"),
            }
            for e in events
        ],
    }

# ─── SSE STREAMING ENDPOINTS ─────────────────────────────────────

@app.get("/api/sse/sessions")
async def sse_sessions(request: Request, token: str = None):
    """Server-Sent Events stream for live dashboard updates.

    On connect, sends the full current state. Then yields incremental
    updates from Redis pub/sub so the dashboard never needs to poll.
    Token is passed as query param because EventSource doesn't support headers.
    """
    if token:
        try:
            claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            tid_from_claims = claims.get("tid")
            if not tid_from_claims:
                raise HTTPException(status_code=401, detail="Not a teacher token")
            teacher = _get_teacher_by_id(str(tid_from_claims))
            if not teacher:
                raise HTTPException(status_code=401, detail="Teacher not found")
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        teacher = require_admin(request)
    tid = str(teacher["id"])

    async def _generate():
        # 1. Send initial full state (same as GET /sessions)
        try:
            initial = await asyncio.to_thread(_build_sessions_payload, tid)
            yield f"event: init\ndata: {json.dumps(initial, default=str)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

        # 2. Stream incremental updates from Redis
        if not _HAS_REDIS:
            # No Redis — fall back to periodic refresh
            while True:
                await asyncio.sleep(5)
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.to_thread(_build_sessions_payload, tid)
                    yield f"event: refresh\ndata: {json.dumps(payload, default=str)}\n\n"
                except Exception:
                    pass
            return

        channel = f"sessions:{tid}"
        async for msg in _bus_subscribe(channel, keepalive_sec=15):
            if await request.is_disconnected():
                break
            if msg.get("_keepalive"):
                yield ":\n"  # SSE comment keepalive
            else:
                yield f"event: update\ndata: {json.dumps(msg, default=str)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/sse/events/{session_id}")
async def sse_student_events(session_id: str, request: Request, token: str = None):
    """SSE stream for per-student violation/force-submit events.

    The Electron client subscribes to this instead of polling GET /events/{sid}.
    Token is passed as query param because EventSource doesn't support headers.
    """
    # Auth: accept token from query param or Authorization header
    if token:
        try:
            claims = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        claims = require_auth(request)

    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    tid = claims.get("tid") or ""

    async def _generate():
        if not _HAS_REDIS:
            # No Redis — just keepalive, Electron falls back to polling
            while True:
                await asyncio.sleep(15)
                if await request.is_disconnected():
                    break
                yield ":\n"
            return

        channel = f"events:{tid}:{session_id}"
        async for msg in _bus_subscribe(channel, keepalive_sec=15):
            if await request.is_disconnected():
                break
            if msg.get("_keepalive"):
                yield ":\n"
            else:
                yield f"data: {json.dumps(msg, default=str)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _build_sessions_payload(tid: str, exam_id: str = None) -> dict:
    """Build the sessions payload (extracted from get_all_sessions for reuse)."""
    cutoff = (now_ist() - timedelta(hours=48)).isoformat()
    evts_query = supabase.table("violations")\
        .select("session_key,violation_type,severity,created_at,details")\
        .gte("created_at", cutoff)
    if tid:
        evts_query = evts_query.eq("teacher_id", str(tid))
    evts_result = evts_query.order("created_at", desc=True).execute()
    events = evts_result.data or []

    sess_query = supabase.table("exam_sessions").select(
        "session_key,status,risk_score,exam_id")
    if tid:
        sess_query = sess_query.eq("teacher_id", str(tid))
    if exam_id:
        sess_query = sess_query.eq("exam_id", exam_id)
    sess_result = sess_query.execute()
    sess_meta = {r["session_key"]: r for r in (sess_result.data or [])}
    submitted = {sk for sk, m in sess_meta.items() if m.get("status") == "completed"}

    sessions: dict = {}
    for e in events:
        sk = e["session_key"]
        if exam_id and sk not in sess_meta:
            continue
        if sk not in sessions:
            meta = sess_meta.get(sk, {})
            cached_risk = meta.get("risk_score")
            if cached_risk is None and sk not in submitted:
                try:
                    cached_risk = compute_risk_score(sk, teacher_id=tid)["risk_score"]
                except Exception:
                    cached_risk = None
            sessions[sk] = {
                "session_id":    sk,
                "last_event":    e["violation_type"],
                "last_severity": e["severity"],
                "last_seen":     fmt_ist(e.get("created_at", "")),
                "details":       e.get("details"),
                "submitted":     sk in submitted,
                "risk_score":    cached_risk,
                "risk_label":    _risk_label(cached_risk)
                                 if cached_risk is not None else None,
            }

    active = [s for s in sessions.values() if not s["submitted"]]
    return {"sessions": active, "all_sessions": list(sessions.values())}


# ─── ADMIN ENDPOINTS (require teacher Bearer token) ──────────────

@app.get("/api/risk-score/{session_id:path}")
def get_risk_score(session_id: str, request: Request):
    """Compute behavioral risk score for any session (live or completed)."""
    teacher = require_admin(request)
    tid = teacher["id"]
    _assert_session_owned(session_id, tid)
    result = compute_risk_score(session_id, teacher_id=tid)
    result["session_id"] = session_id
    return result


@app.get("/api/admin/timeline/{session_id:path}")
def get_timeline(session_id: str, request: Request):
    """Full forensics timeline: every event + screenshot paths for a session."""
    teacher = require_admin(request)
    tid = teacher["id"]
    session_info = _assert_session_owned(session_id, tid)
    viol_result = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .order("created_at")\
        .execute()
    events = viol_result.data or []

    # Gather screenshots for this student (teacher-scoped path only)
    roll = session_info.get("roll_number") or (
        session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
    )
    screenshot_paths = _collect_session_screenshots(roll, str(tid))
    # filename -> URL the dashboard fetches with admin auth
    screenshot_urls = {
        fname: f"/api/admin/screenshot/{roll}/{fname}"
        for fname in screenshot_paths
    }

    # Build timeline entries
    timeline = []
    for e in events:
        entry = {
            "id":        e.get("id"),
            "type":      e["violation_type"],
            "severity":  e["severity"],
            "timestamp": fmt_ist(e.get("created_at", "")),
            "raw_ts":    e.get("created_at", ""),
            "details":   e.get("details"),
            "is_violation": _is_violation(e["violation_type"]),
        }
        match = _match_screenshot_for_violation(e, screenshot_paths)
        if match is not None:
            entry["screenshot"] = screenshot_urls[match.name]
        timeline.append(entry)

    return {
        "session_id":  session_id,
        "roll_number": session_info.get("roll_number", roll),
        "full_name":   session_info.get("full_name", ""),
        "status":      session_info.get("status", "unknown"),
        "started_at":  fmt_ist(session_info.get("started_at", "")),
        "submitted_at": fmt_ist(session_info.get("submitted_at", "")),
        "score":       session_info.get("score"),
        "total":       session_info.get("total"),
        "risk_score":  session_info.get("risk_score"),
        "total_events": len(events),
        "timeline":    timeline,
        "screenshots": list(screenshot_urls.values()),
    }


@app.post("/api/admin/upload-question-image")
def upload_question_image(request: Request, body: dict = Body(...)):
    """Teacher uploads a question image.

    Accepts base64-encoded PNG/JPEG (with or without data URL prefix) and
    a filename hint. Stores the file under
    ``QUESTION_IMG_DIR/<teacher_id>/<sha1>.<ext>`` and returns the URL
    that students and teachers will fetch via ``/api/question-image/...``.

    Files are content-hashed so uploading the same image twice is a
    no-op (dedup) and the URL is stable across edits.
    """
    teacher = require_admin(request)
    tid = str(teacher["id"])
    raw = body.get("image") or body.get("data") or ""
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=400, detail="Missing 'image' (base64)")
    # Strip data URL prefix if present
    if raw.startswith("data:"):
        try:
            _, raw = raw.split(",", 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Malformed data URL")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image payload")
    if len(blob) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 4MB)")

    # Sniff format from magic bytes so we don't trust the client filename.
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
        media = "image/png"
    elif blob[:3] == b"\xff\xd8\xff":
        ext = "jpg"
        media = "image/jpeg"
    elif blob[:6] in (b"GIF87a", b"GIF89a"):
        ext = "gif"
        media = "image/gif"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        ext = "webp"
        media = "image/webp"
    else:
        raise HTTPException(status_code=400, detail="Unsupported image format (PNG/JPEG/GIF/WebP only)")

    digest = hashlib.sha1(blob).hexdigest()[:24]
    filename = f"{digest}.{ext}"
    tdir = Path(QUESTION_IMG_DIR) / tid
    tdir.mkdir(parents=True, exist_ok=True)
    fpath = tdir / filename
    if not fpath.exists():
        try:
            with open(fpath, "wb") as f:
                f.write(blob)
        except OSError as e:
            print(f"[QImage] write failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to store image")

    url = f"/api/question-image/{tid}/{filename}"
    return {"url": url, "bytes": len(blob), "media_type": media}


@app.get("/api/question-image/{tid}/{filename}")
def get_question_image(tid: str, filename: str, request: Request):
    """Serve a question image.

    Authenticated for both teachers (admin token) and students (exam JWT).
    Students can only fetch images scoped to their own teacher's tid,
    enforced via the ``tid`` claim on their exam token.
    """
    # Try admin auth first (teacher viewing/editing). If that fails, fall
    # back to student exam token scoped to the same teacher.
    auth = request.headers.get("Authorization", "")
    allowed = False
    if auth.startswith("Bearer "):
        tok = auth[7:]
        # Attempt admin
        try:
            teacher = verify_admin_token(tok)
            if str(teacher.get("id")) == str(tid):
                allowed = True
        except HTTPException:
            pass
        # Attempt student exam JWT
        if not allowed:
            try:
                payload = jwt.decode(
                    tok, SECRET_KEY, algorithms=["HS256"],
                    options={"verify_aud": False, "require": ["exp"]},
                )
                if str(payload.get("tid") or "") == str(tid):
                    allowed = True
            except JWTError:
                pass
    if not allowed:
        raise HTTPException(status_code=401, detail="Authentication required")

    safe_tid = Path(tid).name
    safe_file = Path(filename).name
    fpath = Path(QUESTION_IMG_DIR) / safe_tid / safe_file
    try:
        fpath.resolve().relative_to(Path(QUESTION_IMG_DIR).resolve())
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Image not found")
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    suffix = fpath.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
    media = media_map.get(suffix, "application/octet-stream")
    return FileResponse(str(fpath), media_type=media)


@app.get("/api/admin/screenshot/{roll}/{filename}")
def get_screenshot(roll: str, filename: str, request: Request):
    """Serve a screenshot image to the admin dashboard.

    Strictly scoped to the requesting teacher's screenshot folder so that
    a teacher cannot read another teacher's evidence by guessing roll
    numbers.
    """
    teacher = require_admin(request)
    # Sanitize path components to prevent directory traversal
    safe_roll = Path(roll).name
    safe_file = Path(filename).name
    tid = str(teacher["id"])
    fpath = Path(SCREENSHOTS_DIR) / tid / safe_roll / safe_file
    # Reject any path that would escape the teacher's directory
    try:
        fpath.resolve().relative_to((Path(SCREENSHOTS_DIR) / tid).resolve())
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    suffix = fpath.suffix.lower()
    media = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(fpath), media_type=media,
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/sessions")
def get_all_sessions(request: Request, exam_id: str = None):
    teacher = require_admin(request)
    tid = teacher["id"]
    try:
        # Limit to last 48h so this never scans the entire violations table
        cutoff = (now_ist() - timedelta(hours=48)).isoformat()
        evts_query = supabase.table("violations")\
            .select("session_key,violation_type,severity,created_at,details")\
            .gte("created_at", cutoff)
        if tid:
            evts_query = evts_query.eq("teacher_id", str(tid))
        evts_result = evts_query.order("created_at", desc=True).execute()
        events = evts_result.data or []

        # Pull session metadata (status + cached risk_score) in one shot so
        # the live tab can show the same risk number as the detail view.
        # Without this, the list shows just the most recent event's severity
        # which often disagrees with the aggregated risk score.
        sess_query = supabase.table("exam_sessions").select(
            "session_key,status,risk_score,exam_id")
        if tid:
            sess_query = sess_query.eq("teacher_id", str(tid))
        if exam_id:
            sess_query = sess_query.eq("exam_id", exam_id)
        sess_result = sess_query.execute()
        sess_meta = {
            r["session_key"]: r for r in (sess_result.data or [])
        }
        submitted = {
            sk for sk, m in sess_meta.items() if m.get("status") == "completed"
        }

        sessions: dict = {}
        for e in events:
            sk = e["session_key"]
            # When filtering by exam, skip events for other exams' sessions
            if exam_id and sk not in sess_meta:
                continue
            if sk not in sessions:
                meta = sess_meta.get(sk, {})
                # For live (in_progress) sessions risk_score is null in the
                # DB, so compute it on the fly. Cheap — bounded by the same
                # 48h violations slice we already fetched per session.
                cached_risk = meta.get("risk_score")
                if cached_risk is None and sk not in submitted:
                    try:
                        cached_risk = compute_risk_score(
                            sk, teacher_id=tid)["risk_score"]
                    except Exception:
                        cached_risk = None
                sessions[sk] = {
                    "session_id":    sk,
                    "last_event":    e["violation_type"],
                    "last_severity": e["severity"],
                    "last_seen":     fmt_ist(e.get("created_at", "")),
                    "details":       e.get("details"),
                    "submitted":     sk in submitted,
                    "risk_score":    cached_risk,
                    "risk_label":    _risk_label(cached_risk)
                                     if cached_risk is not None else None,
                }

        active = [s for s in sessions.values() if not s["submitted"]]
        return {"sessions": active, "all_sessions": list(sessions.values())}
    except Exception as e:
        print(f"[Sessions] ERROR: {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

def _violation_counts_by_session(session_keys: list[str]) -> dict[str, int]:
    """Bulk-fetch violations for all sessions and return a count map.

    Supabase/PostgREST encodes .in_() values as a query string; large arrays
    can exceed the URL limit (~8 KB). Chunk to 200 keys per request.
    """
    if not session_keys:
        return {}
    counts: dict[str, int] = {}
    chunk_size = 200
    for i in range(0, len(session_keys), chunk_size):
        chunk = session_keys[i : i + chunk_size]
        viol_result = supabase.table("violations")\
            .select("session_key,violation_type,severity")\
            .in_("session_key", chunk)\
            .execute()
        for v in (viol_result.data or []):
            if v["severity"] in ("high", "medium") and _is_violation(v["violation_type"]):
                counts[v["session_key"]] = counts.get(v["session_key"], 0) + 1
    return counts


def _fetch_all_results(teacher_id: str = None, exam_id: str = None) -> list[dict]:
    """Shared: fetch all exam sessions with violation counts, scoped to teacher and optionally exam."""
    query = supabase.table("exam_sessions")\
        .select("*")\
        .eq("status", "completed")
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    if exam_id:
        query = query.eq("exam_id", exam_id)
    sess_result = query.order("submitted_at", desc=True).execute()
    sessions = sess_result.data or []
    vcounts = _violation_counts_by_session([s["session_key"] for s in sessions])
    return [
        {
            "session_id":      s["session_key"],
            "roll_number":     s["roll_number"],
            "full_name":       s["full_name"],
            "email":           s.get("email", ""),
            "score":           s.get("score", 0),
            "total":           s.get("total", 0),
            "percentage":      s.get("percentage", 0.0),
            "time_taken_secs": s.get("time_taken_secs", 0),
            "submitted_at":    fmt_ist(s.get("submitted_at", "")),
            "violation_count": vcounts.get(s["session_key"], 0),
            "risk_score":      s.get("risk_score"),
            "risk_label":      _risk_label(s["risk_score"]) if s.get("risk_score") is not None else None,
        }
        for s in sessions
    ]


@app.get("/api/results")
def get_all_results(request: Request, exam_id: str = None):
    teacher = require_admin(request)
    return {"results": _fetch_all_results(teacher["id"], exam_id=exam_id)}

@app.get("/api/export-csv")
def export_csv(request: Request, exam_id: str = None):
    teacher = require_admin(request)
    results = _fetch_all_results(teacher["id"], exam_id=exam_id)
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["Timestamp","SessionID","RollNumber","FullName","Email",
                "Score","Total","Percentage","TimeTaken","Violations","RiskScore","RiskLabel"])
    for s in results:
        w.writerow([
            s["submitted_at"],
            s["session_id"],
            s["roll_number"],
            s["full_name"],
            s["email"],
            s["score"],
            s["total"],
            f"{s['percentage']}%",
            f"{s['time_taken_secs']}s",
            s["violation_count"],
            s.get("risk_score", ""),
            s.get("risk_label", ""),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=results.csv"})

@app.get("/api/export-pdf/{session_id:path}")
def export_pdf(session_id: str, request: Request):
    teacher = require_admin(request)
    tid = teacher["id"]
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        from reportlab.platypus import (SimpleDocTemplate, Table,
                                         TableStyle, Paragraph, Spacer,
                                         Image, PageBreak, KeepTogether)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        exam = _assert_session_owned(session_id, tid)

        viol_result = supabase.table("violations")\
            .select("*")\
            .eq("session_key", session_id)\
            .eq("teacher_id", str(tid))\
            .order("created_at").execute()
        raw_violations = [
            v for v in (viol_result.data or [])
            if v["severity"] in ("high", "medium") and _is_violation(v["violation_type"])
        ]

        ans_result = supabase.table("answers")\
            .select("*")\
            .eq("session_key", session_id)\
            .eq("teacher_id", str(tid))\
            .execute()
        answers = ans_result.data or []

        buf    = io.BytesIO()
        doc    = SimpleDocTemplate(buf, pagesize=A4, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story  = []

        story.append(Paragraph("AI Proctored Exam — Report", styles["Title"]))
        story.append(Spacer(1, 12))

        info = [
            ["Field",          "Value"],
            ["Full Name",      exam["full_name"]],
            ["Roll Number",    exam["roll_number"]],
            ["Email",          exam.get("email", "")],
            ["Submitted At",   fmt_ist(exam.get("submitted_at", ""))],
            ["Score",          f"{exam.get('score',0)}/{exam.get('total',0)} "
                               f"({exam.get('percentage',0)}%)"],
            ["Time Taken",     f"{exam.get('time_taken_secs',0)} seconds "
                               f"({exam.get('time_taken_secs',0)//60}m "
                               f"{exam.get('time_taken_secs',0)%60}s)"],
            ["Total Violations", str(len(raw_violations))],
        ]
        # Compute risk score for the PDF report
        risk = compute_risk_score(session_id, teacher_id=tid)
        info.append(["Behavioral Risk Score",
                      f"{risk['risk_score']}/100 — {risk['label']}"])
        t = Table(info, colWidths=[160, 310])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 10),
            ("ROWBACKGROUNDS", (0,1), (-1,-1),
             [colors.HexColor("#f0f4ff"), colors.white]),
            ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
            ("PADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        story.append(Paragraph(
            f"Violations ({len(raw_violations)} total)", styles["Heading2"]))
        story.append(Spacer(1, 8))

        CONF_MAP = {
            "face_missing": 0.95, "multiple_faces": 0.92,
            "wrong_person": 0.78, "eyes_closed": 0.88,
            "earphone_detected": 0.72, "cheat_object_detected": 0.85,
            "gaze_away": 0.70, "head_away": 0.80,
            "voice_detected": 0.75, "window_focus_lost": 0.99,
            "tab_hidden": 0.99, "lighting_issue": 0.90,
            "shortcut_blocked": 0.99, "camera_covered": 0.95,
        }

        def get_conf(vtype, details):
            det = str(details or "")
            if "confidence:" in det:
                try:
                    raw = det.split("confidence:")[1].split("|")[0].strip()
                    return raw if "%" in raw else f"{raw}%"
                except Exception:
                    pass
            if "conf:" in det:
                try:
                    raw = det.split("conf:")[1].split(" ")[0].strip()
                    val = float(raw)
                    return f"{int(val)}%" if val > 1 else f"{int(val*100)}%"
                except Exception:
                    pass
            return f"{int(CONF_MAP.get(vtype, 0.75) * 100)}%"

        def clean_details(details):
            det = str(details or "")
            return det.split("| confidence:")[0].strip()[:40] \
                   if "| confidence:" in det else det[:40]

        if raw_violations:
            total_conf_vals = []
            vd = [["#", "Type", "Severity", "Time", "Conf", "Details"]]
            for i, v in enumerate(raw_violations, 1):
                conf_str = get_conf(v["violation_type"], v.get("details"))
                try:
                    total_conf_vals.append(float(conf_str.strip("%")) / 100)
                except Exception:
                    pass
                ts_part = ""
                if v.get("created_at"):
                    # fmt_ist → "05 Apr 2026, 02:30:22 PM IST"
                    # Extract time+AM/PM after the comma.
                    _fmted = fmt_ist(v["created_at"])
                    _comma = _fmted.find(",")
                    if _comma >= 0:
                        ts_part = _fmted[_comma+1:].replace("IST","").strip()
                    else:
                        ts_part = _fmted
                vd.append([
                    str(i),
                    v["violation_type"].replace("_", " ").title()[:22],
                    v["severity"].upper(),
                    ts_part,
                    conf_str,
                    clean_details(v.get("details"))[:35],
                ])
            vt = Table(vd, colWidths=[20, 120, 55, 70, 40, 165])
            vt.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#c0392b")),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 8),
                ("ROWBACKGROUNDS", (0,1), (-1,-1),
                 [colors.HexColor("#fff5f5"), colors.white]),
                ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
                ("PADDING", (0,0), (-1,-1), 5),
                ("ALIGN",   (0,0), (0,-1), "CENTER"),
            ]))
            story.append(vt)
            story.append(Spacer(1, 8))

            if total_conf_vals:
                avg_conf  = sum(total_conf_vals) / len(total_conf_vals)
                high_conf = len([c for c in total_conf_vals if c >= 0.85])
                conf_data = [[
                    f"Overall Detection Confidence: {avg_conf:.0%}",
                    f"High Confidence Violations: {high_conf}/{len(raw_violations)}",
                    f"Reliability: {'High' if avg_conf>=0.85 else 'Medium' if avg_conf>=0.70 else 'Low'}",
                ]]
                ct = Table(conf_data, colWidths=[160, 160, 150])
                ct.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#eafaf1")),
                    ("TEXTCOLOR",  (0,0), (-1,-1), colors.HexColor("#1e8449")),
                    ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
                    ("FONTSIZE",   (0,0), (-1,-1), 8),
                    ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
                    ("PADDING", (0,0), (-1,-1), 6),
                    ("ALIGN",   (0,0), (-1,-1), "CENTER"),
                ]))
                story.append(ct)
        else:
            story.append(Paragraph("No violations recorded.", styles["Normal"]))

        # ── Visual Evidence Timeline ──────────────────────────────────────
        # For every violation that has a matching evidence screenshot, embed
        # the image directly into the PDF in chronological order so the
        # report stands on its own — the teacher doesn't have to open the
        # dashboard to see what each flag actually looked like.
        roll_for_evidence = exam.get("roll_number") or (
            session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
        )
        evidence_paths = _collect_session_screenshots(roll_for_evidence, str(tid))
        evidence_items = []
        for idx, v in enumerate(raw_violations, 1):
            match = _match_screenshot_for_violation(v, evidence_paths)
            if match is not None and match.exists():
                evidence_items.append((idx, v, match))

        if evidence_items:
            story.append(Spacer(1, 18))
            story.append(PageBreak())
            story.append(Paragraph(
                f"Visual Evidence ({len(evidence_items)} captures)",
                styles["Heading2"]))
            story.append(Paragraph(
                "Screenshots are listed in the same order as the violations table above.",
                styles["Italic"]))
            story.append(Spacer(1, 10))

            evidence_caption_style = ParagraphStyle(
                "EvidenceCaption", parent=styles["Normal"],
                fontSize=9, leading=12, spaceAfter=4,
            )

            for idx, v, img_path in evidence_items:
                ts_str = fmt_ist(v.get("created_at", ""))
                sev = v["severity"].upper()
                sev_color = "#c0392b" if v["severity"] == "high" else "#d68910"
                vtype_pretty = v["violation_type"].replace("_", " ").title()
                caption = (
                    f'<b>#{idx} — {vtype_pretty}</b>  ·  '
                    f'<font color="{sev_color}"><b>{sev}</b></font>  ·  '
                    f'{ts_str}'
                )
                detail = clean_details(v.get("details"))
                if detail:
                    caption += f'<br/><font size="8" color="#666">{detail}</font>'

                # Render image at fixed width; reportlab keeps aspect ratio
                # via kind='proportional'. KeepTogether stops a caption from
                # being orphaned at the bottom of one page while its image
                # gets pushed to the next.
                try:
                    img_flowable = Image(
                        str(img_path),
                        width=4.5 * inch, height=3.4 * inch,
                        kind="proportional",
                    )
                    story.append(KeepTogether([
                        Paragraph(caption, evidence_caption_style),
                        img_flowable,
                        Spacer(1, 14),
                    ]))
                except Exception as img_err:
                    # Skip unreadable files but keep the caption so the report
                    # is still complete and the teacher knows the frame existed.
                    story.append(Paragraph(
                        caption + f'  <font color="#999">(image unreadable: {img_err})</font>',
                        evidence_caption_style))
                    story.append(Spacer(1, 8))

        story.append(Spacer(1, 20))
        story.append(Paragraph("Answer Sheet", styles["Heading2"]))
        story.append(Spacer(1, 8))

        # Load questions for correct answers (scoped to this teacher)
        try:
            pdf_questions = _load_questions(teacher_id=tid)
            q_correct = {q["id"]: q["correct"] for q in pdf_questions}
            q_texts = {q["id"]: q.get("question", "")[:50] for q in pdf_questions}
        except Exception:
            q_correct = {}
            q_texts = {}

        if answers:
            ad = [["#", "Question", "Student", "Correct", "Result"]]
            for a in answers:
                qid = str(a["question_id"])
                correct = q_correct.get(qid, "?")
                is_right = str(a["answer"]) == correct
                ad.append([
                    f"Q{qid}",
                    q_texts.get(qid, "")[:40],
                    a["answer"],
                    correct,
                    "✓" if is_right else "✗",
                ])
            at = Table(ad, colWidths=[30, 180, 50, 50, 40])
            at.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a1a2e")),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 9),
                ("ROWBACKGROUNDS", (0,1), (-1,-1),
                 [colors.HexColor("#f8f9fa"), colors.white]),
                ("GRID",    (0,0), (-1,-1), 0.5, colors.grey),
                ("PADDING", (0,0), (-1,-1), 6),
            ]))
            story.append(at)

        story.append(Spacer(1, 20))
        story.append(Paragraph(
            f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST | "
            f"Session: {session_id[:20]}...",
            styles["Normal"]))

        doc.build(story)
        buf.seek(0)
        fname = (f"report_{exam['roll_number']}_"
                 f"{now_ist().strftime('%Y%m%d')}.pdf")
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={fname}"})

    except HTTPException:
        raise
    except Exception as e:
        print(f"[PDF] {e}")
        raise HTTPException(status_code=500, detail=f"PDF error: {e}")

# ─── SCORECARD PDF (student-facing) ─────────────────────────────
@app.get("/api/admin/scorecard-pdf/{session_id:path}")
def scorecard_pdf(session_id: str, request: Request):
    """Generate a student-facing scorecard PDF with score breakdown and per-question results."""
    teacher = require_admin(request)
    tid = teacher["id"]
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table,
                                         TableStyle, Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        exam = _assert_session_owned(session_id, tid)
        exam_id = exam.get("exam_id")

        # Load questions for this exam
        questions = _load_questions(teacher_id=tid, exam_id=exam_id)
        q_map = {str(q.get("question_id", q.get("id", ""))): q for q in questions}

        # Load student answers
        ans_rows = (supabase.table("answers").select("question_id,answer")
                    .eq("session_key", session_id)
                    .eq("teacher_id", str(tid)).execute()).data or []
        ans_map = {str(a["question_id"]): a["answer"] for a in ans_rows}

        # Load exam config for title
        config = None
        try:
            config = _load_exam_config(str(tid), exam_id=exam_id)
        except Exception:
            pass
        exam_title = (config or {}).get("title", "Exam")

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story = []

        # Header
        story.append(Paragraph(f"Scorecard — {exam_title}", styles["Title"]))
        story.append(Spacer(1, 12))

        # Student info + score summary
        score = exam.get("score", 0)
        total = exam.get("total", 0)
        pct = exam.get("percentage", 0)
        risk = compute_risk_score(session_id, teacher_id=tid)
        passed = pct >= 40

        info = [
            ["Field", "Value"],
            ["Student Name", exam.get("full_name", "")],
            ["Roll Number", exam.get("roll_number", "")],
            ["Date", fmt_ist(exam.get("submitted_at", exam.get("started_at", "")))],
            ["Score", f"{score}/{total}"],
            ["Percentage", f"{pct}%"],
            ["Result", "PASS" if passed else "FAIL"],
            ["Time Taken", f"{exam.get('time_taken_secs', 0) // 60}m {exam.get('time_taken_secs', 0) % 60}s"],
            ["Risk Level", risk["label"]],
        ]
        t = Table(info, colWidths=[140, 330])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor("#f0f4ff"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("PADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        # Per-question results table
        story.append(Paragraph("Question-wise Results", styles["Heading2"]))
        story.append(Spacer(1, 8))

        if questions:
            qd = [["#", "Question", "Your Answer", "Correct Answer", "Result"]]
            for i, q in enumerate(questions, 1):
                qid = str(q.get("question_id", q.get("id", "")))
                correct_ans = str(q.get("correct", ""))
                student_ans = ans_map.get(qid, "—")
                is_right = str(student_ans) == correct_ans
                q_text = q.get("question", "")
                if len(q_text) > 60:
                    q_text = q_text[:57] + "..."
                qd.append([
                    str(i),
                    q_text,
                    str(student_ans)[:20],
                    correct_ans[:20],
                    "\u2713" if is_right else "\u2717",
                ])
            qt = Table(qd, colWidths=[25, 230, 80, 80, 35])
            qt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.HexColor("#f8f9fa"), colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("ALIGN", (4, 1), (4, -1), "CENTER"),
            ]))
            story.append(qt)
        else:
            story.append(Paragraph("No questions available.", styles["Normal"]))

        # Footer
        story.append(Spacer(1, 20))
        story.append(Paragraph(
            f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST",
            styles["Normal"]))

        doc.build(story)
        buf.seek(0)
        roll = exam.get("roll_number", "unknown")
        fname = f"scorecard_{roll}_{now_ist().strftime('%Y%m%d')}.pdf"
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={fname}"})

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Scorecard PDF] {e}")
        raise HTTPException(status_code=500, detail=f"Scorecard PDF error: {e}")


@app.get("/api/admin/scorecard-zip")
def scorecard_zip(request: Request, exam_id: str = None):
    """Generate a ZIP of all student scorecards for an exam."""
    import zipfile
    teacher = require_admin(request)
    tid = teacher["id"]
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table,
                                         TableStyle, Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet

        # Get all completed sessions
        sess_q = supabase.table("exam_sessions")\
            .select("session_key,roll_number,full_name,score,total,percentage,time_taken_secs,risk_score,started_at,submitted_at,exam_id")\
            .eq("status", "completed").eq("teacher_id", str(tid))
        if exam_id:
            sess_q = sess_q.eq("exam_id", exam_id)
        sessions = (sess_q.execute()).data or []
        if not sessions:
            raise HTTPException(status_code=404, detail="No completed sessions found")

        # Load questions once
        eid = exam_id or (sessions[0].get("exam_id") if sessions else None)
        questions = _load_questions(teacher_id=tid, exam_id=eid)
        q_map = {str(q.get("question_id", q.get("id", ""))): q for q in questions}

        config = None
        try:
            config = _load_exam_config(str(tid), exam_id=eid)
        except Exception:
            pass
        exam_title = (config or {}).get("title", "Exam")

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for sess in sessions:
                sid = sess["session_key"]
                ans_rows = (supabase.table("answers").select("question_id,answer")
                            .eq("session_key", sid)
                            .eq("teacher_id", str(tid)).execute()).data or []
                ans_map = {str(a["question_id"]): a["answer"] for a in ans_rows}

                pdf_buf = io.BytesIO()
                doc = SimpleDocTemplate(pdf_buf, pagesize=A4, topMargin=40, bottomMargin=40)
                styles = getSampleStyleSheet()
                story = []

                story.append(Paragraph(f"Scorecard — {exam_title}", styles["Title"]))
                story.append(Spacer(1, 12))

                score = sess.get("score", 0)
                total = sess.get("total", 0)
                pct = sess.get("percentage", 0)
                passed = pct >= 40

                info = [
                    ["Field", "Value"],
                    ["Student Name", sess.get("full_name", "")],
                    ["Roll Number", sess.get("roll_number", "")],
                    ["Date", fmt_ist(sess.get("submitted_at", sess.get("started_at", "")))],
                    ["Score", f"{score}/{total}"],
                    ["Percentage", f"{pct}%"],
                    ["Result", "PASS" if passed else "FAIL"],
                    ["Time Taken", f"{sess.get('time_taken_secs', 0) // 60}m {sess.get('time_taken_secs', 0) % 60}s"],
                ]
                t = Table(info, colWidths=[140, 330])
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.HexColor("#f0f4ff"), colors.white]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("PADDING", (0, 0), (-1, -1), 8),
                ]))
                story.append(t)
                story.append(Spacer(1, 20))

                story.append(Paragraph("Question-wise Results", styles["Heading2"]))
                story.append(Spacer(1, 8))

                if questions:
                    qd = [["#", "Question", "Your Answer", "Correct", "Result"]]
                    for i, q in enumerate(questions, 1):
                        qid = str(q.get("question_id", q.get("id", "")))
                        correct_ans = str(q.get("correct", ""))
                        student_ans = ans_map.get(qid, "\u2014")
                        is_right = str(student_ans) == correct_ans
                        q_text = q.get("question", "")
                        if len(q_text) > 60:
                            q_text = q_text[:57] + "..."
                        qd.append([str(i), q_text, str(student_ans)[:20], correct_ans[:20],
                                   "\u2713" if is_right else "\u2717"])
                    qt = Table(qd, colWidths=[25, 230, 80, 80, 35])
                    qt.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                         [colors.HexColor("#f8f9fa"), colors.white]),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("PADDING", (0, 0), (-1, -1), 6),
                        ("ALIGN", (4, 1), (4, -1), "CENTER"),
                    ]))
                    story.append(qt)

                story.append(Spacer(1, 20))
                story.append(Paragraph(
                    f"Generated: {now_ist().strftime('%d %b %Y, %I:%M %p')} IST",
                    styles["Normal"]))

                doc.build(story)
                pdf_buf.seek(0)
                roll = sess.get("roll_number", "unknown")
                zf.writestr(f"scorecard_{roll}.pdf", pdf_buf.getvalue())

        zip_buf.seek(0)
        fname = f"scorecards_{exam_id or 'all'}_{now_ist().strftime('%Y%m%d')}.zip"
        return StreamingResponse(
            zip_buf, media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={fname}"})

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Scorecard ZIP] {e}")
        raise HTTPException(status_code=500, detail=f"Scorecard ZIP error: {e}")


@app.get("/api/admin-failed-sessions")
def failed_sessions(request: Request, exam_id: str = None):
    """Returns sessions with submit_failed events that never completed."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    failed = supabase.table("violations").select("session_key")\
        .eq("violation_type", "submit_failed")\
        .eq("teacher_id", tid)\
        .execute()
    failed_keys = {r["session_key"] for r in (failed.data or [])}
    # Only scan sessions that could match (status != completed) — avoids full table scan
    sub_query = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed")\
        .eq("teacher_id", tid)\
        .in_("session_key", list(failed_keys) or ["__none__"])
    if exam_id:
        sub_query = sub_query.eq("exam_id", exam_id)
    submitted = sub_query.execute()
    submitted_keys = {r["session_key"] for r in (submitted.data or [])}
    # If filtering by exam, also restrict failed_keys to that exam's sessions
    if exam_id:
        es = supabase.table("exam_sessions").select("session_key")\
            .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
        exam_skeys = {r["session_key"] for r in (es.data or [])}
        failed_keys = failed_keys & exam_skeys
    unrecovered = [k for k in failed_keys if k not in submitted_keys]
    return {"failed_sessions": unrecovered, "count": len(unrecovered)}

@app.post("/api/admin-cleanup")
def admin_cleanup(request: Request):
    """Delete the calling teacher's screenshots older than 7 days."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    deleted = 0
    cutoff  = now_ist() - timedelta(days=7)
    teacher_root = Path(SCREENSHOTS_DIR) / tid
    if not teacher_root.is_dir():
        return {"deleted": 0}
    try:
        for student_dir in teacher_root.iterdir():
            if student_dir.is_dir():
                for f in student_dir.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                        f.unlink()
                        deleted += 1
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"deleted": deleted}

# ─── CLEAR LIVE SESSIONS (double-confirm) ────────────────────────
# Teachers occasionally need to wipe in-progress sessions (crashed
# Electron clients, abandoned exams, test runs, etc.). This is
# destructive so it is gated by a two-step token flow:
#   1) POST {step:"request"} → server returns a short-lived confirm
#      token scoped to this teacher.
#   2) POST {step:"confirm", token:<t>, ack:"DELETE"} → server actually
#      deletes the rows. Both the token and the ack string must match.
# The token is kept in-process (no new table) and expires in 60 s.

_CLEAR_TOKENS: dict[str, dict] = {}  # fallback when Redis unavailable
_CLEAR_TOKEN_TTL = 60  # seconds
# A session is considered "active" (student currently taking the exam)
# if we've seen a heartbeat from it within the last _CLEAR_ACTIVE_WINDOW
# seconds. Heartbeats are sent by the Electron client every ~10s, so a
# 120s window tolerates network blips but still catches genuinely stuck
# sessions. Active sessions are NEVER wiped by clear-live-sessions.
_CLEAR_ACTIVE_WINDOW = 120


def _clear_token_issue(teacher_id: str) -> str:
    """Mint and remember a one-shot clear-live-sessions token (Redis or in-process)."""
    tok = _uuid.uuid4().hex
    payload = {"teacher_id": str(teacher_id)}
    if _cache:
        _cache.set(f"clear_token:{tok}", payload, ttl=_CLEAR_TOKEN_TTL)
    else:
        _CLEAR_TOKENS[tok] = {
            **payload,
            "expires": time.time() + _CLEAR_TOKEN_TTL,
        }
        # Opportunistically prune expired entries so the map doesn't grow.
        now = time.time()
        stale = [k for k, v in _CLEAR_TOKENS.items() if v["expires"] < now]
        for k in stale:
            _CLEAR_TOKENS.pop(k, None)
    return tok


def _session_is_active(row: dict) -> bool:
    """True if a session's last heartbeat is within the safety window.

    Missing or unparseable timestamps count as stale so that genuinely
    crashed clients (which never had a chance to send a heartbeat)
    remain eligible for cleanup.
    """
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


def _partition_live_sessions(teacher_id: str) -> tuple[list[dict], list[dict]]:
    """Return (active, stale) in-progress sessions for a teacher.

    Active = heartbeat within _CLEAR_ACTIVE_WINDOW seconds. Stale = the
    rest.

    Discovery uses TWO sources — the same approach the live tab uses:
      1. exam_sessions table (in_progress rows)
      2. violations table (last 48h) — session_keys that appear here but
         have NO matching exam_sessions row are "ghost" sessions created
         when the student record was deleted. These are always stale.

    Also picks up orphan sessions whose teacher_id is NULL — these are
    from before multi-tenant scoping was added and should be clearable.
    """
    tid = str(teacher_id)

    # ── 1. exam_sessions-based discovery ──
    result = supabase.table("exam_sessions")\
        .select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id")\
        .eq("teacher_id", tid)\
        .eq("status", "in_progress")\
        .execute()
    rows = list(result.data or [])
    seen = {r["session_key"] for r in rows}

    # Orphans with NULL/empty teacher_id
    for fetch_fn in [
        lambda: supabase.table("exam_sessions")
            .select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id")
            .is_("teacher_id", "null")
            .eq("status", "in_progress")
            .execute(),
        lambda: supabase.table("exam_sessions")
            .select("session_key,roll_number,full_name,started_at,last_heartbeat,teacher_id")
            .eq("teacher_id", "")
            .eq("status", "in_progress")
            .execute(),
    ]:
        try:
            for r in (fetch_fn().data or []):
                if r["session_key"] not in seen:
                    rows.append(r)
                    seen.add(r["session_key"])
        except Exception as e:
            print(f"[ClearLive] orphan query failed: {e}")

    # ── 2. violations-based discovery ("ghost" sessions) ──
    # The live tab finds sessions from violations in the last 48h.
    # Some of those session_keys may not exist in exam_sessions at all
    # (e.g. student row was deleted, or exam_sessions row was manually
    # removed from Supabase). These ghosts keep showing in the live tab
    # but cannot be cleared unless we discover them here too.
    try:
        cutoff = (now_ist() - timedelta(hours=48)).isoformat()
        # Query teacher-scoped violations
        viol_teacher = supabase.table("violations")\
            .select("session_key")\
            .eq("teacher_id", tid)\
            .gte("created_at", cutoff)\
            .execute()
        # Also grab orphan violations (NULL or empty teacher_id)
        viol_orphan1 = supabase.table("violations")\
            .select("session_key")\
            .is_("teacher_id", "null")\
            .gte("created_at", cutoff)\
            .execute()
        viol_orphan2 = supabase.table("violations")\
            .select("session_key")\
            .eq("teacher_id", "")\
            .gte("created_at", cutoff)\
            .execute()
        all_viol_data = (viol_teacher.data or []) + \
                        (viol_orphan1.data or []) + \
                        (viol_orphan2.data or [])
        ghost_keys: set[str] = set()
        for v in all_viol_data:
            sk = v.get("session_key")
            if sk and sk not in seen:
                ghost_keys.add(sk)
        if ghost_keys:
            print(f"[ClearLive] discovered {len(ghost_keys)} ghost session(s) "
                  f"from violations: {ghost_keys}")
        # For each ghost, create a synthetic stale row so the delete
        # loop can wipe its violations/answers.
        for sk in ghost_keys:
            rows.append({
                "session_key": sk,
                "roll_number": sk.split("_")[0] if "_" in sk else sk,
                "full_name": None,
                "started_at": None,
                "last_heartbeat": None,  # no heartbeat → always stale
                "teacher_id": tid,
                "_ghost": True,  # marker so delete logic can skip exam_sessions
            })
            seen.add(sk)
    except Exception as e:
        print(f"[ClearLive] violations ghost discovery failed: {e}")

    active, stale = [], []
    for r in rows:
        (active if _session_is_active(r) else stale).append(r)
    return active, stale


def _clear_token_consume(token: str, teacher_id: str) -> bool:
    """Validate and consume a clear-live-sessions token (Redis or in-process)."""
    if _cache:
        rec = _cache.get(f"clear_token:{token}")
        if not rec:
            return False
        if rec.get("teacher_id") != str(teacher_id):
            return False
        _cache.delete(f"clear_token:{token}")
        return True
    else:
        rec = _CLEAR_TOKENS.pop(token, None)
        if not rec:
            return False
        if rec["teacher_id"] != str(teacher_id):
            return False
        if rec["expires"] < time.time():
            return False
        return True


@app.post("/api/admin/clear-live-sessions")
def clear_live_sessions(request: Request, body: dict = Body(...)):
    """Destructive: wipe all in-progress sessions for the calling teacher.

    Two-step confirmation:
      - ``step=request`` returns a confirm token and a preview count.
      - ``step=confirm`` with ``token`` and ``ack="DELETE"`` actually
        deletes exam_sessions (status=in_progress), plus the answers,
        violations, and screenshot files that belong to those sessions.

    Never touches completed sessions or other teachers' data.
    """
    teacher = require_admin(request)
    tid = str(teacher["id"])
    step = str(body.get("step") or "").lower().strip()

    # When include_completed is true, wipe completed (submitted) sessions
    # too — a full data reset for this teacher's exam history. Still
    # protects actively-heartbeating students.
    include_completed = bool(body.get("include_completed", False))

    if step == "request":
        active, stale = _partition_live_sessions(tid)
        # Optionally include completed sessions in the wipe.
        completed_rows: list[dict] = []
        if include_completed:
            comp = supabase.table("exam_sessions")\
                .select("session_key,roll_number,full_name,started_at,submitted_at")\
                .eq("teacher_id", tid)\
                .eq("status", "completed")\
                .execute()
            completed_rows = comp.data or []
        token = _clear_token_issue(tid)
        return {
            "step":          "request",
            "token":          token,
            "expires_in":     _CLEAR_TOKEN_TTL,
            "active_window_s": _CLEAR_ACTIVE_WINDOW,
            "include_completed": include_completed,
            # Total live sessions (for display). Only `stale_count` will
            # actually be deleted on confirm — `active_count` students
            # are protected while they're still sending heartbeats.
            "count":          len(stale) + len(completed_rows),
            "stale_count":    len(stale),
            "active_count":   len(active),
            "completed_count": len(completed_rows),
            "preview":    [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name"),
                 "started_at":  r.get("started_at"),
                 "last_heartbeat": r.get("last_heartbeat")}
                for r in stale[:20]
            ],
            "active_preview": [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name"),
                 "last_heartbeat": r.get("last_heartbeat")}
                for r in active[:20]
            ],
            "completed_preview": [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name"),
                 "submitted_at": r.get("submitted_at")}
                for r in completed_rows[:20]
            ],
        }

    if step == "confirm":
        token = str(body.get("token") or "")
        ack   = str(body.get("ack") or "")
        if ack != "DELETE":
            raise HTTPException(status_code=400,
                detail="Missing or incorrect ack — expected 'DELETE'")
        if not _clear_token_consume(token, tid):
            raise HTTPException(status_code=400,
                detail="Confirmation token is invalid or expired — restart the clear flow")

        # Re-classify RIGHT NOW, not off a preview that may be stale.
        # If a student resumed or started since the request step, their
        # session will now be "active" and we MUST skip it.
        active, stale = _partition_live_sessions(tid)

        # Optionally include completed sessions.
        completed_keys: list[str] = []
        if include_completed:
            comp = supabase.table("exam_sessions")\
                .select("session_key,roll_number")\
                .eq("teacher_id", tid)\
                .eq("status", "completed")\
                .execute()
            completed_keys = [r["session_key"] for r in (comp.data or [])]

        if not stale and not completed_keys:
            skipped = [
                {"session_key": r["session_key"],
                 "roll_number": r.get("roll_number"),
                 "full_name":   r.get("full_name")}
                for r in active
            ]
            return {"step": "confirm", "cleared": 0, "sessions": 0,
                    "answers": 0, "violations": 0, "screenshots": 0,
                    "skipped_active": len(active), "skipped": skipped,
                    "note": ("No sessions to clear"
                             + (" — active students were protected"
                                if active else ""))}

        # Merge stale live + completed into one list of keys to wipe.
        session_keys = [r["session_key"] for r in stale] + completed_keys
        rolls_seen = set()
        for r in stale:
            if r.get("roll_number"):
                rolls_seen.add(r["roll_number"])
        if include_completed:
            for r in (comp.data or []):
                if r.get("roll_number"):
                    rolls_seen.add(r["roll_number"])

        skipped_active = [
            {"session_key": r["session_key"],
             "roll_number": r.get("roll_number"),
             "full_name":   r.get("full_name")}
            for r in active
        ]
        if active:
            print(f"[ClearLive] teacher={tid} protecting {len(active)} "
                  f"active session(s) from wipe")

        ans_deleted = 0
        viol_deleted = 0
        scr_deleted = 0

        # Build lookups from partition data.
        _sk_tid = {r["session_key"]: r.get("teacher_id") or ""
                   for r in stale}
        _ghost_keys = {r["session_key"] for r in stale if r.get("_ghost")}

        # Delete answers + violations for each session.
        # For ghost sessions (violations-only, no exam_sessions row)
        # we delete by session_key alone — no teacher_id filter — because
        # the violations may have been written with any/no teacher_id.
        for sk in session_keys:
            sk_tid = _sk_tid.get(sk, tid)
            is_ghost = sk in _ghost_keys
            try:
                q = supabase.table("answers").delete().eq("session_key", sk)
                if sk_tid and not is_ghost:
                    q = q.eq("teacher_id", sk_tid)
                r = q.execute()
                ans_deleted += len(r.data or [])
            except Exception as e:
                print(f"[ClearLive] answer delete failed {sk}: {e}")
            try:
                q = supabase.table("violations").delete().eq("session_key", sk)
                if sk_tid and not is_ghost:
                    q = q.eq("teacher_id", sk_tid)
                r = q.execute()
                viol_deleted += len(r.data or [])
            except Exception as e:
                print(f"[ClearLive] violation delete failed {sk}: {e}")

        # Delete the session rows. For stale sessions we scope to
        # status=in_progress; for completed ones we scope to
        # status=completed. Ghost sessions may or may not have an
        # exam_sessions row — try to delete without status filter.
        stale_key_set = {r["session_key"] for r in stale}
        sess_deleted = 0
        for sk in session_keys:
            try:
                if sk in _ghost_keys:
                    # Ghost: try deleting any exam_sessions row by key
                    # (may be a no-op if the row doesn't exist)
                    supabase.table("exam_sessions").delete()\
                        .eq("session_key", sk).execute()
                else:
                    q = supabase.table("exam_sessions").delete()\
                        .eq("session_key", sk)
                    sk_tid = _sk_tid.get(sk, tid)
                    if sk_tid:
                        q = q.eq("teacher_id", sk_tid)
                    if sk in stale_key_set:
                        q = q.eq("status", "in_progress")
                    else:
                        q = q.eq("status", "completed")
                    q.execute()
                sess_deleted += 1
            except Exception as e:
                print(f"[ClearLive] session delete failed {sk}: {e}")

        # Clean up on-disk screenshots for the affected rolls. We skip
        # any roll that has (a) an active session still running or
        # (b) a completed session in the DB that we're NOT clearing.
        active_rolls = {r.get("roll_number") for r in active if r.get("roll_number")}
        t_screens = Path(SCREENSHOTS_DIR) / tid
        if t_screens.is_dir():
            for roll in rolls_seen:
                if not roll:
                    continue
                if roll in active_rolls:
                    continue
                safe = Path(roll).name
                rdir = t_screens / safe
                if not rdir.is_dir():
                    continue
                # If we're NOT wiping completed sessions, check whether
                # the student has completed data worth preserving.
                if not include_completed:
                    comp_chk = supabase.table("exam_sessions")\
                        .select("session_key", count="exact")\
                        .eq("teacher_id", tid)\
                        .eq("roll_number", roll)\
                        .eq("status", "completed")\
                        .execute()
                    if (comp_chk.count or 0) > 0:
                        continue
                try:
                    for f in rdir.iterdir():
                        if f.is_file():
                            f.unlink()
                            scr_deleted += 1
                    rdir.rmdir()
                except Exception as e:
                    print(f"[ClearLive] screenshot cleanup failed {rdir}: {e}")

        print(f"[ClearLive] teacher={tid} sessions={sess_deleted} "
              f"(completed={len(completed_keys)}) "
              f"answers={ans_deleted} violations={viol_deleted} "
              f"screenshots={scr_deleted} "
              f"protected_active={len(active)}")
        return {
            "step":           "confirm",
            "cleared":        sess_deleted,
            "sessions":       sess_deleted,
            "answers":        ans_deleted,
            "violations":     viol_deleted,
            "screenshots":    scr_deleted,
            "completed_cleared": len(completed_keys),
            "skipped_active": len(active),
            "skipped":        skipped_active,
        }

    raise HTTPException(status_code=400,
        detail="'step' must be 'request' or 'confirm'")


@app.post("/api/admin/backfill-risk-scores")
def backfill_risk_scores(request: Request, exam_id: str = None):
    """Recompute and cache risk scores for all completed sessions."""
    teacher = require_admin(request)
    tid = teacher["id"]
    query = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed")\
        .eq("teacher_id", str(tid))
    if exam_id:
        query = query.eq("exam_id", exam_id)
    sessions = query.execute()
    count = 0
    for s in (sessions.data or []):
        risk = compute_risk_score(s["session_key"], teacher_id=tid)
        supabase.table("exam_sessions").update(
            {"risk_score": risk["risk_score"]}
        ).eq("session_key", s["session_key"])\
         .eq("teacher_id", str(tid))\
         .execute()
        count += 1
    return {"backfilled": count}

# ─── EXAM CRUD (multi-exam) ──────────────────────────────────────
@app.get("/api/admin/exams")
def list_exams(request: Request):
    """List all exams for the calling teacher."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    result = supabase.table("exam_config").select("*").eq("teacher_id", tid).execute()
    exams = result.data or []
    # Enrich with question + session counts
    out = []
    for ex in exams:
        eid = ex.get("exam_id")
        qcount = 0
        scount = 0
        try:
            qr = supabase.table("questions").select("question_id", count="exact")\
                .eq("teacher_id", tid).eq("exam_id", eid).execute()
            qcount = qr.count if qr.count is not None else len(qr.data or [])
        except Exception:
            pass
        try:
            sr = supabase.table("exam_sessions").select("session_key", count="exact")\
                .eq("teacher_id", tid).eq("exam_id", eid).execute()
            scount = sr.count if sr.count is not None else len(sr.data or [])
        except Exception:
            pass
        out.append({
            "exam_id":          eid,
            "exam_title":       ex.get("exam_title", "Exam"),
            "duration_minutes": ex.get("duration_minutes", 60),
            "starts_at":        ex.get("starts_at"),
            "ends_at":          ex.get("ends_at"),
            "access_code":      ex.get("access_code", ""),
            "question_count":   qcount,
            "session_count":    scount,
            "created_at":       ex.get("created_at", ""),
        })
    return {"exams": out}

@app.post("/api/admin/exams")
def create_exam(request: Request, body: dict = Body(...)):
    """Create a new exam for the calling teacher."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    title = str(body.get("exam_title", "New Exam")).strip() or "New Exam"
    duration = int(body.get("duration_minutes", 60))
    try:
        result = supabase.table("exam_config").insert({
            "teacher_id":       tid,
            "exam_title":       title,
            "duration_minutes": duration,
        }).execute()
    except Exception as e:
        print(f"[CreateExam] DB error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create exam: {e}")
    row = result.data[0] if result.data else {}
    return {"exam_id": row.get("exam_id"), "exam_title": title, "duration_minutes": duration}

@app.delete("/api/admin/exams/{exam_id}")
def delete_exam(exam_id: str, request: Request):
    """Delete an exam and its questions. Keeps session history."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    # Verify ownership
    check = supabase.table("exam_config").select("exam_id")\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    if not check.data:
        raise HTTPException(status_code=404, detail="Exam not found")
    # Don't allow deleting last exam
    all_exams = supabase.table("exam_config").select("exam_id")\
        .eq("teacher_id", tid).execute()
    if len(all_exams.data or []) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete your only exam")
    # Delete questions for this exam
    supabase.table("questions").delete()\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    # Delete exam config
    supabase.table("exam_config").delete()\
        .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    # Invalidate cache for deleted exam
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
        _cache.delete(f"questions:{tid}:{exam_id or '_'}")
    return {"status": "deleted", "exam_id": exam_id}

# ─── ANALYTICS ────────────────────────────────────────────────────
@app.get("/api/admin/analytics")
def get_analytics(request: Request):
    """Compute exam analytics: score distribution, question analysis, violations, risk."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    exam_id = request.query_params.get("exam_id")

    # Check Redis cache first
    cache_key = f"analytics:{tid}:{exam_id or '_'}"
    if _cache:
        cached = _cache.get(cache_key)
        if cached:
            return cached

    # ── Completed sessions ────────────────────────────────────────
    sess_q = supabase.table("exam_sessions")\
        .select("session_key,roll_number,full_name,score,total,percentage,time_taken_secs,risk_score,started_at")\
        .eq("status", "completed")
    if tid:
        sess_q = sess_q.eq("teacher_id", tid)
    if exam_id:
        sess_q = sess_q.eq("exam_id", exam_id)
    sessions = (sess_q.execute()).data or []

    if not sessions:
        empty = {"exam_overview": {"count": 0}, "score_distribution": [],
                 "question_analysis": [], "violation_summary": {},
                 "risk_distribution": {"low": 0, "medium": 0, "high": 0}}
        if _cache:
            _cache.set(cache_key, empty, ttl=60)
        return empty

    # ── Exam overview ─────────────────────────────────────────────
    count = len(sessions)
    pcts = [s.get("percentage") or 0 for s in sessions]
    times = [s.get("time_taken_secs") or 0 for s in sessions]
    scores = [s.get("score") or 0 for s in sessions]
    totals = [s.get("total") or 1 for s in sessions]
    avg_score = round(sum(scores) / count, 1)
    avg_pct = round(sum(pcts) / count, 1)
    sorted_times = sorted(t for t in times if t > 0)
    median_time = sorted_times[len(sorted_times)//2] if sorted_times else 0
    pass_count = sum(1 for p in pcts if p >= 40)
    overview = {
        "count": count,
        "avg_score": avg_score,
        "avg_total": round(sum(totals) / count, 1),
        "avg_percentage": avg_pct,
        "median_time_secs": median_time,
        "pass_rate": round(pass_count / count * 100, 1),
    }

    # ── Score distribution (10 buckets) ───────────────────────────
    buckets = [0] * 10
    for p in pcts:
        idx = min(int(p // 10), 9)
        buckets[idx] += 1
    score_dist = [{"range": f"{i*10}-{i*10+10}%", "count": buckets[i]} for i in range(10)]

    # ── Question analysis ─────────────────────────────────────────
    questions = _load_questions(tid, exam_id=exam_id)
    q_analysis = []
    if questions:
        # Load all answers for these sessions
        skeys = [s["session_key"] for s in sessions]
        all_answers = {}
        # Batch fetch answers (in chunks to avoid URL length issues)
        for i in range(0, len(skeys), 50):
            chunk = skeys[i:i+50]
            for sk in chunk:
                ans_q = supabase.table("answers").select("question_id,answer")\
                    .eq("session_key", sk)
                if tid:
                    ans_q = ans_q.eq("teacher_id", tid)
                rows = (ans_q.execute()).data or []
                all_answers[sk] = {r["question_id"]: r["answer"] for r in rows}

        # Sort sessions by score for quartile analysis
        sorted_sess = sorted(sessions, key=lambda s: s.get("percentage") or 0)
        q1_cutoff = max(1, count // 4)
        bottom_keys = set(s["session_key"] for s in sorted_sess[:q1_cutoff])
        top_keys = set(s["session_key"] for s in sorted_sess[-q1_cutoff:])

        for q in questions:
            qid = str(q.get("question_id") or q.get("id", ""))
            correct = str(q.get("correct", ""))
            total_attempted = 0
            total_correct = 0
            top_correct = 0
            top_total = 0
            bottom_correct = 0
            bottom_total = 0
            for sk, ans_map in all_answers.items():
                if qid in ans_map:
                    total_attempted += 1
                    is_correct = ans_map[qid] == correct
                    if is_correct:
                        total_correct += 1
                    if sk in top_keys:
                        top_total += 1
                        if is_correct:
                            top_correct += 1
                    if sk in bottom_keys:
                        bottom_total += 1
                        if is_correct:
                            bottom_correct += 1
            difficulty = round(total_correct / max(total_attempted, 1) * 100, 1)
            top_rate = top_correct / max(top_total, 1)
            bottom_rate = bottom_correct / max(bottom_total, 1)
            discrimination = round(top_rate - bottom_rate, 2)
            q_analysis.append({
                "question_id": qid,
                "question": (q.get("question", "")[:80] + "...") if len(q.get("question", "")) > 80 else q.get("question", ""),
                "difficulty_pct": difficulty,
                "discrimination": discrimination,
                "attempted": total_attempted,
                "correct": total_correct,
            })

    # ── Violation summary ─────────────────────────────────────────
    viol_q = supabase.table("violations")\
        .select("violation_type,severity,session_key,created_at")
    if tid:
        viol_q = viol_q.eq("teacher_id", tid)
    viols = (viol_q.execute()).data or []
    # Filter to actual violations
    scored_viols = [v for v in viols if _is_violation(v.get("violation_type", ""))
                    and v.get("severity") in ("high", "medium")]

    type_counts = {}
    for v in scored_viols:
        vt = v["violation_type"]
        type_counts[vt] = type_counts.get(vt, 0) + 1
    viol_summary = {"by_type": type_counts, "total": len(scored_viols)}

    # ── Risk distribution ─────────────────────────────────────────
    risk_dist = {"low": 0, "medium": 0, "high": 0}
    for s in sessions:
        rs = s.get("risk_score") or 0
        if rs <= 30:
            risk_dist["low"] += 1
        elif rs <= 60:
            risk_dist["medium"] += 1
        else:
            risk_dist["high"] += 1

    result = {
        "exam_overview": overview,
        "score_distribution": score_dist,
        "question_analysis": q_analysis,
        "violation_summary": viol_summary,
        "risk_distribution": risk_dist,
    }
    if _cache:
        _cache.set(cache_key, result, ttl=60)
    return result


# ─── STUDENT GROUPS ────────────────────────────────────────────────
@app.get("/api/admin/groups")
def list_groups(request: Request):
    """List all groups for the authenticated teacher."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    rows = (supabase.table("student_groups")
            .select("*").eq("teacher_id", tid)
            .order("created_at").execute()).data or []
    # Attach member counts
    for g in rows:
        members = (supabase.table("student_group_members")
                   .select("id", count="exact")
                   .eq("group_id", g["id"])
                   .eq("teacher_id", tid).execute())
        g["member_count"] = members.count if members.count is not None else len(members.data or [])
    return rows


@app.post("/api/admin/groups")
def create_group(request: Request, body: dict = Body(...)):
    """Create a new student group."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    name = (body.get("group_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="group_name is required")
    try:
        row = (supabase.table("student_groups")
               .insert({"teacher_id": tid, "group_name": name}).execute()).data
        return row[0] if row else {"ok": True}
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Group name already exists")
        raise


@app.put("/api/admin/groups/{group_id}")
def rename_group(group_id: str, request: Request, body: dict = Body(...)):
    """Rename a student group."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    name = (body.get("group_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="group_name is required")
    result = (supabase.table("student_groups")
              .update({"group_name": name})
              .eq("id", group_id).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Group not found")
    return result.data[0]


@app.delete("/api/admin/groups/{group_id}")
def delete_group(group_id: str, request: Request):
    """Delete a student group (cascades to members and exam assignments)."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    result = (supabase.table("student_groups")
              .delete().eq("id", group_id).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"ok": True}


@app.get("/api/admin/groups/{group_id}/members")
def list_group_members(group_id: str, request: Request):
    """List members of a group."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    rows = (supabase.table("student_group_members")
            .select("*").eq("group_id", group_id)
            .eq("teacher_id", tid).execute()).data or []
    return rows


@app.post("/api/admin/groups/{group_id}/members")
def add_group_members(group_id: str, request: Request, body: dict = Body(...)):
    """Add students to a group by roll numbers."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    # Verify group ownership
    grp = (supabase.table("student_groups")
           .select("id").eq("id", group_id).eq("teacher_id", tid).execute()).data
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    rolls = body.get("roll_numbers", [])
    if not rolls:
        raise HTTPException(status_code=400, detail="roll_numbers list is required")
    rows = [{"group_id": group_id, "roll_number": str(r).strip(), "teacher_id": tid}
            for r in rolls if str(r).strip()]
    if rows:
        supabase.table("student_group_members").upsert(rows).execute()
    return {"added": len(rows)}


@app.delete("/api/admin/groups/{group_id}/members")
def remove_group_members(group_id: str, request: Request, body: dict = Body(...)):
    """Remove students from a group by roll numbers."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    rolls = body.get("roll_numbers", [])
    if not rolls:
        raise HTTPException(status_code=400, detail="roll_numbers list is required")
    for r in rolls:
        supabase.table("student_group_members")\
            .delete().eq("group_id", group_id)\
            .eq("roll_number", str(r).strip())\
            .eq("teacher_id", tid).execute()
    return {"removed": len(rolls)}


@app.get("/api/admin/exams/{exam_id}/groups")
def list_exam_groups(exam_id: str, request: Request):
    """List groups assigned to an exam."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    assignments = (supabase.table("exam_group_assignments")
                   .select("group_id").eq("exam_id", exam_id)
                   .eq("teacher_id", tid).execute()).data or []
    if not assignments:
        return []
    gids = [a["group_id"] for a in assignments]
    groups = (supabase.table("student_groups")
              .select("*").in_("id", gids).execute()).data or []
    return groups


@app.post("/api/admin/exams/{exam_id}/groups")
def assign_exam_groups(exam_id: str, request: Request, body: dict = Body(...)):
    """Assign groups to an exam for access control."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    group_ids = body.get("group_ids", [])
    if not group_ids:
        raise HTTPException(status_code=400, detail="group_ids list is required")
    rows = [{"exam_id": exam_id, "group_id": gid, "teacher_id": tid} for gid in group_ids]
    supabase.table("exam_group_assignments").upsert(rows).execute()
    return {"assigned": len(rows)}


@app.delete("/api/admin/exams/{exam_id}/groups/{group_id}")
def unassign_exam_group(exam_id: str, group_id: str, request: Request):
    """Remove a group assignment from an exam."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    supabase.table("exam_group_assignments")\
        .delete().eq("exam_id", exam_id)\
        .eq("group_id", group_id)\
        .eq("teacher_id", tid).execute()
    return {"ok": True}


# ─── GROUP VALIDATION HOOK ─────────────────────────────────────────
def _check_group_access(roll_number: str, teacher_id: str, exam_id: str) -> bool:
    """Check if a student is allowed to take an exam based on group assignments.

    Returns True if:
    - No groups are assigned to the exam (all students allowed — backward compatible)
    - The student's roll_number is in a group assigned to the exam
    """
    assignments = (supabase.table("exam_group_assignments")
                   .select("group_id")
                   .eq("exam_id", exam_id)
                   .eq("teacher_id", teacher_id).execute()).data or []
    if not assignments:
        return True  # No group restrictions
    gids = [a["group_id"] for a in assignments]
    for gid in gids:
        member = (supabase.table("student_group_members")
                  .select("id")
                  .eq("group_id", gid)
                  .eq("roll_number", roll_number)
                  .eq("teacher_id", teacher_id).execute()).data
        if member:
            return True
    return False


# ─── QUESTION BANK ─────────────────────────────────────────────────
@app.get("/api/admin/question-bank")
def list_bank_questions(request: Request):
    """List all question bank entries for the teacher, optionally filtered by tag."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    tag = request.query_params.get("tag")
    q = supabase.table("question_bank").select("*").eq("teacher_id", tid)\
        .order("created_at", desc=True)
    rows = (q.execute()).data or []
    if tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    return rows


@app.post("/api/admin/question-bank")
def add_bank_questions(request: Request, body: dict = Body(...)):
    """Add one or more questions to the bank."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    questions = body.get("questions", [body] if "question" in body else [])
    if not questions:
        raise HTTPException(status_code=400, detail="No questions provided")
    rows = []
    for q in questions:
        rows.append({
            "teacher_id": tid,
            "question": q.get("question", ""),
            "question_type": q.get("question_type", "mcq_single"),
            "options": q.get("options", {}),
            "correct": str(q.get("correct", "")),
            "image_url": q.get("image_url", ""),
            "tags": q.get("tags", []),
        })
    result = supabase.table("question_bank").insert(rows).execute()
    return result.data or []


@app.put("/api/admin/question-bank/{qid}")
def update_bank_question(qid: str, request: Request, body: dict = Body(...)):
    """Update a question in the bank."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    fields = {}
    for k in ("question", "question_type", "options", "correct", "image_url", "tags"):
        if k in body:
            fields[k] = body[k]
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    fields["updated_at"] = "now()"
    result = (supabase.table("question_bank")
              .update(fields).eq("id", qid).eq("teacher_id", tid).execute())
    if not result.data:
        raise HTTPException(status_code=404, detail="Question not found")
    return result.data[0]


@app.delete("/api/admin/question-bank/{qid}")
def delete_bank_question(qid: str, request: Request):
    """Delete a question from the bank."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    supabase.table("question_bank").delete().eq("id", qid).eq("teacher_id", tid).execute()
    return {"ok": True}


@app.post("/api/admin/question-bank/import")
def import_bank_questions(request: Request, body: dict = Body(...)):
    """Bulk import questions from CSV-style JSON array.

    Expected format: list of objects with keys:
    question, type, option_A, option_B, option_C, option_D, correct, image_url, tags
    """
    teacher = require_admin(request)
    tid = str(teacher["id"])
    items = body.get("questions", [])
    if not items:
        raise HTTPException(status_code=400, detail="No questions to import")
    rows = []
    for item in items:
        options = {}
        for letter in ("A", "B", "C", "D", "E", "F"):
            val = item.get(f"option_{letter}")
            if val is not None:
                options[letter] = val
        rows.append({
            "teacher_id": tid,
            "question": item.get("question", ""),
            "question_type": item.get("type", item.get("question_type", "mcq_single")),
            "options": options,
            "correct": str(item.get("correct", "")),
            "image_url": item.get("image_url", ""),
            "tags": item.get("tags", []) if isinstance(item.get("tags"), list)
                    else [t.strip() for t in str(item.get("tags", "")).split(",") if t.strip()],
        })
    result = supabase.table("question_bank").insert(rows).execute()
    return {"imported": len(result.data or [])}


@app.get("/api/admin/question-bank/export")
def export_bank_questions(request: Request):
    """Export all bank questions as JSON."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    rows = (supabase.table("question_bank").select("*")
            .eq("teacher_id", tid)
            .order("created_at", desc=True).execute()).data or []
    # Flatten options for CSV-friendly export
    export = []
    for r in rows:
        entry = {
            "question": r["question"],
            "type": r["question_type"],
            "correct": r["correct"],
            "image_url": r.get("image_url", ""),
            "tags": ",".join(r.get("tags") or []),
        }
        opts = r.get("options") or {}
        for letter in ("A", "B", "C", "D", "E", "F"):
            if letter in opts:
                entry[f"option_{letter}"] = opts[letter]
        export.append(entry)
    return export


@app.post("/api/admin/question-bank/to-exam")
def bank_to_exam(request: Request, body: dict = Body(...)):
    """Copy bank questions into an exam's question list."""
    teacher = require_admin(request)
    tid = str(teacher["id"])
    question_ids = body.get("question_ids", [])
    exam_id = body.get("exam_id")
    if not question_ids or not exam_id:
        raise HTTPException(status_code=400, detail="question_ids and exam_id required")

    # Fetch selected bank questions
    bank_rows = (supabase.table("question_bank").select("*")
                 .eq("teacher_id", tid).in_("id", question_ids).execute()).data or []
    if not bank_rows:
        raise HTTPException(status_code=404, detail="No matching bank questions found")

    # Get current max question_id for this exam
    existing = _load_questions(teacher_id=tid, exam_id=exam_id)
    max_id = max((int(q.get("question_id", q.get("id", 0))) for q in existing), default=0)

    # Insert into questions table
    new_rows = []
    for i, bq in enumerate(bank_rows, start=max_id + 1):
        new_rows.append({
            "teacher_id": tid,
            "exam_id": exam_id,
            "question_id": i,
            "question": bq["question"],
            "question_type": bq.get("question_type", "mcq_single"),
            "options": bq.get("options", {}),
            "correct": bq["correct"],
            "image_url": bq.get("image_url", ""),
        })
    if new_rows:
        supabase.table("questions").insert(new_rows).execute()
        # Invalidate questions cache
        if _cache:
            _cache.delete(f"questions:{tid}:{exam_id or '_'}")
    return {"added": len(new_rows), "starting_id": max_id + 1}


@app.get("/api/admin/questions")
def get_admin_questions(request: Request):
    """Return all questions including correct answers (admin only)."""
    teacher = require_admin(request)
    tid = teacher["id"]
    exam_id = request.query_params.get("exam_id")
    try:
        config = _load_exam_config(str(tid) if tid else None, exam_id=exam_id)
        questions = _load_questions(str(tid) if tid else None, exam_id=exam_id)
        return {
            "exam_title": config.get("exam_title", "Exam"),
            "duration_minutes": config.get("duration_minutes", 60),
            "questions": questions,
        }
    except Exception as e:
        print(f"[Questions] ERROR: {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/answers/{session_id:path}")
def get_admin_answers(session_id: str, request: Request):
    """Return student answers merged with correct answers for the detail modal."""
    teacher = require_admin(request)
    tid = teacher["id"]
    _assert_session_owned(session_id, tid)

    # Load questions from Supabase
    questions = _load_questions(tid)

    # Fetch student answers (scoped to this teacher)
    ans_result = supabase.table("answers").select("question_id,answer")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .execute()
    ans_map = {str(r["question_id"]): str(r["answer"]) for r in (ans_result.data or [])}

    # Merge
    answer_review = []
    for q in questions:
        qid = q["id"]  # already str from _load_questions
        student_ans = ans_map.get(qid, "")
        correct_ans = q["correct"]  # already str from _load_questions
        answer_review.append({
            "question_id":   qid,
            "question":      q.get("question", ""),
            "options":       q.get("options", {}),
            "question_type": q.get("question_type", "mcq_single"),
            "image_url":     q.get("image_url", ""),
            "student_answer": student_ans,
            "correct_answer": correct_ans,
            "is_correct":     _answers_match(student_ans, correct_ans),
        })

    return {"answers": answer_review, "total": len(questions),
            "correct_count": sum(1 for a in answer_review if a["is_correct"])}

@app.post("/api/admin/questions")
def update_questions(request: Request, body: dict = Body(...)):
    """Update questions in Supabase.

    Accepts the extended schema: each question may set ``question_type``
    (``mcq_single`` | ``mcq_multi`` | ``true_false``), an optional
    ``image_url``, and for multi-correct questions a comma-separated
    ``correct`` value (e.g. ``"A,C"``).
    """
    teacher = require_admin(request)
    tid = teacher["id"]
    if "questions" not in body:
        raise HTTPException(status_code=400, detail="Missing 'questions' key")
    questions = body["questions"]
    if not isinstance(questions, list) or len(questions) == 0:
        raise HTTPException(status_code=400, detail="'questions' must be a non-empty list")

    ALLOWED_TYPES = {"mcq_single", "mcq_multi", "true_false"}
    required_fields = {"id", "question", "options", "correct"}
    normalised: list[dict] = []
    for i, q in enumerate(questions):
        missing = required_fields - set(q.keys())
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1} missing fields: {', '.join(sorted(missing))}"
            )
        qtype = str(q.get("question_type", "mcq_single")).strip().lower()
        if qtype not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: invalid question_type '{qtype}'. "
                       f"Must be one of {sorted(ALLOWED_TYPES)}"
            )

        # True/False questions always have exactly two fixed options.
        if qtype == "true_false":
            options = {"True": "True", "False": "False"}
        else:
            if not isinstance(q["options"], dict) or len(q["options"]) < 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {i+1}: 'options' must be a dict with at least 2 entries"
                )
            options = q["options"]

        # Validate correct answer(s) against the options.
        opt_keys = {str(k) for k in options.keys()}
        correct_raw = str(q["correct"] or "")
        correct_parts = [p.strip() for p in correct_raw.split(",") if p.strip()]
        if not correct_parts:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: 'correct' cannot be empty"
            )
        for cp in correct_parts:
            if cp not in opt_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {i+1}: 'correct' value '{cp}' not in options"
                )
        if qtype == "mcq_single" and len(correct_parts) != 1:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: single-choice questions need exactly 1 correct answer"
            )
        if qtype == "mcq_multi" and len(correct_parts) < 2:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: multi-choice questions need at least 2 correct answers"
            )
        if qtype == "true_false" and (len(correct_parts) != 1 or
                                       correct_parts[0] not in ("True", "False")):
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: true/false correct must be 'True' or 'False'"
            )

        normalised.append({
            "question_id":   q["id"],
            "question":      q["question"],
            "options":       options,
            "correct":       ",".join(sorted(correct_parts)),
            "question_type": qtype,
            "image_url":     str(q.get("image_url") or "") or None,
        })

    # Update exam config
    exam_id = body.get("exam_id")
    if tid and exam_id:
        supabase.table("exam_config").update({
            "exam_title": body.get("exam_title", "Exam"),
            "duration_minutes": body.get("duration_minutes", 60),
        }).eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    elif tid:
        supabase.table("exam_config").upsert({
            "teacher_id": tid,
            "exam_title": body.get("exam_title", "Exam"),
            "duration_minutes": body.get("duration_minutes", 60),
        }).execute()
    else:
        supabase.table("exam_config").upsert({
            "id": 1,
            "exam_title": body.get("exam_title", "Exam"),
            "duration_minutes": body.get("duration_minutes", 60),
        }).execute()

    # Replace questions for this exam: backup, delete, insert — rollback on failure
    q_query = supabase.table("questions").select("*")
    if tid:
        q_query = q_query.eq("teacher_id", tid)
    if exam_id:
        q_query = q_query.eq("exam_id", exam_id)
    backup = q_query.execute()
    backup_rows = backup.data or []
    try:
        del_q = supabase.table("questions").delete()
        if tid:
            del_q = del_q.eq("teacher_id", tid)
        if exam_id:
            del_q = del_q.eq("exam_id", exam_id)
        del_q.execute() if tid or exam_id else del_q.neq("question_id", -1).execute()
        extra = {}
        if tid:
            extra["teacher_id"] = tid
        if exam_id:
            extra["exam_id"] = exam_id
        records = [{**r, **extra} for r in normalised]
        try:
            supabase.table("questions").insert(records).execute()
        except Exception as e:
            # Older DBs without the new columns — strip and retry.
            msg = str(e).lower()
            if "question_type" in msg or "image_url" in msg or "column" in msg:
                print("[Questions] new columns missing on DB, retrying without")
                legacy = [
                    {k: v for k, v in r.items()
                     if k not in ("question_type", "image_url")}
                    for r in records
                ]
                supabase.table("questions").insert(legacy).execute()
            else:
                raise
    except Exception as e:
        # Rollback: re-insert backup rows if insert failed
        print(f"[Questions] Insert failed, rolling back: {e}")
        if backup_rows:
            try:
                supabase.table("questions").upsert(backup_rows).execute()
            except Exception as e2:
                print(f"[Questions] Rollback also failed: {e2}")
        raise HTTPException(status_code=500, detail="Failed to update questions — rolled back")
    # Invalidate cached config + questions for this teacher/exam
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
        _cache.delete(f"questions:{tid}:{exam_id or '_'}")
    return {"status": "updated", "count": len(questions)}

@app.get("/api/admin/access-code")
def get_access_code(request: Request):
    """Return the current exam access code (persisted in Supabase)."""
    teacher = require_admin(request)
    exam_id = request.query_params.get("exam_id")
    code = _get_access_code(teacher["id"], exam_id=exam_id)
    return {"access_code": code, "enabled": bool(code)}

@app.post("/api/admin/access-code")
def set_access_code(request: Request, body: dict = Body(...)):
    """Set or clear the exam access code (persisted in Supabase)."""
    teacher = require_admin(request)
    exam_id = body.get("exam_id")
    new_code = str(body.get("access_code", "")).strip().upper()
    _set_access_code(new_code, teacher["id"], exam_id=exam_id)
    # Invalidate cached config (access_code lives inside exam_config)
    if _cache:
        _cache.delete(f"exam_config:{teacher['id']}:{exam_id or '_'}")
    return {"access_code": new_code, "enabled": bool(new_code)}

@app.get("/api/admin/registered-count")
def registered_count(request: Request):
    """Return total number of registered students."""
    teacher = require_admin(request)
    tid = teacher["id"]
    query = supabase.table("students").select("roll_number", count="exact")
    if tid:
        query = query.eq("teacher_id", tid)
    result = query.execute()
    return {"count": result.count if result.count is not None else len(result.data or [])}

@app.get("/api/admin/exam-schedule")
def admin_get_schedule(request: Request):
    """Return current exam schedule for the admin dashboard."""
    teacher = require_admin(request)
    exam_id = request.query_params.get("exam_id")
    config = _load_exam_config(teacher["id"], exam_id=exam_id)
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "starts_at":  config.get("starts_at"),
        "ends_at":    config.get("ends_at"),
    }

@app.post("/api/admin/exam-schedule")
def admin_set_schedule(request: Request, body: dict = Body(...)):
    """Set or clear exam start/end times (persisted in Supabase)."""
    teacher = require_admin(request)
    tid = teacher["id"]
    exam_id = body.get("exam_id")
    if tid and exam_id:
        update = {}
        if "starts_at" in body:
            update["starts_at"] = body["starts_at"]
        if "ends_at" in body:
            update["ends_at"] = body["ends_at"]
        if update:
            supabase.table("exam_config").update(update)\
                .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    else:
        update = {"teacher_id": tid} if tid else {"id": 1}
        if "starts_at" in body:
            update["starts_at"] = body["starts_at"]
        if "ends_at" in body:
            update["ends_at"] = body["ends_at"]
        supabase.table("exam_config").upsert(update).execute()
    # Invalidate cached config
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
    return {
        "status":    "updated",
        "starts_at": body.get("starts_at"),
        "ends_at":   body.get("ends_at"),
    }


@app.get("/api/admin/shuffle-config")
def admin_get_shuffle(request: Request):
    """Return current per-student shuffle toggles."""
    teacher = require_admin(request)
    exam_id = request.query_params.get("exam_id")
    config = _load_exam_config(teacher["id"], exam_id=exam_id)
    sq, so = _get_shuffle_flags(config)
    return {"shuffle_questions": sq, "shuffle_options": so}


@app.post("/api/admin/shuffle-config")
def admin_set_shuffle(request: Request, body: dict = Body(...)):
    """Toggle per-student question / option shuffling."""
    teacher = require_admin(request)
    tid = teacher["id"]
    exam_id = body.get("exam_id")
    fields: dict = {}
    if "shuffle_questions" in body:
        fields["shuffle_questions"] = bool(body["shuffle_questions"])
    if "shuffle_options" in body:
        fields["shuffle_options"] = bool(body["shuffle_options"])
    if not fields:
        raise HTTPException(status_code=400, detail="No shuffle fields provided")
    if tid and exam_id:
        supabase.table("exam_config").update(fields)\
            .eq("teacher_id", tid).eq("exam_id", exam_id).execute()
    else:
        update = {**({"teacher_id": tid} if tid else {"id": 1}), **fields}
        supabase.table("exam_config").upsert(update).execute()
    # Invalidate cached config
    if _cache:
        _cache.delete(f"exam_config:{tid}:{exam_id or '_'}")
    return {
        "status": "updated",
        "shuffle_questions": fields.get("shuffle_questions"),
        "shuffle_options":   fields.get("shuffle_options"),
    }

@app.post("/api/admin-submit/{session_id}")
def admin_submit(session_id: str, request: Request):
    """Force-submit a session that failed to submit properly."""
    teacher = require_admin(request)
    tid = teacher["id"]

    existing_session = _assert_session_owned(session_id, tid)
    if existing_session.get("status") == "completed":
        return {"status": "already_submitted"}

    ev_result = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .eq("teacher_id", str(tid))\
        .order("created_at").execute()
    events = ev_result.data or []
    if not events:
        raise HTTPException(status_code=404, detail="Session not found")

    roll_number = existing_session.get("roll_number") or session_id.rsplit("_", 1)[0]
    full_name   = existing_session.get("full_name") or "Unknown"
    email       = existing_session.get("email") or "unknown@exam.com"
    for e in events:
        if e["violation_type"] == "enrollment_started" and e.get("details"):
            try:
                parts = e["details"].replace("Student: ", "")
                if "(" in parts:
                    full_name   = parts.split("(")[0].strip()
                    roll_number = parts.split("(")[1].replace(")", "").strip()
            except Exception:
                pass

    try:
        s_result = supabase.table("students").select("*")\
            .eq("roll_number", roll_number)\
            .eq("teacher_id", str(tid))\
            .execute()
        if s_result.data:
            full_name = s_result.data[0].get("full_name", full_name)
            email     = s_result.data[0].get("email", email)
    except Exception:
        pass

    answers_map: dict = {}
    for e in events:
        if e["violation_type"] == "answer_selected" and e.get("details"):
            try:
                # format: "q:1|a:B|correct:C" — split on | then on first : only
                parts = {}
                for segment in e["details"].split("|"):
                    k, _, v = segment.partition(":")
                    parts[k.strip()] = v.strip()
                if "q" in parts and "a" in parts:
                    answers_map[parts["q"]] = parts["a"]
            except Exception:
                pass

    existing_eid = existing_session.get("exam_id")
    score, total = _recalculate_score(session_id, answers_map, tid, exam_id=existing_eid)

    pct        = round((score / max(total, 1)) * 100, 1)
    now        = now_ist()
    violations = [e for e in events
                  if e["severity"] in ("high", "medium")
                  and _is_violation(e["violation_type"])]

    risk = compute_risk_score(session_id, teacher_id=tid)

    sess_row = {
        "session_key":     session_id,
        "teacher_id":      str(tid),
        "roll_number":     roll_number,
        "full_name":       full_name,
        "email":           email,
        "score":           score,
        "total":           total,
        "percentage":      pct,
        "time_taken_secs": 0,
        "status":          "completed",
        "submitted_at":    now.isoformat(),
        "risk_score":      risk["risk_score"],
    }
    if existing_eid:
        sess_row["exam_id"] = existing_eid
    supabase.table("exam_sessions").upsert(sess_row).execute()

    if answers_map:
        ans_rows = []
        for qid, ans in answers_map.items():
            row = {"session_key": session_id, "teacher_id": str(tid),
                   "question_id": qid, "answer": ans}
            if existing_eid:
                row["exam_id"] = existing_eid
            ans_rows.append(row)
        supabase.table("answers").upsert(ans_rows).execute()

    supabase.table("violations").insert({
        "session_key":    session_id,
        "teacher_id":     str(tid),
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        f"Admin force-submitted | Violations:{len(violations)} | Risk:{risk['risk_score']}/100",
    }).execute()

    print(f"[ForceSubmit] {session_id} score:{score}/{total} risk:{risk['risk_score']}/100")
    return {
        "status":          "force_submitted",
        "session_id":      session_id,
        "score":           score,
        "total":           total,
        "violation_count": len(violations),
        "risk_score":      risk["risk_score"],
        "risk_label":      risk["label"],
    }


# ─── DEMO REQUEST (public, rate-limited) ────────────────────────

class DemoRequest(BaseModel):
    name: str
    email: str
    institution: str
    role: str
    message: str = ""

@app.post("/api/demo-request")
@limiter.limit("5/hour")
async def submit_demo_request(req: DemoRequest, request: Request):
    """Store a demo request from the marketing site."""
    if not req.name.strip() or not req.email.strip() or not req.institution.strip():
        raise HTTPException(status_code=400, detail="Name, email, and institution are required")

    row = {
        "name":        req.name.strip(),
        "email":       req.email.strip().lower(),
        "institution": req.institution.strip(),
        "role":        req.role.strip(),
        "message":     req.message.strip(),
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("demo_requests").insert(row).execute()
    except Exception as e:
        print(f"[DemoRequest] Failed to store: {e}")
        raise HTTPException(status_code=500, detail="Failed to store request")

    print(f"[DemoRequest] {req.name} <{req.email}> from {req.institution}")
    return {"status": "ok", "message": "Demo request received"}


@app.get("/api/admin/demo-requests")
async def list_demo_requests(request: Request):
    """List all demo requests — restricted to the configured super-admin."""
    teacher = require_admin(request)
    if not SUPER_ADMIN_EMAIL or teacher.get("email", "").lower() != SUPER_ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = supabase.table("demo_requests").select("*").order("created_at", desc=True).execute()
    return {"requests": result.data, "count": len(result.data)}


# ─── IN-EXAM CHAT (WebSockets) ───────────────────────────────────
#
# Ephemeral real-time chat between students (in the Electron exam window)
# and teachers (on the dashboard).  One thread per exam session, scoped to
# the owning teacher.  Nothing is persisted — the server holds only the
# last 50 messages per thread so a late-joining dashboard can backfill.

CHAT_MAX_TEXT_LEN = 2000
CHAT_HISTORY_LIMIT = 50


class ChatHub:
    """In-memory hub for student↔teacher chat sockets.

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
            # If a previous student socket exists for this session, close it
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
            # Ensure the thread exists so teachers can see presence
            self._thread(teacher_id, session_id)

        # Notify teachers of presence change
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

        # Echo back to student
        student_ws = self.student_conns.get(session_id)
        if student_ws is not None:
            await self._safe_send(student_ws, msg)

        # Fan out to every teacher socket on this tenant
        await self._fanout_teachers(meta["teacher_id"], msg)
        return msg

    # ── teacher side ───────────────────────────────────────────
    async def register_teacher(self, teacher_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self.teacher_conns.setdefault(teacher_id, set()).add(ws)

        # Send the current roster + per-session history so the dashboard can
        # hydrate without another round trip.
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
        # Ownership check: target session must belong to this teacher
        meta = self.student_meta.get(session_id)
        if not meta or meta.get("teacher_id") != teacher_id:
            return None
        msg = self._make_msg(sender="teacher", session_id=session_id, text=text)
        self._thread(teacher_id, session_id).append(msg)

        # Deliver to the specific student if online
        student_ws = self.student_conns.get(session_id)
        if student_ws is not None:
            await self._safe_send(student_ws, msg)

        # Mirror to every teacher tab so they stay in sync
        await self._fanout_teachers(teacher_id, msg)
        return msg

    async def teacher_broadcast(self, teacher_id: str, text: str) -> int:
        """Send a broadcast to every online student under this teacher.

        Returns the number of students the broadcast was delivered to.
        """
        msg = self._make_msg(
            sender="teacher", session_id="*", text=text, kind="broadcast")
        delivered = 0
        # Snapshot the target sockets to avoid holding the lock during sends
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
            # Append to the per-session thread so it shows on the teacher view
            self._thread(teacher_id, sid).append(per_msg)
            if await self._safe_send(ws, per_msg):
                delivered += 1

        # Inform every teacher tab that a broadcast was fired
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
            "online": online,
            "roll": meta.get("roll", ""),
            "name": meta.get("name", ""),
            "ts": datetime.now(timezone.utc).isoformat(),
        })


chat_hub = ChatHub()


def _chat_verify_session_owned(session_id: str, teacher_id: str,
                               roll: str) -> Optional[dict]:
    """Confirm that the given session belongs to the teacher and student.

    Returns the exam_sessions row on success, or None if it does not exist
    or does not match.  Completed sessions are rejected so closed threads
    cannot be reopened.
    """
    try:
        result = supabase.table("exam_sessions") \
            .select("*") \
            .eq("session_key", session_id) \
            .execute()
    except Exception:
        return None
    if not result.data:
        return None
    row = result.data[0]
    if str(row.get("teacher_id") or "") != str(teacher_id or ""):
        return None
    if (row.get("roll_number") or "").upper() != (roll or "").upper():
        return None
    if row.get("status") == "completed":
        return None
    return row


@app.websocket("/ws/chat/student")
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

        # Hydrate with any existing thread history
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


@app.websocket("/ws/chat/teacher")
async def ws_chat_teacher(ws: WebSocket):
    """Teacher end of the chat.  Query param: token."""
    await ws.accept()
    teacher_id: Optional[str] = None
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

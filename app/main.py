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

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from jose import jwt, JWTError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import supabase
from logger import get_logger

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
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    except Exception:
        return str(ts_str)

SECRET_KEY       = os.environ["SUPABASE_JWT_SECRET"]
ADMIN_PASSWORD   = os.getenv("ADMIN_PASSWORD", "ProctorAdmin2026!")  # legacy fallback
SCREENSHOTS_DIR  = os.getenv("SCREENSHOTS_DIR", "/app/screenshots")
DOWNLOAD_MAC_ARM = os.getenv("DOWNLOAD_MAC_ARM", "")
DOWNLOAD_MAC_X64 = os.getenv("DOWNLOAD_MAC_X64", "")
DOWNLOAD_WIN     = os.getenv("DOWNLOAD_WIN", "")
TOKEN_TTL_HOURS  = 10

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

# ─── JWT ──────────────────────────────────────────────────────────
def create_token(roll_number: str, teacher_id: str = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "roll": roll_number,
        "exp":  now + timedelta(hours=TOKEN_TTL_HOURS),
        "iat":  now,
    }
    if teacher_id:
        payload["tid"] = teacher_id
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

# ─── Teacher lookup cache (avoids DB hit per request) ────────────
_teacher_cache = {}  # supabase_uid -> teacher dict
_teacher_cache_ttl = {}  # supabase_uid -> expiry timestamp

def _get_teacher_by_uid(uid: str) -> dict | None:
    """Look up teacher by Supabase Auth UID, with 5-min cache."""
    now = time.time()
    if uid in _teacher_cache and _teacher_cache_ttl.get(uid, 0) > now:
        return _teacher_cache[uid]
    result = supabase.table("teachers").select("*").eq("supabase_uid", uid).execute()
    if not result.data:
        return None
    teacher = result.data[0]
    _teacher_cache[uid] = teacher
    _teacher_cache_ttl[uid] = now + 300  # 5 min
    return teacher

def require_admin(request: Request) -> dict:
    """Teacher JWT auth — returns teacher dict with 'id' key.
    Falls back to legacy password auth during migration."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            token = auth[7:]
            header = jwt.get_unverified_header(token)
            alg = header.get("alg", "HS256")
            payload = jwt.decode(token, SECRET_KEY, algorithms=[alg],
                                 options={"verify_aud": False, "verify_exp": True})
        except JWTError as e:
            print(f"[Auth] JWT decode failed: {e}")
            raise HTTPException(status_code=401, detail=f"Invalid or expired token: {e}")
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=403, detail="Not a teacher token")
        teacher = _get_teacher_by_uid(sub)
        if not teacher:
            raise HTTPException(status_code=403, detail="Teacher account not found")
        return teacher

    # Legacy fallback: password header (remove after full migration)
    pwd = request.headers.get("X-Admin-Password", "")
    if pwd and pwd == ADMIN_PASSWORD:
        legacy = supabase.table("teachers").select("*").limit(1).execute()
        if legacy.data:
            return legacy.data[0]
        # No teacher exists yet — return a stub so old dashboard doesn't break
        return {"id": None, "email": "legacy", "full_name": "Admin"}

    raise HTTPException(status_code=401, detail="Authentication required")

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
        "access_token": auth_resp.session.access_token,
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
    """Refresh an expired teacher access token."""
    try:
        auth_resp = supabase.auth.refresh_session(body.refresh_token)
        return {
            "access_token": auth_resp.session.access_token,
            "refresh_token": auth_resp.session.refresh_token,
        }
    except Exception as e:
        print(f"[TeacherRefresh] Error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


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
    "face_enrolled", "heartbeat",
}

def _is_violation(vtype: str) -> bool:
    return vtype not in _NON_VIOLATION_TYPES

def _load_questions(teacher_id: str = None) -> list[dict]:
    """Load questions from Supabase, optionally scoped to a teacher."""
    query = supabase.table("questions")\
        .select("question_id,question,options,correct")
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    result = query.order("question_id").execute()
    return [
        {"id": str(q["question_id"]), "question": q["question"],
         "options": q["options"], "correct": str(q["correct"])}
        for q in (result.data or [])
    ]

def _load_exam_config(teacher_id: str = None) -> dict:
    """Load exam config from Supabase, scoped to teacher if provided."""
    query = supabase.table("exam_config")\
        .select("exam_title,duration_minutes,access_code,starts_at,ends_at")
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
    else:
        query = query.eq("id", 1)  # legacy singleton fallback
    result = query.execute()
    if result.data:
        return result.data[0]
    return {"exam_title": "Exam", "duration_minutes": 60, "access_code": "",
            "starts_at": None, "ends_at": None}

def _get_access_code(teacher_id: str = None) -> str:
    """Load the current exam access code from Supabase."""
    try:
        config = _load_exam_config(teacher_id)
        code = config.get("access_code", "")
        if code:
            return str(code).strip().upper()
    except Exception:
        pass
    return os.getenv("EXAM_ACCESS_CODE", "").strip().upper()

def _set_access_code(code: str, teacher_id: str = None):
    """Persist access code to Supabase exam_config table."""
    if teacher_id:
        supabase.table("exam_config").upsert({
            "teacher_id": teacher_id,
            "access_code": code,
        }).execute()
    else:
        supabase.table("exam_config").upsert({
            "id": 1,
            "access_code": code,
        }).execute()


def _recalculate_score(session_id: str, payload_answers: dict, teacher_id: str = None) -> tuple[int, int]:
    """Calculate score server-side from Supabase questions + saved answers."""
    try:
        questions = _load_questions(teacher_id)
        total = len(questions)
        # Merge DB answers with payload answers (payload takes precedence)
        saved = supabase.table("answers").select("question_id,answer")\
            .eq("session_key", session_id).execute()
        ans_map = {str(r["question_id"]): str(r["answer"]) for r in (saved.data or [])}
        for qid, ans in payload_answers.items():
            ans_map[str(qid)] = str(ans)
        score = sum(1 for q in questions
                    if ans_map.get(q["id"]) == q["correct"])
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


def compute_risk_score(session_id: str) -> dict:
    """Compute behavioral risk score (0–100) from the violations table.

    Returns dict with risk_score, label, duration_minutes, and per-type
    breakdown.  Safe to call for in-progress or completed sessions.
    """
    viol_result = supabase.table("violations")\
        .select("violation_type,severity,created_at")\
        .eq("session_key", session_id)\
        .order("created_at")\
        .execute()
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

    # ── Count occurrences per type ───────────────────────────────────
    counts: dict[str, int] = {}
    severities: dict[str, str] = {}
    for r in scored:
        vtype = r["violation_type"]
        counts[vtype] = counts.get(vtype, 0) + 1
        severities.setdefault(vtype, r["severity"])

    # ── Compute per-type contribution with log saturation ────────────
    breakdown: dict[str, dict] = {}
    raw_sum = 0.0
    log_sat = math.log(1 + _SATURATION_K)

    for vtype, n in counts.items():
        weight = VIOLATION_WEIGHTS.get(vtype)
        if weight is None:
            # Unknown type — use severity-based default
            weight = (_DEFAULT_WEIGHT_HIGH
                      if severities.get(vtype) == "high"
                      else _DEFAULT_WEIGHT_MED)
        contribution = weight * min(1.0, math.log(1 + n) / log_sat)
        raw_sum += contribution
        breakdown[vtype] = {"count": n, "contribution": round(contribution, 1)}

    # ── Duration normalization ───────────────────────────────────────
    duration_factor = _BASELINE_DURATION_MINS / max(duration_mins, 5.0)
    normalized = raw_sum * duration_factor
    risk_score = min(100, round(normalized))

    return {
        "risk_score":       risk_score,
        "label":            _risk_label(risk_score),
        "duration_minutes": round(duration_mins, 1),
        "breakdown":        breakdown,
    }


# ─── PUBLIC ENDPOINTS ─────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "AI Proctor Server running"}

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

    # Check uniqueness (PK constraint is the real guard against races)
    existing = supabase.table("students")\
        .select("roll_number").eq("roll_number", roll).execute()
    if existing.data:
        raise HTTPException(
            status_code=409,
            detail="This roll number is already registered. If this is a mistake, contact your examiner.")

    row = {
        "roll_number": roll,
        "full_name":   name,
        "email":       email,
        "phone":       phone,
    }
    if body.teacher_id:
        row["teacher_id"] = body.teacher_id
    try:
        supabase.table("students").insert(row).execute()
    except Exception as e:
        # Catch PK violation from race condition
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="This roll number is already registered.")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    return {"status": "registered", "roll_number": roll, "full_name": name}

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
    # Look up student first to get their teacher_id for config loading
    pre_check = supabase.table("students")\
        .select("teacher_id")\
        .eq("roll_number", body.roll_number.strip().upper())\
        .execute()
    pre_tid = pre_check.data[0].get("teacher_id") if pre_check.data else None

    # Check exam time window using the student's teacher config
    config = _load_exam_config(pre_tid)
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
    current_code = _get_access_code(student_tid)
    if current_code:
        if not body.access_code or body.access_code.strip().upper() != current_code:
            raise HTTPException(
                status_code=403,
                detail="Invalid exam access code. Ask your examiner for the correct code.")
    completed = supabase.table("exam_sessions").select("session_key")\
        .eq("roll_number", student["roll_number"])\
        .eq("status", "completed")\
        .execute()
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
        "token":       create_token(student["roll_number"], student_tid),
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

def _shuffle_for_student(questions: list[dict], roll: str) -> list[dict]:
    """Deterministically shuffle question order and option order per student.

    Uses roll number as seed so the same student always gets the same order
    (important for resume). Question IDs are preserved — only presentation
    order changes. Scoring is unaffected since it matches by question_id.
    """
    seed = int(hashlib.sha256(roll.encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)

    shuffled = list(questions)
    rng.shuffle(shuffled)

    # Shuffle option order within each question
    result = []
    for q in shuffled:
        opts = q.get("options", {})
        if opts:
            keys = list(opts.keys())
            rng.shuffle(keys)
            q = {**q, "options": {k: opts[k] for k in keys}}
        result.append(q)
    return result


# ─── STUDENT ENDPOINTS (require JWT) ─────────────────────────────
@app.get("/api/questions")
def get_questions(request: Request):
    claims = require_auth(request)
    tid = claims.get("tid")
    questions = _load_questions(tid)
    if not questions:
        raise HTTPException(status_code=404, detail="Questions not found")
    config = _load_exam_config(tid)

    # Randomize question and option order per student (deterministic by roll)
    roll = claims.get("roll", "")
    shuffled = _shuffle_for_student(questions, roll)

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
    result = supabase.table("exam_sessions").select("*")\
        .eq("roll_number", roll_number)\
        .eq("status", "in_progress")\
        .order("started_at", desc=True)\
        .limit(1).execute()
    if not result.data:
        return {"exists": False}
    session = result.data[0]
    answers = supabase.table("answers").select("*")\
        .eq("session_key", session["session_key"]).execute()
    return {
        "exists":      True,
        "session_key": session["session_key"],
        "answer_count": len(answers.data or []),
        "answers":     {str(r["question_id"]): r["answer"] for r in (answers.data or [])},
        "started_at":  session.get("started_at"),
    }

def _check_session_ownership(claims: dict, session_id: str):
    """Raise 403 if the JWT roll doesn't match the session's roll prefix."""
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")


@app.post("/event")
@limiter.limit("120/minute")
def log_event(event: EventIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    tid = claims.get("tid")
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
        supabase.table("exam_sessions").upsert(row).execute()

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
    supabase.table("violations").insert(viol_row).execute()
    return {"status": "logged"}

@app.post("/heartbeat")
def heartbeat(event: EventIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    tid = claims.get("tid")
    row = {
        "session_key":    event.session_id,
        "roll_number":    event.session_id.rsplit("_", 1)[0],
        "last_heartbeat": now_ist().isoformat(),
        "status":         "in_progress",
    }
    if tid:
        row["teacher_id"] = tid
    supabase.table("exam_sessions").upsert(row).execute()
    return {"ok": True}

@app.post("/api/save-answer")
def save_answer(body: AnswerIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    tid = claims.get("tid")
    row = {
        "session_key":  body.session_id,
        "question_id":  body.question_id,
        "answer":       body.answer,
    }
    if tid:
        row["teacher_id"] = tid
    supabase.table("answers").upsert(row).execute()
    return {"status": "saved"}

@app.post("/api/save-answers-bulk")
def save_answers_bulk(body: BulkAnswerIn, request: Request):
    """Periodic bulk save of all answers — safety net for failed individual saves."""
    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    if not body.answers:
        return {"status": "empty", "saved": 0}
    tid = claims.get("tid")
    records = [
        {"session_key": body.session_id, "question_id": str(qid), "answer": str(ans),
         **({"teacher_id": tid} if tid else {})}
        for qid, ans in body.answers.items()
    ]
    supabase.table("answers").upsert(records).execute()
    return {"status": "saved", "saved": len(records)}

@app.post("/api/submit-exam")
@limiter.limit("10/minute")
def submit_exam(result: ResultIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, result.session_id)
    tid = claims.get("tid")
    now = now_ist()

    # Server-side scoring — never trust client score
    server_score, server_total = _recalculate_score(result.session_id, result.answers, teacher_id=tid)
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
    supabase.table("exam_sessions").upsert(session_row).execute()

    # Check time exceeded
    try:
        config = _load_exam_config(teacher_id=tid)
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
            supabase.table("violations").insert(viol).execute()
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
    supabase.table("violations").insert(submit_viol).execute()

    # Cache behavioral risk score
    risk = compute_risk_score(result.session_id)
    supabase.table("exam_sessions").update(
        {"risk_score": risk["risk_score"]}
    ).eq("session_key", result.session_id).execute()

    get_logger(result.session_id).info(
        f"[SUBMIT] {result.roll_number} score:{server_score}/{server_total} "
        f"risk:{risk['risk_score']}/100")
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
        fpath = os.path.join(student_dir, f"frame_{ts}.jpg")
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(data.frame))
    except Exception as e:
        print(f"[Frame] {e}")
    return {"status": "received"}

@app.get("/events/{session_id}")
def get_events(session_id: str, request: Request):
    claims = require_auth(request)
    # Ownership check: session_id is "{roll_number}_..." — student may only
    # read their own events. Admins use the admin endpoints instead.
    session_roll = session_id.rsplit("_", 1)[0].upper()
    if claims.get("roll", "").upper() != session_roll:
        raise HTTPException(status_code=403, detail="Access denied")
    result = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .order("created_at")\
        .execute()
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

# ─── ADMIN ENDPOINTS (require X-Admin-Password header) ───────────

@app.get("/api/risk-score/{session_id:path}")
def get_risk_score(session_id: str, request: Request):
    """Compute behavioral risk score for any session (live or completed)."""
    teacher = require_admin(request)
    result = compute_risk_score(session_id)
    result["session_id"] = session_id
    return result


@app.get("/api/admin/timeline/{session_id:path}")
def get_timeline(session_id: str, request: Request):
    """Full forensics timeline: every event + screenshot paths for a session."""
    teacher = require_admin(request)
    viol_result = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .order("created_at")\
        .execute()
    events = viol_result.data or []

    # Gather screenshots for this student
    roll = session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
    tid = teacher["id"]
    # Check teacher-scoped path first, fall back to legacy flat path
    student_dir = Path(SCREENSHOTS_DIR) / tid / roll if tid else None
    if not student_dir or not student_dir.is_dir():
        student_dir = Path(SCREENSHOTS_DIR) / roll
    screenshots: dict[str, str] = {}   # filename -> relative URL
    if student_dir.is_dir():
        for f in sorted(student_dir.iterdir()):
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                screenshots[f.name] = f"/api/admin/screenshot/{roll}/{f.name}"

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
        # Match screenshot by timestamp proximity
        if e.get("created_at"):
            try:
                evt_ts = datetime.fromisoformat(
                    str(e["created_at"]).replace("Z", "+00:00")
                ).astimezone(IST)
                evt_key = evt_ts.strftime("%Y%m%d_%H%M%S")
                # Look for evidence screenshots saved around this time
                for fname in screenshots:
                    if evt_key in fname:
                        entry["screenshot"] = screenshots[fname]
                        break
            except Exception:
                pass
        timeline.append(entry)

    # Session metadata
    sess_result = supabase.table("exam_sessions")\
        .select("*").eq("session_key", session_id).execute()
    session_info = sess_result.data[0] if sess_result.data else {}

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
        "screenshots": list(screenshots.values()),
    }


@app.get("/api/admin/screenshot/{roll}/{filename}")
def get_screenshot(roll: str, filename: str, request: Request):
    """Serve a screenshot image to the admin dashboard."""
    teacher = require_admin(request)
    # Sanitize path components to prevent directory traversal
    safe_roll = Path(roll).name
    safe_file = Path(filename).name
    tid = teacher["id"]
    # Check teacher-scoped path first, fall back to legacy
    fpath = Path(SCREENSHOTS_DIR) / tid / safe_roll / safe_file if tid else None
    if not fpath or not fpath.exists():
        fpath = Path(SCREENSHOTS_DIR) / safe_roll / safe_file
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    suffix = fpath.suffix.lower()
    media = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(fpath), media_type=media)


@app.get("/sessions")
def get_all_sessions(request: Request):
    teacher = require_admin(request)
    tid = teacher["id"]
    # Limit to last 48h so this never scans the entire violations table
    cutoff = (now_ist() - timedelta(hours=48)).isoformat()
    evts_query = supabase.table("violations")\
        .select("session_key,violation_type,severity,created_at,details")\
        .gte("created_at", cutoff)
    if tid:
        evts_query = evts_query.eq("teacher_id", tid)
    evts_result = evts_query.order("created_at", desc=True).execute()
    events = evts_result.data or []

    sub_query = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed")
    if tid:
        sub_query = sub_query.eq("teacher_id", tid)
    sub_result = sub_query.execute()
    submitted  = {r["session_key"] for r in (sub_result.data or [])}

    sessions: dict = {}
    for e in events:
        sk = e["session_key"]
        if sk not in sessions:
            sessions[sk] = {
                "session_id":    sk,
                "last_event":    e["violation_type"],
                "last_severity": e["severity"],
                "last_seen":     fmt_ist(e.get("created_at", "")),
                "details":       e.get("details"),
                "submitted":     sk in submitted,
            }

    active = [s for s in sessions.values() if not s["submitted"]]
    return {"sessions": active, "all_sessions": list(sessions.values())}

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


def _fetch_all_results(teacher_id: str = None) -> list[dict]:
    """Shared: fetch all exam sessions with violation counts, scoped to teacher."""
    query = supabase.table("exam_sessions")\
        .select("*")\
        .eq("status", "completed")
    if teacher_id:
        query = query.eq("teacher_id", teacher_id)
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
def get_all_results(request: Request):
    teacher = require_admin(request)
    return {"results": _fetch_all_results(teacher["id"])}

@app.get("/api/export-csv")
def export_csv(request: Request):
    teacher = require_admin(request)
    results = _fetch_all_results(teacher["id"])
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
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table,
                                         TableStyle, Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet

        sess_result = supabase.table("exam_sessions")\
            .select("*").eq("session_key", session_id).execute()
        if not sess_result.data:
            raise HTTPException(status_code=404, detail="Result not found")
        exam = sess_result.data[0]

        viol_result = supabase.table("violations")\
            .select("*").eq("session_key", session_id).order("created_at").execute()
        raw_violations = [
            v for v in (viol_result.data or [])
            if v["severity"] in ("high", "medium") and _is_violation(v["violation_type"])
        ]

        ans_result = supabase.table("answers")\
            .select("*").eq("session_key", session_id).execute()
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
        risk = compute_risk_score(session_id)
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
                    ts_parts = fmt_ist(v["created_at"]).split(" ")
                    ts_part  = ts_parts[1].replace(" IST", "") if len(ts_parts) > 1 else ""
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

        story.append(Spacer(1, 20))
        story.append(Paragraph("Answer Sheet", styles["Heading2"]))
        story.append(Spacer(1, 8))

        # Load questions for correct answers
        try:
            pdf_questions = _load_questions()
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
            f"Generated: {now_ist().strftime('%Y-%m-%d %H:%M:%S')} | "
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

@app.get("/api/admin-failed-sessions")
def failed_sessions(request: Request):
    """Returns sessions with submit_failed events that never completed."""
    teacher = require_admin(request)
    tid = teacher["id"]
    failed_query = supabase.table("violations").select("session_key")\
        .eq("violation_type", "submit_failed")
    if tid:
        failed_query = failed_query.eq("teacher_id", tid)
    failed = failed_query.execute()
    failed_keys = {r["session_key"] for r in (failed.data or [])}
    # Only scan sessions that could match (status != completed) — avoids full table scan
    submitted = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed")\
        .in_("session_key", list(failed_keys) or ["__none__"])\
        .execute()
    submitted_keys = {r["session_key"] for r in (submitted.data or [])}
    unrecovered = [k for k in failed_keys if k not in submitted_keys]
    return {"failed_sessions": unrecovered, "count": len(unrecovered)}

@app.post("/api/admin-cleanup")
def admin_cleanup(request: Request):
    """Delete screenshots older than 7 days."""
    teacher = require_admin(request)
    deleted = 0
    cutoff  = now_ist() - timedelta(days=7)
    try:
        for student_dir in Path(SCREENSHOTS_DIR).iterdir():
            if student_dir.is_dir():
                for f in student_dir.iterdir():
                    if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                        f.unlink()
                        deleted += 1
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"deleted": deleted}

@app.post("/api/admin/backfill-risk-scores")
def backfill_risk_scores(request: Request):
    """Recompute and cache risk scores for all completed sessions."""
    teacher = require_admin(request)
    tid = teacher["id"]
    query = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed")
    if tid:
        query = query.eq("teacher_id", tid)
    sessions = query.execute()
    count = 0
    for s in (sessions.data or []):
        risk = compute_risk_score(s["session_key"])
        supabase.table("exam_sessions").update(
            {"risk_score": risk["risk_score"]}
        ).eq("session_key", s["session_key"]).execute()
        count += 1
    return {"backfilled": count}

@app.get("/api/admin/questions")
def get_admin_questions(request: Request):
    """Return all questions including correct answers (admin only)."""
    teacher = require_admin(request)
    tid = teacher["id"]
    config = _load_exam_config(tid)
    questions = _load_questions(tid)
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "duration_minutes": config.get("duration_minutes", 60),
        "questions": questions,
    }

@app.get("/api/admin/answers/{session_id:path}")
def get_admin_answers(session_id: str, request: Request):
    """Return student answers merged with correct answers for the detail modal."""
    teacher = require_admin(request)
    tid = teacher["id"]

    # Load questions from Supabase
    questions = _load_questions(tid)

    # Fetch student answers
    ans_result = supabase.table("answers").select("question_id,answer")\
        .eq("session_key", session_id).execute()
    ans_map = {str(r["question_id"]): str(r["answer"]) for r in (ans_result.data or [])}

    # Merge
    answer_review = []
    for q in questions:
        qid = q["id"]  # already str from _load_questions
        student_ans = ans_map.get(qid, "")
        correct_ans = q["correct"]  # already str from _load_questions
        answer_review.append({
            "question_id": qid,
            "question": q.get("question", ""),
            "options": q.get("options", {}),
            "student_answer": student_ans,
            "correct_answer": correct_ans,
            "is_correct": student_ans == correct_ans,
        })

    return {"answers": answer_review, "total": len(questions),
            "correct_count": sum(1 for a in answer_review if a["is_correct"])}

@app.post("/api/admin/questions")
def update_questions(request: Request, body: dict):
    """Update questions in Supabase."""
    teacher = require_admin(request)
    tid = teacher["id"]
    if "questions" not in body:
        raise HTTPException(status_code=400, detail="Missing 'questions' key")
    questions = body["questions"]
    if not isinstance(questions, list) or len(questions) == 0:
        raise HTTPException(status_code=400, detail="'questions' must be a non-empty list")
    required_fields = {"id", "question", "options", "correct"}
    for i, q in enumerate(questions):
        missing = required_fields - set(q.keys())
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1} missing fields: {', '.join(sorted(missing))}"
            )
        if not isinstance(q["options"], dict) or len(q["options"]) < 2:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: 'options' must be a dict with at least 2 entries"
            )
        if str(q["correct"]) not in {str(k) for k in q["options"].keys()}:
            raise HTTPException(
                status_code=400,
                detail=f"Question {i+1}: 'correct' value '{q['correct']}' not in options"
            )
    # Update exam config
    if tid:
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
    # Replace teacher's questions: backup, delete, insert — rollback on failure
    q_query = supabase.table("questions").select("*")
    if tid:
        q_query = q_query.eq("teacher_id", tid)
    backup = q_query.execute()
    backup_rows = backup.data or []
    try:
        del_query = supabase.table("questions")
        if tid:
            del_query.delete().eq("teacher_id", tid).execute()
        else:
            del_query.delete().neq("question_id", -1).execute()
        records = [
            {"question_id": q["id"], "question": q["question"],
             "options": q["options"], "correct": str(q["correct"]),
             **({"teacher_id": tid} if tid else {})}
            for q in questions
        ]
        supabase.table("questions").insert(records).execute()
    except Exception as e:
        # Rollback: re-insert backup rows if insert failed
        print(f"[Questions] Insert failed, rolling back: {e}")
        if backup_rows:
            try:
                supabase.table("questions").upsert(backup_rows).execute()
            except Exception as e2:
                print(f"[Questions] Rollback also failed: {e2}")
        raise HTTPException(status_code=500, detail="Failed to update questions — rolled back")
    return {"status": "updated", "count": len(questions)}

@app.get("/api/admin/access-code")
def get_access_code(request: Request):
    """Return the current exam access code (persisted in Supabase)."""
    teacher = require_admin(request)
    code = _get_access_code(teacher["id"])
    return {"access_code": code, "enabled": bool(code)}

@app.post("/api/admin/access-code")
def set_access_code(request: Request, body: dict):
    """Set or clear the exam access code (persisted in Supabase)."""
    teacher = require_admin(request)
    new_code = str(body.get("access_code", "")).strip().upper()
    _set_access_code(new_code, teacher["id"])
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
    config = _load_exam_config(teacher["id"])
    return {
        "exam_title": config.get("exam_title", "Exam"),
        "starts_at":  config.get("starts_at"),
        "ends_at":    config.get("ends_at"),
    }

@app.post("/api/admin/exam-schedule")
def admin_set_schedule(request: Request, body: dict):
    """Set or clear exam start/end times (persisted in Supabase)."""
    teacher = require_admin(request)
    tid = teacher["id"]
    if tid:
        update = {"teacher_id": tid}
    else:
        update = {"id": 1}
    if "starts_at" in body:
        update["starts_at"] = body["starts_at"]
    if "ends_at" in body:
        update["ends_at"] = body["ends_at"]
    supabase.table("exam_config").upsert(update).execute()
    return {
        "status":    "updated",
        "starts_at": body.get("starts_at"),
        "ends_at":   body.get("ends_at"),
    }

@app.post("/api/admin-submit/{session_id}")
def admin_submit(session_id: str, request: Request):
    """Force-submit a session that failed to submit properly."""
    teacher = require_admin(request)

    existing = supabase.table("exam_sessions")\
        .select("session_key,status").eq("session_key", session_id).execute()
    if existing.data and existing.data[0].get("status") == "completed":
        return {"status": "already_submitted"}

    ev_result = supabase.table("violations")\
        .select("*").eq("session_key", session_id).order("created_at").execute()
    events = ev_result.data or []
    if not events:
        raise HTTPException(status_code=404, detail="Session not found")

    roll_number = session_id.rsplit("_", 1)[0]
    full_name   = "Unknown"
    email       = "unknown@exam.com"
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
            .eq("roll_number", roll_number).execute()
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

    score, total = _recalculate_score(session_id, answers_map, teacher["id"])

    pct        = round((score / max(total, 1)) * 100, 1)
    now        = now_ist()
    violations = [e for e in events
                  if e["severity"] in ("high", "medium")
                  and _is_violation(e["violation_type"])]

    risk = compute_risk_score(session_id)

    supabase.table("exam_sessions").upsert({
        "session_key":     session_id,
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
    }).execute()

    if answers_map:
        supabase.table("answers").upsert([
            {"session_key": session_id, "question_id": qid, "answer": ans}
            for qid, ans in answers_map.items()
        ]).execute()

    supabase.table("violations").insert({
        "session_key":    session_id,
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
    """List all demo requests (global — not teacher-scoped)."""
    teacher = require_admin(request)
    result = supabase.table("demo_requests").select("*").order("created_at", desc=True).execute()
    return {"requests": result.data, "count": len(result.data)}

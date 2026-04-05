import os
import csv
import io
import json
import base64
import math
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
ADMIN_PASSWORD   = os.getenv("ADMIN_PASSWORD", "ProctorAdmin2026!")
QUESTIONS_FILE   = "/app/questions.json"
SCREENSHOTS_DIR  = os.getenv("SCREENSHOTS_DIR", "/app/screenshots")
DOWNLOAD_MAC_ARM = os.getenv("DOWNLOAD_MAC_ARM", "")
DOWNLOAD_MAC_X64 = os.getenv("DOWNLOAD_MAC_X64", "")
DOWNLOAD_WIN     = os.getenv("DOWNLOAD_WIN", "")
TOKEN_TTL_HOURS  = 10

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

# ─── JWT ──────────────────────────────────────────────────────────
def create_token(roll_number: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "roll": roll_number,
        "exp":  now + timedelta(hours=TOKEN_TTL_HOURS),
        "iat":  now,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def require_auth(request: Request) -> dict:
    """Student JWT auth — required for all exam endpoints."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        return jwt.decode(auth[7:], SECRET_KEY, algorithms=["HS256"])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

def require_admin(request: Request):
    """Admin password auth — required for all admin endpoints."""
    pwd = request.headers.get("X-Admin-Password", "")
    if pwd != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Admin access required")

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

# ─── ADMIN DASHBOARD ─────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"

@app.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard():
    html_path = STATIC_DIR / "dashboard.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return HTMLResponse(html_path.read_text())


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

class ValidateIn(BaseModel):
    roll_number: str

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

def _recalculate_score(session_id: str, payload_answers: dict) -> tuple[int, int]:
    """Calculate score server-side from questions.json + saved answers."""
    try:
        with open(QUESTIONS_FILE) as f:
            qdata = json.load(f)
        questions = qdata.get("questions", [])
        total = len(questions)
        # Merge DB answers with payload answers (payload takes precedence)
        saved = supabase.table("answers").select("question_id,answer")\
            .eq("session_key", session_id).execute()
        ans_map = {r["question_id"]: r["answer"] for r in (saved.data or [])}
        for qid, ans in payload_answers.items():
            ans_map[str(qid)] = str(ans)
        score = sum(1 for q in questions
                    if ans_map.get(str(q["id"])) == str(q.get("correct", "")))
        return score, total
    except Exception as e:
        print(f"[Score] Recalculation failed: {e}")
        return -1, -1  # signals fallback to client score

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

@app.post("/api/validate-student")
@limiter.limit("10/minute")
def validate_student(request: Request, body: ValidateIn):
    result = supabase.table("students")\
        .select("*")\
        .eq("roll_number", body.roll_number.strip().upper())\
        .execute()
    if not result.data:
        raise HTTPException(
            status_code=404,
            detail="Roll number not found. Please complete registration first.")
    student = result.data[0]
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
        "token":       create_token(student["roll_number"]),
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

# ─── STUDENT ENDPOINTS (require JWT) ─────────────────────────────
@app.get("/api/questions")
def get_questions(request: Request):
    require_auth(request)
    if not os.path.exists(QUESTIONS_FILE):
        raise HTTPException(status_code=404, detail="Questions not found")
    with open(QUESTIONS_FILE) as f:
        data = json.load(f)
    # Strip correct answers — students must never see them
    safe_questions = []
    for q in data.get("questions", []):
        sq = {k: v for k, v in q.items() if k != "correct"}
        safe_questions.append(sq)
    return {
        "exam_title": data.get("exam_title", "Exam"),
        "duration_minutes": data.get("duration_minutes"),
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
        "answers":     {r["question_id"]: r["answer"] for r in (answers.data or [])},
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
    get_logger(event.session_id).info(
        f"[{event.severity.upper()}] {event.event_type} | {event.details}")

    # When exam starts, create in-progress session record
    if event.event_type == "exam_started":
        supabase.table("exam_sessions").upsert({
            "session_key": event.session_id,
            "roll_number": event.session_id.rsplit("_", 1)[0],
            "status":      "in_progress",
            "started_at":  now_ist().isoformat(),
        }).execute()

    # Alert on submission failure
    if event.event_type == "submit_failed":
        print(f"[ALERT] SUBMIT FAILED for session {event.session_id} "
              f"— use /api/admin-submit/{event.session_id} to recover")

    supabase.table("violations").insert({
        "session_key":    event.session_id,
        "violation_type": event.event_type,
        "severity":       event.severity,
        "details":        event.details,
    }).execute()
    return {"status": "logged"}

@app.post("/heartbeat")
def heartbeat(event: EventIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, event.session_id)
    supabase.table("exam_sessions").upsert({
        "session_key":    event.session_id,
        "roll_number":    event.session_id.rsplit("_", 1)[0],
        "last_heartbeat": now_ist().isoformat(),
        "status":         "in_progress",
    }).execute()
    return {"ok": True}

@app.post("/api/save-answer")
def save_answer(body: AnswerIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, body.session_id)
    supabase.table("answers").upsert({
        "session_key":  body.session_id,
        "question_id":  body.question_id,
        "answer":       body.answer,
    }).execute()
    return {"status": "saved"}

@app.post("/api/submit-exam")
@limiter.limit("10/minute")
def submit_exam(result: ResultIn, request: Request):
    claims = require_auth(request)
    _check_session_ownership(claims, result.session_id)
    now = now_ist()

    # Server-side scoring — never trust client score
    server_score, server_total = _recalculate_score(result.session_id, result.answers)
    if server_score == -1:
        # questions.json failed to load — fallback to client score
        server_score = result.score
        server_total = result.total

    pct = round((server_score / max(server_total, 1)) * 100, 1)

    supabase.table("exam_sessions").upsert({
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
    }).execute()

    # Check time exceeded
    try:
        with open(QUESTIONS_FILE) as f:
            qdata = json.load(f)
        allowed_secs = qdata.get("duration_minutes", 60) * 60
        if result.time_taken_secs > allowed_secs + 120:  # 2 min grace
            supabase.table("violations").insert({
                "session_key":    result.session_id,
                "violation_type": "time_exceeded",
                "severity":       "high",
                "details":        f"Submitted {result.time_taken_secs - allowed_secs}s past time limit",
            }).execute()
    except Exception as e:
        print(f"[TimeCheck] {e}")

    # Log submission
    supabase.table("violations").insert({
        "session_key":    result.session_id,
        "violation_type": "exam_submitted",
        "severity":       "low",
        "details":        f"Score:{server_score}/{server_total} ({pct}%)",
    }).execute()

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
        roll = data.session_id.rsplit("_", 1)[0] if "_" in data.session_id \
               else data.session_id[:20]
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
    require_admin(request)
    result = compute_risk_score(session_id)
    result["session_id"] = session_id
    return result


@app.get("/api/admin/timeline/{session_id:path}")
def get_timeline(session_id: str, request: Request):
    """Full forensics timeline: every event + screenshot paths for a session."""
    require_admin(request)
    viol_result = supabase.table("violations")\
        .select("*")\
        .eq("session_key", session_id)\
        .order("created_at")\
        .execute()
    events = viol_result.data or []

    # Gather screenshots for this student
    roll = session_id.rsplit("_", 1)[0] if "_" in session_id else session_id[:20]
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
    require_admin(request)
    # Sanitize path components to prevent directory traversal
    safe_roll = Path(roll).name
    safe_file = Path(filename).name
    fpath = Path(SCREENSHOTS_DIR) / safe_roll / safe_file
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    suffix = fpath.suffix.lower()
    media = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(fpath), media_type=media)


@app.get("/sessions")
def get_all_sessions(request: Request):
    require_admin(request)
    # Limit to last 48h so this never scans the entire violations table
    cutoff = (now_ist() - timedelta(hours=48)).isoformat()
    evts_result = supabase.table("violations")\
        .select("session_key,violation_type,severity,created_at,details")\
        .gte("created_at", cutoff)\
        .order("created_at", desc=True)\
        .execute()
    events = evts_result.data or []

    sub_result = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed").execute()
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


def _fetch_all_results() -> list[dict]:
    """Shared: fetch all exam sessions with violation counts."""
    sess_result = supabase.table("exam_sessions")\
        .select("*")\
        .eq("status", "completed")\
        .order("submitted_at", desc=True)\
        .execute()
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
    require_admin(request)
    return {"results": _fetch_all_results()}

@app.get("/api/export-csv")
def export_csv(request: Request):
    require_admin(request)
    results = _fetch_all_results()
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
    require_admin(request)
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
        if answers:
            ad = [["Question", "Answer"]]
            for a in answers:
                ad.append([f"Question {a['question_id']}", a["answer"]])
            at = Table(ad, colWidths=[200, 270])
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
    require_admin(request)
    failed = supabase.table("violations").select("session_key")\
        .eq("violation_type", "submit_failed").execute()
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
    require_admin(request)
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
    require_admin(request)
    sessions = supabase.table("exam_sessions").select("session_key")\
        .eq("status", "completed").execute()
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
    """Return full questions.json including correct answers (admin only)."""
    require_admin(request)
    if not os.path.exists(QUESTIONS_FILE):
        return {"exam_title": "", "duration_minutes": 60, "questions": []}
    with open(QUESTIONS_FILE) as f:
        return json.load(f)

@app.post("/api/admin/questions")
def update_questions(request: Request, body: dict):
    """Update questions.json without rebuilding Docker image."""
    require_admin(request)
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
    with open(QUESTIONS_FILE, "w") as f:
        json.dump(body, f, indent=2)
    return {"status": "updated", "count": len(questions)}

@app.post("/api/admin-submit/{session_id}")
def admin_submit(session_id: str, request: Request):
    """Force-submit a session that failed to submit properly."""
    require_admin(request)

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

    score, total = _recalculate_score(session_id, answers_map)
    if score == -1:
        score = 0
        total = 0

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

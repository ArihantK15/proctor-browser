"""Constants used across the application."""

import os
from pathlib import Path
from datetime import timezone, timedelta

# ─── Timezone ─────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

# ─── Paths ────────────────────────────────────────────────────────
SCREENSHOTS_DIR = os.getenv("SCREENSHOTS_DIR", "/app/screenshots")
QUESTION_IMG_DIR = os.getenv("QUESTION_IMG_DIR", "/app/question_images")
STATIC_DIR = Path(__file__).parent / "static"

# ─── Secrets & Auth ───────────────────────────────────────────────
SECRET_KEY = os.environ["SUPABASE_JWT_SECRET"]
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "").strip().lower()
TOKEN_TTL_HOURS = 10
ADMIN_TOKEN_TTL_HOURS = 12
STUDENT_AUTH_TTL_HOURS = 12
_LOADTEST_SECRET = os.environ.get("LOADTEST_SECRET", "")

# ─── CORS ─────────────────────────────────────────────────────────
_CORS_RAW = os.getenv("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOWED_ORIGINS = [o.strip() for o in _CORS_RAW.split(",") if o.strip()] if _CORS_RAW else [
    "http://localhost",
    "http://localhost:5173",
    "https://app.procta.net",
]

# ─── Releases ─────────────────────────────────────────────────────
RELEASE_REPO = os.getenv("RELEASE_REPO", "ArihantK15/proctor-browser")
RELEASE_TTL_SEC = int(os.getenv("RELEASE_TTL_SEC", "600"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
DOWNLOAD_MAC_ARM = os.getenv("DOWNLOAD_MAC_ARM", "")
DOWNLOAD_MAC_X64 = os.getenv("DOWNLOAD_MAC_X64", "")
DOWNLOAD_WIN = os.getenv("DOWNLOAD_WIN", "")

# ─── Cache limits ─────────────────────────────────────────────────
_TEACHER_CACHE_MAX = 5000
_STUDENT_ACCT_CACHE_MAX = 5000

# ─── Exam / Practice ──────────────────────────────────────────────
PRACTICE_PREFIX = "PRACTICE_"

# ─── Calibration thresholds ───────────────────────────────────────
_CAL_TIGHT_GAZE = 0.10
_CAL_LOOSE_GAZE = 0.50
_CAL_TIGHT_HEAD = 8.0
_CAL_LOOSE_HEAD = 30.0

# ─── Plan / billing ──────────────────────────────────────────────
PLANS = {
    "starter":    {"name": "Starter",  "students": 30,  "price_inr": 149,  "desc": "For small classes & tutorials"},
    "growth":     {"name": "Growth",   "students": 150, "price_inr": 999,  "desc": "For departments & mid-size programs"},
    "pro":        {"name": "Pro",      "students": 500, "price_inr": 2499, "desc": "For large universities & institutions"},
    "enterprise": {"name": "Enterprise", "students": 999999, "price_inr": 0, "desc": "Custom pricing — contact sales"},
}
TRIAL_DAYS = 7

# ─── Risk scoring ─────────────────────────────────────────────────
_SATURATION_K = 5
_BASELINE_DURATION_MINS = 30
_DEFAULT_WEIGHT_HIGH = 10
_DEFAULT_WEIGHT_MED = 5

# ─── Critical event types ─────────────────────────────────────────
_CRITICAL_TYPES = frozenset({
    "phone_consulting", "collaboration", "answer_memo",
    "note_reading", "wrong_person", "calibration_abort",
    "cheat_object_detected", "vm_detected",
    "remote_desktop_detected",
})

# ─── Invites ──────────────────────────────────────────────────────
INVITE_DAILY_CAP = int(os.environ.get("INVITE_DAILY_CAP", "500"))
INVITE_URL_TTL = 600  # 10 minutes

# ─── Reminders ────────────────────────────────────────────────────
REMINDER_POLL_SECONDS = int(os.environ.get("REMINDER_POLL_SECONDS", "300"))
REMINDER_1H_WINDOW_MIN = 10
REMINDER_24H_WINDOW_MIN = 20

# ─── Clear sessions ───────────────────────────────────────────────
_CLEAR_TOKEN_TTL = 60
_CLEAR_ACTIVE_WINDOW = 120

# ─── Chat ─────────────────────────────────────────────────────────
CHAT_MAX_TEXT_LEN = 2000
CHAT_HISTORY_LIMIT = 50

# ─── Pending verifications ────────────────────────────────────────
_PENDING_VERIFICATION_LIMIT = 50
_PENDING_VERIFICATION_TTL = 300

"""Constants used across the application."""

import os
import sys
from pathlib import Path
from datetime import timezone, timedelta


def _required_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(
            f"[boot] FATAL: env var {name} is required.\n"
            "  Local dev: add to .env at repo root.\n"
            "  Prod: set in docker-compose.yml or /etc/procta/secrets.env.",
            file=sys.stderr,
        )
        sys.exit(1)
    return v


# ─── Timezone ─────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

# ─── Paths ────────────────────────────────────────────────────────
SCREENSHOTS_DIR = os.getenv("SCREENSHOTS_DIR", "/app/screenshots")
QUESTION_IMG_DIR = os.getenv("QUESTION_IMG_DIR", "/app/question_images")
STATIC_DIR = Path(__file__).parent / "static"

# ─── Secrets & Auth ───────────────────────────────────────────────
SECRET_KEY = _required_env("SUPABASE_JWT_SECRET")
if len(SECRET_KEY) < 32:
    import logging as _logging
    _logging.warning("[boot] SUPABASE_JWT_SECRET is %d chars (recommended >= 32). HMAC keys shorter than 32 bytes for HS256 weaken signing.", len(SECRET_KEY))
    # H43: Require >= 32 chars in production to ensure HMAC security
    if os.environ.get("ENV", "").lower() in ("production", "prod", ""):
        raise ValueError(
            f"SUPABASE_JWT_SECRET is only {len(SECRET_KEY)} chars. "
            "For production, use at least 32 characters."
        )

# ─── Per-purpose JWT signing keys + rotation (C6) ────────────────
#
# New tokens are signed with explicit per-purpose env keys when provided:
#   JWT_ADMIN_SIGNING_KEY, JWT_STUDENT_SIGNING_KEY, ...
#
# Rotation is additive: put the old key in the matching *_PREVIOUS env var
# (comma-separated) before changing the primary. Decoders try primary first,
# then previous keys, then the legacy derived key so tokens minted before this
# migration naturally expire without forcing every user out at deploy time.
#
# SUPABASE_JWT_SECRET remains as the migration root/legacy key, but it is no
# longer accepted directly by default. Set JWT_ACCEPT_LEGACY_MASTER_TOKENS=true
# for a short emergency bridge only if you know old master-signed tokens are
# still in circulation.
import hmac as _hmac
import hashlib as _hashlib
def _derive_key(purpose: str) -> str:
    return _hmac.new(SECRET_KEY.encode(), purpose.encode(), _hashlib.sha256).hexdigest()

def _split_keys(raw: str | None) -> list[str]:
    return [k.strip() for k in (raw or "").split(",") if k.strip()]

# Env gate to retire SECRET_KEY-derived legacy keys after rotation.
# Default is fail-open only outside production so local/dev tokens keep
# verifying during upgrades. In production, absence of the env var is
# fail-closed: set JWT_ACCEPT_DERIVED_LEGACY_KEYS=true only for a
# deliberate, time-boxed migration window.
# Once you've rotated every accepted JWT off the derived key (verify
# by tailing logs for "Invalid token" spikes during a soak period),
# set JWT_ACCEPT_DERIVED_LEGACY_KEYS=false on the KVM and redeploy.
# Without this gate, leaking SUPABASE_JWT_SECRET would let an
# attacker derive any per-purpose key indefinitely (audit P1.5).
_APP_ENV = os.environ.get("APP_ENV") or os.environ.get("ENV") or "development"
_JWT_LEGACY_DEFAULT = "false" if _APP_ENV.strip().lower() in {"production", "prod"} else "true"
_ACCEPT_LEGACY_DERIVED = os.environ.get(
    "JWT_ACCEPT_DERIVED_LEGACY_KEYS", _JWT_LEGACY_DEFAULT,
).strip().lower() in {"1", "true", "yes", "on"}


def _key_ring(env_name: str, purpose: str) -> list[str]:
    explicit = os.environ.get(env_name, "").strip()
    previous = _split_keys(os.environ.get(f"{env_name}_PREVIOUS"))
    legacy = _derive_key(purpose)
    keys: list[str] = []
    if explicit:
        keys.append(explicit)
    elif _ACCEPT_LEGACY_DERIVED:
        # No explicit key set AND legacy still accepted → use the
        # derived key as the active signing key (back-compat).
        keys.append(legacy)
    else:
        # No explicit key set AND legacy gated off — this purpose is
        # broken. Surface loudly at boot rather than silently signing
        # tokens nobody can verify. Misconfiguration, not an attack
        # vector, but we don't want it to limp along.
        raise RuntimeError(
            f"JWT key ring for purpose '{purpose}' has no explicit "
            f"{env_name} set and JWT_ACCEPT_DERIVED_LEGACY_KEYS=false. "
            f"Set {env_name} to a 32+ char secret before disabling "
            f"the derived-legacy fallback."
        )
    keys.extend(previous)
    if _ACCEPT_LEGACY_DERIVED:
        if legacy not in keys:
            keys.append(legacy)
        if SECRET_KEY not in keys:
            keys.append(SECRET_KEY)
    return keys

ADMIN_SIGNING_KEYS = _key_ring("JWT_ADMIN_SIGNING_KEY", "procta.admin")
STUDENT_SIGNING_KEYS = _key_ring("JWT_STUDENT_SIGNING_KEY", "procta.student")
REFRESH_SIGNING_KEYS = _key_ring("JWT_REFRESH_SIGNING_KEY", "procta.refresh")
RESET_SIGNING_KEYS = _key_ring("JWT_RESET_SIGNING_KEY", "procta.password_reset")
EMAIL_VERIFY_SIGNING_KEYS = _key_ring("JWT_EMAIL_VERIFY_SIGNING_KEY", "procta.email_verify")
REAUTH_SIGNING_KEYS = _key_ring("JWT_REAUTH_SIGNING_KEY", "procta.reauth")
EXAM_TOKEN_SIGNING_KEYS = _key_ring("JWT_EXAM_TOKEN_SIGNING_KEY", "procta.exam_token")
ROOM_CAM_SIGNING_KEYS = _key_ring("JWT_ROOM_CAM_SIGNING_KEY", "procta.room_cam")
UNSUBSCRIBE_SIGNING_KEYS = _key_ring("JWT_UNSUBSCRIBE_SIGNING_KEY", "procta.unsubscribe")

ADMIN_SIGNING_KEY = ADMIN_SIGNING_KEYS[0]
STUDENT_SIGNING_KEY = STUDENT_SIGNING_KEYS[0]
REFRESH_SIGNING_KEY = REFRESH_SIGNING_KEYS[0]
RESET_SIGNING_KEY = RESET_SIGNING_KEYS[0]
EMAIL_VERIFY_SIGNING_KEY = EMAIL_VERIFY_SIGNING_KEYS[0]
REAUTH_SIGNING_KEY = REAUTH_SIGNING_KEYS[0]
EXAM_TOKEN_SIGNING_KEY = EXAM_TOKEN_SIGNING_KEYS[0]
ROOM_CAM_SIGNING_KEY = ROOM_CAM_SIGNING_KEYS[0]
UNSUBSCRIBE_SIGNING_KEY = UNSUBSCRIBE_SIGNING_KEYS[0]

JWT_ACCEPT_LEGACY_MASTER_TOKENS = os.environ.get("JWT_ACCEPT_LEGACY_MASTER_TOKENS", "").strip().lower() in {
    "1", "true", "yes", "on"
}

# Ordered list for middleware that must decode an unknown token type
ALL_SIGNING_KEYS = [
    *ADMIN_SIGNING_KEYS,
    *STUDENT_SIGNING_KEYS,
    *EXAM_TOKEN_SIGNING_KEYS,
    *ROOM_CAM_SIGNING_KEYS,
]
if JWT_ACCEPT_LEGACY_MASTER_TOKENS and SECRET_KEY not in ALL_SIGNING_KEYS:
    ALL_SIGNING_KEYS.append(SECRET_KEY)

SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "").strip().lower()

# ─── Teacher-reported issues ─────────────────────────────────────
ISSUE_CATEGORIES = {"bug", "question", "feature", "session-issue", "other"}
ISSUE_SEVERITIES = {"low", "normal", "high"}
ISSUE_STATUSES = {"open", "triaged", "resolved"}
TOKEN_TTL_HOURS = 10
ADMIN_TOKEN_TTL_HOURS = 12  # legacy export; admin tokens use ADMIN_TOKEN_TTL_MINUTES
STUDENT_AUTH_TTL_HOURS = 12  # legacy export; student dashboard tokens use STUDENT_AUTH_TTL_MINUTES
ADMIN_TOKEN_TTL_MINUTES = int(os.getenv("ADMIN_TOKEN_TTL_MINUTES", "30"))
STUDENT_AUTH_TTL_MINUTES = int(os.getenv("STUDENT_AUTH_TTL_MINUTES", "30"))
_LOADTEST_SECRET = os.environ.get("LOADTEST_SECRET", "")
WS_MAX_CONNECTIONS_PER_IP = int(os.getenv("WS_MAX_CONNECTIONS_PER_IP", "10"))

# ─── CORS ─────────────────────────────────────────────────────────
# The desktop Electron app serves its HTML from a custom protocol
# (procta-lobby://) registered in main.js — see the v2.3.14
# lobby-blank-window fix. There are TWO distinct window origins, because
# the custom-scheme origin is `scheme://host` and the two windows use
# different hosts (main.js protocol.handle map):
#   • procta-lobby://lobby — the lobby window (lobby/student.html)
#   • procta-lobby://exam  — the exam window (exam/index.html, the
#     renderer that runs ID-verification, save-answers, heartbeat,
#     submit-exam, etc.) since d34926a moved it off file:// to fix a
#     packaged-Windows ERR_FILE_NOT_FOUND.
# Every fetch either window makes to /api/v1/* is cross-origin. BOTH
# origins must be allow-listed exactly (Starlette CORS does exact-string
# matching), or that window's preflight fails and the renderer surfaces
# a wall of "blocked by CORS" + "Failed to fetch". The exam origin was
# missing here, which broke ALL exam-window API calls (ID upload, answer
# save, submit) even though the lobby worked fine.
_CORS_RAW = os.getenv("CORS_ALLOWED_ORIGINS", "")
_CORS_DEFAULT_ORIGINS = [
    "http://localhost",
    "http://localhost:5173",
    "https://app.procta.net",
    # Marketing site posts signup/login to the API cross-origin (it's a
    # separate origin from the app). Without these the browser blocks the
    # preflight ("No Access-Control-Allow-Origin") and NO new teacher can
    # sign up from the marketing site.
    "https://www.procta.net",
    "https://procta.net",
    # Electron desktop app (≥v2.3.14) — custom protocol window origins.
    "procta-lobby://lobby",  # lobby window
    "procta-lobby://exam",   # exam/renderer window (d34926a)
]
CORS_ALLOWED_ORIGINS = [o.strip() for o in _CORS_RAW.split(",") if o.strip()] if _CORS_RAW else list(_CORS_DEFAULT_ORIGINS)
# Env-configured CORS should extend, not accidentally remove, the
# Electron window origins. Otherwise packaged/dev desktop builds can
# login from a restored cookie but fail later API preflights as plain
# "Failed to fetch".
for _origin in ("procta-lobby://lobby", "procta-lobby://exam", "null"):
    if _origin not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(_origin)

# ─── App URL (used for absolute URLs in emails, OAuth callbacks, etc) ───
APP_URL = os.getenv("APP_URL", "https://procta.net").rstrip("/")
# Where the marketing site (procta.net) lives — used as the post-OAuth
# default `return_to`. Same as APP_URL today, but kept separate so the
# split deploy (procta.net + app.procta.net) can diverge later.
MARKETING_URL = os.getenv("MARKETING_URL", APP_URL).rstrip("/")

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
# `overage_price_inr` is what each student OVER the plan limit costs
# per billing cycle. Per the plan-description text, all paid tiers
# charge ₹80 per overage student (price_inr ÷ students for Starter/
# Growth/Pro — all happen to be ₹80). Without this explicit field,
# billing.py was using price_inr (the BASE plan price) as the per-
# student overage price → a single student over Growth = ₹12,000
# extra. Audit P1.4.
PLANS: dict[str, dict[str, int | str]] = {
    "starter":    {"name": "Starter",  "students": 30,  "price_inr": 2400,  "overage_price_inr": 80,  "annual_price_inr": 24000,  "desc": "For small classes & tutorials (₹80/extra student)"},
    "growth":     {"name": "Growth",   "students": 150, "price_inr": 12000, "overage_price_inr": 70,  "annual_price_inr": 120000, "desc": "For departments & mid-size programs (₹70/extra student)"},
    "pro":        {"name": "Pro",      "students": 500, "price_inr": 30000, "overage_price_inr": 60,  "annual_price_inr": 300000, "desc": "For large universities & institutions (₹60/extra student)"},
    "enterprise": {"name": "Enterprise", "students": 999999, "price_inr": 0, "overage_price_inr": 0,   "desc": "Custom pricing — contact sales"},
}
TRIAL_DAYS = 14

# ─── Exam settings ───────────────────────────────────────────────────
# Maximum extra minutes a teacher can grant per student for an exam.
# 600 min = 10 hours (covers extreme accessibility accommodations).
MAX_TIME_EXTENSION_MINUTES = 600
# Card-on-signup enforcement (flag-gated rollout, same pattern as
# RLS_SESSION_CONTEXT). When ON: teacher signup creates the subscription in
# 'created' state (no entitlement) and _check_subscription_active blocks usage
# until the billing owner sets up a payment mandate via the onboarding gate.
# Keep OFF until the onboarding-gate UI + Razorpay are verified on prod, then
# flip — flipping early would lock new signups out (no way to add a card yet).
CARD_ON_SIGNUP_ENFORCED = os.environ.get("CARD_ON_SIGNUP_ENFORCED", "").strip().lower() in {"1", "true", "yes"}
# TOTP_ENCRYPTION_KEY + TOTP_GRACE_DAYS constants removed 2026-05-23
# (TOTP retired in favour of email-OTP 2FA). The TOTP_ENCRYPTION_KEY
# env var is still read directly by app/services/crypto.py for
# encrypting Google Classroom OAuth tokens — name is historical.

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
# Bumped 500 → 5000 after a demo-prep teacher hit the cap during
# dry-runs even though Resend itself showed 0 actual sends. Root
# cause was the cap counter being incremented BEFORE the email
# backend call, so noop-backend (no RESEND_API_KEY) dry-runs still
# consumed quota. The defensive skip in invites._claim_and_bump_cap
# now also short-circuits when the emailer backend is noop, but
# the higher default is the real safety net: no real teacher sends
# 5000 invites/day in normal use; abuse cases still need to be
# rate-limited by the @limiter.limit("5/minute") on the route.
# Guard against a misconfigured 0 / negative: with cap <= 0 the claim check
# (count + batch <= cap) denies EVERY invite for 24h. Someone setting 0 thinking
# it means "unlimited" would silently brick all invite sends — clamp to the
# default instead so a typo can't take the feature down.
_invite_cap_raw = int(os.environ.get("INVITE_DAILY_CAP", "5000"))
INVITE_DAILY_CAP = _invite_cap_raw if _invite_cap_raw > 0 else 5000
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

# ─── LTI 1.3 ──────────────────────────────────────────────────────
LTI_LOGIN_URL = os.getenv("LTI_LOGIN_URL", "")
LTI_LAUNCH_URL = os.getenv("LTI_LAUNCH_URL", "")
LTI_DEEP_LINKING_URL = os.getenv("LTI_DEEP_LINKING_URL", "")

# ─── Overage billing (Gap #4) ──────────────────────────────────────
# Feature-flag: must be deliberately 1/true to actually charge add-ons.
OVERAGE_BILLING_ENABLED = os.environ.get("OVERAGE_BILLING_ENABLED", "").lower() in ("1", "true", "yes")
# Grace count: overage <= OVERAGE_GRACE is recorded but not charged.
OVERAGE_GRACE = int(os.getenv("OVERAGE_GRACE", "0"))

# ─── S3 Object Store (Workstream B — encrypted screenshot storage) ──
S3_ENABLED = os.environ.get("S3_ENABLED", "").lower() in ("1", "true", "yes")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "ap-south-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
# Local screenshot retention when S3 IS the system-of-record (days)
S3_LOCAL_CACHE_DAYS = int(os.getenv("S3_LOCAL_CACHE_DAYS", "7"))

# ─── Kiosk attestation (Gap #43) ───────────────────────────────────
KIOSK_ATTESTATION_SECRET = os.environ.get("KIOSK_ATTESTATION_SECRET", "")
MIN_CLIENT_VERSION = os.environ.get("MIN_CLIENT_VERSION", "0.0.0")
KIOSK_ATTESTATION_ENFORCED = os.environ.get("KIOSK_ATTESTATION_ENFORCED", "").lower() in ("1", "true", "yes")

# ─── Pending verifications ────────────────────────────────────────
_PENDING_VERIFICATION_LIMIT = 50
_PENDING_VERIFICATION_TTL = 300

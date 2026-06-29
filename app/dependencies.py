"""
Re-export hub for backward compatibility (mainly for test patches).

All production code imports directly from its source module. This file
is a convenience hub that re-exports every name the app used to import
from the old single-module layout. New code should NOT add imports here;
instead import directly from the specific module.

Migration guide:
  supabase, _atable         → from .database import supabase, async_table as _atable
  limiter, _rate_limit_key  → from .limiter import ...
  get_logger                → from .logger import get_logger
  require_admin, etc.       → from .auth import ...
  fmt_ist, now_ist, etc.    → from .utils import ...
  SECRET_KEY, etc.          → from .constants import ...
  SessionStatus, etc.       → from .models import ...
  _load_questions, etc.     → from .repositories.questions import ...
  _assert_session_owned     → from .repositories.sessions import ...
  compute_risk_score, etc.  → from .services.risk import ...
  _recalculate_score, etc.  → from .services.scoring import ...

"""
from typing import Any, AsyncGenerator

# ─── Auth ──────────────────────────────────────────────────────────
from .auth import (
    create_token, require_auth, verify_student_token,
    issue_admin_token, issue_student_auth_token,
    _check_session_ownership,
    _get_teacher_by_id, _get_teacher_by_uid,
    verify_admin_token, require_admin,
    _get_student_account_by_id, _get_student_account_by_uid,
    verify_student_auth_token, require_student_account,
)

# ─── Database ──────────────────────────────────────────────────────
from .database import supabase, async_table as _atable

# ─── Logger ────────────────────────────────────────────────────────
from .logger import get_logger

# ─── Constants ─────────────────────────────────────────────────────
from .constants import (
    IST, SECRET_KEY, SUPER_ADMIN_EMAIL, SCREENSHOTS_DIR, QUESTION_IMG_DIR,
    STATIC_DIR, DOWNLOAD_MAC_ARM, DOWNLOAD_MAC_X64, DOWNLOAD_WIN,
    CORS_ALLOWED_ORIGINS, RELEASE_REPO, RELEASE_TTL_SEC, GITHUB_TOKEN,
    TOKEN_TTL_HOURS, ADMIN_TOKEN_TTL_HOURS, STUDENT_AUTH_TTL_HOURS,
    _LOADTEST_SECRET, PRACTICE_PREFIX, _CAL_TIGHT_GAZE, _CAL_LOOSE_GAZE,
    _CAL_TIGHT_HEAD, _CAL_LOOSE_HEAD, _SATURATION_K, _BASELINE_DURATION_MINS,
    _DEFAULT_WEIGHT_HIGH, _DEFAULT_WEIGHT_MED, _CRITICAL_TYPES, INVITE_DAILY_CAP,
    INVITE_URL_TTL, REMINDER_POLL_SECONDS, REMINDER_1H_WINDOW_MIN,
    REMINDER_24H_WINDOW_MIN, CHAT_MAX_TEXT_LEN, CHAT_HISTORY_LIMIT,
    _CLEAR_TOKEN_TTL, _CLEAR_ACTIVE_WINDOW, PLANS, TRIAL_DAYS,
)

# ─── Models ────────────────────────────────────────────────────────
from .models import (
    SessionStatus, InviteStatus, VerificationStatus,
    EventIn, RegisterIn, ValidateIn, ResultIn, AnswerIn, BulkAnswerIn,
    FrameIn, IdVerifyIn,
    TeacherSignupIn, TeacherLoginIn, RefreshIn,
    StudentSignupIn, StudentLoginIn, PasswordResetIn,
    OrgRole, OrgInviteStatus, SubscriptionStatus, PlanTier,
    OrgOut, OrgMemberOut, OrgInviteIn, OrgInviteOut, OrgBillingOut, SubscriptionOut,
)

# ─── Utils ─────────────────────────────────────────────────────────
from .utils import (
    now_ist, fmt_ist, _xlsx_safe, _safe_filename,
    _safe_path_component, _assert_within_directory, _html_escape, ts_to_id,
)

# ─── Rate limiter ──────────────────────────────────────────────────
from .limiter import limiter, _rate_limit_key, _custom_rate_limit_handler

# ─── Redis / event bus ─────────────────────────────────────────────
import logging as _logging
_boot_log = _logging.getLogger("boot")
try:
    from .event_bus import publish as _bus_publish, async_publish as _bus_async_publish, subscribe as _bus_subscribe
    _HAS_REDIS = True
except Exception as _e:
    _HAS_REDIS = False
    _boot_log.warning("event_bus import failed (%s) — falling back to in-memory pub/sub.", _e)
    def _bus_publish(channel: str, payload: dict) -> None: pass
    async def _bus_async_publish(channel: str, payload: dict) -> None: pass
    async def _bus_subscribe(channel: str, keepalive_sec: int = 15) -> AsyncGenerator[dict[Any, Any], None]:
        return
        yield  # pragma: no cover, unreachable -- makes this an async generator

try:
    from . import cache as _cache
except Exception as _e:
    _cache = None  # type: ignore[assignment]
    _boot_log.warning("cache import failed (%s) — running without Redis cache.", _e)

# ─── Practice mode ─────────────────────────────────────────────────
from .services.practice import is_practice, PRACTICE_QUESTIONS, _practice_validate_response

# ─── Calibration quality ───────────────────────────────────────────
from .services.calibration import (
    parse_calibration_details as _parse_calibration_details,
    classify_calibration as _classify_calibration,
    get_calibration_quality,
)

# ─── Violation filtering ───────────────────────────────────────────
from .services.risk import _NON_VIOLATION_TYPES, _is_violation

# ─── Question/config loading ───────────────────────────────────────
from .repositories.questions import (
    load_questions as _load_questions,
    load_exam_config as _load_exam_config,
    get_access_code as _get_access_code,
    set_access_code as _set_access_code,
)

# ─── Answer/scoring helpers ────────────────────────────────────────
from .services.scoring import (
    normalise_answer_set as _normalise_answer_set,
    answers_match as _answers_match,
    translate_student_answer as _translate_student_answer,
    canonicalise_student_answer as _canonicalise_student_answer,
    recalculate_score as _recalculate_score,
    shuffle_seed as _shuffle_seed,
    build_shuffle_view as _build_shuffle_view,
    get_shuffle_flags as _get_shuffle_flags,
)

# ─── Session ownership ─────────────────────────────────────────────
from .repositories.sessions import assert_session_owned as _assert_session_owned

# ─── Risk scoring ──────────────────────────────────────────────────
from .services.risk import (
    VIOLATION_WEIGHTS, _SEVERITY_MULTIPLIER, RISK_LABELS,
    publish_critical_alert, _risk_label, compute_risk_score,
    _BEHAVIORAL_PATTERNS, _CRITICAL_VIOLATIONS,
    generate_session_summary, BLOCKING_TYPES,
)

# ─── Screenshot helpers ────────────────────────────────────────────
from .services.sessions import (
    collect_session_screenshots as _collect_session_screenshots,
    match_screenshot_for_violation as _match_screenshot_for_violation,
    cleanup_screenshots as _cleanup_screenshots,
)

# ─── Bulk query helpers ────────────────────────────────────────────
from .repositories.sessions import (
    violation_counts_by_session as _violation_counts_by_session,
    calibration_tiers_by_session as _calibration_tiers_by_session,
    fetch_all_results as _fetch_all_results,
    stream_csv_results as _stream_csv_results,
    check_group_access as _check_group_access,
)

# ─── Org / plan helpers ────────────────────────────────────────────
from .services.sessions import (
    check_org_limits, get_org_subscription, PLAN_LIMITS,
    _CLEAR_TOKENS, _CLEAR_TOKENS_LOCK,
    clear_token_issue as _clear_token_issue,
    session_is_active as _session_is_active,
    partition_live_sessions as _partition_live_sessions,
    clear_token_consume as _clear_token_consume,
    heartbeat_age_seconds as _heartbeat_age_seconds,
    derive_live_state as _derive_live_state,
    build_sessions_payload as _build_sessions_payload,
)

# ─── Invite helpers ────────────────────────────────────────────────
from .invites import (
    _get_invite_base_url, _new_invite_token, _new_access_code,
    _claim_and_bump_cap, _claim_and_bump_cap_legacy,
)

# ─── Reminder loop ─────────────────────────────────────────────────
from .reminders import _reminder_tick, _reminder_loop, _send_reminder_for_invite, _reminder_window

# ─── Download/release cache ────────────────────────────────────────
from .services.release import (
    _RELEASE_CACHE, _RELEASE_CACHE_EXPIRES, _RELEASE_CACHE_LOCK,
    _match_mac_arm64, _match_mac_x64, _match_win,
    _refresh_release_cache, _resolve_release_asset, _download_redirect,
)

# ─── Invite landing HTML ───────────────────────────────────────────
from .services.invite_landing import _render_invite_error, _render_invite_landing

# ─── Chat ──────────────────────────────────────────────────────────
from .services.chat import ChatHub

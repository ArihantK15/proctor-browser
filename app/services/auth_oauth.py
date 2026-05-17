"""OAuth sign-in/sign-up via Supabase Auth (Google + Microsoft).

Server-side flow. The browser never touches the SUPABASE_ANON_KEY.

Flow:
  1. User clicks "Continue with Google" → hits GET /api/v1/auth/oauth/start
     We build a Supabase authorize URL, redirect the browser to Supabase
  2. Supabase redirects to Google/Microsoft → user consents
  3. Provider redirects back to Supabase → Supabase redirects to our
     /api/v1/auth/oauth/callback?code=... (PKCE flow)
  4. We exchange the code with Supabase, get the user, bind to a
     teacher / student_account row (create if first time), issue our
     own JWT, redirect the user to their dashboard.

State token carries (intent, return_to, csrf) signed with SECRET_KEY so
nobody can tamper with which kind of account gets created.
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt as _jwt

from ..constants import SECRET_KEY, APP_URL
from ..database import async_table as _atable, supabase

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
ALLOWED_PROVIDERS = {"google", "azure"}  # azure = Microsoft Entra ID
ALLOWED_INTENTS = {"teacher", "student"}

# Where Supabase sends the browser after OAuth completes. Must be
# whitelisted in the Supabase Auth dashboard's redirect URLs list.
OAUTH_CALLBACK_PATH = "/api/v1/auth/oauth/callback"

# State JWT TTL — short window between "user clicks Google" and
# "callback fires" (typically <60s for a normal flow). 10 min is
# generous and accounts for slow consent screens.
_STATE_TTL_SECONDS = 600


# ── State token helpers ──────────────────────────────────────────


def issue_state_token(*, intent: str, return_to: str) -> str:
    """Sign a state payload that round-trips through Supabase + Google.

    We can't trust query params coming back from Supabase, but we CAN
    trust a JWT we issued ourselves. State carries the intent (teacher
    vs student account creation) and where to send the user after the
    callback completes.
    """
    if intent not in ALLOWED_INTENTS:
        raise ValueError(f"invalid intent: {intent}")
    payload = {
        "intent":    intent,
        "return_to": return_to,
        "iat":       datetime.now(timezone.utc),
        "exp":       datetime.now(timezone.utc) + timedelta(seconds=_STATE_TTL_SECONDS),
        "scope":     "oauth_state",
    }
    return _jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_state_token(token: str) -> dict:
    """Decode + validate the state JWT. Raises on tamper/expiry."""
    claims = _jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    if claims.get("scope") != "oauth_state":
        raise ValueError("wrong scope")
    if claims.get("intent") not in ALLOWED_INTENTS:
        raise ValueError("invalid intent")
    return claims


# ── URL builders ─────────────────────────────────────────────────


def _callback_url() -> str:
    """Absolute URL to our callback endpoint. Must match the
    redirect URL whitelist in the Supabase dashboard exactly."""
    return f"{APP_URL.rstrip('/')}{OAUTH_CALLBACK_PATH}"


def build_authorize_url(*, provider: str, state: str) -> str:
    """Construct the Supabase Auth authorize URL.

    Supabase's hosted OAuth endpoint is:
        {SUPABASE_URL}/auth/v1/authorize
    We pass the provider, our callback as redirect_to, and our signed
    state. Supabase pipes state through unchanged so we get it back
    at the callback.
    """
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL not configured")

    params = {
        "provider":    provider,
        "redirect_to": _callback_url(),
        # `state` is carried through by Supabase and returned in the
        # callback query string. Used to recover intent + return_to.
        "state":       state,
    }
    return f"{SUPABASE_URL.rstrip('/')}/auth/v1/authorize?{urlencode(params)}"


# ── Code → user → account binding ────────────────────────────────


async def exchange_code_for_user(code: str) -> dict:
    """Exchange the PKCE code Supabase gave us for a session.

    supabase-py wraps the underlying gotrue call. We use service-role
    auth here so the exchange has full privileges.

    Returns the Supabase user dict (id, email, etc.) on success.
    Raises ValueError on any failure — caller surfaces a 400.
    """
    try:
        # supabase-py exposes exchange_code_for_session on auth client
        session = supabase.auth.exchange_code_for_session({"auth_code": code})
    except Exception as e:
        logger.exception("[oauth] code exchange failed")
        raise ValueError(f"code exchange failed: {e}") from e

    user = getattr(session, "user", None) or session.get("user") if isinstance(session, dict) else None
    if not user:
        raise ValueError("supabase returned no user")
    # The .user object may be a pydantic model or a dict depending on
    # supabase-py version. Normalise to dict.
    if hasattr(user, "id"):
        return {
            "id":         str(user.id),
            "email":      (user.email or "").strip().lower(),
            "full_name":  ((user.user_metadata or {}).get("full_name")
                            or (user.user_metadata or {}).get("name")
                            or ""),
            "avatar_url": (user.user_metadata or {}).get("avatar_url", ""),
        }
    return {
        "id":         str(user["id"]),
        "email":      (user.get("email") or "").strip().lower(),
        "full_name":  ((user.get("user_metadata") or {}).get("full_name")
                        or (user.get("user_metadata") or {}).get("name")
                        or ""),
        "avatar_url": (user.get("user_metadata") or {}).get("avatar_url", ""),
    }


async def bind_or_create_teacher(sb_user: dict, ip: str = "") -> dict:
    """Find or create the teachers row for a Supabase user.

    Three cases:
      1. teachers row with this supabase_uid exists → just return it.
      2. teachers row with this email exists but no supabase_uid →
         legacy account created by password signup, never verified;
         link them and mark email verified (Google/MS verified it).
      3. Neither exists → first-time OAuth signup, create a fresh org
         + trial subscription + teacher row.
    """
    from ..constants import PLANS, TRIAL_DAYS
    from ..emailer import send_new_account_notification_job  # already shipped
    from ..jobs.helpers import enqueue_job

    uid = sb_user["id"]
    email = sb_user["email"]
    if not email:
        raise ValueError("oauth provider returned no email")

    # Case 1: already bound
    existing = (await _atable("teachers").select("*")
                .eq("supabase_uid", uid).limit(1).execute()).data
    if existing:
        return existing[0]

    # Case 2: same email, no supabase_uid yet (e.g. legacy password
    # signup that never verified). Only link if the legacy account
    # already verified their email — otherwise create a fresh account
    # to prevent pre-registration account takeover (C29).
    by_email = (await _atable("teachers").select("*")
                .eq("email", email).limit(1).execute()).data
    if by_email:
        teacher = by_email[0]
        if not teacher.get("email_verified_at"):
            logger.info("[oauth] legacy teacher %s unverified — creating fresh", email)
        else:
            await _atable("teachers").update({
                "supabase_uid":      uid,
                "email_verified_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", teacher["id"]).execute()
            teacher["supabase_uid"] = uid
            teacher["email_verified_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("[oauth] linked legacy teacher email=%s", email)
            return teacher

    # Case 3: brand-new signup via OAuth. Create org + sub + teacher.
    # Org name defaults to the part before @ — teacher can rename it
    # from the Org Settings panel later.
    org_name = email.split("@", 1)[0].title() + "'s Organisation"
    slug = _make_slug(org_name)
    # Make slug unique if it collides
    suffix = 1
    base_slug = slug
    while True:
        clash = await _atable("organizations").select("id").eq("slug", slug).execute()
        if not clash.data:
            break
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    org_result = await _atable("organizations").insert({
        "name":         org_name,
        "slug":         slug,
        "max_students": PLANS["starter"]["students"],
    }).execute()
    org_id = org_result.data[0]["id"]

    trial_end = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()
    await _atable("subscriptions").insert({
        "org_id":    str(org_id),
        "plan":      "starter",
        "status":    "trialing",
        "trial_end": trial_end,
    }).execute()

    teacher_result = await _atable("teachers").insert({
        "email":             email,
        "full_name":         sb_user.get("full_name") or email.split("@", 1)[0],
        "supabase_uid":      uid,
        "org_id":            str(org_id),
        "org_role":          "admin",
        "email_verified_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    teacher = teacher_result.data[0]

    # Default exam config so the teacher's dashboard isn't empty
    import uuid as _uuid
    await _atable("exam_config").insert({
        "exam_id":          str(_uuid.uuid4()),
        "teacher_id":       teacher["id"],
        "exam_title":       "Exam",
        "duration_minutes": 60,
    }).execute()

    logger.info("[oauth] created new teacher email=%s org=%s", email, org_name)
    enqueue_job(send_new_account_notification_job,
                account_type="teacher", name=teacher["full_name"], email=email)
    return teacher


async def bind_or_create_student(sb_user: dict) -> dict:
    """Find or create the student_accounts row for a Supabase user.

    Two cases (no org/sub bookkeeping needed for students):
      1. student_accounts row with supabase_uid exists → return it.
      2. Doesn't exist → create one. The students-table linkage
         happens later when the student joins a teacher's exam.
    """
    uid = sb_user["id"]
    email = sb_user["email"]
    if not email:
        raise ValueError("oauth provider returned no email")

    existing = (await _atable("student_accounts").select("*")
                .eq("supabase_uid", uid).limit(1).execute()).data
    if existing:
        return existing[0]

    # Email-link path for legacy password signups
    by_email = (await _atable("student_accounts").select("*")
                .eq("email", email).limit(1).execute()).data
    if by_email:
        acct = by_email[0]
        # C29: Only link legacy accounts that have email_verified_at set
        # (i.e., the user already proved email ownership). If the legacy
        # account was never verified, create a fresh account instead —
        # otherwise anyone who knows the email could claim it via OAuth.
        if not acct.get("email_verified_at"):
            logger.info("[oauth] legacy student acct %s unverified — creating fresh", email)
        else:
            await _atable("student_accounts").update({
                "supabase_uid":      uid,
                "email_verified_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", acct["id"]).execute()
            acct["supabase_uid"] = uid
            acct["email_verified_at"] = datetime.now(timezone.utc).isoformat()
            return acct

    # Brand-new student account
    result = await _atable("student_accounts").insert({
        "supabase_uid":      uid,
        "email":             email,
        "full_name":         sb_user.get("full_name") or email.split("@", 1)[0],
        "email_verified_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return result.data[0]


# ── Helpers ──────────────────────────────────────────────────────


def _make_slug(name: str) -> str:
    """Cheap URL-safe slug. Matches the pattern in auth.py:_slugify
    closely enough to avoid divergence. Lowercased, [a-z0-9-] only."""
    import re
    s = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    return s.strip("-")[:40] or "org"

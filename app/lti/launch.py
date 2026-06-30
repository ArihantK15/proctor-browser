"""LTI 1.3 OIDC login initiation + id_token validation.

This module handles:
  1. OIDC login initiation (/lti/login) — redirects the user to the LMS for authentication
  2. Launch validation (/lti/launch) — validates the id_token JWT from the LMS
  3. Session creation — issues our own JWT and redirects the student to the exam
"""

from ..log_safe import safe
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from ..database import async_table as _atable
from .key import sign_jwt_payload, get_kid
from .registration import find_registration, is_deployment_authorized

from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["find_registration", "is_deployment_authorized", "_fetch_platform_jwks"]

# Fixed namespace for deriving a stable, deterministic org id from an LTI
# tenant tuple (issuer|client_id|deployment_id). Same tenant → same org id,
# so concurrent first-launches converge on one org instead of racing to
# create duplicates. Do NOT change this value — it would orphan every
# auto-provisioned org.
_LTI_ORG_NAMESPACE = uuid.UUID("a3f1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d")

# ── Nonce / state management ──────────────────────────────────────
_lti_contexts: dict[str, dict[str, Any]] = {}

# In-memory fallback (used when Redis cache is unavailable)
_nonces: dict[str, dict[str, Any]] = {}
_states: dict[str, dict[str, Any]] = {}

_cache: Any = None
try:
    from .. import cache as _cache_src
    _cache = _cache_src
except Exception:
    pass

_NONCE_TTL = 3600  # 1 hour
_STATE_TTL = 600   # 10 minutes
_MAX_IN_MEMORY = 10000  # C18: cap in-memory entries before cleanup


def _redis():
    """Return the live Redis client, or None when Redis is unreachable.

    Centralises the "is Redis actually up *right now*?" check. The old
    `if _cache:` guards only tested whether the cache MODULE imported
    (always true), so a runtime Redis outage made `_store_*` write to a
    no-op while `_consume_*` returned None without ever reaching the
    in-memory fallback — every LTI launch then died with "Invalid or
    expired OIDC state" and no way to tell why. Routing both store and
    consume through this helper makes the fallback fire on a real outage
    (single-process / dev) and keeps prod on Redis (required across the
    4 uvicorn workers, since the in-memory dicts are per-process)."""
    if not _cache:
        return None
    try:
        return _cache._client()
    except Exception:
        return None


def _cleanup_expired():
    now = time.time()
    expired_nonces = [k for k, v in _nonces.items() if v.get("expires", 0) < now]
    for k in expired_nonces:
        _nonces.pop(k, None)
    expired_states = [k for k, v in _states.items() if v.get("expires", 0) < now]
    for k in expired_states:
        _states.pop(k, None)


def _store_nonce(nonce: str):
    expires = time.time() + _NONCE_TTL
    if _redis() is not None:
        _cache.set(f"lti_nonce:{nonce}", {"expires": expires}, ttl=_NONCE_TTL)
        return
    if len(_nonces) > _MAX_IN_MEMORY:
        _cleanup_expired()
    _nonces[nonce] = {"expires": expires}


def _consume_nonce(nonce: str) -> bool:
    """Atomically consume a nonce — returns True only once per nonce.

    Uses Redis GETDEL (atomic get-and-delete, Redis ≥ 6.2) so two
    concurrent requests can never both pass the check.  Falls back to
    a Lua script for older Redis, and to the in-process dict otherwise.
    """
    r = _redis()
    if r is not None:
        key = f"lti_nonce:{nonce}"
        try:
            result = r.getdel(key)
            return result is not None
        except Exception:
            try:
                _lua = r.register_script(
                    "local v=redis.call('GET',KEYS[1]) "
                    "if v then redis.call('DEL',KEYS[1]) return 1 "
                    "else return 0 end"
                )
                return bool(_lua(keys=[key]))
            except Exception:
                return False
    # In-process fallback (single-process / no Redis)
    entry = _nonces.pop(nonce, None)
    if entry and entry["expires"] > time.time():
        return True
    return False


def _store_state(state: str, data: dict[str, Any]):
    expires = time.time() + _STATE_TTL
    payload = {**data, "expires": expires}
    if _redis() is not None:
        _cache.set(f"lti_state:{state}", payload, ttl=_STATE_TTL)
        return
    if len(_states) > _MAX_IN_MEMORY:
        _cleanup_expired()
    _states[state] = payload


def _consume_state(state: str) -> dict[str, Any] | None:
    """Atomically consume an OIDC state value — returns the stored
    data exactly once per state, or None if missing/already consumed.

    Mirrors _consume_nonce: a GET-then-DELETE pattern (which was the
    previous implementation) is racy across concurrent callbacks and
    permits state replay — an attacker who intercepts the state can
    re-submit the OIDC callback and get the same launch context twice,
    enabling session-fixation attacks. OIDC requires state to be
    single-use; we enforce that here with Redis GETDEL (atomic, since
    Redis 6.2) and fall back to a Lua script for older Redis.
    """
    r = _redis()
    if r is not None:
        key = f"lti_state:{state}"
        try:
            raw = r.getdel(key)
        except Exception:
            try:
                _lua = r.register_script(
                    "local v=redis.call('GET',KEYS[1]) "
                    "if v then redis.call('DEL',KEYS[1]) return v "
                    "else return false end"
                )
                raw = _lua(keys=[key])
            except Exception:
                return None
        if not raw:
            return None
        try:
            import json as _j
            payload = raw if isinstance(raw, (dict, list)) else _j.loads(
                raw.decode() if isinstance(raw, bytes) else raw
            )
            # Same expiry-tolerance check as the in-process branch
            # below — a stale entry that hasn't been TTL-evicted yet
            # is treated as missing.
            if isinstance(payload, dict) and payload.get("expires", 0) > time.time():
                return payload
            return None
        except Exception:
            return None
    # In-process fallback (single-process / no Redis). dict.pop is
    # already atomic at the GIL level, so this path was correct.
    entry = _states.pop(state, None)
    if entry and entry["expires"] > time.time():
        return entry
    return None


# ── JWKS fetching (platform public keys) ─────────────────────────
_jwks_cache: dict[str, dict[str, Any]] = {}
_jwks_cache_ttl: dict[str, float] = {}

_HTTP_TIMEOUT = 15


async def _fetch_platform_jwks(key_set_url: str) -> dict[str, Any] | None:
    now = time.time()
    if key_set_url in _jwks_cache and _jwks_cache_ttl.get(key_set_url, 0) > now:
        return _jwks_cache[key_set_url]
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(key_set_url)
            resp.raise_for_status()
            data = resp.json()
            _jwks_cache[key_set_url] = data
            _jwks_cache_ttl[key_set_url] = now + 300  # cache for 5 min
            return data
    except Exception as e:
        logger.warning("Failed to fetch platform JWKS from %s: %s", key_set_url, e)
        return None


# ── OIDC Login Initiation ─────────────────────────────────────────
def build_oidc_redirect(
    auth_login_url: str,
    client_id: str,
    login_hint: str,
    target_link_uri: str,
    lti_message_hint: str = "",
    deployment_id: str = "",
) -> tuple[str, str]:
    """Build the OIDC redirect URL and generate state.

    Returns (redirect_url, state) where state must be stored before redirecting.
    """
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    _store_nonce(nonce)
    _store_state(state, {
        "target_link_uri": target_link_uri,
        "nonce": nonce,
        "login_hint": login_hint,
    })

    params = {
        "response_type": "id_token",
        "scope": "openid",
        "response_mode": "form_post",
        "client_id": client_id,
        "login_hint": login_hint,
        "lti_message_hint": lti_message_hint,
        "state": state,
        "nonce": nonce,
        "redirect_uri": target_link_uri,
    }
    if deployment_id:
        params["lti_deployment_id"] = deployment_id
    redirect_url = f"{auth_login_url}?{urlencode(params)}"
    return redirect_url, state


# ── ID Token Validation ───────────────────────────────────────────
_LTI_ROLE_LEARNER = "http://purl.imsglobal.org/vocab/lis/v2/membership#Learner"
_LTI_ROLE_INSTRUCTOR = "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"
_LTI_ROLE_ADMIN = "http://purl.imsglobal.org/vocab/lis/v2/membership#Administrator"


def _parse_lti_roles(roles) -> str:
    # Roles SHOULD be an array of role URIs, but some platforms send a single
    # string — normalise. Match instructor/admin across ALL the LTI role
    # vocabularies (membership#, institution/person#, system/person#) plus the
    # bare short names ("Instructor"/"Administrator") legacy platforms send,
    # rather than only the exact membership URI. Real Canvas/Moodle launches
    # carry several role URIs at once, so a suffix/short match is what's robust.
    if isinstance(roles, str):
        roles = [roles]
    roles_lower = [str(r).strip().lower() for r in (roles or [])]

    def _matches(token: str) -> bool:
        return any(r.endswith("#" + token) or r == token for r in roles_lower)

    if _matches("instructor") or _matches("teacher"):
        return "instructor"
    if _matches("administrator") or _matches("admin"):
        return "admin"
    return "learner"


async def validate_id_token(id_token: str, state: str) -> dict[str, Any]:
    """Validate an LTI 1.3 id_token JWT.

    Steps:
      1. Look up the OIDC state to recover nonce + target_link_uri
      2. Decode JWT header to get kid + alg
      3. Find the platform registration by iss + aud
      4. Fetch platform's JWKS and find the matching key
      5. Verify JWT signature + validate standard claims
      6. Consume the nonce (replay protection)
      7. Return the validated claims

    Returns the validated claims dict on success.
    Raises ValueError with a description on failure.
    """
    # 1. Recover state
    state_data = _consume_state(state)
    if not state_data:
        # Disambiguate the three real causes so a failed launch is
        # self-explanatory in the logs instead of a generic 401:
        #   - state_present=False -> the platform POSTed no state, i.e.
        #     it never went through /lti/login first (OIDC login-init URL
        #     misconfigured in the LMS, or the link points straight at
        #     /lti/launch). State is only minted by build_oidc_redirect.
        #   - redis_up=False -> Redis was unreachable; nothing was stored.
        #   - both true -> state expired (>10 min) or was already consumed
        #     (double-submit / refreshing the launch page).
        logger.warning(
            "lti: OIDC state lookup failed — state_present=%s redis_up=%s "
            "(state_present=False => /lti/login was skipped; redis_up=False "
            "=> Redis outage; both True => expired/replayed)",
            bool(state), _redis() is not None,
        )
        raise ValueError("Invalid or expired OIDC state")

    expected_nonce = state_data.get("nonce", "")

    # 2. Decode JWT header (without verification)
    from .jwk_utils import jwk_to_public_key
    import jwt as _jwt
    import base64 as b64
    import json as _json

    try:
        header_b64 = id_token.split(".")[0]
        header_padded = header_b64 + "=" * (4 - len(header_b64) % 4)
        header_bytes = b64.urlsafe_b64decode(header_padded)
        header = _json.loads(header_bytes)
    except Exception as e:
        logger.warning("lti: failed to decode JWT header: %s", e)
        raise ValueError(f"Failed to decode JWT header: {e}")

    kid = header.get("kid", "")
    # NEVER trust the token's `alg` header — algorithm-confusion attacks
    # (CVE-2015-9235 era) work by sending alg="HS256" so PyJWT treats
    # the public RS key as an HMAC secret, letting anyone with the
    # public JWKS forge tokens. LTI 1.3 mandates RS256, so the relying
    # party (us) hardcodes the accepted algorithm regardless of what
    # the token claims. We DO reject early when the header advertises
    # something other than RS256 — there's no legitimate LTI 1.3 path
    # that uses anything else, and a mismatch is a clear signal of an
    # attempted attack worth logging.
    header_alg = header.get("alg", "")
    if header_alg and header_alg != "RS256":
        # header_alg is the JWT algorithm identifier (e.g., "HS256"),
        # not a credential. Semgrep's logger-credential-leak heuristic
        # over-fires on the "alg" identifier.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.warning("lti: rejecting non-RS256 id_token algorithm=%s kid=%s",
                       safe(header_alg), safe(kid))
        raise ValueError(f"Unsupported JWT algorithm: {header_alg}")

    # 3. Find registration
    # Decode payload without verification first to get iss + aud
    try:
        import json as _json2
        payload_b64 = id_token.split(".")[1]
        payload_padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload_bytes = b64.urlsafe_b64decode(payload_padded)
        payload = _json2.loads(payload_bytes)
    except Exception as e:
        logger.warning("lti: failed to decode JWT payload: %s", e)
        raise ValueError(f"Failed to decode JWT payload: {e}")

    iss = payload.get("iss", "")
    aud_raw = payload.get("aud", "")
    client_id = aud_raw if isinstance(aud_raw, str) else (aud_raw[0] if isinstance(aud_raw, list) else "")

    if not iss or not client_id:
        raise ValueError("Missing iss or aud in id_token")

    registration = find_registration(iss, client_id)
    if not registration:
        raise ValueError(f"No registration found for issuer={iss} client_id={client_id}")

    # 4. Fetch platform JWKS
    jwks_data = await _fetch_platform_jwks(registration.key_set_url)
    if not jwks_data:
        raise ValueError("Failed to fetch platform JWKS")

    # Find matching key by kid
    matching_key = None
    for key in jwks_data.get("keys", []):
        if key.get("kid") == kid:
            matching_key = key
            break
    if not matching_key:
        raise ValueError(f"No matching key found for kid={kid}")

    # 5. Verify signature
    # algorithms=["RS256"] is a HARDCODED list, never `[header.alg]`.
    # See the algorithm-confusion comment at the top of step 2 above.
    try:
        public_key = jwk_to_public_key(matching_key)
        claims = _jwt.decode(
            id_token,
            public_key,
            audience=client_id,
            issuer=iss,
            algorithms=["RS256"],
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
            },
        )
    except Exception as e:
        logger.warning("lti: JWT verification failed for iss=%s: %s", safe(iss), safe(e))
        raise ValueError(f"JWT verification failed: {e}")

    # 6. Verify nonce (binding + replay protection).
    # The id_token's nonce MUST equal the nonce we issued for THIS login —
    # recovered from the consumed OIDC state (expected_nonce). Consuming a
    # valid-but-unrelated nonce we happened to issue for a *different* login
    # would otherwise pass, letting a token's nonce and an OIDC state be
    # mixed across two legitimate logins (the LTI 1.3 / OIDC spec requires the
    # token nonce to match the auth request's nonce). After the binding check,
    # consume it for single-use replay protection.
    token_nonce = claims.get("nonce", "")
    if not token_nonce or not expected_nonce or token_nonce != expected_nonce:
        raise ValueError("Invalid nonce")
    if not _consume_nonce(token_nonce):
        raise ValueError("Replayed nonce")

    # 7. Verify deployment
    deployment_id = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
    )
    if not is_deployment_authorized(registration, deployment_id):
        raise ValueError(f"Deployment not authorized: {deployment_id}")

    return claims


# ── AGS / NRPS context storage ────────────────────────────────────
_AGS_NRPS_TTL = 86400  # 24 hours

def _get_ctx_key(iss: str, deployment_id: str) -> str:
    return f"lti_ctx:{iss}|{deployment_id}"


async def _store_ags_nrps_context(claims: dict[str, Any]):
    """Extract and store AGS/NRPS endpoint URLs from launch claims.

    Stored in cache/Redis so the exam completion flow can later push
    grades back to the LMS without needing the full launch context.
    """
    iss = claims.get("iss", "")
    deployment_id = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
    )
    if not iss or not deployment_id:
        return

    ags_claim = claims.get(
        "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint", {}
    )
    nrps_claim = claims.get(
        "https://purl.imsglobal.org/spec/lti-nrps/claim/namesroleservice", {}
    )

    if not ags_claim and not nrps_claim:
        return

    ctx = {
        "iss": iss,
        "deployment_id": deployment_id,
        "ags_lineitems": ags_claim.get("lineitems", ""),
        "ags_lineitem": ags_claim.get("lineitem", ""),
        "ags_scope": ags_claim.get("scope", []),
        "nrps_url": nrps_claim.get("context_memberships_url", ""),
        "nrps_versions": nrps_claim.get("service_versions", []),
    }

    key = _get_ctx_key(iss, deployment_id)
    if _cache:
        _cache.set(key, ctx, ttl=_AGS_NRPS_TTL)
    else:
        # In-memory fallback
        try:
            ctx["expires"] = time.time() + _AGS_NRPS_TTL
            _lti_contexts[key] = ctx
        except Exception:
            logger.warning("lti: failed to store AGS/NRPS context in memory for %s", key)

    logger.debug(
        "Stored AGS/NRPS context: iss=%s deployment=%s ags=%s nrps=%s",
        iss, deployment_id, bool(ags_claim), bool(nrps_claim),
    )


def get_ags_context(iss: str, deployment_id: str) -> dict[str, Any] | None:
    """Retrieve stored AGS context for a deployment."""
    key = _get_ctx_key(iss, deployment_id)
    if _cache:
        from typing import cast
        return cast("dict[str, Any] | None", _cache.get(key))
    entry = _lti_contexts.get(key)
    if entry and entry.get("expires", 0) > time.time():
        return entry
    return None


def get_nrps_context(iss: str, deployment_id: str) -> dict[str, Any] | None:
    """Retrieve stored NRPS context for a deployment."""
    key = _get_ctx_key(iss, deployment_id)
    if _cache:
        from typing import cast
        return cast("dict[str, Any] | None", _cache.get(key))
    entry = _lti_contexts.get(key)
    if entry and entry.get("expires", 0) > time.time():
        return entry
    return None


def get_lti_student_context(lti_user_id: str) -> dict[str, Any] | None:
    """Retrieve stored LTI context for a specific student.

    Returns {iss, deployment_id, sub} if found, or None.
    """
    key = f"lti_student:{lti_user_id}"
    if _cache:
        from typing import cast
        return cast("dict[str, Any] | None", _cache.get(key))
    entry = _lti_contexts.get(key)
    if entry and entry.get("expires", 0) > time.time():
        return entry
    return None


async def store_lti_student_context(claims: dict[str, Any], lti_user_id: str):
    """Store per-student LTI context for later grade passback.

    Saves issuer + deployment_id + sub so the exam submission flow
    can look up the right AGS context and push grades.
    """
    iss = claims.get("iss", "")
    sub = claims.get("sub", "")
    deployment_id = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
    )
    # aud is client_id in LTI 1.3 (may be a string or a single-element list)
    aud_raw = claims.get("aud", "")
    client_id = aud_raw if isinstance(aud_raw, str) else (aud_raw[0] if isinstance(aud_raw, list) else "")
    if not iss or not sub:
        return

    ctx = {
        "iss": iss,
        "sub": sub,
        "deployment_id": deployment_id,
        "client_id": client_id,
    }
    key = f"lti_student:{lti_user_id}"
    if _cache:
        _cache.set(key, ctx, ttl=_AGS_NRPS_TTL)
    else:
        try:
            ctx["expires"] = time.time() + _AGS_NRPS_TTL
            _lti_contexts[key] = ctx
        except Exception:
            logger.warning("lti: failed to store student context in memory for %s", key)


# ── Tenant resolution (Model 3: one org per LMS tenant) ───────────
async def _resolve_org_for_launch(
    iss: str, client_id: str, deployment_id: str, registration, platform_name: str
) -> tuple[str, bool]:
    """Resolve the Procta org a launch belongs to. Returns (org_id, is_explicit).

    Two modes:
      • Explicit binding — the registration declares an org_id (an admin
        pinned this LMS to a pre-existing org). Used as-is.
      • Auto-provision (Model 3) — no org_id on the registration: each LMS
        *tenant* becomes its own isolated Procta org, created on first
        launch. The tenant key is (issuer, client_id, deployment_id), NOT
        issuer alone — hosted Canvas/Moodle share one issuer across all
        their customers, so issuer-only would merge every customer into one
        org (a cross-tenant leak). The org id is a deterministic uuid5 of
        the tuple so concurrent first-launches converge on one org.
    """
    explicit = (registration.org_id if registration else None) or None
    if explicit:
        return explicit, True

    if not (iss and client_id and deployment_id):
        # Without a full tenant tuple we cannot isolate this launch safely.
        raise ValueError(
            "LTI launch missing issuer/client_id/deployment_id — cannot isolate tenant"
        )

    tenant_key = f"{iss}|{client_id}|{deployment_id}"
    org_id = str(uuid.uuid5(_LTI_ORG_NAMESPACE, tenant_key))

    existing = await _atable("organizations").select("id").eq("id", org_id).limit(1).execute()
    if existing.data:
        return org_id, False

    # Auto-provision. select-then-insert (not upsert) so a later launch never
    # clobbers an org an admin has since renamed; the race of two concurrent
    # first-launches is caught below and resolved to the single winner.
    short = uuid.uuid5(_LTI_ORG_NAMESPACE, tenant_key).hex[:10]
    from ..constants import TRIAL_DAYS, PLANS
    from .. import db_context as _dbctx
    try:
        # LTI launch is pre-auth (the LMS, not a Procta user) so there is no
        # owning context: the organizations/subscriptions INSERT policies
        # (phase128 orgs_ins) require app.is_privileged(). Under
        # RLS_SESSION_CONTEXT the ambient context matched zero rows and
        # auto-provisioning was denied. Elevate the provisioning writes.
        with _dbctx.system_context():
            await _atable("organizations").insert({
                "id": org_id,
                "name": f"{platform_name or iss} (LTI)",
                "slug": f"lti-{short}",
                "max_students": PLANS["starter"]["students"],
            }).execute()
            trial_end = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()
            await _atable("subscriptions").upsert({
                "org_id": org_id,
                "plan": "starter",
                "status": "trialing",
                "trial_end": trial_end,
            }, on_conflict="org_id").execute()
        logger.info("lti: auto-provisioned org %s for tenant %s", org_id, safe(tenant_key))
        return org_id, False
    except Exception as e:
        # A concurrent first-launch likely created it between our SELECT and
        # INSERT. Re-check; if it now exists, use it. Otherwise re-raise.
        again = await _atable("organizations").select("id").eq("id", org_id).limit(1).execute()
        if again.data:
            return org_id, False
        logger.error("lti: failed to auto-provision org for tenant %s: %s", safe(tenant_key), e)
        raise


# ── Session creation ──────────────────────────────────────────────
async def find_or_create_lti_user(claims: dict[str, Any]) -> dict[str, Any]:
    """Find or create a teacher/student record from LTI claims.

    Returns a dict with user info suitable for creating a session.
    """
    # LTI Identity Claims
    # See https://www.imsglobal.org/spec/lti/v1p3/#identity-claims
    iss = claims.get("iss", "")
    sub = claims.get("sub", "")
    email = claims.get("email", "")
    name = claims.get("name", "")
    given_name = claims.get("given_name", "")
    family_name = claims.get("family_name", "")
    full_name = name or f"{given_name} {family_name}".strip() or email.split("@")[0]

    lti_roles_raw = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/roles", []
    )
    role = _parse_lti_roles(lti_roles_raw)
    logger.info("lti: launch roles=%s -> role=%s", safe(str(lti_roles_raw)[:300]), role)

    # Unique identifier combining platform + user
    lti_user_id = f"{iss}|{sub}"

    # ── Tenant resolution ─────────────────────────────────────────
    # Every LTI identity MUST resolve to a Procta org, or it would be an
    # org-less user the tenant-isolation (RLS) model cannot scope. The
    # registration may pin an explicit org (manual binding); otherwise each
    # LMS tenant self-onboards into its own auto-provisioned org (Model 3).
    aud_raw = claims.get("aud", "")
    client_id = aud_raw if isinstance(aud_raw, str) \
        else (aud_raw[0] if isinstance(aud_raw, list) and aud_raw else "")
    deployment_id = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/deployment_id", ""
    )
    registration = find_registration(iss, client_id)
    platform_name = getattr(registration, "platform_name", None) if registration else None
    org_id, org_is_explicit = await _resolve_org_for_launch(
        iss, client_id, deployment_id, registration, platform_name or ""
    )

    # Store AGS/NRPS context for grade passback and roster sync
    await _store_ags_nrps_context(claims)

    if role in ("instructor", "admin"):
        # Look up teacher by LTI ID
        try:
            result = await _atable("teachers").select("*").eq("lti_user_id", lti_user_id).limit(1).execute()
            if result.data:
                teacher = result.data[0]
            else:
                # Link to a pre-existing teacher with the same email ONLY
                # within this org — a global email match could bind the
                # launch to a teacher in a DIFFERENT tenant (cross-org leak).
                teacher = None
                if email:
                    by_email = await _atable("teachers").select("*").eq(
                        "email", email).eq("org_id", str(org_id)).limit(1).execute()
                    if by_email.data:
                        teacher = by_email.data[0]
                if teacher is None:
                    # Role within the org. For an explicitly-bound org the
                    # human admin already exists, so LTI instructors are plain
                    # teachers. For an AUTO-provisioned org the first
                    # instructor to arrive becomes its admin (someone must own
                    # it); later instructors are teachers.
                    org_role = "teacher"
                    if not org_is_explicit:
                        admin_exists = await _atable("teachers").select("id").eq(
                            "org_id", str(org_id)).eq("org_role", "admin").limit(1).execute()
                        if not admin_exists.data:
                            org_role = "admin"
                    tid = str(uuid.uuid4())
                    teacher = {
                        "id": tid,
                        # teachers.supabase_uid is NOT NULL + UNIQUE. LTI
                        # instructors are identity-managed by the LMS, not
                        # Supabase Auth, so there is no real Supabase user to
                        # bind to — mint a synthetic UUID to satisfy the
                        # constraint. (uuid4 is collision-safe vs real uids.)
                        # Without this the insert hard-fails and EVERY LTI
                        # instructor launch 500s.
                        "supabase_uid": str(uuid.uuid4()),
                        "email": email or f"lti_{sub[:8]}@lti.procta.net",
                        "full_name": full_name,
                        "org_role": org_role,
                        "org_id": org_id,
                        "lti_user_id": lti_user_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await _atable("teachers").insert(teacher).execute()
            return {"role": "teacher", **teacher}
        except Exception as e:
            logger.error("Failed to find/create LTI teacher: %s", e)
            raise

    # Learner role — find or create student
    try:
        # Look up by LTI user ID
        result = await _atable("students").select("*").eq("lti_user_id", lti_user_id).limit(1).execute()
        if result.data:
            student = result.data[0]
        else:
            # Collision-resistant roll: sub[:12] truncation could map two
            # distinct LMS users (even across platforms) to the same roll.
            # Derive it from a hash of the full platform|user identifier.
            roll_hash = hashlib.sha256(lti_user_id.encode("utf-8")).hexdigest()[:16].upper()
            roll = f"LTI_{roll_hash}"
            student = {
                "roll_number": roll,
                "full_name": full_name,
                "email": email or f"lti_{sub[:8]}@lti.procta.net",
                "lti_user_id": lti_user_id,
                "org_id": org_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await _atable("students").insert(student).execute()

        # Store per-student LTI context for subsequent grade passback
        await store_lti_student_context(claims, lti_user_id)

        return {"role": "learner", **student}
    except Exception as e:
        logger.error("Failed to find/create LTI student: %s", e)
        raise


def issue_lti_session_token(user: dict[str, Any], target_link_uri: str) -> str:
    """Issue a short-lived JWT for the user to access the exam/dashboard."""
    from ..auth.tokens import issue_admin_token, issue_student_auth_token
    from ..auth import create_token
    if user.get("role") == "teacher":
        return issue_admin_token(user)
    else:
        # LTI learners are identity-managed by the LMS, not Procta
        # student_accounts. Keep this token roll-scoped; LMS privacy/export
        # obligations stay with the LMS integration boundary.
        return create_token(
            roll_number=user.get("roll_number", ""),
            teacher_id=user.get("teacher_id", ""),
            exam_id="",
        )

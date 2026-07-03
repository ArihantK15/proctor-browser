"""HMAC-SHA256 kiosk attestation verification.

The Electron client signs a canonical JSON payload with the shared
KIOSK_ATTESTATION_SECRET and sends it to /api/v1/exam/attest so the
server can verify the student is using the secure desktop browser.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from ..constants import KIOSK_ATTESTATION_SECRET, MIN_CLIENT_VERSION

_TS_TOLERANCE = 300  # ±5 minutes


def _canonical(att: dict[str, Any]) -> str:
    """Deterministic JSON serialisation — same order the client uses."""
    return json.dumps(att, sort_keys=True, separators=(",", ":"))


def _semver_gte(installed: str, minimum: str) -> bool:
    """Compare two semver strings (MAJOR.MINOR.PATCH)."""
    def _parts(v: str) -> tuple[int, ...]:
        # Take the leading integer of each dotted part so a suffixed version
        # ("2.4.0-beta") compares on its numeric core instead of collapsing to
        # (0,0,0) and being wrongly rejected. app.getVersion() is normally clean.
        out: list[int] = []
        for p in str(v or "").strip().split("."):
            m = re.match(r"\d+", p)
            out.append(int(m.group()) if m else 0)
        return tuple(out) or (0,)

    return _parts(installed) >= _parts(minimum)


def verify_attestation(
    att: dict[str, Any],
    sig: str,
    expected_session_key: str | None = None,
    expected_roll: str | None = None,
    expected_nonce: str | None = None,
    nonce_issued_at: str | None = None,
) -> tuple[bool, str]:
    """Verify a kiosk attestation payload and signature.

    When *expected_nonce* is provided (v2 attestation) the caller must
    also supply *nonce_issued_at*; the nonce must match, must be within
    the TTL window, and the payload version must be ≥ 2.

    Returns (ok, reason) where *ok* is True only when every check passes.
    """
    if not KIOSK_ATTESTATION_SECRET:
        return False, "attestation not configured"

    # --- signature ---
    canonical = _canonical(att)
    expected_sig = hmac.new(
        KIOSK_ATTESTATION_SECRET.encode(),
        canonical.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return False, "invalid signature"

    # --- timestamp ---
    ts = att.get("ts")
    if not isinstance(ts, (int, float)):
        return False, "missing or invalid timestamp"
    now = time.time()
    if abs(now - ts) > _TS_TOLERANCE:
        return False, "timestamp out of tolerance"

    # --- session_key (attest endpoint only) ---
    if expected_session_key is not None:
        if att.get("session_key") != expected_session_key:
            return False, "session_key mismatch"

    # --- roll (attest endpoint only) ---
    if expected_roll is not None:
        if str(att.get("roll", "")).upper() != str(expected_roll).upper():
            return False, "roll mismatch"

    # --- nonce (v2 attestation only) ---
    if expected_nonce is not None:
        if att.get("v") not in (2, "2"):
            return False, "expected v2 attestation (nonce required)"

        if not hmac.compare_digest(att.get("nonce", ""), expected_nonce):
            return False, "nonce mismatch"

        if nonce_issued_at:
            try:
                issued = datetime.fromisoformat(nonce_issued_at)
                if issued.tzinfo is None:
                    issued = issued.replace(tzinfo=timezone.utc)
                age = time.time() - issued.timestamp()
                if abs(age) > _TS_TOLERANCE:
                    return False, "nonce expired"
            except (ValueError, TypeError):
                return False, "invalid nonce_issued_at"

    # --- kiosk ---
    if att.get("kiosk") is not True:
        return False, "kiosk not enabled"

    # --- client_version ---
    cv = att.get("client_version", "0.0.0")
    if not _semver_gte(cv, MIN_CLIENT_VERSION):
        return False, f"client version {cv} below minimum {MIN_CLIENT_VERSION}"

    return True, "ok"


def verify_app_attestation(att: dict[str, Any], sig: str) -> bool:
    """Lightweight sibling of verify_attestation() for non-exam contexts —
    currently the desktop lobby's login/signup form, which can't use
    Cloudflare Turnstile because it loads via the procta-lobby:// custom
    scheme (a non-DNS "domain" Cloudflare won't allowlist). Proves only
    "this HMAC came from a build holding KIOSK_ATTESTATION_SECRET" plus a
    fresh timestamp — no kiosk/session/nonce/client-version checks, since
    those are exam-window concepts that don't apply to the lobby.
    """
    if not KIOSK_ATTESTATION_SECRET:
        return False
    if not isinstance(att, dict) or not isinstance(sig, str) or not sig:
        return False

    canonical = _canonical(att)
    expected_sig = hmac.new(
        KIOSK_ATTESTATION_SECRET.encode(),
        canonical.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return False

    ts = att.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    return abs(time.time() - ts) <= _TS_TOLERANCE

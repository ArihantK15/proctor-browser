#!/usr/bin/env python3
"""Kiosk-attestation round-trip checker.

Run on the server (inside the api container) to confirm a real client→server
attestation worked, WITHOUT enabling enforcement or grepping logs.

Usage:
    # one session:
    docker compose exec api python scripts/check_attestation.py <session_key>
    # recent sessions overview (no arg):
    docker compose exec api python scripts/check_attestation.py

Reads:
  - exam_sessions.kiosk_attested / attested_at / client_version  (set on success)
  - violations(violation_type='kiosk_attestation_failed').details (reason on fail)

Never prints the secret value — only its presence/length.
"""
import asyncio
import os
import sys

from app.database import async_table as _atable

FAIL_HINTS = {
    "invalid signature": "Client's baked secret != server KIOSK_ATTESTATION_SECRET. "
                         "Re-set the server .env to the EXACT GitHub secret value (or rotate both + rebuild).",
    "attestation not configured": "Server KIOSK_ATTESTATION_SECRET is empty. Set it and restart the api container.",
    "timestamp out of tolerance": "Clock skew between client and server. Check NTP on the student machine / server.",
    "nonce": "Nonce mismatch/expired/replayed, or client sent a v1 payload. Client-side issue.",
    "roll mismatch": "Attestation roll != JWT roll. Client bug.",
    "session_key mismatch": "Attestation session_key != the session being attested. Client bug.",
}


def _hint(reason: str) -> str:
    r = (reason or "").lower()
    for k, v in FAIL_HINTS.items():
        if k in r:
            return v
    return "Unrecognized reason — inspect the client payload."


def _config_banner() -> None:
    secret = os.environ.get("KIOSK_ATTESTATION_SECRET", "")
    enforced = os.environ.get("KIOSK_ATTESTATION_ENFORCED", "")
    print("── attestation config (server) ──")
    print(f"  KIOSK_ATTESTATION_SECRET : {'set (%d chars)' % len(secret) if secret else 'EMPTY — attestation cannot work'}")
    print(f"  KIOSK_ATTESTATION_ENFORCED: {enforced or '(unset/0 — not enforced)'}")
    print()


async def _check_one(session_key: str) -> None:
    rows = (await _atable("exam_sessions")
            .select("session_key,roll_number,status,kiosk_attested,attested_at,client_version")
            .eq("session_key", session_key).limit(1).execute()).data or []
    if not rows:
        print(f"✗ no exam_sessions row for session_key={session_key!r}")
        return
    s = rows[0]
    attested = s.get("kiosk_attested") is True
    print(f"session : {session_key}")
    print(f"  roll        : {s.get('roll_number')}")
    print(f"  status      : {s.get('status')}")
    print(f"  attested    : {attested}")
    if attested:
        print(f"  attested_at : {s.get('attested_at')}")
        print(f"  client_ver  : {s.get('client_version')}")
        print("\n✅ ATTESTED — client↔server secrets match end-to-end. Safe to enforce.")
        return
    fails = (await _atable("violations")
             .select("created_at,details")
             .eq("session_key", session_key)
             .eq("violation_type", "kiosk_attestation_failed")
             .order("created_at", desc=True).limit(3).execute()).data or []
    if fails:
        reason = fails[0].get("details") or "(no detail)"
        print(f"\n✗ NOT ATTESTED — latest failure reason: {reason!r}")
        print(f"  → {_hint(reason)}")
        if len(fails) > 1:
            print(f"  ({len(fails)} recent failures)")
    else:
        print("\n⚠ NOT ATTESTED and no attestation-failure violation recorded — "
              "the client likely never called /api/v1/exam/attest "
              "(old client, or attestation step didn't run).")


async def _overview() -> None:
    rows = (await _atable("exam_sessions")
            .select("session_key,roll_number,status,kiosk_attested,attested_at,started_at")
            .order("started_at", desc=True).limit(15).execute()).data or []
    if not rows:
        print("no exam_sessions found.")
        return
    print("recent sessions (newest first):")
    print(f"  {'attested':9} {'status':14} {'roll':16} session_key")
    for s in rows:
        mark = "yes" if s.get("kiosk_attested") is True else "NO"
        print(f"  {mark:9} {str(s.get('status')):14} {str(s.get('roll_number')):16} {s.get('session_key')}")
    print("\nPass a session_key as an argument to see its attestation reason.")


async def main() -> None:
    _config_banner()
    if len(sys.argv) > 1:
        await _check_one(sys.argv[1].strip())
    else:
        await _overview()


if __name__ == "__main__":
    asyncio.run(main())

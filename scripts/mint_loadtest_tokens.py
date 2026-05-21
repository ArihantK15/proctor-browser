#!/usr/bin/env python3
"""Mint synthetic student JWTs for mixed proctoring load tests.

Run inside the API container so it uses the same JWT secret as production:

    docker compose run --rm --entrypoint python api \
      scripts/mint_loadtest_tokens.py --count 1000 --prefix MIXED \
      > /tmp/mixed_tokens.json

The generated session IDs are intentionally synthetic. They satisfy the
student-token session ownership check but do not need real student rows.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import sys
import uuid

import jwt

DEFAULT_TOKEN_TTL_HOURS = 24


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500, help="number of token rows to emit")
    parser.add_argument("--prefix", default="MIXED", help="roll/session prefix (joined by '_')")
    parser.add_argument(
        "--zero-pad", type=int, default=0,
        help="zero-pad index to N digits — set to 4 to match setup_test_data.py "
             "(produces LOADTEST_0001 instead of LOADTEST_1). Default 0 = no padding.",
    )
    parser.add_argument("--teacher-id", default="", help="optional teacher id claim")
    parser.add_argument("--exam-id", default="", help="optional exam id claim")
    parser.add_argument("--student-id-prefix", default="", help="optional student id prefix claim")
    return parser.parse_args()


def _secret_key() -> str:
    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("SECRET_KEY") or ""
    if not secret:
        raise SystemExit("SUPABASE_JWT_SECRET is required to mint tokens")
    if len(secret) < 32:
        raise SystemExit("SUPABASE_JWT_SECRET must be at least 32 characters")
    return secret


def _create_token(
    roll_number: str,
    *,
    secret_key: str,
    teacher_id: str | None = None,
    exam_id: str | None = None,
    student_id: str | None = None,
) -> tuple[str, str]:
    """Mint a student JWT and return (token, csrf_value).

    The csrf value is also embedded inside the JWT as a claim; we return
    it separately so callers can put it in the X-CSRF-Token header
    without having to decode the JWT to extract it.
    """
    now = datetime.now(timezone.utc)
    ttl_hours = int(os.environ.get("TOKEN_TTL_HOURS") or DEFAULT_TOKEN_TTL_HOURS)
    csrf = secrets.token_hex(16)
    payload = {
        "roll": roll_number,
        "csrf": csrf,
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(hours=ttl_hours),
        "iat": now,
    }
    if teacher_id:
        payload["tid"] = teacher_id
    if exam_id:
        payload["eid"] = exam_id
    if student_id:
        payload["sid"] = student_id
    return jwt.encode(payload, secret_key, algorithm="HS256"), csrf


def main() -> int:
    args = _parse_args()
    if args.count <= 0:
        raise SystemExit("--count must be positive")

    secret_key = _secret_key()
    rows = []
    for idx in range(1, args.count + 1):
        idx_str = f"{idx:0{args.zero_pad}d}" if args.zero_pad > 0 else str(idx)
        roll = f"{args.prefix}_{idx_str}"
        student_id = f"{args.student_id_prefix}{idx}" if args.student_id_prefix else None
        token, csrf = _create_token(
            roll,
            secret_key=secret_key,
            teacher_id=args.teacher_id or None,
            exam_id=args.exam_id or None,
            student_id=student_id,
        )
        rows.append(
            {
                "roll_number": roll,
                "session_id": f"{roll}_RUN",
                "token": token,
                "csrf": csrf,
            }
        )

    json.dump(rows, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

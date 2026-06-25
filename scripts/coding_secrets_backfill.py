"""One-off backfill: envelope-encrypt existing coding answer keys at rest (B1).

Encrypts any `coding_test_cases.expected_output` rows that were written BEFORE
`CODING_SECRETS_KEY` was configured (legacy plaintext). New rows are already
encrypted on write by app/routers/admin_coding.py. Idempotent — `is_encrypted`
skips rows already carrying an `enc:v1:` token, so re-running is safe.

Run inside the app environment (so it sees CODING_SECRETS_KEY + the DB), e.g.
on the prod box where the API runs in Docker:

    git show origin/main:scripts/coding_secrets_backfill.py \\
      | docker exec -i proctor-api python -

Prereqs: CODING_SECRETS_KEY must be set (without it, encrypt() is a no-op
pass-through and this does nothing useful). See docs/coding-secrets-backfill.md.
"""
import asyncio

from app.database import async_table as _atable
from app.db_context import system_context
from app.services import secrets_crypto as S


async def main():
    with system_context():
        rows = (await _atable("coding_test_cases")
                .select("id,expected_output").execute()).data or []
    total = len(rows)
    already = sum(1 for r in rows if S.is_encrypted(r.get("expected_output")))
    plain = total - already
    print("rows=%d already_encrypted=%d plaintext=%d" % (total, already, plain))
    done = 0
    for r in rows:
        cur = r.get("expected_output")
        if S.is_encrypted(cur):
            continue
        with system_context():
            await _atable("coding_test_cases").update(
                {"expected_output": S.encrypt(cur)}).eq("id", r["id"]).execute()
        done += 1
    print("encrypted_now=%d  (re-run safe; idempotent)" % done)


if __name__ == "__main__":
    asyncio.run(main())

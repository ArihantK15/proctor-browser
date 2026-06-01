"""Purpose-tag isolation contract for app.services.email_otp.

The plan in `the-load-is-running-sparkling-canyon.md` adds four new
OTP gates (signup_verify, account_delete, password_reset,
email_change) that all share the same underlying email_otp service.
The security property both Track A and Track B depend on is:
  a code issued for purpose X cannot be replayed against purpose Y.

This test documents the contract. If a future refactor of the
service breaks isolation, this test fires immediately.

Both tracks will reference this contract in their own test files;
the foundation commit lands it once.
"""
from unittest.mock import patch

import pytest

# Mock the database layer in the same way the rest of tests do —
# email_otp's issue() and verify() both call into _atable("email_otps")
# which is patched by conftest's fixtures.


@pytest.mark.asyncio
async def test_purpose_tags_are_isolated(_atable_mock_factory=None):
    """Code issued under purpose A is rejected when verified against purpose B.

    Uses a synthetic in-memory store to avoid a real DB dependency:
    the email_otps mock simply tracks the rows that issue() inserts
    and lets verify() read them back filtered by purpose. Real
    bcrypt hashing is used so we exercise the real verify path.
    """
    from app.services import email_otp

    # Synthetic in-memory store: list of dicts representing rows.
    rows: list[dict] = []
    _id_counter = [0]

    class _Insert:
        def __init__(self, payload): self._payload = payload
        async def execute(self):
            _id_counter[0] += 1
            row = {
                "id": _id_counter[0],
                "attempts": 0,
                "used_at": None,
                **self._payload,
            }
            rows.append(row)
            return type("R", (), {"data": [row]})()

    class _Select:
        def __init__(self):
            self._filters = []
            self._order = None
            self._limit_n = None
            self._is_null = []
        def eq(self, col, val):
            self._filters.append((col, val))
            return self
        def is_(self, col, val):
            if val == "null":
                self._is_null.append(col)
            return self
        def gte(self, _col, _val):
            # Rate-limit lookup in email_otp.issue() — the test doesn't
            # exercise the cap so a permissive no-op is fine. Real test
            # for the cap would use a separate fixture.
            return self
        def order(self, col, desc=False):
            self._order = (col, desc)
            return self
        def limit(self, n):
            self._limit_n = n
            return self
        async def execute(self):
            matches = []
            for r in rows:
                if all(r.get(c) == v for c, v in self._filters):
                    if all(r.get(c) is None for c in self._is_null):
                        matches.append(r)
            return type("R", (), {"data": matches})()

    class _Update:
        def __init__(self, payload):
            self._payload = payload
            self._filters = []
            self._is_null = []
        def eq(self, col, val):
            self._filters.append((col, val))
            return self
        def is_(self, col, val):
            if val == "null":
                self._is_null.append(col)
            return self
        async def execute(self):
            # Apply only to rows that match ALL chained filters — the
            # prior implementation re-walked rows on every .eq() call,
            # which silently dropped the AND-semantics that issue()'s
            # "invalidate prior unused codes" UPDATE depends on.
            for r in rows:
                if all(r.get(c) == v for c, v in self._filters) and \
                   all(r.get(c) is None for c in self._is_null):
                    r.update(self._payload)
            return type("R", (), {"data": []})()

    class _Table:
        def insert(self, payload): return _Insert(payload)
        def select(self, *_args, **_kw): return _Select()
        def update(self, payload): return _Update(payload)

    def _atable_stub(name): return _Table()

    with patch.object(email_otp, "_atable", _atable_stub):
        # Issue under purpose "signup_verify" for user "u1"
        code_signup = await email_otp.issue("student", "u1", "signup_verify")
        assert len(code_signup) == 6 and code_signup.isdigit()

        # The same code must NOT verify against a different purpose
        cross_ok = await email_otp.verify(
            "student", "u1", "account_delete", code_signup,
        )
        assert cross_ok is False, (
            "Purpose-tag isolation broken: a signup_verify code was "
            "accepted as an account_delete code. This would let an "
            "attacker who intercepts a signup OTP delete the account."
        )

        # The same code MUST verify against its own purpose
        same_ok = await email_otp.verify(
            "student", "u1", "signup_verify", code_signup,
        )
        assert same_ok is True


@pytest.mark.asyncio
async def test_purpose_tags_isolated_across_users():
    """Code issued for user A cannot be used to verify user B's action."""
    from app.services import email_otp

    rows: list[dict] = []
    _id_counter = [0]

    class _Insert:
        def __init__(self, payload): self._payload = payload
        async def execute(self):
            _id_counter[0] += 1
            row = {"id": _id_counter[0], "attempts": 0, "used_at": None, **self._payload}
            rows.append(row)
            return type("R", (), {"data": [row]})()

    class _Select:
        def __init__(self):
            self._f = []
            self._is_null = []
        def eq(self, c, v): self._f.append((c, v)); return self
        def is_(self, c, v):
            if v == "null": self._is_null.append(c)
            return self
        def gte(self, _c, _v): return self
        def order(self, *_a, **_kw): return self
        def limit(self, _n): return self
        async def execute(self):
            m = [r for r in rows
                 if all(r.get(c) == v for c, v in self._f)
                 and all(r.get(c) is None for c in self._is_null)]
            return type("R", (), {"data": m})()

    class _Update:
        def __init__(self, p):
            self._p = p
            self._f = []
            self._is_null = []
        def eq(self, c, v): self._f.append((c, v)); return self
        def is_(self, c, v):
            if v == "null": self._is_null.append(c)
            return self
        async def execute(self):
            for r in rows:
                if all(r.get(c) == v for c, v in self._f) and \
                   all(r.get(c) is None for c in self._is_null):
                    r.update(self._p)
            return type("R", (), {"data": []})()

    class _Table:
        def insert(self, p): return _Insert(p)
        def select(self, *_a, **_k): return _Select()
        def update(self, p): return _Update(p)

    with patch.object(email_otp, "_atable", lambda _n: _Table()):
        code_a = await email_otp.issue("student", "user_a", "signup_verify")
        ok_cross_user = await email_otp.verify("student", "user_b", "signup_verify", code_a)
        assert ok_cross_user is False, (
            "User isolation broken: user A's OTP was accepted for user B. "
            "An attacker who knows ANY user's OTP could pwn ANY other "
            "account."
        )

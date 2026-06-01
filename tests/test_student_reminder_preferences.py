"""Student exam-reminder preference regressions."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import shared_supabase_mock


def _student_account_headers(account_id="reminder-student-1", email="alice-reminders@test.com"):
    import jwt as jose_jwt
    from app.constants import STUDENT_SIGNING_KEY

    now = datetime.now(timezone.utc)
    token = jose_jwt.encode({
        "sid": account_id,
        "email": email,
        "role": "student_account",
        "iat": now,
        "exp": now + timedelta(hours=4),
    }, STUDENT_SIGNING_KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class _ReminderDB:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def __call__(self, table_name):
        return _Chain(self, table_name)


class _Chain:
    def __init__(self, db: _ReminderDB, table_name: str):
        self.db = db
        self.table_name = table_name
        self.eqs: dict[str, object] = {}
        self.ins: dict[str, set] = {}
        self.nulls: set[str] = set()
        self.payload = None
        self.op = "select"
        self._limit = None

    def select(self, *a, **kw): self.op = "select"; return self
    def eq(self, col, val): self.eqs[col] = val; return self
    def in_(self, col, vals): self.ins[col] = set(vals or []); return self
    def is_(self, col, val):
        if val == "null":
            self.nulls.add(col)
        return self
    def gte(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def limit(self, n): self._limit = n; return self
    def update(self, payload): self.op = "update"; self.payload = dict(payload); return self

    def _rows(self):
        rows = list(self.db.tables.get(self.table_name, []))
        out = []
        for row in rows:
            if any(str(row.get(k) or "") != str(v or "") for k, v in self.eqs.items()):
                continue
            if any(row.get(k) not in vals for k, vals in self.ins.items()):
                continue
            if any(row.get(k) is not None for k in self.nulls):
                continue
            out.append(row)
        if self._limit is not None:
            out = out[:self._limit]
        return out

    async def execute(self):
        rows = self._rows()
        if self.op == "update":
            for row in rows:
                row.update(self.payload or {})
        return MagicMock(data=rows, count=len(rows))


def test_student_can_read_and_update_exam_reminder_preference(client):
    db = _ReminderDB({
        "auth_sessions": [],
        "student_accounts": [{
            "id": "reminder-student-1",
            "email": "alice-reminders@test.com",
            "full_name": "Alice",
            "email_reminders_enabled": False,
        }],
        "auth_events": [],
    })
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        headers = _student_account_headers()
        get_resp = client.get("/api/v1/student/account/preferences", headers=headers)
        patch_resp = client.patch(
            "/api/v1/student/account/preferences",
            headers=headers,
            json={"email_reminders_enabled": True},
        )

    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json() == {"email_reminders_enabled": False}
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json() == {"email_reminders_enabled": True}
    assert db.tables["student_accounts"][0]["email_reminders_enabled"] is True


@pytest.mark.asyncio
async def test_reminder_worker_skips_email_when_student_preference_is_off():
    from app import reminders

    starts_at = datetime.now(timezone.utc) + timedelta(minutes=60)
    db = _ReminderDB({
        "exam_config": [{
            "exam_id": "exam-1",
            "teacher_id": "teacher-1",
            "exam_title": "Midterm",
            "starts_at": starts_at,
        }],
        "student_invites": [{
            "token": "invite-token",
            "email": "alice-reminders@test.com",
            "full_name": "Alice",
            "roll_number": "R1",
            "exam_id": "exam-1",
            "status": "sent",
            "reminder_1h_at": None,
            "reminder_24h_at": None,
        }],
        "student_accounts": [{
            "id": "reminder-student-1",
            "email": "alice-reminders@test.com",
            "email_reminders_enabled": False,
        }],
    })

    with patch.object(reminders, "_atable", side_effect=db), \
         patch.object(reminders, "_send_reminder_for_invite", return_value=True) as send_mock:
        await reminders._reminder_tick()

    assert send_mock.call_count == 0
    assert db.tables["student_invites"][0]["reminder_1h_at"] is not None

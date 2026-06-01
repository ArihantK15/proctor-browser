"""Tenant-boundary regressions for student registration and lobby flows."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from tests.conftest import shared_supabase_mock


def _student_account_headers(account_id="student-1", email="alice@test.com"):
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


class _TenantDB:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def __call__(self, table_name):
        return _Chain(self, table_name)


class _Chain:
    def __init__(self, db: _TenantDB, table_name: str):
        self.db = db
        self.table_name = table_name
        self.eqs: dict[str, object] = {}
        self.ins: dict[str, set] = {}
        self.nulls: set[str] = set()
        self.payload = None
        self.op = "select"
        self._limit = None
        self._order = None
        self._desc = False

    def select(self, *a, **kw): self.op = "select"; return self
    def eq(self, col, val): self.eqs[col] = val; return self
    def in_(self, col, vals): self.ins[col] = set(vals or []); return self
    def is_(self, col, val):
        if val == "null":
            self.nulls.add(col)
        return self
    def order(self, col, desc=False, **kw):
        self._order = col
        self._desc = bool(desc)
        return self
    def limit(self, n): self._limit = n; return self
    def update(self, payload): self.op = "update"; self.payload = dict(payload); return self
    def insert(self, payload): self.op = "insert"; self.payload = payload; return self
    def upsert(self, payload, **kw): self.op = "insert"; self.payload = payload; return self
    def delete(self): self.op = "delete"; return self

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
        if self._order:
            out.sort(key=lambda r: str(r.get(self._order) or ""), reverse=self._desc)
        if self._limit is not None:
            out = out[:self._limit]
        return out

    async def execute(self):
        rows = self._rows()
        if self.op == "update":
            for row in rows:
                row.update(self.payload or {})
            return MagicMock(data=rows)
        if self.op == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            self.db.tables.setdefault(self.table_name, []).extend(dict(p) for p in payloads)
            return MagicMock(data=payloads)
        return MagicMock(data=rows, count=len(rows))


def test_student_lobby_only_lists_enrollments_bound_to_that_account(client):
    """Same email under another account must not bleed into this lobby."""
    db = _TenantDB({
        "auth_sessions": [],
        "student_accounts": [{
            "id": "student-1",
            "email": "alice@test.com",
            "full_name": "Alice",
        }],
        "students": [
            {
                "roll_number": "A1",
                "teacher_id": "teacher-1",
                "exam_id": "exam-1",
                "email": "alice@test.com",
                "account_id": "student-1",
            },
            {
                "roll_number": "B2",
                "teacher_id": "teacher-2",
                "exam_id": "exam-2",
                "email": "alice@test.com",
                "account_id": "student-2",
            },
        ],
        "teachers": [
            {"id": "teacher-1", "full_name": "Teacher One"},
            {"id": "teacher-2", "full_name": "Teacher Two"},
        ],
        "exam_config": [
            {"teacher_id": "teacher-1", "exam_id": "exam-1", "exam_title": "Owned Exam"},
            {"teacher_id": "teacher-2", "exam_id": "exam-2", "exam_title": "Other Exam"},
        ],
        "exam_sessions": [],
    })
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.get("/api/student/exams", headers=_student_account_headers())

    assert resp.status_code == 200, resp.text
    exams = resp.json()["exams"]
    assert [e["exam_id"] for e in exams] == ["exam-1"]
    assert exams[0]["teacher_id"] == "teacher-1"


def test_student_lobby_claims_unlinked_roster_row_for_matching_account_email(client):
    db = _TenantDB({
        "auth_sessions": [],
        "student_accounts": [{
            "id": "student-1",
            "email": "alice@test.com",
            "full_name": "Alice",
        }],
        "students": [{
            "roll_number": "A1",
            "teacher_id": "teacher-1",
            "exam_id": "exam-1",
            "email": "alice@test.com",
            "account_id": None,
        }],
        "teachers": [{"id": "teacher-1", "full_name": "Teacher One"}],
        "exam_config": [{
            "teacher_id": "teacher-1",
            "exam_id": "exam-1",
            "exam_title": "Claimed Exam",
        }],
        "exam_sessions": [],
    })
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.get("/api/student/exams", headers=_student_account_headers())

    assert resp.status_code == 200, resp.text
    assert resp.json()["exams"][0]["exam_id"] == "exam-1"
    assert db.tables["students"][0]["account_id"] == "student-1"


def test_public_registration_rejects_exam_not_owned_by_teacher(client):
    db = _TenantDB({
        "teachers": [{"id": "teacher-1", "full_name": "Teacher One"}],
        "exam_config": [{"teacher_id": "teacher-2", "exam_id": "exam-foreign"}],
        "students": [],
    })
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.post("/api/v1/register-student", json={
            "teacher_id": "teacher-1",
            "exam_id": "exam-foreign",
            "roll_number": "A1",
            "full_name": "Alice",
            "email": "alice@test.com",
        })

    assert resp.status_code == 404, resp.text
    assert db.tables["students"] == []


def test_exam_launch_rejects_teacher_exam_mismatch(client):
    db = _TenantDB({
        "exam_config": [{"teacher_id": "teacher-2", "exam_id": "exam-foreign"}],
    })
    sm = shared_supabase_mock()
    with patch.object(sm, "table", side_effect=db):
        resp = client.post("/api/v1/validate-student", json={
            "teacher_id": "teacher-1",
            "exam_id": "exam-foreign",
            "roll_number": "A1",
            "access_code": "",
        })

    assert resp.status_code == 403, resp.text

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, name: str, db: "_FakeIssueDb"):
        self.name = name
        self.db = db
        self.op = "select"
        self.payload = None
        self.filters: list[tuple[str, str, object]] = []
        self._limit = None

    def select(self, *_args):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field, values):
        self.filters.append(("in", field, {str(v) for v in values}))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self._limit = value
        return self

    async def execute(self):
        if self.op == "insert":
            row = {
                "id": f"issue-{len(self.db.tables['issues']) + 1}",
                "created_at": "2026-05-24T00:00:00+00:00",
                "resolved_at": None,
                "superadmin_note": None,
                **self.payload,
            }
            self.db.tables["issues"].append(row)
            return _Result([deepcopy(row)])

        rows = [deepcopy(r) for r in self.db.tables.get(self.name, [])]
        for op, field, value in self.filters:
            if op == "eq":
                rows = [r for r in rows if str(r.get(field)) == str(value)]
            elif op == "in":
                rows = [r for r in rows if str(r.get(field)) in value]

        if self.op == "update":
            updated = []
            for row in self.db.tables.get(self.name, []):
                if all(str(row.get(field)) == str(value) for op, field, value in self.filters if op == "eq"):
                    row.update(self.payload)
                    updated.append(deepcopy(row))
            return _Result(updated)

        if self._limit is not None:
            rows = rows[: self._limit]
        return _Result(rows)


class _FakeIssueDb:
    def __init__(self):
        self.tables = {
            "issues": [
                {
                    "id": "issue-existing",
                    "org_id": "org-1",
                    "teacher_id": "teacher-1",
                    "session_id": "ROLL_abc",
                    "exam_id": "exam-1",
                    "category": "bug",
                    "severity": "normal",
                    "description": "Existing issue with enough context",
                    "status": "open",
                    "superadmin_note": "",
                    "created_at": "2026-05-24T00:00:00+00:00",
                    "resolved_at": None,
                }
            ],
            "organizations": [{"id": "org-1", "name": "Procta Test Org"}],
            "teachers": [{"id": "teacher-1", "full_name": "Teacher One", "email": "teacher@procta.test"}],
        }

    def table(self, name: str):
        return _FakeTable(name, self)


def _teacher(role="teacher"):
    return {
        "id": "teacher-1",
        "email": "teacher@procta.test",
        "full_name": "Teacher One",
        "org_id": "org-1",
        "org_role": role,
    }


def _patch_auth_and_db(monkeypatch, db: _FakeIssueDb, role="teacher"):
    from app.routers import issues

    async def fake_require_admin(_request):
        return _teacher(role)

    monkeypatch.setattr(issues, "require_admin", fake_require_admin)
    monkeypatch.setattr(issues, "_atable", db.table)


def test_teacher_can_create_and_list_own_issues(client, monkeypatch, admin_headers):
    db = _FakeIssueDb()
    _patch_auth_and_db(monkeypatch, db, role="teacher")

    resp = client.post(
        "/api/v1/issues",
        headers=admin_headers,
        json={
            "category": "session-issue",
            "severity": "high",
            "description": "Student video froze during the live proctoring review.",
            "session_id": "ROLL001_xyz",
            "exam_id": "exam-1",
        },
    )

    assert resp.status_code == 200
    created = resp.json()["issue"]
    assert created["category"] == "session-issue"
    assert created["severity"] == "high"
    assert created["teacher_id"] == "teacher-1"
    assert created["org_id"] == "org-1"

    mine = client.get("/api/v1/issues/mine", headers=admin_headers)
    assert mine.status_code == 200
    ids = {i["id"] for i in mine.json()["issues"]}
    assert created["id"] in ids
    assert "issue-existing" in ids


def test_org_admin_cannot_access_superadmin_issues(client, monkeypatch, admin_headers):
    db = _FakeIssueDb()
    _patch_auth_and_db(monkeypatch, db, role="admin")

    resp = client.get("/api/v1/admin/issues", headers=admin_headers)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Super admin access required"


def test_superadmin_can_list_and_update_issues(client, monkeypatch, admin_headers):
    db = _FakeIssueDb()
    _patch_auth_and_db(monkeypatch, db, role="superadmin")

    listed = client.get("/api/v1/admin/issues?status=open", headers=admin_headers)
    assert listed.status_code == 200
    issue = listed.json()["issues"][0]
    assert issue["id"] == "issue-existing"
    assert issue["org_name"] == "Procta Test Org"
    assert issue["teacher_email"] == "teacher@procta.test"
    assert listed.json()["open_count"] == 1

    patched = client.patch(
        "/api/v1/admin/issues/issue-existing",
        headers=admin_headers,
        json={"status": "triaged", "superadmin_note": "Queued for product review."},
    )

    assert patched.status_code == 200
    updated = patched.json()["issue"]
    assert updated["status"] == "triaged"
    assert updated["superadmin_note"] == "Queued for product review."


def test_issue_validation_rejects_bad_payloads(client, monkeypatch, admin_headers):
    db = _FakeIssueDb()
    _patch_auth_and_db(monkeypatch, db, role="teacher")

    bad_category = client.post(
        "/api/v1/issues",
        headers=admin_headers,
        json={"category": "billing", "severity": "normal", "description": "This is long enough to pass validation."},
    )
    assert bad_category.status_code == 400

    short_description = client.post(
        "/api/v1/issues",
        headers=admin_headers,
        json={"category": "bug", "severity": "normal", "description": "Too short"},
    )
    assert short_description.status_code == 400

    _patch_auth_and_db(monkeypatch, db, role="superadmin")
    bad_status = client.patch(
        "/api/v1/admin/issues/issue-existing",
        headers=admin_headers,
        json={"status": "done"},
    )
    assert bad_status.status_code == 400


def test_issues_migration_is_idempotent_and_indexed():
    sql = Path("migrations/phase70_issues.sql").read_text()

    assert "CREATE TABLE IF NOT EXISTS issues" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_issues_status_partial" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_issues_org" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_issues_teacher" in sql
    assert re.search(r"teacher_id\s+UUID NOT NULL REFERENCES teachers\(id\)", sql)
    assert re.search(r"org_id\s+UUID NOT NULL REFERENCES organizations\(id\)", sql)

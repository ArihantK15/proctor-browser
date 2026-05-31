from unittest.mock import AsyncMock, patch


class _Result:
    def __init__(self, data=None):
        self.data = data or []


class _Table:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.op = ""
        self.payload = None
        self.filters = {}

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def execute(self):
        if self.op == "select":
            if self.name == "student_accounts" and self.filters.get("id") == "student-1":
                return _Result([self.db.account])
            if self.name == "students":
                return _Result(self.db.students)
            if self.name == "exam_sessions":
                return _Result(self.db.sessions)
            return _Result([])
        if self.op == "update":
            self.db.updates.append((self.name, dict(self.payload), dict(self.filters)))
            return _Result([])
        if self.op == "delete":
            self.db.deletes.append((self.name, dict(self.filters)))
            return _Result([])
        return _Result([])


class _Db:
    def __init__(self):
        self.account = {
            "id": "student-1",
            "email": "student@example.com",
            "full_name": "Student One",
            "supabase_uid": "",
            "email_verified_at": "2026-05-31T00:00:00+05:30",
        }
        self.students = [{
            "teacher_id": "teacher-1",
            "full_name": "Student One",
            "email": "student@example.com",
            "roll_number": "R001",
            "created_at": "2026-05-31T00:00:00+05:30",
        }]
        self.sessions = [{
            "teacher_id": "teacher-1",
            "full_name": "Student One",
            "email": "student@example.com",
            "roll_number": "R001",
            "created_at": "2026-05-31T00:00:00+05:30",
        }]
        self.updates = []
        self.deletes = []

    def table(self, name):
        return _Table(self, name)


def test_student_delete_request_sends_account_delete_otp(client, student_headers):
    account = {"id": "student-1", "email": "student@example.com", "full_name": "Student"}
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.services.email_otp.issue", new=AsyncMock(return_value="123456")) as issue_mock:
        resp = client.post("/api/v1/student/account/delete-request", headers=student_headers)

    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    issue_mock.assert_awaited_once_with("student", "student-1", "account_delete")


def test_student_delete_confirm_rejects_wrong_code(client, student_headers):
    account = {"id": "student-1", "email": "student@example.com", "full_name": "Student"}
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=False)):
        resp = client.post("/api/v1/student/account/delete-confirm", headers=student_headers, json={
            "otp_code": "123456",
        })

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Invalid or expired code"


def test_student_delete_confirm_anonymises_evidence_and_deletes_account(client, student_headers):
    account = {"id": "student-1", "email": "student@example.com", "full_name": "Student"}
    db = _Db()
    teacher = {"id": "teacher-1", "email": "teacher@example.com", "full_name": "Teacher"}
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=True)), \
         patch("app.routers.auth._atable", db.table), \
         patch("app.routers.auth._get_teacher_by_id", new=AsyncMock(return_value=teacher)), \
         patch("app.routers.auth.record_auth_event", new=AsyncMock()):
        resp = client.post("/api/v1/student/account/delete-confirm", headers=student_headers, json={
            "otp_code": "123456",
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert any(name == "exam_sessions" and payload["full_name"] == "Deleted User"
               for name, payload, _filters in db.updates)
    assert any(name == "appeals" and payload["student_id"]
               for name, payload, _filters in db.updates)
    assert ("student_accounts", {"id": "student-1"}) in db.deletes
    assert ("students", {"account_id": "student-1"}) in db.deletes
    assert ("consent_records", {"user_id": "student-1"}) in db.deletes


def test_account_delete_otp_cannot_be_reused_for_email_change(client, student_headers):
    account = {"id": "student-1", "email": "student@example.com", "full_name": "Student"}
    db = _Db()
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.routers.auth._atable", db.table), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=False)) as verify_mock:
        resp = client.post("/api/v1/student/account/email-change-confirm", headers=student_headers, json={
            "new_email": "new@example.com",
            "code": "123456",
        })

    assert resp.status_code == 403
    verify_mock.assert_awaited_once_with("student", "student-1", "email_change", "123456")

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

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def execute(self):
        if self.op == "select":
            if self.name == "student_accounts" and self.filters.get("email") == self.db.existing_email:
                return _Result([{"id": "other-account"}])
            if self.name == "student_accounts" and self.filters.get("id") == "student-1":
                return _Result([{"supabase_uid": ""}])
            return _Result([])
        if self.op == "update":
            self.db.updates.append((self.name, dict(self.payload), dict(self.filters)))
        return _Result([])


class _Db:
    def __init__(self, existing_email=""):
        self.existing_email = existing_email
        self.updates = []

    def table(self, name):
        return _Table(self, name)


def test_student_email_change_request_requires_reauth_and_sends_code(client):
    account = {"id": "student-1", "email": "old@example.com", "full_name": "Student"}
    db = _Db()
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.auth.admin_auth.require_reauth_or_403") as reauth_mock, \
         patch("app.routers.auth._atable", db.table), \
         patch("app.services.email_otp.issue", new=AsyncMock(return_value="123456")) as issue_mock:
        resp = client.post("/api/v1/student/account/email-change-request", json={
            "new_email": "new@example.com",
            "reauth_token": "fresh",
        })

    assert resp.status_code == 200
    assert resp.json()["sent"] is True
    reauth_mock.assert_called_once()
    issue_mock.assert_awaited_once_with("student", "student-1", "email_change:new@example.com")


def test_student_email_change_confirm_updates_account_and_roster(client):
    account = {"id": "student-1", "email": "old@example.com", "full_name": "Student"}
    db = _Db()
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.routers.auth._atable", db.table), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=True)) as verify_mock:
        resp = client.post("/api/v1/student/account/email-change-confirm", json={
            "new_email": "new@example.com",
            "code": "123456",
        })

    assert resp.status_code == 200
    assert resp.json()["email"] == "new@example.com"
    verify_mock.assert_awaited_once_with("student", "student-1", "email_change:new@example.com", "123456")
    assert ("student_accounts", {"email": "new@example.com", "updated_at": db.updates[0][1]["updated_at"]}, {"id": "student-1"}) in db.updates
    assert ("students", {"email": "new@example.com"}, {"account_id": "student-1"}) in db.updates
    assert ("students", {"email": "new@example.com"}, {"email": "old@example.com"}) in db.updates


def test_student_email_change_rejects_existing_email(client):
    account = {"id": "student-1", "email": "old@example.com", "full_name": "Student"}
    db = _Db(existing_email="used@example.com")
    with patch("app.routers.auth.require_student_account", new=AsyncMock(return_value=account)), \
         patch("app.auth.admin_auth.require_reauth_or_403"), \
         patch("app.routers.auth._atable", db.table):
        resp = client.post("/api/v1/student/account/email-change-request", json={
            "new_email": "used@example.com",
            "reauth_token": "fresh",
        })

    assert resp.status_code == 409

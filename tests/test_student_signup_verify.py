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

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def is_(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def execute(self):
        if self.name == "student_accounts" and self.op == "select":
            if self.db.existing_account:
                return _Result([self.db.existing_account])
            return _Result([])
        if self.name == "student_accounts" and self.op == "insert":
            self.db.inserted_account = {"id": "student-1", **self.payload}
            return _Result([self.db.inserted_account])
        if self.op == "update":
            self.db.updates.append((self.name, dict(self.payload), dict(self.filters)))
        return _Result([])


class _Db:
    def __init__(self, existing_account=None):
        self.existing_account = existing_account
        self.inserted_account = None
        self.updates = []

    def table(self, name):
        return _Table(self, name)


def test_student_signup_creates_pending_account_and_sends_otp(client):
    db = _Db()
    with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
         patch("app.routers.auth.local_password_auth_enabled", return_value=True), \
         patch("app.routers.auth.hash_password", new=AsyncMock(return_value="hash")), \
         patch("app.routers.auth.new_auth_uid", return_value="uid-1"), \
         patch("app.routers.auth._atable", db.table), \
         patch("app.routers.auth._track_a_issue_signup_otp", new=AsyncMock()) as issue_mock:
        resp = client.post("/api/v1/student/auth/signup", json={
            "email": "student@example.com",
            "full_name": "Student One",
            "password": "StrongPass1!",
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["verify_required"] is True
    assert "access_token" not in body
    assert db.inserted_account["email_verified_at"] is None
    issue_mock.assert_awaited_once()


def test_student_signup_existing_email_409_even_with_weak_password(client):
    """A returning student must get a clean 409 regardless of password
    strength. The existence check runs BEFORE validate_password so a
    legacy/sub-policy password never masks the 409 with a 400 — the
    registration page relies on this 409 to auto-detect an existing account."""
    db = _Db(existing_account={"id": "student-1"})
    with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
         patch("app.routers.auth.local_password_auth_enabled", return_value=True), \
         patch("app.routers.auth._atable", db.table), \
         patch("app.routers.auth._track_a_issue_signup_otp", new=AsyncMock()) as issue_mock:
        resp = client.post("/api/v1/student/auth/signup", json={
            "email": "student@example.com",
            "full_name": "Student One",
            "password": "short",  # deliberately fails the strength policy
        })

    assert resp.status_code == 409
    # No account row was inserted and no OTP was sent for an existing email.
    assert db.inserted_account is None
    issue_mock.assert_not_awaited()


def test_student_login_blocks_unverified_account(client):
    account = {
        "id": "student-1",
        "email": "student@example.com",
        "full_name": "Student",
        "password_hash": "hash",
        "email_verified_at": None,
        "password_changed_at": None,
    }
    with patch("app.routers.auth.verify_or_403", new=AsyncMock()), \
         patch("app.routers.auth.check_lockout", new=AsyncMock(return_value=(False, 0))), \
         patch("app.routers.auth.local_password_auth_enabled", return_value=True), \
         patch("app.routers.auth._get_student_by_email_for_auth", new=AsyncMock(return_value=account)), \
         patch("app.routers.auth._track_a_hydrate_student_account", new=AsyncMock(return_value=account)), \
         patch("app.routers.auth.verify_password", new=AsyncMock(return_value=True)), \
         patch("app.routers.auth.record_auth_event", new=AsyncMock()):
        resp = client.post("/api/v1/student/auth/login", json={
            "email": "student@example.com",
            "password": "StrongPass1!",
        })

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"


def test_verify_signup_otp_marks_account_verified(client):
    account = {
        "id": "student-1",
        "email": "student@example.com",
        "full_name": "Student",
        "email_verified_at": None,
    }
    db = _Db(existing_account=account)
    with patch("app.routers.auth._get_student_by_email_for_auth", new=AsyncMock(return_value=account)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=True)) as verify_mock, \
         patch("app.routers.auth._atable", db.table), \
         patch("app.routers.auth.record_auth_event", new=AsyncMock()):
        resp = client.post("/api/v1/student/auth/verify-signup-otp", json={
            "email": "student@example.com",
            "code": "123456",
        })

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    verify_mock.assert_awaited_once_with("student", "student-1", "signup_verify", "123456")
    assert db.updates[0][0] == "student_accounts"
    assert db.updates[0][2] == {"id": "student-1"}


def test_verify_signup_otp_rejects_wrong_code(client):
    account = {"id": "student-1", "email": "student@example.com", "email_verified_at": None}
    with patch("app.routers.auth._get_student_by_email_for_auth", new=AsyncMock(return_value=account)), \
         patch("app.services.email_otp.verify", new=AsyncMock(return_value=False)):
        resp = client.post("/api/v1/student/auth/verify-signup-otp", json={
            "email": "student@example.com",
            "code": "000000",
        })

    assert resp.status_code == 403

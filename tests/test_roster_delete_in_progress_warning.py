from unittest.mock import AsyncMock, patch


class _Result:
    def __init__(self, data=None):
        self.data = data or []


class _Table:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.op = ""
        self.filters = {}
        self.in_filters = {}

    def select(self, *_args, **_kwargs):
        self.op = "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, value):
        self.in_filters[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def execute(self):
        if self.name == "students" and self.op == "select":
            rows = list(self.db.students)
            for key, value in self.filters.items():
                rows = [r for r in rows if r.get(key) == value]
            return _Result(rows)
        if self.name == "exam_sessions" and self.op == "select":
            rows = list(self.db.sessions)
            for key, value in self.filters.items():
                rows = [r for r in rows if r.get(key) == value]
            return _Result(rows)
        if self.name == "students" and self.op == "delete":
            self.db.deleted_ids.extend(self.in_filters.get("id", []))
            return _Result([])
        return _Result([])


class _Db:
    def __init__(self):
        self.students = [{
            "id": "row-1",
            "teacher_id": "teacher-1",
            "roll_number": "R001",
            "email": "student@example.com",
            "exam_id": "exam-1",
        }]
        self.sessions = [{
            "session_key": "R001_teacher-1",
            "teacher_id": "teacher-1",
            "roll_number": "R001",
            "email": "student@example.com",
            "full_name": "Student One",
            "exam_id": "exam-1",
            "status": "in_progress",
        }]
        self.deleted_ids = []

    def table(self, name):
        return _Table(self, name)


def test_roster_delete_returns_warning_before_deleting(client, admin_headers):
    db = _Db()
    with patch("app.routers.admin_students.require_admin", new=AsyncMock(return_value={"id": "teacher-1"})), \
         patch("app.routers.admin_students._atable", db.table):
        resp = client.delete(
            "/api/v1/admin/students/roster?roll_number=R001&exam_id=exam-1",
            headers=admin_headers,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 0
    assert body["needs_confirmation"] is True
    assert body["warnings"][0]["code"] == "in_progress_session"
    assert db.deleted_ids == []


def test_roster_delete_confirm_warnings_deletes(client, admin_headers):
    db = _Db()
    with patch("app.routers.admin_students.require_admin", new=AsyncMock(return_value={"id": "teacher-1"})), \
         patch("app.routers.admin_students._atable", db.table):
        resp = client.delete(
            "/api/v1/admin/students/roster?roll_number=R001&exam_id=exam-1&confirm_warnings=true",
            headers=admin_headers,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["warnings"][0]["session_key"] == "R001_teacher-1"
    assert db.deleted_ids == ["row-1"]

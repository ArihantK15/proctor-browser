"""Tests for exam admin endpoints that lacked coverage.

Covers:
  1. ``duplicate_exam`` (POST .../exams/{exam_id}/duplicate)
"""

import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

from tests.conftest import make_admin_token, shared_supabase_mock  # noqa: E402


# ─── Helpers ─────────────────────────────────────────────────────────

TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T", "org_id": "org-1"}
SRC_EXAM = {
    "exam_id": "exam-1", "exam_title": "Midterm",
    "duration_minutes": 60, "shuffle_questions": False,
    "shuffle_options": True, "starts_at": None, "ends_at": None,
    "access_code": "ABC", "teacher_id": "teacher-1",
}
SRC_QUESTIONS = [
    {"id": "q1", "question_id": 1, "exam_id": "exam-1", "teacher_id": "teacher-1",
     "question": "Q1?", "options": ["A", "B", "C", "D"], "correct": "A",
     "order_index": 1, "created_at": "...", "updated_at": "..."},
    {"id": "q2", "question_id": 2, "exam_id": "exam-1", "teacher_id": "teacher-1",
     "question": "Q2?", "options": ["A", "B", "C", "D"], "correct": "B",
     "order_index": 2, "created_at": "...", "updated_at": "..."},
]


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _table_side_effect(mapping):
    def _build_chain(data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like", "count"):
            getattr(m, attr).return_value = m

        async def _execute():
            return MagicMock(data=data)

        m.execute = _execute
        return m

    def _side_effect(name):
        return _build_chain(mapping.get(name, []))

    return _side_effect


# ═══════════════════════════════════════════════════════════════════
#  DELETE /api/v1/admin/exams/{exam_id}
# ═══════════════════════════════════════════════════════════════════


class TestDeleteExam:
    DEL_HEADERS = {
        **_admin_headers(),
        "X-Reauth-Token": "test-reauth-token",
    }

    @staticmethod
    def _mock_chain(data=None, count=None):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like"):
            getattr(m, attr).return_value = m

        async def _execute():
            r = MagicMock()
            r.data = data if data is not None else []
            r.count = count
            return r

        m.execute = _execute
        return m

    @staticmethod
    def _mk_patch(data_map):
        def _side_effect(name):
            cfg = data_map.get(name, {})
            return TestDeleteExam._mock_chain(
                data=cfg.get("data", []),
                count=cfg.get("count"),
            )
        return patch("app.routers.admin_exams._atable", side_effect=_side_effect)

    def test_active_sessions_block_deletion(self, client):
        # exam_config found, >=2 exams, but exam_sessions has active ones.
        from unittest.mock import patch as _mpatch
        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}
        with _mpatch("app.auth.admin_auth.require_reauth_or_403"), \
             _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._cache"), \
             self._mk_patch({
                 "exam_config": {"data": [{"exam_id": "exam-1"}]},
                 "exam_sessions": {"count": 3},
             }):
            resp = client.delete("/api/v1/admin/exams/exam-1",
                                 headers=self.DEL_HEADERS)
        assert resp.status_code == 409
        assert "active sessions" in resp.json().get("detail", "").lower()

    def test_happy_path_deletes_exam(self, client):
        from unittest.mock import patch as _mpatch
        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}
        with _mpatch("app.auth.admin_auth.require_reauth_or_403"), \
             _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._cache"), \
             _mpatch("app.services.admin_audit.log_admin_action"), \
             self._mk_patch({
                 "exam_config": {"data": [
                     {"exam_id": "exam-1", "exam_title": "Midterm"},
                     {"exam_id": "exam-2", "exam_title": "Final"},
                 ]},
                 "exam_sessions": {"count": 0},
                 "questions": {"data": []},
             }):
            resp = client.delete("/api/v1/admin/exams/exam-1",
                                 headers=self.DEL_HEADERS)
        assert resp.status_code == 200
        assert resp.json().get("status") == "deleted"

    def test_delete_exam_also_deletes_invites(self, client):
        """Regression: deleting an exam must also delete its per-exam
        student_invites, else they're orphaned (ghost lobby entry / inflated
        registered counts)."""
        from unittest.mock import patch as _mpatch, MagicMock
        deleted_tables = []

        def _atable(name):
            chain = MagicMock()
            chain.select.side_effect = lambda *a, **k: chain
            chain.eq.side_effect = lambda *a, **k: chain
            chain.in_.side_effect = lambda *a, **k: chain

            def _delete(*a, **k):
                deleted_tables.append(name)
                return chain
            chain.delete.side_effect = _delete

            async def _execute():
                r = MagicMock()
                r.data = ([{"exam_id": "exam-1"}, {"exam_id": "exam-2"}]
                          if name == "exam_config" else [])
                r.count = 0
                return r
            chain.execute.side_effect = _execute
            return chain

        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}
        with _mpatch("app.auth.admin_auth.require_reauth_or_403"), \
             _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._cache"), \
             _mpatch("app.services.admin_audit.log_admin_action"), \
             _mpatch("app.routers.admin_exams._atable", side_effect=_atable):
            resp = client.delete("/api/v1/admin/exams/exam-1",
                                 headers=self.DEL_HEADERS)
        assert resp.status_code == 200
        assert "questions" in deleted_tables
        assert "student_invites" in deleted_tables   # the orphan fix
        assert "exam_config" in deleted_tables


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/exams/pass-mark
# ═══════════════════════════════════════════════════════════════════


class TestSetPassMark:

    def test_sets_pass_mark(self, client):
        from unittest.mock import MagicMock, patch as _mpatch
        m = MagicMock()
        for attr in ("update", "eq"):
            getattr(m, attr).return_value = m
        async def _fake_execute():
            return MagicMock()
        m.execute = _fake_execute
        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}
        with _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._atable", return_value=m):
            resp = client.post("/api/v1/admin/exams/pass-mark",
                               json={"exam_id": "exam-1", "pass_mark": 60},
                               headers=TestDeleteExam.DEL_HEADERS)
        assert resp.status_code == 200
        assert resp.json().get("pass_mark") == 60

    def test_rejects_invalid_range(self, client):
        from unittest.mock import patch as _mpatch
        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}
        with _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin):
            resp = client.post("/api/v1/admin/exams/pass-mark",
                               json={"exam_id": "exam-1", "pass_mark": 150},
                               headers=TestDeleteExam.DEL_HEADERS)
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/exams/{exam_id}/duplicate
# ═══════════════════════════════════════════════════════════════════


class TestDuplicateExam:
    """Clone an exam together with its questions."""

    def test_source_exam_not_found_404(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [],  # no source exam
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 404
        assert "not found" in resp.json().get("detail", "").lower()

    def test_duplicate_with_questions(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [SRC_EXAM],
            "questions": SRC_QUESTIONS,
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "duplicated"
        assert body["source_exam_id"] == "exam-1"
        assert body["questions_copied"] == 2
        assert body["exam_id"] != "exam-1"
        # Default title appends " (copy)"
        assert "(copy)" in body["exam_title"]

    def test_duplicate_with_custom_title(self, client):
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [SRC_EXAM],
            "questions": SRC_QUESTIONS,
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={"new_title": "Custom Clone"},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["exam_title"] == "Custom Clone"

    def test_duplicate_copies_grading_and_proctoring_settings(self, client):
        """A duplicate must carry the source's pass_mark / proctoring sensitivity
        / phone-cam / audio settings, not silently reset them to column defaults.
        Per-instance fields (schedule, exam_id) reset; access_code gets a fresh
        value of its own rather than being copied or left empty."""
        from unittest.mock import patch as _mpatch, MagicMock
        src = {
            "exam_id": "exam-1", "exam_title": "Midterm", "duration_minutes": 90,
            "shuffle_questions": True, "shuffle_options": True,
            "access_code": "ABC", "teacher_id": "teacher-1", "starts_at": "2026-01-01T00:00:00Z",
            "pass_mark": 70, "proctoring_sensitivity": "strict",
            "phone_camera_enabled": True, "audio_keywords": "bomb,gun",
            "audio_keywords_language": "en+hi",
        }
        captured = {}

        def _atable(name):
            chain = MagicMock()
            chain.select.side_effect = lambda *a, **k: chain
            chain.eq.side_effect = lambda *a, **k: chain
            chain.order.side_effect = lambda *a, **k: chain

            def _insert(payload, *a, **k):
                if name == "exam_config":
                    captured["cfg"] = payload
                return chain
            chain.insert.side_effect = _insert

            async def _execute():
                r = MagicMock()
                r.data = [src] if name == "exam_config" else []
                return r
            chain.execute.side_effect = _execute
            return chain

        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}
        with _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._cache"), \
             _mpatch("app.routers.admin_exams._atable", side_effect=_atable):
            resp = client.post("/api/v1/admin/exams/exam-1/duplicate",
                               json={}, headers=_admin_headers())
        assert resp.status_code == 200, resp.text
        cfg = captured["cfg"]
        assert cfg["pass_mark"] == 70
        assert cfg["proctoring_sensitivity"] == "strict"
        assert cfg["phone_camera_enabled"] is True
        assert cfg["audio_keywords"] == "bomb,gun"
        assert cfg["audio_keywords_language"] == "en+hi"
        # per-instance fields reset, not copied — access_code is compulsory
        # for every exam, so a duplicate gets its OWN freshly generated
        # code rather than an empty one or the source's code.
        assert cfg["access_code"] and cfg["access_code"] != src.get("access_code")
        assert cfg["starts_at"] is None
        assert cfg["exam_id"] != "exam-1"

    def test_duplicate_with_no_questions(self, client):
        """An exam with zero questions should still clone the config."""
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
            "teachers": [TEACHER],
            "exam_config": [SRC_EXAM],
            "questions": [],
        })):
            resp = client.post(
                "/api/v1/admin/exams/exam-1/duplicate",
                json={},
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["questions_copied"] == 0


# ═══════════════════════════════════════════════════════════════════
#  POST /api/v1/admin/exams (idempotency)
# ═══════════════════════════════════════════════════════════════════


class TestCreateExamIdempotency:

    def test_idempotency_key_returns_cached(self, client):
        from unittest.mock import MagicMock, patch as _mpatch
        cache_store = {}
        mock_cache = MagicMock()
        def _cached_get(key):
            return cache_store.get(key)
        def _cached_set(key, value, ttl=None):
            cache_store[key] = value
        mock_cache.get.side_effect = _cached_get
        mock_cache.set.side_effect = _cached_set

        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}

        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like"):
            getattr(m, attr).return_value = m
        async def _fake_exec():
            r = MagicMock()
            r.data = [{"exam_id": "exam-idem-1"}]
            return r
        m.execute = _fake_exec

        with _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._cache", mock_cache), \
             _mpatch("app.routers.admin_exams._atable", return_value=m):

            h = {**_admin_headers(), "Idempotency-Key": "idem-1"}
            r1 = client.post("/api/v1/admin/exams", json={"exam_title": "Test"}, headers=h)
            r2 = client.post("/api/v1/admin/exams", json={"exam_title": "Test"}, headers=h)

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Second call should return the cached response (same exam_id)
        assert r1.json()["exam_id"] == r2.json()["exam_id"]

    def test_no_idempotency_key_creates_new(self, client):
        from unittest.mock import MagicMock, patch as _mpatch
        call_count = 0

        async def _fake_admin(req):
            return {"id": "teacher-1", "org_id": "org-1"}

        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "insert", "upsert",
                     "update", "delete", "gte", "lte", "gt", "lt",
                     "like"):
            getattr(m, attr).return_value = m

        async def _fake_exec():
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.data = [{"exam_id": f"exam-{call_count}"}]
            return r
        m.execute = _fake_exec

        with _mpatch("app.routers.admin_exams.require_admin", side_effect=_fake_admin), \
             _mpatch("app.routers.admin_exams._atable", return_value=m):

            h = _admin_headers()
            r1 = client.post("/api/v1/admin/exams", json={"exam_title": "Test"}, headers=h)
            r2 = client.post("/api/v1/admin/exams", json={"exam_title": "Test"}, headers=h)

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Without idempotency key, each call creates a new exam (different IDs)
        assert r1.json()["exam_id"] != r2.json()["exam_id"]

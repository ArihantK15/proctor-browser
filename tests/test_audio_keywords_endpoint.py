"""
Tests for Phase 75 audio-keywords endpoints.

Contract:
  • GET  /api/v1/admin/audio-keywords?exam_id=… returns the saved
    list + language + supported language list.
  • POST /api/v1/admin/audio-keywords accepts a list of strings
    (each 2–80 chars, max 50 entries) + a language ∈ {en,hi,en+hi}.
    Strings are stripped, case-insensitive deduped. Bad input → 400
    with a useful detail; nothing written.

Mocks Supabase via the shared mock pattern lifted from
tests/test_session_intervention.py.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import shared_supabase_mock, make_admin_token  # noqa: E402


TEACHER = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof T"}


def _admin_headers():
    return {"Authorization": f"Bearer {make_admin_token(teacher_id='teacher-1', email='prof@test.com')}"}


def _exam_config_row(keywords=None, lang="en", **overrides):
    base = {
        "id": 1, "exam_id": "exam-A", "teacher_id": "teacher-1",
        "exam_title": "T", "duration_minutes": 60, "access_code": "",
        "starts_at": None, "ends_at": None,
        "shuffle_questions": True, "shuffle_options": True,
        "phone_camera_enabled": False, "proctoring_sensitivity": "balanced",
        "audio_keywords": (json.dumps(keywords) if keywords else None),
        "audio_keywords_language": lang,
        "created_at": "2026-05-29T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _table_side_effect(mapping, captured: dict):
    def _build_chain(name, data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "gte", "lte"):
            getattr(m, attr).return_value = m

        def _cap_update(row):
            captured.setdefault(f"{name}_update", row)
            return m
        m.update.side_effect = _cap_update
        m.delete.return_value = m

        async def _execute():
            if m.update.call_count:
                return MagicMock(data=[])
            return MagicMock(data=data)
        m.execute = _execute
        return m

    def _side(name):
        return _build_chain(name, mapping.get(name, []))
    return _side


# ─── GET ─────────────────────────────────────────────────────────────

class TestGetAudioKeywords:
    def test_returns_saved_keywords_and_language(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row(keywords=["option a", "the answer is"], lang="en+hi")],
             }, captured)):
            resp = client.get(
                "/api/v1/admin/audio-keywords?exam_id=exam-A",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["audio_keywords"] == ["option a", "the answer is"]
        assert body["audio_keywords_language"] == "en+hi"
        assert "en" in body["supported_languages"]
        assert body["max_keywords"] == 50

    def test_null_keywords_returns_empty_list(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row(keywords=None)],
             }, captured)):
            resp = client.get(
                "/api/v1/admin/audio-keywords?exam_id=exam-A",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["audio_keywords"] == []
        assert resp.json()["audio_keywords_language"] == "en"

    def test_corrupt_json_returns_empty(self, client):
        """A malformed value in audio_keywords (e.g. saved by hand) must
        not crash the read path. Empty list, not 500."""
        captured: dict = {}
        sm = shared_supabase_mock()
        row = _exam_config_row()
        row["audio_keywords"] = "{not valid json"
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [row],
             }, captured)):
            resp = client.get(
                "/api/v1/admin/audio-keywords?exam_id=exam-A",
                headers=_admin_headers(),
            )
        assert resp.status_code == 200
        assert resp.json()["audio_keywords"] == []


# ─── POST ────────────────────────────────────────────────────────────

class TestSetAudioKeywords:
    def test_valid_list_persists_as_json(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row()],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": ["option a", "the answer is", "  Periodic Table  "],
                    "audio_keywords_language": "en",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Stripped + preserved order
        assert body["audio_keywords"] == ["option a", "the answer is", "Periodic Table"]
        upd = captured.get("exam_config_update", {})
        assert upd.get("audio_keywords_language") == "en"
        # Stored as JSON string
        saved = json.loads(upd["audio_keywords"])
        assert saved == ["option a", "the answer is", "Periodic Table"]

    def test_case_insensitive_dedupe(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row()],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": ["Option A", "option a", "OPTION A", "answer"],
                    "audio_keywords_language": "en",
                },
            )
        assert resp.status_code == 200
        # First-seen wins (preserves teacher's casing for that one).
        assert resp.json()["audio_keywords"] == ["Option A", "answer"]

    def test_too_short_returns_400(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row()],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": ["a"],  # 1 char — below MIN_KEYWORD_LEN
                    "audio_keywords_language": "en",
                },
            )
        assert resp.status_code == 400
        assert "short" in resp.text.lower()
        assert "exam_config_update" not in captured

    def test_too_long_returns_400(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row()],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": ["x" * 100],  # > MAX_KEYWORD_LEN
                    "audio_keywords_language": "en",
                },
            )
        assert resp.status_code == 400
        assert "long" in resp.text.lower()

    def test_unknown_language_returns_400(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row()],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": ["option a"],
                    "audio_keywords_language": "klingon",
                },
            )
        assert resp.status_code == 400
        assert "language" in resp.text.lower()
        assert "exam_config_update" not in captured

    def test_too_many_entries_returns_400(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row()],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": [f"keyword {i}" for i in range(60)],
                    "audio_keywords_language": "en",
                },
            )
        assert resp.status_code == 400
        assert "many" in resp.text.lower()

    def test_empty_list_clears_back_to_defaults(self, client):
        captured: dict = {}
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "teachers":    [TEACHER],
                "exam_config": [_exam_config_row(keywords=["existing"])],
             }, captured)):
            resp = client.post(
                "/api/v1/admin/audio-keywords",
                headers=_admin_headers(),
                json={
                    "exam_id": "exam-A",
                    "audio_keywords": [],
                    "audio_keywords_language": "en",
                },
            )
        assert resp.status_code == 200
        upd = captured.get("exam_config_update", {})
        # NULL (not empty list) → reverts to built-in defaults at proctor launch
        assert upd.get("audio_keywords") is None

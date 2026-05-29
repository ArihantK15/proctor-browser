"""
Test for GET /api/v1/exam/audio-config — the student-authed endpoint
proctor.py hits at startup to pull the per-exam keyword list +
language. Mirrors the pattern used by test_audio_keywords_endpoint.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import shared_supabase_mock, make_student_token  # noqa: E402


def _exam_config_row(keywords=None, lang="en"):
    return {
        "id": 1, "exam_id": "exam-A", "teacher_id": "teacher-1",
        "exam_title": "T", "duration_minutes": 60, "access_code": "",
        "starts_at": None, "ends_at": None,
        "shuffle_questions": True, "shuffle_options": True,
        "phone_camera_enabled": False, "proctoring_sensitivity": "balanced",
        "audio_keywords": (json.dumps(keywords) if keywords else None),
        "audio_keywords_language": lang,
        "created_at": "2026-05-29T00:00:00+00:00",
    }


def _table_side_effect(mapping):
    def _build_chain(data):
        m = MagicMock()
        for attr in ("select", "eq", "neq", "is_", "in_", "order",
                     "limit", "single", "range", "gte", "lte"):
            getattr(m, attr).return_value = m
        async def _execute():
            return MagicMock(data=data)
        m.execute = _execute
        return m
    return lambda name: _build_chain(mapping.get(name, []))


class TestAudioConfigEndpoint:
    def test_returns_saved_config(self, client):
        token = make_student_token(roll="ALICE001", tid="teacher-1", eid="exam-A")
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "exam_config": [_exam_config_row(keywords=["option a", "newton"], lang="en+hi")],
             })):
            resp = client.get(
                "/api/v1/exam/audio-config?session_id=ALICE001_1",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["audio_keywords"] == ["option a", "newton"]
        assert body["audio_keywords_language"] == "en+hi"

    def test_null_keywords_returns_empty(self, client):
        token = make_student_token(roll="BOB", tid="teacher-1", eid="exam-A")
        sm = shared_supabase_mock()
        with patch.object(sm, "table", side_effect=_table_side_effect({
                "exam_config": [_exam_config_row(keywords=None, lang="en")],
             })):
            resp = client.get(
                "/api/v1/exam/audio-config?session_id=BOB_1",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert resp.json()["audio_keywords"] == []
        assert resp.json()["audio_keywords_language"] == "en"

    def test_practice_short_circuits(self, client):
        """Practice mode skips DB lookups + returns empty config.
        PRACTICE_PREFIX is 'PRACTICE_' (constants.py)."""
        resp = client.get("/api/v1/exam/audio-config?session_id=PRACTICE_001")
        # No auth header — practice path returns 200 without needing one.
        assert resp.status_code == 200
        assert resp.json()["audio_keywords"] == []

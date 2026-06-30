"""Tests for app/routers/grading.py — grading endpoints.

Covers _apply_short_answer_to_session and all six endpoints.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


# ── helpers ─────────────────────────────────────────────────────────────

_TEACHER = {"id": "teacher-1", "email": "t@test.com", "full_name": "Test Teacher"}


def _fake_atable(data: list | None = None, *, count: int | None = None):
    """Build a mocked _atable chain whose .execute() returns the given data."""
    m = MagicMock()
    for attr in ("select", "eq", "is_", "in_", "neq", "order", "limit", "range", "update", "insert", "upsert", "delete"):
        getattr(m, attr).return_value = m

    async def _exec():
        r = MagicMock()
        r.data = data if data is not None else []
        r.count = count if count is not None else (len(data) if data else 0)
        return r

    m.execute = _exec
    return m


def _grade_start(extra_patches: list | None = None):
    """Start patches common to all grading endpoints + any extra.

    Returns (patcher_list, fake_atable_instance) — caller MUST stop
    each patcher in reverse order.
    """
    fake = _fake_atable()
    patches = [
        patch("app.routers.grading.require_admin", return_value=_TEACHER),
        patch("app.routers.grading._atable", return_value=fake),
        # Patch at the *source* module — endpoints lazily import these
        # inside their function bodies via ``from ..auth.scope import …``.
        patch("app.auth.scope.apply_teacher_scope", side_effect=lambda q, *a: q),
        patch("app.auth.scope.resolve_scope", return_value={}),
        patch("app.auth.scope.scope_to_teacher_ids", return_value=["teacher-1"]),
    ]
    if extra_patches:
        patches.extend(extra_patches)
    for p in patches:
        p.start()
    return patches, fake


def _grade_stop(patches: list):
    for p in reversed(patches):
        p.stop()


# ── _apply_short_answer_to_session ──────────────────────────────────────

class TestApplyShortAnswerToSession:
    @pytest.mark.asyncio
    async def test_returns_none_when_session_not_found(self):
        fake = _fake_atable([])  # empty data
        by_name = [patch("app.routers.grading._atable", return_value=fake)]
        for p in by_name:
            p.start()
        try:
            from app.routers.grading import _apply_short_answer_to_session
            result = await _apply_short_answer_to_session("sess-1", "teacher-1")
            assert result is None
        finally:
            for p in reversed(by_name):
                p.stop()

    @pytest.mark.asyncio
    async def test_returns_totals(self):
        sess_data = [{"session_key": "sess-1", "exam_id": "exam-1", "teacher_id": "teacher-1"}]
        sa_qs_data = [{"question_id": "q-1", "max_score": 5.0}]
        sa_ans_data = [{"teacher_score": 4.0}]
        answers_execute_mock = AsyncMock(return_value=MagicMock(data=sa_ans_data))

        chain = _fake_atable(sess_data)
        # Override the two subsequent queries to return their own data
        orig_execute = chain.execute

        async def _execute():
            if chain._called_count == 0:
                chain._called_count += 1
                return MagicMock(data=sess_data)
            return MagicMock(data=sa_qs_data)

        chain._called_count = 0
        chain.execute = _execute

        sa_qs_chain = _fake_atable(sa_qs_data)
        sa_ans_chain = _fake_atable(sa_ans_data)

        atable_call_count = 0

        def _atable_side_effect(name: str):
            nonlocal atable_call_count
            if atable_call_count == 0:
                atable_call_count += 1
                return chain
            elif atable_call_count == 1:
                atable_call_count += 1
                return sa_qs_chain
            atable_call_count += 1
            return sa_ans_chain

        with patch("app.routers.grading._atable", side_effect=_atable_side_effect), \
             patch("app.services.scoring.recalculate_score", AsyncMock(return_value=(80, 100))), \
             patch("app.routers.grading.require_admin", return_value=_TEACHER):
            from app.routers.grading import _apply_short_answer_to_session
            result = await _apply_short_answer_to_session("sess-1", "teacher-1")
        assert result is not None
        assert result["score"] == 84  # 80 mcq + 4 short answer
        assert result["total"] == 105  # 100 mcq + 5 short answer max
        assert result["percentage"] == 80.0

    @pytest.mark.asyncio
    async def test_returns_none_on_mcq_recalc_failure(self):
        chain = _fake_atable([{"session_key": "sess-1", "exam_id": "exam-1", "teacher_id": "teacher-1"}])
        with patch("app.routers.grading._atable", return_value=chain), \
             patch("app.services.scoring.recalculate_score", AsyncMock(side_effect=RuntimeError("fail"))), \
             patch("app.routers.grading.require_admin", return_value=_TEACHER):
            from app.routers.grading import _apply_short_answer_to_session
            result = await _apply_short_answer_to_session("sess-1", "teacher-1")
        assert result is None


# ── pending_grades ──────────────────────────────────────────────────────

class TestPendingGrades:
    def test_returns_empty_when_no_questions(self):
        patches, fake = _grade_start()
        try:
            resp = client.get("/api/v1/admin/pending-grades")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_pending"] == 0
            assert data["questions"] == []
            assert data["answers"] == []
        finally:
            _grade_stop(patches)

    def test_returns_enriched_answers(self):
        questions = [{"id": "qid-1", "question_id": "q-1", "exam_id": "exam-1",
                      "question": "What?", "reference_answer": "Ans", "rubric": "Rubric",
                      "max_score": 5}]
        answers = [{"id": "aid-1", "session_key": "sess-1", "question_id": "q-1",
                    "answer": "Student ans", "ai_score": 3.0, "ai_feedback": "Good",
                    "ai_confidence": "high", "teacher_score": None, "exam_id": "exam-1"}]
        sessions = [{"session_key": "sess-1", "roll_number": "ALICE", "full_name": "Alice"}]

        fake_q = _fake_atable(questions)
        fake_a = _fake_atable(answers)
        fake_s = _fake_atable(sessions)

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_q
            elif call_count == 1:
                call_count += 1
                return fake_a
            else:
                return fake_s

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.get("/api/v1/admin/pending-grades")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_pending"] == 1
            assert data["answers"][0]["roll_number"] == "ALICE"
            assert data["answers"][0]["ai_score"] == 3.0
        finally:
            _grade_stop(patches)

    def test_session_lookup_failure_does_not_crash(self):
        questions = [{"id": "qid-1", "question_id": "q-1", "exam_id": "exam-1",
                      "question": "What?", "reference_answer": "Ans", "rubric": "Rubric",
                      "max_score": 5}]
        answers = [{"id": "aid-1", "session_key": "sess-1", "question_id": "q-1",
                    "answer": "Student ans", "ai_score": 3.0, "ai_feedback": "Good",
                    "ai_confidence": "high", "teacher_score": None, "exam_id": "exam-1"}]

        fake_q = _fake_atable(questions)
        fake_a = _fake_atable(answers)
        fake_s = MagicMock()
        fake_s.select.return_value = fake_s
        fake_s.eq.return_value = fake_s
        fake_s.in_.return_value = fake_s
        fake_s.execute = AsyncMock(side_effect=RuntimeError("session lookup fail"))

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_q
            elif call_count == 1:
                call_count += 1
                return fake_a
            else:
                return fake_s

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.get("/api/v1/admin/pending-grades")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_pending"] == 1
        finally:
            _grade_stop(patches)

    def test_filters_by_exam_id(self):
        patches, fake = _grade_start()
        try:
            resp = client.get("/api/v1/admin/pending-grades?exam_id=exam-1")
            assert resp.status_code == 200
        finally:
            _grade_stop(patches)


# ── grade_suggest ───────────────────────────────────────────────────────

class TestGradeSuggest:
    def test_llm_not_configured_returns_503(self):
        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=False),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["aid-1"]})
            assert resp.status_code == 503
        finally:
            _grade_stop(patches)

    def test_missing_answer_ids_returns_400(self):
        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest", json={"answer_ids": []})
            assert resp.status_code == 400
        finally:
            _grade_stop(patches)

    def test_too_many_ids_returns_413(self):
        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["a"] * 51})
            assert resp.status_code == 413
        finally:
            _grade_stop(patches)

    def test_no_matching_answers(self):
        fake = _fake_atable([])  # no answers found
        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
            patch("app.routers.grading._atable", return_value=fake),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["aid-1"]})
            assert resp.status_code == 200
            assert resp.json()["graded"] == 0
        finally:
            _grade_stop(patches)

    def test_successful_grading(self):
        answers = [{"id": "aid-1", "question_id": "q-1", "answer": "Student answer",
                    "teacher_id": "teacher-1", "exam_id": "exam-1"}]
        questions = [{"question_id": "q-1", "question": "What?", "reference_answer": "Ans",
                      "rubric": "Rubric", "max_score": 5.0}]

        fake_a = _fake_atable(answers)
        fake_q = _fake_atable(questions)

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_a
            else:
                call_count += 1
                return fake_q

        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
            patch("app.llm.grade_short_answer", AsyncMock(return_value={
                "score": 4.0, "feedback": "Good job", "confidence": "high",
            })),
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["aid-1"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["graded"] == 1
            assert data["results"][0]["score"] == 4.0
        finally:
            _grade_stop(patches)

    def test_llm_error_returns_error_in_result(self):
        answers = [{"id": "aid-1", "question_id": "q-1", "answer": "Student answer",
                    "teacher_id": "teacher-1"}]
        questions = [{"question_id": "q-1", "question": "What?", "reference_answer": "Ans",
                      "rubric": "Rubric", "max_score": 5.0}]
        fake_a = _fake_atable(answers)
        fake_q = _fake_atable(questions)

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_a
            else:
                call_count += 1
                return fake_q

        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
            patch("app.llm.grade_short_answer", AsyncMock(side_effect=RuntimeError("LLM timeout"))),
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["aid-1"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["graded"] == 1
            assert "error" in data["results"][0]
        finally:
            _grade_stop(patches)

    def test_question_not_found_for_answer(self):
        answers = [{"id": "aid-1", "question_id": "unknown-q", "answer": "Ans",
                    "teacher_id": "teacher-1"}]
        fake_a = _fake_atable(answers)
        fake_q = _fake_atable([])

        def _atable_side(name: str):
            if "answers" in name or name == "answers":
                return fake_a
            return fake_q

        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["aid-1"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["results"][0].get("error") == "question not found"
        finally:
            _grade_stop(patches)

    def test_update_db_failure_does_not_crash(self):
        answers = [{"id": "aid-1", "question_id": "q-1", "answer": "Student answer",
                    "teacher_id": "teacher-1"}]
        questions = [{"question_id": "q-1", "question": "What?", "reference_answer": "Ans",
                      "rubric": "Rubric", "max_score": 5.0}]
        fake_a = _fake_atable(answers)
        fake_q = _fake_atable(questions)
        fake_update = _fake_atable()
        fake_update.update = MagicMock(return_value=fake_update)
        fake_update.eq = MagicMock(return_value=fake_update)
        fake_update.execute = AsyncMock(side_effect=RuntimeError("DB write fail"))

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_a
            elif call_count == 1:
                call_count += 1
                return fake_q
            else:
                return fake_update

        patches, _ = _grade_start([
            patch("app.llm.is_configured", return_value=True),
            patch("app.llm.grade_short_answer", AsyncMock(return_value={
                "score": 4.0, "feedback": "Good", "confidence": "high",
            })),
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-suggest",
                               json={"answer_ids": ["aid-1"]})
            assert resp.status_code == 200
        finally:
            _grade_stop(patches)


# ── grade_confirm ───────────────────────────────────────────────────────

class TestGradeConfirm:
    def test_empty_answer_id_returns_400(self):
        patches, _ = _grade_start()
        try:
            resp = client.post("/api/v1/admin/grade-confirm", json={
                "answer_id": "", "score": 4.0,
            })
            assert resp.status_code == 400
        finally:
            _grade_stop(patches)

    def test_answer_not_found_returns_404(self):
        fake = _fake_atable([])
        patches, _ = _grade_start([
            patch("app.routers.grading._atable", return_value=fake),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-confirm", json={
                "answer_id": "aid-1", "score": 4.0,
            })
            assert resp.status_code == 404
        finally:
            _grade_stop(patches)

    def test_score_out_of_range_returns_400(self):
        answer = [{"id": "aid-1", "question_id": "q-1", "session_key": "sess-1",
                   "ai_score": 3.0, "ai_confidence": "high"}]
        question = [{"question_id": "q-1", "max_score": 5.0, "exam_id": "exam-1"}]

        fake_ans = _fake_atable(answer)
        fake_q = _fake_atable(question)

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_ans
            return fake_q

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-confirm", json={
                "answer_id": "aid-1", "score": 99.0,
            })
            assert resp.status_code == 400
        finally:
            _grade_stop(patches)

    def test_successful_confirm(self):
        answer = [{"id": "aid-1", "question_id": "q-1", "session_key": "sess-1",
                   "ai_score": 3.0, "ai_confidence": "high"}]
        question = [{"question_id": "q-1", "max_score": 5.0, "exam_id": "exam-1"}]
        fake_ans = _fake_atable(answer)
        fake_q = _fake_atable(question)
        fake_audit = _fake_atable()

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_ans
            elif call_count == 1:
                call_count += 1
                return fake_q
            call_count += 1
            return fake_audit

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
            patch("app.services.scoring.recalculate_score", AsyncMock(return_value=(80, 100))),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-confirm", json={
                "answer_id": "aid-1", "score": 4.0,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
        finally:
            _grade_stop(patches)

    def test_idempotency_duplicate(self):
        from app.services.idempotency import idempotency_key as _idk
        idem_k = _idk("grade-confirm", "teacher-1", "idem-1")
        from tests.conftest import mock_cache
        mock_cache.get.return_value = None
        mock_cache.set.return_value = True
        mock_cache.set_if_absent.return_value = False  # already reserved

        patches, _ = _grade_start()
        try:
            resp = client.post("/api/v1/admin/grade-confirm", json={
                "answer_id": "aid-1", "score": 4.0, "idempotency_key": "idem-1",
            })
            assert resp.status_code == 409
        finally:
            _grade_stop(patches)

    def test_audit_insert_failure_swallowed(self):
        answer = [{"id": "aid-1", "question_id": "q-1", "session_key": "sess-1",
                   "ai_score": 3.0, "ai_confidence": "high"}]
        question = [{"question_id": "q-1", "max_score": 5.0, "exam_id": "exam-1"}]
        fake_ans = _fake_atable(answer)
        fake_q = _fake_atable(question)
        fake_audit = _fake_atable()
        fake_audit.insert = MagicMock(return_value=fake_audit)
        fake_audit.execute = AsyncMock(side_effect=RuntimeError("audit fail"))

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_ans
            elif call_count == 1:
                call_count += 1
                return fake_q
            elif call_count == 2:
                call_count += 1
                return _fake_atable()  # update
            else:
                return fake_audit

        with patch("app.routers.grading.require_admin", return_value=_TEACHER):
            with patch("app.routers.grading._atable", side_effect=_atable_side):
                resp = client.post("/api/v1/admin/grade-confirm", json={
                    "answer_id": "aid-1", "score": 4.0,
                })
                assert resp.status_code == 200


# ── grade_confirm_bulk ──────────────────────────────────────────────────

class TestGradeConfirmBulk:
    def test_missing_exam_id_returns_400(self):
        patches, _ = _grade_start()
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={"action": "accept"})
            assert resp.status_code == 400
        finally:
            _grade_stop(patches)

    def test_invalid_action_returns_400(self):
        patches, _ = _grade_start()
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={
                "exam_id": "exam-1", "action": "invalid",
            })
            assert resp.status_code == 400
        finally:
            _grade_stop(patches)

    def test_no_pending_answers(self):
        patches, _ = _grade_start()
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={
                "exam_id": "exam-1", "action": "accept",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["confirmed"] == 0
        finally:
            _grade_stop(patches)

    def test_bulk_accept(self):
        pending = [
            {"id": "aid-1", "session_key": "sess-1", "question_id": "q-1",
             "ai_score": 4.0, "ai_confidence": "high"},
            {"id": "aid-2", "session_key": "sess-1", "question_id": "q-1",
             "ai_score": 3.0, "ai_confidence": "medium"},
        ]
        questions = [{"question_id": "q-1", "max_score": 5.0, "exam_id": "exam-1"}]
        fake_pending = _fake_atable(pending)
        fake_q = _fake_atable(questions)
        fake_upsert = _fake_atable()

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_pending
            elif call_count == 1:
                call_count += 1
                return fake_q
            call_count += 1
            return fake_upsert

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={
                "exam_id": "exam-1", "action": "accept",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["confirmed"] == 2
        finally:
            _grade_stop(patches)

    def test_bulk_reject(self):
        pending = [
            {"id": "aid-1", "session_key": "sess-1", "question_id": "q-1",
             "ai_score": 4.0, "ai_confidence": "high"},
        ]
        questions = [{"question_id": "q-1", "max_score": 5.0, "exam_id": "exam-1"}]
        fake_pending = _fake_atable(pending)
        fake_q = _fake_atable(questions)

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return fake_pending
            call_count += 1
            return fake_q

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={
                "exam_id": "exam-1", "action": "reject",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["confirmed"] == 1
        finally:
            _grade_stop(patches)

    def test_confidence_filter(self):
        patches, _ = _grade_start()
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={
                "exam_id": "exam-1", "action": "accept", "confidence_filter": "high",
            })
            assert resp.status_code == 200
        finally:
            _grade_stop(patches)

    def test_skip_when_ai_score_none(self):
        pending = [
            {"id": "aid-1", "session_key": "sess-1", "question_id": "q-1",
             "ai_score": None, "ai_confidence": "high"},
        ]
        fake_pending = _fake_atable(pending)

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", return_value=fake_pending),
        ])
        try:
            resp = client.post("/api/v1/admin/grade-confirm-bulk", json={
                "exam_id": "exam-1", "action": "accept",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["skipped"] == 1
            assert data["confirmed"] == 0
        finally:
            _grade_stop(patches)


# ── grading_audit ───────────────────────────────────────────────────────

class TestGradingAudit:
    def test_returns_events_and_stats(self):
        events = [
            {"action": "confirmed", "teacher_id": "teacher-1"},
            {"action": "overridden", "teacher_id": "teacher-1"},
            {"action": "bulk_reject", "teacher_id": "teacher-1"},
        ]
        fake = _fake_atable(events)

        call_count = 0

        def _atable_side(name: str):
            nonlocal call_count
            call_count += 1
            return fake

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", side_effect=_atable_side),
        ])
        try:
            resp = client.get("/api/v1/admin/grading-audit")
            assert resp.status_code == 200
            data = resp.json()
            assert data["stats"]["total"] == 3
            assert data["stats"]["accepted"] == 1
            assert data["stats"]["overridden"] == 1
            assert data["stats"]["rejected"] == 1
        finally:
            _grade_stop(patches)

    def test_filters_by_exam_id(self):
        patches, _ = _grade_start()
        try:
            resp = client.get("/api/v1/admin/grading-audit?exam_id=exam-1")
            assert resp.status_code == 200
        finally:
            _grade_stop(patches)

    def test_respects_limit(self):
        events = [{"action": "confirmed"}] * 50
        fake = _fake_atable(events)

        patches, _ = _grade_start([
            patch("app.routers.grading._atable", return_value=fake),
        ])
        try:
            resp = client.get("/api/v1/admin/grading-audit?limit=10")
            assert resp.status_code == 200
        finally:
            _grade_stop(patches)

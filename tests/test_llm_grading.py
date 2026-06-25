"""Fixtures for the AI short-answer grader (app.llm.grade_short_answer).

The grader is the human-in-the-loop suggestion step: it returns
``{score, feedback, confidence}`` and must NEVER raise into the caller —
a flaky LLM or a Redis blip has to degrade to "review manually", not
crash the whole batch. These tests pin that contract by mocking the
network call (``_chat_json``) so they run offline and deterministically.

``app.cache`` is already a MagicMock (see conftest) whose ``get`` returns
None, so the cache layer is a no-op unless a test opts into a failure.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.llm as llm
from tests.conftest import mock_cache


@pytest.fixture
def configured(monkeypatch):
    """Make is_configured() true without touching real env/keys."""
    monkeypatch.setattr(llm, "LLM_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _reset_cache():
    """Default the shared cache mock to a clean miss before each test so a
    side_effect set by one test can't leak into the next."""
    mock_cache.reset_mock()
    mock_cache.get.return_value = None
    mock_cache.get.side_effect = None
    mock_cache.set.side_effect = None
    yield


def _stub_chat(monkeypatch, payload):
    async def _fake(system, user, **kw):
        return payload
    monkeypatch.setattr(llm, "_chat_json", _fake)


@pytest.mark.asyncio
async def test_not_configured_returns_low_confidence(monkeypatch):
    monkeypatch.setattr(llm, "LLM_API_KEY", "")
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r == {"score": None, "feedback": "AI grader not configured.",
                 "confidence": "low"}


@pytest.mark.asyncio
async def test_blank_answer_short_circuits_without_llm(configured, monkeypatch):
    # If _chat_json were called it would raise — proving we never hit it.
    def _boom(*a, **k):
        raise AssertionError("LLM must not be called for a blank answer")
    monkeypatch.setattr(llm, "_chat_json", _boom)
    r = await llm.grade_short_answer("q", "ref", "", "   ", 5.0)
    assert r == {"score": 0.0, "feedback": "Blank answer.", "confidence": "high"}


@pytest.mark.asyncio
async def test_normal_grade_passes_through(configured, monkeypatch):
    _stub_chat(monkeypatch, {"score": 1.5, "feedback": "Partial.", "confidence": "medium"})
    r = await llm.grade_short_answer("q", "ref", "rubric", "ans", 2.0)
    assert r == {"score": 1.5, "feedback": "Partial.", "confidence": "medium"}


@pytest.mark.asyncio
async def test_score_is_clamped_to_max(configured, monkeypatch):
    _stub_chat(monkeypatch, {"score": 9, "feedback": "", "confidence": "high"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] == 2.0


@pytest.mark.asyncio
async def test_negative_score_is_clamped_to_zero(configured, monkeypatch):
    _stub_chat(monkeypatch, {"score": -3, "feedback": "", "confidence": "high"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] == 0.0


@pytest.mark.asyncio
async def test_string_score_is_coerced(configured, monkeypatch):
    _stub_chat(monkeypatch, {"score": "1.5", "feedback": "ok", "confidence": "high"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] == 1.5


@pytest.mark.asyncio
async def test_non_numeric_score_defaults_to_zero(configured, monkeypatch):
    _stub_chat(monkeypatch, {"score": "lots", "feedback": "ok", "confidence": "high"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] == 0.0


@pytest.mark.asyncio
async def test_invalid_confidence_normalised(configured, monkeypatch):
    _stub_chat(monkeypatch, {"score": 1, "feedback": "x", "confidence": "VERY-SURE"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["confidence"] == "medium"


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_manual_review(configured, monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("groq 503")
    monkeypatch.setattr(llm, "_chat_json", _raise)
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] is None
    assert r["confidence"] == "low"
    assert "manually" in r["feedback"].lower()


@pytest.mark.asyncio
async def test_cache_read_failure_does_not_crash(configured, monkeypatch):
    """A Redis hiccup on the cache READ must not bubble out of the grader.
    Regression: the except handler referenced an undefined ``logger`` and
    raised NameError, turning every batch row into an error."""
    mock_cache.get.side_effect = RuntimeError("redis down")
    _stub_chat(monkeypatch, {"score": 1, "feedback": "ok", "confidence": "high"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] == 1.0


@pytest.mark.asyncio
async def test_cache_write_failure_does_not_crash(configured, monkeypatch):
    """Same contract for the cache WRITE path after a successful grade."""
    mock_cache.set.side_effect = RuntimeError("redis down")
    _stub_chat(monkeypatch, {"score": 1, "feedback": "ok", "confidence": "high"})
    r = await llm.grade_short_answer("q", "ref", "", "ans", 2.0)
    assert r["score"] == 1.0


# ── Privacy guard: identifier-free payload (DPDP data-minimization) ──────────
# The grader sends anonymous prose to an external LLM. These two tests lock that
# contract so a future change can't silently start leaking student identifiers.

@pytest.mark.asyncio
async def test_grade_prompt_is_built_only_from_its_inputs(configured, monkeypatch):
    """The prompt is a pure function of the grader's (identifier-free) inputs — it must
    not pull in any ambient student context. Unique sentinels for each input must all
    appear, proving the prompt is composed solely from the passed arguments."""
    captured = {}

    async def _capture(system, user, **kw):
        captured["user"] = user
        return {"score": 1, "feedback": "ok", "confidence": "high"}

    monkeypatch.setattr(llm, "_chat_json", _capture)
    await llm.grade_short_answer(
        question="Q_SENTINEL", reference="REF_SENTINEL", rubric="RUBRIC_SENTINEL",
        student_answer="ANS_SENTINEL", max_score=2.0)
    for token in ("Q_SENTINEL", "REF_SENTINEL", "RUBRIC_SENTINEL", "ANS_SENTINEL"):
        assert token in captured["user"]


def test_grade_inputs_frozen_to_anonymous_set():
    """Privacy tripwire (DPDP data-minimization): the ONLY student data reaching the
    external LLM is the answer prose. The grader's parameters are frozen to the
    identifier-free set; adding one (e.g. student_name / roll_number / email /
    session_id) trips this on purpose — update it only after confirming the new field
    is NOT an identifier (and never folds an identifier into the prompt)."""
    import inspect
    assert set(inspect.signature(llm.grade_short_answer).parameters) == {
        "question", "reference", "rubric", "student_answer", "max_score"}

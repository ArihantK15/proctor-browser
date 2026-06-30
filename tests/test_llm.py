"""Tests for app/llm.py --- LLM helpers.

Each function's HTTP call to _chat_json is mocked so tests run offline.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.llm as llm
from tests.conftest import mock_cache


# -- Fixtures

@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(llm, "LLM_API_KEY", "test-key")


def _stub_chat(monkeypatch, payload):
    async def _fake(system, user, **kw):
        return payload
    monkeypatch.setattr(llm, "_chat_json", _fake)


# -- is_configured

class TestIsConfigured:
    def test_true_when_key_set(self):
        with patch("app.llm.LLM_API_KEY", "test-key"):
            from app.llm import is_configured
            assert is_configured() is True

    def test_false_when_key_not_set(self):
        with patch("app.llm.LLM_API_KEY", ""):
            from app.llm import is_configured
            assert is_configured() is False


# -- _looks_like_multi

class TestLooksLikeMulti:
    def test_valid_single_letter(self):
        assert llm._looks_like_multi("A") is True

    def test_multiple_letters(self):
        assert llm._looks_like_multi("A,C") is True

    def test_three_letters(self):
        assert llm._looks_like_multi("A,B,D") is True

    def test_handles_spaces(self):
        assert llm._looks_like_multi("B, D") is True

    def test_invalid_letters(self):
        assert llm._looks_like_multi("X,Y") is False

    def test_empty_string(self):
        assert llm._looks_like_multi("") is False


# -- _chat_json

class TestChatJson:
    @pytest.mark.asyncio
    async def test_raises_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(llm, "LLM_API_KEY", "")
        with pytest.raises(RuntimeError, match="not configured"):
            await llm._chat_json("sys", "user")

    @pytest.mark.asyncio
    async def test_successful_call(self, monkeypatch):
        monkeypatch.setattr(llm, "LLM_API_KEY", "key")
        monkeypatch.setattr(llm, "LLM_TIMEOUT", 30)
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"ok": true}'}}]
        })
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=fake_resp)
            result = await llm._chat_json("sys", "user")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        with patch("app.llm.LLM_API_KEY", "key"), \
             patch("app.llm.LLM_TIMEOUT", 30), \
             patch("httpx.AsyncClient") as mock_client:
            fake_resp = MagicMock()
            fake_resp.status_code = 200
            fake_resp.json = MagicMock(return_value={
                "choices": [{"message": {"content": "```json\n{\"ok\": true}\n```"}}]
            })
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=fake_resp)
            result = await llm._chat_json("sys", "user")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_raises_on_bad_json(self):
        with patch("app.llm.LLM_API_KEY", "key"), \
             patch("app.llm.LLM_TIMEOUT", 30), \
             patch("httpx.AsyncClient") as mock_client:
            fake_resp = MagicMock()
            fake_resp.status_code = 200
            fake_resp.json = MagicMock(return_value={
                "choices": [{"message": {"content": "not json"}}]
            })
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=fake_resp)
            with pytest.raises(RuntimeError, match="malformed JSON"):
                await llm._chat_json("sys", "user")

    @pytest.mark.asyncio
    async def test_sends_openrouter_headers(self):
        with patch("app.llm.LLM_API_KEY", "key"), \
             patch("app.llm.LLM_BASE_URL", "https://openrouter.ai/v1"), \
             patch("app.llm.LLM_TIMEOUT", 30), \
             patch("httpx.AsyncClient") as mock_client:
            fake_resp = MagicMock()
            fake_resp.status_code = 200
            fake_resp.json = MagicMock(return_value={
                "choices": [{"message": {"content": '{"ok": true}'}}]
            })
            mock_post = AsyncMock(return_value=fake_resp)
            mock_client.return_value.__aenter__.return_value.post = mock_post
            await llm._chat_json("sys", "user")
            call_headers = mock_post.call_args[1]["headers"]
            assert "HTTP-Referer" in call_headers
            assert "X-Title" in call_headers

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        with patch("app.llm.LLM_API_KEY", "key"), \
             patch("app.llm.LLM_TIMEOUT", 30), \
             patch("httpx.AsyncClient") as mock_client:
            fake_resp = MagicMock()
            fake_resp.status_code = 500
            fake_resp.text = "Internal Server Error"
            fake_resp.raise_for_status.side_effect = Exception("HTTP 500")
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=fake_resp)
            with pytest.raises(Exception):
                await llm._chat_json("sys", "user")


# -- generate_questions

class TestGenerateQuestions:
    _SAMPLE_RESPONSE = {
        "questions": [
            {
                "question": "What is 2+2?",
                "question_type": "mcq_single",
                "option_A": "3",
                "option_B": "4",
                "option_C": "5",
                "option_D": "6",
                "correct": "B",
                "tags": ["math", "easy"],
            }
        ]
    }

    @pytest.mark.asyncio
    async def test_generates_and_cleans(self, configured, monkeypatch):
        _stub_chat(monkeypatch, self._SAMPLE_RESPONSE)
        result = await llm.generate_questions("math", count=1)
        assert len(result) == 1
        assert result[0]["question"] == "What is 2+2?"
        assert result[0]["correct"] == "B"

    @pytest.mark.asyncio
    async def test_clamps_count(self, configured, monkeypatch):
        _stub_chat(monkeypatch, self._SAMPLE_RESPONSE)
        result = await llm.generate_questions("math", count=999)
        assert len(result) <= 25

    @pytest.mark.asyncio
    async def test_handles_non_list_questions(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"questions": "not a list"})
        with pytest.raises(RuntimeError, match="non-list"):
            await llm.generate_questions("math", count=1)

    @pytest.mark.asyncio
    async def test_skips_incomplete_options(self, configured, monkeypatch):
        bad = {
            "questions": [
                {
                    "question": "Incomplete?",
                    "question_type": "mcq_single",
                    "option_A": "",
                    "option_B": "42",
                    "option_C": "",
                    "option_D": "",
                    "correct": "B",
                    "tags": [],
                }
            ]
        }
        _stub_chat(monkeypatch, bad)
        result = await llm.generate_questions("math", count=1)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_maps_option_text_to_letter(self, configured, monkeypatch):
        text_as_opt = {
            "questions": [
                {
                    "question": "Q?",
                    "question_type": "mcq_single",
                    "option_A": "Paris",
                    "option_B": "London",
                    "option_C": "Berlin",
                    "option_D": "Madrid",
                    "correct": "London",
                    "tags": [],
                }
            ]
        }
        _stub_chat(monkeypatch, text_as_opt)
        result = await llm.generate_questions("geo", count=1)
        assert result[0]["correct"] == "B"

    @pytest.mark.asyncio
    async def test_handles_source_text(self, configured, monkeypatch):
        _stub_chat(monkeypatch, self._SAMPLE_RESPONSE)
        result = await llm.generate_questions("math", count=1, source_text="A" * 5000)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_non_dict_question(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"questions": ["not a dict", self._SAMPLE_RESPONSE["questions"][0]]})
        result = await llm.generate_questions("math", count=5)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_skips_empty_question_text(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"questions": [
            {"question": "", "question_type": "mcq_single", "option_A": "A",
             "option_B": "B", "option_C": "C", "option_D": "D", "correct": "A", "tags": []}
        ]})
        result = await llm.generate_questions("math", count=1)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_falls_back_correct_to_a(self, configured, monkeypatch):
        """When the LLM returns option text that doesn't match any letter."""
        _stub_chat(monkeypatch, {"questions": [
            {"question": "Q?", "question_type": "mcq_single", "option_A": "Paris",
             "option_B": "London", "option_C": "Berlin", "option_D": "Madrid",
             "correct": "NoneOfTheAbove", "tags": []}
        ]})
        result = await llm.generate_questions("geo", count=1)
        assert result[0]["correct"] == "A"

    @pytest.mark.asyncio
    async def test_non_list_tags_coerced(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"questions": [
            {"question": "Q?", "question_type": "mcq_single", "option_A": "A",
             "option_B": "B", "option_C": "C", "option_D": "D",
             "correct": "A", "tags": "math"}
        ]})
        result = await llm.generate_questions("math", count=1)
        assert result[0]["tags"] == ["math"]


# -- generate_coding_question

class TestGenerateCodingQuestion:
    _SAMPLE = {
        "question": "Write a program to add two numbers.",
        "starter_code": "// write code",
        "reference_solution": "console.log(...)",
        "test_cases": [
            {"input": "1 2", "expected_output": "3", "visibility": "sample"},
            {"input": "5 7", "expected_output": "12", "visibility": "hidden"},
        ],
    }

    @pytest.mark.asyncio
    async def test_generates_coding_question(self, configured, monkeypatch):
        _stub_chat(monkeypatch, self._SAMPLE)
        result = await llm.generate_coding_question("math")
        assert result["question"] == "Write a program to add two numbers."
        assert result["needs_verification"] is True

    @pytest.mark.asyncio
    async def test_ensures_hidden_test_case(self, configured, monkeypatch):
        only_sample = {**self._SAMPLE, "test_cases": [
            {"input": "1", "expected_output": "1", "visibility": "sample"},
        ]}
        _stub_chat(monkeypatch, only_sample)
        result = await llm.generate_coding_question("math")
        assert result["test_cases"][-1]["visibility"] == "hidden"

    @pytest.mark.asyncio
    async def test_no_cases_raises(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {**self._SAMPLE, "test_cases": []})
        with pytest.raises(RuntimeError, match="no test_cases"):
            await llm.generate_coding_question("math")

    @pytest.mark.asyncio
    async def test_unknown_language_defaults(self, configured, monkeypatch):
        _stub_chat(monkeypatch, self._SAMPLE)
        result = await llm.generate_coding_question("math", language="rust")
        assert result["options"]["allowed_languages"] == ["javascript"]

    @pytest.mark.asyncio
    async def test_missing_statement_raises(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"question": "", "starter_code": "", "reference_solution": "", "test_cases": [{"input": "1", "expected_output": "1"}]})
        with pytest.raises(RuntimeError, match="no 'question' statement"):
            await llm.generate_coding_question("math")

    @pytest.mark.asyncio
    async def test_skips_invalid_case(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {**self._SAMPLE, "test_cases": [
            {"not_a_case": True},
            {"input": "1", "expected_output": "1", "visibility": "hidden"},
        ]})
        result = await llm.generate_coding_question("math")
        assert len(result["test_cases"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_visibility_normalised(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {**self._SAMPLE, "test_cases": [
            {"input": "1", "expected_output": "1", "visibility": "public"},
        ]})
        result = await llm.generate_coding_question("math")
        assert result["test_cases"][0]["visibility"] == "hidden"

    @pytest.mark.asyncio
    async def test_no_usable_cases_raises(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {**self._SAMPLE, "test_cases": [
            {"not_a_case": True},
        ]})
        with pytest.raises(RuntimeError, match="no usable test_cases"):
            await llm.generate_coding_question("math")

class TestScorecardInsight:
    @pytest.mark.asyncio
    async def test_not_configured_returns_empty(self, monkeypatch):
        monkeypatch.setattr(llm, "LLM_API_KEY", "")
        r = await llm.scorecard_insight({"score": 5}, [{"question": "Q1", "is_correct": True}])
        assert r == ""

    @pytest.mark.asyncio
    async def test_empty_per_question_returns_empty(self, configured, monkeypatch):
        r = await llm.scorecard_insight({"score": 5}, [])
        assert r == ""

    @pytest.mark.asyncio
    async def test_returns_note(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"note": "Great work on algebra."})
        r = await llm.scorecard_insight(
            {"score": 8, "total": 10, "percentage": 80, "passed": True, "exam_title": "Math"},
            [{"question": "Q1", "is_correct": True}],
        )
        assert r == "Great work on algebra."

    @pytest.mark.asyncio
    async def test_empty_on_llm_failure(self, configured, monkeypatch):
        async def _raise(*a, **k):
            raise RuntimeError("fail")
        monkeypatch.setattr(llm, "_chat_json", _raise)
        r = await llm.scorecard_insight(
            {"score": 5, "total": 10}, [{"question": "Q1", "is_correct": True}],
        )
        assert r == ""


# -- suggest_tags

class TestSuggestTags:
    @pytest.mark.asyncio
    async def test_empty_question_returns_empty(self):
        r = await llm.suggest_tags("", {}, "")
        assert r == []

    @pytest.mark.asyncio
    async def test_returns_tags(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"tags": ["math", "algebra", "easy"]})
        r = await llm.suggest_tags("Solve for x", {}, "A")
        assert r == ["math", "algebra", "easy"]

    @pytest.mark.asyncio
    async def test_non_list_tags_returns_empty(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"tags": "not a list"})
        r = await llm.suggest_tags("Solve for x", {}, "A")
        assert r == []


# -- live_risk_triage

class TestLiveRiskTriage:
    @pytest.mark.asyncio
    async def test_not_configured_returns_empty(self, monkeypatch):
        monkeypatch.setattr(llm, "LLM_API_KEY", "")
        r = await llm.live_risk_triage({"full_name": "A"}, [])
        assert r == ""

    @pytest.mark.asyncio
    async def test_no_notable_violations_returns_clean(self, configured, monkeypatch):
        r = await llm.live_risk_triage(
            {"full_name": "Alice", "roll_number": "R1", "elapsed_minutes": 30},
            [{"violation_type": "heartbeat", "severity": "low"}],
        )
        assert r == "No concerning patterns."

    @pytest.mark.asyncio
    async def test_returns_summary(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"summary": "Student looked away at Q3."})
        r = await llm.live_risk_triage(
            {"full_name": "Bob", "roll_number": "R2", "elapsed_minutes": 15,
             "exam_title": "Test", "current_question": 3},
            [{"violation_type": "gaze_away", "severity": "high", "details": "looked left"}],
        )
        assert r == "Student looked away at Q3."

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self, configured, monkeypatch):
        async def _raise(*a, **k):
            raise RuntimeError("fail")
        monkeypatch.setattr(llm, "_chat_json", _raise)
        r = await llm.live_risk_triage(
            {"full_name": "C", "roll_number": "R3", "elapsed_minutes": 10},
            [{"violation_type": "phone_detected", "severity": "high"}],
        )
        assert r == ""


# -- lint_questions

class TestLintQuestions:
    @pytest.mark.asyncio
    async def test_not_configured_returns_empty(self, monkeypatch):
        monkeypatch.setattr(llm, "LLM_API_KEY", "")
        r = await llm.lint_questions([{"question": "Q1"}])
        assert r == []

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        r = await llm.lint_questions([])
        assert r == []

    @pytest.mark.asyncio
    async def test_returns_issues(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {
            "results": [{"idx": 0, "issues": [{"type": "AMBIGUOUS", "severity": "medium", "note": "Unclear"}]}]
        })
        r = await llm.lint_questions([{"question": "Q1?", "options": {"A": "1", "B": "2"}, "correct": "A", "idx": 0}])
        assert len(r) == 1
        assert r[0]["issues"][0]["type"] == "AMBIGUOUS"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self, configured, monkeypatch):
        async def _raise(*a, **k):
            raise RuntimeError("fail")
        monkeypatch.setattr(llm, "_chat_json", _raise)
        r = await llm.lint_questions([{"question": "Q1?"}])
        assert r == []

    @pytest.mark.asyncio
    async def test_non_list_results_returns_empty(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"results": "not a list"})
        r = await llm.lint_questions([{"question": "Q1?"}])
        assert r == []

    @pytest.mark.asyncio
    async def test_non_dict_result_is_skipped_but_question_included(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"results": ["not a dict"]})
        r = await llm.lint_questions([{"question": "Q1?", "idx": 0}])
        assert r == [{"idx": 0, "issues": []}]

    @pytest.mark.asyncio
    async def test_results_without_idx_are_skipped_but_question_included(self, configured, monkeypatch):
        """When the LLM returns a result without idx, it's skipped, but the
        question still appears in the output with empty issues."""
        _stub_chat(monkeypatch, {"results": [{"issues": [{"type": "AMBIGUOUS"}]}]})
        r = await llm.lint_questions([{"question": "Q1?", "idx": 0}])
        assert r == [{"idx": 0, "issues": []}]

    @pytest.mark.asyncio
    async def test_skips_non_dict_issue(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"results": [{"idx": 0, "issues": ["not a dict"]}]})
        r = await llm.lint_questions([{"question": "Q1?", "idx": 0}])
        assert r == [{"idx": 0, "issues": []}]

class TestGenerateRubric:
    @pytest.mark.asyncio
    async def test_not_configured_returns_default(self, monkeypatch):
        monkeypatch.setattr(llm, "LLM_API_KEY", "")
        r = await llm.generate_rubric("Q?", "Ans", 5)
        assert r["max_score"] == 5
        assert r["criteria"] == []

    @pytest.mark.asyncio
    async def test_empty_question_returns_default(self, configured, monkeypatch):
        r = await llm.generate_rubric("", "Ans", 5)
        assert r["max_score"] == 5

    @pytest.mark.asyncio
    async def test_returns_rubric(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {
            "rubric": "Mark based on...", "max_score": 5,
            "criteria": [{"score": 5, "label": "Excellent", "description": "Full marks"}],
        })
        r = await llm.generate_rubric("What is X?", "X is Y", 5)
        assert r["rubric"] == "Mark based on..."
        assert len(r["criteria"]) == 1

    @pytest.mark.asyncio
    async def test_failure_returns_error_in_rubric(self, configured, monkeypatch):
        async def _raise(*a, **k):
            raise RuntimeError("LLM down")
        monkeypatch.setattr(llm, "_chat_json", _raise)
        r = await llm.generate_rubric("Q?", "Ans", 5)
        assert "LLM down" in r["rubric"]

    @pytest.mark.asyncio
    async def test_empty_both_returns_default(self, configured, monkeypatch):
        _stub_chat(monkeypatch, {"rubric": "", "criteria": []})
        r = await llm.generate_rubric("Q?", "Ans", 5)
        assert r["max_score"] == 5
        assert r["criteria"] == []


# -- grade_short_answer cache hit (not covered by test_llm_grading.py) --

class TestGradeCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self, configured, monkeypatch):
        cached = {"score": 4.0, "feedback": "cached", "confidence": "high"}
        mock_cache.get.return_value = cached

        def _should_not_be_called(*a, **k):
            raise AssertionError("LLM should not be called on cache hit")
        monkeypatch.setattr(llm, "_chat_json", _should_not_be_called)
        r = await llm.grade_short_answer("q", "ref", "", "ans", 5.0)
        assert r == cached

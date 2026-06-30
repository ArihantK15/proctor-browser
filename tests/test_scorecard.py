"""Tests for the scorecard PDF generation service (app/services/scorecard.py).

Covers student-name resolution, logo fetching helpers, and the full
_build_scorecard_pdf pipeline with reportlab rendering.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
import pytest

from app.services import scorecard

import app.services.sessions as _sessions_mod


# ── resolve_student_name ────────────────────────────────────────────


class TestResolveStudentName:
    @pytest.mark.asyncio
    async def test_uses_full_name_when_present(self):
        exam = {"full_name": "Alice Smith", "roll_number": "A001"}
        name = await scorecard.resolve_student_name(exam, "t1")
        assert name == "Alice Smith"

    @pytest.mark.asyncio
    async def test_falls_back_to_roster(self, monkeypatch):
        sink = {}
        class _RosterTable:
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            async def execute(self):
                sink["called"] = True
                r = MagicMock()
                r.data = [{"full_name": "Roster Name"}]
                return r
        monkeypatch.setattr(scorecard, "_atable", lambda name: _RosterTable())

        exam = {"full_name": "", "roll_number": "A001", "email": ""}
        name = await scorecard.resolve_student_name(exam, "t1")
        assert name == "Roster Name"
        assert sink["called"]

    @pytest.mark.asyncio
    async def test_roster_failure_does_not_raise(self, monkeypatch):
        class _FailingTable:
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            async def execute(self):
                raise RuntimeError("db down")
        monkeypatch.setattr(scorecard, "_atable", lambda name: _FailingTable())

        exam = {"full_name": "", "roll_number": "A001", "email": "a@b.com"}
        name = await scorecard.resolve_student_name(exam, "t1")
        assert name == "a@b.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_email_when_no_name_or_roster(self, monkeypatch):
        class _EmptyTable:
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            async def execute(self):
                r = MagicMock()
                r.data = []
                return r
        monkeypatch.setattr(scorecard, "_atable", lambda name: _EmptyTable())

        exam = {"full_name": "", "roll_number": "A001", "email": "alice@test.com"}
        name = await scorecard.resolve_student_name(exam, "t1")
        assert name == "alice@test.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_roll_when_nothing_else(self, monkeypatch):
        class _EmptyTable:
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def limit(self, *a, **kw): return self
            async def execute(self):
                r = MagicMock()
                r.data = []
                return r
        monkeypatch.setattr(scorecard, "_atable", lambda name: _EmptyTable())

        exam = {"full_name": "", "roll_number": "A001", "email": ""}
        name = await scorecard.resolve_student_name(exam, "t1")
        assert name == "A001"


# ── _download_logo_bytes ────────────────────────────────────────────


class TestDownloadLogoBytes:
    @pytest.mark.asyncio
    async def test_data_uri_png(self):
        import base64
        raw = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        b64 = base64.b64encode(raw).decode()
        url = f"data:image/png;base64,{b64}"
        result = await scorecard._download_logo_bytes(url)
        assert result is not None
        data, mime = result
        assert mime == "image/png"
        assert data == raw

    @pytest.mark.asyncio
    async def test_data_uri_too_large(self):
        import base64
        raw = b"x" * (2 * 1024 * 1024 + 1)
        b64 = base64.b64encode(raw).decode()
        url = f"data:image/png;base64,{b64}"
        result = await scorecard._download_logo_bytes(url)
        assert result is None

    @pytest.mark.asyncio
    async def test_data_uri_malformed_returns_none(self):
        url = "data:image/png;base64,!!!invalid!!!"
        result = await scorecard._download_logo_bytes(url)
        assert result is None

    @pytest.mark.asyncio
    async def test_https_fetch_success(self, monkeypatch):
        class _OkResponse:
            status_code = 200
            content = b"fake-image-bytes"
            headers = {"content-type": "image/webp"}

        async def _mock_get(*a, **kw):
            return _OkResponse()

        monkeypatch.setattr("httpx.AsyncClient.get", _mock_get)
        result = await scorecard._download_logo_bytes("https://example.com/logo.webp")
        assert result == (b"fake-image-bytes", "image/webp")

    @pytest.mark.asyncio
    async def test_https_fetch_failure_returns_none(self, monkeypatch):
        async def _mock_get(*a, **kw):
            class _Resp:
                status_code = 404
                content = b""
                headers = {}
            return _Resp()

        monkeypatch.setattr("httpx.AsyncClient.get", _mock_get)
        result = await scorecard._download_logo_bytes("https://example.com/missing")
        assert result is None


# ── _build_scorecard_pdf (main entry point, full pipeline) ──────────


class TestBuildScorecardPdf:
    EXAM = {
        "full_name": "Alice Smith",
        "roll_number": "A001",
        "exam_id": "exam-1",
        "score": 8.0,
        "total": 10.0,
        "submitted_at": "2025-06-01T10:00:00+05:30",
        "started_at": "2025-06-01T09:00:00+05:30",
        "time_taken_secs": 3600,
        "status": "submitted",
        "percentage": 80.0,
        "termination_reason_code": "",
        "termination_reason_text": "",
        "email": "alice@test.com",
    }

    def _patch_deps(self, monkeypatch, **overrides):
        state = dict(
            exam=dict(self.EXAM),  # copy so test mutations don't leak
            questions=[],
            answers=[],
            exam_config={"exam_title": "Midterm", "pass_mark": 40},
            risk={"label": "Low", "score": 0},
            violations=[],
        )
        state.update(overrides)

        monkeypatch.setattr(scorecard, "_assert_session_owned",
                            AsyncMock(side_effect=lambda *a: state["exam"]))
        monkeypatch.setattr(scorecard, "_load_questions",
                            AsyncMock(side_effect=lambda *a, **kw: state["questions"]))
        monkeypatch.setattr(scorecard, "_load_exam_config",
                            AsyncMock(side_effect=lambda *a, **kw: state["exam_config"]))
        monkeypatch.setattr(scorecard, "compute_risk_score",
                            AsyncMock(side_effect=lambda *a, **kw: state["risk"]))
        monkeypatch.setattr(scorecard, "_fetch_org_logo_image",
                            AsyncMock(return_value=None))
        monkeypatch.setattr(scorecard, "fmt_ist", lambda x: x)
        monkeypatch.setattr(scorecard, "now_ist", lambda: datetime(2025, 6, 1, 12, 0))

        monkeypatch.setattr(_sessions_mod, "collect_session_screenshots",
                            MagicMock(return_value={}))
        monkeypatch.setattr(_sessions_mod, "match_screenshot_for_violation",
                            MagicMock(return_value=None))
        monkeypatch.setattr(_sessions_mod, "match_room_screenshot_for_violation",
                            MagicMock(return_value=None))

        class _Atable:
            def __init__(self, name):
                self._name = name
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def order(self, *a, **kw): return self
            async def execute(self):
                r = MagicMock()
                if self._name == "answers":
                    r.data = state["answers"]
                else:
                    r.data = state["violations"]
                return r

        monkeypatch.setattr(scorecard, "_atable", lambda name: _Atable(name))
        return state

    @pytest.mark.asyncio
    async def test_returns_pdf_and_summary(self, monkeypatch):
        self._patch_deps(monkeypatch)
        pdf_bytes, filename, summary = await scorecard._build_scorecard_pdf(
            "sess-1", "teacher-1"
        )

        assert pdf_bytes[:4] == b"%PDF"
        assert filename.startswith("scorecard_")
        assert filename.endswith(".pdf")
        assert summary["exam_title"] == "Midterm"
        assert summary["score"] == 8.0
        assert summary["total"] == 10.0
        assert summary["percentage"] == 80.0
        assert summary["passed"] is True
        assert summary["risk_label"] == "Low"
        assert summary["total_violations"] == 0

    @pytest.mark.asyncio
    async def test_returns_fail_when_below_pass_mark(self, monkeypatch):
        d = self._patch_deps(monkeypatch)
        d["exam"]["percentage"] = 30.0
        d["exam"]["score"] = 3.0

        _, _, summary = await scorecard._build_scorecard_pdf("sess-1", "t1")
        assert summary["passed"] is False
        assert summary["percentage"] == 30.0

    @pytest.mark.asyncio
    async def test_violation_counts_in_summary(self, monkeypatch):
        d = self._patch_deps(monkeypatch)
        d["violations"] = [
            {"id": "v1", "violation_type": "gaze_away", "severity": "high",
             "details": {}, "created_at": "2025-06-01T10:05:00"},
            {"id": "v2", "violation_type": "gaze_away", "severity": "low",
             "details": {}, "created_at": "2025-06-01T10:06:00"},
            {"id": "v3", "violation_type": "voice_detected", "severity": "medium",
             "details": {}, "created_at": "2025-06-01T10:07:00"},
        ]
        monkeypatch.setattr(scorecard, "_is_violation", lambda vtype: True)

        _, _, summary = await scorecard._build_scorecard_pdf("sess-1", "t1")
        assert summary["total_violations"] == 3
        assert summary["violations"]["gaze_away"]["total"] == 2
        assert summary["violations"]["gaze_away"]["high"] == 1
        assert summary["violations"]["gaze_away"]["low"] == 1
        assert summary["violations"]["voice_detected"]["total"] == 1
        assert summary["violations"]["voice_detected"]["medium"] == 1

    @pytest.mark.asyncio
    async def test_pdf_contains_termination_reason(self, monkeypatch):
        d = self._patch_deps(monkeypatch)
        d["exam"]["status"] = "force_submitted"
        d["exam"]["termination_reason_code"] = "academic_dishonesty"
        d["exam"]["termination_reason_text"] = "Student was looking at notes"

        pdf_bytes, _, _ = await scorecard._build_scorecard_pdf("sess-1", "t1")
        assert pdf_bytes[:4] == b"%PDF"

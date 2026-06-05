"""Tests for the automated suspicious activity summary feature."""
import pytest

from app.services.risk import generate_session_summary


class TestGenerateSessionSummary:
    """Unit tests for the narrative summary generator."""

    def test_clean_session(self):
        result = generate_session_summary([])
        assert result["severity"] == "clean"
        assert result["pattern_count"] == 0
        assert "No suspicious activity" in result["narrative"]

    def test_standard_violations_only(self):
        violations = [
            {"violation_type": "gaze_away", "severity": "low", "created_at": "2025-01-01T09:00:00Z"},
            {"violation_type": "gaze_away", "severity": "low", "created_at": "2025-01-01T09:05:00Z"},
            {"violation_type": "gaze_away", "severity": "low", "created_at": "2025-01-01T09:10:00Z"},
            {"violation_type": "head_turned", "severity": "medium", "created_at": "2025-01-01T09:15:00Z"},
        ]
        result = generate_session_summary(violations)
        assert result["severity"] == "minor"
        assert result["pattern_count"] == 0
        assert "gaze away (3" in result["narrative"]
        assert "head turned (1" in result["narrative"]

    def test_behavioral_patterns(self):
        violations = [
            {"violation_type": "phone_consulting", "severity": "critical",
             "details": "[Behavioral] Phone detected + gaze down pattern (conf 0.85)",
             "created_at": "2025-01-01T09:10:00Z"},
            {"violation_type": "phone_consulting", "severity": "critical",
             "details": "[Behavioral] Phone detected + gaze down pattern (conf 0.90)",
             "created_at": "2025-01-01T09:25:00Z"},
            {"violation_type": "collaboration", "severity": "critical",
             "details": "[Behavioral] Voice + multiple faces (conf 0.78)",
             "created_at": "2025-01-01T09:40:00Z"},
        ]
        result = generate_session_summary(violations)
        assert result["severity"] == "critical"
        assert result["pattern_count"] == 2
        assert "phone" in result["narrative"].lower()
        assert "another person" in result["narrative"]
        assert "behavioral analysis" in result["narrative"].lower()

    def test_critical_violations(self):
        violations = [
            {"violation_type": "wrong_person", "severity": "high",
             "created_at": "2025-01-01T09:00:00Z"},
            {"violation_type": "calibration_abort", "severity": "critical",
             "created_at": "2025-01-01T08:55:00Z"},
        ]
        result = generate_session_summary(violations)
        assert result["severity"] == "critical"
        assert "different person" in result["narrative"]
        assert "calibration was aborted" in result["narrative"]

    def test_mixed_violations(self):
        violations = [
            {"violation_type": "wrong_person", "severity": "high",
             "created_at": "2025-01-01T09:00:00Z"},
            {"violation_type": "phone_consulting", "severity": "critical",
             "details": "[Behavioral] Phone detected (conf 0.9)",
             "created_at": "2025-01-01T09:10:00Z"},
            {"violation_type": "gaze_away", "severity": "low",
             "created_at": "2025-01-01T09:15:00Z"},
            {"violation_type": "gaze_away", "severity": "low",
             "created_at": "2025-01-01T09:20:00Z"},
        ]
        result = generate_session_summary(violations, {
            "full_name": "Alice",
            "roll_number": "R001",
            "risk_score": 65,
        })
        assert result["severity"] == "critical"
        assert "Alice (R001)" in result["narrative"]
        assert "wrong_person" in result["narrative"] or "different person" in result["narrative"]
        assert "phone" in result["narrative"].lower()
        assert "gaze away (2)" in result["narrative"]
        assert "65/100" in result["narrative"]

    def test_highlights_capped_at_20(self):
        violations = []
        for i in range(50):
            violations.append({
                "violation_type": "gaze_away",
                "severity": "low",
                "created_at": f"2025-01-01T09:{i:02d}:00Z",
            })
        result = generate_session_summary(violations)
        assert len(result["highlights"]) <= 20

    def test_non_violation_events_ignored(self):
        violations = [
            {"violation_type": "heartbeat", "severity": "low",
             "created_at": "2025-01-01T09:00:00Z"},
            {"violation_type": "exam_started", "severity": "low",
             "created_at": "2025-01-01T09:01:00Z"},
        ]
        result = generate_session_summary(violations)
        assert result["severity"] == "clean"
        assert "No suspicious activity" in result["narrative"]

    def test_dismissed_violations_excluded(self):
        # Due-process: a flag dismissed by a teacher or an accepted appeal
        # must drop out of the narrative entirely (phase94 remediation hook).
        violations = [
            {"violation_type": "wrong_person", "severity": "high",
             "created_at": "2025-01-01T09:00:00Z",
             "dismissed_at": "2025-01-01T10:00:00Z",
             "dismissed_reason": "appeal_accepted"},
        ]
        result = generate_session_summary(violations)
        assert result["severity"] == "clean"
        assert "No suspicious activity" in result["narrative"]

    def test_dismissed_one_of_many_excluded(self):
        # The active flag still summarizes; only the dismissed one is gone.
        violations = [
            {"violation_type": "wrong_person", "severity": "high",
             "created_at": "2025-01-01T09:00:00Z",
             "dismissed_at": "2025-01-01T10:00:00Z"},
            {"violation_type": "gaze_away", "severity": "low",
             "created_at": "2025-01-01T09:05:00Z"},
            {"violation_type": "gaze_away", "severity": "low",
             "created_at": "2025-01-01T09:10:00Z"},
        ]
        result = generate_session_summary(violations)
        assert "different person" not in result["narrative"]
        assert "gaze away (2" in result["narrative"]

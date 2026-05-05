"""Tests for real-time alert publishing logic.

Uses a local copy of _CRITICAL_TYPES to avoid importing the app
(which requires DB/Redis). The set is kept in sync with
app/dependencies.py — any mismatch will fail the mirror test.
"""

import pytest

# Local mirror of app/dependencies.py _CRITICAL_TYPES
_CRITICAL_TYPES = frozenset({
    "phone_consulting", "collaboration", "answer_memo",
    "note_reading", "wrong_person", "calibration_abort",
    "cheat_object_detected", "vm_detected",
    "remote_desktop_detected",
})


class TestCriticalAlertTypes:
    """Verify the critical alert type classification."""

    def test_critical_types_is_frozenset(self):
        assert isinstance(_CRITICAL_TYPES, frozenset)

    def test_phone_consulting_is_critical(self):
        assert "phone_consulting" in _CRITICAL_TYPES

    def test_collaboration_is_critical(self):
        assert "collaboration" in _CRITICAL_TYPES

    def test_wrong_person_is_critical(self):
        assert "wrong_person" in _CRITICAL_TYPES

    def test_cheat_object_is_critical(self):
        assert "cheat_object_detected" in _CRITICAL_TYPES

    def test_vm_detected_is_critical(self):
        assert "vm_detected" in _CRITICAL_TYPES

    def test_remote_desktop_is_critical(self):
        assert "remote_desktop_detected" in _CRITICAL_TYPES

    def test_answer_memo_is_critical(self):
        assert "answer_memo" in _CRITICAL_TYPES

    def test_note_reading_is_critical(self):
        assert "note_reading" in _CRITICAL_TYPES

    def test_calibration_abort_is_critical(self):
        assert "calibration_abort" in _CRITICAL_TYPES

    def test_gaze_away_not_critical(self):
        """Gaze away is behavioral, not critical."""
        assert "gaze_away" not in _CRITICAL_TYPES

    def test_tab_switch_not_critical(self):
        """Tab switch is standard violation."""
        assert "tab_switch" not in _CRITICAL_TYPES

    def test_face_absent_not_critical(self):
        """Face absent is standard, not critical."""
        assert "face_absent" not in _CRITICAL_TYPES

    def test_multiple_people_not_critical(self):
        """Multiple people is important but not in critical set."""
        assert "multiple_people" not in _CRITICAL_TYPES


class TestAlertFiltering:
    """Test the alert filtering logic without Redis dependency."""

    def _should_alert(self, violation_type, severity):
        """Replicates the filtering logic from publish_critical_alert."""
        return (violation_type in _CRITICAL_TYPES or
                severity in ("critical", "high"))

    def test_critical_type_alerts(self):
        assert self._should_alert("phone_consulting", "medium") is True

    def test_high_severity_alerts(self):
        assert self._should_alert("gaze_away", "high") is True

    def test_critical_severity_alerts(self):
        assert self._should_alert("tab_switch", "critical") is True

    def test_medium_standard_no_alert(self):
        assert self._should_alert("tab_switch", "medium") is False

    def test_low_severity_no_alert(self):
        assert self._should_alert("gaze_away", "low") is False

    def test_collaboration_always_alerts(self):
        assert self._should_alert("collaboration", "low") is True

    def test_wrong_person_always_alerts(self):
        assert self._should_alert("wrong_person", "medium") is True


class TestDependencyMirror:
    """Ensure local test copy matches app/dependencies.py."""

    def test_critical_types_mirror(self):
        """If this fails, update the local mirror in this file."""
        from app.dependencies import _CRITICAL_TYPES as real_types
        assert _CRITICAL_TYPES == real_types, (
            "Local _CRITICAL_TYPES mirror in test_alerts.py is out of sync. "
            "Update the local copy to match app/dependencies.py."
        )

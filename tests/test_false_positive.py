from app.services.false_positive import explain_flag, normalize_sensitivity


def test_proctoring_sensitivity_migration_adds_guarded_config():
    sql = open("migrations/phase56_proctoring_sensitivity.sql", encoding="utf-8").read().lower()
    assert "add column if not exists proctoring_sensitivity" in sql
    assert "strict" in sql
    assert "balanced" in sql
    assert "lenient" in sql
    assert "exam_config_proctoring_sensitivity_check" in sql


def test_normalize_sensitivity_defaults_unknown_values():
    assert normalize_sensitivity("strict") == "strict"
    assert normalize_sensitivity("LENIENT") == "lenient"
    assert normalize_sensitivity("noisy") == "balanced"
    assert normalize_sensitivity(None) == "balanced"


def test_low_confidence_gaze_flag_recommends_human_review():
    result = explain_flag(
        {
            "violation_type": "gaze_away",
            "severity": "medium",
            "detection_confidence": 0.61,
        },
        calibration={"tier": "tight", "reason": "Narrow range."},
        sensitivity="balanced",
    )

    assert result["confidence_label"] == "low"
    assert result["reliability"] == "needs_review"
    assert result["human_review_recommended"] is True
    assert "context_sensitive_detector" in result["reason_codes"]
    assert "calibration_tight" in result["reason_codes"]


def test_high_confidence_focus_loss_is_strong_signal():
    result = explain_flag(
        {
            "violation_type": "window_focus_lost",
            "severity": "medium",
            "detection_confidence": 0.99,
        },
        calibration={"tier": "normal", "reason": "Calibration within typical envelope."},
        sensitivity="balanced",
    )

    assert result["confidence_label"] == "high"
    assert result["reliability"] == "strong"
    assert result["human_review_recommended"] is False

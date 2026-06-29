"""False-positive controls and teacher-facing flag explanations."""

from __future__ import annotations

from typing import Any

from .risk import _is_violation

SENSITIVITY_PRESETS = {
    "strict": {
        "label": "Strict",
        "description": "Flags more events. Best for high-stakes exams with manual review capacity.",
        "review_threshold": 0.55,
    },
    "balanced": {
        "label": "Balanced",
        "description": "Default balance between catching risk and limiting false positives.",
        "review_threshold": 0.70,
    },
    "lenient": {
        "label": "Lenient",
        "description": "Reduces noisy flags. Best for practice tests or low-stakes assessments.",
        "review_threshold": 0.82,
    },
}

DEFAULT_CONFIDENCE_BY_TYPE = {
    "window_focus_lost": 0.99,
    "tab_hidden": 0.99,
    "shortcut_blocked": 0.98,
    "face_missing": 0.90,
    "multiple_faces": 0.88,
    "camera_covered": 0.88,
    "eyes_closed": 0.82,
    "wrong_person": 0.78,
    "voice_detected": 0.75,
    "earphone_detected": 0.72,
    "gaze_away": 0.68,
    "head_turned": 0.68,
    "head_away": 0.68,
    "lighting_issue": 0.60,
}

LOW_CONFIDENCE_TYPES = {"gaze_away", "head_turned", "head_away", "lighting_issue", "voice_detected"}


def normalize_sensitivity(value: str | None) -> str:
    value = str(value or "balanced").strip().lower()
    return value if value in SENSITIVITY_PRESETS else "balanced"


def confidence_value(event: dict[str, Any]) -> float | None:
    raw = event.get("detection_confidence")
    if raw is not None:
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return None
    vtype = str(event.get("violation_type") or event.get("type") or "")
    return DEFAULT_CONFIDENCE_BY_TYPE.get(vtype)


def confidence_label(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.85:
        return "high"
    if value >= 0.70:
        return "medium"
    return "low"


def explain_flag(
    event: dict[str, Any],
    *,
    calibration: dict[str, Any] | None = None,
    sensitivity: str | None = None,
) -> dict[str, Any]:
    """Return a stable explanation object for review UI and exports."""
    vtype = str(event.get("violation_type") or event.get("type") or "")
    severity = str(event.get("severity") or "low").lower()
    sensitivity = normalize_sensitivity(sensitivity)
    preset = SENSITIVITY_PRESETS[sensitivity]
    conf = confidence_value(event)
    label = confidence_label(conf)
    is_violation = _is_violation(vtype)

    reason_codes: list[str] = []
    notes: list[str] = []
    if not is_violation:
        reason_codes.append("housekeeping_event")
        notes.append("This is an audit event, not a cheating signal.")
    if conf is None:
        reason_codes.append("confidence_unavailable")
        notes.append("Detector did not provide a calibrated confidence score.")
    elif conf < float(str(preset.get("review_threshold", 0))):
        reason_codes.append("below_sensitivity_threshold")
        notes.append(
            f"{preset['label']} mode expects confidence >= {preset['review_threshold']:.0%}; "
            f"this event is {conf:.0%}."
        )
    if vtype in LOW_CONFIDENCE_TYPES:
        reason_codes.append("context_sensitive_detector")
        notes.append("This detector can be affected by lighting, posture, camera angle, or room noise.")

    cal_tier = (calibration or {}).get("tier")
    if cal_tier in {"tight", "loose", "missing"}:
        reason_codes.append(f"calibration_{cal_tier}")
        notes.append((calibration or {}).get("reason") or "Calibration quality may affect detector reliability.")

    if severity in {"critical", "high"}:
        reason_codes.append("high_severity")
    elif severity == "medium":
        reason_codes.append("medium_severity")

    human_review = (
        is_violation
        and (
            label in {"low", "unknown"}
            or "below_sensitivity_threshold" in reason_codes
            or cal_tier in {"tight", "loose", "missing"}
            or severity in {"critical", "high"}
        )
    )
    reliability = "strong"
    if label in {"low", "unknown"} or cal_tier in {"tight", "loose", "missing"}:
        reliability = "needs_review"
    elif "below_sensitivity_threshold" in reason_codes or vtype in LOW_CONFIDENCE_TYPES:
        reliability = "moderate"

    return {
        "confidence": conf,
        "confidence_label": label,
        "sensitivity": sensitivity,
        "sensitivity_label": preset["label"],
        "review_threshold": preset["review_threshold"],
        "reliability": reliability,
        "human_review_recommended": human_review,
        "reason_codes": reason_codes,
        "explanation": " ".join(notes) if notes else "Signal is consistent with the configured sensitivity profile.",
    }

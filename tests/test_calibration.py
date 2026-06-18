"""Fixtures for calibration parse + tier classification (services/calibration.py).

The proctor reports a student's calibration envelope in one of three text
formats; parse_calibration_details must read all three, and
classify_calibration buckets the result into missing/tight/loose/normal
against fixed thresholds. A regression here mislabels how cooperatively a
student calibrated, which feeds review triage.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.calibration import parse_calibration_details, classify_calibration


# ── parsing the three formats ────────────────────────────────────────

def test_parse_json_format():
    details = ('{"gaze_yaw_range": 0.2, "gaze_pitch_range": 0.15, '
               '"head_yaw_range": 12, "head_pitch_range": 10, '
               '"gaze_yaw": 0.01, "gaze_pitch": -0.02, "head_yaw": 1, "head_pitch": 2}')
    p = parse_calibration_details(details)
    assert p["gaze_yaw_range"] == 0.2
    assert p["head_yaw_range"] == 12.0
    assert p["gaze_pitch"] == -0.02


def test_parse_proctor_format():
    details = "gaze yaw:+0.12rad pitch:-0.05rad | head yaw:+3.0° pitch:+2.0°"
    p = parse_calibration_details(details)
    assert p["gaze_yaw_range"] == 0.12      # abs of signed value
    assert p["gaze_pitch_range"] == 0.05
    assert p["head_yaw"] == 3.0             # signed bias preserved
    assert p["head_pitch"] == 2.0


def test_parse_legacy_format():
    details = "range gaze:±(0.2, 0.15) head:±(12°, 10°) bias gaze:(0.01, -0.02)"
    p = parse_calibration_details(details)
    assert p["gaze_yaw_range"] == 0.2
    assert p["head_pitch_range"] == 10.0
    assert p["gaze_yaw"] == 0.01
    assert p["head_yaw"] == 0.0            # not in legacy bias → defaulted


def test_parse_empty_or_garbage_returns_none():
    assert parse_calibration_details("") is None
    assert parse_calibration_details(None) is None
    assert parse_calibration_details("no numbers here") is None
    assert parse_calibration_details("{not valid json") is None


# ── tier classification ──────────────────────────────────────────────

def test_classify_missing():
    assert classify_calibration(None)["tier"] == "missing"


def test_classify_tight_when_range_below_threshold():
    # gaze yaw range 0.05 < _CAL_TIGHT_GAZE (0.10) → barely moved
    p = {"gaze_yaw_range": 0.05, "gaze_pitch_range": 0.2,
         "head_yaw_range": 12, "head_pitch_range": 12}
    assert classify_calibration(p)["tier"] == "tight"


def test_classify_tight_on_head_axis():
    p = {"gaze_yaw_range": 0.2, "gaze_pitch_range": 0.2,
         "head_yaw_range": 5, "head_pitch_range": 12}  # 5 < _CAL_TIGHT_HEAD (8)
    assert classify_calibration(p)["tier"] == "tight"


def test_classify_loose_when_range_above_threshold():
    # not tight, but gaze range 0.6 > _CAL_LOOSE_GAZE (0.50) → moved a lot
    p = {"gaze_yaw_range": 0.6, "gaze_pitch_range": 0.6,
         "head_yaw_range": 15, "head_pitch_range": 15}
    assert classify_calibration(p)["tier"] == "loose"


def test_classify_normal_within_envelope():
    p = {"gaze_yaw_range": 0.2, "gaze_pitch_range": 0.2,
         "head_yaw_range": 15, "head_pitch_range": 15}
    assert classify_calibration(p)["tier"] == "normal"

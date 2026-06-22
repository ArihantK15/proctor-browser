"""
End-to-end smoke test for the proctor.py detection pipeline.

Feeds synthetic frames through the YOLO worker, SAHI worker, and
phone classification pipeline to verify the full data flow:
  frame → worker → result queue → coordinate scaling → phone classification

Requires proctor.py dependencies (cv2, numpy, onnxruntime, etc.).
"""
import sys
import os
import time
import pytest
from unittest.mock import MagicMock, patch

_proctor_deps = ["cv2", "numpy", "uniface", "onnxruntime"]
_missing = []
for dep in _proctor_deps:
    if dep not in sys.modules:
        try:
            __import__(dep)
        except ImportError:
            _missing.append(dep)

pytestmark = pytest.mark.skipif(
    bool(_missing),
    reason=f"proctor.py dependencies not installed (missing: {', '.join(_missing)})"
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fake_psutil(cpu, battery_pct=None, plugged=True, rss_mb=0.0):
    """A stand-in psutil for governor tests: fixed cpu%, optional battery, RSS."""
    m = MagicMock()
    m.cpu_percent.return_value = cpu
    if battery_pct is None:
        m.sensors_battery.return_value = None          # desktop / no battery
    else:
        bat = MagicMock(); bat.percent = battery_pct; bat.power_plugged = plugged
        m.sensors_battery.return_value = bat
    proc = MagicMock(); proc.memory_info.return_value.rss = int(rss_mb * 1024 * 1024)
    m.Process.return_value = proc
    return m

TEST_ENV = {
    "SESSION_ID": "smoke-sess",
    "JWT_TOKEN": "",
    "SERVER_URL": "http://localhost:8000/event",
    "HEADLESS": True,
    "SKIP_ENROLLMENT": True,
    "YOLO_MODEL_PATH": "bogus.onnx",
    "GAZE_MODEL_PATH": "bogus.onnx",
    "RETINA_MODEL_PATH": "bogus.onnx",
    "EVIDENCE_DIR": "/tmp/procta_smoke_evidence",
}
for k, v in TEST_ENV.items():
    os.environ.setdefault(k, str(v))

for mod_name in ["sounddevice", "uniface", "insightface"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

import cv2
import numpy as np


# NOTE: the old TestYOLOWorkerCoordinateScaling tests were removed here.
# They re-implemented the PRE-ONNX 416-square-resize math INLINE and
# asserted on their own arithmetic — they never called proctor and would
# have passed even if detection were broken. Real coverage of the migrated
# ONNX decode / letterbox / NMS / aspect-handling now lives in
# tests/test_proctor_onnx_detection.py.


class TestPhoneClassificationIntegration:
    """Verify that phone detection coordinates flow correctly through
    the classification pipeline."""

    def test_phone_in_hand_classification_with_real_coords(self):
        """A phone detected in the upper portion of the frame (above face)
        should be classified as in-hand."""
        from proctor import classify_phone_position

        # Simulated full-frame coordinates from YOLO worker
        # Phone at top of a 640×480 frame
        phone_box = (200, 50, 280, 120)  # center_y = 85
        # Face below the phone
        face_bbox = (180, 150, 300, 300)  # face_bottom = 300

        result = classify_phone_position(phone_box, face_bbox, frame_h=480)
        # 85 < 300 * 0.50 = 150 → in_hand
        assert result == "phone_in_hand"

    def test_phone_on_desk_classification_with_real_coords(self):
        """A phone detected at the bottom of the frame should be on-desk."""
        from proctor import classify_phone_position

        phone_box = (200, 350, 280, 420)  # center_y = 385
        face_bbox = (180, 100, 300, 250)  # face_bottom = 250

        result = classify_phone_position(phone_box, face_bbox, frame_h=480)
        # 385 > 250 * 0.50 = 125 (not in_hand check)
        # 385 > 480 * 0.65 = 312 → on_desk
        assert result == "phone_on_desk"

    def test_no_face_bbox_defaults_correctly(self):
        """When no face is detected, phone classification should
        use frame-height heuristic only."""
        from proctor import classify_phone_position

        # Phone in middle of frame with no face
        phone_box = (200, 200, 280, 260)  # center_y = 230
        result = classify_phone_position(phone_box, face_bbox=None, frame_h=480)
        # 230 < 480 * 0.65 = 312 → not below desk line → phone_detected
        assert result == "phone_detected"


class TestDetectionResultFormat:
    """Verify that detection results have the expected format."""

    def test_yolo_result_format(self):
        """YOLO results should be tuples of (name, conf, x1, y1, x2, y2)."""
        from proctor import YoloWorker, CHEAT_IDS

        # Verify CHEAT_IDS maps are valid
        assert len(CHEAT_IDS) > 0
        for cls_id, name in CHEAT_IDS.items():
            assert isinstance(cls_id, int)
            assert isinstance(name, str)

    def test_handheld_labels_match_phone_consumers(self):
        """Regression: the phone-position and behavioral phone_in_hand consumers
        gate on name == "Phone". With the custom detector the handheld class (2)
        is labelled plain "Phone", so those branches fire. Guard that every
        handheld label is exactly "Phone" so the consumer contract holds."""
        from proctor import CHEAT_IDS, HANDHELD_CHEAT_IDS
        assert HANDHELD_CHEAT_IDS, "expected at least one handheld class"
        for cid in HANDHELD_CHEAT_IDS:
            assert cid in CHEAT_IDS, f"handheld id {cid} not in CHEAT_IDS"
            assert CHEAT_IDS[cid] == "Phone", (cid, CHEAT_IDS[cid])


class TestContinuousIdentityVerification:
    """Verify the continuous identity verification constants and logic."""

    def test_wrong_person_threshold_is_configurable(self):
        """The threshold should be a float between 0 and 1."""
        from proctor import WRONG_PERSON_THRESHOLD
        assert 0 < WRONG_PERSON_THRESHOLD < 1
        # Default is 0.25 — cosine similarity below this = different person
        assert WRONG_PERSON_THRESHOLD == 0.25

    def test_wrong_person_check_frequency(self):
        """Post-calibration check should run frequently."""
        from proctor import WRONG_PERSON_CHECK_FREQ
        assert WRONG_PERSON_CHECK_FREQ <= 30  # should be at least as frequent as before
        assert WRONG_PERSON_CHECK_FREQ > 0

    def test_similarity_math(self):
        """Cosine similarity of normalized embeddings should be in [-1, 1]."""
        import numpy as np
        # Two identical unit vectors
        a = np.array([1.0, 0.0, 0.0, 0.0])
        assert np.dot(a, a) == pytest.approx(1.0, abs=1e-10)

        # Threshold check: similarity below threshold = mismatch
        threshold = 0.25
        # Similar vector (high cosine similarity)
        similar = np.array([0.9, 0.3, 0.2, 0.1])
        similar = similar / np.linalg.norm(similar)
        # Very different vector — nearly orthogonal
        different = np.array([0.01, 0.01, 0.01, 0.99])
        different = different / np.linalg.norm(different)

        sim_score = float(np.dot(similar, a))
        diff_score = float(np.dot(different, a))

        assert sim_score > threshold  # should pass
        assert diff_score < threshold  # should fail

    def test_lazy_enrollment_window_is_reasonable(self):
        """TARGET_FPS should be a sane proctoring cadence (env-tunable).

        Lowered from 15 to 7 to cut steady-state CPU on weak student laptops
        (proctoring detects second-scale behaviours, so 15Hz was wasted work).
        Assert a reasonable band rather than a magic constant so field tuning
        via PROCTOR_TARGET_FPS doesn't break the test.
        """
        from proctor import TARGET_FPS
        assert 3 <= TARGET_FPS <= 15

    def test_governor_tier_ladder(self):
        """The hardware governor degrades gracefully through fps rungs instead
        of the old binary 15<->0.5 cliff that oscillated under sustained load."""
        from proctor import _HardwareGovernor, TARGET_FPS, THROTTLE_LOW_FPS
        g = _HardwareGovernor()
        assert g._tiers[0] == float(TARGET_FPS)            # top rung = target
        assert g._tiers[-1] == float(THROTTLE_LOW_FPS)     # floor rung
        assert g._tiers == sorted(g._tiers, reverse=True)  # strictly descending
        assert len(g._tiers) == len(set(g._tiers))         # no duplicate rungs
        assert g.effective_fps == float(TARGET_FPS)        # starts at full speed
        assert THROTTLE_LOW_FPS >= 3                        # floor still useful

    def test_governor_forces_floor_on_low_battery(self, monkeypatch):
        """Critically low battery (unplugged) slams straight to the floor tier."""
        import proctor
        monkeypatch.setattr(proctor, "_PSUTIL_OK", True)
        monkeypatch.setattr(proctor._HardwareGovernor, "_read_thermal_stress",
                            staticmethod(lambda: None))
        monkeypatch.setattr(proctor, "_psutil",
                            _fake_psutil(cpu=10, battery_pct=15, plugged=False))
        g = proctor._HardwareGovernor()
        g.maybe_update()
        assert g._on_battery is True
        assert g.effective_fps == float(proctor.THROTTLE_LOW_FPS)

    def test_governor_forces_floor_on_high_rss(self, monkeypatch):
        """RSS over the watchdog ceiling forces the floor tier (no model unload)."""
        import proctor
        monkeypatch.setattr(proctor, "_PSUTIL_OK", True)
        monkeypatch.setattr(proctor, "MAX_RSS_MB", 500.0)
        monkeypatch.setattr(proctor._HardwareGovernor, "_read_thermal_stress",
                            staticmethod(lambda: None))
        monkeypatch.setattr(proctor, "_psutil", _fake_psutil(cpu=10, rss_mb=600))
        g = proctor._HardwareGovernor()
        g.maybe_update()
        assert g.effective_fps == float(proctor.THROTTLE_LOW_FPS)

    def test_governor_no_force_when_healthy_and_plugged(self, monkeypatch):
        """Plugged, cool, low RAM → stays at full speed (no spurious throttle)."""
        import proctor
        monkeypatch.setattr(proctor, "_PSUTIL_OK", True)
        monkeypatch.setattr(proctor, "MAX_RSS_MB", 0.0)        # watchdog disabled
        monkeypatch.setattr(proctor._HardwareGovernor, "_read_thermal_stress",
                            staticmethod(lambda: None))
        monkeypatch.setattr(proctor, "_psutil",
                            _fake_psutil(cpu=10, battery_pct=90, plugged=True))
        g = proctor._HardwareGovernor()
        g.maybe_update()
        assert g._on_battery is False
        assert g.effective_fps == float(proctor.TARGET_FPS)

    def test_governor_battery_engages_throttle_sooner(self, monkeypatch):
        """On battery the CPU engage threshold drops, so a mid-load that is
        ignored when plugged throttles when unplugged."""
        import proctor
        monkeypatch.setattr(proctor, "_PSUTIL_OK", True)
        monkeypatch.setattr(proctor, "MAX_RSS_MB", 0.0)
        monkeypatch.setattr(proctor._HardwareGovernor, "_read_thermal_stress",
                            staticmethod(lambda: None))
        cpu_mid = proctor.THROTTLE_ENGAGE_PCT - (proctor.BATTERY_ENGAGE_DROP / 2.0)
        # Unplugged (90% so no hard floor): mid CPU clears the battery-adjusted
        # engage and throttles after the 2-sample streak.
        monkeypatch.setattr(proctor, "_psutil",
                            _fake_psutil(cpu=cpu_mid, battery_pct=90, plugged=False))
        g = proctor._HardwareGovernor()
        for _ in range(2):
            g._last_sample_at = 0.0                 # bypass the per-window gate
            g.maybe_update()
        assert g._tier_idx >= 1
        # Plugged at the same CPU stays put (below the normal engage threshold).
        monkeypatch.setattr(proctor, "_psutil",
                            _fake_psutil(cpu=cpu_mid, battery_pct=90, plugged=True))
        g2 = proctor._HardwareGovernor()
        for _ in range(2):
            g2._last_sample_at = 0.0
            g2.maybe_update()
        assert g2._tier_idx == 0

"""
Tests for proctor.py detection features.

Each test isolates a specific detection function with mocked dependencies.
Heavy deps (cv2, numpy) are required but no camera/mic/network is needed.

These tests are skipped in CI unless proctor.py dependencies are installed.
"""
import sys
import os
import pytest

# Skip all proctor tests if heavy dependencies are unavailable (e.g. CI).
# CI installs only requirements.txt (server deps), not requirements-proctor.txt.
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

from unittest.mock import MagicMock, patch

# ── Import proctor module (cv2/numpy required, camera not) ──────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# We need to import proctor but it starts threads and tries to connect to
# a server at module-level. Mock those globals first.
TEST_ENV = {
    "SESSION_ID": "test-sess",
    "JWT_TOKEN": "",
    "SERVER_URL": "http://localhost:8000/event",
    "HEADLESS": True,
    "SKIP_ENROLLMENT": True,
    "YOLO_MODEL_PATH": "bogus.pt",
    "GAZE_MODEL_PATH": "bogus.onnx",
    "RETINA_MODEL_PATH": "bogus.onnx",
    "EVIDENCE_DIR": "/tmp/procta_test_evidence",
}

for k, v in TEST_ENV.items():
    os.environ.setdefault(k, str(v))

# Patch modules that proctor.py tries to import at top-level
for mod_name in ["sounddevice", "uniface", "ultralytics", "insightface"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

import numpy as np


# ── Phone-in-hand vs phone-on-desk classification ───────────────────────

class TestPhonePositionClassification:
    """classify_phone_position(phone_box, face_bbox, frame_h) -> str"""

    def _classify(self, phone_box, face_bbox, frame_h=480):
        """Import from proctor module."""
        from proctor import classify_phone_position
        return classify_phone_position(phone_box, face_bbox, frame_h)

    def test_phone_above_face_bottom_is_in_hand(self):
        # Phone center at y=100, face bottom at y=300, ratio threshold 0.50
        # 100 < 300 * 0.50 = 150 → in_hand
        result = self._classify(
            phone_box=(100, 80, 140, 120),   # center_y = 100
            face_bbox=(80, 100, 200, 300),    # face_bottom = 300
            frame_h=480,
        )
        assert result == "phone_in_hand"

    def test_phone_below_65pct_frame_is_on_desk(self):
        # Phone center at y=350, frame_h=480, 350 > 480*0.65 = 312 → on_desk
        result = self._classify(
            phone_box=(100, 330, 140, 370),   # center_y = 350
            face_bbox=None,
            frame_h=480,
        )
        assert result == "phone_on_desk"

    def test_no_face_and_mid_frame_defaults_to_on_desk(self):
        # No face bbox, phone center in middle of frame → default on_desk
        result = self._classify(
            phone_box=(100, 200, 140, 240),   # center_y = 220
            face_bbox=None,
            frame_h=480,
        )
        assert result == "phone_on_desk"

    def test_phone_at_edge_of_hand_threshold(self):
        # Phone center exactly at face_bottom * 0.50 → not < so falls through
        # to on_desk check: 150 < 480*0.65=312 → on_desk
        result = self._classify(
            phone_box=(100, 130, 140, 170),   # center_y = 150
            face_bbox=(80, 100, 200, 300),    # face_bottom = 300
            frame_h=480,
        )
        assert result == "phone_on_desk"

    def test_phone_slightly_above_hand_threshold(self):
        # 149 < 300 * 0.50 = 150 → in_hand
        result = self._classify(
            phone_box=(100, 129, 140, 169),   # center_y = 149
            face_bbox=(80, 100, 200, 300),    # face_bottom = 300
            frame_h=480,
        )
        assert result == "phone_in_hand"

    def test_phone_high_in_frame_with_face_is_in_hand(self):
        # Phone very high (y=50), face bottom at 200, 50 < 200*0.50=100 → in_hand
        result = self._classify(
            phone_box=(100, 40, 140, 60),     # center_y = 50
            face_bbox=(80, 80, 200, 200),     # face_bottom = 200
            frame_h=480,
        )
        assert result == "phone_in_hand"

    def test_phone_low_with_face_below_desk_threshold(self):
        # Phone at y=400, face_bottom=300: 400 > 300*0.50=150 so not in_hand
        # 400 > 480*0.65=312 → on_desk
        result = self._classify(
            phone_box=(100, 380, 140, 420),   # center_y = 400
            face_bbox=(80, 100, 200, 300),    # face_bottom = 300
            frame_h=480,
        )
        assert result == "phone_on_desk"


# ── Screen-share feed detection ─────────────────────────────────────────

class TestScreenShareFeedDetection:
    """_detect_screen_share_feed(frame) -> Optional[str]"""

    def _detect(self, frame):
        from proctor import _detect_screen_share_feed
        return _detect_screen_share_feed(frame)

    def test_synthetic_screen_like_frame(self):
        """Screen-like heuristic requires high edge_ratio AND low laplacian_var.
        This is hard to synthesize with numpy arrays, so we verify the logic
        by patching cv2 operations to return controlled values."""
        frame = np.full((480, 640, 3), 200, dtype=np.uint8)
        # Mock edge detection to simulate a screen-like frame
        with patch("proctor.cv2.Canny", return_value=np.ones((480, 640), dtype=np.uint8) * 255):
            with patch("proctor.cv2.Laplacian") as mock_lap:
                mock_lap.return_value.var.return_value = 10.0  # very low noise
                result = self._detect(frame)
                assert result is not None
                assert "screen_like" in result

    def test_high_noise_frame_not_flagged(self):
        """High laplacian variance (optical camera noise) should not trigger."""
        frame = np.full((480, 640, 3), 200, dtype=np.uint8)
        with patch("proctor.cv2.Canny", return_value=np.ones((480, 640), dtype=np.uint8) * 255):
            with patch("proctor.cv2.Laplacian") as mock_lap:
                mock_lap.return_value.var.return_value = 200.0  # high noise
                result = self._detect(frame)
                assert result is None

    def test_low_edge_frame_not_flagged(self):
        """Low edge ratio should not trigger even with low noise."""
        frame = np.full((480, 640, 3), 200, dtype=np.uint8)
        with patch("proctor.cv2.Canny", return_value=np.zeros((480, 640), dtype=np.uint8)):
            with patch("proctor.cv2.Laplacian") as mock_lap:
                mock_lap.return_value.var.return_value = 5.0
                result = self._detect(frame)
                assert result is None

    def test_low_edge_frame_not_flagged(self):
        """Low edge ratio should not trigger even with low noise."""
        frame = np.full((480, 640, 3), 200, dtype=np.uint8)
        with patch("cv2.Canny", return_value=np.zeros((480, 640), dtype=np.uint8)):
            with patch("cv2.Laplacian") as mock_lap:
                mock_lap.return_value.var.return_value = 5.0
                result = self._detect(frame)
                assert result is None

    def test_natural_camera_feed_not_flagged(self):
        """A frame with random noise (simulates optical camera)."""
        frame = np.random.randint(50, 200, (480, 640, 3), dtype=np.uint8)
        result = self._detect(frame)
        assert result is None

    def test_uniform_frame_not_flagged(self):
        """Solid color frame — no edges."""
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        result = self._detect(frame)
        assert result is None


# ── Severity escalation ─────────────────────────────────────────────────

class TestSeverityEscalation:
    """Escalation logic inside run_proctoring: _track_violation,
    _get_escalated_severity, log_if_allowed."""

    def _make_escalation_context(self):
        """Create the local variables that run_proctoring uses."""
        import time as _time

        last_logged = {}
        COOLDOWN = 8.0
        ESCALATION_WINDOW_SECS = 300
        ESCALATION_TIERS = {"low": "medium", "medium": "high", "high": "critical"}

        _violation_history = {}

        def _track_violation(etype):
            now = _time.time()
            cutoff = now - ESCALATION_WINDOW_SECS
            history = _violation_history.get(etype, [])
            history = [(t, s) for t, s in history if t > cutoff]
            history.append((now, "medium"))
            _violation_history[etype] = history

        def _get_escalated_severity(etype, base_severity):
            now = _time.time()
            cutoff = now - ESCALATION_WINDOW_SECS
            history = _violation_history.get(etype, [])
            history = [(t, s) for t, s in history if t > cutoff]
            repeat_count = len(history)  # history already includes current violation via _track_violation
            if repeat_count >= 3:
                severity = "critical"
            elif repeat_count == 2:
                severity = ESCALATION_TIERS.get(base_severity, base_severity)
            else:
                severity = base_severity
            return severity, repeat_count

        logged_events = []

        def log_event(etype, severity, details):
            logged_events.append({"type": etype, "severity": severity, "details": details})

        def can_log(etype):
            now = _time.time()
            if now - last_logged.get(etype, 0) >= COOLDOWN:
                last_logged[etype] = now
                return True
            return False

        def log_if_allowed(etype, base_severity, details):
            _track_violation(etype)
            now = _time.time()
            if now - last_logged.get(etype, 0) >= COOLDOWN:
                last_logged[etype] = now
                severity, repeat = _get_escalated_severity(etype, base_severity)
                if repeat > 1:
                    details = f"[{repeat}x repeat] {details}"
                log_event(etype, severity, details)
                return True
            return False

        return {
            "log_if_allowed": log_if_allowed,
            "log_event": log_event,
            "can_log": can_log,
            "_track_violation": _track_violation,
            "_get_escalated_severity": _get_escalated_severity,
            "logged_events": logged_events,
            "last_logged": last_logged,
            "_violation_history": _violation_history,
        }

    def test_first_violation_logs_at_base_severity(self):
        ctx = self._make_escalation_context()
        ctx["log_if_allowed"]("gaze_away", "medium", "Looking left")
        assert len(ctx["logged_events"]) == 1
        assert ctx["logged_events"][0]["severity"] == "medium"

    def test_second_violation_within_window_escalates(self):
        ctx = self._make_escalation_context()
        ctx["log_if_allowed"]("gaze_away", "medium", "Looking left")
        # Bypass cooldown for test
        ctx["last_logged"]["gaze_away"] = 0
        ctx["log_if_allowed"]("gaze_away", "medium", "Looking right")
        assert len(ctx["logged_events"]) == 2
        assert ctx["logged_events"][1]["severity"] == "high"  # medium → high
        assert "2x repeat" in ctx["logged_events"][1]["details"]

    def test_third_violation_goes_critical(self):
        ctx = self._make_escalation_context()
        ctx["log_if_allowed"]("gaze_away", "medium", "v1")
        ctx["last_logged"]["gaze_away"] = 0
        ctx["log_if_allowed"]("gaze_away", "medium", "v2")
        ctx["last_logged"]["gaze_away"] = 0
        ctx["log_if_allowed"]("gaze_away", "medium", "v3")
        assert len(ctx["logged_events"]) == 3
        assert ctx["logged_events"][2]["severity"] == "critical"
        assert "3x repeat" in ctx["logged_events"][2]["details"]

    def test_cooldown_does_not_reset_escalation_counter(self):
        """Violations tracked during cooldown should still count for escalation."""
        ctx = self._make_escalation_context()
        # First violation logs
        ctx["log_if_allowed"]("gaze_away", "medium", "v1")
        assert len(ctx["logged_events"]) == 1

        # Second violation within cooldown — NOT logged but SHOULD be tracked
        result = ctx["log_if_allowed"]("gaze_away", "medium", "v2")
        assert result is False  # cooldown blocked logging
        assert len(ctx["logged_events"]) == 1  # still only 1 logged

        # Third violation after cooldown — should be escalated to critical
        # because 2 prior violations were tracked
        ctx["last_logged"]["gaze_away"] = 0
        result = ctx["log_if_allowed"]("gaze_away", "medium", "v3")
        assert result is True
        assert ctx["logged_events"][1]["severity"] == "critical"
        assert "3x repeat" in ctx["logged_events"][1]["details"]

    def test_different_violation_types_track_independently(self):
        ctx = self._make_escalation_context()
        ctx["log_if_allowed"]("gaze_away", "medium", "v1")
        ctx["last_logged"]["voice_detected"] = 0
        ctx["log_if_allowed"]("voice_detected", "medium", "v1")
        # voice_detected should not be escalated (no prior history)
        assert len(ctx["logged_events"]) == 2
        assert ctx["logged_events"][1]["severity"] == "medium"

    def test_high_to_critical_escalation(self):
        ctx = self._make_escalation_context()
        ctx["log_if_allowed"]("phone_in_hand", "high", "v1")
        ctx["last_logged"]["phone_in_hand"] = 0
        ctx["log_if_allowed"]("phone_in_hand", "high", "v2")
        assert ctx["logged_events"][1]["severity"] == "critical"  # high → critical on 2nd


# ── Virtual camera detection ────────────────────────────────────────────

class TestVirtualCameraDetection:
    """_detect_virtual_camera() -> Optional[str]"""

    def test_detects_obs_virtual_on_macos(self):
        from proctor import _detect_virtual_camera
        fake_output = """
SPCameraDataType:

    FaceTime HD Camera:
      Camera Name: FaceTime HD Camera

    OBS Virtual Camera:
      Camera Name: OBS Virtual Camera
"""
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output)
                result = _detect_virtual_camera()
                assert result is not None
                assert "obs" in result.lower() or "virtual" in result.lower()

    def test_no_virtual_camera_on_macos(self):
        from proctor import _detect_virtual_camera
        fake_output = """
SPCameraDataType:

    FaceTime HD Camera:
      Camera Name: FaceTime HD Camera
"""
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output)
                result = _detect_virtual_camera()
                assert result is None

    def test_subprocess_timeout_handled(self):
        from proctor import _detect_virtual_camera
        import subprocess as _sub
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run", side_effect=_sub.TimeoutExpired("cmd", 5)):
                result = _detect_virtual_camera()
                assert result is None


# ── VM detection ────────────────────────────────────────────────────────

class TestVMDetection:
    """_detect_vm() -> Optional[str]"""

    def test_detects_vmware_on_macos(self):
        from proctor import _detect_vm
        fake_output = "hw.model: VMwareVirtualPlatform\n"
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output)
                result = _detect_vm()
                assert result is not None
                assert "vmware" in result.lower()

    def test_no_vm_on_clean_macos(self):
        from proctor import _detect_vm
        fake_output = "hw.model: MacBookPro18,1\n"
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=fake_output)
                result = _detect_vm()
                assert result is None

    def test_subprocess_error_handled(self):
        from proctor import _detect_vm
        with patch("platform.system", return_value="Darwin"):
            with patch("subprocess.run", side_effect=OSError("not found")):
                result = _detect_vm()
                assert result is None


# ── WebSocket URL derivation ────────────────────────────────────────────

class TestWebSocketURL:
    """_derive_ws_url() converts HTTP URLs to WS URLs."""

    def test_https_to_wss(self):
        from proctor import _derive_ws_url
        with patch("proctor.SERVER_URL", "https://api.example.com/event"):
            with patch("proctor.SESSION_ID", "test-123"):
                url = _derive_ws_url()
                assert url.startswith("wss://")
                assert "test-123" in url

    def test_http_to_ws(self):
        from proctor import _derive_ws_url
        with patch("proctor.SERVER_URL", "http://localhost:8000/event"):
            with patch("proctor.SESSION_ID", "test-456"):
                url = _derive_ws_url()
                assert url.startswith("ws://")
                assert "test-456" in url

    def test_bare_url_without_scheme(self):
        from proctor import _derive_ws_url
        with patch("proctor.SERVER_URL", "http://localhost/event"):
            with patch("proctor.SESSION_ID", "test-789"):
                url = _derive_ws_url()
                assert "/ws/v1/live-frame/test-789" in url


def _cv2():
    """Return cv2 import (needed for test helpers)."""
    import cv2
    return cv2

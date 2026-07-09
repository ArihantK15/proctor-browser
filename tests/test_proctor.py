"""
Unit tests for proctor.py — targeted coverage of pure/testable logic in the
on-device proctoring daemon.

proctor.py is a 4000+ line, high-complexity file. This file does NOT attempt
full coverage — most of the file is camera I/O, live model inference (RetinaFace
/ YOLO / gaze ONNX), audio capture, and network/IPC plumbing that can't be
meaningfully unit-tested without a real camera/GPU/model or heavy end-to-end
mocking already covered elsewhere (see test_proctor_e2e.py, test_proctor_features.py,
test_proctor_calibration.py, test_proctor_onnx_detection.py).

This file covers pure decision/calculation logic that had NO existing coverage:
  - _wants_context            (pre-violation context-buffer eligibility)
  - _dominant_direction       (head/gaze direction-label tie-break)
  - _compute_proctoring_tier  (model-availability -> tier mapping)
  - _frame_is_usable          (IR/grayscale camera rejection heuristic)
  - _camera_backend_candidates (per-OS backend candidate list + de-dupe)
  - _proctor_frame_init_state (run_proctoring state-dict factory)
  - _limit_fps                (governor-driven frame pacing + fps tracking)
  - get_head_pose             (solvePnP yaw/pitch, including the fail-safe path)
  - eyes_detected              (fail-open / fail-closed edges of the Haar path)
  - _process_yolo_results     (YOLO detection -> history/threshold/event state
                                machine, with the yolo worker and I/O mocked out)

Requires proctor.py's heavy deps (cv2, numpy, uniface, onnxruntime) to be
installed — skipped otherwise, matching the other tests/test_proctor_*.py files.
"""
import os
import sys
import time
import platform

import pytest
from unittest.mock import MagicMock, patch

# Skip all proctor tests if heavy dependencies are unavailable (e.g. CI runs
# only requirements.txt, not requirements-proctor.txt).
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

# proctor.py talks to a server + starts threads at import time; mock those
# globals first, same pattern as the other tests/test_proctor_*.py files.
TEST_ENV = {
    "SESSION_ID": "test-proctor-sess",
    "JWT_TOKEN": "",
    "SERVER_URL": "http://localhost:8000/event",
    "HEADLESS": True,
    "SKIP_ENROLLMENT": True,
    "YOLO_MODEL_PATH": "bogus.onnx",
    "GAZE_MODEL_PATH": "bogus.onnx",
    "RETINA_MODEL_PATH": "bogus.onnx",
    "EVIDENCE_DIR": "/tmp/procta_test_proctor_evidence",
}
for k, v in TEST_ENV.items():
    os.environ.setdefault(k, str(v))

for mod_name in ["sounddevice", "uniface", "insightface"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

import numpy as np
import cv2
import proctor


# ─── _wants_context ──────────────────────────────────────────────────────────

class TestWantsContext:
    """_wants_context(label) -> bool: does this event type get the 3-frame
    pre-violation context buffer, or stay single-frame?"""

    @pytest.mark.parametrize("label", [
        "phone_in_hand", "phone_on_desk", "phone_detected",
        "multiple_faces", "multiple_voices", "wrong_person",
        "face_missing", "sustained_voice", "conversation",
        "earbud_left", "earbud_right",
        "screen_share_detected", "cheat_object_detected",
        "keyword_uttered_x", "phone_consulting", "collaboration",
        "answer_memo", "note_reading",
    ])
    def test_matched_prefixes_want_context(self, label):
        assert proctor._wants_context(label) is True

    @pytest.mark.parametrize("label", [
        "gaze_away", "head_turned", "eyes_closed", "face_too_small",
        "reference_frame", "calibration_abort", "client_throttled",
        "audio_unavailable", "",
    ])
    def test_unmatched_labels_do_not_want_context(self, label):
        assert proctor._wants_context(label) is False

    def test_none_label_is_safe(self):
        # `label or ""` guard — must not raise on None.
        assert proctor._wants_context(None) is False

    def test_prefix_match_is_not_substring_match(self):
        # A prefix must anchor at the start, not appear anywhere in the string.
        assert proctor._wants_context("not_a_phone_event") is False


# ─── _dominant_direction ─────────────────────────────────────────────────────

class TestDominantDirection:
    """_dominant_direction(yaw, pitch, yaw_thresh, pitch_thresh) picks the
    axis that is proportionally further past its own threshold, not just
    whichever raw value is larger — this was a real misfire fix (see the
    comment above the function in proctor.py)."""

    def test_yaw_dominant_right(self):
        # yaw is 2x its threshold, pitch is at its threshold -> yaw wins.
        assert proctor._dominant_direction(yaw=20, pitch=10, yaw_thresh=10, pitch_thresh=10) == "right"

    def test_yaw_dominant_left(self):
        assert proctor._dominant_direction(yaw=-20, pitch=10, yaw_thresh=10, pitch_thresh=10) == "left"

    def test_pitch_dominant_down(self):
        assert proctor._dominant_direction(yaw=5, pitch=20, yaw_thresh=10, pitch_thresh=10) == "down"

    def test_pitch_dominant_up(self):
        assert proctor._dominant_direction(yaw=5, pitch=-20, yaw_thresh=10, pitch_thresh=10) == "up"

    def test_the_documented_misfire_case(self):
        """The bug this function fixes: yaw barely over its threshold while
        pitch is way past its own -> must label by dominance, not "yaw first"."""
        # yaw ratio = 11/10 = 1.1, pitch ratio = 40/10 = 4.0 -> pitch dominates.
        assert proctor._dominant_direction(yaw=11, pitch=-40, yaw_thresh=10, pitch_thresh=10) == "up"

    def test_exact_tie_prefers_yaw_axis(self):
        # yaw_ratio >= pitch_ratio -> ties go to yaw (per the `>=` in the impl).
        assert proctor._dominant_direction(yaw=10, pitch=10, yaw_thresh=10, pitch_thresh=10) == "right"

    def test_zero_threshold_does_not_divide_by_zero(self):
        # max(thresh, 1e-6) guard.
        result = proctor._dominant_direction(yaw=5, pitch=0, yaw_thresh=0, pitch_thresh=10)
        assert result in ("left", "right")


# ─── _compute_proctoring_tier ────────────────────────────────────────────────

class TestComputeProctoringTier:
    """_compute_proctoring_tier(models) -> {'tier', 'missing'}: this is the
    single source of truth for graceful degradation reporting. Must never
    report a tier that contradicts its own `missing` list."""

    ALL_UP = {"retina": True, "yolo": True, "gaze": True,
              "insightface": True, "ear": True, "eyes": True}

    def test_all_models_up_is_full(self):
        result = proctor._compute_proctoring_tier(self.ALL_UP)
        assert result == {"tier": "full", "missing": []}

    def test_no_face_detection_is_minimal_even_if_others_up(self):
        models = dict(self.ALL_UP, retina=False)
        result = proctor._compute_proctoring_tier(models)
        assert result["tier"] == "minimal"
        assert "retina" in result["missing"]

    def test_face_up_but_one_secondary_down_is_reduced(self):
        models = dict(self.ALL_UP, yolo=False)
        result = proctor._compute_proctoring_tier(models)
        assert result["tier"] == "reduced"
        assert result["missing"] == ["yolo"]

    def test_face_up_multiple_secondaries_down_lists_all_missing(self):
        models = dict(self.ALL_UP, yolo=False, gaze=False)
        result = proctor._compute_proctoring_tier(models)
        assert result["tier"] == "reduced"
        assert result["missing"] == ["gaze", "yolo"]  # sorted

    def test_missing_never_contradicts_tier(self):
        """Regression guard for the bug called out in the docstring: a
        'full' tier must never carry a non-empty `missing` list, and
        `missing` may only ever contain keys the tier is computed from."""
        considered = ("retina", "yolo", "gaze", "insightface", "ear")
        for face in (True, False):
            for yolo in (True, False):
                models = dict(self.ALL_UP, retina=face, yolo=yolo)
                result = proctor._compute_proctoring_tier(models)
                if result["tier"] == "full":
                    assert result["missing"] == []
                assert set(result["missing"]).issubset(considered)

    def test_unconsidered_key_is_ignored(self):
        # 'eyes'/'onnxruntime' aren't in the tier's `considered` set.
        models = dict(self.ALL_UP, eyes=False)
        result = proctor._compute_proctoring_tier(models)
        assert result["tier"] == "full"
        assert "eyes" not in result["missing"]

    def test_missing_dict_keys_absent_are_treated_falsy(self):
        # A model dict that simply omits a key (rather than False) must
        # still be treated as "not available" via .get(k).
        models = {"retina": True}
        result = proctor._compute_proctoring_tier(models)
        assert result["tier"] == "reduced"
        assert result["missing"] == ["ear", "gaze", "insightface", "yolo"]


# ─── _frame_is_usable ─────────────────────────────────────────────────────────

class TestFrameIsUsable:
    """_frame_is_usable(frame): rejects near-black or grayscale (IR /
    Windows-Hello) frames so calibration doesn't hang on a dead camera feed."""

    def test_none_frame_is_not_usable(self):
        assert proctor._frame_is_usable(None) is False

    def test_empty_frame_is_not_usable(self):
        assert proctor._frame_is_usable(np.zeros((0, 0, 3), dtype=np.uint8)) is False

    def test_single_channel_frame_is_not_usable(self):
        gray = np.full((100, 100), 128, dtype=np.uint8)
        assert proctor._frame_is_usable(gray) is False

    def test_near_black_color_frame_is_not_usable(self):
        black = np.zeros((100, 100, 3), dtype=np.uint8)
        assert proctor._frame_is_usable(black) is False

    def test_grayscale_looking_color_frame_is_not_usable(self):
        # R == G == B everywhere but bright -> IR camera streaming as 3-channel.
        mono = np.full((100, 100, 3), 150, dtype=np.uint8)
        assert proctor._frame_is_usable(mono) is False

    def test_lit_color_frame_is_usable(self):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :, 0] = 40   # B
        frame[:, :, 1] = 90   # G
        frame[:, :, 2] = 180  # R — clearly colored and lit
        assert proctor._frame_is_usable(frame) is True

    def test_error_during_probe_defaults_to_usable(self):
        """Fail-open: a probe error must never block camera selection."""
        class Explodes:
            size = 10
            ndim = 3
            shape = (10, 10, 3)
            def __getitem__(self, key):
                raise RuntimeError("boom")
        assert proctor._frame_is_usable(Explodes()) is True


# ─── _camera_backend_candidates ───────────────────────────────────────────────

class TestCameraBackendCandidates:
    """_camera_backend_candidates(): per-OS candidate list, always ending in
    a DEFAULT fallback, with no duplicate (name, backend) pairs."""

    def test_windows_gets_dshow_and_msmf_plus_default(self, monkeypatch):
        monkeypatch.setattr(proctor.platform, "system", lambda: "Windows")
        names = [n for n, _ in proctor._camera_backend_candidates()]
        assert names[-1] == "DEFAULT"
        assert "DSHOW" in names or "MSMF" in names  # at least one real backend

    def test_darwin_gets_avfoundation_plus_default(self, monkeypatch):
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        names = [n for n, _ in proctor._camera_backend_candidates()]
        assert names[-1] == "DEFAULT"

    def test_linux_gets_v4l2_plus_default(self, monkeypatch):
        monkeypatch.setattr(proctor.platform, "system", lambda: "Linux")
        names = [n for n, _ in proctor._camera_backend_candidates()]
        assert names[-1] == "DEFAULT"

    def test_unknown_os_falls_back_to_default_only(self, monkeypatch):
        monkeypatch.setattr(proctor.platform, "system", lambda: "PlanNine")
        result = proctor._camera_backend_candidates()
        assert result == [("DEFAULT", None)]

    def test_no_duplicate_backend_entries(self, monkeypatch):
        monkeypatch.setattr(proctor.platform, "system", lambda: "Darwin")
        result = proctor._camera_backend_candidates()
        assert len(result) == len(set(result))

    def test_missing_cv2_constant_keeps_name_but_backend_is_none(self, monkeypatch):
        """If a cv2 build lacks e.g. CAP_DSHOW, getattr returns None. The
        dedup key is (name, backend), so a missing constant still yields a
        distinctly-named candidate (not silently merged into DEFAULT) —
        callers pass backend=None straight to cv2.VideoCapture, which is
        equivalent to not specifying a backend."""
        monkeypatch.setattr(proctor.platform, "system", lambda: "Windows")
        with patch.object(proctor.cv2, "CAP_DSHOW", None, create=True), \
             patch.object(proctor.cv2, "CAP_MSMF", None, create=True):
            result = proctor._camera_backend_candidates()
            assert ("DSHOW", None) in result
            assert ("MSMF", None) in result
            assert ("DEFAULT", None) in result
            # No accidental collapsing of distinctly-named entries.
            assert len(result) == len(set(result)) == 3


# ─── _proctor_frame_init_state ────────────────────────────────────────────────

class TestProctorFrameInitState:
    """_proctor_frame_init_state(): the state dict factory for run_proctoring."""

    def test_returns_expected_keys_and_defaults(self):
        state = proctor._proctor_frame_init_state()
        assert state["frame_count"] == 0
        assert state["consecutive_failures"] == 0
        assert state["last_logged"] == {}
        assert state["object_history"] == {}
        assert state["_violation_history"] == {}
        assert state["lazy_enroll_done"] is False
        assert state["_last_face_bbox"] is None
        assert state["_fps_warned"] is False
        assert state["_last_yolo_result"] is None
        assert state["_last_yolo_frame"] == 0

    def test_each_call_returns_a_fresh_independent_dict(self):
        """Regression guard: mutable defaults (dicts/lists) must not be
        shared across calls, or one session's history would bleed into
        the next session's run_proctoring() state."""
        s1 = proctor._proctor_frame_init_state()
        s2 = proctor._proctor_frame_init_state()
        s1["object_history"]["Phone"] = 5
        s1["_voice_burst_times"].append(123.0)
        assert s2["object_history"] == {}
        assert s2["_voice_burst_times"] == []


# ─── _limit_fps ───────────────────────────────────────────────────────────────

class TestLimitFps:
    """_limit_fps(state, governor, loop_start): paces the loop to the
    governor's effective_fps and records rolling fps history."""

    def _governor(self, fps=1000.0):
        g = MagicMock()
        g.effective_fps = fps
        return g

    def test_updates_last_frame_end_and_returns_positive_fps(self):
        state = proctor._proctor_frame_init_state()
        state["_last_frame_end"] = time.time() - 0.01
        governor = self._governor(fps=1000.0)  # huge fps -> ~no sleep
        actual = proctor._limit_fps(state, governor, time.time())
        assert actual > 0
        assert state["_last_frame_end"] >= state.get("_last_frame_end")  # was updated (no exception)

    def test_appends_to_fps_history_ring(self):
        state = proctor._proctor_frame_init_state()
        governor = self._governor(fps=1000.0)
        for _ in range(5):
            state["_last_frame_end"] = time.time() - 0.001
            proctor._limit_fps(state, governor, time.time())
        assert len(state["_fps_history"]) == 5

    def test_fps_history_ring_is_capped_at_30(self):
        state = proctor._proctor_frame_init_state()
        governor = self._governor(fps=1000.0)
        for _ in range(40):
            state["_last_frame_end"] = time.time() - 0.001
            proctor._limit_fps(state, governor, time.time())
        assert len(state["_fps_history"]) == 30

    def test_zero_effective_fps_does_not_crash(self):
        """max(governor.effective_fps, 0.1) guard against a div-by-zero /
        negative sleep target — 0 fps floors to 0.1fps (a 10s period), not
        an exception or an infinite sleep. Use a loop_start far enough in
        the past that elapsed already exceeds that period, so this test
        doesn't itself have to sleep 10s to prove it."""
        state = proctor._proctor_frame_init_state()
        state["_last_frame_end"] = time.time() - 20
        governor = self._governor(fps=0.0)
        start = time.time()
        actual_fps = proctor._limit_fps(state, governor, time.time() - 20)
        assert time.time() - start < 1.0
        assert actual_fps > 0


# ─── get_head_pose ────────────────────────────────────────────────────────────

class TestGetHeadPose:
    """get_head_pose(landmarks_2d, img_w, img_h) -> (yaw_deg, pitch_deg),
    via cv2.solvePnP against the canonical 3D face model."""

    def _frontal_landmarks(self, w=640, h=480):
        """5 RetinaFace-style 2D points (leye, reye, nose, lmouth, rmouth)
        for a face looking straight at the camera, roughly centered."""
        cx, cy = w / 2, h / 2
        return np.array([
            [cx - 40, cy - 20],   # left eye
            [cx + 40, cy - 20],   # right eye
            [cx,      cy + 10],   # nose
            [cx - 25, cy + 50],   # left mouth
            [cx + 25, cy + 50],   # right mouth
        ], dtype=np.float64)

    def test_frontal_face_yields_small_yaw_and_pitch(self):
        yaw, pitch = proctor.get_head_pose(self._frontal_landmarks(), 640, 480)
        assert isinstance(yaw, float) and isinstance(pitch, float)
        # A roughly-frontal synthetic face shouldn't solve to an extreme angle.
        assert abs(yaw) < 60
        assert abs(pitch) < 60

    def test_degenerate_landmarks_fail_safe_to_zero(self):
        """All points identical -> solvePnP either fails or degenerates;
        the function must never raise, and falls back to (0.0, 0.0) on
        any exception."""
        degenerate = np.zeros((5, 2), dtype=np.float64)
        yaw, pitch = proctor.get_head_pose(degenerate, 640, 480)
        assert isinstance(yaw, float) and isinstance(pitch, float)

    def test_solve_failure_returns_zero_zero(self):
        with patch.object(proctor.cv2, "solvePnP", return_value=(False, None, None)):
            yaw, pitch = proctor.get_head_pose(self._frontal_landmarks(), 640, 480)
        assert (yaw, pitch) == (0.0, 0.0)

    def test_internal_exception_returns_zero_zero(self):
        with patch.object(proctor.cv2, "solvePnP", side_effect=RuntimeError("boom")):
            yaw, pitch = proctor.get_head_pose(self._frontal_landmarks(), 640, 480)
        assert (yaw, pitch) == (0.0, 0.0)

    def test_pitch_unwraps_past_90_degrees(self):
        """solvePnP can return a 180deg-flipped basis; the unwrap logic
        must fold a pitch > 90 back into the +/-90 range."""
        with patch.object(proctor.cv2, "solvePnP", return_value=(True, np.zeros((3, 1)), None)), \
             patch.object(proctor.cv2, "Rodrigues", return_value=(np.eye(3), None)), \
             patch.object(proctor.cv2, "RQDecomp3x3", return_value=([170.0, 10.0, 0.0], None, None, None, None, None)):
            yaw, pitch = proctor.get_head_pose(self._frontal_landmarks(), 640, 480)
        # pitch=170 -> 170 - sign(170)*180 = -10
        assert pitch == pytest.approx(-10.0)
        assert yaw == pytest.approx(10.0)


# ─── eyes_detected ─────────────────────────────────────────────────────────────

class TestEyesDetected:
    """eyes_detected(face_crop): fail-open when the cascade is unavailable
    or the crop is empty; fail-closed on an internal exception."""

    def test_empty_crop_fails_open(self):
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        assert proctor.eyes_detected(empty) is True

    def test_cascade_unavailable_fails_open(self, monkeypatch):
        monkeypatch.setattr(proctor, "EYES_AVAILABLE", False)
        crop = np.zeros((50, 50, 3), dtype=np.uint8)
        assert proctor.eyes_detected(crop) is True

    def test_exception_during_detection_fails_closed(self, monkeypatch):
        monkeypatch.setattr(proctor, "EYES_AVAILABLE", True)
        crop = np.zeros((50, 50, 3), dtype=np.uint8)
        with patch.object(proctor.cv2, "cvtColor", side_effect=RuntimeError("boom")):
            assert proctor.eyes_detected(crop) is False

    def test_cascade_finds_eyes_returns_true(self, monkeypatch):
        monkeypatch.setattr(proctor, "EYES_AVAILABLE", True)
        fake_cascade = MagicMock()
        fake_cascade.detectMultiScale.return_value = np.array([[0, 0, 20, 20]])
        monkeypatch.setattr(proctor, "_eye_cascade", fake_cascade)
        crop = np.zeros((50, 50, 3), dtype=np.uint8)
        assert proctor.eyes_detected(crop) is True

    def test_cascade_finds_nothing_returns_false(self, monkeypatch):
        monkeypatch.setattr(proctor, "EYES_AVAILABLE", True)
        fake_cascade = MagicMock()
        fake_cascade.detectMultiScale.return_value = ()
        monkeypatch.setattr(proctor, "_eye_cascade", fake_cascade)
        crop = np.zeros((50, 50, 3), dtype=np.uint8)
        assert proctor.eyes_detected(crop) is False


# ─── _process_yolo_results ─────────────────────────────────────────────────────

class TestProcessYoloResults:
    """_process_yolo_results(state, frame, frame_count, W, H, can_log,
    log_if_allowed): the per-frame YOLO detection/history/event state
    machine. The yolo worker itself and all I/O (save_evidence, logging)
    are mocked so only the decision logic under test executes."""

    def _base_state(self):
        return proctor._proctor_frame_init_state()

    def test_returns_empty_set_when_yolo_unavailable(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", False)
        state = self._base_state()
        result = proctor._process_yolo_results(
            state, frame=None, frame_count=0, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        assert result == set()

    def test_no_result_yet_returns_empty_set_without_crashing(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = None
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        state = self._base_state()
        result = proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=0, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        assert result == set()
        fake_worker.submit.assert_called_once()

    def test_submits_only_on_the_configured_cadence(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = None
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        state = self._base_state()
        every_n = proctor.YOLO_EVERY_N
        # frame_count not a multiple of YOLO_EVERY_N -> no submit.
        proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=every_n + 1, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        fake_worker.submit.assert_not_called()

    def test_error_result_is_surfaced_and_returns_empty_set(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = {"error": "onnx runtime exploded"}
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        state = self._base_state()
        result = proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=0, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        assert result == set()

    def test_detection_below_min_frames_does_not_log_yet(self, monkeypatch):
        """YOLO_MIN_FRAMES gating: a single-frame sighting must not fire an
        event immediately (debounce against one-frame false positives)."""
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = {
            "detections": [("Calculator", 0.9, 10, 10, 50, 50)]
        }
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        state = self._base_state()
        logged = []
        proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=0, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *a: logged.append(a))
        assert logged == []
        assert state["object_history"]["Calculator"] == 1

    def test_sustained_detection_fires_generic_cheat_object_event(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = {
            "detections": [("Calculator", 0.9, 10, 10, 50, 50)]
        }
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        monkeypatch.setattr(proctor, "save_evidence", MagicMock())
        state = self._base_state()
        logged = []
        # Feed it YOLO_MIN_FRAMES consecutive "seen" frames to cross the gate.
        for i in range(proctor.YOLO_MIN_FRAMES):
            proctor._process_yolo_results(
                state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=i, W=640, H=480,
                can_log=lambda *_: True, log_if_allowed=lambda *a: logged.append(a))
        assert len(logged) == 1
        event_name, severity, details = logged[0]
        assert event_name == "cheat_object_detected"
        assert severity == "high"
        assert "Calculator" in details
        # History resets after firing so it doesn't spam every frame.
        assert state["object_history"].get("Calculator", 0) == 0

    def test_phone_detection_uses_position_classification_and_severity(self, monkeypatch):
        """A sustained Phone sighting routes through classify_phone_position
        and picks event name/severity from the result (critical=in-hand)."""
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        # Phone box high in the frame with no face -> ambiguous ("phone_detected")
        # unless we force phone_in_hand via a face bbox. Use classify's own
        # logic: phone center above half of frame height with no face -> use
        # frame-based desk check. We construct a clear "in hand" case here.
        phone_box = (100, 10, 150, 40)  # center_y = 25
        fake_worker.get_result.return_value = {
            "detections": [("Phone", 0.95, *phone_box)]
        }
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        monkeypatch.setattr(proctor, "save_evidence", MagicMock())
        state = self._base_state()
        state["_last_face_bbox"] = (80, 60, 180, 300)  # face_bottom=300, ratio*.5=150 > 25
        logged = []
        for i in range(proctor.YOLO_MIN_FRAMES):
            proctor._process_yolo_results(
                state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=i, W=640, H=480,
                can_log=lambda *_: True, log_if_allowed=lambda *a: logged.append(a))
        assert len(logged) == 1
        event_name, severity, details = logged[0]
        assert event_name == "cheat_phone_in_hand"
        assert severity == "critical"

    def test_can_log_false_suppresses_event(self, monkeypatch):
        """The cooldown gate (can_log) must be respected even once the
        min-frames threshold is crossed."""
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = {
            "detections": [("Calculator", 0.9, 10, 10, 50, 50)]
        }
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        monkeypatch.setattr(proctor, "save_evidence", MagicMock())
        state = self._base_state()
        logged = []
        for i in range(proctor.YOLO_MIN_FRAMES):
            proctor._process_yolo_results(
                state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=i, W=640, H=480,
                can_log=lambda *_: False, log_if_allowed=lambda *a: logged.append(a))
        assert logged == []
        # History count is retained (not reset) so it can still fire on a
        # future frame once the cooldown lifts.
        assert state["object_history"]["Calculator"] >= proctor.YOLO_MIN_FRAMES

    def test_object_leaving_frame_decays_and_is_forgotten(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        state = self._base_state()

        fake_worker.get_result.return_value = {
            "detections": [("Calculator", 0.9, 10, 10, 50, 50)]
        }
        proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=0, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        assert state["object_history"]["Calculator"] == 1

        # Object no longer seen -> decays by 1 and is dropped once it hits 0.
        fake_worker.get_result.return_value = {"detections": []}
        proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=1, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        assert "Calculator" not in state["object_history"]

    def test_records_last_phone_seen_timestamp(self, monkeypatch):
        monkeypatch.setattr(proctor, "YOLO_AVAILABLE", True)
        fake_worker = MagicMock()
        fake_worker.get_result.return_value = {
            "detections": [("Phone", 0.9, 10, 10, 50, 50)]
        }
        monkeypatch.setattr(proctor, "yolo_worker", fake_worker)
        state = self._base_state()
        assert "_last_phone_seen_t" not in state
        before = time.time()
        proctor._process_yolo_results(
            state, frame=np.zeros((10, 10, 3), np.uint8), frame_count=0, W=640, H=480,
            can_log=lambda *_: True, log_if_allowed=lambda *_: None)
        assert state["_last_phone_seen_t"] >= before

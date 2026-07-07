"""
Unit tests for the ONNX object-detection path in proctor.py (the migration
off ultralytics/torch onto a bundled weights/yolov8n.onnx run via
onnxruntime).

These exercise the highest-risk migrated code — the custom letterbox +
[1,84,8400] decode + per-class NMS + un-letterbox coordinate transform in
``_yolo_infer`` — by feeding a SYNTHETIC ONNX output through a fake session,
so no real model or camera is needed. Also guards that the YoloWorker does
not distort aspect ratio before inference.

Requires proctor.py deps (cv2, numpy, onnxruntime).
"""
import os
import sys
import pytest
from unittest.mock import MagicMock

_deps = ["cv2", "numpy", "onnxruntime"]
_missing = []
for _d in _deps:
    try:
        __import__(_d)
    except ImportError:
        _missing.append(_d)

pytestmark = pytest.mark.skipif(
    bool(_missing), reason=f"proctor deps missing: {', '.join(_missing)}"
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Keep proctor's import light + offline: mock the heavy face/audio libs and
# point the daemon threads at an unreachable server so they fail fast.
for _m in ["sounddevice", "uniface", "insightface"]:
    sys.modules.setdefault(_m, MagicMock())
os.environ.setdefault("PROCTOR_SERVER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PROCTOR_SESSION_ID", "unit-test")
os.environ.setdefault("PROCTOR_JWT_TOKEN", "")

import numpy as np  # noqa: E402


class _FakeYoloSession:
    """Minimal stand-in for an onnxruntime InferenceSession: returns a
    pre-baked [1, 84, 8400] output regardless of the feed."""
    def __init__(self, output):
        self._output = output

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def run(self, output_names, feed):
        return [self._output]


def _blank_output(num_anchors=8400, num_classes=80):
    # YOLOv8 ONNX output is [1, 4+nc, anchors]; 4 = cx,cy,w,h (letterboxed
    # 640 px space), then per-class scores (no separate objectness).
    return np.zeros((1, 4 + num_classes, num_anchors), dtype=np.float32)


def _put_box(out, anchor, cls_id, score, cx, cy, w, h):
    out[0, 0, anchor] = cx
    out[0, 1, anchor] = cy
    out[0, 2, anchor] = w
    out[0, 3, anchor] = h
    out[0, 4 + cls_id, anchor] = score


class TestYoloInferDecode:
    """The migrated _yolo_infer decode/letterbox/NMS, fed synthetic output."""

    def test_decodes_class_conf_and_unletterboxed_coords(self):
        from proctor import _yolo_infer

        # 640x480 input. Letterbox: scale=min(640/640,640/480)=1.0,
        # pad_x=0, pad_y=(640-480)/2=80 -> top=80, left=0.
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        out = _blank_output()
        # Phone (COCO 67) centered at letterboxed (320,240), 80x100 box.
        _put_box(out, 0, 67, 0.90, 320.0, 240.0, 80.0, 100.0)

        dets = _yolo_infer(_FakeYoloSession(out), img)
        assert len(dets) == 1, dets
        cls_id, conf, x1, y1, x2, y2 = dets[0]
        assert cls_id == 67
        assert conf == pytest.approx(0.90, abs=1e-5)
        # Un-letterbox (subtract pad, divide by scale=1.0):
        #   x1=(320-40-0)=280  x2=(320+40-0)=360
        #   y1=(240-50-80)=110 y2=(240+50-80)=210
        assert (x1, y1, x2, y2) == (280, 110, 360, 210)

    def test_below_decode_floor_is_filtered(self):
        # _yolo_infer now decodes down to YOLO_DECODE_FLOOR (the lower of the
        # standard and phone thresholds); the final per-class call is made by
        # _cheat_detection_kept. Anything below the floor is still dropped.
        from proctor import _yolo_infer, YOLO_DECODE_FLOOR

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        out = _blank_output()
        _put_box(out, 0, 67, YOLO_DECODE_FLOOR - 0.05, 320, 240, 80, 100)
        assert _yolo_infer(_FakeYoloSession(out), img) == []

    def test_low_conf_phone_survives_decode_for_per_class_gate(self):
        # A dark/close phone scoring between the phone threshold and the
        # standard threshold must survive decode so _cheat_detection_kept can
        # keep it — this is the fix for phones that previously fell through.
        from proctor import _yolo_infer, YOLO_PHONE_CONFIDENCE, YOLO_CONFIDENCE

        conf = (YOLO_PHONE_CONFIDENCE + YOLO_CONFIDENCE) / 2.0
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        out = _blank_output()
        _put_box(out, 0, 67, conf, 320, 240, 80, 100)
        dets = _yolo_infer(_FakeYoloSession(out), img)
        assert any(d[0] == 67 for d in dets), "low-conf phone must survive decode"

    def test_cheat_detection_kept_per_class_thresholds(self):
        from proctor import (_cheat_detection_kept, YOLO_PHONE_CONFIDENCE,
                             YOLO_CONFIDENCE)

        # Custom 7-class model: Phone (2) is gated at YOLO_PHONE_CONFIDENCE; the
        # others at YOLO_CONFIDENCE. Note the phone bar is now the STRICTER of the
        # two (0.40 vs 0.35) — the trained phone class reads cleanly, so the old
        # low COCO floor would false-positive. Assert against each class's own
        # threshold so the test holds regardless of which bar is higher.
        assert _cheat_detection_kept(2, YOLO_PHONE_CONFIDENCE)
        assert _cheat_detection_kept(2, YOLO_PHONE_CONFIDENCE + 0.01)
        assert not _cheat_detection_kept(2, YOLO_PHONE_CONFIDENCE - 0.01)
        # Non-handheld cheat objects (Earphone 0, Headphone 1, Calculator 3,
        # Laptop 4, Monitor 5, Tablet 6).
        for cid in (0, 1, 3, 4, 5, 6):
            assert _cheat_detection_kept(cid, YOLO_CONFIDENCE)
            assert _cheat_detection_kept(cid, YOLO_CONFIDENCE + 0.01)
            assert not _cheat_detection_kept(cid, YOLO_CONFIDENCE - 0.01)
        # Classes outside CHEAT_IDS (any stray id) are never kept, even at max
        # confidence.
        assert not _cheat_detection_kept(7, 0.99)
        assert not _cheat_detection_kept(9, 0.99)

    def test_per_class_nms_dedups_overlapping_same_class(self):
        from proctor import _yolo_infer

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        out = _blank_output()
        # Two heavily-overlapping phone boxes (IoU > 0.7) → NMS keeps one.
        _put_box(out, 0, 67, 0.90, 320, 240, 80, 100)
        _put_box(out, 1, 67, 0.80, 322, 242, 80, 100)
        dets = _yolo_infer(_FakeYoloSession(out), img)
        phones = [d for d in dets if d[0] == 67]
        assert len(phones) == 1, f"NMS should dedup overlaps, got {phones}"
        assert phones[0][1] == pytest.approx(0.90, abs=1e-5)  # higher score kept

    def test_distinct_classes_both_kept(self):
        from proctor import _yolo_infer

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        out = _blank_output()
        _put_box(out, 0, 67, 0.90, 200, 200, 60, 80)   # phone
        _put_box(out, 1, 73, 0.85, 450, 300, 90, 70)   # book
        classes = sorted(d[0] for d in _yolo_infer(_FakeYoloSession(out), img))
        assert classes == [67, 73]


class TestYoloWorkerInputIsUndistorted:
    """The frame fed to inference must keep its aspect ratio — a square
    stretch (the old cv2.resize(frame,(416,416))) distorts webcam frames
    (4:3 / 16:9) before YOLO sees them and degrades object detection."""

    def test_submit_preserves_aspect_ratio(self):
        from proctor import YoloWorker

        w = YoloWorker()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)  # 4:3
        w.submit(frame, 1, 640, 480)
        small, _fc, _W, _H = w.frame_q.get_nowait()
        sh, sw = small.shape[:2]
        assert abs((sw / sh) - (640 / 480)) < 0.05, (
            f"submit distorted aspect ratio: {sw}x{sh} (was the frame "
            f"square-stretched before inference?)"
        )


# ── Calibration-derived thresholds ────────────────────────────────────────
class TestCalibrationThresholds:
    """_tune_threshold turns the edge-dot calibration range (PROCTOR_*_RANGE)
    into per-student gaze/head thresholds. This is the calibration path that
    sets GAZE_YAW_RAD / HEAD_YAW_THRESHOLD etc. at import."""

    KEY = "TEST_CAL_RANGE"

    def _call(self, raw):
        from proctor import _tune_threshold
        if raw is None:
            os.environ.pop(self.KEY, None)
        else:
            os.environ[self.KEY] = raw
        try:
            return _tune_threshold(self.KEY, 0.30, 0.55, 0.15, 0.90)
        finally:
            os.environ.pop(self.KEY, None)

    def test_unset_returns_defaults(self):
        assert self._call(None) == (0.30, 0.55)

    def test_valid_range_scales_and_clamps(self):
        med, ext = self._call("0.28")
        assert med == pytest.approx(0.28 * 1.30, abs=1e-6)               # 30% past edge
        assert ext == pytest.approx(max(med * 1.40, min(0.90 * 1.55, 0.28 * 1.90)), abs=1e-6)

    def test_tiny_range_clamps_to_floor(self):
        med, _ext = self._call("0.01")
        assert med == pytest.approx(0.15)  # floor

    def test_invalid_or_nonpositive_falls_back_to_defaults(self):
        for bad in ("not-a-number", "0", ""):
            assert self._call(bad) == (0.30, 0.55), bad


# ── Gaze ONNX decode (resnet18_gaze.onnx → yaw/pitch) ──────────────────────
class _FakeGazeSession:
    def __init__(self, outputs):  # [yaw_logits(1,90), pitch_logits(1,90)]
        self._outputs = outputs

    def run(self, output_names, feed):
        return self._outputs


class TestGazeOnnxDecode:
    def test_softmax_normalizes_and_orders(self):
        from proctor import GazeEstimator
        p = GazeEstimator._softmax(np.array([[1.0, 2.0, 5.0]], dtype=np.float32))
        assert p.sum() == pytest.approx(1.0, abs=1e-6)
        assert p[0, 2] > p[0, 1] > p[0, 0]

    def _make(self, yaw_logits, pitch_logits):
        from collections import deque
        from proctor import GazeEstimator
        g = GazeEstimator.__new__(GazeEstimator)
        g._bins = 90
        g._binwidth = 4
        g._angle_offset = 180
        g.idx_tensor = np.arange(90, dtype=np.float32)
        g.input_name = "input"
        g.input_size = (224, 224)
        g.output_names = ["yaw", "pitch"]
        g.yaw_buf = deque(maxlen=1)
        g.pitch_buf = deque(maxlen=1)
        g.session = _FakeGazeSession([yaw_logits, pitch_logits])
        return g

    def _peak(self, bin_idx):
        v = np.full((1, 90), -10.0, dtype=np.float32)
        v[0, bin_idx] = 10.0
        return v

    def test_centered_gaze_decodes_near_zero(self):
        # bin 45 → 45*4 - 180 = 0°
        g = self._make(self._peak(45), self._peak(45))
        yaw, pitch = g.estimate(np.zeros((50, 50, 3), dtype=np.uint8))
        assert abs(yaw) < 0.02
        assert abs(pitch) < 0.02

    def test_offset_bin_decodes_to_expected_angle(self):
        import math
        # yaw peaked at bin 50 → 50*4 - 180 = 20°
        g = self._make(self._peak(50), self._peak(45))
        yaw, pitch = g.estimate(np.zeros((50, 50, 3), dtype=np.uint8))
        assert yaw == pytest.approx(math.radians(20), abs=0.03)
        assert abs(pitch) < 0.05

    def test_empty_crop_returns_zero(self):
        g = self._make(self._peak(45), self._peak(45))
        assert g.estimate(np.zeros((0, 0, 3), dtype=np.uint8)) == (0.0, 0.0)


# ── Earbud ONNX (earbud_classifier.onnx) + ear-bbox geometry ───────────────
class _FakeEarSession:
    def __init__(self, out):  # (1, num_classes)
        self._out = out

    def run(self, names, feed):
        return [self._out]


def _five_landmarks():
    # left_eye, right_eye, nose, left_mouth, right_mouth (eye_dist = 80)
    return np.array([[200, 200], [280, 200], [240, 250], [210, 300], [270, 300]],
                    dtype=np.float32)


class TestEarbudOnnx:
    def _EC(self):
        import proctor
        inst = getattr(proctor, "_ear_classifier", None)
        if inst is None:
            pytest.skip("EarClassifier unavailable (onnxruntime missing)")
        return type(inst)

    def test_left_ear_bbox_is_left_of_left_eye(self):
        EC = self._EC()
        lm = _five_landmarks()
        bbox = EC._estimate_ear_bbox(lm, 640, 480, "left")
        assert bbox is not None
        x1, _y1, x2, _y2 = bbox
        assert (x1 + x2) / 2 < lm[0][0]  # ear region centred left of the left eye

    def test_right_ear_bbox_is_right_of_right_eye(self):
        EC = self._EC()
        lm = _five_landmarks()
        x1, _y1, x2, _y2 = EC._estimate_ear_bbox(lm, 640, 480, "right")
        assert (x1 + x2) / 2 > lm[1][0]  # centred right of the right eye

    def test_tiny_face_returns_no_bbox(self):
        EC = self._EC()
        lm = np.array([[100, 100], [105, 100], [102, 103], [100, 106], [105, 106]],
                      dtype=np.float32)  # eye_dist = 5 → box < 20px
        assert EC._estimate_ear_bbox(lm, 640, 480, "left") is None

    def test_classify_extracts_ear_class_probability(self):
        EC = self._EC()
        ec = EC.__new__(EC)
        ec.session = _FakeEarSession(np.array([[0.3, 0.7]], dtype=np.float32))  # [no-ear, ear]
        ec.model_size = (64, 64)
        ec.input_name = "input"
        ec.output_name = "output"
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        left_conf, right_conf = ec.classify(frame, _five_landmarks(), 640, 480)
        assert left_conf == pytest.approx(0.7, abs=1e-5)
        assert right_conf == pytest.approx(0.7, abs=1e-5)


def test_detect_faces_uses_scrfd_not_uniface():
    """detect_faces() must not be backed by a live uniface RetinaFace
    instance any more — it should be backed by insightface.model_zoo's
    SCRFD-weighted detector."""
    import proctor
    assert 'uniface' not in sys.modules or not hasattr(proctor, '_retina') or proctor._retina is None, (
        "proctor._retina should no longer be a live uniface RetinaFace instance")
    assert hasattr(proctor, '_scrfd_detector'), "expected a _scrfd_detector module attribute"

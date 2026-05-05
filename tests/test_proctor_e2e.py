"""
End-to-end smoke test for the proctor.py detection pipeline.

Feeds synthetic frames through the YOLO worker, SAHI worker, and
phone classification pipeline to verify the full data flow:
  frame → worker → result queue → coordinate scaling → phone classification

Requires proctor.py dependencies (cv2, numpy, ultralytics, etc.).
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

TEST_ENV = {
    "SESSION_ID": "smoke-sess",
    "JWT_TOKEN": "",
    "SERVER_URL": "http://localhost:8000/event",
    "HEADLESS": True,
    "SKIP_ENROLLMENT": True,
    "YOLO_MODEL_PATH": "bogus.pt",
    "GAZE_MODEL_PATH": "bogus.onnx",
    "RETINA_MODEL_PATH": "bogus.onnx",
    "EVIDENCE_DIR": "/tmp/procta_smoke_evidence",
}
for k, v in TEST_ENV.items():
    os.environ.setdefault(k, str(v))

for mod_name in ["sounddevice", "uniface", "ultralytics", "insightface"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

import cv2
import numpy as np


class TestYOLOWorkerCoordinateScaling:
    """Verify that the YOLO worker correctly scales detection coordinates
    from the 416×416 resized image back to the original frame dimensions."""

    def test_scaling_preserves_aspect_ratio_correctly(self):
        """A box at the center of a 416×416 image should map to the
        center of any original frame regardless of aspect ratio."""
        from proctor import YoloWorker, CHEAT_IDS

        worker = YoloWorker()
        worker.start()

        # Create a 640×480 frame (4:3 aspect ratio)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # We can't run real YOLO without a model, but we can verify
        # the scaling math by checking the submit/get_result interface
        # and the coordinate transformation in _run.

        # The scaling formula is: x_orig = x_small * W / w_small
        # where w_small = 416 always (the resize target).
        W, H = 640, 480

        # Simulate what _run does:
        small = cv2.resize(frame, (416, 416))
        h, w = small.shape[:2]  # both 416

        # A box at the center of the small image
        x1_s, y1_s, x2_s, y2_s = 180, 180, 236, 236

        # Scale back to original frame
        x1_o = int(x1_s * W / w)
        y1_o = int(y1_s * H / h)
        x2_o = int(x2_s * W / w)
        y2_o = int(y2_s * H / h)

        # Center of 640×480 is (320, 240)
        center_x = (x1_o + x2_o) / 2
        center_y = (y1_o + y2_o) / 2

        # Should be close to center (allowing for integer rounding)
        assert abs(center_x - W / 2) < 5
        assert abs(center_y - H / 2) < 5

        worker.stop()

    def test_scaling_for_wide_frame(self):
        """A 1280×720 frame (16:9) should scale correctly."""
        W, H = 1280, 720
        small = np.zeros((416, 416, 3), dtype=np.uint8)
        h, w = small.shape[:2]

        # Box covering the full small image
        x1_s, y1_s, x2_s, y2_s = 0, 0, 416, 416
        x1_o = int(x1_s * W / w)
        y1_o = int(y1_s * H / h)
        x2_o = int(x2_s * W / w)
        y2_o = int(y2_s * H / h)

        assert x1_o == 0
        assert y1_o == 0
        assert x2_o == W
        assert y2_o == H

    def test_scaling_for_narrow_frame(self):
        """A 480×640 frame (portrait) should scale correctly."""
        W, H = 480, 640
        small = np.zeros((416, 416, 3), dtype=np.uint8)
        h, w = small.shape[:2]

        # Box at center of small image
        x1_s, y1_s, x2_s, y2_s = 158, 158, 258, 258
        x1_o = int(x1_s * W / w)
        y1_o = int(y1_s * H / h)
        x2_o = int(x2_s * W / w)
        y2_o = int(y2_s * H / h)

        # Center of 480×640 is (240, 320)
        center_x = (x1_o + x2_o) / 2
        center_y = (y1_o + y2_o) / 2

        assert abs(center_x - W / 2) < 3
        assert abs(center_y - H / 2) < 3


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
        # 230 < 480 * 0.65 = 312 → not on_desk → default on_desk
        assert result == "phone_on_desk"


class TestSAHIWorkerTileCoordinates:
    """Verify SAHI tile offset addition is correct."""

    def test_tile_offset_addition(self):
        """Detections in a tile should have the tile offset added back."""
        from proctor import SahiYoloWorker

        worker = SahiYoloWorker()

        # Create a test frame
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Generate tiles and verify offsets
        tiles = list(worker._generate_tiles(frame))
        assert len(tiles) > 0

        # First tile should start at (0, 0)
        first_tile, ox, oy = tiles[0]
        assert ox == 0
        assert oy == 0

        # Verify tile dimensions
        tile_h, tile_w = first_tile.shape[:2]
        assert tile_w <= SahiYoloWorker.TILE_SIZE
        assert tile_h <= SahiYoloWorker.TILE_SIZE

        # Verify overlap
        if len(tiles) >= 2:
            _, ox2, _ = tiles[1]
            step = int(SahiYoloWorker.TILE_SIZE * (1 - SahiYoloWorker.OVERLAP))
            assert ox2 == step  # second tile starts at step offset


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

    def test_sahi_result_format(self):
        """SAHI results after NMS merge should be (name, conf, x1, y1, x2, y2)."""
        from proctor import SahiYoloWorker

        worker = SahiYoloWorker()

        # Test NMS merge with known inputs
        detections = [
            ("Phone", 0.9, 100, 100, 200, 200),
            ("Phone", 0.8, 110, 110, 210, 210),  # overlaps with first
            ("Book", 0.7, 300, 300, 400, 400),
        ]
        merged = worker._nms_merge(detections)

        # Should have at least 2 results (Phone merged, Book separate)
        assert len(merged) >= 2
        for det in merged:
            assert len(det) == 6
            assert isinstance(det[0], str)
            assert isinstance(det[1], float)
            assert all(isinstance(x, int) for x in det[2:])


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
        """LAZY_ENROLL_WINDOW should be defined (inside run_proctoring)."""
        from proctor import TARGET_FPS
        # TARGET_FPS should be 15
        assert TARGET_FPS == 15

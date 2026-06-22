"""Unit tests for proctor.py calibration-baseline plausibility (v2.3.50 misfire fix).

The Windows run froze a head baseline of +53° and a gaze baseline of +0.51rad as
"centre", so every forward glance read as EXTREME off-screen. These helpers decide
when a calibration baseline is trustworthy; the run loop uses them to keep a good
gaze bias, disable garbage head-pose, and cap a wild gaze baseline.
"""
import os
import sys

import pytest

_deps = ["cv2", "numpy", "uniface", "onnxruntime"]
_missing = [d for d in _deps if __import__("importlib").util.find_spec(d) is None]
pytestmark = pytest.mark.skipif(bool(_missing),
                                reason=f"proctor deps missing: {', '.join(_missing)}")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import MagicMock  # noqa: E402
for _m in ["sounddevice", "uniface", "insightface"]:
    sys.modules.setdefault(_m, MagicMock())
os.environ.setdefault("HEADLESS", "True")
os.environ.setdefault("SKIP_ENROLLMENT", "True")

import proctor  # noqa: E402


class TestHeadBiasPlausible:
    def test_garbage_head_yaw_rejected(self):
        # The actual v2.3.50 dot-calibration output.
        assert proctor._head_bias_plausible(-38, -9) is False

    def test_garbage_self_cal_head_rejected(self):
        # The self-cal froze +53° — also garbage.
        assert proctor._head_bias_plausible(53, 10) is False

    def test_reasonable_head_accepted(self):
        assert proctor._head_bias_plausible(10, 5) is True

    def test_boundaries(self):
        assert proctor._head_bias_plausible(20, 25) is True     # inclusive
        assert proctor._head_bias_plausible(21, 0) is False
        assert proctor._head_bias_plausible(0, 26) is False


class TestGazeBiasPlausible:
    def test_garbage_gaze_rejected(self):
        # The self-cal froze +0.51rad as "centre".
        assert proctor._gaze_bias_plausible(0.51, 0.13) is False

    def test_good_dot_calibration_gaze_accepted(self):
        # The dot-calibration's actual (good) gaze bias — must NOT be discarded.
        assert proctor._gaze_bias_plausible(-0.0955, -0.0155) is True

    def test_boundaries(self):
        assert proctor._gaze_bias_plausible(0.35, 0.35) is True   # inclusive
        assert proctor._gaze_bias_plausible(0.36, 0.0) is False
        assert proctor._gaze_bias_plausible(0.0, -0.36) is False

"""Tests for the pre-violation context-frame evidence feature.

Covers the three server-side seams the 1 Hz pre-violation ring buffer lands on:
  - the list-matcher that gathers ctx_ frames for a flag (sessions.py),
  - the primary matcher's refusal to ever return a ctx_ frame as the primary,
  - _save_context_frames, which anchors each frame's filename to
    flag_time - offset_ms so the strip orders t-3s -> t-1s -> flag,
  - the FrameIn / ContextFrame request models that carry the packet.

This is the appeal-defeating evidence: when a flag fires, the 3 seconds of
frames captured BEFORE it (dropped pen vs. phone) travel with it.
"""
import base64
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.sessions import (  # noqa: E402
    match_context_screenshots_for_violation as mctx,
    match_screenshot_for_violation as mprimary,
)

# A minimal but real JPEG-ish payload (SOI marker + bytes) so b64decode round-trips.
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 8
_B64 = base64.b64encode(_JPEG).decode()


def test_context_matcher_orders_and_windows():
    v = {"created_at": "2026-01-01T12:00:05+00:00", "violation_type": "phone_in_hand"}
    names = [
        "ctx_phone_in_hand_20260101_120002.jpg",  # t-3
        "ctx_phone_in_hand_20260101_120004.jpg",  # t-1
        "ctx_phone_in_hand_20260101_120003.jpg",  # t-2
        "ctx_phone_in_hand_20260101_115000.jpg",  # 10 min before → outside window
        "ctx_multiple_faces_20260101_120003.jpg",  # wrong type
        "evt_phone_in_hand_20260101_120005.jpg",   # primary, not a ctx frame
    ]
    ss = {n: Path(n) for n in names}
    out = [p.name for p in mctx(v, ss)]
    assert out == [
        "ctx_phone_in_hand_20260101_120002.jpg",
        "ctx_phone_in_hand_20260101_120003.jpg",
        "ctx_phone_in_hand_20260101_120004.jpg",
    ]


def test_context_matcher_empty_for_event_without_context():
    v = {"created_at": "2026-01-01T12:00:05+00:00", "violation_type": "gaze_away"}
    ss = {"evt_gaze_away_20260101_120005.jpg": Path("x")}
    assert mctx(v, ss) == []


def test_primary_matcher_never_returns_context_frame():
    # Only a ctx_ frame is present (no evt_). The primary matcher must NOT
    # fall back to it — a context frame can never stand in as the primary.
    v = {"created_at": "2026-01-01T12:00:05+00:00", "violation_type": "phone_in_hand"}
    ss = {"ctx_phone_in_hand_20260101_120004.jpg": Path("c")}
    assert mprimary(v, ss) is None


# ── _save_context_frames: filenames anchored to flag_time - offset_ms ──
def _ctx(offset_ms, b64=_B64):
    from app.models.exam import ContextFrame
    return ContextFrame(frame_b64=b64, offset_ms=offset_ms)


def test_save_context_frames_anchors_timestamps_to_flag(tmp_path):
    from app.routers.exam import _save_context_frames
    flag = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    # Out-of-order on input; filenames must still derive from flag - offset.
    frames = [_ctx(1000), _ctx(3000), _ctx(2000)]
    out = _save_context_frames(str(tmp_path), "phone_in_hand", frames, flag)

    assert out == [
        "ctx_phone_in_hand_20260101_120004.jpg",  # flag - 1s
        "ctx_phone_in_hand_20260101_120002.jpg",  # flag - 3s
        "ctx_phone_in_hand_20260101_120003.jpg",  # flag - 2s
    ]
    # Files actually exist on disk with the decoded bytes.
    for name in out:
        p = tmp_path / name
        assert p.exists() and p.read_bytes() == _JPEG

    # And the matcher re-gathers them in chronological order for the flag.
    violation = {"created_at": "2026-01-01T12:00:05+00:00",
                 "violation_type": "phone_in_hand"}
    ss = {name: (tmp_path / name) for name in out}
    assert [p.name for p in mctx(violation, ss)] == [
        "ctx_phone_in_hand_20260101_120002.jpg",
        "ctx_phone_in_hand_20260101_120003.jpg",
        "ctx_phone_in_hand_20260101_120004.jpg",
    ]


def test_save_context_frames_sanitizes_event_label(tmp_path):
    from app.routers.exam import _save_context_frames
    flag = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    out = _save_context_frames(str(tmp_path), "phone/in hand!", [_ctx(1000)], flag)
    assert len(out) == 1
    # Path-unsafe chars are collapsed to underscores; no separators leak.
    assert "/" not in out[0] and " " not in out[0]
    assert out[0].startswith("ctx_phone_in_hand_")


def test_save_context_frames_skips_bad_frame_best_effort(tmp_path):
    """A single undecodable frame must never fail the whole packet — the
    good frames still land (the flag upload can't be lost over context)."""
    from app.routers.exam import _save_context_frames
    flag = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    bad = _ctx(2000, b64="a")  # invalid base64 -> raises on decode
    out = _save_context_frames(str(tmp_path), "gaze_away", [_ctx(1000), bad], flag)
    assert out == ["ctx_gaze_away_20260101_120004.jpg"]
    assert (tmp_path / out[0]).exists()


def test_save_context_frames_empty_input(tmp_path):
    from app.routers.exam import _save_context_frames
    flag = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    assert _save_context_frames(str(tmp_path), "phone_in_hand", [], flag) == []


# ── request models carry the packet without breaking old clients ──
def test_framein_accepts_context_frames_and_defaults_none():
    from app.models.exam import FrameIn
    # Old clients omit the field entirely -> None, unchanged behaviour.
    legacy = FrameIn(session_id="r_t", frame=_B64, timestamp="2026-01-01T12:00:05Z")
    assert legacy.context_frames is None

    fi = FrameIn(session_id="r_t", frame=_B64, timestamp="2026-01-01T12:00:05Z",
                 event_type="phone_in_hand",
                 context_frames=[{"frame_b64": _B64, "offset_ms": 3000}])
    assert len(fi.context_frames) == 1
    assert fi.context_frames[0].offset_ms == 3000


def test_contextframe_is_strict_on_offset():
    from pydantic import ValidationError

    from app.models.exam import ContextFrame
    ContextFrame(frame_b64=_B64, offset_ms=3000)  # ok
    with pytest.raises(ValidationError):
        ContextFrame(frame_b64=_B64, offset_ms="3000")  # strict: no str->int coercion

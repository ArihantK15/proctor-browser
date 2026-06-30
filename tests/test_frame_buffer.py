"""Unit tests for frame_buffer.FrameRingBuffer (desktop pre-violation buffer)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame_buffer import FrameRingBuffer  # noqa: E402


class _Clock:
    """Injectable monotonic clock."""
    def __init__(self):
        self.t = 1000.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


def test_1hz_gate_rejects_rapid_pushes():
    clk = _Clock()
    buf = FrameRingBuffer(_now=clk)
    assert buf.maybe_push("a") is True          # first always accepted
    assert buf.maybe_push("b") is False         # <1s later → rejected
    clk.advance(0.5)
    assert buf.maybe_push("c") is False         # still <1s since last push
    clk.advance(0.6)                            # now 1.1s since last accepted
    assert buf.maybe_push("d") is True
    assert len(buf) == 2                         # only a and d stored


def test_empty_frame_not_stored():
    buf = FrameRingBuffer(_now=_Clock())
    assert buf.maybe_push("") is False
    assert buf.maybe_push(None) is False
    assert len(buf) == 0


def test_eviction_keeps_only_buffer_depth():
    clk = _Clock()
    # 5s buffer at 1Hz → holds 5 frames; 8 pushes (1s apart) → newest 5 retained
    buf = FrameRingBuffer(buffer_secs=5.0, push_interval=1.0, _now=clk)
    for i in range(8):
        buf.maybe_push(f"f{i}")
        clk.advance(1.0)
    assert len(buf) == 5
    # context should be the 3 newest (f5,f6,f7), oldest-first
    ctx = buf.context_before(3)
    assert [c["frame_b64"] for c in ctx] == ["f5", "f6", "f7"]


def test_context_offsets_measured_back_from_flag():
    clk = _Clock()
    buf = FrameRingBuffer(_now=clk)
    # push 3 frames at t=0,1,2 (relative); flag at t=2.0
    buf.maybe_push("t-2"); clk.advance(1.0)
    buf.maybe_push("t-1"); clk.advance(1.0)
    buf.maybe_push("t-0")
    flag = clk()
    ctx = buf.context_before(3, flag_ts=flag)
    assert [c["frame_b64"] for c in ctx] == ["t-2", "t-1", "t-0"]
    # oldest-first → decreasing offsets; newest ~0ms
    offs = [c["offset_ms"] for c in ctx]
    assert offs == sorted(offs, reverse=True)
    assert offs[0] == 2000 and offs[-1] == 0
    assert all(o >= 0 for o in offs)


def test_context_caps_at_available_frames():
    clk = _Clock()
    buf = FrameRingBuffer(_now=clk)
    buf.maybe_push("only")
    ctx = buf.context_before(3)
    assert len(ctx) == 1 and ctx[0]["frame_b64"] == "only"


def test_clear_empties_buffer_and_resets_gate():
    clk = _Clock()
    buf = FrameRingBuffer(_now=clk)
    buf.maybe_push("a")
    buf.clear()
    assert len(buf) == 0
    # gate reset → next push accepted immediately even without advancing clock
    assert buf.maybe_push("b") is True

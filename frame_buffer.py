"""frame_buffer.py — desktop-camera pre-violation ring buffer (Procta).

Holds the last few seconds of already-JPEG-encoded camera frames in RAM so
that, when proctor.py flags a serious violation, it can attach the frames
captured in the seconds BEFORE the flag as appeal context — the "dashcam for
exams" buffer. A single static frame ("turned head") is weak, contestable
evidence; t-3s..t-0 gives the teacher the why.

Design notes
------------
- **Pure + dependency-free** (no cv2/numpy). proctor.py encodes each frame to a
  base64 JPEG and pushes the bytes here. That keeps this module unit-testable
  and lets it ship via extraResources alongside behavioral_analysis.py /
  audio_processor.py.
- **1 Hz, wall-clock gated.** Frames are admitted at most once per second based
  on a real clock, NOT once per proctor-loop iteration — so a throttled capture
  loop (hardware governor) or a slow laptop still yields ~1 context frame/sec.
- **Latency-proof.** The packet is anchored on the FLAG timestamp; context
  offsets are measured back from it. As long as the buffer is a little deeper
  than worst-case AI latency (5 s vs sub-second per-frame inference), the real
  moment is always still in RAM when the flag fires.

Privacy (DPDP Act 2023, data minimisation)
------------------------------------------
Frames live ONLY in this in-RAM deque, are overwritten on the 1 Hz cadence, and
are NEVER written to disk. Non-anomalous frames are evicted and gone; only on a
violation are the surviving context frames read out and uploaded. Nothing here
persists past process exit.
"""
import time
from collections import deque

PUSH_INTERVAL_SECS = 1.0   # 1 Hz capture cadence into the buffer
BUFFER_SECS = 5.0          # internal depth — safety margin over worst-case AI latency
CONTEXT_FRAMES = 3         # pre-flag frames attached to an evidence packet


class FrameRingBuffer:
    """In-RAM 1 Hz ring of (timestamp, jpeg_b64). See module docstring."""

    def __init__(self, *, buffer_secs: float = BUFFER_SECS,
                 push_interval: float = PUSH_INTERVAL_SECS,
                 _now=time.monotonic):
        self._now = _now
        self._interval = push_interval
        # +1 so we always retain at least CONTEXT_FRAMES worth even at the boundary.
        self._maxlen = max(1, int(round(buffer_secs / push_interval)))
        self._buf: deque = deque(maxlen=self._maxlen)  # (ts, jpeg_b64)
        self._last_push = None

    def maybe_push(self, jpeg_b64) -> bool:
        """Admit a frame if >= push_interval since the last one (1 Hz gate).

        Returns True when the frame was stored, False when rate-limited or empty.
        Wall-clock gated, so capture-loop fps has no effect on the cadence.
        """
        if not jpeg_b64:
            return False
        now = self._now()
        if self._last_push is not None and (now - self._last_push) < self._interval:
            return False
        self._buf.append((now, jpeg_b64))
        self._last_push = now
        return True

    def context_before(self, n: int = CONTEXT_FRAMES, flag_ts=None) -> list:
        """Up to *n* most-recent buffered frames as appeal context, OLDEST-FIRST.

        Each item is ``{"frame_b64": str, "offset_ms": int}`` where offset_ms is
        how long BEFORE *flag_ts* the frame was captured (>= 0). flag_ts defaults
        to "now"; the server reconstructs each frame's wall-clock as
        ``flag_time - offset_ms`` so the timeline orders them t-3 → t-1 → flag.
        """
        if flag_ts is None:
            flag_ts = self._now()
        recent = list(self._buf)[-n:] if n > 0 else []
        out = []
        for ts, b64 in recent:  # deque is oldest→newest, slice preserves that
            offset_ms = int(max(0, round((flag_ts - ts) * 1000)))
            out.append({"frame_b64": b64, "offset_ms": offset_ms})
        return out

    def __len__(self) -> int:
        return len(self._buf)

    def clear(self) -> None:
        self._buf.clear()
        self._last_push = None


__all__ = ["FrameRingBuffer", "PUSH_INTERVAL_SECS", "BUFFER_SECS", "CONTEXT_FRAMES"]

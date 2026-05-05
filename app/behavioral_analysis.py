"""
behavioral_analysis.py — Multi-signal correlation engine for Procta.

Combines independent detection signals (gaze, voice, phone, face position,
head pose) into higher-level behavioral patterns that indicate likely cheating.

Patterns detected:
  phone_consulting      — gaze down + phone in hand simultaneously
  note_reading          — sustained gaze away + head turned + no voice
  collaboration         — voice detected + face away + multiple faces
  answer_memo           — gaze away → look down → look up → (answer/click) sequence
  nervous_evasion       — many micro-glances (<2s each) in a short window
  sustained_offtask     — cumulative gaze away > threshold in a time window

Each pattern emits a composite event with confidence score and contributing
signals, enabling the teacher dashboard to show actionable intelligence
instead of raw violation counts.
"""
import time
from collections import deque
from typing import Optional


# ─── SIGNAL BUFFER ──────────────────────────────────────────────────────────────
# A 60-second sliding window that stores all per-frame signals.
# Each entry is a dict with timestamp + signal values.

SIGNAL_BUFFER_SECS = 60.0


class SignalBuffer:
    """Sliding window buffer of per-frame signals for behavioral analysis."""

    def __init__(self, window_secs: float = SIGNAL_BUFFER_SECS):
        self._window = window_secs
        self._entries: deque = deque()

    def push(self, signal: dict):
        """Record a frame's signals. signal must have a 't' key (timestamp)."""
        signal.setdefault("t", time.time())
        self._entries.append(signal)
        self._prune()

    def _prune(self):
        cutoff = time.time() - self._window
        while self._entries and self._entries[0]["t"] < cutoff:
            self._entries.popleft()

    def get_entries(self, lookback_secs: Optional[float] = None) -> list:
        """Return all entries in the window, optionally restricted to last N seconds."""
        self._prune()
        if lookback_secs is None:
            return list(self._entries)
        cutoff = time.time() - lookback_secs
        return [e for e in self._entries if e["t"] >= cutoff]

    def count_entries(self, lookback_secs: Optional[float] = None) -> int:
        return len(self.get_entries(lookback_secs))

    def clear(self):
        self._entries.clear()


# ─── PATTERN MATCHERS ───────────────────────────────────────────────────────────
# Each matcher examines the buffer and returns a match result dict or None.
# Result dict: {"pattern": str, "confidence": float, "detail": str}
# Confidence is 0.0–1.0.

def match_phone_consulting(buffer: SignalBuffer, lookback_secs: float = 5.0):
    """Phone consulting: gaze down + phone in hand simultaneously.

    Both signals must be present in the same frame for this to fire.
    """
    for entry in buffer.get_entries(lookback_secs):
        if entry.get("gaze_down") and entry.get("phone_in_hand"):
            dur = entry.get("gaze_down_secs", 0)
            conf = min(1.0, 0.7 + dur * 0.1)
            return {
                "pattern": "phone_consulting",
                "confidence": round(conf, 2),
                "detail": f"Looking down while holding phone ({dur:.0f}s)",
            }
    return None


def match_note_reading(buffer: SignalBuffer, lookback_secs: float = 10.0):
    """Note reading: sustained gaze away + head turned + no voice.

    The student looks off-screen for an extended period while remaining silent —
    consistent with reading notes placed beside or below the monitor.
    """
    entries = buffer.get_entries(lookback_secs)
    if not entries:
        return None

    consecutive_offtask = 0
    for entry in entries:
        gaze_away = entry.get("gaze_away", False)
        head_turned = entry.get("head_turned", False)
        voice = entry.get("voice_active", False)

        if gaze_away and head_turned and not voice:
            consecutive_offtask += 1
        else:
            consecutive_offtask = 0

        if consecutive_offtask >= 15:
            dur = consecutive_offtask / 15.0
            conf = min(1.0, 0.6 + dur * 0.05)
            return {
                "pattern": "note_reading",
                "confidence": round(conf, 2),
                "detail": f"Sustained off-screen gaze + head turned ({dur:.0f}s, silent)",
            }
    return None


def match_collaboration(buffer: SignalBuffer, lookback_secs: float = 8.0):
    """Collaboration: voice detected + face away + multiple faces.

    The student is talking while looking away, and another face is detected —
    consistent with talking to someone else in the room.
    """
    for entry in buffer.get_entries(lookback_secs):
        voice = entry.get("voice_active", False)
        face_away = entry.get("face_away", False)
        multiple = entry.get("multiple_faces", False)

        if voice and face_away:
            conf = 0.75
            if multiple:
                conf = 0.95
            return {
                "pattern": "collaboration",
                "confidence": round(conf, 2),
                "detail": f"Talking while looking away{' + second face' if multiple else ''}",
            }
    return None


def match_answer_memo(buffer: SignalBuffer, lookback_secs: float = 12.0):
    """Answer memo: gaze away → look down → look up sequence.

    Classic cheat pattern: read answer off-screen, memorize it, look back
    at screen to type/click the answer. The full sequence takes ~5-8s.
    """
    entries = buffer.get_entries(lookback_secs)
    if len(entries) < 5:
        return None

    state = 0
    sequence_start = None
    for entry in entries:
        t = entry["t"]
        gaze_away = entry.get("gaze_away", False)
        gaze_down = entry.get("gaze_down", False)
        centered = entry.get("gaze_centered", False)

        if state == 0 and gaze_away and not gaze_down:
            state = 1
            sequence_start = t
        elif state == 1 and gaze_down:
            state = 2
        elif state == 2 and centered:
            state = 3
            dur = t - sequence_start if sequence_start else 0
            if 3.0 <= dur <= 12.0:
                conf = min(1.0, 0.7 + (1.0 / max(dur - 3, 1)))
                return {
                    "pattern": "answer_memo",
                    "confidence": round(conf, 2),
                    "detail": f"Look away → down → up sequence ({dur:.0f}s)",
                }
            state = 0
            sequence_start = None
        elif state == 2 and gaze_away and not gaze_down:
            state = 1
        elif not gaze_away and not gaze_down and not centered:
            state = 0
            sequence_start = None
    return None


def match_nervous_evasion(buffer: SignalBuffer, lookback_secs: float = 60.0):
    """Nervous evasion: 5+ micro-glances (<2s each) in 60s.

    Repeated quick glances away from screen — could indicate hidden material
    or general anxiety about being watched.
    """
    entries = buffer.get_entries(lookback_secs)
    if len(entries) < 3:
        return None

    micro_glance_count = 0
    in_glance = False
    glance_start = None

    for entry in entries:
        t = entry["t"]
        gaze_away = entry.get("gaze_away", False)

        if gaze_away and not in_glance:
            in_glance = True
            glance_start = t
        elif not gaze_away and in_glance:
            in_glance = False
            if glance_start is not None:
                glance_dur = t - glance_start
                if glance_dur < 2.0:
                    micro_glance_count += 1

    if in_glance and glance_start is not None:
        glance_dur = time.time() - glance_start
        if glance_dur < 2.0:
            micro_glance_count += 1

    if micro_glance_count >= 5:
        conf = min(1.0, 0.5 + micro_glance_count * 0.07)
        return {
            "pattern": "nervous_evasion",
            "confidence": round(conf, 2),
            "detail": f"{micro_glance_count} micro-glances in {lookback_secs:.0f}s",
        }
    return None


def match_sustained_offtask(buffer: SignalBuffer, lookback_secs: float = 60.0):
    """Sustained offtask: cumulative gaze away > 15s in 60s window.

    The student has spent a significant fraction of the last minute looking
    away from the screen, indicating they are not engaged with the exam.
    """
    entries = buffer.get_entries(lookback_secs)
    if len(entries) < 5:
        return None

    offtask_frames = 0
    for entry in entries:
        if entry.get("gaze_away", False) or entry.get("head_turned", False):
            offtask_frames += 1

    cumulative_secs = offtask_frames / 15.0
    threshold = 15.0

    if cumulative_secs >= threshold:
        ratio = cumulative_secs / lookback_secs
        conf = min(1.0, 0.5 + ratio * 1.5)
        return {
            "pattern": "sustained_offtask",
            "confidence": round(conf, 2),
            "detail": f"{cumulative_secs:.0f}s off-screen in last {lookback_secs:.0f}s ({ratio*100:.0f}%)",
        }
    return None


# ─── BEHAVIORAL ENGINE ──────────────────────────────────────────────────────────
# Runs all pattern matchers on each invocation. Returns the highest-confidence
# match (if any) to avoid spamming multiple patterns per frame.

PATTERN_MATCHERS = [
    match_phone_consulting,
    match_collaboration,
    match_answer_memo,
    match_note_reading,
    match_sustained_offtask,
    match_nervous_evasion,
]

PATTERN_SEVERITY = {
    "phone_consulting":    "critical",
    "collaboration":       "critical",
    "answer_memo":         "high",
    "note_reading":        "high",
    "sustained_offtask":   "medium",
    "nervous_evasion":     "medium",
}

PATTERN_CONFIDENCE = {
    "phone_consulting":    0.95,
    "collaboration":       0.92,
    "answer_memo":         0.88,
    "note_reading":        0.85,
    "sustained_offtask":   0.80,
    "nervous_evasion":     0.75,
}


class BehavioralEngine:
    """High-level interface: push signals, detect patterns, return matches."""

    def __init__(self, check_interval: int = 15):
        self.buffer = SignalBuffer()
        self.check_interval = check_interval
        self._frame_count = 0
        self._last_match = {}
        self._cooldown = 30.0

    def push(self, signal: dict):
        """Record a frame's signals."""
        self.buffer.push(signal)
        self._frame_count += 1

    def check(self) -> Optional[dict]:
        """Run all pattern matchers. Returns highest-confidence match or None."""
        self._frame_count += 1
        if self._frame_count % self.check_interval != 0:
            return None

        now = time.time()
        matches = []
        for matcher in PATTERN_MATCHERS:
            result = matcher(self.buffer)
            if result:
                pattern = result["pattern"]
                last_time = self._last_match.get(pattern, 0)
                if now - last_time < self._cooldown:
                    continue
                result["severity"] = PATTERN_SEVERITY.get(pattern, "medium")
                result["confidence_base"] = PATTERN_CONFIDENCE.get(pattern, 0.75)
                matches.append(result)

        if not matches:
            return None

        matches.sort(key=lambda m: m["confidence"], reverse=True)
        best = matches[0]
        self._last_match[best["pattern"]] = now
        return best

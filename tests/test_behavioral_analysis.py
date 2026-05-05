"""
Tests for behavioral_analysis.py — multi-signal correlation engine.

Verifies that each pattern matcher correctly identifies behavioral patterns
from synthetic signal sequences, and that the BehavioralEngine integrates
them properly with cooldowns and confidence scoring.
"""
import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from behavioral_analysis import (
    SignalBuffer,
    BehavioralEngine,
    match_phone_consulting,
    match_collaboration,
    match_answer_memo,
    match_note_reading,
    match_sustained_offtask,
    match_nervous_evasion,
    PATTERN_SEVERITY,
    PATTERN_CONFIDENCE,
    PATTERN_MATCHERS,
)


# ─── SignalBuffer ──────────────────────────────────────────────────────────────

class TestSignalBuffer:
    def test_push_and_get(self):
        buf = SignalBuffer(window_secs=60)
        buf.push({"gaze_away": True, "t": time.time()})
        entries = buf.get_entries()
        assert len(entries) == 1
        assert entries[0]["gaze_away"] is True

    def test_prunes_old_entries(self):
        buf = SignalBuffer(window_secs=1)
        buf.push({"gaze_away": True, "t": time.time() - 5})
        buf.push({"gaze_away": False, "t": time.time() - 2})
        buf.push({"gaze_away": True, "t": time.time()})
        entries = buf.get_entries()
        assert len(entries) == 1
        assert entries[0]["gaze_away"] is True

    def test_lookback_filter(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        buf.push({"gaze_away": True, "t": now - 30})
        buf.push({"gaze_away": False, "t": now - 5})
        buf.push({"gaze_away": True, "t": now})
        assert len(buf.get_entries(lookback_secs=10)) == 2
        assert len(buf.get_entries(lookback_secs=40)) == 3

    def test_count_entries(self):
        buf = SignalBuffer(window_secs=60)
        for i in range(10):
            buf.push({"t": time.time() - i})
        assert buf.count_entries() == 10
        assert buf.count_entries(lookback_secs=5) == 5

    def test_clear(self):
        buf = SignalBuffer(window_secs=60)
        buf.push({"t": time.time()})
        buf.push({"t": time.time()})
        buf.clear()
        assert buf.count_entries() == 0

    def test_auto_timestamp(self):
        buf = SignalBuffer(window_secs=60)
        buf.push({"gaze_away": True})
        assert "t" in buf.get_entries()[0]


# ─── Pattern: phone_consulting ──────────────────────────────────────────────────

class TestPhoneConsulting:
    def test_matches_when_both_signals_present(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"gaze_down": True, "phone_in_hand": True,
                   "gaze_down_secs": 3, "t": time.time()})
        result = match_phone_consulting(buf)
        assert result is not None
        assert result["pattern"] == "phone_consulting"
        assert result["confidence"] >= 0.7

    def test_no_match_without_gaze_down(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"gaze_down": False, "phone_in_hand": True,
                   "gaze_down_secs": 0, "t": time.time()})
        assert match_phone_consulting(buf) is None

    def test_no_match_without_phone(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"gaze_down": True, "phone_in_hand": False,
                   "gaze_down_secs": 3, "t": time.time()})
        assert match_phone_consulting(buf) is None

    def test_higher_confidence_with_longer_gaze(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"gaze_down": True, "phone_in_hand": True,
                   "gaze_down_secs": 10, "t": time.time()})
        result = match_phone_consulting(buf)
        assert result["confidence"] > 0.8

    def test_empty_buffer(self):
        buf = SignalBuffer(window_secs=10)
        assert match_phone_consulting(buf) is None


# ─── Pattern: collaboration ─────────────────────────────────────────────────────

class TestCollaboration:
    def test_matches_voice_and_face_away(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"voice_active": True, "face_away": True,
                   "multiple_faces": False, "t": time.time()})
        result = match_collaboration(buf)
        assert result is not None
        assert result["pattern"] == "collaboration"
        assert result["confidence"] == 0.75

    def test_higher_confidence_with_multiple_faces(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"voice_active": True, "face_away": True,
                   "multiple_faces": True, "t": time.time()})
        result = match_collaboration(buf)
        assert result["confidence"] == 0.95

    def test_no_match_without_voice(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"voice_active": False, "face_away": True,
                   "t": time.time()})
        assert match_collaboration(buf) is None

    def test_no_match_without_face_away(self):
        buf = SignalBuffer(window_secs=10)
        buf.push({"voice_active": True, "face_away": False,
                   "t": time.time()})
        assert match_collaboration(buf) is None


# ─── Pattern: answer_memo ───────────────────────────────────────────────────────

class TestAnswerMemo:
    def test_matches_full_sequence(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        # Need at least 5 entries (minimum length check)
        # Phase 1: gaze away (not down) — sustained
        buf.push({"gaze_away": True, "gaze_down": False,
                   "gaze_centered": False, "t": now - 6})
        buf.push({"gaze_away": True, "gaze_down": False,
                   "gaze_centered": False, "t": now - 5.5})
        buf.push({"gaze_away": True, "gaze_down": False,
                   "gaze_centered": False, "t": now - 5})
        # Phase 2: look down
        buf.push({"gaze_away": False, "gaze_down": True,
                   "gaze_centered": False, "t": now - 3})
        buf.push({"gaze_away": False, "gaze_down": True,
                   "gaze_centered": False, "t": now - 2})
        # Phase 3: back to center
        buf.push({"gaze_away": False, "gaze_down": False,
                   "gaze_centered": True, "t": now})
        result = match_answer_memo(buf)
        assert result is not None
        assert result["pattern"] == "answer_memo"

    def test_no_match_without_down_phase(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        buf.push({"gaze_away": True, "gaze_down": False,
                   "gaze_centered": False, "t": now - 5})
        buf.push({"gaze_away": False, "gaze_down": False,
                   "gaze_centered": True, "t": now})
        assert match_answer_memo(buf) is None

    def test_no_match_if_too_fast(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        buf.push({"gaze_away": True, "gaze_down": False,
                   "gaze_centered": False, "t": now - 1})
        buf.push({"gaze_away": False, "gaze_down": True,
                   "gaze_centered": False, "t": now - 0.5})
        buf.push({"gaze_away": False, "gaze_down": False,
                   "gaze_centered": True, "t": now})
        # Sequence takes < 3s, should not match
        assert match_answer_memo(buf) is None

    def test_too_few_entries(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        buf.push({"gaze_away": True, "gaze_down": False,
                   "gaze_centered": False, "t": now})
        assert match_answer_memo(buf) is None


# ─── Pattern: note_reading ──────────────────────────────────────────────────────

class TestNoteReading:
    def test_matches_sustained_pattern(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        for i in range(20):
            buf.push({"gaze_away": True, "head_turned": True,
                       "voice_active": False, "t": now - (20 - i) * 0.5})
        result = match_note_reading(buf)
        assert result is not None
        assert result["pattern"] == "note_reading"

    def test_no_match_if_voice_present(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        for i in range(20):
            buf.push({"gaze_away": True, "head_turned": True,
                       "voice_active": True, "t": now - (20 - i) * 0.5})
        assert match_note_reading(buf) is None

    def test_no_match_if_not_head_turned(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        for i in range(20):
            buf.push({"gaze_away": True, "head_turned": False,
                       "voice_active": False, "t": now - (20 - i) * 0.5})
        assert match_note_reading(buf) is None

    def test_no_match_if_insufficient_consecutive(self):
        buf = SignalBuffer(window_secs=15)
        now = time.time()
        for i in range(10):
            buf.push({"gaze_away": True, "head_turned": True,
                       "voice_active": False, "t": now - (10 - i) * 0.5})
        # Only 10 consecutive, need 15
        assert match_note_reading(buf) is None


# ─── Pattern: sustained_offtask ─────────────────────────────────────────────────

class TestSustainedOfftask:
    def test_matches_high_offtask_ratio(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        # Need ~225+ frames of off-task to exceed 15s threshold
        # (225 / 15fps = 15s). Push 250 entries tightly spaced.
        for i in range(250):
            buf.push({"gaze_away": True, "head_turned": False,
                       "t": now - (250 - i) * 0.066})
        result = match_sustained_offtask(buf)
        assert result is not None
        assert result["pattern"] == "sustained_offtask"

    def test_no_match_if_low_offtask(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        for i in range(60):
            buf.push({"gaze_away": False, "head_turned": False,
                       "t": now - (60 - i) * 0.9})
        assert match_sustained_offtask(buf) is None

    def test_matches_with_head_turned(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        # Need enough frames to exceed 15s threshold at 15fps
        for i in range(250):
            buf.push({"gaze_away": False, "head_turned": True,
                       "t": now - (250 - i) * 0.066})
        result = match_sustained_offtask(buf)
        assert result is not None


# ─── Pattern: nervous_evasion ───────────────────────────────────────────────────

class TestNervousEvasion:
    def test_matches_many_micro_glances(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        # 6 micro-glances, each ~1s long
        for i in range(6):
            t_start = now - (12 - i * 2)
            buf.push({"gaze_away": True, "t": t_start})
            buf.push({"gaze_away": False, "t": t_start + 1.0})
        result = match_nervous_evasion(buf)
        assert result is not None
        assert result["pattern"] == "nervous_evasion"

    def test_no_match_with_few_glances(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        # Only 2 micro-glances
        for i in range(2):
            t_start = now - (4 - i * 2)
            buf.push({"gaze_away": True, "t": t_start})
            buf.push({"gaze_away": False, "t": t_start + 1.0})
        assert match_nervous_evasion(buf) is None

    def test_no_match_for_sustained_glance(self):
        buf = SignalBuffer(window_secs=60)
        now = time.time()
        # One long glance (5s) — not a micro-glance
        buf.push({"gaze_away": True, "t": now - 5})
        buf.push({"gaze_away": False, "t": now})
        assert match_nervous_evasion(buf) is None


# ─── BehavioralEngine ───────────────────────────────────────────────────────────

class TestBehavioralEngine:
    def test_engine_pushes_and_checks(self):
        engine = BehavioralEngine(check_interval=1)
        now = time.time()
        for i in range(20):
            engine.push({"gaze_away": True, "head_turned": True,
                          "voice_active": False, "t": now - (20 - i) * 0.5})
        result = engine.check()
        assert result is not None
        assert "pattern" in result
        assert "confidence" in result
        assert "severity" in result

    def test_cooldown_prevents_spam(self):
        engine = BehavioralEngine(check_interval=1)
        engine._cooldown = 30
        now = time.time()
        for i in range(20):
            engine.push({"gaze_away": True, "head_turned": True,
                          "voice_active": False, "t": now - (20 - i) * 0.5})
        result1 = engine.check()
        assert result1 is not None
        # Second check should return None due to cooldown
        result2 = engine.check()
        assert result2 is None

    def test_skips_when_not_check_interval(self):
        engine = BehavioralEngine(check_interval=10)
        for i in range(5):
            engine.push({"gaze_away": True, "t": time.time()})
        result = engine.check()
        assert result is None

    def test_returns_highest_confidence_match(self):
        engine = BehavioralEngine(check_interval=1)
        engine._cooldown = 0
        now = time.time()
        # Push signals that could trigger multiple patterns
        for i in range(20):
            engine.push({
                "gaze_away": True, "head_turned": True,
                "voice_active": False, "gaze_down": True,
                "phone_in_hand": True, "gaze_down_secs": 3,
                "face_away": True, "multiple_faces": False,
                "gaze_centered": False, "t": now - (20 - i) * 0.5,
            })
        result = engine.check()
        assert result is not None
        # phone_consulting (0.7+0.3) should beat note_reading (0.6+0.05)
        assert result["confidence"] >= 0.7

    def test_all_patterns_have_severity_and_confidence(self):
        for pattern in PATTERN_SEVERITY:
            assert pattern in PATTERN_CONFIDENCE
            assert PATTERN_SEVERITY[pattern] in ("critical", "high", "medium", "low")
            assert 0 <= PATTERN_CONFIDENCE[pattern] <= 1


# ─── Pattern Registry ───────────────────────────────────────────────────────────

class TestPatternRegistry:
    def test_all_matchers_are_registered(self):
        expected = [
            match_phone_consulting,
            match_collaboration,
            match_answer_memo,
            match_note_reading,
            match_sustained_offtask,
            match_nervous_evasion,
        ]
        assert len(PATTERN_MATCHERS) == len(expected)
        for m in expected:
            assert m in PATTERN_MATCHERS

    def test_all_patterns_have_metadata(self):
        for matcher in PATTERN_MATCHERS:
            result = matcher(SignalBuffer(window_secs=60))
            # None is OK — empty buffer shouldn't match
            assert result is None or all(
                k in result for k in ("pattern", "confidence", "detail")
            )

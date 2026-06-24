"""B2 (transcript-on-flag): the AudioProcessor exposes a bounded recent
transcript so the proctor can attach it to a COLLABORATION flag only — never a
continuous stream. Tests the public recent_transcript() accessor."""
import time

import pytest

try:
    from audio_processor import AudioProcessor, AudioRingBuffer
except Exception as e:  # pragma: no cover - deps missing in some envs
    pytest.skip(f"audio_processor import failed: {e}", allow_module_level=True)


def _mk():
    return AudioProcessor(
        ring=AudioRingBuffer(),
        log_event_cb=lambda *a: None,
        save_evidence_cb=lambda *a: None,
    )


def test_recent_transcript_returns_recent_text():
    ap = _mk()
    ap._transcript.append((time.time(), "what is the answer to question two"))
    assert "answer to question two" in ap.recent_transcript()


def test_recent_transcript_empty_when_nothing_recorded():
    assert _mk().recent_transcript() == ""


def test_recent_transcript_drops_stale_speech():
    ap = _mk()
    ap._transcript.append((time.time() - 99999, "spoken long ago"))
    assert ap.recent_transcript() == ""


def test_recent_transcript_is_bounded():
    ap = _mk()
    ap._transcript.append((time.time(), "x" * 1000))
    assert len(ap.recent_transcript()) <= 200

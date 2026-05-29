"""
Unit tests for the pure-logic surface of audio_processor.py.

Vosk + Silero VAD live behind a soft-import; these tests cover the
parts that don't need either to be installed:

  • text normalisation (lowercase, strip punct, collapse whitespace)
  • keyword merging (built-in defaults + per-exam additions, deduped)
  • 2-cluster silhouette heuristic (separable clusters → 2 voices;
    overlapping clusters → 1)
  • AudioRingBuffer producer/consumer + overwrite-on-full behaviour
  • AudioProcessor.available() returns False when models aren't
    downloaded (the dev-machine case)

Vosk-dependent flows (recognizer feed, end-to-end keyword detection)
are out of scope for unit tests — they need the 40 MB model archive.
Manual smoke covered in the plan's verification section.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import audio_processor as ap  # noqa: E402


class TestNormaliseText:
    def test_lowercase_and_strip_punct(self):
        assert ap._normalise_text("Option C, the Answer!") == "option c the answer"

    def test_collapse_whitespace(self):
        assert ap._normalise_text("  the   answer\tis  \nB ") == "the answer is b"

    def test_empty_input(self):
        assert ap._normalise_text("") == ""
        assert ap._normalise_text("   ") == ""

    def test_unicode_preserved(self):
        # Hindi keyword normalises sensibly
        s = ap._normalise_text("Jawab kya hai?")
        assert "jawab" in s
        assert "hai" in s


class TestLoadKeywords:
    def test_en_only_uses_default_en(self):
        kws = ap._load_keywords_for_lang("en", [])
        assert "option a" in kws
        assert "the answer is" in kws
        # No Hindi defaults leaked in
        assert "jawab" not in kws

    def test_hi_only_uses_default_hi(self):
        kws = ap._load_keywords_for_lang("hi", [])
        assert "jawab" in kws
        # No English defaults
        assert "option a" not in kws

    def test_en_plus_hi_unions_both(self):
        kws = ap._load_keywords_for_lang("en+hi", [])
        assert "option a" in kws
        assert "jawab" in kws

    def test_custom_merged_and_normalised(self):
        kws = ap._load_keywords_for_lang("en", ["Periodic Table", "Newton's Third Law"])
        assert "periodic table" in kws
        assert "newton s third law" in kws

    def test_dedupe_against_defaults(self):
        """A teacher who types 'option a' again shouldn't double the list."""
        kws = ap._load_keywords_for_lang("en", ["Option A", "OPTION A"])
        assert kws.count("option a") == 1


class TestTwoClusterSilhouette:
    def test_one_voice_below_min_cluster_size(self):
        """Below 2 * MIN_CLUSTER_SIZE total → can't form two clusters."""
        n, score = ap._twocluster_silhouette([[1.0, 2.0, 3.0]] * 4)
        assert n == 1

    def test_one_voice_identical_vectors(self):
        n, score = ap._twocluster_silhouette([[1.0, 2.0, 3.0]] * 10)
        assert n == 1
        assert score < ap.VOICE_COUNT_SILHOUETTE_THRESHOLD

    def test_two_voices_separable_clusters(self):
        """Two well-separated MFCC means → silhouette > threshold."""
        cluster_a = [[10.0, 0.0, 0.0] for _ in range(6)]
        cluster_b = [[0.0, 0.0, 10.0] for _ in range(6)]
        n, score = ap._twocluster_silhouette(cluster_a + cluster_b)
        assert n == 2
        assert score >= ap.VOICE_COUNT_SILHOUETTE_THRESHOLD

    def test_overlapping_clusters_stay_one(self):
        """Tiny perturbations of one vector → still one voice."""
        import random
        random.seed(42)
        vectors = [[1.0 + random.uniform(-0.01, 0.01),
                    2.0 + random.uniform(-0.01, 0.01),
                    3.0 + random.uniform(-0.01, 0.01)] for _ in range(12)]
        n, score = ap._twocluster_silhouette(vectors)
        # Either we say 1 voice, OR if 2, the silhouette score should
        # be near zero (very weak separation).
        assert n == 1 or score < 0.3


class TestRingBuffer:
    def test_write_read_roundtrip(self):
        rb = ap.AudioRingBuffer(max_secs=1.0)
        rb.write(b"hello world")
        out = rb.read(11, timeout=0.1)
        assert out == b"hello world"

    def test_partial_read(self):
        rb = ap.AudioRingBuffer(max_secs=1.0)
        rb.write(b"abcdef")
        assert rb.read(3, timeout=0.1) == b"abc"
        assert rb.read(3, timeout=0.1) == b"def"

    def test_overwrite_on_full(self):
        """When the producer races ahead, oldest bytes get dropped —
        the consumer never sees stale data older than the cap."""
        rb = ap.AudioRingBuffer(max_secs=0.001)  # tiny: ~32 bytes capacity
        cap_bytes = rb._cap
        # Write more than capacity
        rb.write(b"X" * cap_bytes)
        rb.write(b"Y" * 10)
        out = rb.read(cap_bytes, timeout=0.1)
        # The trailing Y's must be present (newest data preserved)
        assert b"Y" in out
        # Total drained doesn't exceed capacity
        assert len(out) <= cap_bytes

    def test_read_timeout_returns_empty(self):
        rb = ap.AudioRingBuffer(max_secs=1.0)
        t0 = time.time()
        out = rb.read(10, timeout=0.1)
        assert out == b""
        assert time.time() - t0 >= 0.09  # actually waited


class TestProcessorAvailability:
    def test_available_returns_false_without_models(self, tmp_path, monkeypatch):
        """On a dev machine the 40 MB Vosk model dir doesn't exist;
        available() must return False so _start_audio() can skip
        starting the worker."""
        monkeypatch.setattr(ap, "VOSK_EN_MODEL_DIR", str(tmp_path / "nonexistent"))
        assert ap.AudioProcessor.available() is False


class TestExports:
    def test_public_symbols_present(self):
        """Anything proctor.py soft-imports must be in __all__ so a
        future refactor doesn't quietly break the integration."""
        for name in ("AudioProcessor", "AudioRingBuffer",
                     "DEFAULT_EN", "DEFAULT_HI",
                     "_load_keywords_for_lang", "_normalise_text",
                     "_twocluster_silhouette", "SAMPLE_RATE"):
            assert name in ap.__all__
            assert hasattr(ap, name)

"""On-device audio detection — keyword spotting + multi-voice check.

Phase 75 of the audio + intervention plan. Runs alongside the existing
RMS-only voice path in proctor.py; never replaces it. Two new event
types pushed through the same `log_if_allowed` + `save_evidence`
pipeline the proctor already uses:

  keyword_uttered           student spoke a flagged phrase
  multiple_voices_detected  two distinct voices in the last ~60 s

Privacy posture is unchanged: no raw audio leaves the device. Only the
event metadata + the existing camera JPEG snapshot the proctor
already uploads.

Architecture
------------
- A SINGLE background worker thread (`AudioProcessor.run`) consumes
  16-kHz mono PCM frames from a ring buffer that proctor.py's
  existing `audio_thread()` callback feeds. The buffer is intentionally
  the only coupling point.
- VAD (Silero) gates everything: on silence we don't run Vosk, we
  don't extract MFCCs. ~30 ms cadence.
- On voice-active chunks: feed Vosk recognizer(s) — one per active
  language. After every partial result, normalise + substring-match
  against the merged keyword list. Fires `keyword_uttered` with the
  matched phrase + a ~5 s transcript snippet in the details string.
- In parallel: every voice-active segment contributes its mean MFCC
  vector to a rolling 60-s buffer. Every 5 s, run a simple 2-cluster
  silhouette check; if separation > threshold and both clusters have
  ≥ 3 vectors, fire `multiple_voices_detected`.
- HardwareGovernor integration: the worker reads
  `governor.effective_fps` each cycle. If the governor has throttled
  the video loop below TARGET_FPS, the audio worker skips every other
  pass — keyword latency degrades from ~2 s to ~4 s instead of
  freezing the exam UI.

Soft-import boundary
--------------------
vosk + python_speech_features are both soft-imported. If either is
missing — typical for a dev machine that hasn't run the model
download yet — the module logs "audio processor unavailable" ONCE
and the proctor keeps running with the existing RMS path. Same
pattern as proctor.py uses for sounddevice and psutil.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("audio_processor")

# Sample rate the proctor's audio_thread uses. Hard-coded to match;
# downsampling here would just add CPU cost for no gain.
SAMPLE_RATE = 16000

# Vosk consumes 16-kHz int16 PCM. Default model paths under ./weights/.
# Override via env to point at a different download location.
_DEFAULT_WEIGHTS_DIR = Path(__file__).parent / "weights"
VOSK_EN_MODEL_DIR = os.environ.get(
    "PROCTOR_VOSK_EN_MODEL",
    str(_DEFAULT_WEIGHTS_DIR / "vosk-model-small-en-in-0.4"),
)
VOSK_HI_MODEL_DIR = os.environ.get(
    "PROCTOR_VOSK_HI_MODEL",
    str(_DEFAULT_WEIGHTS_DIR / "vosk-model-small-hi-0.22"),
)
SILERO_VAD_ONNX = os.environ.get(
    "PROCTOR_SILERO_VAD",
    str(_DEFAULT_WEIGHTS_DIR / "silero_vad.onnx"),
)

# ── Built-in defaults — see plan for sync requirements ──────────
# MUST stay in lockstep with the dashboard's placeholder + the SQL
# default. Per-exam keywords are MERGED with these at proctor launch.
DEFAULT_EN = [
    "option a", "option b", "option c", "option d",
    "the answer is", "answer is", "correct answer",
    "what is the answer", "help me", "tell me",
    "first question", "second question", "third question",
]
DEFAULT_HI = [
    "jawab", "sahi hai", "uttar",
    "option ek", "option do", "option teen", "option char",
    "kya hai jawab", "madad karo",
]

# Voice-count heuristic tunables
MFCC_BUFFER_SECS = 60.0
MFCC_NUM_COEFFS = 13
VOICE_COUNT_CHECK_EVERY_SECS = 5.0
VOICE_COUNT_MIN_CLUSTER_SIZE = 3
VOICE_COUNT_SILHOUETTE_THRESHOLD = 0.50
# Absolute-cosine-distance guard. Below this, the two "clusters" are
# essentially the same voice with mic noise — unit-normalising MFCCs
# can still produce a stable 2-partition with a silhouette > 0.5 in
# that case, which is a false-positive we don't want firing every
# 5 s of recorded silence. ~0.05 cosine distance ≈ 18° angle.
VOICE_COUNT_MIN_INTER_DIST = 0.05

# Transcript snippet shown to the teacher in the details string
TRANSCRIPT_SNIPPET_SECS = 5.0

# Cooldown so a long phrase containing the same keyword doesn't fire
# 10 times in a row. Same idea as proctor.py's per-event cooldowns.
KEYWORD_COOLDOWN_SECS = 8.0
VOICE_COUNT_COOLDOWN_SECS = 30.0


def _normalise_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Run before
    every keyword match so an utterance like 'Option C, the answer.'
    matches the literal 'option c'."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_keywords_for_lang(lang: str, custom: list[str]) -> list[str]:
    """Merge the built-in defaults for the active language with any
    teacher-supplied custom phrases. Returned list is pre-normalised
    so the hot path is just substring matching."""
    base: list[str] = []
    if lang in ("en", "en+hi"):
        base.extend(DEFAULT_EN)
    if lang in ("hi", "en+hi"):
        base.extend(DEFAULT_HI)
    base.extend(custom or [])
    seen: set[str] = set()
    out: list[str] = []
    for kw in base:
        norm = _normalise_text(kw)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


# ── numpy-only 2-cluster silhouette check ───────────────────────
#
# We avoid sklearn because (a) it's a 30 MB dep and (b) we only need
# the simplest possible question: "do these N MFCC vectors look like
# they came from 1 speaker or 2?". Algorithm:
#
#   1. Compute pairwise cosine distance matrix.
#   2. Find the two most-distant points → seed clusters A and B.
#   3. Assign every other point to whichever seed it's closer to.
#   4. Compute a simplified silhouette: for each point, mean intra-
#      cluster distance vs mean nearest-cluster distance.
#
# Returns (n_distinct_voices, silhouette_score). n_distinct_voices is
# 1 if either cluster is too small or silhouette is below threshold,
# 2 otherwise.

def _twocluster_silhouette(mfcc_vectors):
    try:
        import numpy as np
    except ImportError:
        return (1, 0.0)
    if len(mfcc_vectors) < 2 * VOICE_COUNT_MIN_CLUSTER_SIZE:
        return (1, 0.0)
    # float64 (default) instead of float32 — the silhouette score on
    # the boundary case (orthogonal/opposite clusters) was rounding
    # just below threshold on some BLAS builds in float32. We're
    # dealing with O(50) 13-coeff vectors; the bytes saved are
    # rounding error in the noise floor.
    X = np.asarray(mfcc_vectors, dtype=np.float64)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    sim = Xn @ Xn.T
    dist = 1.0 - sim
    np.fill_diagonal(dist, 0.0)
    # Seed selection: anchor at row 0, pick the most-distant row as
    # the second seed. Then re-pick row-0-replacement as the most-
    # distant row from the chosen seed. Deterministic + avoids the
    # numpy argmax tie-breaking that bit us in CI when many distances
    # tied at the global max.
    j = int(np.argmax(dist[0, :]))
    if j == 0:
        return (1, 0.0)
    i = int(np.argmax(dist[:, j]))
    if i == j:
        return (1, 0.0)
    # Absolute-distance guard: if even the FURTHEST pair of MFCC means
    # is close together, this is one voice with mic noise — not two.
    if float(dist[i, j]) < VOICE_COUNT_MIN_INTER_DIST:
        return (1, 0.0)
    # Assign each row to its closer seed
    labels = np.where(dist[:, i] < dist[:, j], 0, 1)
    a_idx = np.where(labels == 0)[0]
    b_idx = np.where(labels == 1)[0]
    if len(a_idx) < VOICE_COUNT_MIN_CLUSTER_SIZE or len(b_idx) < VOICE_COUNT_MIN_CLUSTER_SIZE:
        return (1, 0.0)
    # Simplified silhouette: mean of (b - a) / max(a, b) per point
    sils = []
    for grp_self, grp_other in ((a_idx, b_idx), (b_idx, a_idx)):
        for p in grp_self:
            a = np.mean([dist[p, q] for q in grp_self if q != p]) if len(grp_self) > 1 else 0.0
            b = np.mean([dist[p, q] for q in grp_other])
            if max(a, b) > 0:
                sils.append((b - a) / max(a, b))
    if not sils:
        return (1, 0.0)
    score = float(sum(sils) / len(sils))
    if score >= VOICE_COUNT_SILHOUETTE_THRESHOLD:
        return (2, score)
    return (1, score)


# ── Ring buffer for the audio_thread → worker handoff ──────────
#
# `AudioRingBuffer` is intentionally minimal: a fixed-size bytearray
# that producer (audio_thread callback in proctor.py) writes to and
# consumer (this worker) drains. Both sides hold the same lock; the
# producer never blocks even if the consumer is slow — instead we
# overwrite the oldest data. Acceptable because the consumer keeps up
# in practice (~16 kHz × 2 bytes = 32 KB/s; we drain in 30 ms chunks).

class AudioRingBuffer:
    def __init__(self, max_secs: float = 30.0):
        self._cap = int(SAMPLE_RATE * max_secs * 2)  # int16 → 2 bytes
        self._buf = bytearray(self._cap)
        self._w = 0  # next write index
        self._r = 0  # next read index
        self._size = 0
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)

    def write(self, data: bytes) -> None:
        with self._lock:
            n = len(data)
            if n >= self._cap:
                data = data[-self._cap:]
                n = len(data)
            for i in range(n):
                self._buf[self._w] = data[i]
                self._w = (self._w + 1) % self._cap
            # Overwrote unread bytes → bump the read pointer + clamp size
            if self._size + n > self._cap:
                drop = self._size + n - self._cap
                self._r = (self._r + drop) % self._cap
                self._size = self._cap
            else:
                self._size += n
            self._not_empty.notify()

    def read(self, n_bytes: int, timeout: float = 1.0) -> bytes:
        with self._not_empty:
            deadline = time.time() + timeout
            while self._size == 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return b""
                self._not_empty.wait(timeout=remaining)
            take = min(n_bytes, self._size)
            out = bytearray(take)
            for i in range(take):
                out[i] = self._buf[self._r]
                self._r = (self._r + 1) % self._cap
            self._size -= take
            return bytes(out)


# ── Main worker ──────────────────────────────────────────────────

class AudioProcessor:
    """One worker thread, two detectors. Construct, then call start()
    once the proctor's audio_thread is feeding the shared ring buffer.

    log_event_cb: callable(event_type: str, severity: str, details: str)
                  — proctor.py's existing log_if_allowed function.
    save_evidence_cb: callable(label: str) — captures the most-recent
                     camera frame and queues it for upload. Phase 75
                     deliberately fires JPEG evidence for both new
                     events so the teacher sees who was in the room at
                     the moment of detection.
    get_effective_fps: callable() → float — proctor's HardwareGovernor
                      hook. Returns the current allowed FPS; we
                      compare to TARGET_FPS to decide when to throttle.
    target_fps: float — the proctor's target FPS, used as the
                throttling threshold. Default 15 (matches TARGET_FPS).
    """

    def __init__(
        self,
        *,
        ring: AudioRingBuffer,
        log_event_cb: Callable[[str, str, str], None],
        save_evidence_cb: Callable[[str], None],
        language: str = "en",
        custom_keywords: Optional[list[str]] = None,
        get_effective_fps: Optional[Callable[[], float]] = None,
        target_fps: float = 15.0,
    ):
        self._ring = ring
        self._log_event = log_event_cb
        self._save_evidence = save_evidence_cb
        self._language = language if language in ("en", "hi", "en+hi") else "en"
        self._custom = list(custom_keywords or [])
        self._keywords = _load_keywords_for_lang(self._language, self._custom)
        self._get_fps = get_effective_fps
        self._target_fps = target_fps

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Per-language recognizers, lazily initialised in run() so the
        # constructor stays cheap and import errors land at start()
        # time.
        self._recognizers: dict = {}
        self._vad = None

        # Recent transcript snippets for keyword-uttered details strings.
        # Each entry: (ts, text). Drained on read.
        self._transcript = deque()

        # MFCC buffer for voice-count check: (ts, vector) tuples.
        self._mfcc_buf: deque = deque()
        self._last_voice_count_check = 0.0

        # Cooldowns
        self._last_keyword_at: dict[str, float] = {}
        self._last_voice_count_at = 0.0

        # Throttle bookkeeping
        self._throttle_skip = False

    @staticmethod
    def available() -> bool:
        """True if both vosk and python_speech_features import cleanly
        AND the model directories exist on disk. The model check is
        the more common failure (deps install but the 40 MB downloads
        didn't)."""
        try:
            import vosk  # noqa: F401
            import python_speech_features  # noqa: F401
        except Exception:
            return False
        if not Path(VOSK_EN_MODEL_DIR).is_dir():
            return False
        return True

    def start(self) -> bool:
        if not self.available():
            logger.warning(
                "[audio_processor] unavailable — vosk/python_speech_features missing "
                "or model files not downloaded (run scripts/download_audio_models.sh)"
            )
            return False
        self._thread = threading.Thread(target=self.run, daemon=True, name="audio-worker")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    # ── Inner loop ──────────────────────────────────────────────

    def run(self) -> None:
        try:
            import vosk
            from python_speech_features import mfcc as _mfcc
            import numpy as np
        except Exception as e:
            logger.warning("[audio_processor] startup import failed: %s", e)
            return

        # Vosk: load each active language's recognizer
        try:
            if self._language in ("en", "en+hi"):
                self._recognizers["en"] = vosk.KaldiRecognizer(
                    vosk.Model(VOSK_EN_MODEL_DIR), SAMPLE_RATE
                )
            if self._language in ("hi", "en+hi") and Path(VOSK_HI_MODEL_DIR).is_dir():
                self._recognizers["hi"] = vosk.KaldiRecognizer(
                    vosk.Model(VOSK_HI_MODEL_DIR), SAMPLE_RATE
                )
        except Exception as e:
            logger.error("[audio_processor] vosk model load failed: %s", e)
            return
        if not self._recognizers:
            logger.warning("[audio_processor] no recognizers — bailing")
            return

        # Silero VAD: optional but strongly preferred. If the ONNX
        # isn't on disk we degrade to "always voice", which means Vosk
        # runs continuously — more CPU, no accuracy loss.
        vad_session = None
        try:
            if Path(SILERO_VAD_ONNX).is_file():
                import onnxruntime as ort
                vad_session = ort.InferenceSession(SILERO_VAD_ONNX, providers=["CPUExecutionProvider"])
        except Exception as e:
            logger.warning("[audio_processor] Silero VAD load failed (continuing without): %s", e)
            vad_session = None

        logger.info(
            "[audio_processor] started: langs=%s keywords=%d vad=%s",
            list(self._recognizers.keys()),
            len(self._keywords),
            "on" if vad_session else "off",
        )

        # Read ~500 ms at a time. Big enough for Vosk to make progress,
        # small enough that throttling is responsive.
        chunk_bytes = int(SAMPLE_RATE * 0.5) * 2
        cycle_n = 0
        while not self._stop.is_set():
            cycle_n += 1
            # Governor: if proctor video is throttled, skip every other
            # ASR pass. We still drain the ring buffer to avoid
            # backlog → MFCC stays current.
            throttled = self._is_throttled()
            run_asr = not throttled or (cycle_n % 2 == 0)

            data = self._ring.read(chunk_bytes, timeout=1.0)
            if not data:
                continue

            # Decide voice-active for this chunk. Pure-numpy RMS gate
            # as a cheap fallback when VAD is off.
            voice_active = self._voice_active(data, vad_session)
            if not voice_active:
                continue

            # MFCC for the voice-count heuristic. Cheap; run even when
            # ASR is throttled.
            try:
                pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                if len(pcm) > 0:
                    feats = _mfcc(pcm, samplerate=SAMPLE_RATE, numcep=MFCC_NUM_COEFFS,
                                  winlen=0.025, winstep=0.010, nfilt=26, nfft=512)
                    if feats.shape[0] > 0:
                        self._mfcc_buf.append((time.time(), feats.mean(axis=0).tolist()))
            except Exception as e:
                logger.debug("[audio_processor] MFCC step failed: %s", e)

            # Drop old MFCCs outside the rolling window.
            cutoff = time.time() - MFCC_BUFFER_SECS
            while self._mfcc_buf and self._mfcc_buf[0][0] < cutoff:
                self._mfcc_buf.popleft()

            # Voice-count check on a 5 s cadence.
            if time.time() - self._last_voice_count_check >= VOICE_COUNT_CHECK_EVERY_SECS:
                self._last_voice_count_check = time.time()
                self._check_voice_count()

            if not run_asr:
                continue

            # Feed each Vosk recognizer + check partial hypotheses.
            for lang_key, rec in self._recognizers.items():
                try:
                    rec.AcceptWaveform(data)
                    partial = json.loads(rec.PartialResult()).get("partial", "")
                    if partial:
                        self._record_transcript(partial)
                        self._check_keywords(partial)
                    # Periodically drain the final result so memory
                    # doesn't grow. Don't re-check keywords here — we
                    # already saw the substring in the partial.
                    if cycle_n % 20 == 0:
                        rec.Result()
                except Exception as e:
                    logger.debug("[audio_processor] recognizer %s step failed: %s", lang_key, e)

    # ── Helpers ─────────────────────────────────────────────────

    def _is_throttled(self) -> bool:
        if not self._get_fps:
            return False
        try:
            return self._get_fps() < self._target_fps
        except Exception:
            return False

    def _voice_active(self, pcm_bytes: bytes, vad_session) -> bool:
        try:
            import numpy as np
        except Exception:
            return True  # without numpy we can't gate; just process
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(pcm) == 0:
            return False
        # Cheap RMS gate covers most rooms. Silero only fine-tunes.
        rms = float(np.sqrt(np.mean(pcm * pcm)))
        if rms < 0.005:
            return False
        if vad_session is None:
            return True
        # Silero VAD expects a 512-sample window at 16 kHz returning a
        # P(speech). We sample a few windows from the chunk and OR them.
        try:
            window = 512
            sample_count = max(1, len(pcm) // window)
            for i in range(sample_count):
                w = pcm[i * window:(i + 1) * window]
                if len(w) < window:
                    break
                inp = w.astype(np.float32).reshape(1, -1)
                state_h = np.zeros((2, 1, 64), dtype=np.float32)
                state_c = np.zeros((2, 1, 64), dtype=np.float32)
                # Silero v4 signature: (input, h, c, sr) → (output, h, c)
                ort_inputs = {
                    "input": inp,
                    "h": state_h, "c": state_c,
                    "sr": np.array(SAMPLE_RATE, dtype=np.int64),
                }
                outs = vad_session.run(None, ort_inputs)
                p = float(outs[0].squeeze())
                if p > 0.5:
                    return True
            return False
        except Exception:
            return True  # any VAD error → fall through to "always voice"

    def _record_transcript(self, text: str) -> None:
        now = time.time()
        self._transcript.append((now, text))
        cutoff = now - 30.0
        while self._transcript and self._transcript[0][0] < cutoff:
            self._transcript.popleft()

    def _check_keywords(self, partial_text: str) -> None:
        if not self._keywords:
            return
        norm = _normalise_text(partial_text)
        if not norm:
            return
        now = time.time()
        for kw in self._keywords:
            if kw in norm:
                last = self._last_keyword_at.get(kw, 0.0)
                if now - last < KEYWORD_COOLDOWN_SECS:
                    continue
                self._last_keyword_at[kw] = now
                snippet = self._recent_snippet()
                details = f"Heard: '{snippet}' (matched: {kw})"
                try:
                    self._log_event("keyword_uttered", "high", details[:500])
                    self._save_evidence("keyword_uttered")
                except Exception as e:
                    logger.warning("[audio_processor] log/evidence for keyword failed: %s", e)
                break  # one match per partial is enough

    def _recent_snippet(self) -> str:
        cutoff = time.time() - TRANSCRIPT_SNIPPET_SECS
        recent = [t for ts, t in self._transcript if ts >= cutoff]
        return " ".join(recent)[-200:]  # cap so details stays bounded

    def _check_voice_count(self) -> None:
        if len(self._mfcc_buf) < 2 * VOICE_COUNT_MIN_CLUSTER_SIZE:
            return
        n_voices, score = _twocluster_silhouette([v for _, v in self._mfcc_buf])
        if n_voices >= 2:
            now = time.time()
            if now - self._last_voice_count_at < VOICE_COUNT_COOLDOWN_SECS:
                return
            self._last_voice_count_at = now
            details = (f"2 distinct voices in last {int(MFCC_BUFFER_SECS)}s "
                       f"(separation: {score:.2f})")
            try:
                self._log_event("multiple_voices_detected", "high", details)
                self._save_evidence("multiple_voices_detected")
            except Exception as e:
                logger.warning("[audio_processor] log/evidence for voice count failed: %s", e)


__all__ = [
    "AudioProcessor", "AudioRingBuffer",
    "DEFAULT_EN", "DEFAULT_HI",
    "_load_keywords_for_lang", "_normalise_text",
    "_twocluster_silhouette",
    "SAMPLE_RATE",
]

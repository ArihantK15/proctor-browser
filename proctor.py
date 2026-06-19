"""
proctor.py — Procta local proctoring daemon (Phase 2.5: mediapipe-free)

Spawned by the Electron main process for the duration of an active exam.
Watches the student's webcam + microphone and POSTs violation events back
to the FastAPI backend, which surfaces them to the teacher dashboard.

This file replaces an earlier mediapipe-based implementation. mediapipe
proved to be a chronic install / runtime failure point on Python 3.12 +
Apple Silicon (mutex lock crashes at import, protobuf version drift,
TensorFlow transitive deps, etc.) — see the project history for the
incident chain. The pivot keeps every behavioural feature of the previous
proctor and only swaps the face/landmark/gaze backend.

Detection stack
───────────────
  Face detection + 5-point landmarks  →  uniface RetinaFace (ONNX)
  Gaze direction (yaw, pitch radians) →  ResNet18 ONNX gaze model
                                          (yakhyo/gaze-estimation weights)
  Head pose (yaw, pitch degrees)      →  cv2.solvePnP from RetinaFace lms
  Eye open/closed                     →  OpenCV Haar cascade (built-in)
  Cheat objects (phone, book, …)      →  Ultralytics YOLOv8 (unchanged)
  Wrong-person identity check         →  InsightFace embeddings (unchanged)
  Voice / sustained-audio detection   →  sounddevice RMS (unchanged)

All counters, frame-thresholds, cooldowns, server-event names, screenshot
evidence paths, heartbeat behaviour, and JWT auth are preserved bit-for-bit
so the teacher dashboard's expectations are unchanged.
"""

import os
import sys
import time
import base64
import platform
import signal
import ssl
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import requests

# ── Windows-safe console encoding ─────────────────────────────────────────────
# When Electron spawns us, stdout/stderr are pipes; on Windows Python defaults
# those pipes to the ANSI code page (cp1252), which CANNOT encode the ✅/❌/🎯
# status glyphs used throughout this file. A bare print() of one then raises
# UnicodeEncodeError — and when that fires inside an optional-detector except
# handler (e.g. "face detection disabled"), it turns a graceful degrade into a
# hard daemon crash (Exited 1), stranding the student on calibration. Force
# UTF-8 with replacement so no status line can ever crash the proctor. Must run
# before the first print() and before the heavy imports below. errors="replace"
# is the belt-and-suspenders: even an exotic glyph degrades to "?" not a raise.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Make our OWN directory importable before pulling in sibling modules
# (behavioral_analysis, audio_processor). The bundled Windows interpreter is
# the python.org *embeddable* build: its ._pth puts the runtime in isolated
# path mode, so the script's directory is NOT auto-added to sys.path and a
# bare `from behavioral_analysis import …` raises ModuleNotFoundError. (The
# old system-Python fallback hid this because system Python adds cwd.) Insert
# the resolved file dir explicitly so siblings import regardless of the
# interpreter flavour, the working directory, or PYTHONPATH (which ._pth mode
# ignores). Harmless on system Python where the dir is already on the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Soft-import psutil so older bundled clients that don't ship it can
# still run — the thermal/CPU governor below silently no-ops when
# psutil is unavailable. Fresh installs from requirements-proctor.txt
# get it.
try:
    import psutil as _psutil
    _PSUTIL_OK = True
except ImportError:
    _psutil = None
    _PSUTIL_OK = False
import cv2
import numpy as np
from collections import deque
from datetime import datetime, timezone
from queue import Queue, Empty, Full
from typing import Optional, Tuple

# ─── BEHAVIORAL ANALYSIS (multi-signal correlation) ────────────────────────────
from behavioral_analysis import BehavioralEngine
_behavioral = BehavioralEngine(check_interval=15)

# ─── PRE-VIOLATION CONTEXT BUFFER ("dashcam") ──────────────────────────────────
# RAM-only 1 Hz ring of recent camera frames. On a serious (appeal-critical)
# violation we attach the 3 frames captured just BEFORE the flag so the teacher
# can see the lead-up (dropped pen vs. phone), not a single out-of-context shot.
# Never touches disk; see frame_buffer.py for the DPDP data-minimisation rationale.
from frame_buffer import FrameRingBuffer
_frame_ring = FrameRingBuffer()

# Events serious enough that a student is likely to appeal — these carry the
# pre-violation context buffer. The common low/medium gaze noise
# (gaze_away/head_turned/eyes_closed/face_too_small/reference_frame/
# calibration_abort) deliberately stays single-frame to keep storage/bandwidth
# down. Matched by prefix so escalated/suffixed variants (earbud_left,
# phone_in_hand, …) are covered.
_CONTEXT_EVENT_PREFIXES = (
    "phone", "multiple_faces", "multiple_voices", "wrong_person", "face_missing",
    "sustained_voice", "conversation", "earbud", "screen_share", "cheat_object",
    "keyword_uttered", "phone_consulting", "collaboration", "answer_memo", "note_reading",
)

def _wants_context(label: str) -> bool:
    return any((label or "").startswith(p) for p in _CONTEXT_EVENT_PREFIXES)

# ─── OPTIONAL DETECTORS ───────────────────────────────────────────────────────
# Each heavy dep is wrapped in a try/except so a missing model file or
# broken install can never crash proctor.py — it degrades to whatever
# detectors are still available.

# Records the *error class name* for any model that fails to load, keyed by a
# stable model name. Populated by the import-time loaders below and surfaced
# as privacy-safe `model_load_failed` diagnostics (in main()) and by
# --selftest. METADATA ONLY — error class names + flags, never frames, audio,
# or identity. This is what makes on-device boot failures observable
# server-side without ever shipping media off the student's machine.
_MODEL_ERRORS: dict = {}

# uniface downloads its RetinaFace model to ~/.uniface/models on FIRST use.
# That network fetch FAILS offline / on flaky connections (e.g. a student on
# slow Wi-Fi), leaving face detection dead — calibration then sits forever on
# "No face detected". Every other model (gaze, YOLO) is bundled in weights/ for
# offline-first; RetinaFace was the one exception. Ship retinaface_mnet_v2.onnx
# in weights/ too and pre-seed uniface's cache from it so RetinaFace() loads
# with ZERO network. uniface SHA-256-verifies the file against the official
# hash, so seeding a byte-identical copy is safe (a mismatch self-heals: uniface
# deletes it and re-downloads).
def _seed_retina_model():
    try:
        import shutil
        from uniface.constants import RetinaFaceWeights
        fname = RetinaFaceWeights.MNET_V2.value + ".onnx"   # retinaface_mnet_v2.onnx
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(os.environ.get("PROCTA_WEIGHTS_DIR", ""), fname),
            os.path.join(here, "weights", fname),
            os.path.join(os.environ.get("ELECTRON_RESOURCES_PATH", ""), "weights", fname),
        ]
        bundled = next((p for p in candidates if p and os.path.exists(p)), None)
        if not bundled:
            return
        cache_dir = os.path.expanduser("~/.uniface/models")
        os.makedirs(cache_dir, exist_ok=True)
        dest = os.path.join(cache_dir, fname)
        if not os.path.exists(dest):
            shutil.copy2(bundled, dest)
            print(f"[Retina] seeded bundled model -> {dest}")
    except Exception as _se:
        print(f"[Retina] model seed skipped: {_se}")


# uniface: face detection + 5 landmarks (ONNX RetinaFace under the hood)
try:
    _seed_retina_model()
    from uniface import RetinaFace
    _retina = RetinaFace()
    RETINA_AVAILABLE = True
    print("[Retina] ✅ Ready")
except Exception as _re:
    print(f"[Retina] ❌ Not available: {_re} — face detection disabled")
    RETINA_AVAILABLE = False
    _retina = None
    _MODEL_ERRORS["retina"] = type(_re).__name__

# onnxruntime: gaze direction model. Loaded lazily by GazeEstimator below.
try:
    import onnxruntime as ort
    ORT_AVAILABLE = True
except Exception as _oe:
    print(f"[ONNX] ❌ Not available: {_oe} — gaze direction disabled")
    ORT_AVAILABLE = False
    _MODEL_ERRORS["onnxruntime"] = type(_oe).__name__

# YOLO cheat-object detection — runs on onnxruntime (NOT torch).
# We ship a pre-exported ONNX and load it with the same ORT session pattern
# as the gaze model, so the whole proctor depends on a single runtime. The
# model is loaded lazily to avoid blocking startup and to keep the resident
# footprint low until the first object check.
#
# Preferred model is weights/yolo26n.onnx (YOLO26: NMS-free / end-to-end,
# ~43% faster CPU inference than YOLO11n); we fall back to the legacy
# weights/yolov8n.onnx if the YOLO26 file isn't present (see _find_yolo_model).
# Both use a STATIC 640x640 input ([1,3,640,640]). They differ ONLY in the
# output head, which _yolo_infer auto-detects:
#   * YOLO26 end-to-end -> [1, N, 6] rows [x1,y1,x2,y2,conf,cls], deduped.
#   * Legacy YOLOv8     -> [1, 84, 8400] (4 box + 80 COCO class scores per
#                          anchor; no objectness) — needs argmax + NMS.
# Re-export YOLO26 with (end-to-end is the default):
#   yolo export model=yolo26n.pt format=onnx imgsz=640 opset=19
_yolo_session = None
_yolo_input_name: Optional[str] = None
_YOLO_INPUT_SIZE = 640           # square letterbox target the .onnx expects
YOLO_AVAILABLE = False
_YOLO_LOCK = threading.Lock()


def _find_yolo_model() -> Optional[str]:
    """Resolve the bundled YOLO ONNX. Prefers weights/yolo26n.onnx and falls
    back to the legacy weights/yolov8n.onnx, so dropping the YOLO26 export
    into weights/ switches the proctor over with no other change (and removing
    it reverts cleanly). Mirrors _find_gaze_model so the Electron bundle
    (weights/ shipped via extraResources) and a dev checkout both work, with a
    PROCTOR_YOLO_MODEL override on top. The decode in _yolo_infer auto-detects
    which head the chosen file emits."""
    here = os.path.dirname(os.path.abspath(__file__))
    res = os.environ.get("ELECTRON_RESOURCES_PATH", "")
    candidates = [
        os.environ.get("PROCTOR_YOLO_MODEL", ""),
        os.path.join(here, "weights", "yolo26n.onnx"),
        os.path.join(res,  "weights", "yolo26n.onnx"),
        os.path.join(here, "weights", "yolov8n.onnx"),
        os.path.join(res,  "weights", "yolov8n.onnx"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            # Loud signal when we fall back to the legacy model despite
            # preferring YOLO26 — otherwise "thought we swapped but didn't" is
            # invisible (the chosen path is the only clue, and it looks normal).
            # Skip when PROCTOR_YOLO_MODEL was set (an explicit, deliberate choice).
            if p.endswith("yolov8n.onnx") and not os.environ.get("PROCTOR_YOLO_MODEL"):
                print("[YOLO] ⚠ yolo26n.onnx not found in weights/ — running "
                      "LEGACY yolov8n. Run scripts/export_yolo26.py to produce "
                      "weights/yolo26n.onnx and activate the YOLO26 swap.")
            return p
    return None


def _yolo_providers():
    """Best-effort accelerated providers, intersected with what this ORT
    build actually ships, so a missing CoreML/DirectML/CUDA provider can
    never fail session init. CPU is always the guaranteed fallback."""
    preferred = ["CUDAExecutionProvider", "CoreMLExecutionProvider",
                 "DmlExecutionProvider", "CPUExecutionProvider"]
    try:
        available = set(ort.get_available_providers())
    except Exception:
        available = {"CPUExecutionProvider"}
    chosen = [p for p in preferred if p in available]
    if "CPUExecutionProvider" not in chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


def _load_yolo():
    """Load the YOLOv8n ONNX session on demand. Thread-safe; returns the
    ort.InferenceSession (or None if unavailable). Callers treat the
    return value as an opaque handle and pass it to _yolo_infer()."""
    global _yolo_session, _yolo_input_name, YOLO_AVAILABLE
    with _YOLO_LOCK:
        if _yolo_session is not None:
            return _yolo_session
        if not ORT_AVAILABLE:
            print("[YOLO] Not available: onnxruntime not installed")
            YOLO_AVAILABLE = False
            return None
        model_path = _find_yolo_model()
        if not model_path:
            print("[YOLO] Not available: weights/yolo26n.onnx (or legacy "
                  "weights/yolov8n.onnx) not found")
            YOLO_AVAILABLE = False
            _MODEL_ERRORS["yolo"] = "weights_missing"
            return None
        try:
            providers = _yolo_providers()
            print(f"[YOLO] Loading {model_path} (providers={providers})...")
            sess = ort.InferenceSession(model_path, providers=providers)
            _yolo_input_name = sess.get_inputs()[0].name
            _yolo_session = sess
            YOLO_AVAILABLE = True
            print(f"[YOLO] Ready (active provider: {sess.get_providers()[0]})")
            return _yolo_session
        except Exception as _ye:
            print(f"[YOLO] Not available: {_ye}")
            YOLO_AVAILABLE = False
            _MODEL_ERRORS["yolo"] = type(_ye).__name__
            return None


def _yolo_infer(session, bgr_img):
    """Run the bundled YOLO model on a BGR image and return detections as a
    list of (cls_id, conf, x1, y1, x2, y2) tuples in **bgr_img's own pixel
    coordinates**. Centralises the letterbox + decode math so the two
    workers share one well-tested path. cls_id is the raw COCO index;
    callers apply the CHEAT_IDS filter themselves.

    Two output heads are supported and auto-detected from the output shape:

      * YOLO26 end-to-end / NMS-free  -> output [1, N, 6] (last dim == 6),
        each row already deduplicated as [x1, y1, x2, y2, conf, cls] in the
        letterboxed input space. No NMS is applied.
      * Legacy YOLOv8                 -> output [1, 84, 8400] (4 box coords +
        80 class scores per anchor, no objectness). Needs argmax + per-class
        NMS (conf=YOLO_CONFIDENCE 0.35, IoU=0.7, agnostic=False).

    Auto-detection keeps us correct whether weights/yolo26n.onnx or the
    legacy weights/yolov8n.onnx is shipped, and is immune to YOLO26 exports
    that fall back to the legacy head (ultralytics issue #23397)."""
    h0, w0 = bgr_img.shape[:2]
    if h0 == 0 or w0 == 0:
        return []
    size = _YOLO_INPUT_SIZE

    # 1. Letterbox to size x size, centre-padded with 114 (ultralytics const).
    scale = min(size / w0, size / h0)
    new_w, new_h = int(round(w0 * scale)), int(round(h0 * scale))
    resized = cv2.resize(bgr_img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x = (size - new_w) / 2.0
    pad_y = (size - new_h) / 2.0
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    top, left = int(round(pad_y - 0.1)), int(round(pad_x - 0.1))
    canvas[top:top + new_h, left:left + new_w] = resized

    # 2. BGR->RGB, /255, HWC->CHW, batch.
    blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    blob = np.expand_dims(np.transpose(blob, (2, 0, 1)), 0)

    # 3. Inference.
    name = _yolo_input_name or session.get_inputs()[0].name
    out = session.run(None, {name: blob})[0]   # [1,84,8400] or [1,N,6]

    # 3b. End-to-end / NMS-free head (YOLO26): [1, N, 6] rows of
    #     [x1, y1, x2, y2, conf, cls] already in the letterboxed space and
    #     already deduplicated. Threshold + un-letterbox, no NMS.
    if out.ndim == 3 and out.shape[2] == 6:
        dets = out[0]                                  # [N, 6]
        if dets.shape[0] == 0:
            return []
        confs_e = dets[:, 4]
        keep_e = confs_e >= YOLO_CONFIDENCE
        if not np.any(keep_e):
            return []
        dets = dets[keep_e]
        ex1 = np.clip((dets[:, 0] - left) / scale, 0, w0)
        ey1 = np.clip((dets[:, 1] - top) / scale, 0, h0)
        ex2 = np.clip((dets[:, 2] - left) / scale, 0, w0)
        ey2 = np.clip((dets[:, 3] - top) / scale, 0, h0)
        return [
            (int(dets[i, 5]), float(dets[i, 4]),
             int(ex1[i]), int(ey1[i]), int(ex2[i]), int(ey2[i]))
            for i in range(dets.shape[0])
        ]

    # 4. Legacy decode: -> [8400, 84]; first 4 = cx,cy,w,h, rest = 80 class scores.
    preds = np.squeeze(out, 0).T
    if preds.shape[0] == 0:
        return []
    class_scores = preds[:, 4:]
    confs = class_scores.max(axis=1)
    keep = confs >= YOLO_CONFIDENCE
    if not np.any(keep):
        return []
    preds = preds[keep]
    confs = confs[keep]
    cls_ids = class_scores[keep].argmax(axis=1)

    # 5. xywh (letterboxed space) -> xyxy, then undo the letterbox back to
    #    the original image, and clip.
    cx, cy, bw, bh = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
    x1 = (cx - bw / 2.0 - left) / scale
    y1 = (cy - bh / 2.0 - top) / scale
    x2 = (cx + bw / 2.0 - left) / scale
    y2 = (cy + bh / 2.0 - top) / scale
    x1 = np.clip(x1, 0, w0); y1 = np.clip(y1, 0, h0)
    x2 = np.clip(x2, 0, w0); y2 = np.clip(y2, 0, h0)

    # 6. Per-class NMS (matches ultralytics agnostic=False default).
    detections = []
    for c in np.unique(cls_ids):
        m = cls_ids == c
        boxes_xywh = [[float(x1[i]), float(y1[i]),
                       float(x2[i] - x1[i]), float(y2[i] - y1[i])]
                      for i in np.nonzero(m)[0]]
        scores_c = [float(confs[i]) for i in np.nonzero(m)[0]]
        idxs = cv2.dnn.NMSBoxes(boxes_xywh, scores_c,
                                float(YOLO_CONFIDENCE), 0.7)
        if len(idxs) == 0:
            continue
        src = np.nonzero(m)[0]
        for j in np.array(idxs).flatten():
            i = src[j]
            detections.append((int(c), float(confs[i]),
                               int(x1[i]), int(y1[i]),
                               int(x2[i]), int(y2[i])))
    return detections


class YoloWorker:
    """Background thread that runs YOLO inference off the main capture loop.

    The main loop puts (frame, conf, frame_count) tuples into ``frame_q``.
    The worker runs inference and puts results into ``result_q`` as dicts:
        {"frame_count": N, "detections": [(class_name, conf), ...], "error": None}
    or  {"frame_count": N, "detections": [], "error": "message"}

    If the result queue is full or the worker is slow the main loop never
    blocks — old results are simply dropped.
    """

    def __init__(self):
        self.frame_q = Queue(maxsize=2)
        self.result_q = Queue(maxsize=2)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._submit_errs = 0

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="yolo-worker")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def submit(self, frame: np.ndarray, frame_count: int, W: int, H: int):
        """Queue a frame for YOLO inference (non-blocking).

        Downscale ASPECT-PRESERVING (long side -> the model input). The old
        cv2.resize(frame, (416, 416)) square-stretched non-square webcam
        frames (4:3 / 16:9), distorting objects before YOLO ever saw them and
        hurting detection (phones especially). _yolo_infer letterboxes to 640
        itself and returns boxes in the SUBMITTED image's coords, which _run
        scales back to W×H — so any uniform-aspect scale here is correct.
        The resize is also a snapshot, so the main loop's later HUD drawing on
        `frame` can't race into the worker.
        """
        try:
            fh, fw = frame.shape[:2]
            s = min(1.0, float(_YOLO_INPUT_SIZE) / max(fw, fh))
            small = (cv2.resize(frame, (max(1, round(fw * s)), max(1, round(fh * s))))
                     if s < 1.0 else frame.copy())
            self.frame_q.put_nowait((small, frame_count, W, H))
        except Full:
            pass  # worker busy — drop this frame (expected backpressure)
        except Exception as _se:
            # A NON-Full error (e.g. a malformed frame hitting cv2.resize) was
            # silently swallowed here before, hiding real per-frame faults.
            # Rate-limit so a persistent fault is visible without flooding.
            self._submit_errs += 1
            if self._submit_errs <= 3 or self._submit_errs % 100 == 0:
                print(f"[YOLO] submit error #{self._submit_errs}: "
                      f"{type(_se).__name__}: {_se}")

    def get_result(self, frame_count: int):
        # Async inference lags the capture loop by 2-5 frames (~100-300ms), so a
        # ready result is ALWAYS tagged with an earlier frame_count than the one
        # being processed now. The old `== frame_count` check therefore discarded
        # EVERY result — cheat-object detections (phones, etc.) never reached the
        # dashboard. result_q is drained each frame so what's here is the most
        # recent completed inference; a few frames of lag is immaterial for
        # "is there a phone in view". (frame_count kept for API.)
        try:
            return self.result_q.get_nowait()
        except Empty:
            return None

    def _run(self):
        session = _load_yolo()
        if session is None:
            return

        while not self._stop.is_set():
            try:
                small, frame_count, W, H = self.frame_q.get(timeout=0.5)
            except Empty:
                continue

            try:
                detections = []
                h, w = small.shape[:2]
                for cls_id, conf, x1, y1, x2, y2 in _yolo_infer(session, small):
                    if cls_id in CHEAT_IDS:
                        detections.append((
                            CHEAT_IDS[cls_id],
                            conf,
                            int(x1 * W / w), int(y1 * H / h),
                            int(x2 * W / w), int(y2 * H / h),
                        ))
                self.result_q.put_nowait({
                    "frame_count": frame_count,
                    "detections": detections,
                    "error": None,
                })
            except Exception as e:
                try:
                    self.result_q.put_nowait({
                        "frame_count": frame_count,
                        "detections": [],
                        "error": str(e),
                    })
                except Exception:
                    pass  # result queue full, discard


# Global YOLO worker — created at module load but only starts when
# the proctoring loop begins.
yolo_worker = YoloWorker()

# ─── SAHI TILING for YOLO (small object detection) ───────────────────────────
# Slicing Aided Hyper Inference: splits the frame into overlapping tiles,
# runs YOLO on each tile at full resolution, then merges detections.
# It improves recall for SMALL/DISTANT instances of the COCO classes YOLO
# already knows (phone, laptop, book, keyboard, TV). It does NOT add classes,
# so it cannot detect earbuds/headphones — those aren't COCO classes (the
# ear-crop classifier handles earbuds). OFF by default (PROCTOR_ENABLE_SAHI);
# see _sahi_available. Runs on a separate background thread.
# SAHI_EVERY_N is defined after YOLO_EVERY_N (line ~634) to avoid
# forward-reference errors at module load time.

class SahiYoloWorker:
    """Background thread that runs SAHI-tiled YOLO inference.

    Splits the frame into overlapping tiles (default 320x320, 20% overlap),
    runs YOLO on each tile, and merges results with simple NMS.
    """

    TILE_SIZE = 320
    OVERLAP = 0.2

    def __init__(self):
        self.frame_q = Queue(maxsize=1)
        self.result_q = Queue(maxsize=1)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="sahi-worker")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def submit(self, frame: np.ndarray, frame_count: int):
        try:
            self.frame_q.put_nowait((frame.copy(), frame_count))
        except Full:
            pass  # worker busy — drop this frame (expected backpressure)
        except Exception as _se:
            print(f"[SAHI] submit error: {type(_se).__name__}: {_se}")

    def get_result(self, frame_count: int):
        # Same async-lag fix as YoloWorker.get_result: never require an exact
        # frame_count match or every SAHI result is discarded. Return the most
        # recent completed inference (maxsize=1 queue, drained each frame).
        try:
            return self.result_q.get_nowait()
        except Empty:
            return None

    @staticmethod
    def _generate_tiles(frame: np.ndarray):
        h, w = frame.shape[:2]
        step = int(SahiYoloWorker.TILE_SIZE * (1 - SahiYoloWorker.OVERLAP))
        for y in range(0, max(h - SahiYoloWorker.TILE_SIZE + 1, 1), step):
            for x in range(0, max(w - SahiYoloWorker.TILE_SIZE + 1, 1), step):
                y_end = min(y + SahiYoloWorker.TILE_SIZE, h)
                x_end = min(x + SahiYoloWorker.TILE_SIZE, w)
                if y_end - y < 50 or x_end - x < 50:
                    continue
                yield frame[y:y_end, x:x_end], x, y

    @staticmethod
    def _nms_merge(detections: list, iou_thresh: float = 0.5):
        if not detections:
            return []
        by_name = {}
        for name, conf, x1, y1, x2, y2 in detections:
            by_name.setdefault(name, []).append((conf, x1, y1, x2, y2))
        merged = []
        for name, boxes in by_name.items():
            boxes.sort(reverse=True)
            kept = []
            for conf, x1, y1, x2, y2 in boxes:
                overlap = False
                for kc, kx1, ky1, kx2, ky2 in kept:
                    ix1 = max(x1, kx1); iy1 = max(y1, ky1)
                    ix2 = min(x2, kx2); iy2 = min(y2, ky2)
                    if ix1 < ix2 and iy1 < iy2:
                        inter = (ix2 - ix1) * (iy2 - iy1)
                        union = (x2-x1)*(y2-y1) + (kx2-kx1)*(ky2-ky1) - inter
                        if union > 0 and inter / union > iou_thresh:
                            overlap = True
                            break
                if not overlap:
                    kept.append((conf, x1, y1, x2, y2))
            merged.extend([(name, conf, x1, y1, x2, y2) for conf, x1, y1, x2, y2 in kept])
        return merged

    def _run(self):
        session = _load_yolo()
        if session is None:
            return

        while not self._stop.is_set():
            try:
                frame, frame_count = self.frame_q.get(timeout=0.5)
            except Empty:
                continue

            all_dets = []
            try:
                for tile, ox, oy in self._generate_tiles(frame):
                    for cls_id, conf, x1, y1, x2, y2 in _yolo_infer(session, tile):
                        if cls_id in CHEAT_IDS:
                            all_dets.append((CHEAT_IDS[cls_id], conf,
                                             x1 + ox, y1 + oy,
                                             x2 + ox, y2 + oy))
                merged = self._nms_merge(all_dets)
                self.result_q.put_nowait({
                    "frame_count": frame_count,
                    "detections": merged,
                    "error": None,
                })
            except Exception as e:
                try:
                    self.result_q.put_nowait({
                        "frame_count": frame_count,
                        "detections": [],
                        "error": str(e),
                    })
                except Exception:
                    pass

sahi_worker = SahiYoloWorker()
def _sahi_available() -> bool:
    """SAHI is usable only when YOLO is loaded AND it's explicitly enabled
    (PROCTOR_ENABLE_SAHI). Default off: on a 640x480 laptop webcam its
    small-object benefit is marginal and tiled inference adds CPU. (The room
    camera is where it would pay off, but that's a separate, privacy-gated
    design — not the laptop.)"""
    return YOLO_AVAILABLE and SAHI_ENABLED

# ─── EAR-CROP CLASSIFIER (earphone/earbud detection) ──────────────────────────
# Uses face landmarks from RetinaFace to crop the ear regions, then runs
# a lightweight classifier to detect earbuds. Runs every 5th frame to
# balance accuracy with CPU overhead.

def _find_ear_model() -> Optional[str]:
    candidates = [
        os.environ.get("PROCTOR_EAR_MODEL", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "weights", "earbud_classifier.onnx"),
        os.path.join(os.environ.get("ELECTRON_RESOURCES_PATH", ""),
                     "weights", "earbud_classifier.onnx"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

_ear_classifier = None
EAR_CLASSIFIER_AVAILABLE = False

if ORT_AVAILABLE:
    class EarClassifier:
        def __init__(self):
            self.session = None
            self.model_size = None
            self.input_name = None
            self.output_name = None
            _model_path = _find_ear_model()
            if _model_path:
                try:
                    self.session = ort.InferenceSession(
                        _model_path, providers=["CPUExecutionProvider"])
                    self.input_name = self.session.get_inputs()[0].name
                    input_shape = self.session.get_inputs()[0].shape
                    self.model_size = tuple(input_shape[2:][::-1])
                    self.output_name = self.session.get_outputs()[0].name
                    print(f"[EarClassifier] ✅ Loaded from {_model_path}")
                except Exception as _ee:
                    print(f"[EarClassifier] ⚠ Model load failed: {_ee}")
                    _MODEL_ERRORS["ear"] = type(_ee).__name__
            else:
                print("[EarClassifier] ⚠ No ear model found — heuristic fallback enabled")

        @staticmethod
        def _estimate_ear_bbox(lm_2d: np.ndarray, W: int, H: int, side: str):
            left_eye = lm_2d[0]
            right_eye = lm_2d[1]
            left_mouth = lm_2d[3]
            right_mouth = lm_2d[4]
            eye_dist = np.linalg.norm(right_eye - left_eye)
            if side == "left":
                cx = left_eye[0] - eye_dist * 0.6
                cy = (left_eye[1] + left_mouth[1]) / 2
            else:
                cx = right_eye[0] + eye_dist * 0.6
                cy = (right_eye[1] + right_mouth[1]) / 2
            half = int(eye_dist * 0.9)
            x1 = max(0, int(cx - half))
            y1 = max(0, int(cy - half * 1.2))
            x2 = min(W, int(cx + half))
            y2 = min(H, int(cy + half * 0.8))
            if x2 - x1 < 20 or y2 - y1 < 20:
                return None
            return (x1, y1, x2, y2)

        def classify(self, frame: np.ndarray, lm_2d: np.ndarray, W: int, H: int):
            if self.session is None:
                return self._heuristic_detect(frame, lm_2d, W, H)
            left_conf, right_conf = 0.0, 0.0
            for side in ["left", "right"]:
                bbox = self._estimate_ear_bbox(lm_2d, W, H, side)
                if bbox is None:
                    continue
                x1, y1, x2, y2 = bbox
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    img = cv2.resize(crop, self.model_size)
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    img = img.astype(np.float32) / 255.0
                    img = np.transpose(np.expand_dims(img, 0), (0, 3, 1, 2))
                    outputs = self.session.run([self.output_name], {self.input_name: img})
                    prob = float(outputs[0][0][1]) if outputs[0].shape[1] > 1 else 0.0
                    if side == "left":
                        left_conf = prob
                    else:
                        right_conf = prob
                except Exception:
                    pass
            return left_conf, right_conf

        @staticmethod
        def _heuristic_detect(frame: np.ndarray, lm_2d: np.ndarray, W: int, H: int):
            left_conf, right_conf = 0.0, 0.0
            for side_idx, side in enumerate(["left", "right"]):
                bbox = EarClassifier._estimate_ear_bbox(lm_2d, W, H, side)
                if bbox is None:
                    continue
                x1, y1, x2, y2 = bbox
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    edges = cv2.Canny(gray, 50, 150)
                    edge_density = np.sum(edges > 0) / edges.size
                    mask = gray < 80
                    dark_ratio = np.sum(mask) / mask.size
                    if 0.05 < edge_density < 0.35 and 0.02 < dark_ratio < 0.3:
                        conf = min(0.9, edge_density * 3.0 + dark_ratio * 2.0)
                        if side_idx == 0:
                            left_conf = conf
                        else:
                            right_conf = conf
                except Exception:
                    pass
            return left_conf, right_conf

    _ear_classifier = EarClassifier()
    EAR_CLASSIFIER_AVAILABLE = True
else:
    print("[EarClassifier] ❌ onnxruntime not available — earbud detection disabled")

# InsightFace: face-embedding wrong-person detection
try:
    from insightface.app import FaceAnalysis as _FaceAnalysis
    _insight_app = _FaceAnalysis(
        name='buffalo_sc',
        providers=['CPUExecutionProvider'],
    )
    _insight_app.prepare(ctx_id=-1, det_size=(320, 320))
    INSIGHT_AVAILABLE = True
    print("[InsightFace] ✅ Ready")
except Exception as _ie:
    print(f"[InsightFace] ❌ Not available: {_ie} — wrong-person detection disabled")
    INSIGHT_AVAILABLE = False
    _insight_app = None
    _MODEL_ERRORS["insightface"] = type(_ie).__name__

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SESSION_ID   = os.getenv("PROCTOR_SESSION_ID",  "test-session")
EVIDENCE_DIR = os.getenv("PROCTOR_EVIDENCE_DIR", os.path.join(tempfile.gettempdir(), "procta_evidence"))
JWT_TOKEN    = os.getenv("PROCTOR_JWT_TOKEN",   "")
if not JWT_TOKEN:
    print("[PROCTOR] ⚠ No JWT token — server requests will be unauthenticated")

# ── SERVER_URL normalisation ───────────────────────────────────────
# PROCTOR_SERVER_URL has had three historical shapes. We accept all
# three and normalise to a single, correct event POST target.
#
#   (1) "https://app.procta.net"               base only (preferred)
#   (2) "https://app.procta.net/event"         legacy — pre-/api/v1
#                                              prefix, what every
#                                              desktop client shipped
#                                              before v2.3.1 sets
#   (3) "https://app.procta.net/api/v1/event"  canonical
#
# All three resolve to SERVER_BASE = "https://app.procta.net" and
# SERVER_URL = "https://app.procta.net/api/v1/event". This unblocks
# every already-installed desktop client without needing a rebuild,
# AND fixes a long-standing bug in the previous code:
#
#   SERVER_BASE = SERVER_URL.rstrip("/event").rstrip("/")
#
# `str.rstrip` strips any of the characters in its argument, not the
# literal suffix. So for SERVER_URL = "https://app.procta.net/event"
# it stripped characters from the set {'/', 'e', 'v', 'n', 't'} and
# produced "https://app.procta." — eating into ".net". Every URL
# derived from SERVER_BASE (heartbeat, system-check) then targeted a
# nonexistent host and the requests silently DNS-failed inside the
# try/except wrappers. urlsplit gives us the right base every time.
from urllib.parse import urlsplit as _urlsplit_init, urlunsplit as _urlunsplit_init
_raw_server_url = os.getenv("PROCTOR_SERVER_URL", "http://localhost:8000/api/v1/event")
_parsed_server = _urlsplit_init(_raw_server_url)
SERVER_BASE = _urlunsplit_init((_parsed_server.scheme, _parsed_server.netloc, "", "", ""))
SERVER_URL  = f"{SERVER_BASE}/api/v1/event"
EVIDENCE_UPLOAD_URL = f"{SERVER_BASE}/api/v1/analyze-frame"
HEADLESS          = platform.system() == "Windows" or \
                    os.environ.get("PROCTOR_HEADLESS","0") == "1"
SKIP_ENROLLMENT   = os.environ.get("PROCTOR_SKIP_ENROLLMENT","0") == "1"
# SAHI tiled detection: OFF by default, its own flag. It was previously coupled
# to `not SKIP_ENROLLMENT`, which (a) silently disabled it in ALL of production
# — the launcher always sets SKIP_ENROLLMENT=1 — and (b) wrongly tied object
# detection to face enrollment. Decoupled so it's controllable on its own.
SAHI_ENABLED      = os.environ.get("PROCTOR_ENABLE_SAHI","0") == "1"
CALIBRATION_MODE  = os.environ.get("PROCTOR_CALIBRATION_MODE","0") == "1"

# Pre-set biases from renderer dot-calibration (skip self-calibration if present).
# Only read at module level — use local copies inside run_proctoring().
_INITIAL_GAZE_YAW_BIAS  = os.environ.get("PROCTOR_GAZE_YAW_BIAS")
_INITIAL_GAZE_PITCH_BIAS = os.environ.get("PROCTOR_GAZE_PITCH_BIAS")
_INITIAL_HEAD_YAW_BIAS  = os.environ.get("PROCTOR_HEAD_YAW_BIAS")
_INITIAL_HEAD_PITCH_BIAS = os.environ.get("PROCTOR_HEAD_PITCH_BIAS")

os.makedirs(EVIDENCE_DIR, exist_ok=True)

# Where to find the gaze model. Looked up in this order:
#   1. PROCTOR_GAZE_MODEL env var (override for packaged builds)
#   2. ./weights/resnet18_gaze.onnx (alongside this script in dev)
#   3. process.resourcesPath/weights/resnet18_gaze.onnx (electron-builder)
def _find_gaze_model() -> Optional[str]:
    candidates = [
        os.environ.get("PROCTOR_GAZE_MODEL", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "weights", "resnet18_gaze.onnx"),
        os.path.join(os.environ.get("ELECTRON_RESOURCES_PATH", ""),
                     "weights", "resnet18_gaze.onnx"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

# ─── CONFIDENCE SCORES ────────────────────────────────────────────────────────
# Reported alongside each violation in the `details` field. The teacher
# dashboard does not gate on these — they're informational only.
CONFIDENCE = {
    "face_missing":          0.95,
    "multiple_faces":        0.92,
    "gaze_away":             0.82,
    "head_turned":           0.85,
    "eyes_closed":           0.88,
    "cheat_object_detected": 0.85,
    "voice_detected":        0.75,
    "earphone_detected":     0.72,
    "face_too_small":        0.80,
    "cheat_phone_in_hand":   0.90,
    "cheat_phone_on_desk":   0.85,
    "cheat_phone_detected":  0.88,
    "sustained_voice":       0.88,
    "conversation_detected": 0.92,
    "virtual_camera_detected": 0.95,
    "screen_share_feed":     0.90,
    "vm_detected":           0.85,
}

# ─── THRESHOLDS ───────────────────────────────────────────────────────────────
# Tuned for "ADHD-friendly" tolerance: a student fidgeting, glancing around,
# or shifting their head a few degrees while reading the question is NOT a
# violation. Only a sustained look-away or an extreme glance-to-edge fires.
#
# Two tiers per signal:
#   • NORMAL  → fires after a long sustain (~1s+) at moderate angles
#   • EXTREME → fires faster (~0.4s) but only for blatant edge-of-screen looks
#
# This gives genuine cheating attempts (turning head to side, looking off-
# screen) a fast catch while letting honest students breathe.
# Note: these are bias-corrected. With calibration the student's "centre"
# is 0,0 so we can be a bit stricter than the bias-free build was.
GAZE_YAW_RAD          = 0.30   # ~17° from calibrated centre (medium tier)
GAZE_PITCH_RAD        = 0.35   # ~20° from calibrated centre
GAZE_YAW_EXTREME      = 0.55   # ~31° — clearly looking off-screen (high tier)
GAZE_PITCH_EXTREME    = 0.55   # ~31°
GAZE_FRAMES_NEEDED    = 12     # ~0.8s at 15fps before medium flag
GAZE_EXTREME_FRAMES   = 5      # ~0.33s for the extreme/high tier
HEAD_YAW_THRESHOLD    = 22     # degrees from calibrated centre (medium)
HEAD_PITCH_THRESHOLD  = 28
HEAD_YAW_EXTREME      = 40     # clearly turned away from monitor (high)
HEAD_PITCH_EXTREME    = 45
PHONE_HAND_RATIO         = 0.50  # phone center above this fraction of face bottom = in-hand
PHONE_DESK_Y_RATIO       = 0.65  # phone center below this fraction of frame height = on-desk

# ─── VIOLATION SEVERITY ESCALATION ────────────────────────────────────────────
# When the same violation type repeats, severity auto-escalates:
#   1st offense → original severity
#   2nd offense (within window) → +1 tier (medium→high, high→critical)
#   3+ offenses (within window) → critical
ESCALATION_WINDOW_SECS = 300  # 5-minute window for repeat offenses
ESCALATION_TIERS = {
    "low":    "medium",
    "medium": "high",
    "high":   "critical",
    "critical": "critical",  # ceiling
}

# ─── PER-STUDENT THRESHOLD OVERRIDES (from edge-dot calibration) ─────────────
# When the renderer's dot-calibration measures how far a student's gaze/head
# actually moves to reach each screen corner, it sends the max observed
# deviation in these env vars. We scale that by MEDIUM_MARGIN / EXTREME_MARGIN
# to derive per-student thresholds. Clamped with FLOOR / CEIL so a lazy or
# noisy calibration can't make the proctor paranoid (too low) or toothless
# (too high).
#
# Intuition: if the student had to rotate their eyes by 0.28 rad to look at
# the bottom-right corner of their screen, anything > 0.28 * 1.30 = 0.36 rad
# is definitely past the edge, i.e. off-screen. Students with small laptops
# get stricter thresholds; students with 32" monitors get looser ones.
_MEDIUM_MARGIN = 1.30   # flag when 30% past the edge
_EXTREME_MARGIN = 1.90  # extreme tier at ~90% past the edge

def _tune_threshold(env_key, default, extreme_default, floor, ceil):
    """Return (medium, extreme) thresholds scaled from a calibration range."""
    raw = os.environ.get(env_key)
    if not raw:
        return default, extreme_default
    try:
        r = abs(float(raw))
    except ValueError:
        return default, extreme_default
    if r <= 0:
        return default, extreme_default
    med = max(floor, min(ceil, r * _MEDIUM_MARGIN))
    ext = max(med * 1.40, min(ceil * 1.55, r * _EXTREME_MARGIN))
    return med, ext

# Apply overrides. Floors/ceilings chosen so even weird calibrations stay
# in a sensible proctoring range.
GAZE_YAW_RAD,   GAZE_YAW_EXTREME    = _tune_threshold(
    "PROCTOR_GAZE_YAW_RANGE",   GAZE_YAW_RAD,   GAZE_YAW_EXTREME,   0.22, 0.50)
GAZE_PITCH_RAD, GAZE_PITCH_EXTREME  = _tune_threshold(
    "PROCTOR_GAZE_PITCH_RANGE", GAZE_PITCH_RAD, GAZE_PITCH_EXTREME, 0.25, 0.55)
HEAD_YAW_THRESHOLD,  HEAD_YAW_EXTREME  = _tune_threshold(
    "PROCTOR_HEAD_YAW_RANGE",   HEAD_YAW_THRESHOLD,  HEAD_YAW_EXTREME,  15, 30)
HEAD_PITCH_THRESHOLD, HEAD_PITCH_EXTREME = _tune_threshold(
    "PROCTOR_HEAD_PITCH_RANGE", HEAD_PITCH_THRESHOLD, HEAD_PITCH_EXTREME, 20, 35)

# Report whether per-student thresholds are in effect.
if os.environ.get("PROCTOR_GAZE_YAW_RANGE"):
    print(f"[PROCTOR] 🎯 Per-student thresholds active — "
          f"gaze yaw:{GAZE_YAW_RAD:.2f}/{GAZE_YAW_EXTREME:.2f}rad "
          f"pitch:{GAZE_PITCH_RAD:.2f}/{GAZE_PITCH_EXTREME:.2f}rad "
          f"head yaw:{HEAD_YAW_THRESHOLD:.0f}/{HEAD_YAW_EXTREME:.0f}° "
          f"pitch:{HEAD_PITCH_THRESHOLD:.0f}/{HEAD_PITCH_EXTREME:.0f}°")
HEAD_FRAMES_NEEDED    = 12
HEAD_EXTREME_FRAMES   = 5
FACE_MISSING_FRAMES   = 24     # ~1.6s at 15fps — survives any blip
EYES_CLOSED_FRAMES    = 20     # ~1.3s — natural blinks won't trip this
MULTI_FACE_FRAMES     = 5
WARMUP_GRACE_FRAMES   = 30     # ~1s — faster perceived camera startup
YOLO_CONFIDENCE     = 0.35
YOLO_MIN_FRAMES     = 2
YOLO_EVERY_N        = 5
SAHI_EVERY_N        = YOLO_EVERY_N * 3  # run SAHI every 3rd YOLO cycle (15 frames)
VOICE_THRESHOLD     = float(os.getenv("PROCTOR_VOICE_THRESHOLD", "0.035"))
VOICE_SUSTAINED_SECS = 8.0
SUSTAINED_VOICE_SECS = 20.0   # flag if voice continues for 20s+
CONVERSATION_BURSTS  = 4      # min bursts with short gaps to flag conversation
CONVERSATION_WINDOW  = 45.0   # seconds window to observe conversation pattern
CONVERSATION_GAP_MAX = 3.0    # max silence between bursts for "turn-taking"
WRONG_PERSON_THRESHOLD = float(os.getenv("PROCTOR_WRONG_PERSON_THRESHOLD", "0.25"))
WRONG_PERSON_CHECK_FREQ = 10    # verify identity every N frames (was 30)
TARGET_FPS          = 15
FACE_MIN_SIZE       = 50  # min face height/width px (student too far)
EAR_EVERY_N         = 5
EAR_THRESHOLD       = 0.6

# ─── ADAPTIVE HARDWARE GOVERNOR ───────────────────────────────────────────────
# Budget student laptops thermal-throttle their CPU under sustained ML
# load. Without adaptation the proctor either pegs the CPU (exam UI
# lags) or gets killed by the OOM-killer (proctoring goes dark, which
# looks like cheating). The governor samples CPU every 5 s and:
#   * CPU > THROTTLE_ENGAGE_PCT for two consecutive samples
#       -> drop effective FPS to THROTTLE_LOW_FPS (event-only mode)
#   * CPU < THROTTLE_RELEASE_PCT for two consecutive samples
#       -> ramp back to TARGET_FPS
# Transitions are logged exactly once via a `client_throttled` event
# (severity=info) so teachers can correlate "exam felt slow" with
# the student's actual hardware state.
#
# Tunable via env so a researcher running their own bench can disable
# the governor entirely (THROTTLE_ENGAGE_PCT=101) without recompiling.
THROTTLE_ENGAGE_PCT     = float(os.getenv("PROCTOR_THROTTLE_ENGAGE_PCT", "85"))
THROTTLE_RELEASE_PCT    = float(os.getenv("PROCTOR_THROTTLE_RELEASE_PCT", "60"))
THROTTLE_LOW_FPS        = float(os.getenv("PROCTOR_THROTTLE_LOW_FPS", "0.5"))   # ≈1 frame / 2 s
THROTTLE_SAMPLE_SECS    = float(os.getenv("PROCTOR_THROTTLE_SAMPLE_SECS", "5"))


class _HardwareGovernor:
    """Adaptive frame-rate governor based on CPU load + thermal stress.

    Stateful — call `.maybe_update()` once per main-loop iteration; it
    samples at most once per THROTTLE_SAMPLE_SECS interval so the cost
    is negligible. Read `.effective_fps` to get the current cap (use
    in place of TARGET_FPS in the frame-rate limiter).

    No-ops gracefully when psutil isn't bundled (returns TARGET_FPS
    forever). No-ops gracefully when the configured engage threshold
    is above 100 (disables the governor).

    Thermal reading is best-effort per platform:
      macOS  → ``pmset -g therm`` thermal pressure (0‑4)
      Windows → ``wmic`` / ``Get-CimInstance`` MSAcpi_ThermalZoneTemperature
      Fallback → CPU load only (original behaviour).
    """

    __slots__ = ("effective_fps", "_hi_streak", "_lo_streak",
                 "_last_sample_at", "_last_thermal_check",
                 "_throttled", "_on_transition", "_thermal_pressure",
                 "_thermal_executor", "_thermal_future")

    def __init__(self, on_transition=None):
        self.effective_fps = float(TARGET_FPS)
        self._hi_streak = 0
        self._lo_streak = 0
        self._last_sample_at = 0.0
        self._last_thermal_check = 0.0
        self._throttled = False
        self._thermal_pressure = None  # float 0.0-1.0 or None
        # Optional callback fired exactly once per transition. Receives
        # a dict {"throttled": bool, "cpu_pct": float, "thermal": float|None}.
        # Used by the main loop to POST a `client_throttled` event back
        # to the server.
        self._on_transition = on_transition
        self._thermal_executor = ThreadPoolExecutor(max_workers=1)
        self._thermal_future = None

    # ── Platform thermal sensing ─────────────────────────────────

    @staticmethod
    def _read_thermal_stress() -> float | None:
        """Return normalised thermal pressure 0.0‑1.0, or None on failure.

        Resolution:
          * macOS  → ``pmset -g therm`` thermal pressure (0‑4) / 4
          * Windows → ``wmic`` or PowerShell ``Get-CimInstance``
                      MSAcpi_ThermalZoneTemperature → Celsius → 0.0‑1.0
                      (0.0 ≈ 60 °C, 1.0 ≈ 100 °C)
          * else   → ``None`` (CPU‑only fallback).
        """
        import subprocess
        import platform
        os_name = platform.system()
        try:
            if os_name == "Darwin":
                r = subprocess.run(
                    ["pmset", "-g", "therm"],
                    capture_output=True, text=True, timeout=2,
                )
                # "Thermal pressure: N"   or   "No thermal warning …"
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if "Thermal pressure:" in line:
                        parts = line.split()
                        val = int(parts[-1])  # 0‑4
                        return min(val / 4.0, 1.0)
                # "No thermal warning …" → confirmed cool
                return 0.0

            if os_name == "Windows":
                # Try wmic first (fast, universal on Windows 10/11)
                try:
                    r = subprocess.run(
                        ["wmic",
                         "/namespace:\\\\root\\wmi",
                         "PATH", "MSAcpi_ThermalZoneTemperature",
                         "get", "CurrentTemperature", "/format:csv"],
                        capture_output=True, text=True, timeout=3,
                    )
                except FileNotFoundError:
                    # Fall back to PowerShell Get-CimInstance
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive",
                         "-Command",
                         "(Get-CimInstance -Namespace root/wmi "
                         "-ClassName MSAcpi_ThermalZoneTemperature"
                         ").CurrentTemperature"],
                        capture_output=True, text=True, timeout=5,
                    )
                temps_c = []
                for token in r.stdout.split():
                    token = token.strip()
                    try:
                        raw = int(token)
                    except ValueError:
                        continue
                    if raw < 2000:  # unreliably low → skip
                        continue
                    temp_c = (raw / 10.0) - 273.15
                    temps_c.append(temp_c)
                if not temps_c:
                    return None
                hottest = max(temps_c)
                # Map 60 °C → 0.0, 100 °C → 1.0, clamp at bounds
                return max(0.0, min((hottest - 60.0) / 40.0, 1.0))
        except Exception:
            pass
        return None

    def maybe_update(self):
        if not _PSUTIL_OK or THROTTLE_ENGAGE_PCT >= 100:
            return
        now = time.time()
        if now - self._last_sample_at < THROTTLE_SAMPLE_SECS:
            return
        self._last_sample_at = now

        try:
            # interval=None reads since the last call — non-blocking.
            cpu = _psutil.cpu_percent(interval=None)
        except Exception:
            cpu = None

        # Sample thermal stress at most once per sample window.
        # Offloaded to a background thread so a slow/wedged subprocess
        # never blocks the main capture loop.
        if now - self._last_thermal_check >= THROTTLE_SAMPLE_SECS:
            self._last_thermal_check = now
            if self._thermal_future is not None and self._thermal_future.done():
                try:
                    raw = self._thermal_future.result()
                    if raw is not None:
                        self._thermal_pressure = raw
                except Exception:
                    pass
            if self._thermal_future is None or self._thermal_future.done():
                self._thermal_future = self._thermal_executor.submit(
                    type(self)._read_thermal_stress)

        # Combine signals: hi = CPU high OR thermal stressed,
        # lo = CPU low AND thermal normal (or unknown).
        thermal_stressed = (
            self._thermal_pressure is not None
            and self._thermal_pressure >= 0.75
        )
        thermal_cool = (
            self._thermal_pressure is not None
            and self._thermal_pressure < 0.5
        )

        hi_signal = (cpu is not None and cpu >= THROTTLE_ENGAGE_PCT)
        if thermal_stressed:
            hi_signal = True

        lo_signal = (cpu is not None and cpu <= THROTTLE_RELEASE_PCT)
        if thermal_cool:
            lo_signal = True
        elif self._thermal_pressure is not None and not thermal_cool:
            # Thermal pressure in the ambiguous band (0.5‑0.74) —
            # don't count as a recovery signal unless CPU also agrees.
            lo_signal = lo_signal

        if hi_signal:
            self._hi_streak += 1
            self._lo_streak = 0
        elif lo_signal:
            self._lo_streak += 1
            self._hi_streak = 0
        else:
            # Hysteresis band — neither escalate nor relax.
            return

        if not self._throttled and self._hi_streak >= 2:
            self._throttled = True
            self.effective_fps = float(THROTTLE_LOW_FPS)
            self._notify(cpu)
        elif self._throttled and self._lo_streak >= 2:
            self._throttled = False
            self.effective_fps = float(TARGET_FPS)
            self._notify(cpu)

    def _notify(self, cpu):
        try:
            if self._on_transition:
                self._on_transition({
                    "throttled": self._throttled,
                    "cpu_pct": cpu,
                    "thermal": self._thermal_pressure,
                })
        except Exception:
            # Never let a logging hiccup take down the main loop.
            pass


# Smoothing window for gaze readings — averages out per-frame jitter so we
# don't flag a single noisy frame as "looking away". 5 frames at ~30fps
# gives a ~150ms low-pass which feels responsive without being twitchy.
GAZE_SMOOTH_WINDOW = 5

# ─── PER-STUDENT CALIBRATION ──────────────────────────────────────────────────
# Both the ResNet18 gaze model and the solvePnP head-pose pipeline have a
# per-camera + per-person bias of 5–15° at the rest position ("looking at
# the screen"). Without subtracting this bias, a student whose webcam sits
# high or whose head naturally tilts already starts halfway to threshold,
# causing false positives that the loose tier values can only hide, not fix.
#
# At session start we collect the first CALIBRATION_FRAMES clean readings,
# average them, and treat that as the personal "centre". Every subsequent
# yaw/pitch is compared against the threshold *after* subtracting the bias.
CALIBRATION_FRAMES = 45      # ~3s at 15fps — long enough to be stable
CALIBRATION_MAX_WAIT = 240   # give up after this many frames if face missing

# ─── DIRECTION HELPER ────────────────────────────────────────────────────────
# The old cascade checked yaw first, then pitch as a fallback — so if yaw was
# *barely* over threshold while pitch was way past, the label said "right"
# instead of "down". Fix: pick whichever axis dominates (relative to its own
# threshold) to avoid misleading labels like "head turns right" when the user
# was clearly looking down.
def _dominant_direction(yaw: float, pitch: float,
                        yaw_thresh: float, pitch_thresh: float) -> str:
    """Return 'left'|'right'|'up'|'down' choosing the dominant axis."""
    # Normalise each axis by its threshold so they're comparable.
    yaw_ratio   = abs(yaw)   / max(yaw_thresh,   1e-6)
    pitch_ratio = abs(pitch) / max(pitch_thresh, 1e-6)
    if yaw_ratio >= pitch_ratio:
        return "left" if yaw < 0 else "right"
    else:
        return "up"   if pitch < 0 else "down"


# ─── CHEAT OBJECTS ────────────────────────────────────────────────────────────
# COCO class IDs for items that shouldn't be on the desk during an exam.
CHEAT_IDS = {
    67: "Phone",
    63: "Laptop",
    73: "Book",
    66: "Keyboard",
    62: "TV",
}

def classify_phone_position(phone_box: Tuple[int, int, int, int],
                            face_bbox: Optional[Tuple[int, int, int, int]],
                            frame_h: int) -> str:
    """Classify phone position: 'phone_in_hand', 'phone_on_desk', or 'phone_detected'.

    If the phone's center is above ~50% of the face bottom, the student is
    likely holding it (critical severity). If it's below ~65% of frame height,
    it's resting on the desk (high severity). Otherwise returns 'phone_detected'
    (ambiguous — no face visible or phone mid-frame).
    """
    px1, py1, px2, py2 = phone_box
    phone_center_y = (py1 + py2) / 2

    if face_bbox is not None:
        _, fy1, _, fy2 = face_bbox
        face_bottom = fy2
        if phone_center_y < face_bottom * PHONE_HAND_RATIO:
            return "phone_in_hand"

    if phone_center_y > frame_h * PHONE_DESK_Y_RATIO:
        return "phone_on_desk"

    return "phone_detected"

# ─── SERVER LOGGING ───────────────────────────────────────────────────────────
session_start = time.time()
violation_count = 0

HEARTBEAT_URL = f"{SERVER_BASE}/api/v1/heartbeat"

# ─── THREAD-LOCAL HTTP SESSION ──────────────────────────────────────────────
# Each thread gets its own requests.Session() so connection-pool state is
# never shared across threads (requests.Session is not thread-safe).
_http_local = threading.local()
_HTTP_HEADERS = {
    "Content-Type": "application/json",
    **({"Authorization": f"Bearer {JWT_TOKEN}"} if JWT_TOKEN else {}),
}

def _session():
    if not hasattr(_http_local, "sess"):
        s = requests.Session()
        s.headers.update(_HTTP_HEADERS)
        _http_local.sess = s
    return _http_local.sess
_violation_lock = threading.Lock()

def _heartbeat_loop():
    hb_failures = 0
    while True:
        time.sleep(30)
        ok = False
        try:
            r = _session().post(
                HEARTBEAT_URL,
                json={"session_id": SESSION_ID, "event_type": "heartbeat",
                      "severity": "low", "details": "alive"},
                timeout=5
            )
            # requests.post() only raises on connection-layer errors;
            # 4xx/5xx HTTP responses are NOT exceptions. Without the
            # status check, an auth failure (401) or backend outage
            # (5xx) would silently count as success and we'd hammer
            # the server every 30s with no backoff.
            ok = r.ok
            if not ok:
                print(f"[Heartbeat] HTTP {r.status_code}")
        except Exception as e:
            print(f"[Heartbeat Error] {e}")
        if ok:
            hb_failures = 0
        else:
            hb_failures += 1
            hb_backoff = min(60, 2 ** min(hb_failures, 5))
            print(f"[Heartbeat] backoff: {hb_backoff}s")
            time.sleep(hb_backoff)

threading.Thread(target=_heartbeat_loop, daemon=True).start()

# ─── ON-DEMAND LIVE CAMERA STREAM ─────────────────────────────────────────────
# When a teacher clicks "View camera" on the dashboard, the server flips a
# per-session Redis flag. This thread polls that flag every 2s; while it's
# set, the main capture loop (further down) sees `_LIVE_VIEW_ACTIVE = True`
# and pushes one downscaled JPEG to the server every ~1.5s. When the flag
# clears (teacher closes the panel, or 60s TTL expires from inactivity),
# we stop uploading. No persistent storage, no continuous streaming —
# this is strictly opt-in surveillance with a hard kill-switch.

CONTROL_URL    = f"{SERVER_BASE}/api/v1/proctor/control/{SESSION_ID}"
LIVE_FRAME_URL = f"{SERVER_BASE}/api/v1/proctor/live-frame"
_LIVE_VIEW_ACTIVE = False
_LIVE_VIEW_LOCK = threading.Lock()

# ─── WebSocket live-feed (preferred) with HTTP fallback ───────
def _derive_ws_url():
    """Convert the current server URL → ws(s) URL for live-frame streaming."""
    parsed = _urlsplit_init(SERVER_URL or SERVER_BASE)
    base = _urlunsplit_init((parsed.scheme, parsed.netloc, "", "", "")) if parsed.scheme and parsed.netloc else SERVER_BASE
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + f"/ws/v1/live-frame/{SESSION_ID}"
    if base.startswith("http://"):
        if not os.environ.get("PROCTOR_ALLOW_WS", "").strip().lower() in {"1", "true"}:
            print("[LiveFeed] ⚠ WebSocket URL uses ws:// — JWT sent in cleartext! Set PROCTOR_ALLOW_WS=1 to proceed.")
        return "ws://" + base[len("http://"):] + f"/ws/v1/live-frame/{SESSION_ID}"
    return base + f"/ws/v1/live-frame/{SESSION_ID}"

WS_LIVE_URL = _derive_ws_url()

_ws_conn = None
_ws_lock = threading.Lock()
_ws_backoff = 0
_ws_last_attempt = 0.0
_WS_MAX_BACKOFF = 30

def _get_ws():
    global _ws_conn, _ws_backoff, _ws_last_attempt
    with _ws_lock:
        if _ws_conn is not None:
            return _ws_conn
        now = time.time()
        if now - _ws_last_attempt < _ws_backoff:
            return None  # still cooling down
        _ws_last_attempt = now
        try:
            import websocket
            _ws_kwargs = dict(timeout=5, skip_utf8_encoding=True)
            if WS_LIVE_URL.startswith("wss://"):
                _ws_kwargs["sslopt"] = {
                    "cert_reqs": ssl.CERT_REQUIRED,
                    "check_hostname": True,
                }
            ws = websocket.create_connection(WS_LIVE_URL, **_ws_kwargs)
            import json
            ws.send(json.dumps({"token": JWT_TOKEN}))
            _ws_conn = ws
            _ws_backoff = 0  # reset on success
            print("[LiveFeed] ✅ WebSocket connected", flush=True)
        except Exception as _we:
            if _ws_backoff == 0:
                print(f"[LiveFeed] WS not available, using HTTP fallback",
                      flush=True)
            _ws_backoff = min(_WS_MAX_BACKOFF, max(1, _ws_backoff * 2 or 1))
        return _ws_conn

def _reset_ws():
    global _ws_conn, _ws_backoff
    with _ws_lock:
        if _ws_conn:
            try:
                _ws_conn.close()
            except Exception:
                pass
            _ws_conn = None
            _ws_backoff = min(_WS_MAX_BACKOFF, max(1, _ws_backoff * 2))

def _control_loop():
    """Poll the server every 2s for control flags. Sets the global
    _LIVE_VIEW_ACTIVE so the capture loop knows whether to upload."""
    global _LIVE_VIEW_ACTIVE
    while True:
        try:
            r = _session().get(CONTROL_URL, timeout=4)
            if r.ok:
                want = bool(r.json().get("live_view"))
                with _LIVE_VIEW_LOCK:
                    if want != _LIVE_VIEW_ACTIVE:
                        print(f"[LiveView] {'ENABLED' if want else 'disabled'}",
                              flush=True)
                    _LIVE_VIEW_ACTIVE = want
        except Exception:
            # Transient network blips just leave the previous state in
            # place. Worst case: we stream for an extra 60s after the
            # teacher actually closed the panel — bounded by the
            # server-side TTL so it can't run forever.
            pass
        time.sleep(2)

threading.Thread(target=_control_loop, daemon=True).start()


def upload_live_frame(frame_bgr):
    small = cv2.resize(frame_bgr, (320, 240), interpolation=cv2.INTER_AREA)
    try:
        _live_q.put_nowait((small.copy(), time.time()))
    except Exception:
        pass  # queue full, skip this frame

# Track the last live-frame send so we can pace at ~1.5 s without
# making the inner capture loop care about wall time.
_LAST_LIVE_FRAME_TS = 0.0
_LIVE_FRAME_INTERVAL_SEC = 1.5
_live_q: Queue = Queue(maxsize=2)

def _live_upload_loop():
    while True:
        try:
            small, _ts = _live_q.get(timeout=1)
        except Empty:
            continue
        try:
            ok, buf = cv2.imencode(".jpg", small,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if not ok:
                continue
            raw_bytes = buf.tobytes()
            ws = _get_ws()
            if ws is not None:
                try:
                    ws.send_binary(raw_bytes)
                    continue
                except Exception:
                    _reset_ws()
            b64 = base64.b64encode(raw_bytes).decode("ascii")
            _session().post(
                LIVE_FRAME_URL,
                json={"session_id": SESSION_ID, "jpeg_b64": b64},
                timeout=4,
            )
        except Exception:
            pass

threading.Thread(target=_live_upload_loop, daemon=True, name="live-uploader").start()

# ─── ASYNCHRONOUS EVENT (VIOLATION) UPLOAD WORKER ─────────────────────────────
# log_event() enqueues here instead of POSTing inline. A synchronous post in
# log_event (timeout=3) used to run ON the per-frame proctoring loop, so a
# slow/unreachable server stalled video processing up to 3s per event —
# stuttering/freezing the exam on a flaky network. Single consumer keeps
# events FIFO-ordered; the queue is bounded so a long outage can't grow memory.
_event_q: "Queue" = Queue(maxsize=500)
# Count of events dropped because the queue saturated (sustained outage).
# Surfaced as a single `event_queue_full` diagnostic once connectivity
# returns — emitting it mid-outage would just fail too. METADATA ONLY.
_dropped_events = 0
_dropped_lock = threading.Lock()

# A dequeued event whose POST fails transiently is RETRIED, not dropped — the
# old loop discarded it the moment it left the queue (dequeue == commit), so a
# single network blip silently lost a violation with no counted signal.
_EVENT_MAX_RETRIES = 5

def _event_upload_loop():
    global _dropped_events
    consecutive_failures = 0
    pending = None        # payload kept across iterations to retry on failure
    pending_tries = 0     # attempts already spent on `pending`
    while True:
        if pending is not None:
            payload = pending
        else:
            try:
                payload = _event_q.get(timeout=1)
            except Empty:
                continue
        status = None
        err_msg = ""
        try:
            r = _session().post(SERVER_URL, json=payload, timeout=5)
            status = r.status_code
            if not r.ok:
                err_msg = f"HTTP {r.status_code}"
        except Exception as e:
            err_msg = str(e)
        if status is not None and 200 <= status < 300:
            pending = None
            pending_tries = 0
            consecutive_failures = 0
            # Connectivity is back — report any drops that happened during
            # the outage, exactly once, so the gap is visible server-side.
            with _dropped_lock:
                n = _dropped_events
                _dropped_events = 0
            if n > 0:
                try:
                    _session().post(SERVER_URL, json=dict(
                        session_id=SESSION_ID, event_type="event_queue_full",
                        severity="low",
                        details=f"dropped:{n} events during upload outage"),
                        timeout=5)
                except Exception:
                    # Still flaky — fold the count back in for the next recovery.
                    with _dropped_lock:
                        _dropped_events += n
            continue
        # A 4xx won't succeed on retry (bad payload / auth) — drop it so one
        # poison event can't wedge delivery of everything queued behind it.
        if status is not None and 400 <= status < 500:
            print(f"[Event Upload Error] {err_msg} — dropping (client error)")
            pending = None
            pending_tries = 0
            with _dropped_lock:
                _dropped_events += 1
            continue
        # Transient (5xx / connection): keep the event and retry after backoff,
        # up to a bounded budget so a permanently-unreachable server can't
        # block the queue forever. Exhausting the budget counts as a drop.
        consecutive_failures += 1
        pending_tries += 1
        if pending_tries <= _EVENT_MAX_RETRIES:
            pending = payload
        else:
            print(f"[Event Upload Error] {err_msg} — giving up after "
                  f"{_EVENT_MAX_RETRIES} retries")
            pending = None
            pending_tries = 0
            with _dropped_lock:
                _dropped_events += 1
        backoff = min(30, 2 ** min(consecutive_failures, 5))
        print(f"[Event Upload Error] {err_msg} (backoff: {backoff}s)")
        time.sleep(backoff)

threading.Thread(target=_event_upload_loop, daemon=True, name="event-uploader").start()

def _flush_events(timeout=3.0):
    """Best-effort drain of the event queue before the process exits, so the
    final session_ended event (and any backlog) has a chance to send."""
    deadline = time.time() + timeout
    while not _event_q.empty() and time.time() < deadline:
        time.sleep(0.1)

def _post_event_now(payload, attempts=3, timeout=4):
    """SYNCHRONOUS best-effort event POST with a short bounded retry. Used for
    the terminal session_ended at teardown: the async uploader may be mid-
    backoff and _http is about to close, so routing the final event through the
    queue risked losing it on a transient blip. A couple of quick retries land
    it without hanging exit on a genuine outage (the server reaper reconciles
    that case). Returns True iff the server accepted it."""
    for i in range(attempts):
        try:
            r = _session().post(SERVER_URL, json=payload, timeout=timeout)
            if r.ok:
                return True
        except Exception:
            pass
        if i < attempts - 1:
            time.sleep(0.5 * (i + 1))
    return False

def log_event(etype, severity, details):
    global violation_count, _dropped_events
    conf = CONFIDENCE.get(etype, 0.75)
    conf_tag = f"confidence:{int(conf*100)}%"
    # Avoid leading `| confidence:...` when details is empty/whitespace.
    full_details = f"{details} | {conf_tag}" if (details and str(details).strip()) else conf_tag
    if severity in ("high", "medium"):
        with _violation_lock:
            violation_count += 1
    payload = dict(session_id=SESSION_ID, event_type=etype,
                   severity=severity, details=full_details)
    try:
        _event_q.put_nowait(payload)
        print(f"[VIOLATION] {etype}: {details}")
    except Full:
        # Saturated (long outage) — drop the OLDEST so the newest violation
        # still lands, rather than blocking the caller (which may be the loop).
        try:
            _event_q.get_nowait()
            _event_q.put_nowait(payload)
            print(f"[VIOLATION] {etype}: {details} (queue full — dropped oldest)")
            with _dropped_lock:
                _dropped_events += 1
        except Exception:
            print(f"[Server Error] event queue full — dropped {etype}")
            with _dropped_lock:
                _dropped_events += 1

def save_evidence(frame, label):
    save_local_flag = os.environ.get("SAVE_EVIDENCE_LOCAL", "").strip().lower() in {"1", "true"}
    if not JWT_TOKEN and not save_local_flag:
        return
    # Privacy: in a real exam (JWT present) evidence frames live ONLY in RAM and
    # are uploaded on a violation — they are NEVER written to the student's disk.
    # The local .jpg dump is a dev/debug aid, gated strictly behind
    # SAVE_EVIDENCE_LOCAL, so the "Zero-Storage Local Buffer" guarantee holds in
    # production. A disk-write failure here must NOT block the upload.
    if save_local_flag:
        try:
            ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = os.path.join(EVIDENCE_DIR, f"{label}_{ts}.jpg")
            if cv2.imwrite(path, frame):
                print(f"[Evidence] → {path}")
            else:
                print(f"[Evidence Error] Write failed: {path}")
        except Exception as e:
            print(f"[Evidence Error] {e}")
    if not JWT_TOKEN:
        return  # debug-only local save; no server to upload to
    try:
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        b64 = base64.b64encode(jpg.tobytes()).decode("ascii")
        # Appeal-critical events carry the pre-violation desktop context buffer
        # (t-3s..t-0); everything else stays single-frame.
        context = _frame_ring.context_before() if _wants_context(label) else None
        _evidence_q.put_nowait((b64, label, context or None))
    except Full:
        pass  # queue saturated (burst of flags) — drop oldest-vs-newest is fine
    except Exception:
        pass

# ─── ASYNCHRONOUS EVIDENCE UPLOAD WORKER ──────────────────────────────────────
# Bumped 8→16: an appeal-critical flag now enqueues a packet (flag + up to 3
# context frames is one item, but bursts of distinct flags enqueue more), so a
# little more headroom avoids dropping evidence during a rapid violation burst.
_evidence_q: Queue = Queue(maxsize=16)

def _evidence_upload_loop():
    consecutive_failures = 0
    while True:
        try:
            item = _evidence_q.get(timeout=1)
        except Empty:
            continue
        b64, label = item[0], item[1]
        context = item[2] if len(item) > 2 else None
        ok = False
        err_msg = ""
        try:
            payload = {
                "session_id": SESSION_ID,
                "frame":      b64,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "event_type": label,
            }
            # Pre-violation context frames (oldest-first, each with offset_ms back
            # from the flag) for appeal-critical events. Absent for single-frame
            # events, so the server keeps its existing one-frame behaviour.
            if context:
                payload["context_frames"] = context
            r = _session().post(EVIDENCE_UPLOAD_URL, json=payload, timeout=10)
            # Same rationale as _heartbeat_loop: check the HTTP status
            # so 4xx/5xx triggers backoff, not just connect failures.
            ok = r.ok
            if not ok:
                err_msg = f"HTTP {r.status_code}"
        except Exception as e:
            err_msg = str(e)
        if ok:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            backoff = min(30, 2 ** min(consecutive_failures, 5))
            print(f"[Evidence Upload Error] {err_msg} (backoff: {backoff}s)")
            time.sleep(backoff)

threading.Thread(target=_evidence_upload_loop, daemon=True, name="evidence-uploader").start()

def _cleanup_evidence_dir(max_age_days: int = 7):
    """Remove evidence files older than max_age_days to prevent disk leaks."""
    if not os.path.isdir(EVIDENCE_DIR):
        return
    now = time.time()
    cutoff = now - max_age_days * 86400
    try:
        for fname in os.listdir(EVIDENCE_DIR):
            fpath = os.path.join(EVIDENCE_DIR, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
    except Exception as _exc:
        print(f"[Evidence Cleanup] Error: {_exc}")

# ─── GAZE ESTIMATOR (ONNX) ────────────────────────────────────────────────────
# Wraps the ResNet18 gaze model. Input: a tight crop of the face. Output:
# (yaw, pitch) in radians, smoothed over GAZE_SMOOTH_WINDOW recent frames.
# The model emits per-bin softmax probabilities over 90 angle bins (binwidth
# 4°, offset 180°), which we collapse into a continuous expected angle.
class GazeEstimator:
    def __init__(self, model_path: str):
        self.session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        self._bins         = 90
        self._binwidth     = 4
        self._angle_offset = 180
        self.idx_tensor    = np.arange(self._bins, dtype=np.float32)
        input_cfg          = self.session.get_inputs()[0]
        self.input_name    = input_cfg.name
        # input_cfg.shape is [N, C, H, W]; we want (W, H) for cv2.resize
        self.input_size    = tuple(input_cfg.shape[2:][::-1])
        self.output_names  = [o.name for o in self.session.get_outputs()]
        self.yaw_buf       = deque(maxlen=GAZE_SMOOTH_WINDOW)
        self.pitch_buf     = deque(maxlen=GAZE_SMOOTH_WINDOW)

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = cv2.resize(image, self.input_size).astype(np.float32) / 255.0
        # ImageNet normalization — the resnet18 backbone expects this.
        image = (image - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return np.expand_dims(np.transpose(image, (2, 0, 1)), 0).astype(np.float32)

    @staticmethod
    def _softmax(x):
        e = np.exp(x - np.max(x, axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)

    def estimate(self, face_crop: np.ndarray) -> Tuple[float, float]:
        if face_crop.size == 0:
            return 0.0, 0.0
        outputs = self.session.run(
            self.output_names, {self.input_name: self._preprocess(face_crop)})
        yaw_p   = self._softmax(outputs[0])
        pitch_p = self._softmax(outputs[1])
        # Expected value over the bin grid → continuous angle in degrees,
        # then to radians for downstream comparisons.
        yaw   = float(np.radians(
            (np.sum(yaw_p   * self.idx_tensor, axis=1) * self._binwidth - self._angle_offset)[0]))
        pitch = float(np.radians(
            (np.sum(pitch_p * self.idx_tensor, axis=1) * self._binwidth - self._angle_offset)[0]))
        self.yaw_buf.append(yaw)
        self.pitch_buf.append(pitch)
        return (sum(self.yaw_buf)   / len(self.yaw_buf),
                sum(self.pitch_buf) / len(self.pitch_buf))

# Lazy-init the gaze estimator. If the model file isn't present we just
# disable gaze checking — head pose + face count + eyes still work.
_gaze_engine: Optional[GazeEstimator] = None
GAZE_AVAILABLE = False
if ORT_AVAILABLE:
    _gaze_model_path = _find_gaze_model()
    if _gaze_model_path:
        try:
            _gaze_engine = GazeEstimator(_gaze_model_path)
            GAZE_AVAILABLE = True
            print(f"[Gaze] ✅ ResNet18 ONNX loaded from {_gaze_model_path}")
        except Exception as _ge:
            print(f"[Gaze] ❌ Model load failed: {_ge}")
            _MODEL_ERRORS["gaze"] = type(_ge).__name__
    else:
        print("[Gaze] ❌ resnet18_gaze.onnx not found in weights/ — gaze direction disabled")
        _MODEL_ERRORS["gaze"] = "weights_missing"

# ─── HEAD POSE (cv2.solvePnP from RetinaFace 5-point landmarks) ───────────────
# RetinaFace returns 5 2D points: left_eye, right_eye, nose, left_mouth,
# right_mouth. We pair them with a canonical 3D model of those points and
# solve for the head's rotation (yaw + pitch in degrees). For numerical
# stability we synthesize a 6th forehead point above the eye midpoint.
_HEAD_MODEL_3D = np.array([
    [-225.0,  170.0, -135.0],   # left eye
    [ 225.0,  170.0, -135.0],   # right eye
    [   0.0,    0.0,    0.0],   # nose tip
    [-150.0, -150.0, -125.0],   # left mouth
    [ 150.0, -150.0, -125.0],   # right mouth
    [   0.0,  330.0,  -65.0],   # forehead (synthetic)
], dtype=np.float64)

def get_head_pose(landmarks_2d: np.ndarray,
                  img_w: int, img_h: int) -> Tuple[float, float]:
    """Return (yaw_deg, pitch_deg). 0,0 = facing camera. Positive yaw = right."""
    try:
        leye, reye = landmarks_2d[0], landmarks_2d[1]
        eye_mid    = (leye + reye) / 2
        forehead   = eye_mid - np.array([0, np.linalg.norm(reye - leye) * 0.6])
        lm6        = np.vstack([landmarks_2d, forehead])
        focal      = img_w
        cam_matrix = np.array(
            [[focal, 0, img_w / 2],
             [0, focal, img_h / 2],
             [0, 0, 1]], dtype=np.float64)
        ok, rvec, _ = cv2.solvePnP(
            _HEAD_MODEL_3D, lm6, cam_matrix, np.zeros((4, 1)),
            flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            return 0.0, 0.0
        rmat, _ = cv2.Rodrigues(rvec)
        angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)
        yaw   = float(angles[1])
        pitch = float(angles[0])
        # solvePnP can return a 180° flipped basis on some frames. Unwrap.
        if abs(pitch) > 90:
            pitch = pitch - np.sign(pitch) * 180
        if abs(yaw) > 90:
            yaw = yaw - np.sign(yaw) * 180
        return yaw, pitch
    except Exception:
        return 0.0, 0.0

# ─── EYE OPEN/CLOSED (Haar cascade) ───────────────────────────────────────────
# Built into OpenCV (cv2.data.haarcascades). No extra weights to ship.
# We treat "no eyes detected" as "eyes closed" — for proctoring purposes
# the difference doesn't matter and the user-visible signal is the same.
_eye_cascade_path = cv2.data.haarcascades + "haarcascade_eye.xml"
_eye_cascade = cv2.CascadeClassifier(_eye_cascade_path)
EYES_AVAILABLE = not _eye_cascade.empty()
if not EYES_AVAILABLE:
    print(f"[Eyes] ❌ Haar cascade not loaded from {_eye_cascade_path}")
else:
    print("[Eyes] ✅ Haar cascade loaded")

def eyes_detected(face_crop: np.ndarray) -> bool:
    if not EYES_AVAILABLE or face_crop.size == 0:
        return True  # fail-open: don't false-flag if detector unavailable
    try:
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        eyes = _eye_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20))
        return len(eyes) > 0
    except Exception:
        return False  # fail-closed: crash → treat as closed

# ─── AUDIO (voice detection) ──────────────────────────────────────────────────
AUDIO_AVAILABLE = False
audio_rms       = 0.0
audio_lock      = threading.Lock()

# Phase 75 — ring buffer fed by audio_thread's callback so the
# AudioProcessor worker (keyword + multi-voice detection) has a low-
# latency feed without coupling to sounddevice. Initialised lazily in
# _start_audio so the existing RMS-only code path is unaffected when
# audio_processor isn't available (vosk missing, models not downloaded).
_audio_ring = None
_audio_processor = None

def audio_thread():
    global audio_rms, AUDIO_AVAILABLE
    try:
        import sounddevice as sd
        AUDIO_AVAILABLE = True
        print("[AUDIO] ✅ Microphone active")
        def callback(indata, frames, time_info, status):
            global audio_rms
            rms = float(np.sqrt(np.mean(indata**2)))
            with audio_lock:
                audio_rms = rms
            # Phase 75: also copy PCM into the ring buffer if the
            # AudioProcessor is running. indata is float32; processor
            # wants int16, so convert here once instead of in the
            # consumer's hot path.
            if _audio_ring is not None:
                try:
                    pcm16 = (np.clip(indata[:, 0], -1.0, 1.0) * 32767.0).astype(np.int16)
                    _audio_ring.write(pcm16.tobytes())
                except Exception:
                    pass
        with sd.InputStream(callback=callback,
                            channels=1, samplerate=16000,
                            blocksize=1024):
            while True:
                time.sleep(0.1)
    except Exception as e:
        print(f"[AUDIO] ❌ {e}")

def _start_audio(*, governor=None):
    """Start the audio-analysis daemon thread. Called lazily from main().

    Also bootstraps the Phase 75 AudioProcessor (Vosk + Silero VAD +
    MFCC clustering) when available — soft-imported so a missing
    vosk install or absent model files don't break the rest of the
    proctor. `governor` is the _HardwareGovernor instance so the
    audio worker can read effective_fps and skip cycles when CPU is
    hot.
    """
    threading.Thread(target=audio_thread, daemon=True).start()
    time.sleep(1.5)

    # Bring up the Phase 75 processor opportunistically. Hard
    # try/except around the whole block — this is additive, never
    # fatal to the proctor.
    global _audio_ring, _audio_processor
    try:
        import audio_processor as _ap
        _audio_reason = _ap.AudioProcessor.unavailable_reason()
        if _audio_reason is not None:
            print("[AUDIO] keyword/voice-count detection: unavailable "
                  f"({_audio_reason} — see scripts/download_audio_models.sh)")
            # Surface it as a diagnosable (non-violation) event so a model-less
            # session shows up in the dashboard instead of looking like clean
            # audio. Best-effort — never block the proctor on telemetry.
            try:
                log_event("audio_unavailable", "info", f"reason={_audio_reason}")
            except Exception:
                pass
            return
        # Per-exam keyword list + language from env (set by the
        # Electron python-manager from exam_config). Empty → built-ins.
        # Config flow: env vars win (test / override path). Otherwise
        # fetch from /api/v1/exam/audio-config so a teacher's per-exam
        # changes are picked up at proctor launch with no Electron
        # plumbing change. Failures fall back to defaults.
        lang = (os.environ.get("PROCTOR_AUDIO_LANG") or "").strip()
        custom_json = os.environ.get("PROCTOR_AUDIO_KEYWORDS_JSON", "")
        custom: list = []
        if not lang or not custom_json:
            try:
                from urllib.parse import urljoin as _urljoin
                cfg_url = _urljoin(SERVER_URL, "/api/v1/exam/audio-config")
                r = _session().get(cfg_url,
                              params={"session_id": SESSION_ID},
                              headers={"Authorization": f"Bearer {JWT_TOKEN}"} if JWT_TOKEN else {},
                              timeout=5)
                if r.status_code == 200:
                    d = r.json()
                    if not lang:
                        lang = (d.get("audio_keywords_language") or "en").strip()
                    if not custom_json:
                        fetched = d.get("audio_keywords") or []
                        if isinstance(fetched, list):
                            custom = [str(k) for k in fetched if isinstance(k, str)]
            except Exception as e:
                print(f"[AUDIO] audio-config fetch failed (using defaults): {e}")
        if custom_json and not custom:
            try:
                parsed = json.loads(custom_json)
                if isinstance(parsed, list):
                    custom = [str(k) for k in parsed if isinstance(k, str)]
            except Exception:
                pass
        if lang not in ("en", "hi", "en+hi"):
            lang = "en"
        _audio_ring = _ap.AudioRingBuffer(max_secs=30.0)

        def _log_cb(etype, severity, details):
            try:
                log_event(etype, severity, details)
            except Exception as e:
                print(f"[AUDIO] log_event failed: {e}")

        def _save_cb(label):
            # save_evidence needs a frame which we don't have inside
            # the audio worker. The main loop's next per-frame snapshot
            # serves as the camera evidence — correlated by timestamp
            # in the dashboard timeline.
            pass

        def _get_fps():
            try:
                return float(getattr(governor, "effective_fps", 15.0))
            except Exception:
                return 15.0

        _audio_processor = _ap.AudioProcessor(
            ring=_audio_ring,
            log_event_cb=_log_cb,
            save_evidence_cb=_save_cb,
            language=lang,
            custom_keywords=custom,
            get_effective_fps=_get_fps,
            target_fps=15.0,
        )
        if _audio_processor.start():
            print(f"[AUDIO] ✅ keyword/voice-count active (lang={lang}, +{len(custom)} custom)")
        else:
            _audio_processor = None
            _audio_ring = None
            try:
                log_event("audio_unavailable", "info", "reason=start-failed")
            except Exception:
                pass
    except Exception as e:
        print(f"[AUDIO] keyword/voice-count bootstrap failed: {e}")
        _audio_processor = None
        _audio_ring = None
        try:
            log_event("audio_unavailable", "info", f"reason=bootstrap-error:{type(e).__name__}")
        except Exception:
            pass

# ─── VIRTUAL WEBCAM / SCREEN-SHARE DETECTION ─────────────────────────────────
# Detects when the student uses a virtual camera (OBS, ManyCam, etc.) instead
# of a physical webcam, which could be used to feed pre-recorded or
# manipulated footage. Also detects screen-share-like feeds.

VIRTUAL_CAM_KEYWORDS = [
    "obs", "manycam", "snap camera", "cama", "manyCam",
    "virtual", "fake", "splitcam", "youcam", "perfect camera",
    "cyberlink", "xsplit", "vcam", "e2eSoft", "broadcastcam",
    "manyCam Virtual", "OBS Virtual", "Unity Capture",
    "NVIDIA Broadcast", "streamlabs", "prism",
]

def _detect_virtual_camera():
    """Check if the active camera is a known virtual webcam."""
    system = platform.system()
    try:
        if system == "Darwin":
            import subprocess
            result = subprocess.run(
                ["system_profiler", "SPCameraDataType"],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for keyword in VIRTUAL_CAM_KEYWORDS:
                    if keyword.lower() in result.stdout.lower():
                        return keyword
        elif system == "Windows":
            import subprocess
            # wmic was removed in Windows 11 24H2 — use the supported
            # Get-CimInstance (present on every Windows 10+ box). The old
            # `wmic path Win32_PnPEntity` call returned nothing on current
            # installs, so virtual-camera detection went silently dark there.
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_PnPEntity | "
                 "Where-Object { $_.PNPClass -eq 'Media' } | "
                 "Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=8)
            if result.returncode == 0:
                for keyword in VIRTUAL_CAM_KEYWORDS:
                    if keyword.lower() in result.stdout.lower():
                        return keyword
    except Exception as _exc:
        print(f"[VIRTUAL CAM] Detection error: {_exc}")
    return None

def _detect_screen_share_feed(frame: np.ndarray) -> Optional[str]:
    """Heuristic: detect if camera frame looks like a screen capture.

    Screen shares tend to have:
    - High edge density (UI elements, text)
    - Many sharp rectangular boundaries
    - Very low noise (digital source, not optical)
    """
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Edge density via Canny
        edges = cv2.Canny(gray, 50, 150)
        edge_ratio = np.sum(edges > 0) / (edges.shape[0] * edges.shape[1])

        # Noise level (std of Laplacian — camera feeds have optical noise,
        # screen captures are much cleaner)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()

        # Screen-like: high edge density + very low noise
        if edge_ratio > 0.15 and laplacian_var < 50:
            return f"screen_like (edge:{edge_ratio:.2f} noise:{laplacian_var:.0f})"
    except Exception:
        pass
    return None

# Deferred to main() so subprocess calls don't block module import
_virtual_camera_name = None

# ─── VM / SANDBOX DETECTION ──────────────────────────────────────────────────
# Checks for common virtual machine and sandbox indicators. Students running
# the proctor inside a VM could bypass restrictions or share the host's screen.

VM_INDICATORS = [
    "vmware", "virtualbox", "vbox", "parallels", "hyper-v",
    "qemu", "kvm", "xen", "bochs", "virtio", "vmm",
]

def _detect_vm() -> Optional[str]:
    """Check for virtual machine indicators."""
    system = platform.system()
    try:
        if system == "Darwin":
            import subprocess
            result = subprocess.run(
                ["sysctl", "-n", "hw.model"], capture_output=True,
                text=True, timeout=5)
            if result.returncode == 0:
                for indicator in VM_INDICATORS:
                    if indicator in result.stdout.lower():
                        return indicator
            # Also check for VM-specific hardware
            result = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                for indicator in VM_INDICATORS:
                    if indicator in result.stdout.lower():
                        return indicator
        elif system == "Windows":
            import subprocess
            # wmic was removed in Windows 11 24H2, so the old `wmic bios` /
            # `wmic computersystem` calls returned nothing on current installs
            # and Windows VM detection went silently dark. Get-CimInstance is
            # the supported replacement, present on every Windows 10+ box —
            # mirror of the fix already in lib/integrity.js.
            # Check BIOS serial number (VMs often use generic ones)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_BIOS | Select-Object -ExpandProperty SerialNumber"],
                capture_output=True, text=True, timeout=8)
            if result.returncode == 0:
                out = result.stdout.lower()
                for indicator in VM_INDICATORS:
                    if indicator in out:
                        return indicator
                # Generic serial numbers are a strong VM signal
                if "vmware" in out or "virtualbox" in out or \
                   "0000" in out or "none" in out:
                    return "generic_bios_serial"
            # Check manufacturer + model (Parallels / VMware show in Model)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_ComputerSystem | "
                 "Select-Object Manufacturer,Model | Format-List"],
                capture_output=True, text=True, timeout=8)
            if result.returncode == 0:
                for indicator in VM_INDICATORS:
                    if indicator in result.stdout.lower():
                        return indicator
        elif system == "Linux":
            import subprocess
            # Check DMI product name
            for path in ["/sys/class/dmi/id/product_name",
                         "/sys/class/dmi/id/sys_vendor"]:
                try:
                    with open(path) as f:
                        content = f.read().lower()
                        for indicator in VM_INDICATORS:
                            if indicator in content:
                                return indicator
                except (OSError, IOError):
                    continue
            # Check CPU info
            try:
                with open("/proc/cpuinfo") as f:
                    content = f.read().lower()
                    for indicator in VM_INDICATORS:
                        if indicator in content:
                            return indicator
            except (OSError, IOError):
                pass
    except Exception as _exc:
        print(f"[VM DETECT] Detection error: {_exc}")
    return None

# Deferred to main() so subprocess calls don't block module import
_vm_name = None

# ─── PRE-EXAM SYSTEM CHECK ───────────────────────────────────────────────────
# Runs before the proctoring loop to verify all subsystems are functional.
# Results are POSTed to the server so the teacher dashboard can show readiness.

SYSTEM_CHECK_URL = f"{SERVER_BASE}/api/v1/proctor/system-check"

def run_system_check(cap: Optional[cv2.VideoCapture] = None) -> dict:
    """Verify camera, audio, network, and detection models.

    If *cap* is provided (already-open VideoCapture), it will be used
    instead of opening a second handle on the same device.
    """
    results = {
        "session_id": SESSION_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "overall": "pass",
    }

    # 1. Network connectivity
    try:
        _session().get(f"{SERVER_BASE}/health", timeout=5)
        results["checks"]["network"] = {"status": "pass", "detail": "Server reachable"}
    except Exception as e:
        results["checks"]["network"] = {"status": "fail", "detail": str(e)}
        results["overall"] = "fail"

    # 2. Camera — use provided cap or open a fresh one
    test_cap = cap
    own_cap = False
    if test_cap is None:
        test_cap = cv2.VideoCapture(0)
        own_cap = True
    try:
        if test_cap.isOpened():
            ret, test_frame = test_cap.read()
            if ret and test_frame is not None:
                h, w = test_frame.shape[:2]
                results["checks"]["camera"] = {
                    "status": "pass", "detail": f"Camera active ({w}x{h})"}
            else:
                results["checks"]["camera"] = {
                    "status": "fail", "detail": "Camera opened but no frames"}
                results["overall"] = "fail"
        else:
            results["checks"]["camera"] = {
                "status": "fail", "detail": "Camera not accessible"}
            results["overall"] = "fail"
    except Exception as e:
        results["checks"]["camera"] = {"status": "fail", "detail": str(e)}
        results["overall"] = "fail"
    finally:
        if own_cap and test_cap:
            test_cap.release()

    # 3. Audio
    results["checks"]["microphone"] = {
        "status": "pass" if AUDIO_AVAILABLE else "warn",
        "detail": "Microphone active" if AUDIO_AVAILABLE else "Microphone unavailable — voice detection disabled"
    }
    if not AUDIO_AVAILABLE and results["overall"] == "pass":
        results["overall"] = "warn"

    # 4. Face detection
    results["checks"]["face_detection"] = {
        "status": "pass" if RETINA_AVAILABLE else "warn",
        "detail": "RetinaFace ready" if RETINA_AVAILABLE else "Face detection disabled"
    }

    # 5. Gaze estimation
    results["checks"]["gaze_estimation"] = {
        "status": "pass" if GAZE_AVAILABLE else "warn",
        "detail": "Gaze model loaded" if GAZE_AVAILABLE else "Gaze estimation disabled"
    }

    # 6. YOLO object detection
    results["checks"]["object_detection"] = {
        "status": "pass" if YOLO_AVAILABLE else "warn",
        "detail": "Object detector ready" if YOLO_AVAILABLE else "Object detection disabled"
    }

    # 7. Wrong-person detection
    results["checks"]["identity_check"] = {
        "status": "pass" if INSIGHT_AVAILABLE else "warn",
        "detail": "InsightFace ready" if INSIGHT_AVAILABLE else "Identity check disabled"
    }

    # 8. Virtual camera check
    if _virtual_camera_name:
        results["checks"]["virtual_camera"] = {
            "status": "fail", "detail": f"Virtual camera: {_virtual_camera_name}"}
        results["overall"] = "fail"
    else:
        results["checks"]["virtual_camera"] = {
            "status": "pass", "detail": "Physical webcam"}

    return results

# ─── FACE EMBEDDING (wrong-person detection) ──────────────────────────────────
enrolled_embedding = None  # populated during enrollment, used in main loop

def get_face_embedding(frame):
    """Return normed InsightFace embedding for the largest face, or None."""
    if not INSIGHT_AVAILABLE:
        return None
    try:
        faces = _insight_app.get(frame)
        if faces:
            return faces[0].normed_embedding
    except Exception:
        pass
    return None

def get_face_embedding_from_crop(face_crop):
    if not INSIGHT_AVAILABLE or face_crop.size == 0:
        return None
    try:
        faces = _insight_app.get(face_crop)
        if faces:
            return faces[0].normed_embedding
    except Exception:
        pass
    return None

# ─── DETECTION HELPERS ────────────────────────────────────────────────────────
# uniface returns a list of face dicts with bbox + landmarks. Wrap that
# behind a single function so the main loop doesn't need to know the format.
def detect_faces(frame: np.ndarray):
    """Return list of (bbox, landmarks_2d) tuples — empty list if no faces.

    RetinaFace.detect()'s per-face shape drifted across uniface majors:
      • 1.1.x → list of dicts  {'bbox':[x1,y1,x2,y2], 'confidence':f, 'landmarks':[[x,y]*5]}
      • 3.x   → list of Face dataclass objects (attribute access; .to_dict() given)
    Calling .get() on a 3.x Face raised "'Face' object has no attribute 'get'"
    on EVERY frame — swallowed below → "No face detected" forever even with the
    model loaded. We normalise each item to a dict so the parser is
    version-agnostic, and still handle the legacy (boxes, landmarks) tuple.
    """
    if not RETINA_AVAILABLE:
        return []
    try:
        result = _retina.detect(frame)
        if result is None:
            return []

        # List of per-face items — dicts (1.1.x) or Face objects (3.x).
        if isinstance(result, list):
            out = []
            for face in result:
                if isinstance(face, dict):
                    fd = face
                elif hasattr(face, "to_dict"):
                    fd = face.to_dict()              # uniface ≥3.x Face dataclass
                else:
                    fd = {"bbox":      getattr(face, "bbox", None),
                          "landmarks": getattr(face, "landmarks", None)}
                bbox = fd.get("bbox")
                lms  = fd.get("landmarks")
                if bbox is None or lms is None:
                    continue
                bbox_int = [int(round(c)) for c in bbox[:4]]
                lm_arr   = np.asarray(lms, dtype=np.float64).reshape(-1, 2)[:5]
                if lm_arr.shape != (5, 2):
                    continue
                out.append((bbox_int, lm_arr))
            return out

        # Legacy API: (boxes, landmarks) ndarray tuple.
        if isinstance(result, tuple) and len(result) == 2:
            boxes, landmarks = result
            if boxes is None or len(boxes) == 0:
                return []
            out = []
            for i, box in enumerate(boxes):
                bbox_int = box[:4].astype(int).tolist()
                lm_arr   = np.asarray(landmarks[i], dtype=np.float64).reshape(-1, 2)[:5]
                out.append((bbox_int, lm_arr))
            return out

        # Anything else → unsupported, fail loudly once.
        print(f"[Retina] ⚠ Unexpected detect() return type: {type(result)}")
        return []
    except Exception as e:
        print(f"[Retina Error] {e}")
        return []

# ─── ENROLLMENT ───────────────────────────────────────────────────────────────
# Walks the student through 5 head poses, captures one InsightFace embedding
# during the "look straight" pose, and returns. Same UI flow as the previous
# proctor — only the underlying face detector changed.
def run_enrollment(cap, W, H):
    print("\n[ENROLLMENT] Starting face enrollment...")
    log_event("enrollment_started", "low", f"Session: {SESSION_ID}")

    DIRECTIONS  = [
        "Look STRAIGHT at camera",
        "Turn slightly LEFT",
        "Turn slightly RIGHT",
        "Tilt slightly UP",
        "Tilt slightly DOWN",
    ]
    SAMPLES_PER  = 15
    MAX_FRAMES   = 900   # ~30s timeout
    direction    = 0
    count        = 0
    total_frames = 0

    while direction < len(DIRECTIONS):
        total_frames += 1
        if total_frames > MAX_FRAMES:
            print("[ENROLLMENT] ⚠️ Timeout — skipping remaining directions")
            break

        ret, frame = cap.read()
        if not ret:
            print("[ENROLLMENT] ⚠️ Camera frame failed — skipping enrollment")
            break

        faces = detect_faces(frame)
        ok = len(faces) == 1

        if not HEADLESS:
            overlay = frame.copy()
            cv2.rectangle(overlay, (0,0), (W, 80),
                          (0,100,0) if ok else (0,0,150), -1)
            cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
            cv2.putText(frame, DIRECTIONS[direction],
                        (15,40), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (255,255,255), 2)
            pct = int((direction*SAMPLES_PER+count) /
                      (len(DIRECTIONS)*SAMPLES_PER) * 100)
            cv2.rectangle(frame, (0, H-20), (int(W*pct/100), H),
                          (0,255,0), -1)
            cv2.putText(frame, f"Step {direction+1}/{len(DIRECTIONS)} ({pct}%)",
                        (15, H-5), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255,255,255), 1)
            cv2.imshow("AI Proctor — Enrollment", frame)
            if cv2.waitKey(1) == 27:
                break

        if ok:
            count += 1
            # Capture face embedding at the midpoint of the "straight" pose.
            global enrolled_embedding
            if direction == 0 and count == SAMPLES_PER // 2 and \
               enrolled_embedding is None and INSIGHT_AVAILABLE:
                emb = get_face_embedding(frame)
                if emb is not None:
                    enrolled_embedding = emb
                    print("[ENROLLMENT] ✅ InsightFace embedding captured")
                    log_event("face_enrolled", "low",
                              "InsightFace embedding stored")

            if count >= SAMPLES_PER:
                print(f"[ENROLLMENT] ✅ Direction {direction+1} done")
                direction += 1
                count = 0
        else:
            count = max(0, count - 1)

        time.sleep(1.0 / TARGET_FPS)

    if not HEADLESS:
        cv2.destroyAllWindows()

    log_event("enrollment_complete", "low",
              f"Enrolled {len(DIRECTIONS)} directions")
    print("[ENROLLMENT] ✅ Complete! Starting proctoring...\n")

# ─── MAIN PROCTORING LOOP ─────────────────────────────────────────────────────
def _print_tuning_summary():
    """Dump every detection threshold to stdout exactly once at startup so
    we can confirm at a glance which version of the proctor is actually
    running on the student's machine when debugging false positives."""
    print("[PROCTOR] ┌─ Detection tuning ──────────────────────────────")
    print(f"[PROCTOR] │ gaze:      yaw>{GAZE_YAW_RAD:.2f}rad  pitch>{GAZE_PITCH_RAD:.2f}rad  "
          f"frames>{GAZE_FRAMES_NEEDED}  (medium)")
    print(f"[PROCTOR] │ gaze EXT:  yaw>{GAZE_YAW_EXTREME:.2f}rad  pitch>{GAZE_PITCH_EXTREME:.2f}rad  "
          f"frames>{GAZE_EXTREME_FRAMES}  (high)")
    print(f"[PROCTOR] │ head:      yaw>{HEAD_YAW_THRESHOLD}°  pitch>{HEAD_PITCH_THRESHOLD}°  "
          f"frames>{HEAD_FRAMES_NEEDED}  (medium)")
    print(f"[PROCTOR] │ head EXT:  yaw>{HEAD_YAW_EXTREME}°  pitch>{HEAD_PITCH_EXTREME}°  "
          f"frames>{HEAD_EXTREME_FRAMES}  (high)")
    print(f"[PROCTOR] │ face miss: {FACE_MISSING_FRAMES} frames   "
          f"warmup grace: {WARMUP_GRACE_FRAMES} frames")
    print(f"[PROCTOR] │ eyes shut: {EYES_CLOSED_FRAMES} frames   "
          f"multi-face: {MULTI_FACE_FRAMES} frames")
    print(f"[PROCTOR] │ calibration: {CALIBRATION_FRAMES} frames "
          f"(max wait {CALIBRATION_MAX_WAIT})")
    print(f"[PROCTOR] │ voice rms>{VOICE_THRESHOLD}  sustained>{VOICE_SUSTAINED_SECS}s")
    print(f"[PROCTOR] │ wrong-person cosine<{WRONG_PERSON_THRESHOLD}")
    print("[PROCTOR] └──────────────────────────────────────────────────")


# ─── CALIBRATION MODE ────────────────────────────────────────────────────────
# When PROCTOR_CALIBRATION_MODE=1 the renderer is showing a dot-calibration
# UI. proctor.py opens the camera, runs face+gaze+head detection each frame,
# and streams readings as JSON lines (prefixed "CAL:") on stdout. The Electron
# main process parses these and forwards them to the renderer via IPC.
# No violation detection, no event posting, no heartbeat.
import json as _json

def run_calibration(cap, W, H):
    """Stream face/gaze/head readings for the renderer calibration UI."""
    print("[CALIBRATION] 🎯 Streaming readings for dot calibration...")
    sys.stdout.flush()

    consecutive_failures = 0
    MAX_FAILURES = 30

    while True:
        ret, frame = cap.read()
        if not ret:
            consecutive_failures += 1
            if consecutive_failures >= MAX_FAILURES:
                print("CAL:" + _json.dumps({"error": "camera_lost"}))
                sys.stdout.flush()
                break
            time.sleep(0.05)
            continue
        consecutive_failures = 0

        faces = detect_faces(frame)
        if len(faces) != 1:
            print("CAL:" + _json.dumps({"face": False, "count": len(faces)}))
            sys.stdout.flush()
            time.sleep(0.066)
            continue

        bbox, lm_2d = faces[0]
        x1, y1, x2, y2 = bbox
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(W, x2), min(H, y2)
        face_crop = frame[y1:y2, x1:x2]

        reading = {"face": True,
                   "gaze_yaw": 0.0, "gaze_pitch": 0.0,
                   "head_yaw": 0.0, "head_pitch": 0.0}

        if GAZE_AVAILABLE and face_crop.size > 0:
            yaw, pitch = _gaze_engine.estimate(face_crop)
            reading["gaze_yaw"]   = round(float(yaw), 4)
            reading["gaze_pitch"] = round(float(pitch), 4)

        hyaw, hpitch = get_head_pose(lm_2d, W, H)
        reading["head_yaw"]   = round(float(hyaw), 2)
        reading["head_pitch"] = round(float(hpitch), 2)

        # Sanitise NaN/Inf — json.dumps raises ValueError on them
        for k, v in list(reading.items()):
            if isinstance(v, float) and (v != v or v == float('inf') or v == float('-inf')):
                reading[k] = 0.0
        print("CAL:" + _json.dumps(reading))
        sys.stdout.flush()
        time.sleep(0.066)  # ~15fps


# ── Per-frame processing helpers (extracted from run_proctoring) ───────────

def _process_yolo_results(
    state: dict,
    frame, frame_count: int, W: int, H: int,
    can_log, log_if_allowed,
) -> set:
    """Submit frame to YOLO worker and process returned detections."""
    seen_names: set = set()
    if not YOLO_AVAILABLE:
        return seen_names
    if frame_count % YOLO_EVERY_N == 0:
        yolo_worker.submit(frame, frame_count, W, H)
    yolo_result = yolo_worker.get_result(frame_count)
    if yolo_result is None:
        return seen_names
    state["_last_yolo_result"] = yolo_result
    state["_last_yolo_frame"] = frame_count
    if yolo_result.get("error"):
        print(f"[YOLO Error] {yolo_result['error']}")
        return seen_names
    detections = yolo_result["detections"]
    seen_names = {det[0] for det in detections}
    _history = state.setdefault("object_history", {})
    for name in seen_names:
        _history[name] = _history.get(name, 0) + 1
    for name in list(_history):
        if name not in seen_names:
            _history[name] = max(0, _history[name] - 1)
            if _history[name] == 0:
                del _history[name]
    for det in detections:
        name, conf = det[0], det[1]
        if _history.get(name, 0) >= YOLO_MIN_FRAMES:
            if name == "Phone" and len(det) >= 6:
                phone_box = (det[2], det[3], det[4], det[5])
                phone_type = classify_phone_position(phone_box, state.get("_last_face_bbox"), H)
                event_name = f"cheat_{phone_type}"
                severity = "critical" if phone_type == "phone_in_hand" else "high"
                details = f"{phone_type} (conf:{conf:.0%})"
            else:
                event_name = "cheat_object_detected"
                severity = "high"
                details = f"{name} detected (conf:{conf:.0%})"
            if can_log(event_name):
                log_if_allowed(event_name, severity, details)
                save_evidence(frame, event_name)
                _history[name] = 0
    return seen_names


def _process_sahi_results(
    state: dict,
    frame, frame_count: int, W: int, H: int,
    can_log, log_if_allowed,
):
    """Submit frame to SAHI worker and process returned detections."""
    if _sahi_available() and frame_count % SAHI_EVERY_N == 0:
        sahi_worker.submit(frame, frame_count)
    if not _sahi_available():
        return
    sahi_result = sahi_worker.get_result(frame_count)
    if sahi_result is None:
        return
    state["_last_sahi_result"] = sahi_result
    state["_last_sahi_frame"] = frame_count
    if sahi_result.get("error"):
        print(f"[SAHI Error] {sahi_result['error']}")
        return
    sahi_detections = sahi_result["detections"]
    sahi_seen = {det[0] for det in sahi_detections}
    # SAHI keeps its OWN accumulation history. It used to share
    # state["object_history"] with YOLO, but YOLO's decay loop drops any name
    # not in YOLO's seen-set — including SAHI-only objects (earbuds) — so a
    # YOLO pass on an overlapping frame kept knocking SAHI's count back below
    # threshold, suppressing it. Separate dicts; cross-detector duplicate events
    # are already prevented by the shared can_log() cooldown.
    _history = state.setdefault("sahi_object_history", {})
    for name in sahi_seen:
        _history[name] = _history.get(name, 0) + 1
    for name in list(_history):
        if name not in sahi_seen:
            _history[name] = max(0, _history[name] - 1)
            if _history[name] == 0:
                del _history[name]
    for det in sahi_detections:
        name, conf = det[0], det[1]
        if _history.get(name, 0) >= YOLO_MIN_FRAMES:
            if name == "Phone" and len(det) >= 6:
                phone_box = (det[2], det[3], det[4], det[5])
                phone_type = classify_phone_position(phone_box, state.get("_last_face_bbox"), H)
                event_name = f"cheat_{phone_type}"
                severity = "critical" if phone_type == "phone_in_hand" else "high"
                details = f"{phone_type} via SAHI (conf:{conf:.0%})"
            else:
                event_name = "cheat_object_detected"
                severity = "high"
                details = f"{name} via SAHI (conf:{conf:.0%})"
            if can_log(event_name):
                log_if_allowed(event_name, severity, details)
                save_evidence(frame, event_name)
                _history[name] = 0


def _process_ear_detection(
    state: dict,
    frame, num_faces: int, lm_2d, frame_count: int, W: int, H: int,
    can_log, log_if_allowed,
):
    """Run earbud ear-crop classifier."""
    if not (EAR_CLASSIFIER_AVAILABLE and _ear_classifier is not None
            and num_faces == 1 and frame_count % EAR_EVERY_N == 0):
        return
    # Own history dict — same reason as SAHI: sharing state["object_history"]
    # let YOLO's decay loop (which drops any name not in YOLO's seen-set) knock
    # the left_earbud/right_earbud counts back down before they reach threshold.
    _history = state.setdefault("ear_object_history", {})
    try:
        left_conf, right_conf = _ear_classifier.classify(frame, lm_2d, W, H)
        for side, conf in [("left_earbud", left_conf), ("right_earbud", right_conf)]:
            if conf >= EAR_THRESHOLD:
                _history[side] = _history.get(side, 0) + 1
                if _history.get(side, 0) >= 2:
                    if can_log(f"earbud_{side}"):
                        log_if_allowed("cheat_object_detected", "high",
                                  f"{side} detected (conf:{conf:.0%})")
                        save_evidence(frame, f"earbud_{side}")
                        _history[side] = 0
            else:
                _history[side] = max(0, _history.get(side, 0) - 1)
                if _history.get(side, 0) == 0:
                    _history.pop(side, None)
    except Exception:
        pass


def _process_voice_detection(
    state: dict,
    frame,
    can_log, log_if_allowed,
):
    """Sustained-voice and conversation-pattern detection."""
    if not AUDIO_AVAILABLE:
        return
    with audio_lock:
        rms = audio_rms
    now = time.time()
    voice_start_time = state.get("voice_start_time")
    if rms > VOICE_THRESHOLD:
        if voice_start_time is None:
            state["voice_start_time"] = now
        elif now - voice_start_time >= VOICE_SUSTAINED_SECS:
            if can_log("voice_detected"):
                log_if_allowed("voice_detected", "medium",
                          f"Voice sustained (rms:{rms:.3f})")
            _bursts = state.setdefault("_voice_burst_times", [])
            _bursts.append(now)
            # Bound it: this list is only reset when a full conversation pattern
            # fires, so sparse bursts that never reach that threshold would grow
            # it for the whole session. Keep the recent tail.
            if len(_bursts) > 64:
                del _bursts[:-64]
            state["voice_start_time"] = None
            _silence_start = state.get("_silence_start")
            if _silence_start is not None:
                gap = now - _silence_start
                if gap <= CONVERSATION_GAP_MAX:
                    state["_voice_burst_count"] = state.get("_voice_burst_count", 0) + 1
            if state.get("_conversation_window_start") is None:
                state["_conversation_window_start"] = now
            state["_silence_start"] = None
        # Track sustained voice
        if state.get("_sustained_voice_start") is None:
            state["_sustained_voice_start"] = now
    else:
        state["voice_start_time"] = None
        state["_sustained_voice_start"] = None
        if state.get("_silence_start") is None:
            state["_silence_start"] = now

    # Sustained voice check (20s+)
    _svs = state.get("_sustained_voice_start")
    if _svs is not None:
        sustained_duration = now - _svs
        if sustained_duration >= SUSTAINED_VOICE_SECS:
            if can_log("sustained_voice"):
                log_if_allowed("sustained_voice", "high",
                          f"Sustained audio for {sustained_duration:.0f}s (rms:{rms:.3f})")
                save_evidence(frame, "sustained_voice")
            state["_sustained_voice_start"] = now

    # Conversation pattern check
    _burst_count = state.get("_voice_burst_count", 0)
    _conv_start = state.get("_conversation_window_start")
    if _burst_count >= CONVERSATION_BURSTS and _conv_start is not None:
        window_elapsed = now - _conv_start
        if window_elapsed <= CONVERSATION_WINDOW:
            if can_log("conversation_detected"):
                log_if_allowed("conversation_detected", "high",
                          f"{_burst_count} voice bursts in {window_elapsed:.0f}s (turn-taking pattern)")
                save_evidence(frame, "conversation")
            state["_voice_burst_count"] = 0
            state["_conversation_window_start"] = None
            state["_voice_burst_times"] = []
        elif window_elapsed > CONVERSATION_WINDOW:
            state["_voice_burst_count"] = 0
            state["_conversation_window_start"] = None
            state["_voice_burst_times"] = []


def _process_behavioral(
    state: dict,
    frame,
    W: int, H: int,
    num_faces: int,
    calibrated: bool,
    gaze_yaw: float, gaze_pitch: float,
    head_yaw: float, head_pitch: float,
    can_log, log_if_allowed,
):
    """Push per-frame signals into behavioral engine and check for patterns."""
    voice_active = (audio_rms > VOICE_THRESHOLD) if AUDIO_AVAILABLE else False
    is_gaze_away = (num_faces == 1 and calibrated and
                    (abs(gaze_yaw) > GAZE_YAW_RAD or abs(gaze_pitch) > GAZE_PITCH_RAD))
    is_gaze_down = (num_faces == 1 and calibrated and gaze_pitch > GAZE_PITCH_RAD)
    is_gaze_centered = (num_faces == 1 and calibrated and
                        abs(gaze_yaw) <= GAZE_YAW_RAD * 0.5 and
                        abs(gaze_pitch) <= GAZE_PITCH_RAD * 0.5)
    is_head_turned = (num_faces == 1 and calibrated and
                      (abs(head_yaw) > HEAD_YAW_THRESHOLD or abs(head_pitch) > HEAD_PITCH_THRESHOLD))

    if is_gaze_down:
        if state.get("_gaze_down_start") is None:
            state["_gaze_down_start"] = time.time()
        gaze_down_secs = time.time() - state["_gaze_down_start"]
    else:
        state["_gaze_down_start"] = None
        gaze_down_secs = 0

    # Phone in hand — use cached YOLO/SAHI
    STALE = 30
    phone_in_hand = False
    yolo_res = state.get("_last_yolo_result")
    yolo_frm = state.get("_last_yolo_frame", 0)
    fcount = state["frame_count"]
    if YOLO_AVAILABLE and yolo_res and yolo_res.get("detections") \
       and fcount - yolo_frm < STALE:
        for det in yolo_res["detections"]:
            if det[0] == "Phone" and len(det) >= 6:
                phone_box = (det[2], det[3], det[4], det[5])
                phone_type = classify_phone_position(phone_box, state.get("_last_face_bbox"), H)
                phone_in_hand = (phone_type == "phone_in_hand")
                break
    if not phone_in_hand:
        sahi_res = state.get("_last_sahi_result")
        sahi_frm = state.get("_last_sahi_frame", 0)
        if _sahi_available() and sahi_res and sahi_res.get("detections") \
           and fcount - sahi_frm < STALE:
            for det in sahi_res["detections"]:
                if det[0] == "Phone" and len(det) >= 6:
                    phone_box = (det[2], det[3], det[4], det[5])
                    phone_type = classify_phone_position(phone_box, state.get("_last_face_bbox"), H)
                    phone_in_hand = (phone_type == "phone_in_hand")
                    break

    _behavioral.push({
        "gaze_away":      is_gaze_away,
        "gaze_down":      is_gaze_down,
        "gaze_centered":  is_gaze_centered,
        "gaze_down_secs": gaze_down_secs,
        "head_turned":    is_head_turned,
        "face_away":      is_gaze_away or is_head_turned,
        "multiple_faces": num_faces >= 2,
        "phone_in_hand":  phone_in_hand,
        "voice_active":   voice_active,
        "t":              time.monotonic(),
    })
    behavioral_match = _behavioral.check()
    if behavioral_match:
        pattern = behavioral_match["pattern"]
        severity = behavioral_match["severity"]
        detail = behavioral_match["detail"]
        conf = behavioral_match["confidence"]
        conf_base = behavioral_match.get("confidence_base", 0.75)
        full_conf = round((conf + conf_base) / 2, 2)
        if can_log(pattern):
            log_if_allowed(pattern, severity,
                      f"{detail} (behavioral confidence:{full_conf:.0%})")
            save_evidence(frame, pattern)


def _draw_hud(frame, W: int, H: int, state: dict, num_faces: int,
              gaze_away_count: int, head_away_count: int):
    """Overlay HUD text on the frame."""
    if HEADLESS:
        return
    voice_secs = int(time.time() - state.get("voice_start_time", 0)) \
        if state.get("voice_start_time") else 0
    sustained_secs = int(time.time() - state.get("_sustained_voice_start", 0)) \
        if state.get("_sustained_voice_start") else 0
    burst_count = state.get("_voice_burst_count", 0)
    conv_indicator = f" Conv:{burst_count}" if burst_count > 0 else ""
    status = (f"Faces:{num_faces} | "
              f"Gaze:{gaze_away_count}/{GAZE_FRAMES_NEEDED} | "
              f"Head:{head_away_count}/{HEAD_FRAMES_NEEDED} | "
              f"Voice:{voice_secs}s{conv_indicator}")
    if sustained_secs > 0:
        status += f" | Sustained:{sustained_secs}s"
    cv2.rectangle(frame, (0, 0), (W, 35), (20, 20, 20), -1)
    cv2.putText(frame, status, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    cv2.putText(frame, "AI PROCTOR ACTIVE", (W - 180, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.imshow("AI Proctor", frame)
    cv2.waitKey(1)


def _limit_fps(state: dict, governor, _loop_start: float) -> float:
    """Enforce target FPS via the hardware governor and track actual FPS."""
    _now = time.time()
    # Cap at governor's effective FPS
    _target = 1.0 / max(governor.effective_fps, 0.1)
    _elapsed = _now - _loop_start
    if _elapsed < _target:
        time.sleep(_target - _elapsed)
    # Track actual FPS
    _actual_fps = 1.0 / max(_now - state["_last_frame_end"], 1e-6)
    state["_last_frame_end"] = _now
    _fps_history = state.setdefault("_fps_history", deque(maxlen=30))
    _fps_history.append(_actual_fps)
    _fps_warned = state.get("_fps_warned", False)
    if state.get("frame_count", 0) % 60 == 0 and len(_fps_history) >= 15:
        _avg_fps = sum(_fps_history) / len(_fps_history)
        if _avg_fps < TARGET_FPS * 0.5 and not _fps_warned:
            print(f"[PROCTOR] ⚠️ Performance warning — avg {_avg_fps:.1f}fps "
                  f"(target {TARGET_FPS}fps). Check CPU usage or reduce detection cadence.")
            state["_fps_warned"] = True
        elif _avg_fps >= TARGET_FPS * 0.8:
            state["_fps_warned"] = False
    return _actual_fps


def _proctor_frame_init_state() -> dict:
    """Return a mutable state dict for run_proctoring."""
    return {
        "last_logged": {},
        "_violation_history": {},
        "object_history": {},
        "frame_count": 0,
        "consecutive_failures": 0,
        "_last_frame_end": time.time(),
        "_fps_warned": False,
        "lazy_enroll_done": False,
        "_last_face_bbox": None,

        "voice_start_time": None,
        "_voice_burst_times": [],
        "_sustained_voice_start": None,
        "_silence_start": None,
        "_voice_burst_count": 0,
        "_conversation_window_start": None,
        "_gaze_down_start": None,
        "_last_yolo_result": None,
        "_last_yolo_frame": 0,
        "_last_sahi_result": None,
        "_last_sahi_frame": 0,
    }


def run_proctoring(cap, W, H):
    print(f"[PROCTOR] 🟢 Monitoring LIVE — Session: {SESSION_ID}")
    _print_tuning_summary()

    # Start YOLO background worker for off-thread cheat-object detection.
    # The model loads lazily inside the worker; YOLO_AVAILABLE flips to True
    # once loading succeeds (typically 1-2 seconds). Until then the main
    # loop skips submission harmlessly.
    yolo_worker.start()
    # One-time object-detection availability signal. _load_yolo() is cached
    # (the worker reuses the same session), so forcing it here just gives an
    # immediate, diagnosable telemetry event when onnxruntime is missing or
    # the weights aren't found — instead of the detector silently no-op'ing.
    try:
        if _load_yolo() is None:
            _yolo_reason = ("onnxruntime-missing" if not ORT_AVAILABLE
                            else _MODEL_ERRORS.get("yolo", "load-failed"))
            log_event("object_detection_unavailable", "info", f"reason={_yolo_reason}")
    except Exception:
        pass
    if _sahi_available():
        sahi_worker.start()

    # We mutate _LAST_LIVE_FRAME_TS from inside the capture loop to
    # pace live-view uploads. Declared global because the variable
    # itself lives at module scope so the control thread can also
    # see / reset it if we ever need to.
    global _LAST_LIVE_FRAME_TS

    # ── Per-event sustain counters ─────────────────────────────────────────
    # Each detection only fires after its consecutive-frame threshold is
    # met — single noisy frames are ignored.
    face_missing_count  = 0
    multi_face_count    = 0
    gaze_away_count     = 0
    gaze_extreme_count  = 0
    head_away_count     = 0
    head_extreme_count  = 0
    eyes_closed_count   = 0

    # ── Per-student calibration bias ───────────────────────────────────────
    # Default to zero bias + self-calibration. If pre-set biases from the
    # renderer's dot-calibration are available AND parse cleanly, we adopt
    # them and skip self-calibration. Initializing defaults up-front means
    # a partial-failure path (e.g., YAW parses, PITCH raises) can't leave
    # any of the four bias variables unbound when the main loop reads them.
    gaze_yaw_bias   = 0.0
    gaze_pitch_bias = 0.0
    head_yaw_bias   = 0.0
    head_pitch_bias = 0.0
    cal_gaze_yaw    = []
    cal_gaze_pitch  = []
    cal_head_yaw    = []
    cal_head_pitch  = []
    calibrated      = False

    if _INITIAL_GAZE_YAW_BIAS is not None:
        try:
            gaze_yaw_bias   = float(_INITIAL_GAZE_YAW_BIAS)
            gaze_pitch_bias = float(_INITIAL_GAZE_PITCH_BIAS or 0)
            head_yaw_bias   = float(_INITIAL_HEAD_YAW_BIAS or 0)
            head_pitch_bias = float(_INITIAL_HEAD_PITCH_BIAS or 0)
            calibrated      = True
            print(f"[CALIBRATION] ✅ Using pre-set biases from dot calibration — "
                  f"gaze:({gaze_yaw_bias:+.2f},{gaze_pitch_bias:+.2f}) "
                  f"head:({head_yaw_bias:+.0f},{head_pitch_bias:+.0f})")
        except (ValueError, TypeError):
            # Reset partial assignments so we self-calibrate from scratch
            # rather than running with one good axis and three garbage ones.
            gaze_yaw_bias = gaze_pitch_bias = head_yaw_bias = head_pitch_bias = 0.0
            calibrated = False
            print("[PROCTOR] ⚠️ Invalid preset biases — falling back to self-calibration")

    # ── Shared mutable state for extracted helpers ────────────────────────
    state = _proctor_frame_init_state()
    state["lazy_enroll_done"] = not SKIP_ENROLLMENT
    state["_last_frame_end"] = time.time()

    # Per-frame constant (used in the ear-dect call below)
    LAZY_ENROLL_WINDOW = 60   # ~4 seconds at 15fps

    # ── Inner logging helpers (closures over `state`) ─────────────────────
    def can_log(etype):
        now = time.time()
        COOLDOWN = 8.0
        if now - state["last_logged"].get(etype, 0) >= COOLDOWN:
            state["last_logged"][etype] = now
            return True
        return False

    MAX_FAILURES = 30

    # Adaptive CPU governor — see class docstring at the top of the
    # file. Logs every throttle transition back to the server as a
    # `client_throttled` event so teachers can see why a student's
    # cadence dropped without it being treated as a violation.
    def _on_throttle_transition(info):
        thr_state = "throttled" if info.get("throttled") else "recovered"
        cpu_pct = info.get("cpu_pct")
        cpu_txt = f"{cpu_pct:.0f}%" if isinstance(cpu_pct, (int, float)) else "n/a"
        thermal_val = info.get("thermal")
        thermal_txt = (f"thermal={thermal_val:.2f}"
                       if isinstance(thermal_val, (int, float))
                       else "thermal=n/a")
        print(f"[PROCTOR] hardware governor {thr_state}: cpu={cpu_txt} "
              f"{thermal_txt} -> {governor.effective_fps:.1f} fps")
        try:
            log_event("client_throttled", "info",
                      f"CPU {cpu_txt}, {thermal_txt}, "
                      f"effective {governor.effective_fps:.1f} fps "
                      f"(state={thr_state})")
        except Exception:
            pass

    governor = _HardwareGovernor(on_transition=_on_throttle_transition)

    # ── Severity escalation tracking ─────────────────────────────────────
    # Tracks (timestamp, original_severity) per violation type. Escalates
    # when the same type fires repeatedly within ESCALATION_WINDOW_SECS.
    def _track_violation(etype: str, severity: str):
        now = time.time()
        cutoff = now - ESCALATION_WINDOW_SECS
        history = state["_violation_history"].get(etype, [])
        history = [(t, s) for t, s in history if t > cutoff]
        history.append((now, severity))
        state["_violation_history"][etype] = history

    def _get_escalated_severity(etype: str, base_severity: str) -> Tuple[str, int]:
        now = time.time()
        cutoff = now - ESCALATION_WINDOW_SECS
        history = state["_violation_history"].get(etype, [])
        history = [(t, s) for t, s in history if t > cutoff]
        repeat_count = len(history)
        if repeat_count >= 3:
            severity = "critical"
        elif repeat_count == 2:
            severity = ESCALATION_TIERS.get(base_severity, base_severity)
        else:
            severity = base_severity
        return severity, repeat_count

    def log_if_allowed(etype: str, base_severity: str, details: str) -> bool:
        now = time.time()
        COOLDOWN = 8.0
        if now - state["last_logged"].get(etype, 0) >= COOLDOWN:
            state["last_logged"][etype] = now
            # Record the repeat-offense history ONLY when we actually log an
            # event — NOT on every frame-level poll. log_if_allowed() is called
            # once per frame for as long as a deviation persists, so tracking
            # per-call inflated the repeat counter 15-120x within a second,
            # forcing _get_escalated_severity() straight to "critical" and
            # erasing the medium tier from the dashboard/report. Tracking per
            # logged event makes the medium→high→critical escalation reflect
            # genuine repeat offenses (one per COOLDOWN window).
            _track_violation(etype, base_severity)
            severity, repeat = _get_escalated_severity(etype, base_severity)
            if repeat > 1:
                details = f"[{repeat}x repeat] {details}"
            log_event(etype, severity, details)
            return True
        return False

    def _freeze_calibration_bias(reason: str):
        nonlocal calibrated, head_yaw_bias, head_pitch_bias, gaze_yaw_bias, gaze_pitch_bias
        if cal_head_yaw:
            head_yaw_bias   = sum(cal_head_yaw)   / len(cal_head_yaw)
            head_pitch_bias = sum(cal_head_pitch) / len(cal_head_pitch)
        if cal_gaze_yaw:
            gaze_yaw_bias   = sum(cal_gaze_yaw)   / len(cal_gaze_yaw)
            gaze_pitch_bias = sum(cal_gaze_pitch) / len(cal_gaze_pitch)
        calibrated = True
        print(f"[CALIBRATION] {reason} "
              f"({len(cal_head_yaw)} samples) — "
              f"gaze:({gaze_yaw_bias:+.2f},{gaze_pitch_bias:+.2f})rad "
              f"head:({head_yaw_bias:+.0f},{head_pitch_bias:+.0f})°")

    _rb_last_encode = 0.0   # wall-clock of the last context-buffer push

    while True:
        try:
            _loop_start = time.time()
            ret, frame = cap.read()
            if not ret:
                state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
                print(f"[PROCTOR] Frame read failed ({state['consecutive_failures']}/{MAX_FAILURES})")
                if state["consecutive_failures"] >= MAX_FAILURES:
                    print("[PROCTOR] Camera lost — too many failures!")
                    break
                time.sleep(0.05)
                continue
            state["consecutive_failures"] = 0

            # Pre-violation context buffer (RAM-only "dashcam"). Feed at ~1 Hz
            # by wall clock — NOT per loop iteration — so a throttled/slow
            # capture loop still yields ~1 context frame/sec. Cheap time gate
            # BEFORE the JPEG encode so we don't pay encode cost every frame.
            # Downscaled (longest side ≤640) at q60 → ~30-40 KB/frame in RAM.
            if _loop_start - _rb_last_encode >= 1.0:
                _rb_last_encode = _loop_start
                try:
                    _rb_h, _rb_w = frame.shape[:2]
                    _rb_scale = min(1.0, 640.0 / max(_rb_h, _rb_w))
                    _rb_img = (cv2.resize(frame, (int(_rb_w * _rb_scale), int(_rb_h * _rb_scale)),
                                          interpolation=cv2.INTER_AREA)
                               if _rb_scale < 1.0 else frame)
                    _rb_ok, _rb_jpg = cv2.imencode(".jpg", _rb_img, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    if _rb_ok:
                        _frame_ring.maybe_push(base64.b64encode(_rb_jpg.tobytes()).decode("ascii"))
                except Exception:
                    pass  # context buffer is best-effort; never break the loop

            # Live-view: if a teacher has opened the camera-feed panel for
            # this session, push one downscaled JPEG every ~1.5 s.
            if _LIVE_VIEW_ACTIVE:
                _now = time.time()
                if _now - _LAST_LIVE_FRAME_TS >= _LIVE_FRAME_INTERVAL_SEC:
                    _LAST_LIVE_FRAME_TS = _now
                    upload_live_frame(frame)

            state["frame_count"] += 1
            frame_count = state["frame_count"]

            # ── CALIBRATION TIMEOUT ──────────────────────────────────────────────
            if not calibrated and frame_count >= CALIBRATION_MAX_WAIT:
                _freeze_calibration_bias(f"⚠ timed out after {frame_count} frames")
                log_event("calibration_timeout", "low",
                          f"samples:{len(cal_head_yaw)}")

            # ── SCREEN-SHARE FEED DETECTION ──────────────────────────────────────
            if frame_count % 30 == 0:
                screen_feed = _detect_screen_share_feed(frame)
                if screen_feed and can_log("screen_share_feed"):
                    log_if_allowed("screen_share_feed", "critical",
                              f"Camera feed resembles screen capture: {screen_feed}")
                    save_evidence(frame, "screen_share_feed")

            # ── LAZY ENROLLMENT ──────────────────────────────────────────────────
            if not state["lazy_enroll_done"] and INSIGHT_AVAILABLE and not calibrated:
                emb = get_face_embedding(frame)
                if emb is not None:
                    global enrolled_embedding
                    enrolled_embedding = emb
                    state["lazy_enroll_done"] = True
                    print(f"[PROCTOR] ✅ Identity reference captured at frame {frame_count}")
                    log_event("face_enrolled", "low",
                              f"Identity reference at frame {frame_count}")
                    save_evidence(frame, "reference_frame")
                elif frame_count > LAZY_ENROLL_WINDOW:
                    state["lazy_enroll_done"] = True
                    print("[PROCTOR] ⚠ Could not capture face embedding in first "
                          f"{LAZY_ENROLL_WINDOW} frames — wrong-person check disabled")

            # ── FACE DETECTION ───────────────────────────────────────────────────
            faces = detect_faces(frame)
            num_faces = len(faces)

            # Per-frame readings used by the HUD; default to "everything fine".
            gaze_yaw   = 0.0
            gaze_pitch = 0.0
            head_yaw   = 0.0
            head_pitch = 0.0
            face_crop  = None
            lm_2d      = None

            if num_faces == 0:
                state["_last_face_bbox"] = None
                # Decay, don't hard-reset: a 0-face frame is ambiguous (camera
                # briefly occluded), so flashing a second face then blocking the
                # lens must not zero the accumulating evidence each cycle and
                # dodge the threshold. Matches the decay every other counter uses
                # below. A *single* face (the else branch) still hard-resets,
                # since one face is positive proof there aren't multiple.
                multi_face_count   = max(0, multi_face_count - 1)
                gaze_away_count    = max(0, gaze_away_count - 1)
                gaze_extreme_count = max(0, gaze_extreme_count - 2)
                eyes_closed_count  = max(0, eyes_closed_count - 1)
                head_away_count    = max(0, head_away_count - 1)
                head_extreme_count = max(0, head_extreme_count - 2)
                if frame_count < WARMUP_GRACE_FRAMES:
                    face_missing_count = 0
                else:
                    face_missing_count += 1
                    if face_missing_count >= FACE_MISSING_FRAMES and \
                       can_log("face_missing"):
                        log_event("face_missing", "high",
                                  f"No face detected for {face_missing_count} frames")
                        save_evidence(frame, "face_missing")

            elif num_faces >= 2:
                state["_last_face_bbox"] = None
                face_missing_count = 0
                multi_face_count  += 1
                if multi_face_count >= MULTI_FACE_FRAMES and \
                   can_log("multiple_faces"):
                    log_event("multiple_faces", "high",
                              f"{num_faces} faces in frame")
                    save_evidence(frame, "multiple_faces")

            else:
                face_missing_count = 0
                multi_face_count   = 0
                bbox, lm_2d = faces[0]
                x1, y1, x2, y2 = bbox
                state["_last_face_bbox"] = (x1, y1, x2, y2)
                x1 = max(0, x1); y1 = max(0, y1)
                x2 = min(W, x2); y2 = min(H, y2)
                face_crop = frame[y1:y2, x1:x2]

                # ── CONTINUOUS IDENTITY VERIFICATION (calibration phase) ─────────
                if enrolled_embedding is not None and INSIGHT_AVAILABLE and \
                   not calibrated:
                    current_emb = get_face_embedding_from_crop(face_crop)
                    if current_emb is not None:
                        similarity = float(np.dot(enrolled_embedding, current_emb))
                        if similarity < WRONG_PERSON_THRESHOLD:
                            print(f"[IDENTITY] ❌ Different person during "
                                  f"calibration! (similarity: {similarity:.2f})")
                            log_event("calibration_abort", "critical",
                                      f"Identity swap during calibration "
                                      f"(similarity: {similarity:.2f})")
                            save_evidence(frame, "calibration_abort")
                            calibrated = False
                            cal_gaze_yaw.clear()
                            cal_gaze_pitch.clear()
                            cal_head_yaw.clear()
                            cal_head_pitch.clear()
                            state["frame_count"] = 0
                            enrolled_embedding = current_emb
                            print("[IDENTITY] ⚠ Reference updated to new face — "
                                  "recalibrating...")

                # Face too small
                fh, fw = face_crop.shape[:2]
                if fh < FACE_MIN_SIZE or fw < FACE_MIN_SIZE:
                    face_missing_count += 1
                    if face_missing_count >= FACE_MISSING_FRAMES and \
                       can_log("face_too_small"):
                        log_event("face_too_small", "medium",
                                  f"Face too small ({fh}x{fw}px, min {FACE_MIN_SIZE}px)")
                        save_evidence(frame, "face_too_small")

                # ── GAZE ─────────────────────────────────────────────────────────
                if GAZE_AVAILABLE and face_crop.size > 0:
                    gaze_yaw_raw, gaze_pitch_raw = _gaze_engine.estimate(face_crop)
                    gaze_yaw   = gaze_yaw_raw   - gaze_yaw_bias
                    gaze_pitch = gaze_pitch_raw - gaze_pitch_bias
                    is_extreme = (abs(gaze_yaw)   > GAZE_YAW_EXTREME or
                                  abs(gaze_pitch) > GAZE_PITCH_EXTREME)
                    is_away    = (abs(gaze_yaw)   > GAZE_YAW_RAD or
                                  abs(gaze_pitch) > GAZE_PITCH_RAD)
                    if not calibrated:
                        cal_gaze_yaw.append(gaze_yaw_raw)
                        cal_gaze_pitch.append(gaze_pitch_raw)
                        is_extreme = False
                        is_away    = False

                    if is_extreme:
                        gaze_extreme_count += 2
                        gaze_away_count    += 1
                    elif is_away:
                        gaze_away_count    += 1
                        gaze_extreme_count = max(0, gaze_extreme_count - 1)
                    else:
                        gaze_away_count    = max(0, gaze_away_count - 1)
                        gaze_extreme_count = max(0, gaze_extreme_count - 2)

                    if frame_count % 60 == 0:
                        print(f"[Gaze Debug] yaw:{gaze_yaw:+.2f}rad "
                              f"pitch:{gaze_pitch:+.2f}rad "
                              f"normal:{gaze_away_count}/{GAZE_FRAMES_NEEDED} "
                              f"extreme:{gaze_extreme_count}/{GAZE_EXTREME_FRAMES}")

                    if gaze_extreme_count >= GAZE_EXTREME_FRAMES:
                        direction = _dominant_direction(
                            gaze_yaw, gaze_pitch, GAZE_YAW_RAD, GAZE_PITCH_RAD)
                        if log_if_allowed("gaze_away", "high",
                                   f"Looking off-screen {direction} "
                                   f"(yaw:{gaze_yaw:+.2f}rad pitch:{gaze_pitch:+.2f}rad EXTREME)"):
                            save_evidence(frame, "gaze_away")
                            gaze_away_count    = 0
                            gaze_extreme_count = 0
                    elif gaze_away_count >= GAZE_FRAMES_NEEDED:
                        direction = _dominant_direction(
                            gaze_yaw, gaze_pitch, GAZE_YAW_RAD, GAZE_PITCH_RAD)
                        if log_if_allowed("gaze_away", "medium",
                                   f"Looking {direction} "
                                   f"(yaw:{gaze_yaw:+.2f}rad pitch:{gaze_pitch:+.2f}rad)"):
                            save_evidence(frame, "gaze_away")
                            gaze_away_count = 0

                # ── HEAD POSE ────────────────────────────────────────────────────
                head_yaw_raw, head_pitch_raw = get_head_pose(lm_2d, W, H)
                head_yaw   = head_yaw_raw   - head_yaw_bias
                head_pitch = head_pitch_raw - head_pitch_bias
                head_is_extreme = (abs(head_yaw)   > HEAD_YAW_EXTREME or
                                   abs(head_pitch) > HEAD_PITCH_EXTREME)
                head_is_away    = (abs(head_yaw)   > HEAD_YAW_THRESHOLD or
                                   abs(head_pitch) > HEAD_PITCH_THRESHOLD)
                if not calibrated:
                    cal_head_yaw.append(head_yaw_raw)
                    cal_head_pitch.append(head_pitch_raw)
                    head_is_extreme = False
                    head_is_away    = False

                if head_is_extreme:
                    head_extreme_count += 2
                    head_away_count    += 1
                elif head_is_away:
                    head_away_count    += 1
                    head_extreme_count = max(0, head_extreme_count - 1)
                else:
                    head_away_count    = max(0, head_away_count - 1)
                    head_extreme_count = max(0, head_extreme_count - 2)

                if head_extreme_count >= HEAD_EXTREME_FRAMES:
                    direction = _dominant_direction(
                        head_yaw, head_pitch, HEAD_YAW_THRESHOLD, HEAD_PITCH_THRESHOLD)
                    if log_if_allowed("head_turned", "high",
                              f"Head turned {direction} "
                              f"(yaw:{head_yaw:+.0f}° pitch:{head_pitch:+.0f}° EXTREME)"):
                        save_evidence(frame, "head_turned")
                        head_away_count    = 0
                        head_extreme_count = 0
                elif head_away_count >= HEAD_FRAMES_NEEDED:
                    direction = _dominant_direction(
                        head_yaw, head_pitch, HEAD_YAW_THRESHOLD, HEAD_PITCH_THRESHOLD)
                    if log_if_allowed("head_turned", "medium",
                              f"Head turned {direction} "
                              f"(yaw:{head_yaw:+.0f}° pitch:{head_pitch:+.0f}°)"):
                        save_evidence(frame, "head_turned")
                        head_away_count = 0

                # ── EYES OPEN/CLOSED ─────────────────────────────────────────────
                eyes_open = eyes_detected(face_crop)
                if not eyes_open:
                    eyes_closed_count += 1
                else:
                    eyes_closed_count = max(0, eyes_closed_count - 2)

                if eyes_closed_count >= EYES_CLOSED_FRAMES:
                    if log_if_allowed("eyes_closed", "high", "Eyes closed"):
                        save_evidence(frame, "eyes_closed")

                # ── CALIBRATION FREEZE ───────────────────────────────────────────
                if not calibrated and len(cal_head_yaw) >= CALIBRATION_FRAMES:
                    _freeze_calibration_bias(f"✅ baseline frozen after {len(cal_head_yaw)} frames")
                    log_event("calibration_complete", "low",
                              f"gaze yaw:{gaze_yaw_bias:+.2f}rad "
                              f"pitch:{gaze_pitch_bias:+.2f}rad | "
                              f"head yaw:{head_yaw_bias:+.0f}° "
                              f"pitch:{head_pitch_bias:+.0f}°")

                # ── HUD: draw bbox + landmarks ───────────────────────────────────
                if not HEADLESS:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    for px, py in lm_2d.astype(int):
                        cv2.circle(frame, (px, py), 2, (0, 255, 255), -1)

            # ── YOLO OBJECT DETECTION (background thread) ────────────────────────
            yolo_seen = _process_yolo_results(state, frame, frame_count, W, H,
                                              can_log, log_if_allowed)

            # ── SAHI TILED DETECTION (small objects) ─────────────────────────────
            _process_sahi_results(state, frame, frame_count, W, H,
                                  can_log, log_if_allowed)

            # ── EAR-CROP CLASSIFIER (earbud detection) ───────────────────────────
            _process_ear_detection(state, frame, num_faces, lm_2d, frame_count, W, H,
                                   can_log, log_if_allowed)

            # ── VOICE DETECTION ──────────────────────────────────────────────────
            _process_voice_detection(state, frame, can_log, log_if_allowed)

            # ── WRONG PERSON CHECK (post-calibration safety net) ─────────────────
            if enrolled_embedding is not None and INSIGHT_AVAILABLE and \
               frame_count % WRONG_PERSON_CHECK_FREQ == 0 and calibrated:
                if face_crop is not None:
                    current_emb = get_face_embedding_from_crop(face_crop)
                else:
                    current_emb = get_face_embedding(frame)
                if current_emb is not None:
                    similarity = float(np.dot(enrolled_embedding, current_emb))
                    if similarity < WRONG_PERSON_THRESHOLD and \
                       can_log("wrong_person"):
                        log_if_allowed("wrong_person", "medium",
                                  f"Different person detected "
                                  f"(cosine similarity: {similarity:.2f})")
                        save_evidence(frame, "wrong_person")

            # ── BEHAVIORAL ANALYSIS (multi-signal correlation) ─────────────────
            _process_behavioral(state, frame, W, H, num_faces, calibrated,
                                gaze_yaw, gaze_pitch, head_yaw, head_pitch,
                                can_log, log_if_allowed)

            # ── HUD ──────────────────────────────────────────────────────────────
            _draw_hud(frame, W, H, state, num_faces, gaze_away_count, head_away_count)

            # ── FPS LIMITER ──────────────────────────────────────────────────────
            governor.maybe_update()
            _limit_fps(state, governor, _loop_start)
            state["loop_errors"] = 0  # full clean iteration → reset consecutive-error count
        except (KeyboardInterrupt, SystemExit):
            raise  # clean shutdown must propagate to main()
        except Exception as _loop_exc:
            # Isolate per-frame errors: one bad frame (transient cv2/
            # numpy/detector hiccup) must not tear down the whole exam
            # session. Log rate-limited, skip the frame, keep going.
            state["loop_errors"] = state.get("loop_errors", 0) + 1
            _n = state["loop_errors"]
            if _n <= 3 or _n % 50 == 0:
                print(f"[PROCTOR] ⚠ frame-loop error #{_n}: "
                      f"{type(_loop_exc).__name__}: {_loop_exc}")
            if _n >= 60:
                print("[PROCTOR] ❌ persistent frame-loop errors — exiting for a clean restart")
                break  # bounded by python-manager restart cap
            time.sleep(0.03)
            continue

# ─── READINESS / DIAGNOSTICS ───────────────────────────────────────────────────
def _compute_proctoring_tier(models: dict) -> dict:
    """Map model availability → a proctoring TIER. Phase 1.6: this formalises
    the existing per-model graceful degradation into an explicit, observable
    contract. The exam ALWAYS starts (a single unavailable model never blocks
    it — each detector is guarded at its call site); the tier only describes
    how much on-device analysis is live so coverage degrades instead of the
    exam failing.

        full     — face detection + object + gaze + identity all loaded.
        reduced  — face detection loaded but one+ secondary detector is down.
        minimal  — face detection itself is down; only motion/audio heuristics.

    A missing CAMERA is the one hard stop (handled in main()): with no frames
    there is nothing to proctor, so that case exits rather than degrades."""
    face = bool(models.get("retina"))
    secondary = ("yolo", "gaze", "insightface", "ear")
    if not face:
        tier = "minimal"
    elif all(models.get(k) for k in secondary):
        tier = "full"
    else:
        tier = "reduced"
    # `missing` lists only the models the TIER is computed from, so it can't
    # contradict the tier (the old all-models version produced e.g. tier:"full"
    # with missing:["eyes"]). onnxruntime is omitted — when it's down the
    # detectors that depend on it already appear here, and its own failure is
    # still reported separately via model_errors.
    considered = ("retina",) + secondary
    return {
        "tier": tier,
        "missing": sorted(k for k in considered if not models.get(k)),
    }


def _collect_readiness() -> dict:
    """Privacy-safe readiness snapshot. METADATA ONLY — model availability
    flags, error classes, interpreter/OS/arch, audio-model file presence.
    NEVER frames, audio, or identity. This is the boundary that lets us see
    *why* an on-device boot failed server-side without ever shipping media
    off the student's machine — keep it that way.

    Reused by --selftest, the proctor_boot diagnostic event, and the
    pre-exam System Check (1.4)."""
    try:
        ort_version = ort.__version__ if ORT_AVAILABLE else None
    except Exception:
        ort_version = None

    def _present(p: str) -> bool:
        try:
            return bool(p) and os.path.exists(p)
        except Exception:
            return False

    models = {
        "retina": RETINA_AVAILABLE,
        "onnxruntime": ORT_AVAILABLE,
        "yolo": YOLO_AVAILABLE,
        "gaze": GAZE_AVAILABLE,
        "ear": EAR_CLASSIFIER_AVAILABLE,
        "insightface": INSIGHT_AVAILABLE,
        "eyes": EYES_AVAILABLE,
    }

    return {
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "arch": platform.machine(),
        "ort_version": ort_version,
        "models": models,
        "proctoring": _compute_proctoring_tier(models),
        "audio_models": {
            "vosk_en": _present(os.environ.get("PROCTOR_VOSK_EN_MODEL", "")),
            "vosk_hi": _present(os.environ.get("PROCTOR_VOSK_HI_MODEL", "")),
            "silero_vad": _present(os.environ.get("PROCTOR_SILERO_VAD", "")),
        },
        "model_errors": dict(_MODEL_ERRORS),
    }


def run_selftest() -> int:
    """Initialise every model, print a JSON readiness report to stdout, exit.
    No camera, no proctoring loop, no event POST. Used by Phase 0 diagnosis,
    the System Check (1.4), and CI. Returns a process exit code (0 = the
    critical detectors loaded; 1 = a hard dependency like onnxruntime is
    missing so proctoring would run badly degraded)."""
    # YOLO loads lazily — exercise it so the report reflects reality.
    try:
        _load_yolo()
    except Exception as e:
        _MODEL_ERRORS["yolo"] = type(e).__name__
    report = _collect_readiness()
    # Marker prefix so the Electron side can grep one line out of stdout.
    print("SELFTEST:" + _json.dumps(report))
    sys.stdout.flush()
    # onnxruntime underpins gaze/yolo/ear; uniface underpins face detection.
    # Either missing means a materially degraded exam, so flag non-zero.
    return 0 if (report["models"]["onnxruntime"] and report["models"]["retina"]) else 1


def _camera_backend_candidates():
    system = platform.system()
    candidates = []
    if system == "Windows":
        candidates.extend([
            ("DSHOW", getattr(cv2, "CAP_DSHOW", None)),
            ("MSMF", getattr(cv2, "CAP_MSMF", None)),
        ])
    elif system == "Darwin":
        candidates.append(("AVFOUNDATION", getattr(cv2, "CAP_AVFOUNDATION", None)))
    elif system == "Linux":
        candidates.append(("V4L2", getattr(cv2, "CAP_V4L2", None)))
    candidates.append(("DEFAULT", None))

    deduped = []
    seen = set()
    for name, backend in candidates:
        key = (name, backend)
        if backend is not None and not isinstance(backend, int):
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append((name, backend))
    return deduped


def _frame_is_usable(frame) -> bool:
    """True if a frame looks like a real, lit COLOR image — not the near-black
    or grayscale output of an IR / Windows-Hello camera. _open_camera uses this
    to skip the IR camera on dual-camera laptops: it streams fine (so the old
    'has frames' check accepted it), but its mono/dark frames yield no face, so
    gaze calibration sat forever on 'No face detected'."""
    try:
        if frame is None or getattr(frame, "size", 0) == 0:
            return False
        if getattr(frame, "ndim", 0) < 3 or frame.shape[2] < 3:
            return False  # single-channel feed = IR / grayscale
        b = float(frame[:, :, 0].mean())
        g = float(frame[:, :, 1].mean())
        r = float(frame[:, :, 2].mean())
        if (r + g + b) / 3.0 < 12.0:                        # essentially black (shutter / IR, no illum)
            return False
        if max(abs(r - g), abs(g - b), abs(r - b)) < 3.0:   # R≈G≈B → grayscale / IR
            return False
        return True
    except Exception:
        return True  # never reject on a probe error — degrade to "usable"


def _camera_probe(cap, attempts: int = 12):
    """Read up to `attempts` frames (first ones are often blank on Windows).
    Returns (had_any_frame, had_usable_color_frame)."""
    had = False
    for _ in range(attempts):
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0) > 0:
            had = True
            if _frame_is_usable(frame):
                return True, True
        time.sleep(0.03)
    return had, False


def _open_camera():
    """Open a real camera, PREFERRING one that yields a usable color frame.

    Browser pre-checks use Chromium's camera stack; proctoring uses OpenCV. Two
    problems this handles: (1) on Windows the default backend can fail while
    MSMF/DSHOW succeeds, so each candidate is frame-tested, not trusted on
    isOpened(); (2) dual-camera (RGB+IR, Windows Hello) laptops enumerate the IR
    camera too — it streams but its mono/dark frames never yield a face, and the
    old 'first camera with any frames' rule grabbed it. So take the FIRST camera
    with a usable COLOR frame, and fall back to any camera that at least streams
    only if none qualify (a dark room / mono webcam is better proctored than not
    at all). PROCTOR_CAMERA_INDEX forces a specific index as an escape hatch.
    """
    forced = os.environ.get("PROCTOR_CAMERA_INDEX", "").strip()
    indices = [int(forced)] if forced.isdigit() else [0, 1, 2]
    fallback = None  # (idx, backend_name, backend) — first cam with ANY frames

    for idx in indices:
        for backend_name, backend in _camera_backend_candidates():
            cap = None
            try:
                print(f"[CAM] Trying camera index={idx} backend={backend_name}")
                cap = cv2.VideoCapture(idx) if backend is None else cv2.VideoCapture(idx, backend)
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                had, usable = _camera_probe(cap)
                if usable:
                    print(f"[CAM] Ready (usable color) index={idx} backend={backend_name}")
                    return cap, {"index": idx, "backend": backend_name, "usable": True}
                if had and fallback is None:
                    fallback = (idx, backend_name, backend)
                cap.release()
            except Exception as exc:
                print(f"[CAM] Failed index={idx} backend={backend_name}: "
                      f"{type(exc).__name__}: {exc}")
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass

    if fallback is not None:
        idx, backend_name, backend = fallback
        print(f"[CAM] No usable color camera — falling back to index={idx} backend={backend_name}")
        cap = cv2.VideoCapture(idx) if backend is None else cv2.VideoCapture(idx, backend)
        if cap is not None and cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            return cap, {"index": idx, "backend": backend_name, "usable": False}
    return None, None


def _open_camera_retry(total_timeout: float = 4.0):
    """Open the camera, retrying briefly to absorb a cross-process handoff race.

    The browser (Chromium getUserMedia, used by ID verification) may still be
    releasing the webcam when this separate OpenCV process starts. On macOS the
    device is exclusive, so the first _open_camera() pass can find no frames
    purely because the previous holder hasn't let go yet. Retrying for a short
    bounded window lets the common handoff race self-heal without depending on a
    fixed sleep guess on the renderer side.

    Shutdown-safe by design: this catches NOTHING. KeyboardInterrupt (SIGINT,
    sent by stopCalibration) and SystemExit (SIGTERM via _handle_sigterm) are
    BaseExceptions, so they propagate straight out and a stop / panic-mode
    teardown during the wait still exits the process immediately — the retry
    never delays shutdown. _open_camera() already releases any cap it can't use,
    so retrying leaks no camera FD. If the whole window elapses we return
    (None, None), leaving main()'s existing failure path (event POST + exit 1)
    exactly as before — only reached up to ~total_timeout later.
    """
    deadline = time.monotonic() + max(0.0, total_timeout)
    attempt = 0
    while True:
        attempt += 1
        cap, meta = _open_camera()
        if cap is not None and cap.isOpened():
            if attempt > 1:
                print(f"[CAM] Acquired on retry attempt {attempt}")
            return cap, meta
        if time.monotonic() >= deadline:
            return None, None
        print(f"[CAM] No camera yet (attempt {attempt}) — likely still held by "
              f"the browser; retrying…")
        time.sleep(0.4)


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print(f"[PROCTOR] Session: {SESSION_ID}")
    print(f"[PROCTOR] Server:  {SERVER_URL}")
    print(f"[PROCTOR] Headless: {HEADLESS}")

    # Privacy-safe boot diagnostic: model flags + versions + OS/arch, through
    # the existing event pipeline (POST /api/v1/event). METADATA ONLY — never
    # media or identity. Makes on-device boot observable server-side.
    # Eagerly load YOLO before the boot snapshot (exam mode only) so the
    # readiness/tier/model_load_failed reflect REALITY. The object detector
    # otherwise loads lazily inside YoloWorker._run (started later in the
    # proctoring loop), so this boot snapshot ran while YOLO_AVAILABLE was still
    # False → it reported yolo:false + a FALSE "reduced" tier + a spurious
    # model_load_failed:yolo on every exam, and the phone wasn't detectable until
    # the worker caught up. Calibration doesn't use objects, so skip it there.
    # Boot diagnostic + tier are emitted in EXAM mode only. Calibration
    # deliberately skips _load_yolo() (dot-calibration doesn't use the object
    # detector), so emitting a snapshot here would POST a FALSE yolo:false +
    # "reduced" tier + missing:["yolo"] for the session — even though the
    # detector loads fine the moment the real exam proctor starts. Calibration
    # reports its own readings via CAL:, so it needs no proctor_boot.
    if not CALIBRATION_MODE:
        try:
            _load_yolo()
        except Exception as _ye:
            _MODEL_ERRORS["yolo"] = type(_ye).__name__
        try:
            _boot = _collect_readiness()
            log_event("proctor_boot", "low", _json.dumps(_boot))
            for _name, _ok in _boot["models"].items():
                if not _ok:
                    log_event("model_load_failed", "low",
                              _json.dumps({"model": _name,
                                           "error": _MODEL_ERRORS.get(_name, "unavailable")}))
            # Phase 1.6 — surface the active proctoring tier (full/reduced/minimal)
            # so a degraded-but-running exam is visible to teachers (and, via the
            # System Check, to the student) instead of silently losing coverage.
            # Low severity: a reduced tier is informational, not a violation.
            _tier = _boot.get("proctoring", {})
            print(f"[PROCTOR] Proctoring tier: {_tier.get('tier', 'unknown')} "
                  f"(missing: {', '.join(_tier.get('missing', [])) or 'none'})")
            log_event("proctoring_tier", "low", _json.dumps(_tier))
        except Exception as _be:
            print(f"[PROCTOR] boot diagnostic skipped: {_be}")

    cap, cam_meta = _open_camera_retry()
    if cap is None or not cap.isOpened():
        try:
            _session().post(SERVER_URL, json=dict(
                session_id = SESSION_ID,
                event_type = "proctor_camera_failed",
                severity   = "high",
                details    = "Cannot open any camera — proctoring disabled"
            ), timeout=3)
        except Exception:
            pass
        if CALIBRATION_MODE:
            print("CAL:" + _json.dumps({
                "error": "camera_open_failed",
                "detail": "OpenCV could not open a webcam. Close other camera apps and retry."
            }), flush=True)
        print("[PROCTOR] ❌ Cannot open camera!")
        sys.exit(1)

    # Everything past this point HOLDS the camera. Wrap the whole body in
    # try/finally so a SIGINT/SIGTERM (or any exception) during warmup,
    # audio start, the system check, or enrollment ALSO releases the camera
    # and workers. Previously only run_proctoring() was guarded, so a signal
    # during those earlier phases leaked the camera FD (released only when
    # the OS reaped the process) — blocking the next proctor/exam launch.
    try:
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if W == 0 or H == 0:
            W, H = 640, 480
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
            print(f"[PROCTOR] Camera returned 0x0 — forcing {W}x{H}")
        _cam_desc = f"{cam_meta.get('backend')} index {cam_meta.get('index')}" \
            if cam_meta else "unknown backend"
        print(f"[PROCTOR] Camera: {W}x{H} ({_cam_desc})")

        if CALIBRATION_MODE:
            print("[PROCTOR] Warming up camera for calibration...")
            for _ in range(10):
                cap.read()
            time.sleep(0.5)
            try:
                run_calibration(cap, W, H)
            except KeyboardInterrupt:
                print("\n[CALIBRATION] Stopped by signal")
            print("[CALIBRATION] Done")
            return  # the outer finally still releases the camera + workers

        # First few frames are often blank, especially on Windows.
        # ── Lazy VM + virtual camera detection ───────────────────────────
        # Run after camera open (non-blocking at import time). Results are
        # logged via normal event pipeline.
        global _virtual_camera_name, _vm_name
        _virtual_camera_name = _detect_virtual_camera()
        if _virtual_camera_name:
            print(f"[VIRTUAL CAM] ⚠ Virtual camera detected: '{_virtual_camera_name}'")
            log_event("virtual_camera_detected", "critical",
                      f"Virtual webcam: {_virtual_camera_name}")
        else:
            print("[VIRTUAL CAM] ✅ Physical webcam confirmed")
        _vm_name = _detect_vm()
        if _vm_name:
            print(f"[VM DETECT] ⚠ Virtual machine indicator found: '{_vm_name}'")
            log_event("vm_detected", "high", f"VM indicator: {_vm_name}")

        print("[PROCTOR] Warming up camera...")
        for _ in range(10):
            cap.read()
        time.sleep(0.5)

        # ── Start audio analysis ────────────────────────────────────────────
        _start_audio()

        # ── Pre-exam system check (runs after camera is ready) ────────────────
        print("[PROCTOR] Running pre-exam system check...")
        check_results = run_system_check(cap=cap)
        try:
            _session().post(SYSTEM_CHECK_URL, json=check_results, timeout=5)
            print(f"[PROCTOR] System check: {check_results['overall'].upper()}")
            for name, result in check_results["checks"].items():
                icon = "✅" if result["status"] == "pass" else "⚠️" if result["status"] == "warn" else "❌"
                print(f"  {icon} {name}: {result['detail']}")
        except Exception:
            pass  # Server may not have the endpoint yet — non-fatal

        if HEADLESS or SKIP_ENROLLMENT:
            reason = "headless mode" if HEADLESS else "renderer handled enrollment"
            print(f"[ENROLLMENT] Skipping UI phase — {reason}")
            print("[ENROLLMENT] Face embedding will be captured on first clear frame.")
            log_event("enrollment_complete", "low", f"Skipped: {reason}")
        else:
            run_enrollment(cap, W, H)

        try:
            run_proctoring(cap, W, H)
        except KeyboardInterrupt:
            print("\n[PROCTOR] Stopped by signal")
        except SystemExit:
            print("\n[PROCTOR] Stopped by SIGTERM")
    finally:
        try: yolo_worker.stop()
        except Exception: pass
        try:
            if _sahi_available():
                sahi_worker.stop()
        except Exception: pass
        try: cap.release()
        except Exception: pass
        try: _reset_ws()
        except Exception: pass
        # Drain the async backlog, THEN deliver the terminal session_ended
        # SYNCHRONOUSLY before closing _http. The async uploader may be mid-
        # backoff and _http is about to close, so the queue+flush path could
        # silently lose the final event on a transient blip; a short bounded
        # retry lands it without hanging exit on a real outage.
        try: _flush_events()
        except Exception: pass
        if not CALIBRATION_MODE:
            try:
                duration = int(time.time() - session_start)
                _conf = CONFIDENCE.get("session_ended", 0.75)
                with _violation_lock:
                    _vc = violation_count
                _ended = dict(
                    session_id=SESSION_ID, event_type="session_ended", severity="low",
                    details=(f"violations:{_vc} | duration:{duration}s "
                             f"| confidence:{int(_conf * 100)}%"))
                if not _post_event_now(_ended):
                    print("[PROCTOR] ⚠ session_ended not delivered — server will reconcile")
            except Exception: pass
        # Thread-local sessions need no explicit cleanup.
        try: _cleanup_evidence_dir()
        except Exception: pass
        if not HEADLESS:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        print("[PROCTOR] ✅ Session ended")

def _handle_sigterm(signum, frame):
    print("\n[PROCTOR] SIGTERM received — shutting down")
    sys.exit(0)

if __name__ == "__main__":
    # --selftest: readiness report only (no camera / proctoring / event POST).
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    signal.signal(signal.SIGTERM, _handle_sigterm)
    main()

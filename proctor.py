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
import threading
import requests
import cv2
import numpy as np
from collections import deque
from datetime import datetime
from queue import Queue, Empty
from typing import Optional, Tuple

# ─── OPTIONAL DETECTORS ───────────────────────────────────────────────────────
# Each heavy dep is wrapped in a try/except so a missing model file or
# broken install can never crash proctor.py — it degrades to whatever
# detectors are still available.

# uniface: face detection + 5 landmarks (ONNX RetinaFace under the hood)
try:
    from uniface import RetinaFace
    _retina = RetinaFace()
    RETINA_AVAILABLE = True
    print("[Retina] ✅ Ready")
except Exception as _re:
    print(f"[Retina] ❌ Not available: {_re} — face detection disabled")
    RETINA_AVAILABLE = False
    _retina = None

# onnxruntime: gaze direction model. Loaded lazily by GazeEstimator below.
try:
    import onnxruntime as ort
    ORT_AVAILABLE = True
except Exception as _oe:
    print(f"[ONNX] ❌ Not available: {_oe} — gaze direction disabled")
    ORT_AVAILABLE = False

# ultralytics YOLO: cheat object detection — loaded lazily to avoid
# blocking proctor startup and to keep memory footprint low on 2GB droplets.
_yolo_model = None
YOLO_AVAILABLE = False
_YOLO_LOCK = threading.Lock()

def _load_yolo():
    """Load YOLOv8 model on demand. Thread-safe. Auto-detects GPU."""
    global _yolo_model, YOLO_AVAILABLE
    with _YOLO_LOCK:
        if _yolo_model is not None or YOLO_AVAILABLE:
            return _yolo_model
        try:
            from ultralytics import YOLO  # noqa
            print("[YOLO] Loading model (lazy)...")
            _yolo_model = YOLO("yolov8n.pt")

            # Auto-detect GPU: CUDA (NVIDIA) > MPS (Apple Silicon) > CPU
            device = "cpu"
            try:
                import torch
                if torch.cuda.is_available():
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
            except Exception:
                pass

            if device != "cpu":
                _yolo_model.to(device)
                print(f"[YOLO]  Using {device.upper()} acceleration")

            YOLO_AVAILABLE = True
            print("[YOLO] Ready")
            return _yolo_model
        except Exception as _ye:
            print(f"[YOLO] Not available: {_ye}")
            YOLO_AVAILABLE = False
            return None


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

    def submit(self, frame: np.ndarray, frame_count: int):
        """Queue a frame for YOLO inference (non-blocking)."""
        try:
            # Downscale before sending to the worker — saves queue memory
            # and keeps the worker's cv2.resize call (which we removed)
            # from being duplicated.
            small = cv2.resize(frame, (416, 416))
            self.frame_q.put_nowait((small.copy(), frame_count))
        except Exception:
            pass  # queue full, skip this frame

    def get_result(self, frame_count: int):
        """Check if a result is available for the given frame count.

        Returns the result dict or None. Results older than the requested
        frame_count are silently discarded.
        """
        try:
            result = self.result_q.get_nowait()
            if result["frame_count"] <= frame_count:
                return result
            # Future result — put it back and return None
            self.result_q.put_nowait(result)
        except Empty:
            pass
        return None

    def _run(self):
        model = _load_yolo()
        if model is None:
            return

        while not self._stop.is_set():
            try:
                small, frame_count = self.frame_q.get(timeout=0.5)
            except Empty:
                continue

            try:
                res = model(small, verbose=False, conf=YOLO_CONFIDENCE)[0]
                detections = []
                for box in res.boxes:
                    cls_id = int(box.cls[0])
                    if cls_id in CHEAT_IDS:
                        detections.append((CHEAT_IDS[cls_id], float(box.conf[0])))
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

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SESSION_ID   = os.getenv("PROCTOR_SESSION_ID",  "test-session")
SERVER_URL   = os.getenv("PROCTOR_SERVER_URL",  "http://localhost:8000/event")
EVIDENCE_DIR = os.getenv("PROCTOR_EVIDENCE_DIR", "/tmp/evidence")
JWT_TOKEN    = os.getenv("PROCTOR_JWT_TOKEN",   "")

# Derive the analyze-frame endpoint from SERVER_URL. Same host, same auth.
# This is what makes evidence screenshots show up in the teacher's forensics
# timeline — without it the only screenshot the server ever sees is the
# single reference frame the renderer uploads during enrollment.
EVIDENCE_UPLOAD_URL = SERVER_URL.replace("/event", "/api/v1/analyze-frame")
HEADLESS          = platform.system() == "Windows" or \
                    os.environ.get("PROCTOR_HEADLESS","0") == "1"
SKIP_ENROLLMENT   = os.environ.get("PROCTOR_SKIP_ENROLLMENT","0") == "1"
CALIBRATION_MODE  = os.environ.get("PROCTOR_CALIBRATION_MODE","0") == "1"

# Pre-set biases from renderer dot-calibration (skip self-calibration if present)
_PRESET_GAZE_YAW_BIAS  = os.environ.get("PROCTOR_GAZE_YAW_BIAS")
_PRESET_GAZE_PITCH_BIAS = os.environ.get("PROCTOR_GAZE_PITCH_BIAS")
_PRESET_HEAD_YAW_BIAS  = os.environ.get("PROCTOR_HEAD_YAW_BIAS")
_PRESET_HEAD_PITCH_BIAS = os.environ.get("PROCTOR_HEAD_PITCH_BIAS")

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
VOICE_THRESHOLD     = float(os.getenv("PROCTOR_VOICE_THRESHOLD", "0.035"))
VOICE_SUSTAINED_SECS = 8.0
WRONG_PERSON_THRESHOLD = float(os.getenv("PROCTOR_WRONG_PERSON_THRESHOLD", "0.25"))

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

# ─── SERVER LOGGING ───────────────────────────────────────────────────────────
session_start = time.time()
violation_count = 0

HEADERS = {
    "Content-Type": "application/json",
    **({"Authorization": f"Bearer {JWT_TOKEN}"} if JWT_TOKEN else {}),
}

HEARTBEAT_URL = SERVER_URL.replace("/event", "/heartbeat")

def _heartbeat_loop():
    while True:
        time.sleep(30)
        try:
            requests.post(
                HEARTBEAT_URL,
                json={"session_id": SESSION_ID, "event_type": "heartbeat",
                      "severity": "low", "details": "alive"},
                timeout=5, headers=HEADERS
            )
        except Exception:
            pass

threading.Thread(target=_heartbeat_loop, daemon=True).start()

# ─── ON-DEMAND LIVE CAMERA STREAM ─────────────────────────────────────────────
# When a teacher clicks "View camera" on the dashboard, the server flips a
# per-session Redis flag. This thread polls that flag every 2s; while it's
# set, the main capture loop (further down) sees `_LIVE_VIEW_ACTIVE = True`
# and pushes one downscaled JPEG to the server every ~1.5s. When the flag
# clears (teacher closes the panel, or 60s TTL expires from inactivity),
# we stop uploading. No persistent storage, no continuous streaming —
# this is strictly opt-in surveillance with a hard kill-switch.

CONTROL_URL    = SERVER_URL.replace("/event", f"/api/v1/proctor/control/{SESSION_ID}")
LIVE_FRAME_URL = SERVER_URL.replace("/event", "/api/v1/proctor/live-frame")
_LIVE_VIEW_ACTIVE = False
_LIVE_VIEW_LOCK = threading.Lock()

# ─── WebSocket live-feed (preferred) with HTTP fallback ───────
def _derive_ws_url():
    """Convert http(s)://host[:port]/path → ws(s)://host[:port]/ws/live-frame/{sid}."""
    url = SERVER_URL.replace("/event", "")
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):] + f"/ws/v1/live-frame/{SESSION_ID}"
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):] + f"/ws/v1/live-frame/{SESSION_ID}"
    return url + f"/ws/v1/live-frame/{SESSION_ID}"

WS_LIVE_URL = _derive_ws_url()

_ws_conn = None
_ws_lock = threading.Lock()

def _get_ws():
    """Return a live WebSocket connection or None (WS may not be supported)."""
    global _ws_conn
    with _ws_lock:
        if _ws_conn is None:
            try:
                import websocket
                ws = websocket.create_connection(WS_LIVE_URL, timeout=5,
                                                 skip_utf8_encoding=True)
                # Auth handshake: send JWT as text frame
                import json
                ws.send(json.dumps({"token": JWT_TOKEN}))
                _ws_conn = ws
                print("[LiveFeed] ✅ WebSocket connected", flush=True)
            except Exception as _we:
                print(f"[LiveFeed] WS not available ({_we}), using HTTP fallback",
                      flush=True)
        return _ws_conn

def _reset_ws():
    """Close and clear the WS connection so next frame retries."""
    global _ws_conn
    with _ws_lock:
        if _ws_conn:
            try:
                _ws_conn.close()
            except Exception:
                pass
            _ws_conn = None

def _control_loop():
    """Poll the server every 2s for control flags. Sets the global
    _LIVE_VIEW_ACTIVE so the capture loop knows whether to upload."""
    global _LIVE_VIEW_ACTIVE
    while True:
        try:
            r = requests.get(CONTROL_URL, headers=HEADERS, timeout=4)
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
    """Best-effort upload of one webcam frame for the teacher's
    live-view panel. Downscales to 320x240 JPEG q70 (~15-25 KB).

    Prefers WebSocket binary stream (raw JPEG bytes, no base64 overhead).
    Falls back to HTTP POST for v2.2.0 servers without WS support.
    Failures are silent."""
    try:
        import cv2 as _cv2
        small = _cv2.resize(frame_bgr, (320, 240), interpolation=_cv2.INTER_AREA)
        ok, buf = _cv2.imencode(".jpg", small,
                                [int(_cv2.IMWRITE_JPEG_QUALITY), 70])
        if not ok:
            return
        raw_bytes = buf.tobytes()

        # Try WebSocket binary first (lower latency, ~33% less bandwidth)
        ws = _get_ws()
        if ws is not None:
            try:
                ws.send_binary(raw_bytes)
                return
            except Exception:
                _reset_ws()

        # Fallback: HTTP POST with base64 (v2.2.0 compat)
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        requests.post(
            LIVE_FRAME_URL,
            json={"session_id": SESSION_ID, "jpeg_b64": b64},
            headers=HEADERS, timeout=4,
        )
    except Exception:
        pass

# Track the last live-frame send so we can pace at ~1.5 s without
# making the inner capture loop care about wall time.
_LAST_LIVE_FRAME_TS = 0.0
_LIVE_FRAME_INTERVAL_SEC = 1.5

def log_event(etype, severity, details):
    global violation_count
    conf = CONFIDENCE.get(etype, 0.75)
    full_details = f"{details} | confidence:{int(conf*100)}%"
    if severity in ("high", "medium"):
        violation_count += 1
    try:
        requests.post(SERVER_URL, json=dict(
            session_id = SESSION_ID,
            event_type = etype,
            severity   = severity,
            details    = full_details
        ), timeout=3, headers=HEADERS)
        print(f"[VIOLATION] {etype}: {details}")
    except Exception as e:
        print(f"[Server Error] {e}")

def save_evidence(frame, label):
    """Persist a violation snapshot locally AND upload it to the backend so
    the teacher's forensics timeline can show it. The upload uses the same
    /api/analyze-frame endpoint the renderer uses for the reference frame.
    """
    try:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(EVIDENCE_DIR, f"{label}_{ts}.jpg")
        cv2.imwrite(path, frame)
        print(f"[Evidence] → {path}")
    except Exception as e:
        print(f"[Evidence Error] {e}")
        return

    # Upload to backend (best-effort — never let a failed upload break the
    # detection loop). The server names the file with our timestamp so the
    # timeline matcher can pair it with the violation event we logged on the
    # same second.
    if not JWT_TOKEN:
        return
    try:
        import base64
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        b64 = base64.b64encode(jpg.tobytes()).decode("ascii")
        requests.post(
            EVIDENCE_UPLOAD_URL,
            json={
                "session_id": SESSION_ID,
                "frame":      b64,
                "timestamp":  datetime.now().isoformat(),
                "event_type": label,   # used by server to prefix the filename
            },
            headers=HEADERS,
            timeout=5,
        )
    except Exception as e:
        print(f"[Evidence Upload Error] {e}")

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
    else:
        print("[Gaze] ❌ resnet18_gaze.onnx not found in weights/ — gaze direction disabled")

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
        return True

# ─── AUDIO (voice detection) ──────────────────────────────────────────────────
AUDIO_AVAILABLE = False
audio_rms       = 0.0
audio_lock      = threading.Lock()

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
        with sd.InputStream(callback=callback,
                            channels=1, samplerate=16000,
                            blocksize=1024):
            while True:
                time.sleep(0.1)
    except Exception as e:
        print(f"[AUDIO] ❌ {e}")

threading.Thread(target=audio_thread, daemon=True).start()
time.sleep(1.5)

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

# ─── DETECTION HELPERS ────────────────────────────────────────────────────────
# uniface returns a list of face dicts with bbox + landmarks. Wrap that
# behind a single function so the main loop doesn't need to know the format.
def detect_faces(frame: np.ndarray):
    """Return list of (bbox, landmarks_2d) tuples — empty list if no faces.

    uniface 1.1.0's RetinaFace.detect() returns a list of dicts shaped like:
        {'bbox': [x1, y1, x2, y2],
         'confidence': float,
         'landmarks': [[x,y]*5]}
    Older uniface versions returned a (boxes, landmarks) ndarray tuple — we
    detect both shapes so the proctor doesn't break across version bumps.
    """
    if not RETINA_AVAILABLE:
        return []
    try:
        result = _retina.detect(frame)
        if result is None:
            return []

        # New API (uniface ≥ 1.1): list of per-face dicts.
        if isinstance(result, list):
            out = []
            for face in result:
                bbox = face.get("bbox")
                lms  = face.get("landmarks")
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


def run_proctoring(cap, W, H):
    print(f"[PROCTOR] 🟢 Monitoring LIVE — Session: {SESSION_ID}")
    _print_tuning_summary()

    # Start YOLO background worker for off-thread cheat-object detection.
    # The model loads lazily inside the worker; YOLO_AVAILABLE flips to True
    # once loading succeeds (typically 1-2 seconds). Until then the main
    # loop skips submission harmlessly.
    yolo_worker.start()

    # We mutate _LAST_LIVE_FRAME_TS from inside the capture loop to
    # pace live-view uploads. Declared global because the variable
    # itself lives at module scope so the control thread can also
    # see / reset it if we ever need to.
    global _LAST_LIVE_FRAME_TS

    # Per-event sustain counters. Each detection only fires after its
    # consecutive-frame threshold is met — single noisy frames are ignored.
    face_missing_count  = 0
    multi_face_count    = 0
    gaze_away_count     = 0
    gaze_extreme_count  = 0
    head_away_count     = 0
    head_extreme_count  = 0
    eyes_closed_count   = 0

    # Per-student calibration bias. If pre-set biases from the renderer's
    # dot-calibration are available, use them and skip self-calibration.
    if _PRESET_GAZE_YAW_BIAS is not None:
        try:
            gaze_yaw_bias   = float(_PRESET_GAZE_YAW_BIAS)
            gaze_pitch_bias = float(_PRESET_GAZE_PITCH_BIAS or 0)
            head_yaw_bias   = float(_PRESET_HEAD_YAW_BIAS or 0)
            head_pitch_bias = float(_PRESET_HEAD_PITCH_BIAS or 0)
        except (ValueError, TypeError):
            print("[PROCTOR] ⚠️ Invalid preset biases — falling back to self-calibration")
            _PRESET_GAZE_YAW_BIAS = None
        calibrated      = True
        cal_gaze_yaw    = []
        cal_gaze_pitch  = []
        cal_head_yaw    = []
        cal_head_pitch  = []
        print(f"[CALIBRATION] ✅ Using pre-set biases from dot calibration — "
              f"gaze:({gaze_yaw_bias:+.2f},{gaze_pitch_bias:+.2f}) "
              f"head:({head_yaw_bias:+.0f},{head_pitch_bias:+.0f})")
    else:
        gaze_yaw_bias   = 0.0
        gaze_pitch_bias = 0.0
        head_yaw_bias   = 0.0
        head_pitch_bias = 0.0
        cal_gaze_yaw    = []
        cal_gaze_pitch  = []
        cal_head_yaw    = []
        cal_head_pitch  = []
        calibrated      = False
    object_history      = {}
    frame_count         = 0
    voice_start_time    = None

    # Lazy enrollment: when SKIP_ENROLLMENT is set the renderer ran the
    # student through enrollment in the browser UI; proctor.py still needs
    # an InsightFace embedding for wrong-person detection. Capture it on
    # the first clean frame within LAZY_ENROLL_WINDOW.
    LAZY_ENROLL_WINDOW = 60   # ~2 seconds at 30fps
    lazy_enroll_done   = not SKIP_ENROLLMENT

    last_logged = {}
    COOLDOWN    = 8.0
    def can_log(etype):
        now = time.time()
        if now - last_logged.get(etype, 0) >= COOLDOWN:
            last_logged[etype] = now
            return True
        return False

    consecutive_failures = 0
    MAX_FAILURES = 30

    while True:
        ret, frame = cap.read()
        if not ret:
            consecutive_failures += 1
            print(f"[PROCTOR] Frame read failed ({consecutive_failures}/{MAX_FAILURES})")
            if consecutive_failures >= MAX_FAILURES:
                print("[PROCTOR] Camera lost — too many failures!")
                break
            time.sleep(0.05)
            continue
        consecutive_failures = 0

        # Live-view: if a teacher has opened the camera-feed panel for
        # this session, push one downscaled JPEG every ~1.5 s. We do
        # this BEFORE the heavy detection pipeline so the upload races
        # in parallel with face/gaze inference and doesn't add to the
        # per-frame budget. Encode + POST happens on this thread; ~5 ms
        # encode + fire-and-forget POST is well under one frame's
        # budget at 15 fps.
        if _LIVE_VIEW_ACTIVE:
            _now = time.time()
            if _now - _LAST_LIVE_FRAME_TS >= _LIVE_FRAME_INTERVAL_SEC:
                _LAST_LIVE_FRAME_TS = _now
                threading.Thread(
                    target=upload_live_frame, args=(frame.copy(),), daemon=True
                ).start()

        frame_count += 1

        # ── LAZY ENROLLMENT ──────────────────────────────────────────────────
        if not lazy_enroll_done and INSIGHT_AVAILABLE:
            if frame_count <= LAZY_ENROLL_WINDOW:
                emb = get_face_embedding(frame)
                if emb is not None:
                    global enrolled_embedding
                    enrolled_embedding = emb
                    lazy_enroll_done   = True
                    print("[PROCTOR] ✅ Face embedding captured (lazy enrollment)")
                    log_event("face_enrolled", "low",
                              f"Lazy embedding at frame {frame_count}")
                    # Upload a reference frame so the teacher always has a
                    # face photo in the timeline, even with zero violations.
                    save_evidence(frame, "reference_frame")
            else:
                lazy_enroll_done = True
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

        # Hard-freeze calibration if we've waited too long. Worst case the
        # student gets the default (0,0) bias — same as the previous build.
        if not calibrated and frame_count >= CALIBRATION_MAX_WAIT:
            if cal_head_yaw:
                head_yaw_bias   = sum(cal_head_yaw)   / len(cal_head_yaw)
                head_pitch_bias = sum(cal_head_pitch) / len(cal_head_pitch)
            if cal_gaze_yaw:
                gaze_yaw_bias   = sum(cal_gaze_yaw)   / len(cal_gaze_yaw)
                gaze_pitch_bias = sum(cal_gaze_pitch) / len(cal_gaze_pitch)
            calibrated = True
            print(f"[CALIBRATION] ⚠ timed out after {frame_count} frames "
                  f"with {len(cal_head_yaw)} samples — "
                  f"freezing bias gaze:({gaze_yaw_bias:+.2f},{gaze_pitch_bias:+.2f}) "
                  f"head:({head_yaw_bias:+.0f},{head_pitch_bias:+.0f})")
            log_event("calibration_timeout", "low",
                      f"samples:{len(cal_head_yaw)}")

        if num_faces == 0:
            multi_face_count = 0
            # Decay gaze/eyes counters slowly so a brief face loss doesn't
            # erase what we already saw — they'll keep accumulating once the
            # face comes back.
            gaze_away_count   = max(0, gaze_away_count - 1)
            eyes_closed_count = max(0, eyes_closed_count - 1)

            # Camera startup grace: macOS often returns black frames for the
            # first ~1-2 seconds after VideoCapture opens. Don't even count
            # missing frames during this window — otherwise the moment the
            # grace period ends the counter is already past threshold and
            # fires instantly.
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
            # Clamp to frame bounds before slicing — RetinaFace can return
            # boxes that extend outside the frame for partial faces.
            x1 = max(0, x1); y1 = max(0, y1)
            x2 = min(W, x2); y2 = min(H, y2)
            face_crop = frame[y1:y2, x1:x2]

            # ── GAZE ─────────────────────────────────────────────────────────
            if GAZE_AVAILABLE and face_crop.size > 0:
                gaze_yaw_raw, gaze_pitch_raw = _gaze_engine.estimate(face_crop)
                # Subtract per-student bias so 0,0 means "this student
                # looking at the screen" rather than the model's idealised
                # forward vector.
                gaze_yaw   = gaze_yaw_raw   - gaze_yaw_bias
                gaze_pitch = gaze_pitch_raw - gaze_pitch_bias
                is_extreme = (abs(gaze_yaw)   > GAZE_YAW_EXTREME or
                              abs(gaze_pitch) > GAZE_PITCH_EXTREME)
                is_away    = (abs(gaze_yaw)   > GAZE_YAW_RAD or
                              abs(gaze_pitch) > GAZE_PITCH_RAD)
                if not calibrated:
                    # Skip threshold checks entirely during calibration —
                    # we just collect samples and bail.
                    cal_gaze_yaw.append(gaze_yaw_raw)
                    cal_gaze_pitch.append(gaze_pitch_raw)
                    is_extreme = False
                    is_away    = False

                # Leaky-bucket counters. Extreme looks add 2/frame so they
                # cross the smaller GAZE_EXTREME_FRAMES bar fast; normal
                # away-looks add 1; centered gaze decays the buckets.
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

                # Extreme tier fires first (faster + higher confidence).
                if gaze_extreme_count >= GAZE_EXTREME_FRAMES and \
                   can_log("gaze_away"):
                    direction = _dominant_direction(
                        gaze_yaw, gaze_pitch, GAZE_YAW_RAD, GAZE_PITCH_RAD)
                    log_event("gaze_away", "high",
                              f"Looking off-screen {direction} "
                              f"(yaw:{gaze_yaw:+.2f}rad pitch:{gaze_pitch:+.2f}rad EXTREME)")
                    save_evidence(frame, "gaze_away")
                    gaze_away_count    = 0
                    gaze_extreme_count = 0
                elif gaze_away_count >= GAZE_FRAMES_NEEDED and \
                     can_log("gaze_away"):
                    direction = _dominant_direction(
                        gaze_yaw, gaze_pitch, GAZE_YAW_RAD, GAZE_PITCH_RAD)
                    log_event("gaze_away", "medium",
                              f"Looking {direction} "
                              f"(yaw:{gaze_yaw:+.2f}rad pitch:{gaze_pitch:+.2f}rad)")
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

            if head_extreme_count >= HEAD_EXTREME_FRAMES and \
               can_log("head_turned"):
                direction = _dominant_direction(
                    head_yaw, head_pitch, HEAD_YAW_THRESHOLD, HEAD_PITCH_THRESHOLD)
                log_event("head_turned", "high",
                          f"Head turned {direction} "
                          f"(yaw:{head_yaw:+.0f}° pitch:{head_pitch:+.0f}° EXTREME)")
                save_evidence(frame, "head_turned")
                head_away_count    = 0
                head_extreme_count = 0
            elif head_away_count >= HEAD_FRAMES_NEEDED and \
                 can_log("head_turned"):
                direction = _dominant_direction(
                    head_yaw, head_pitch, HEAD_YAW_THRESHOLD, HEAD_PITCH_THRESHOLD)
                log_event("head_turned", "medium",
                          f"Head turned {direction} "
                          f"(yaw:{head_yaw:+.0f}° pitch:{head_pitch:+.0f}°)")
                save_evidence(frame, "head_turned")
                head_away_count = 0

            # ── EYES OPEN/CLOSED ─────────────────────────────────────────────
            eyes_open = eyes_detected(face_crop)
            if not eyes_open:
                eyes_closed_count += 1
            else:
                eyes_closed_count = max(0, eyes_closed_count - 2)

            if eyes_closed_count >= EYES_CLOSED_FRAMES and \
               can_log("eyes_closed"):
                log_event("eyes_closed", "high", "Eyes closed")
                save_evidence(frame, "eyes_closed")

            # ── CALIBRATION FREEZE ───────────────────────────────────────────
            # Once we have CALIBRATION_FRAMES clean samples, freeze the
            # bias and start enforcing thresholds. If the student moved
            # around during calibration that's fine — the average still
            # reflects "their" centred position better than 0.
            if not calibrated and len(cal_head_yaw) >= CALIBRATION_FRAMES:
                if cal_gaze_yaw:
                    gaze_yaw_bias   = sum(cal_gaze_yaw)   / len(cal_gaze_yaw)
                    gaze_pitch_bias = sum(cal_gaze_pitch) / len(cal_gaze_pitch)
                head_yaw_bias   = sum(cal_head_yaw)   / len(cal_head_yaw)
                head_pitch_bias = sum(cal_head_pitch) / len(cal_head_pitch)
                calibrated = True
                print(f"[CALIBRATION] ✅ baseline frozen after "
                      f"{len(cal_head_yaw)} frames — "
                      f"gaze bias yaw:{gaze_yaw_bias:+.2f}rad "
                      f"pitch:{gaze_pitch_bias:+.2f}rad | "
                      f"head bias yaw:{head_yaw_bias:+.0f}° "
                      f"pitch:{head_pitch_bias:+.0f}°")
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
        # Every YOLO_EVERY_N frames we submit the frame to the worker thread.
        # We also check the result queue for completed inferences — results
        # arrive 1-3 frames later on CPU, so we process them when available
        # without blocking the capture loop.
        if YOLO_AVAILABLE:
            if frame_count % YOLO_EVERY_N == 0:
                yolo_worker.submit(frame, frame_count)

            yolo_result = yolo_worker.get_result(frame_count)
            if yolo_result is not None:
                if yolo_result.get("error"):
                    print(f"[YOLO Error] {yolo_result['error']}")
                else:
                    detections = yolo_result["detections"]
                    seen_names = set()
                    for name, conf in detections:
                        seen_names.add(name)
                        object_history[name] = object_history.get(name, 0) + 1

                    # Decay objects we did not see this frame so a fleeting
                    # detection doesn't get stuck above the threshold forever.
                    for name in list(object_history):
                        if name not in seen_names:
                            object_history[name] = max(0, object_history[name] - 1)

                    for name, conf in detections:
                        if object_history.get(name, 0) >= YOLO_MIN_FRAMES:
                            if can_log(f"cheat_{name}"):
                                log_event("cheat_object_detected", "high",
                                          f"{name} detected (conf:{conf:.0%})")
                                save_evidence(frame, f"cheat_{name}")
                                object_history[name] = 0

        # ── VOICE DETECTION ──────────────────────────────────────────────────
        # Sustained-time approach: only log if RMS stays above threshold for
        # the full window. Eliminates double-logging on brief noises.
        if AUDIO_AVAILABLE:
            with audio_lock:
                rms = audio_rms
            if rms > VOICE_THRESHOLD:
                if voice_start_time is None:
                    voice_start_time = time.time()
                elif time.time() - voice_start_time >= VOICE_SUSTAINED_SECS:
                    if can_log("voice_detected"):
                        log_event("voice_detected", "medium",
                                  f"Voice sustained (rms:{rms:.3f})")
                    voice_start_time = time.time()  # require another full window
            else:
                voice_start_time = None

        # ── WRONG PERSON CHECK ───────────────────────────────────────────────
        if enrolled_embedding is not None and INSIGHT_AVAILABLE and \
           frame_count % 30 == 0:
            current_emb = get_face_embedding(frame)
            if current_emb is not None:
                similarity = float(np.dot(enrolled_embedding, current_emb))
                if similarity < WRONG_PERSON_THRESHOLD and \
                   can_log("wrong_person"):
                    log_event("wrong_person", "medium",
                              f"Different person detected "
                              f"(cosine similarity: {similarity:.2f})")
                    save_evidence(frame, "wrong_person")

        # ── HUD ──────────────────────────────────────────────────────────────
        if not HEADLESS:
            cv2.rectangle(frame, (0,0), (W,35), (20,20,20), -1)
            voice_secs = int(time.time() - voice_start_time) \
                if voice_start_time else 0
            status = (f"Faces:{num_faces} | "
                      f"Gaze:{gaze_away_count}/{GAZE_FRAMES_NEEDED} | "
                      f"Head:{head_away_count}/{HEAD_FRAMES_NEEDED} | "
                      f"Voice:{voice_secs:.0f}s")
            cv2.putText(frame, status, (8,22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (200,200,200), 1)
            cv2.putText(frame, "AI PROCTOR ACTIVE",
                        (W-180, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0,255,0), 1)
            cv2.imshow("AI Proctor", frame)
            cv2.waitKey(1)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print(f"[PROCTOR] Session: {SESSION_ID}")
    print(f"[PROCTOR] Server:  {SERVER_URL}")
    print(f"[PROCTOR] Headless: {HEADLESS}")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) \
              if platform.system() == "Windows" \
              else cv2.VideoCapture(1)
    if not cap.isOpened():
        cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        try:
            requests.post(SERVER_URL, json=dict(
                session_id = SESSION_ID,
                event_type = "proctor_camera_failed",
                severity   = "high",
                details    = "Cannot open any camera — proctoring disabled"
            ), timeout=3, headers=HEADERS)
        except Exception:
            pass
        print("[PROCTOR] ❌ Cannot open camera!")
        sys.exit(1)

    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if W == 0 or H == 0:
        W, H = 640, 480
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        print(f"[PROCTOR] Camera returned 0x0 — forcing {W}x{H}")
    print(f"[PROCTOR] Camera: {W}x{H}")

    # First few frames are often blank, especially on Windows.
    print("[PROCTOR] Warming up camera...")
    for _ in range(10):
        cap.read()
    time.sleep(0.5)

    # ── Calibration-only mode: stream readings and exit ────────────
    if CALIBRATION_MODE:
        try:
            run_calibration(cap, W, H)
        except KeyboardInterrupt:
            print("\n[CALIBRATION] Stopped by signal")
        finally:
            cap.release()
            print("[CALIBRATION] Done")
        return

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
    finally:
        yolo_worker.stop()
        duration = int(time.time() - session_start)
        log_event("session_ended", "low",
                  f"violations:{violation_count} | duration:{duration}s")
        cap.release()
        if not HEADLESS:
            cv2.destroyAllWindows()
        print("[PROCTOR] ✅ Session ended")

if __name__ == "__main__":
    main()

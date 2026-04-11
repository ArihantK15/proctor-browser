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
import platform
import threading
import requests
import cv2
import numpy as np
from collections import deque
from datetime import datetime
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

# ultralytics YOLO: cheat object detection
try:
    from ultralytics import YOLO
    print("[YOLO] Loading model...")
    yolo_model = YOLO("yolov8n.pt")
    YOLO_AVAILABLE = True
    print("[YOLO] ✅ Ready")
except Exception as _ye:
    print(f"[YOLO] ❌ Not available: {_ye}")
    YOLO_AVAILABLE = False
    yolo_model = None

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
EVIDENCE_UPLOAD_URL = SERVER_URL.replace("/event", "/api/analyze-frame")
HEADLESS          = platform.system() == "Windows" or \
                    os.environ.get("PROCTOR_HEADLESS","0") == "1"
SKIP_ENROLLMENT   = os.environ.get("PROCTOR_SKIP_ENROLLMENT","0") == "1"

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
# All frame-count and time thresholds are preserved from the mediapipe era
# so the teacher's experience (how many false positives, how fast a flag
# fires) is unchanged. Only the gaze threshold unit had to change because
# the new ONNX model emits radians, not the old normalized iris ratio.
GAZE_YAW_RAD        = 0.25   # ~14° — looking left/right beyond this = gaze away
GAZE_PITCH_RAD      = 0.30   # ~17° — looking up/down beyond this   = gaze away
GAZE_FRAMES_NEEDED  = 6      # frames-in-bucket before logging (leaky)
HEAD_YAW_THRESHOLD  = 22     # degrees (solvePnP yaw)
HEAD_PITCH_THRESHOLD = 30    # degrees (solvePnP pitch)
HEAD_FRAMES_NEEDED  = 6
FACE_MISSING_FRAMES = 18     # ~600ms at 30fps — survives camera warmup blips
EYES_CLOSED_FRAMES  = 12
MULTI_FACE_FRAMES   = 3
WARMUP_GRACE_FRAMES = 60     # ignore face_missing for first ~2s after camera open
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
def run_proctoring(cap, W, H):
    print(f"[PROCTOR] 🟢 Monitoring LIVE — Session: {SESSION_ID}")

    # Per-event sustain counters. Each detection only fires after its
    # consecutive-frame threshold is met — single noisy frames are ignored.
    face_missing_count  = 0
    multi_face_count    = 0
    gaze_away_count     = 0
    head_away_count     = 0
    eyes_closed_count   = 0
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
                gaze_yaw, gaze_pitch = _gaze_engine.estimate(face_crop)
                # Leaky-bucket: looking-away frames add 1, looking-at-screen
                # frames subtract 1 (not reset to 0). This way a student who
                # glances around still triggers, while a single noisy frame
                # in the middle of an honest exam does not.
                if abs(gaze_yaw)   > GAZE_YAW_RAD or \
                   abs(gaze_pitch) > GAZE_PITCH_RAD:
                    gaze_away_count += 1
                else:
                    gaze_away_count = max(0, gaze_away_count - 1)

                # Periodic debug print so we can see what the model is
                # actually emitting if a future student reports false
                # negatives. One line every ~2s at 30fps is harmless.
                if frame_count % 60 == 0:
                    print(f"[Gaze Debug] yaw:{gaze_yaw:+.2f}rad "
                          f"pitch:{gaze_pitch:+.2f}rad "
                          f"bucket:{gaze_away_count}/{GAZE_FRAMES_NEEDED}")

                if gaze_away_count >= GAZE_FRAMES_NEEDED and \
                   can_log("gaze_away"):
                    direction = "left"  if gaze_yaw   < -GAZE_YAW_RAD else \
                                "right" if gaze_yaw   >  GAZE_YAW_RAD else \
                                "up"    if gaze_pitch < -GAZE_PITCH_RAD else \
                                "down"
                    log_event("gaze_away", "high",
                              f"Looking {direction} "
                              f"(yaw:{gaze_yaw:+.2f}rad pitch:{gaze_pitch:+.2f}rad)")
                    save_evidence(frame, "gaze_away")
                    gaze_away_count = 0

            # ── HEAD POSE ────────────────────────────────────────────────────
            head_yaw, head_pitch = get_head_pose(lm_2d, W, H)
            if abs(head_yaw)   > HEAD_YAW_THRESHOLD or \
               abs(head_pitch) > HEAD_PITCH_THRESHOLD:
                head_away_count += 1
            else:
                head_away_count = 0

            if head_away_count >= HEAD_FRAMES_NEEDED and \
               can_log("head_turned"):
                direction = "left"  if head_yaw   < -HEAD_YAW_THRESHOLD else \
                            "right" if head_yaw   >  HEAD_YAW_THRESHOLD else \
                            "up"    if head_pitch < -HEAD_PITCH_THRESHOLD else \
                            "down"
                log_event("head_turned", "high",
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

            # ── HUD: draw bbox + landmarks ───────────────────────────────────
            if not HEADLESS:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                for px, py in lm_2d.astype(int):
                    cv2.circle(frame, (px, py), 2, (0, 255, 255), -1)

        # ── YOLO OBJECT DETECTION ────────────────────────────────────────────
        if YOLO_AVAILABLE and frame_count % YOLO_EVERY_N == 0:
            try:
                small  = cv2.resize(frame, (416, 416))
                res    = yolo_model(small, verbose=False, conf=YOLO_CONFIDENCE)[0]
                seen_names = set()
                detected = []
                for box in res.boxes:
                    cls_id = int(box.cls[0])
                    if cls_id not in CHEAT_IDS:
                        continue
                    name = CHEAT_IDS[cls_id]
                    seen_names.add(name)
                    object_history[name] = object_history.get(name, 0) + 1
                    if object_history[name] >= YOLO_MIN_FRAMES:
                        detected.append((name, float(box.conf[0])))

                # Decay objects we did not see this frame so a fleeting
                # detection doesn't get stuck above the threshold forever.
                for name in list(object_history):
                    if name not in seen_names:
                        object_history[name] = max(0, object_history[name] - 1)

                for name, conf in detected:
                    if can_log(f"cheat_{name}"):
                        log_event("cheat_object_detected", "high",
                                  f"{name} detected (conf:{conf:.0%})")
                        save_evidence(frame, f"cheat_{name}")
                        object_history[name] = 0

            except Exception as e:
                print(f"[YOLO Error] {e}")

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
    print(f"[PROCTOR] Camera: {W}x{H}")

    # First few frames are often blank, especially on Windows.
    print("[PROCTOR] Warming up camera...")
    for _ in range(10):
        cap.read()
    time.sleep(0.5)

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
        duration = int(time.time() - session_start)
        log_event("session_ended", "low",
                  f"violations:{violation_count} | duration:{duration}s")
        cap.release()
        if not HEADLESS:
            cv2.destroyAllWindows()
        print("[PROCTOR] ✅ Session ended")

if __name__ == "__main__":
    main()

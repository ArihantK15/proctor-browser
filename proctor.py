import os
import sys
import time
import platform
import threading
import requests
import cv2
import numpy as np
import mediapipe as mp
from datetime import datetime
from collections import deque
from ultralytics import YOLO

# InsightFace for face-embedding wrong-person detection
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
HEADLESS     = platform.system() == "Windows" or \
               os.environ.get("PROCTOR_HEADLESS","0") == "1"

os.makedirs(EVIDENCE_DIR, exist_ok=True)

# ─── CONFIDENCE SCORES ────────────────────────────────────────────────────────
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
GAZE_THRESHOLD      = 0.32   # ratio outside this = looking away
GAZE_FRAMES_NEEDED  = 8      # consecutive frames before logging
HEAD_YAW_THRESHOLD  = 25     # degrees
HEAD_FRAMES_NEEDED  = 8
FACE_MISSING_FRAMES = 10     # frames without face before logging
EYES_CLOSED_FRAMES  = 12
MULTI_FACE_FRAMES   = 3
YOLO_CONFIDENCE     = 0.35   # raised slightly — reduces false positives
YOLO_MIN_FRAMES     = 2
YOLO_EVERY_N        = 5
VOICE_THRESHOLD     = float(os.getenv("PROCTOR_VOICE_THRESHOLD", "0.035"))
VOICE_FRAMES_NEEDED = 15     # kept for backward compat (not used in new logic)
WRONG_PERSON_THRESHOLD = float(os.getenv("PROCTOR_WRONG_PERSON_THRESHOLD", "0.25"))

# ─── CHEAT OBJECTS ────────────────────────────────────────────────────────────
CHEAT_IDS = {
    67: "Phone",
    63: "Laptop",
    73: "Book",
    66: "Keyboard",
    62: "TV",
}

# ─── SERVER LOGGING ───────────────────────────────────────────────────────────
session_start = time.time()
violation_count = 0  # lightweight counter only

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
    try:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(EVIDENCE_DIR, f"{label}_{ts}.jpg")
        cv2.imwrite(path, frame)
        print(f"[Evidence] → {path}")
    except Exception as e:
        print(f"[Evidence Error] {e}")

# ─── MEDIAPIPE ────────────────────────────────────────────────────────────────
mp_face_mesh = mp.solutions.face_mesh
mp_drawing   = mp.solutions.drawing_utils

face_mesh = mp_face_mesh.FaceMesh(
    max_num_faces        = 4,
    refine_landmarks     = True,
    min_detection_confidence = 0.5,
    min_tracking_confidence  = 0.5,
)

# ─── YOLO ─────────────────────────────────────────────────────────────────────
print("[YOLO] Loading model...")
try:
    yolo_model = YOLO("yolov8n.pt")
    print("[YOLO] ✅ Ready")
    YOLO_AVAILABLE = True
except Exception as e:
    print(f"[YOLO] ❌ Failed: {e}")
    YOLO_AVAILABLE = False

# ─── AUDIO ────────────────────────────────────────────────────────────────────
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

# ─── GAZE DETECTION ───────────────────────────────────────────────────────────
def get_gaze_ratio(landmarks):
    """Returns ratio 0-1. 0.5=center, <0.3=right, >0.7=left"""
    try:
        l_iris  = landmarks[468].x
        r_iris  = landmarks[473].x
        l_left  = landmarks[33].x
        l_right = landmarks[133].x
        r_left  = landmarks[362].x
        r_right = landmarks[263].x

        l_ratio = (l_iris - l_left)  / max(l_right - l_left,  0.001)
        r_ratio = (r_iris - r_left)  / max(r_right - r_left, 0.001)
        return (l_ratio + r_ratio) / 2
    except:
        return 0.5

def get_eye_openness(landmarks, W, H):
    """Returns True if eyes are open"""
    try:
        # Left eye: top=159, bottom=145
        l_top    = landmarks[159].y * H
        l_bottom = landmarks[145].y * H
        l_open   = abs(l_bottom - l_top)
        # Right eye: top=386, bottom=374
        r_top    = landmarks[386].y * H
        r_bottom = landmarks[374].y * H
        r_open   = abs(r_bottom - r_top)
        avg = (l_open + r_open) / 2
        return avg > 4.0  # pixels
    except:
        return True

def get_head_yaw(landmarks, W, H):
    """Returns yaw angle in degrees. 0=straight"""
    try:
        nose    = landmarks[1]
        l_cheek = landmarks[234]
        r_cheek = landmarks[454]
        nose_x  = nose.x * W
        l_x     = l_cheek.x * W
        r_x     = r_cheek.x * W
        center  = (l_x + r_x) / 2
        face_w  = r_x - l_x
        if face_w < 1:
            return 0
        offset  = (nose_x - center) / face_w
        return offset * 90
    except:
        return 0

# ─── FACE EMBEDDING ───────────────────────────────────────────────────────────
enrolled_embedding = None  # set during enrollment; used in proctoring loop

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

# ─── ENROLLMENT ───────────────────────────────────────────────────────────────
def run_enrollment(cap, W, H):
    print("\n[ENROLLMENT] Starting face enrollment...")
    log_event("enrollment_started", "low",
              f"Session: {SESSION_ID}")

    DIRECTIONS  = [
        "Look STRAIGHT at camera",
        "Turn slightly LEFT",
        "Turn slightly RIGHT",
        "Tilt slightly UP",
        "Tilt slightly DOWN",
    ]
    SAMPLES_PER    = 15
    MAX_FRAMES     = 900   # ~30s timeout — bail out if camera/face issues
    samples        = []
    direction      = 0
    count          = 0
    total_frames   = 0
    enrolled_face  = None

    while direction < len(DIRECTIONS):
        total_frames += 1
        if total_frames > MAX_FRAMES:
            print("[ENROLLMENT] ⚠️ Timeout — skipping remaining directions")
            break

        ret, frame = cap.read()
        if not ret:
            print("[ENROLLMENT] ⚠️ Camera frame failed — skipping enrollment")
            break

        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb)

        ok = results.multi_face_landmarks and \
             len(results.multi_face_landmarks) == 1

        # Draw direction text
        if not HEADLESS:
            overlay = frame.copy()
            cv2.rectangle(overlay, (0,0), (W, 80),
                          (0,100,0) if ok else (0,0,150), -1)
            cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
            cv2.putText(frame, DIRECTIONS[direction],
                        (15,40), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (255,255,255), 2)
            pct = int((direction*SAMPLES_PER+count)/(len(DIRECTIONS)*SAMPLES_PER)*100)
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
            if count == 1 and direction == 0:
                enrolled_face = results.multi_face_landmarks[0]

            # Capture face embedding at the midpoint of direction 0 (straight shot)
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
    return enrolled_face

# ─── MAIN PROCTORING LOOP ─────────────────────────────────────────────────────
def run_proctoring(cap, W, H):
    print(f"[PROCTOR] 🟢 Monitoring LIVE — Session: {SESSION_ID}")

    # Counters
    face_missing_count  = 0
    multi_face_count    = 0
    gaze_away_count     = 0
    head_away_count     = 0
    eyes_closed_count   = 0
    object_history      = {}
    frame_count         = 0
    voice_start_time    = None  # time when RMS first exceeded threshold

    # Cooldowns (prevent spam)
    last_logged         = {}
    COOLDOWN            = 8.0  # seconds between same violation

    def can_log(etype):
        now = time.time()
        if now - last_logged.get(etype, 0) >= COOLDOWN:
            last_logged[etype] = now
            return True
        return False

    consecutive_failures = 0
    MAX_FAILURES = 30  # allow up to 30 bad frames before giving up

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
        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb)

        # ── FACE DETECTION ────────────────────────────────────────────────────
        faces = results.multi_face_landmarks or []
        num_faces = len(faces)

        if num_faces == 0:
            face_missing_count += 1
            multi_face_count    = 0
            gaze_away_count     = 0
            eyes_closed_count   = 0

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
            lm = faces[0].landmark

            # ── GAZE ──────────────────────────────────────────────────────────
            gaze = get_gaze_ratio(lm)
            if gaze < GAZE_THRESHOLD or gaze > (1 - GAZE_THRESHOLD):
                gaze_away_count += 1
            else:
                gaze_away_count = 0

            if gaze_away_count >= GAZE_FRAMES_NEEDED and \
               can_log("gaze_away"):
                direction = "right" if gaze < GAZE_THRESHOLD else "left"
                log_event("gaze_away", "high",
                          f"Looking {direction} (ratio:{gaze:.2f})")
                save_evidence(frame, "gaze_away")
                gaze_away_count = 0

            # ── HEAD POSE ─────────────────────────────────────────────────────
            yaw = get_head_yaw(lm, W, H)
            if abs(yaw) > HEAD_YAW_THRESHOLD:
                head_away_count += 1
            else:
                head_away_count = 0

            if head_away_count >= HEAD_FRAMES_NEEDED and \
               can_log("head_turned"):
                direction = "left" if yaw < 0 else "right"
                log_event("head_turned", "high",
                          f"Head turned {direction} ({yaw:.0f}°)")
                save_evidence(frame, "head_turned")
                head_away_count = 0

            # ── EYES CLOSED ───────────────────────────────────────────────────
            eyes_open = get_eye_openness(lm, W, H)
            if not eyes_open:
                eyes_closed_count += 1
            else:
                eyes_closed_count = max(0, eyes_closed_count - 2)

            if eyes_closed_count >= EYES_CLOSED_FRAMES and \
               can_log("eyes_closed"):
                log_event("eyes_closed", "high", "Eyes closed")
                save_evidence(frame, "eyes_closed")

            # ── DRAW LANDMARKS (non-headless) ──────────────────────────────────
            if not HEADLESS:
                h_lm = faces[0]
                mp_drawing.draw_landmarks(
                    frame, h_lm,
                    mp_face_mesh.FACEMESH_CONTOURS,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=mp_drawing.DrawingSpec(
                        color=(0,255,0), thickness=1))

        # ── YOLO OBJECT DETECTION ─────────────────────────────────────────────
        if YOLO_AVAILABLE and frame_count % YOLO_EVERY_N == 0:
            try:
                small  = cv2.resize(frame, (416, 416))
                sx, sy = W/416, H/416
                res    = yolo_model(small, verbose=False,
                                    conf=YOLO_CONFIDENCE)[0]
                detected = []
                for box in res.boxes:
                    cls_id = int(box.cls[0])
                    conf   = float(box.conf[0])
                    if cls_id in CHEAT_IDS:
                        name = CHEAT_IDS[cls_id]
                        object_history[name] = \
                            object_history.get(name, 0) + 1
                        if object_history[name] >= YOLO_MIN_FRAMES:
                            detected.append((name, conf))
                    else:
                        # Decay unseen objects
                        pass

                # Decay objects not seen this frame
                seen_names = {CHEAT_IDS[int(b.cls[0])]
                              for b in res.boxes
                              if int(b.cls[0]) in CHEAT_IDS}
                for name in list(object_history.keys()):
                    if name not in seen_names:
                        object_history[name] = max(
                            0, object_history[name] - 1)

                for name, conf in detected:
                    if can_log(f"cheat_{name}"):
                        log_event("cheat_object_detected", "high",
                                  f"{name} detected (conf:{conf:.0%})")
                        save_evidence(frame, f"cheat_{name}")
                        object_history[name] = 0  # reset so re-detection needs full build-up

            except Exception as e:
                print(f"[YOLO Error] {e}")

        # ── VOICE DETECTION ───────────────────────────────────────────────────
        # Sustained-time approach: only log if RMS stays above threshold for
        # the full COOLDOWN window continuously. Eliminates double-logging.
        if AUDIO_AVAILABLE:
            with audio_lock:
                rms = audio_rms
            if rms > VOICE_THRESHOLD:
                if voice_start_time is None:
                    voice_start_time = time.time()
                elif time.time() - voice_start_time >= COOLDOWN:
                    if can_log("voice_detected"):
                        log_event("voice_detected", "medium",
                                  f"Voice sustained (rms:{rms:.3f})")
                    voice_start_time = time.time()  # reset — require another full window
            else:
                voice_start_time = None  # gap in audio resets the timer

        # ── WRONG PERSON CHECK ────────────────────────────────────────────────
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

        # ── HUD DISPLAY ───────────────────────────────────────────────────────
        if not HEADLESS:
            # Status bar
            status_color = (0,200,0) if num_faces == 1 else (0,0,200)
            cv2.rectangle(frame, (0,0), (W,35), (20,20,20), -1)
            voice_secs = int(time.time() - voice_start_time) \
                if voice_start_time else 0
            status = f"Faces:{num_faces} | " \
                     f"Gaze:{gaze_away_count}/{GAZE_FRAMES_NEEDED} | " \
                     f"Head:{head_away_count}/{HEAD_FRAMES_NEEDED} | " \
                     f"Voice:{voice_secs:.0f}s/{COOLDOWN:.0f}s"
            cv2.putText(frame, status, (8,22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (200,200,200), 1)
            cv2.putText(frame, "AI PROCTOR ACTIVE",
                        (W-180, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0,255,0), 1)
            cv2.imshow("AI Proctor", frame)
            cv2.waitKey(1)

    cap.release()
    if not HEADLESS:
        cv2.destroyAllWindows()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print(f"[PROCTOR] Session: {SESSION_ID}")
    print(f"[PROCTOR] Server:  {SERVER_URL}")
    print(f"[PROCTOR] Headless: {HEADLESS}")

    # Try default backend first (MSMF on Windows — allows sharing with browser)
    # Fallback to CAP_DSHOW only if default fails
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) \
              if platform.system() == "Windows" \
              else cv2.VideoCapture(1)
    if not cap.isOpened():
        cap = cv2.VideoCapture(1)  # try second camera
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

    # Warm up camera — first frames on Windows are often blank
    print("[PROCTOR] Warming up camera...")
    for _ in range(10):
        cap.read()
    time.sleep(0.5)

    # Wait for audio thread
    time.sleep(1)

    # Skip enrollment in headless mode — no window to show guidance,
    # and it can hang forever if face isn't detected
    if HEADLESS:
        print("[ENROLLMENT] Headless mode — skipping enrollment")
        log_event("enrollment_complete", "low", "Headless mode — enrollment skipped")
    else:
        run_enrollment(cap, W, H)

    # Main proctoring
    try:
        run_proctoring(cap, W, H)
    except KeyboardInterrupt:
        print("\n[PROCTOR] Stopped by signal")
    finally:
        # Session summary
        duration = int(time.time() - session_start)
        log_event("session_ended", "low",
                  f"violations:{violation_count} | duration:{duration}s")
        cap.release()
        if not HEADLESS:
            cv2.destroyAllWindows()
        print("[PROCTOR] ✅ Session ended")

if __name__ == "__main__":
    main()

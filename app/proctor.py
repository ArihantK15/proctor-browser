import cv2
import numpy as np
import requests
import uuid
import time
import os
import mediapipe as mp
import sounddevice as sd
import threading
import json
from collections import deque
from datetime import datetime
from ultralytics import YOLO

# ─── CONFIG ───────────────────────────────────────────────────────────────────
SERVER_URL            = "http://localhost:8000/event"
SESSION_ID            = str(uuid.uuid4())
EVIDENCE_DIR          = "/Users/arihantkaul/proctored-browser/logs/evidence"
LIGHTING_MIN          = 40
LIGHTING_MAX          = 220

# ─── FACE RECOGNITION ─────────────────────────────────────────────────────────
SAMPLES_NEEDED        = 20
CONFIDENCE_THRESHOLD  = 50
SMOOTHING_WINDOW      = 12

# ─── DETECTION THRESHOLDS ─────────────────────────────────────────────────────
BLINK_THRESHOLD       = 0.20
BLINK_MAX_FRAMES      = 20
GAZE_THRESHOLD        = 0.28
HEAD_YAW_THRESHOLD    = 25
HEAD_PITCH_THRESHOLD  = 30
GRACE_FRAMES          = 60
WRITING_GLANCE_MAX    = 90
SUSPICIOUS_SUSTAINED  = 150
PHONE_DOWN_FRAMES     = 30
GLANCE_HISTORY_SIZE   = 300

# ─── OBJECT DETECTION ─────────────────────────────────────────────────────────
YOLO_CONFIDENCE       = 0.60
YOLO_MIN_FRAMES       = 5
YOLO_EVERY_N          = 10
EAR_EVERY_N           = 15
OBJECT_GRACE_FRAMES   = 30
PAUSE_LINGER_FRAMES   = 20
EAR_PAD               = 90

CHEAT_OBJECTS = {
    67: ("Phone",    "high"),
    63: ("Laptop",   "high"),
    73: ("Book",     "medium"),
    66: ("Keyboard", "medium"),
    65: ("Remote",   "medium"),
    62: ("TV",       "high"),
}

CUSTOM_CHEAT_LABELS = {
    "earphone", "earphones", "earbud", "earbuds",
    "headphone", "headphones", "airpods",
    "smartwatch", "smart watch",
}

L_EAR_REGION = [234, 93, 132, 58]
R_EAR_REGION = [454, 323, 361, 288]

# ─── AUDIO CONFIG ─────────────────────────────────────────────────────────────
AUDIO_SAMPLE_RATE     = 16000
AUDIO_BLOCK_SIZE      = 512
AUDIO_THRESHOLD       = 0.012
AUDIO_VOICE_FRAMES    = 25
AUDIO_COOLDOWN        = 8

# ─── EARPHONE DETECTION ───────────────────────────────────────────────────────
EARPHONE_CONFIRM_SCORE  = 1.5       # lowered — easier to trigger
EARPHONE_GRACE_FRAMES   = 45
AUDIO_LIP_WINDOW        = 60
LIP_MOVEMENT_THRESHOLD  = 1.8
LIP_HISTORY_SIZE        = 90
HEAD_ROLL_THRESHOLD     = 3.0       # lowered from 8.0
HEAD_WIDTH_THRESHOLD    = 0.55      # head wider than this = headphones

os.makedirs(EVIDENCE_DIR, exist_ok=True)

# ─── MEDIAPIPE INIT ───────────────────────────────────────────────────────────
face_mesh = mp.solutions.face_mesh.FaceMesh(
    max_num_faces            = 2,
    refine_landmarks         = True,
    min_detection_confidence = 0.5,
    min_tracking_confidence  = 0.5
)

# ─── YOLO INIT ────────────────────────────────────────────────────────────────
print("[YOLO] Loading model...")
yolo_model = YOLO("yolov8n.pt")
print("[YOLO] ✅ Ready.\n")

# ─── OPENCV INIT ──────────────────────────────────────────────────────────────
recognizer = cv2.face.LBPHFaceRecognizer_create()
video_cap  = cv2.VideoCapture(0)
if not video_cap.isOpened():
    raise RuntimeError("Could not open camera.")

video_cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
video_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
video_cap.set(cv2.CAP_PROP_FPS,          30)

W = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
H = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

print("[MEDIAPIPE] Warming up...")
for _ in range(10):
    ret, wf = video_cap.read()
    if ret:
        face_mesh.process(cv2.cvtColor(wf, cv2.COLOR_BGR2RGB))
print("[MEDIAPIPE] ✅ Ready.\n")

# ─── LANDMARK INDICES ─────────────────────────────────────────────────────────
L_EAR_IDX     = [362, 385, 387, 263, 373, 380]
R_EAR_IDX     = [33,  160, 158, 133, 153, 144]
R_IRIS        = 468;  L_IRIS  = 473
R_EYE_L       = 33;   R_EYE_R = 133
L_EYE_L       = 362;  L_EYE_R = 263
NOSE_TIP      = 1;    CHIN    = 152
LIP_TOP       = [13, 312, 311, 310]
LIP_BOTTOM    = [14, 317, 402, 318]
LIP_LEFT      = 61
LIP_RIGHT     = 291
L_EYE_CENTER  = 159
R_EYE_CENTER  = 386

# ─── GLOBAL STATE ─────────────────────────────────────────────────────────────
conf_buffer           = deque(maxlen=SMOOTHING_WINDOW)
blink_frames          = 0
pitch_history         = deque(maxlen=GLANCE_HISTORY_SIZE)
down_frames           = 0
screen_frames         = 0
glance_count          = 0
phone_and_down_frames = 0

grace_counters = {
    "face_missing"  : 0,
    "multiple_faces": 0,
    "wrong_person"  : 0,
    "eyes_closed"   : 0,
    "head_away"     : 0,
    "gaze_away"     : 0,
    "voice"         : 0,
    "earphone"      : 0,
}

detected_objects  = []
ear_objects       = []
object_grace      = 0
pause_linger      = 0
exam_paused       = False
object_history    = {}

audio_rms         = 0.0
audio_lock        = threading.Lock()
voice_frames      = 0

lip_history       = deque(maxlen=LIP_HISTORY_SIZE)
audio_history     = deque(maxlen=AUDIO_LIP_WINDOW)
earphone_score    = 0.0
earphone_grace    = 0
earphone_signals  = {}


def tick_grace(key, condition):
    if condition:
        grace_counters[key] += 1
    else:
        grace_counters[key] = 0
    return grace_counters[key] >= GRACE_FRAMES


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def log_event(etype, severity, details):
    try:
        requests.post(SERVER_URL, json=dict(
            session_id=SESSION_ID,
            event_type=etype,
            severity=severity,
            details=details), timeout=2)
    except Exception as e:
        print(f"[Server] {e}")

def save_evidence(frame, reason):
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(EVIDENCE_DIR, f"{reason}_{ts}.jpg")
    cv2.imwrite(path, frame)
    log_event(f"{reason}_screenshot", "low", f"Evidence: {path}")
    print(f"[Evidence] → {path}")

def check_lighting(frame):
    mean = np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if mean < LIGHTING_MIN: return False, f"Too dark ({mean:.0f})"
    if mean > LIGHTING_MAX: return False, f"Too bright ({mean:.0f})"
    return True, f"OK ({mean:.0f})"

def compute_ear(lm, indices):
    pts = np.array([(lm[i].x * W, lm[i].y * H) for i in indices])
    A   = np.linalg.norm(pts[1] - pts[5])
    B   = np.linalg.norm(pts[2] - pts[4])
    C   = np.linalg.norm(pts[0] - pts[3])
    return (A + B) / (2.0 * C + 1e-6)

def compute_mar(lm):
    try:
        top_pts   = np.array([(lm[i].x*W, lm[i].y*H) for i in LIP_TOP])
        bot_pts   = np.array([(lm[i].x*W, lm[i].y*H) for i in LIP_BOTTOM])
        left_pt   = np.array([lm[LIP_LEFT].x*W,  lm[LIP_LEFT].y*H])
        right_pt  = np.array([lm[LIP_RIGHT].x*W, lm[LIP_RIGHT].y*H])
        vert      = np.mean([np.linalg.norm(top_pts[i] - bot_pts[i])
                             for i in range(len(top_pts))])
        horiz     = np.linalg.norm(left_pt - right_pt)
        return float(vert / (horiz + 1e-6))
    except Exception:
        return 0.0

def compute_head_roll(lm):
    try:
        le = np.array([lm[L_EYE_CENTER].x*W, lm[L_EYE_CENTER].y*H])
        re = np.array([lm[R_EYE_CENTER].x*W, lm[R_EYE_CENTER].y*H])
        return float(np.degrees(np.arctan2(re[1]-le[1], re[0]-le[0])))
    except Exception:
        return 0.0

def compute_gaze_dev(lm, iris_idx, el_idx, er_idx):
    try:
        if iris_idx >= len(lm): return 0.0
        ix     = lm[iris_idx].x * W
        lx     = lm[el_idx].x   * W
        rx     = lm[er_idx].x   * W
        center = (lx + rx) / 2.0
        width  = abs(rx - lx)
        if width < 5: return 0.0
        return (ix - center) / width
    except Exception:
        return 0.0

def compute_head_pose(lm):
    nose     = np.array([lm[NOSE_TIP].x*W, lm[NOSE_TIP].y*H])
    chin     = np.array([lm[CHIN].x*W,     lm[CHIN].y*H])
    l_eye    = np.array([lm[33].x*W,       lm[33].y*H])
    r_eye    = np.array([lm[263].x*W,      lm[263].y*H])
    face_vec = chin - nose
    pitch    = float(np.degrees(np.arctan2(face_vec[0], face_vec[1])))
    l_dist   = abs(nose[0] - l_eye[0])
    r_dist   = abs(nose[0] - r_eye[0])
    total    = l_dist + r_dist + 1e-6
    yaw      = float(((r_dist - l_dist) / total) * 90)
    return yaw, pitch

def face_bbox(fl, pad=20):
    xs = [lm.x * W for lm in fl.landmark[:468]]
    ys = [lm.y * H for lm in fl.landmark[:468]]
    x1 = max(0, int(min(xs)) - pad)
    y1 = max(0, int(min(ys)) - pad)
    x2 = min(W, int(max(xs)) + pad)
    y2 = min(H, int(max(ys)) + pad)
    return x1, y1, x2-x1, y2-y1

def preprocess(gray, x, y, w, h):
    if w <= 0 or h <= 0: return None
    roi = gray[max(0,y):min(gray.shape[0],y+h),
               max(0,x):min(gray.shape[1],x+w)]
    if roi.size == 0: return None
    roi = cv2.resize(roi, (200, 200))
    roi = cv2.equalizeHist(roi)
    roi = cv2.GaussianBlur(roi, (5, 5), 0)
    return roi

def put(frame, text, pos, color, scale=0.6, thick=2):
    cv2.putText(frame, text, pos,
                cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)

def banner(frame, text, color):
    ov = frame.copy()
    cv2.rectangle(ov, (0, 0), (W, 60), color, -1)
    cv2.addWeighted(ov, 0.4, frame, 0.6, 0, frame)
    put(frame, text, (15, 40), (255, 255, 255), scale=0.85, thick=2)

def grace_bar(frame, key, label):
    val   = min(grace_counters[key], GRACE_FRAMES)
    ratio = val / GRACE_FRAMES
    bx, by, bw, bh = 15, 68, 200, 8
    cv2.rectangle(frame, (bx, by), (bx+bw, by+bh), (60,60,60), -1)
    cv2.rectangle(frame, (bx, by),
                  (bx+int(bw*ratio), by+bh), (0,165,255), -1)
    put(frame, f"{label} ({val}/{GRACE_FRAMES}f)",
        (bx, by+22), (200,165,100), scale=0.42, thick=1)


# ─── AUDIO ────────────────────────────────────────────────────────────────────
def audio_callback(indata, frames, time_info, status):
    global audio_rms
    rms = float(np.sqrt(np.mean(indata ** 2)))
    with audio_lock:
        audio_rms = rms

def start_audio_monitor():
    try:
        stream = sd.InputStream(
            samplerate = AUDIO_SAMPLE_RATE,
            blocksize  = AUDIO_BLOCK_SIZE,
            channels   = 1,
            dtype      = 'float32',
            callback   = audio_callback
        )
        stream.start()
        print("[AUDIO] ✅ Microphone active.")
        return stream
    except Exception as e:
        print(f"[AUDIO] ⚠️  Could not start mic: {e}")
        return None

def get_audio_rms():
    with audio_lock:
        return audio_rms

def draw_audio_bar(frame, rms, lip_moving):
    bx, by = W-130, H-75
    bw, bh = 110, 10
    ratio  = min(rms / (AUDIO_THRESHOLD * 2), 1.0)
    color  = (0,255,0) if rms < AUDIO_THRESHOLD else (0,0,255)
    cv2.rectangle(frame, (bx,by), (bx+bw,by+bh), (40,40,40), -1)
    cv2.rectangle(frame, (bx,by), (bx+int(bw*ratio),by+bh), color, -1)
    cv2.rectangle(frame, (bx,by), (bx+bw,by+bh), (80,80,80), 1)
    tx = bx + int(bw * 0.5)
    cv2.line(frame, (tx,by-2), (tx,by+bh+2), (200,200,0), 1)
    put(frame, f"MIC:{rms:.3f}",
        (bx, by-6), (150,150,150), scale=0.36, thick=1)
    lip_color = (0,200,0) if lip_moving else (100,100,100)
    put(frame, f"LIP:{'MOVING' if lip_moving else 'STILL'}",
        (bx, by+22), lip_color, scale=0.36, thick=1)


# ─── EARPHONE DETECTION ───────────────────────────────────────────────────────
def run_visual_ear_detection(frame, lm):
    """
    Multi-type detector:
    A) Head width anomaly    — over-ear headphones make head wider
    B) Dark band above head  — headphone arc/band on top of head
    C) Ear region blobs      — white (AirPods) or dark (earphones)
    """
    found_any = False

    # ── Get face bounding box ─────────────────────────────────────────────────
    face_xs  = [lm[i].x * W for i in range(min(468, len(lm)))]
    face_ys  = [lm[i].y * H for i in range(min(468, len(lm)))]
    face_x1  = int(min(face_xs))
    face_y1  = int(min(face_ys))
    face_x2  = int(max(face_xs))
    face_y2  = int(max(face_ys))
    face_w   = face_x2 - face_x1
    face_h   = face_y2 - face_y1

    # ── A: Head width check ───────────────────────────────────────────────────
    width_ratio = face_w / W
    if width_ratio > HEAD_WIDTH_THRESHOLD:
        found_any = True
        cv2.rectangle(frame,
                      (face_x1-8, face_y1-8),
                      (face_x2+8, face_y2+8),
                      (0, 165, 255), 2)
        put(frame, f"Wide head ({width_ratio:.2f})",
            (face_x1, face_y1-14),
            (0, 165, 255), scale=0.42, thick=1)

    # ── B: Dark band above head ───────────────────────────────────────────────
    pad_above = int(face_h * 0.55)
    above_x1  = max(0, face_x1 - 50)
    above_y1  = max(0, face_y1 - pad_above)
    above_x2  = min(W, face_x2 + 50)
    above_y2  = face_y1

    cv2.rectangle(frame, (above_x1, above_y1),
                  (above_x2, above_y2), (50, 50, 50), 1)

    face_crop  = frame[face_y1:face_y2, face_x1:face_x2]
    above_crop = frame[above_y1:above_y2, above_x1:above_x2]

    if face_crop.size > 0 and above_crop.size > 0:
        # Get face skin brightness as reference
        face_gray  = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
        face_mean  = np.mean(face_gray)

        above_gray = cv2.cvtColor(above_crop, cv2.COLOR_BGR2GRAY)

        # Threshold: anything darker than 60% of face skin = object
        thresh_val = int(face_mean * 0.60)
        _, thresh  = cv2.threshold(above_gray, thresh_val,
                                   255, cv2.THRESH_BINARY_INV)

        # Morphological cleanup
        k      = np.ones((5, 5), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN,  k)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, k)

        dark_ratio = np.sum(thresh > 0) / max(thresh.size, 1)

        if dark_ratio > 0.15:
            found_any = True
            cv2.rectangle(frame, (above_x1, above_y1),
                          (above_x2, above_y2), (0, 165, 255), 2)
            put(frame, f"Headband ({dark_ratio:.0%})",
                (above_x1+2, above_y1-6),
                (0, 165, 255), scale=0.42, thick=1)

    # ── C: Ear region blobs ───────────────────────────────────────────────────
    for side, indices in [("L", L_EAR_REGION), ("R", R_EAR_REGION)]:
        xs = [lm[i].x * W for i in indices]
        ys = [lm[i].y * H for i in indices]
        x1 = max(0, int(min(xs)) - EAR_PAD)
        y1 = max(0, int(min(ys)) - EAR_PAD)
        x2 = min(W, int(max(xs)) + EAR_PAD)
        y2 = min(H, int(max(ys)) + EAR_PAD)
        cv2.rectangle(frame, (x1,y1), (x2,y2), (60,60,60), 1)
        put(frame, side, (x1+4, y1+12),
            (80,80,80), scale=0.38, thick=1)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            continue

        ch, cw = crop.shape[:2]
        hsv    = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

        # White earbuds (AirPods)
        white_mask = cv2.inRange(hsv,
                                  np.array([0,   0, 170]),
                                  np.array([180, 50, 255]))

        # Dark earphones/headphones at ear
        dark_mask  = cv2.inRange(hsv,
                                  np.array([0,   0,   0]),
                                  np.array([180, 255, 70]))

        # Remove skin tone from both
        skin_mask  = cv2.inRange(hsv,
                                  np.array([0,  20,  80]),
                                  np.array([25, 170, 255]))
        no_skin    = cv2.bitwise_not(skin_mask)
        white_mask = cv2.bitwise_and(white_mask, no_skin)
        dark_mask  = cv2.bitwise_and(dark_mask,  no_skin)

        for mask, obj_label in [(white_mask, "Earbud"),
                                 (dark_mask,  "Headphone")]:
            k    = np.ones((3, 3), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
            mask = cv2.dilate(mask, k, iterations=2)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                            cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 200 or area > 12000:
                    continue
                bx, by, bw, bh = cv2.boundingRect(cnt)
                aspect          = bh / max(bw, 1)
                if not (0.3 < aspect < 5.0):
                    continue
                if by < ch * 0.05:
                    continue
                solidity = area / max(bw * bh, 1)
                if solidity < 0.25:
                    continue
                found_any = True
                ax1 = x1+bx; ay1 = y1+by
                ax2 = x1+bx+bw; ay2 = y1+by+bh
                cv2.rectangle(frame, (ax1,ay1), (ax2,ay2),
                              (0,165,255), 2)
                put(frame, f"{obj_label}? {side}",
                    (ax1, ay1-6), (0,165,255), scale=0.42, thick=1)

    return found_any

def compute_earphone_score(frame, lm, rms, mar, roll, ear_visual):
    global earphone_signals
    score   = 0.0
    signals = {}

    # ── Signal 1: Visual (white/dark blob near ear or headband) ───────────────
    if ear_visual:
        score           += 1.5
        signals["visual"] = True
    else:
        signals["visual"] = False

    # ── Signal 2: Audio present + lips not moving ─────────────────────────────
    audio_present = rms > AUDIO_THRESHOLD
    lip_still     = mar < LIP_MOVEMENT_THRESHOLD
    audio_history.append(1 if audio_present else 0)
    lip_history.append(1 if not lip_still else 0)

    if len(audio_history) >= 20:
        audio_ratio = sum(list(audio_history)[-40:]) / min(40, len(audio_history))
        lip_ratio   = sum(list(lip_history)[-40:])   / min(40, len(lip_history))
        if audio_ratio > 0.25 and lip_ratio < 0.20:
            score              += 1.5
            signals["audio_lip"] = True
        else:
            signals["audio_lip"] = False
    else:
        signals["audio_lip"] = False

    # ── Signal 3: Head roll asymmetry ─────────────────────────────────────────
    if abs(roll) > HEAD_ROLL_THRESHOLD:
        score          += 0.8
        signals["roll"] = True
    else:
        signals["roll"] = False

    # ── Signal 4: Head width anomaly ──────────────────────────────────────────
    face_xs     = [lm[i].x * W for i in range(min(468, len(lm)))]
    face_w      = max(face_xs) - min(face_xs)
    width_ratio = face_w / W
    if width_ratio > HEAD_WIDTH_THRESHOLD:
        score           += 1.2
        signals["width"] = True
    else:
        signals["width"] = False

    # ── Signal 5: Sustained audio ─────────────────────────────────────────────
    if audio_present:
        score            += 0.5
        signals["audio"]  = True
    else:
        signals["audio"]  = False

    earphone_signals = signals
    return score

def draw_earphone_panel(frame, score, signals, grace):
    px, py = 10, H-135
    pw, ph = 230, 125
    panel  = frame.copy()
    cv2.rectangle(panel, (px,py), (px+pw,py+ph), (20,20,20), -1)
    cv2.addWeighted(panel, 0.7, frame, 0.3, 0, frame)
    cv2.rectangle(frame, (px,py), (px+pw,py+ph), (60,60,60), 1)
    put(frame, "Earphone Detection",
        (px+6, py+14), (180,180,180), scale=0.4, thick=1)

    ratio = min(score / EARPHONE_CONFIRM_SCORE, 1.0)
    bx, by = px+6, py+22
    bw, bh = pw-12, 8
    cv2.rectangle(frame, (bx,by), (bx+bw,by+bh), (40,40,40), -1)
    bar_color = (0,200,0) if score < EARPHONE_CONFIRM_SCORE \
                else (0,0,255)
    cv2.rectangle(frame, (bx,by),
                  (bx+int(bw*ratio),by+bh), bar_color, -1)
    put(frame, f"Score: {score:.1f}/{EARPHONE_CONFIRM_SCORE}",
        (bx, by+20), (200,200,200), scale=0.38, thick=1)

    signal_labels = [
        ("visual",    "Visual near ear"),
        ("audio_lip", "Sound+lips still"),
        ("roll",      "Head tilt"),
        ("width",     "Head too wide"),
        ("audio",     "Audio present"),
    ]
    for i, (key, label) in enumerate(signal_labels):
        active = signals.get(key, False)
        color  = (0,220,0) if active else (80,80,80)
        dot_x  = px + 12
        dot_y  = py + 50 + i * 15
        cv2.circle(frame, (dot_x, dot_y), 4, color, -1)
        put(frame, label, (dot_x+10, dot_y+4),
            color, scale=0.35, thick=1)

    if grace > 0:
        put(frame, f"Grace: {grace}/{EARPHONE_GRACE_FRAMES}",
            (px+6, py+ph-8), (200,165,0), scale=0.38, thick=1)


# ─── YOLO OBJECT DETECTION ────────────────────────────────────────────────────
def run_yolo_fullframe(frame):
    small  = cv2.resize(frame, (320, 320))
    sx, sy = W/320, H/320
    found  = []
    res    = yolo_model(small, verbose=False, conf=YOLO_CONFIDENCE)[0]
    for box in res.boxes:
        cls_id       = int(box.cls[0])
        conf         = float(box.conf[0])
        x1,y1,x2,y2 = box.xyxy[0].tolist()
        x1,y1,x2,y2 = int(x1*sx),int(y1*sy),int(x2*sx),int(y2*sy)
        label        = yolo_model.names[cls_id].lower()
        if cls_id in CHEAT_OBJECTS:
            name, _ = CHEAT_OBJECTS[cls_id]
            if cls_id == 63 and (y2-y1) > (x2-x1):
                continue
            found.append((name, conf, (x1,y1,x2,y2)))
        elif any(cl in label for cl in CUSTOM_CHEAT_LABELS):
            found.append((label.title(), conf, (x1,y1,x2,y2)))
    return found

def filter_by_persistence(new_detections):
    global object_history
    current_labels = {o[0] for o in new_detections}
    updated = {}
    for obj in new_detections:
        updated[obj[0]] = object_history.get(obj[0], 0) + 1
    for label in list(object_history.keys()):
        if label not in current_labels:
            updated[label] = 0
    object_history = updated
    return [o for o in new_detections
            if object_history.get(o[0], 0) >= YOLO_MIN_FRAMES]

def draw_object_boxes(frame, objects):
    for label, conf, (x1,y1,x2,y2) in objects:
        cv2.rectangle(frame,(x1,y1),(x2,y2),(0,0,255),2)
        tag = f"{label} {conf*100:.0f}%"
        tw  = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX,
                              0.5, 1)[0][0]
        cv2.rectangle(frame,(x1,y1-20),(x1+tw+6,y1),(0,0,180),-1)
        put(frame,tag,(x1+3,y1-5),(255,255,255),scale=0.5,thick=1)

def draw_pause_overlay(frame, objects):
    cv2.rectangle(frame,(0,0),(W,H),(0,0,0),-1)
    t     = time.time()
    alpha = int(120+60*abs(np.sin(t*3)))
    cv2.rectangle(frame,(4,4),(W-4,H-4),(0,0,alpha),12)
    cw = min(460,W-40); ch = 190+len(objects)*28
    cx = (W-cw)//2;     cy = (H-ch)//2
    cv2.rectangle(frame,(cx,cy),(cx+cw,cy+ch),(30,30,30),-1)
    cv2.rectangle(frame,(cx,cy),(cx+cw,cy+ch),(0,0,200),2)
    bx = cx+cw//2-18; by = cy+18
    cv2.rectangle(frame,(bx,   by),(bx+12,by+34),(0,0,200),-1)
    cv2.rectangle(frame,(bx+18,by),(bx+30,by+34),(0,0,200),-1)
    put(frame,"EXAM PAUSED",
        (cx+cw//2-110,cy+78),(255,255,255),scale=1.0,thick=2)
    put(frame,"Prohibited item detected",
        (cx+cw//2-130,cy+108),(100,100,255),scale=0.55,thick=1)
    cv2.line(frame,(cx+16,cy+124),(cx+cw-16,cy+124),(60,60,60),1)
    for i,(label,conf,_) in enumerate(objects[:5]):
        dy = cy+142+i*28
        cv2.circle(frame,(cx+24,dy),4,(0,0,200),-1)
        put(frame,f"{label} ({conf*100:.0f}%)",
            (cx+36,dy+5),(210,170,170),scale=0.5,thick=1)
    put(frame,"Remove item to resume",
        (cx+cw//2-100,cy+ch-12),(130,130,130),scale=0.45,thick=1)


# ─── PHASE 0: LIGHTING GATE ───────────────────────────────────────────────────
def lighting_gate():
    print("\n[LIGHTING] Checking...")
    while True:
        ret, frame = video_cap.read()
        if not ret: continue
        ok, msg = check_lighting(frame)
        banner(frame,f"Lighting: {msg}",(0,100,0) if ok else (0,0,140))
        if ok:
            put(frame,"Good — press SPACE to start enrollment",
                (15,90),(180,255,180),scale=0.55)
        else:
            put(frame,"Adjust lighting",
                (15,90),(100,100,255),scale=0.55)
        cv2.imshow("AI Proctor", frame)
        key = cv2.waitKey(1)
        if key == ord(' ') and ok: break
        if key == ord('q'):
            video_cap.release(); cv2.destroyAllWindows(); exit()
    print("[LIGHTING] ✅ Good.\n")


# ─── PHASE 1: ENROLLMENT ──────────────────────────────────────────────────────
DIRECTIONS = [
    "Look STRAIGHT at the camera",
    "Turn slightly LEFT",
    "Turn slightly RIGHT",
    "Tilt slightly UP",
    "Tilt slightly DOWN",
]

def enroll_user():
    print("[ENROLLMENT] Starting...")
    samples, labels = [], []
    for direction in DIRECTIONS:
        print(f"\n  ➡️  {direction}")
        collected = 0
        while collected < SAMPLES_NEEDED:
            ret, frame = video_cap.read()
            if not ret: continue
            rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)
            gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            banner(frame,f"ENROLL: {direction}",(20,80,180))
            put(frame,f"Samples: {collected}/{SAMPLES_NEEDED}",
                (15,88),(255,255,0),scale=0.55)
            put(frame,"No earphones — plain background recommended",
                (15,112),(150,150,200),scale=0.45)
            if results.multi_face_landmarks:
                if len(results.multi_face_landmarks) == 1:
                    fl      = results.multi_face_landmarks[0]
                    lm      = fl.landmark
                    x,y,fw,fh = face_bbox(fl)
                    cv2.rectangle(frame,(x,y),(x+fw,y+fh),(0,255,0),2)
                    avg_ear = (compute_ear(lm,L_EAR_IDX) +
                               compute_ear(lm,R_EAR_IDX)) / 2
                    if avg_ear > BLINK_THRESHOLD:
                        roi = preprocess(gray,x,y,fw,fh)
                        if roi is not None:
                            samples.append(roi)
                            labels.append(0)
                            collected += 1
                            put(frame,f"Capturing EAR:{avg_ear:.2f}",
                                (x,y-10),(0,255,0),scale=0.5)
                    else:
                        put(frame,"Open eyes wider",
                            (x,y-10),(0,165,255),scale=0.5)
                else:
                    put(frame,"One person only",
                        (15,140),(0,165,255),scale=0.55)
            else:
                put(frame,"No face — move closer",
                    (15,140),(0,0,255),scale=0.55)
            cv2.imshow("AI Proctor", frame)
            if cv2.waitKey(1) == ord("q"):
                video_cap.release(); cv2.destroyAllWindows(); exit()
        time.sleep(0.3)
    print("\n[ENROLLMENT] Training recognizer...")
    recognizer.train(samples, np.array(labels))
    print("[ENROLLMENT] ✅ Done!\n")
    log_event("enrollment_complete","low",
              f"Enrolled at {datetime.utcnow().isoformat()}")


# ─── PHASE 2: PROCTORING ──────────────────────────────────────────────────────
def proctor():
    global blink_frames, down_frames, screen_frames
    global glance_count, phone_and_down_frames
    global object_grace, pause_linger, exam_paused
    global detected_objects, ear_objects, voice_frames
    global earphone_grace, earphone_score, earphone_signals

    print(f"\n[PROCTOR] Session: {SESSION_ID}")
    audio_stream          = start_audio_monitor()
    last_log              = {}
    blink_frames          = 0
    down_frames           = 0
    screen_frames         = 0
    glance_count          = 0
    phone_and_down_frames = 0
    object_grace          = 0
    pause_linger          = 0
    exam_paused           = False
    detected_objects      = []
    ear_objects           = []
    voice_frames          = 0
    earphone_grace        = 0
    earphone_score        = 0.0
    earphone_signals      = {}
    last_recheck          = time.time()
    recheck_needed        = False
    sc                    = None
    yaw = pitch = avg_gaze = roll = mar = 0.0
    lip_moving            = False
    is_natural_writing    = False
    is_suspicious_down    = False
    is_looking_down       = False
    is_turned             = False
    earphone_flagged      = False
    frame_count           = 0
    current_face_lm       = None
    ear_visual_found      = False
    fps_time              = time.time()
    fps                   = 0.0

    def cooldown(key, secs=5):
        now = time.time()
        if now - last_log.get(key, 0) > secs:
            last_log[key] = now
            return True
        return False

    while True:
        ret, frame = video_cap.read()
        if not ret or frame is None: continue

        frame_count += 1
        now          = time.time()
        fps          = 0.9*fps + 0.1*(1.0/max(now-fps_time,1e-6))
        fps_time     = now

        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb)
        gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        lit_ok, lit_msg    = check_lighting(frame)
        nfaces             = (len(results.multi_face_landmarks)
                               if results.multi_face_landmarks else 0)
        unauth             = False
        eyes_shut          = False
        gaze_off           = False
        is_looking_down    = False
        is_turned          = False
        is_natural_writing = False
        is_suspicious_down = False
        current_face_lm    = None
        ear_visual_found   = False

        if now - last_recheck > 600:
            recheck_needed = True
            last_recheck   = now

        if results.multi_face_landmarks:
            current_face_lm = results.multi_face_landmarks[0].landmark

        # ── Audio ──────────────────────────────────────────────────────────────
        rms          = get_audio_rms()
        voice_active = rms > AUDIO_THRESHOLD
        if voice_active: voice_frames += 1
        else:            voice_frames  = max(0, voice_frames-1)
        voice_alert = voice_frames >= AUDIO_VOICE_FRAMES

        # ── YOLO ───────────────────────────────────────────────────────────────
        if frame_count % YOLO_EVERY_N == 0:
            raw              = run_yolo_fullframe(frame)
            detected_objects = filter_by_persistence(raw)
        else:
            filter_by_persistence([])

        all_objects      = detected_objects
        has_cheat_object = len(all_objects) > 0
        phone_visible    = any(o[0]=="Phone" for o in all_objects)

        # ── Visual ear detection ───────────────────────────────────────────────
        if frame_count % EAR_EVERY_N == 0 and current_face_lm:
            ear_visual_found = run_visual_ear_detection(
                frame, current_face_lm)
        elif current_face_lm:
            for side, indices in [("L",L_EAR_REGION),("R",R_EAR_REGION)]:
                xs = [current_face_lm[i].x*W for i in indices]
                ys = [current_face_lm[i].y*H for i in indices]
                x1 = max(0,int(min(xs))-EAR_PAD)
                y1 = max(0,int(min(ys))-EAR_PAD)
                x2 = min(W,int(max(xs))+EAR_PAD)
                y2 = min(H,int(max(ys))+EAR_PAD)
                cv2.rectangle(frame,(x1,y1),(x2,y2),(60,60,60),1)

        # ── Object pause logic ─────────────────────────────────────────────────
        if has_cheat_object:
            object_grace += 1
            pause_linger  = PAUSE_LINGER_FRAMES
        else:
            object_grace = max(0, object_grace-2)
            if pause_linger > 0: pause_linger -= 1

        if object_grace >= OBJECT_GRACE_FRAMES and not exam_paused:
            exam_paused = True
            names = ", ".join(set(o[0] for o in all_objects))
            log_event("cheat_object_detected","high",f"Detected:{names}")
            save_evidence(frame,"cheat_object")

        if exam_paused and pause_linger==0 and not has_cheat_object:
            exam_paused  = False
            object_grace = 0
            log_event("exam_resumed","low","Object removed")

        if all_objects:
            draw_object_boxes(frame, all_objects)

        # ── Pause screen ───────────────────────────────────────────────────────
        if exam_paused:
            draw_pause_overlay(frame, all_objects)
            put(frame,
                f"Obj:{object_grace}/{OBJECT_GRACE_FRAMES} "
                f"Linger:{pause_linger} FPS:{fps:.0f}",
                (10,H-10),(150,150,150),scale=0.38,thick=1)
            cv2.imshow("AI Proctor", frame)
            if cv2.waitKey(1) == ord("q"): break
            continue

        # ── Face analysis ──────────────────────────────────────────────────────
        if results.multi_face_landmarks:
            for fl in results.multi_face_landmarks:
                lm = fl.landmark
                x, y, fw, fh = face_bbox(fl)

                roi = preprocess(gray,x,y,fw,fh)
                if roi is not None:
                    lbl, conf = recognizer.predict(roi)
                    conf_buffer.append(conf)
                    sc   = float(np.mean(conf_buffer))
                    auth = lbl==0 and sc < CONFIDENCE_THRESHOLD
                    if not auth: unauth = True
                    if recheck_needed and auth: recheck_needed = False
                    bc = (0,255,0) if auth else (0,0,255)
                    cv2.rectangle(frame,(x,y),(x+fw,y+fh),bc,2)
                    put(frame,
                        "Auth" if auth else f"Unknown({sc:.0f})",
                        (x,y-10),bc,scale=0.5)

                avg_e = (compute_ear(lm,L_EAR_IDX) +
                         compute_ear(lm,R_EAR_IDX)) / 2
                if avg_e < BLINK_THRESHOLD:
                    blink_frames += 1
                    if blink_frames >= BLINK_MAX_FRAMES: eyes_shut = True
                else:
                    blink_frames = 0

                try:
                    dg_r     = compute_gaze_dev(lm,R_IRIS,R_EYE_L,R_EYE_R)
                    dg_l     = compute_gaze_dev(lm,L_IRIS,L_EYE_L,L_EYE_R)
                    avg_gaze = (abs(dg_r)+abs(dg_l)) / 2
                    if avg_gaze > GAZE_THRESHOLD: gaze_off = True
                except Exception:
                    avg_gaze = 0.0

                yaw, pitch = compute_head_pose(lm)
                roll       = compute_head_roll(lm)
                mar        = compute_mar(lm)
                lip_moving = mar > LIP_MOVEMENT_THRESHOLD

                pitch_history.append(pitch)
                is_looking_down = pitch > HEAD_PITCH_THRESHOLD
                is_turned       = abs(yaw) > HEAD_YAW_THRESHOLD

                if is_looking_down:
                    down_frames  += 1
                    screen_frames = 0
                    phone_and_down_frames = (phone_and_down_frames+1
                                             if phone_visible else 0)
                else:
                    if 0 < down_frames <= WRITING_GLANCE_MAX:
                        glance_count += 1
                    down_frames           = 0
                    screen_frames        += 1
                    phone_and_down_frames = 0

                is_natural_writing = (
                    0 < down_frames <= WRITING_GLANCE_MAX and
                    not is_turned and not phone_visible)
                is_suspicious_down = (
                    down_frames > SUSPICIOUS_SUSTAINED or
                    phone_and_down_frames > PHONE_DOWN_FRAMES or
                    (is_looking_down and is_turned))

        # ── Earphone combined score ────────────────────────────────────────────
        if current_face_lm:
            earphone_score = compute_earphone_score(
                frame, current_face_lm, rms, mar,
                roll, ear_visual_found)

            if earphone_score >= EARPHONE_CONFIRM_SCORE:
                earphone_grace += 1
            else:
                earphone_grace = max(0, earphone_grace-1)

            earphone_flagged = earphone_grace >= EARPHONE_GRACE_FRAMES
        else:
            earphone_flagged = False

        draw_earphone_panel(frame, earphone_score,
                            earphone_signals, earphone_grace)

        # ── Grace gates ────────────────────────────────────────────────────────
        real_face_missing = tick_grace("face_missing",   nfaces==0)
        real_multi_face   = tick_grace("multiple_faces", nfaces>=2)
        real_wrong_person = tick_grace("wrong_person",   unauth)
        real_eyes_shut    = tick_grace("eyes_closed",    eyes_shut)
        real_head_away    = tick_grace("head_away",
                                       is_suspicious_down or
                                       (is_turned and not is_looking_down))
        real_gaze_away    = tick_grace("gaze_away",      gaze_off)
        real_voice        = tick_grace("voice",          voice_alert)

        # ── HUD ───────────────────────────────────────────────────────────────
        draw_audio_bar(frame, rms, lip_moving)
        put(frame,
            f"FPS:{fps:.0f} EAR:{blink_frames} "
            f"Gaze:{avg_gaze:.2f} Yaw:{yaw:.0f} "
            f"Roll:{roll:.1f} MAR:{mar:.2f}",
            (10,H-12),(150,150,150),scale=0.36,thick=1)
        if sc is not None:
            put(frame,
                f"Conf:{sc:.0f}/{CONFIDENCE_THRESHOLD} "
                f"Pitch:{pitch:.0f} Light:{lit_msg}",
                (10,H-30),(180,180,0),scale=0.36,thick=1)
        if has_cheat_object:
            put(frame,
                f"Object: {object_grace}/{OBJECT_GRACE_FRAMES}f",
                (10,H-48),(0,100,255),scale=0.36,thick=1)

        # ── Alert logic ────────────────────────────────────────────────────────
        if real_face_missing:
            banner(frame,"No Face Detected!",(0,0,160))
            if cooldown("face_missing"):
                log_event("face_missing","high","No face")
                save_evidence(frame,"face_missing")

        elif real_multi_face:
            banner(frame,f"WARNING: {nfaces} Faces!",(0,0,180))
            if cooldown("multi_face"):
                log_event("multiple_faces","high",f"{nfaces} faces")
                save_evidence(frame,"multiple_faces")

        elif real_wrong_person:
            banner(frame,"ALERT: Unauthorized Person!",(0,0,200))
            if cooldown("wrong_person"):
                log_event("wrong_person","high",f"conf:{sc:.0f}")
                save_evidence(frame,"wrong_person")

        elif real_eyes_shut:
            banner(frame,f"ALERT: Eyes Closed! ({blink_frames}f)",
                   (0,60,180))
            if cooldown("eyes_closed"):
                log_event("eyes_closed","high",
                          f"closed {blink_frames}f")
                save_evidence(frame,"eyes_closed")

        elif earphone_flagged:
            active = [k for k,v in earphone_signals.items() if v]
            banner(frame,
                   f"ALERT: Earphone/Headphone! [{','.join(active)}]",
                   (0,60,200))
            if cooldown("earphone", secs=10):
                log_event("earphone_detected","high",
                          f"score={earphone_score:.1f} "
                          f"signals={active}")
                save_evidence(frame,"earphone")

        elif real_voice:
            banner(frame,
                   f"ALERT: Voice Detected! (RMS:{rms:.3f})",
                   (0,80,160))
            if cooldown("voice", secs=AUDIO_COOLDOWN):
                log_event("voice_detected","high",
                          f"rms={rms:.4f}")
                save_evidence(frame,"voice_detected")

        elif real_head_away:
            if phone_and_down_frames > PHONE_DOWN_FRAMES:
                msg = f"ALERT: Phone Use! ({phone_and_down_frames}f)"
            elif down_frames > SUSPICIOUS_SUSTAINED:
                msg = f"ALERT: Looking Down Too Long! ({down_frames}f)"
            elif is_looking_down and is_turned:
                msg = "ALERT: Down+Sideways!"
            else:
                msg = f"ALERT: Head Turned! Yaw:{yaw:.0f}"
            banner(frame,msg,(0,100,180))
            if cooldown("head_away"):
                log_event("head_away","medium",
                          f"yaw={yaw:.1f} pitch={pitch:.1f}")

        elif real_gaze_away:
            banner(frame,
                   f"ALERT: Looking Away! ({avg_gaze:.2f})",
                   (0,120,180))
            if cooldown("gaze_away"):
                log_event("gaze_away","medium",
                          f"dev={avg_gaze:.2f}")

        elif is_natural_writing:
            banner(frame,
                   f"Writing — glance #{glance_count}",
                   (0,90,60))

        elif recheck_needed:
            banner(frame,"IDENTITY CHECK: Look at camera",(120,60,0))

        else:
            banner(frame,"Authorized — All Clear",(0,120,0))

        for key, label in [
            ("face_missing", "No face"),
            ("head_away",    "Head turned"),
            ("gaze_away",    "Gaze away"),
            ("eyes_closed",  "Eyes closed"),
            ("wrong_person", "Unknown"),
            ("voice",        "Voice"),
        ]:
            if 0 < grace_counters[key] < GRACE_FRAMES:
                grace_bar(frame, key, label)
                break

        if not lit_ok and cooldown("lighting",secs=15):
            log_event("lighting_issue","low",lit_msg)

        cv2.imshow("AI Proctor", frame)
        if cv2.waitKey(1) == ord("q"):
            break

    if audio_stream:
        audio_stream.stop()
        audio_stream.close()
    video_cap.release()
    cv2.destroyAllWindows()
    log_event("session_ended","low","Session ended")
    print("\n[DONE] Session ended.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    lighting_gate()
    enroll_user()
    proctor()
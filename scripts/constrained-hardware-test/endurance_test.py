"""45-minute full-pipeline endurance test — NOT just face detection.

Runs the actual per-frame proctoring cycle (face detect, 106-pt landmarks,
eye-openness, head-pose, gaze, YOLO object detection, InsightFace identity)
against a real (non-mocked) test image, paced at the real governor-controlled
cadence, for a sustained period. Tracks memory growth (leak detection),
per-model timing over time (degradation detection), and correctness
(detection must stay stable throughout — no silent drift/crash).

Meant to run inside a tightly constrained container (see run_endurance.sh —
--memory=4g, deliberately BELOW the ~8GB industry-minimum target laptop RAM,
to prove real headroom rather than just scraping by).
"""
import os
import sys
import time
import statistics
import resource

import numpy as np
import proctor
from insightface.data import get_image

DURATION_SEC = int(os.environ.get("ENDURANCE_DURATION_SEC", str(45 * 60)))
CHECKIN_EVERY_SEC = 60

proctor._load_yolo()  # lazy-loaded in the real app on first use — force it now

print(f"RETINA_AVAILABLE={proctor.RETINA_AVAILABLE}")
print(f"LANDMARK106_AVAILABLE={proctor.LANDMARK106_AVAILABLE}")
print(f"GAZE_AVAILABLE={proctor.GAZE_AVAILABLE}")
print(f"YOLO_AVAILABLE={proctor.YOLO_AVAILABLE}")
print(f"INSIGHT_AVAILABLE={proctor.INSIGHT_AVAILABLE}")
print(f"TARGET_FPS={proctor.TARGET_FPS} GAZE_EVERY_N={proctor.GAZE_EVERY_N} "
      f"YOLO_EVERY_N={proctor.YOLO_EVERY_N}")
print(f"Running for {DURATION_SEC}s ({DURATION_SEC/60:.1f} min)...")
sys.stdout.flush()

img = get_image('t1')
H, W = img.shape[:2]

governor = proctor._HardwareGovernor()

timings = {"detect": [], "landmark106": [], "eyes": [], "head_pose": [],
           "gaze": [], "yolo": [], "identity": []}
face_counts = []
detection_failures = 0
frame_count = 0

start_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
start_t = time.perf_counter()
last_checkin = start_t
rss_checkpoints = []  # (elapsed_sec, rss_mb) — used to separate one-time
                       # warm-up allocation from a genuine ongoing leak

while (time.perf_counter() - start_t) < DURATION_SEC:
    frame_count += 1
    governor.maybe_update()
    fps = governor.effective_fps
    frame_deadline = time.perf_counter() + (1.0 / max(fps, 0.1))

    t0 = time.perf_counter()
    faces = proctor.detect_faces(img)
    timings["detect"].append(time.perf_counter() - t0)
    face_counts.append(len(faces))
    if not faces:
        detection_failures += 1
    else:
        bbox, lm_2d = faces[0]
        x1, y1, x2, y2 = [max(0, v) for v in bbox]
        x2, y2 = min(W, x2), min(H, y2)
        face_crop = img[y1:y2, x1:x2]

        _head_n = proctor.GAZE_EVERY_N * (2 if fps < proctor.TARGET_FPS else 1)
        if frame_count % _head_n == 0:
            t0 = time.perf_counter()
            _ = proctor.get_head_pose(lm_2d, W, H)
            timings["head_pose"].append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            _ = proctor.eyes_open_from_landmarks(img, bbox)
            timings["landmark106"].append(time.perf_counter() - t0)

        _gaze_n = proctor.GAZE_EVERY_N * (2 if fps < proctor.TARGET_FPS else 1)
        if proctor.GAZE_AVAILABLE and face_crop.size > 0 and frame_count % _gaze_n == 0:
            t0 = time.perf_counter()
            _ = proctor._gaze_engine.estimate(face_crop)
            timings["gaze"].append(time.perf_counter() - t0)

        _yolo_n = 1 if fps < 5 else proctor.YOLO_EVERY_N
        if proctor.YOLO_AVAILABLE and frame_count % _yolo_n == 0:
            t0 = time.perf_counter()
            _ = proctor._yolo_infer(proctor._yolo_session, img)
            timings["yolo"].append(time.perf_counter() - t0)

        if proctor.INSIGHT_AVAILABLE and frame_count % proctor.WRONG_PERSON_CHECK_FREQ == 0:
            t0 = time.perf_counter()
            _ = proctor.get_face_embedding_from_crop(img, lm_2d)
            timings["identity"].append(time.perf_counter() - t0)

    now = time.perf_counter()
    if now - last_checkin >= CHECKIN_EVERY_SEC:
        last_checkin = now
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        elapsed = now - start_t
        rss_checkpoints.append((elapsed, rss_mb))
        print(f"[{elapsed/60:5.1f} min] frame={frame_count} fps={fps:.1f} "
              f"rss={rss_mb:.1f}MB faces={face_counts[-1]} "
              f"detect_avg={statistics.mean(timings['detect'][-100:])*1000:.1f}ms")
        sys.stdout.flush()

    sleep_for = frame_deadline - time.perf_counter()
    if sleep_for > 0:
        time.sleep(sleep_for)

end_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
elapsed = time.perf_counter() - start_t

print("\n===== ENDURANCE TEST SUMMARY =====")
print(f"Duration: {elapsed/60:.1f} min, {frame_count} frames "
      f"({frame_count/elapsed:.2f} fps actual)")
print(f"RSS: start={start_rss_mb:.1f}MB end={end_rss_mb:.1f}MB "
      f"growth={end_rss_mb - start_rss_mb:+.1f}MB")

# Separate one-time warm-up allocation (model sessions, buffer pools) from a
# genuine ongoing leak by comparing growth in the second half of the run
# against the first half — a real leak keeps growing throughout; warm-up
# plateaus after the first checkpoint or two.
late_growth_mb = 0.0
late_growth_rate_mb_per_min = 0.0
if len(rss_checkpoints) >= 4:
    mid = len(rss_checkpoints) // 2
    t_first, rss_first = rss_checkpoints[mid]
    t_last, rss_last = rss_checkpoints[-1]
    late_growth_mb = rss_last - rss_first
    span_min = (t_last - t_first) / 60.0
    late_growth_rate_mb_per_min = late_growth_mb / span_min if span_min > 0 else 0.0
    print(f"Second-half RSS growth: {late_growth_mb:+.1f}MB over {span_min:.1f} min "
          f"({late_growth_rate_mb_per_min:+.2f} MB/min) — this is the leak signal, "
          f"not the total (which includes one-time model warm-up)")
print(f"Detection failures (0 faces on a known-face image): "
      f"{detection_failures}/{frame_count}")
print(f"Face count stability: min={min(face_counts)} max={max(face_counts)} "
      f"(should be constant on a static test image)")
for name, samples in timings.items():
    if samples:
        print(f"{name:12s}: n={len(samples):5d}  "
              f"mean={statistics.mean(samples)*1000:7.2f}ms  "
              f"p95={sorted(samples)[int(len(samples)*0.95)]*1000:7.2f}ms  "
              f"max={max(samples)*1000:7.2f}ms")
    else:
        print(f"{name:12s}: n=0 (never ran — model unavailable or cadence never hit)")

# Fail loudly on real problems, not just print-and-hope.
assert detection_failures == 0, f"{detection_failures} frames failed to detect the face at all"
assert min(face_counts) == max(face_counts), "face count was not stable across the run"
assert late_growth_rate_mb_per_min < 5.0, (
    f"Second-half RSS growth rate {late_growth_rate_mb_per_min:.2f} MB/min looks like an "
    f"ongoing leak, not one-time warm-up (extrapolated over 45 min: "
    f"{late_growth_rate_mb_per_min * 45:.0f}MB)")
print("\nENDURANCE TEST PASSED")

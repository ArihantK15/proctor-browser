"""
Memory profiling for proctor.py (client-side daemon).

Measures the RAM footprint of each heavy dependency to determine
if the student's machine can handle the proctoring pipeline alongside
the exam browser.

Run: python3 scripts/profile_proctor_memory.py
"""
import os
import sys
import gc
import time
import resource

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def mem_mb():
    """Current RSS in MB."""
    import subprocess
    try:
        pid = os.getpid()
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True
        )
        return int(result.stdout.strip()) / 1024  # KB → MB
    except Exception:
        # Fallback to resource (less accurate on macOS)
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return ru.ru_maxrss / 1024


def gc_collect():
    gc.collect()
    gc.collect()
    gc.collect()


def main():
    print("=" * 60)
    print("PROCTOR.PY MEMORY PROFILE")
    print("=" * 60)

    # ── Baseline ────────────────────────────────────────────────────
    gc_collect()
    baseline = mem_mb()
    print(f"\n  Baseline (Python): {baseline:.0f} MB")

    # ── NumPy ───────────────────────────────────────────────────────
    import numpy as np
    gc_collect()
    after_numpy = mem_mb()
    print(f"  + numpy:             {after_numpy - baseline:+.0f} MB  (total: {after_numpy:.0f} MB)")

    # ── OpenCV ──────────────────────────────────────────────────────
    import cv2
    gc_collect()
    after_cv2 = mem_mb()
    print(f"  + cv2:               {after_cv2 - after_numpy:+.0f} MB  (total: {after_cv2:.0f} MB)")

    # ── Ultralytics (YOLOv8) ────────────────────────────────────────
    try:
        from ultralytics import YOLO
        gc_collect()
        after_ultralytics = mem_mb()
        print(f"  + ultralytics:     {after_ultralytics - after_cv2:+.0f} MB  (total: {after_ultralytics:.0f} MB)")
    except ImportError:
        print("  + ultralytics:     NOT INSTALLED (skipped)")
        after_ultralytics = after_cv2

    # ── InsightFace ─────────────────────────────────────────────────
    try:
        from insightface.app import FaceAnalysis
        gc_collect()
        after_insight = mem_mb()
        print(f"  + insightface:     {after_insight - after_ultralytics:+.0f} MB  (total: {after_insight:.0f} MB)")
    except ImportError:
        print("  + insightface:     NOT INSTALLED (skipped)")
        after_insight = after_ultralytics

    # ── YOLO model load ─────────────────────────────────────────────
    yolo_mem = 0
    yolo_path = os.path.join(os.path.dirname(__file__), "..", "models", "yolo_cheat.pt")
    if os.path.exists(yolo_path):
        try:
            model = YOLO(yolo_path)
            gc_collect()
            after_yolo_load = mem_mb()
            yolo_mem = after_yolo_load - after_insight
            print(f"  + YOLO model load: {yolo_mem:+.0f} MB  (total: {after_yolo_load:.0f} MB)")
        except Exception as e:
            print(f"  + YOLO model load: FAILED ({e})")
            after_yolo_load = after_insight
    else:
        print(f"  + YOLO model load: SKIPPED (no model at {yolo_path})")
        after_yolo_load = after_insight

    # ── InsightFace model init ──────────────────────────────────────
    face_mem = 0
    try:
        _face_app = FaceAnalysis(
            providers=["CPUExecutionProvider"],
            name="antelopev2"
        )
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
        gc_collect()
        after_face_load = mem_mb()
        face_mem = after_face_load - after_yolo_load
        print(f"  + FaceAnalysis init: {face_mem:+.0f} MB  (total: {after_face_load:.0f} MB)")
    except Exception as e:
        print(f"  + FaceAnalysis init: FAILED ({e})")
        after_face_load = after_yolo_load

    # ── Frame processing simulation ─────────────────────────────────
    print(f"\n  Frame Processing Simulation")
    print(f"  {'─' * 40}")

    # Simulate a 640×480 frame
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    gc_collect()
    mem_before_frames = mem_mb()

    # Run 10 inference cycles (fewer for speed, enough for trend detection)
    frame_times = []
    for i in range(10):
        start = time.perf_counter()
        # Simulate frame processing: resize, convert, YOLO inference
        resized = cv2.resize(frame, (416, 416))
        if os.path.exists(yolo_path):
            _ = model(resized, verbose=False, conf=0.3)
        elapsed = (time.perf_counter() - start) * 1000
        frame_times.append(elapsed)

    gc_collect()
    mem_after_frames = mem_mb()
    frame_growth = mem_after_frames - mem_before_frames

    avg_frame_ms = sum(frame_times) / len(frame_times)
    p95_frame_ms = sorted(frame_times)[int(len(frame_times) * 0.95)]

    print(f"  Frame processing (10 cycles):")
    print(f"    Avg: {avg_frame_ms:.0f}ms/frame | P95: {p95_frame_ms:.0f}ms/frame")
    print(f"    Memory growth: {frame_growth:+.0f} MB")
    if frame_growth > 50:
        print(f"    ⚠️ Possible memory leak — growth exceeds 50 MB")
    else:
        print(f"    ✅ Memory stable")

    # ── Summary ─────────────────────────────────────────────────────
    total = mem_mb()
    print(f"\n  {'=' * 40}")
    print(f"  MEMORY SUMMARY")
    print(f"  {'=' * 40}")
    print(f"  Peak RSS:        {total:.0f} MB")
    print(f"  Baseline Python: {baseline:.0f} MB")
    print(f"  Proctor overhead: {total - baseline:.0f} MB")
    print(f"  YOLO model:      {yolo_mem:.0f} MB")
    print(f"  InsightFace:     {face_mem:.0f} MB")

    # 2GB droplet assessment
    droplet_ram = 2048
    os_reserve = 512  # OS + browser + other processes
    available = droplet_ram - os_reserve
    headroom = available - total
    print(f"\n  2GB Droplet Assessment:")
    print(f"    Total RAM:       {droplet_ram} MB")
    print(f"    OS reserve:      {os_reserve} MB (estimated)")
    print(f"    Available:       {available} MB")
    print(f"    Proctor usage:   {total:.0f} MB")
    print(f"    Headroom:        {headroom:.0f} MB")

    if headroom > 512:
        print(f"    ✅ Comfortable headroom ({headroom:.0f} MB free)")
    elif headroom > 256:
        print(f"    ⚠️ Tight but workable ({headroom:.0f} MB free)")
    else:
        print(f"    ❌ Risk of OOM — consider disabling features")

    print(f"\n  Recommendations:")
    if total > 1500:
        print(f"    • Use YOLOv8n (nano) instead of larger models")
        print(f"    • Disable InsightFace if identity verification not needed")
        print(f"    • Reduce camera resolution to 640×480")
    elif total > 1000:
        print(f"    • Monitor memory during long exam sessions (2+ hours)")
        print(f"    • Consider GC tuning: gc.set_threshold(700, 10, 10)")
    else:
        print(f"    • Memory footprint is well within limits")
        print(f"    • No action needed")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()

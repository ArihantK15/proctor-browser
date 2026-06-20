#!/usr/bin/env python3
"""Harvest ear-region crops for training the earbud classifier.

The proctor's EarClassifier (proctor.py) crops the ear region from RetinaFace
5-point landmarks and runs a 2-class CNN over it. This tool reproduces the
*exact same* crop geometry so the training data matches what the model will see
at inference — then you label the crops and train with train_earbud_classifier.py.

Landmark source: insightface buffalo_sc (already a runtime dependency, same pack
the proctor uses), whose kps order is [left_eye, right_eye, nose, left_mouth,
right_mouth] — identical to the indices EarClassifier._estimate_ear_bbox expects.

Usage
-----
  # Live webcam — wear earbuds, vary head angle / lighting for ~10 min:
  python scripts/harvest_ear_crops.py --label earbud --out data/ear --webcam

  # Then WITHOUT earbuds, same variety:
  python scripts/harvest_ear_crops.py --label no_earbud --out data/ear --webcam

  # Or from a folder of saved frames (e.g. exam evidence screenshots):
  python scripts/harvest_ear_crops.py --label earbud --out data/ear --frames path/to/imgs

Output: <out>/<label>/*.png  (square-ish ear crops, ready for training)
"""
import argparse
import glob
import os
import sys
import time

import cv2
import numpy as np


def estimate_ear_bbox(lm_2d: np.ndarray, W: int, H: int, side: str):
    """COPIED VERBATIM from proctor.py EarClassifier._estimate_ear_bbox so the
    harvested crops are geometrically identical to inference-time crops. Keep in
    sync if proctor.py changes."""
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


def build_face_app():
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_sc",
                       providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(320, 320))
    return app


def crops_from_frame(app, frame):
    """Return [(side, crop_bgr), ...] for every detected face's two ears."""
    out = []
    H, W = frame.shape[:2]
    for face in app.get(frame):
        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 5:
            continue
        lm = np.asarray(kps, dtype=np.float32)
        for side in ("left", "right"):
            bbox = estimate_ear_bbox(lm, W, H, side)
            if bbox is None:
                continue
            x1, y1, x2, y2 = bbox
            crop = frame[y1:y2, x1:x2]
            if crop.size:
                out.append((side, crop))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["earbud", "no_earbud"])
    ap.add_argument("--out", default="data/ear", help="dataset root")
    ap.add_argument("--webcam", action="store_true")
    ap.add_argument("--cam-index", type=int, default=0)
    ap.add_argument("--frames", help="dir of image files to process instead of webcam")
    ap.add_argument("--max", type=int, default=4000, help="stop after N crops")
    ap.add_argument("--every", type=int, default=5, help="webcam: sample every Nth frame")
    args = ap.parse_args()

    dst = os.path.join(args.out, args.label)
    os.makedirs(dst, exist_ok=True)
    app = build_face_app()
    saved = 0

    def save(crop, tag):
        nonlocal saved
        p = os.path.join(dst, f"{args.label}_{tag}_{saved:06d}.png")
        cv2.imwrite(p, crop)
        saved += 1

    if args.frames:
        files = sorted(sum([glob.glob(os.path.join(args.frames, e))
                            for e in ("*.png", "*.jpg", "*.jpeg")], []))
        for f in files:
            img = cv2.imread(f)
            if img is None:
                continue
            for side, crop in crops_from_frame(app, img):
                save(crop, side)
                if saved >= args.max:
                    break
            if saved >= args.max:
                break
    else:
        cap = cv2.VideoCapture(args.cam_index,
                               cv2.CAP_DSHOW if sys.platform.startswith("win") else 0)
        if not cap.isOpened():
            print("ERROR: cannot open webcam"); sys.exit(1)
        print(f"[harvest] webcam open — collecting '{args.label}' crops. Ctrl+C to stop.")
        n = 0
        try:
            while saved < args.max:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05); continue
                n += 1
                if n % args.every:
                    continue
                for side, crop in crops_from_frame(app, frame):
                    save(crop, side)
                if saved and saved % 100 == 0:
                    print(f"[harvest] {saved} crops")
        except KeyboardInterrupt:
            pass
        finally:
            cap.release()

    print(f"[harvest] done — {saved} crops in {dst}")


if __name__ == "__main__":
    main()

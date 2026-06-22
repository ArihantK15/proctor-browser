#!/usr/bin/env python3
"""Live webcam smoke-test for the proctoring object detector.

Reuses proctor.py's EXACT model load + decode + confidence gate + label map, so
what you see here is exactly what the proctor will see — but with NO server, no
gaze/audio/identity, no Electron build. Just the detector and a preview window.

Run:
    cd <this repo>
    python3 scripts/test_detector_webcam.py          # camera 0
    CAM=1 python3 scripts/test_detector_webcam.py     # a different camera

Green box  = passes the confidence gate (would be logged after YOLO_MIN_FRAMES).
Orange box = detected but below the gate (tune PROCTOR_YOLO_PHONE_CONFIDENCE).
Press  q  to quit.
"""
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proctor  # noqa: E402  (loads the real models on import)


def main():
    sess = proctor._load_yolo()
    if not proctor.YOLO_AVAILABLE or sess is None:
        print("[test] YOLO failed to load — check weights/yolo26n.onnx")
        return
    print(f"[test] model:   {proctor._find_yolo_model()}")
    print(f"[test] classes: {proctor.CHEAT_IDS}")
    print(f"[test] gates:   phone>={proctor.YOLO_PHONE_CONFIDENCE}  "
          f"other>={proctor.YOLO_CONFIDENCE}")

    cam = int(os.getenv("CAM", "0"))
    cap = cv2.VideoCapture(cam)
    if not cap.isOpened():
        print(f"[test] cannot open camera {cam} (try CAM=1)")
        return
    print("[test] running — hold up a phone / earbuds / a watch. Press q to quit.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        for cls_id, conf, x1, y1, x2, y2 in proctor._yolo_infer(sess, frame):
            kept = proctor._cheat_detection_kept(cls_id, conf)
            label = proctor.CHEAT_IDS.get(cls_id, f"cls{cls_id}")
            color = (0, 200, 0) if kept else (0, 165, 255)  # green / orange (BGR)
            tag = f"{label} {conf:.2f}" + ("" if kept else " (below gate)")
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
            cv2.putText(frame, tag, (int(x1), max(14, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cv2.imshow("Procta detector test - q to quit", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

"""Accuracy validation for the two models where INT8 quantization showed a
real speedup (SCRFD detector, YOLO26n) — checks the speedup didn't come at
the cost of correctness, on the same real calibration images."""
import os
import sys
import glob

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import proctor
import insightface as _insightface_pkg

WEIGHTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'weights')
_bundled_images_dir = os.path.join(os.path.dirname(_insightface_pkg.__file__), 'data', 'images')
image_paths = sorted(glob.glob(os.path.join(_bundled_images_dir, '*')))


def iou(box_a, box_b):
    xa1, ya1, xa2, ya2 = box_a[:4]
    xb1, yb1, xb2, yb2 = box_b[:4]
    ix1, iy1 = max(xa1, xb1), max(ya1, yb1)
    ix2, iy2 = min(xa2, xb2), min(ya2, yb2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(0, xa2 - xa1) * max(0, ya2 - ya1)
    area_b = max(0, xb2 - xb1) * max(0, yb2 - yb1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


print("===== SCRFD: FP32 vs INT8 detection agreement =====")
scrfd_fp32 = ort.InferenceSession(os.path.join(WEIGHTS_DIR, 'det_500m.onnx'), providers=['CPUExecutionProvider'])
scrfd_int8 = ort.InferenceSession(os.path.join(WEIGHTS_DIR, 'det_500m.int8.onnx'), providers=['CPUExecutionProvider'])

from insightface.model_zoo import get_model
det_fp32 = get_model(os.path.join(WEIGHTS_DIR, 'det_500m.onnx'), providers=['CPUExecutionProvider'])
det_fp32.prepare(ctx_id=-1, input_size=(320, 320), det_thresh=0.5)
det_int8 = get_model(os.path.join(WEIGHTS_DIR, 'det_500m.int8.onnx'), providers=['CPUExecutionProvider'])
det_int8.prepare(ctx_id=-1, input_size=(320, 320), det_thresh=0.5)

scrfd_ious = []
scrfd_count_mismatches = 0
for path in image_paths:
    frame = cv2.imread(path)
    if frame is None:
        continue
    bboxes_fp32, _ = det_fp32.detect(frame, max_num=0)
    bboxes_int8, _ = det_int8.detect(frame, max_num=0)
    print(f"{os.path.basename(path):25s} fp32_faces={len(bboxes_fp32)} int8_faces={len(bboxes_int8)}")
    if len(bboxes_fp32) != len(bboxes_int8):
        scrfd_count_mismatches += 1
        continue
    # match by nearest bbox (images are simple enough that order is usually stable)
    for b32 in bboxes_fp32:
        best_iou = max((iou(b32, b8) for b8 in bboxes_int8), default=0.0)
        scrfd_ious.append(best_iou)

print(f"\nFace count mismatches: {scrfd_count_mismatches}/{len(image_paths)} images")
if scrfd_ious:
    print(f"Bbox IoU (fp32 vs int8, matched faces): mean={np.mean(scrfd_ious):.3f} min={np.min(scrfd_ious):.3f}")


print("\n===== YOLO26n: FP32 vs INT8 detection agreement =====")
yolo_fp32 = ort.InferenceSession(os.path.join(WEIGHTS_DIR, 'yolo26n.onnx'), providers=['CPUExecutionProvider'])
yolo_int8 = ort.InferenceSession(os.path.join(WEIGHTS_DIR, 'yolo26n.int8.onnx'), providers=['CPUExecutionProvider'])

for path in image_paths:
    frame = cv2.imread(path)
    if frame is None:
        continue
    dets_fp32 = proctor._yolo_infer(yolo_fp32, frame)
    dets_int8 = proctor._yolo_infer(yolo_int8, frame)
    print(f"{os.path.basename(path):25s} fp32_dets={len(dets_fp32)} int8_dets={len(dets_int8)}  "
          f"fp32_classes={sorted(set(d[0] for d in dets_fp32))}  "
          f"int8_classes={sorted(set(d[0] for d in dets_int8))}")

print("\n(Note: bundled calibration images are people's faces, not "
      "earphone/phone/watch/etc — so 0 detections on both is EXPECTED and "
      "not informative about the cheat-object classes that actually matter. "
      "This only checks the quantized model doesn't produce spurious/garbage "
      "detections on ordinary photos, not real accuracy on the target classes.)")

"""INT8 static-quantization experiment across all 5 CPU models in the vision
pipeline (SCRFD detector, 2d106det landmarks, gaze ResNet18, YOLO26n,
InsightFace recognition). For each model: quantize with real calibration
images, benchmark FP32 vs INT8 latency, and validate accuracy didn't
meaningfully regress. NOT wired into production — this produces .int8.onnx
siblings + a report so a human can decide which (if any) to adopt.

Honest caveat: calibration data is only the handful of real photos bundled
with the insightface package (6 images, ~8 distinct faces after cropping) —
genuinely small for calibration (typical guidance is 100-1000+ samples).
Treat the accuracy-preservation numbers as a first-pass signal, not a
guarantee — a production adoption would want a larger, more diverse
calibration set before shipping.
"""
import os
import sys
import time
import glob
import statistics

import cv2
import numpy as np
import onnxruntime as ort
from onnxruntime.quantization import quantize_static, CalibrationDataReader, QuantType, CalibrationMethod, QuantFormat

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import proctor
from insightface.app.common import Face
from insightface.data import get_image as insight_get_image

WEIGHTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'weights')

# ─── Step 1: collect real calibration face crops from the bundled images ────
import insightface as _insightface_pkg
_bundled_images_dir = os.path.join(os.path.dirname(_insightface_pkg.__file__), 'data', 'images')
_bundled_image_paths = sorted(glob.glob(os.path.join(_bundled_images_dir, '*')))
print(f"Bundled calibration images: {[os.path.basename(p) for p in _bundled_image_paths]}")

calib_frames = []
calib_faces = []  # (frame, bbox[4], kps[5,2])
for path in _bundled_image_paths:
    frame = cv2.imread(path)
    if frame is None:
        continue
    calib_frames.append(frame)
    faces = proctor.detect_faces(frame)
    for bbox, lm_2d in faces:
        calib_faces.append((frame, bbox, lm_2d))

print(f"Calibration set: {len(calib_frames)} images, {len(calib_faces)} face crops")
assert len(calib_faces) >= 5, "need at least a handful of real faces to calibrate against"


def _make_face_crop(frame, bbox):
    x1, y1, x2, y2 = [max(0, int(v)) for v in bbox[:4]]
    x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
    return frame[y1:y2, x1:x2]


# ─── Calibration data readers, one per model, matching its EXACT preprocessing ──

class SCRFDCalibReader(CalibrationDataReader):
    def __init__(self, frames, input_name, size=320):
        self.input_name = input_name
        self.size = size
        self._data = iter(frames)

    def get_next(self):
        frame = next(self._data, None)
        if frame is None:
            return None
        resized = cv2.resize(frame, (self.size, self.size))
        blob = cv2.dnn.blobFromImage(resized, 1.0 / 128, (self.size, self.size),
                                      (127.5, 127.5, 127.5), swapRB=True)
        return {self.input_name: blob.astype(np.float32)}


class Landmark106CalibReader(CalibrationDataReader):
    def __init__(self, faces, input_name, size=192):
        self.input_name = input_name
        self.size = size
        self._data = iter(faces)

    def get_next(self):
        item = next(self._data, None)
        if item is None:
            return None
        frame, bbox, _ = item
        crop = _make_face_crop(frame, bbox)
        if crop.size == 0:
            return self.get_next()
        resized = cv2.resize(crop, (self.size, self.size))
        blob = cv2.dnn.blobFromImage(resized, 1.0, (self.size, self.size),
                                      (0, 0, 0), swapRB=True).astype(np.float32)
        return {self.input_name: blob}


class GazeCalibReader(CalibrationDataReader):
    def __init__(self, faces, gaze_engine):
        self.engine = gaze_engine
        self._data = iter(faces)

    def get_next(self):
        item = next(self._data, None)
        if item is None:
            return None
        frame, bbox, _ = item
        crop = _make_face_crop(frame, bbox)
        if crop.size == 0:
            return self.get_next()
        return {self.engine.input_name: self.engine._preprocess(crop)}


class YOLOCalibReader(CalibrationDataReader):
    def __init__(self, frames, input_name, size=640):
        self.input_name = input_name
        self.size = size
        self._data = iter(frames)

    def get_next(self):
        frame = next(self._data, None)
        if frame is None:
            return None
        h0, w0 = frame.shape[:2]
        scale = min(self.size / w0, self.size / h0)
        new_w, new_h = int(round(w0 * scale)), int(round(h0 * scale))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (self.size - new_w) // 2, (self.size - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.expand_dims(np.transpose(blob, (2, 0, 1)), 0)
        return {self.input_name: blob}


class RecognitionCalibReader(CalibrationDataReader):
    def __init__(self, faces, input_name, size=112):
        self.input_name = input_name
        self.size = size
        self._data = iter(faces)

    def get_next(self):
        item = next(self._data, None)
        if item is None:
            return None
        frame, bbox, lm_2d = item
        from insightface.utils import face_align
        aligned = face_align.norm_crop(frame, landmark=lm_2d, image_size=self.size)
        blob = cv2.dnn.blobFromImage(aligned, 1.0 / 127.5, (self.size, self.size),
                                      (127.5, 127.5, 127.5), swapRB=True).astype(np.float32)
        return {self.input_name: blob}


def get_input_name(model_path):
    sess = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
    return sess.get_inputs()[0].name


def benchmark(model_path, input_name, sample_input, n=50):
    sess = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
    for _ in range(3):  # warmup
        sess.run(None, {input_name: sample_input})
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        sess.run(None, {input_name: sample_input})
        times.append(time.perf_counter() - t0)
    return statistics.mean(times) * 1000, sorted(times)[int(n * 0.95)] * 1000


def quantize_one(name, fp32_path, reader, sample_input, input_name):
    int8_path = fp32_path.replace('.onnx', '.int8.onnx')
    print(f"\n--- {name} ---")
    print(f"FP32: {fp32_path} ({os.path.getsize(fp32_path)/1e6:.1f}MB)")
    try:
        quantize_static(
            model_input=fp32_path,
            model_output=int8_path,
            calibration_data_reader=reader,
            quant_format=QuantFormat.QDQ,
            calibrate_method=CalibrationMethod.MinMax,
            weight_type=QuantType.QInt8,
            activation_type=QuantType.QUInt8,
        )
    except Exception as e:
        print(f"QUANTIZATION FAILED: {e}")
        return None
    int8_size = os.path.getsize(int8_path) / 1e6
    print(f"INT8: {int8_path} ({int8_size:.1f}MB)")

    fp32_mean, fp32_p95 = benchmark(fp32_path, input_name, sample_input)
    int8_mean, int8_p95 = benchmark(int8_path, input_name, sample_input)
    speedup = fp32_mean / int8_mean if int8_mean > 0 else 0
    print(f"FP32: {fp32_mean:.2f}ms mean, {fp32_p95:.2f}ms p95")
    print(f"INT8: {int8_mean:.2f}ms mean, {int8_p95:.2f}ms p95")
    print(f"Speedup: {speedup:.2f}x")
    return {"name": name, "fp32_mean": fp32_mean, "int8_mean": int8_mean,
            "speedup": speedup, "fp32_size_mb": os.path.getsize(fp32_path)/1e6,
            "int8_size_mb": int8_size, "int8_path": int8_path}


results = []

# 1. SCRFD detector
scrfd_path = proctor._find_scrfd_model()
scrfd_input = get_input_name(scrfd_path)
r = quantize_one("SCRFD detector", scrfd_path,
                  SCRFDCalibReader(calib_frames, scrfd_input),
                  cv2.dnn.blobFromImage(cv2.resize(calib_frames[0], (320, 320)), 1.0/128, (320, 320), (127.5,)*3, swapRB=True).astype(np.float32),
                  scrfd_input)
if r:
    results.append(r)

# 2. 2d106det landmark model
lmk_path = proctor._find_landmark106_model()
lmk_input = get_input_name(lmk_path)
sample_crop = cv2.resize(_make_face_crop(*calib_faces[0][:2]), (192, 192))
sample_blob = cv2.dnn.blobFromImage(sample_crop, 1.0, (192, 192), (0,0,0), swapRB=True).astype(np.float32)
r = quantize_one("2d106det landmarks", lmk_path,
                  Landmark106CalibReader(calib_faces, lmk_input),
                  sample_blob, lmk_input)
if r:
    results.append(r)

# 3. Gaze ResNet18
gaze_path = proctor._find_gaze_model()
gaze_input = get_input_name(gaze_path)
sample_crop = _make_face_crop(*calib_faces[0][:2])
sample_blob = proctor._gaze_engine._preprocess(sample_crop)
r = quantize_one("Gaze ResNet18", gaze_path,
                  GazeCalibReader(calib_faces, proctor._gaze_engine),
                  sample_blob, gaze_input)
if r:
    results.append(r)

# 4. YOLO26n
proctor._load_yolo()
yolo_path = proctor._find_yolo_model()
yolo_input = get_input_name(yolo_path)
h0, w0 = calib_frames[0].shape[:2]
scale = min(640/w0, 640/h0)
sample_resized = cv2.resize(calib_frames[0], (int(w0*scale), int(h0*scale)))
canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
canvas[:sample_resized.shape[0], :sample_resized.shape[1]] = sample_resized
sample_blob = np.expand_dims(np.transpose(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32)/255.0, (2,0,1)), 0)
r = quantize_one("YOLO26n object detector", yolo_path,
                  YOLOCalibReader(calib_frames, yolo_input),
                  sample_blob, yolo_input)
if r:
    results.append(r)

# 5. InsightFace recognition
rec_path = proctor._find_insight_rec_model()
rec_input = get_input_name(rec_path)
from insightface.utils import face_align
sample_aligned = face_align.norm_crop(calib_faces[0][0], landmark=calib_faces[0][2], image_size=112)
sample_blob = cv2.dnn.blobFromImage(sample_aligned, 1.0/127.5, (112,112), (127.5,)*3, swapRB=True).astype(np.float32)
r = quantize_one("InsightFace recognition", rec_path,
                  RecognitionCalibReader(calib_faces, rec_input),
                  sample_blob, rec_input)
if r:
    results.append(r)

print("\n\n===== QUANTIZATION EXPERIMENT SUMMARY =====")
print(f"{'Model':30s} {'FP32 (ms)':>10s} {'INT8 (ms)':>10s} {'Speedup':>8s} {'FP32 MB':>8s} {'INT8 MB':>8s}")
for r in results:
    print(f"{r['name']:30s} {r['fp32_mean']:10.2f} {r['int8_mean']:10.2f} "
          f"{r['speedup']:7.2f}x {r['fp32_size_mb']:8.1f} {r['int8_size_mb']:8.1f}")

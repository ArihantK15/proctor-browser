# Vision Pipeline Consolidation (SCRFD + 2d106det) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `proctor.py`'s RetinaFace (`uniface`) detector and Haar-cascade eyes-closed detector with InsightFace's own SCRFD-weighted detector (`det_500m.onnx`) and 106-point landmark model (`2d106det.onnx`), fixing the fragility behind the historical v2.3.50 head-pose incident and giving eyes-closed detection a real, geometry-based signal instead of a decades-old cascade classifier.

**Architecture:** `detect_faces(frame)` keeps its exact external contract — `list[(bbox_int, lm_arr)]` — but is now backed by `insightface.model_zoo`'s SCRFD-weighted detector instead of `uniface`. Head-pose keeps using the detector's own 5-point kps (unchanged `solvePnP` code, zero landmark-remapping risk). A new 106-point landmark pass runs on the existing `_head_n` cadence and feeds *only* eye-openness geometry (empirically verified index ranges, not guessed). The Haar cascade and `uniface` both retire to dormant fallback code, not deletion.

**Tech Stack:** Python, `insightface` (already a dependency), `onnxruntime` CPU execution provider, `opencv-python`, existing `proctor.py` test harness (`node --test` is for the Electron side; Python tests run via `pytest`).

## Global Constraints

- Full existing test suite (`pytest tests/test_proctor_e2e.py tests/test_proctor_features.py tests/test_proctor_calibration.py tests/test_proctor_onnx_detection.py`, 73 tests as of this session) must stay green after every task.
- External telemetry field names in the Python selftest's `models: {...}` report (`proctor.py:3750-3756`: `retina`, `eyes`) must NOT change, even though their internals do.
- `GazeEstimator` is explicitly out of scope — do not touch it.
- Do not delete `uniface` RetinaFace code or the Haar cascade — keep both as dormant fallback, matching the existing `EarClassifier`-retirement precedent (`proctor.py:3608-3612` comment).
- Do not name the new eye-openness code/constants `EAR` — that abbreviation is already taken by `EAR_EVERY_N` (EarClassifier cadence). Use `EYE_OPEN_RATIO` / `_eye_openness`.
- Verified model files already present locally at:
  - `/Users/arihantkaul/.insightface/models/buffalo_sc/det_500m.onnx` (SCRFD detector, 2.5MB, loads via `insightface.model_zoo.get_model()` as an internal `RetinaFace`-class wrapper — same class name as the old library, different/better weights)
  - `/Users/arihantkaul/.insightface/models/antelopev2/antelopev2/2d106det.onnx` (106-point landmark model, 5.0MB, class `Landmark`, taskname `landmark_2d_106`)
  - `/Users/arihantkaul/.insightface/models/buffalo_sc/w600k_mbf.onnx` (recognition model, already wired in this session's earlier InsightFace fix)
- The shared data object for all `insightface.model_zoo` model calls must be `insightface.app.common.Face(...)`, NOT `types.SimpleNamespace` — `Landmark.get()` does dict-style `face[taskname] = pred` assignment which `SimpleNamespace` doesn't support (verified: raises `TypeError: 'types.SimpleNamespace' object does not support item assignment`). `Face` supports both dict-style and attribute-style access, verified working for both `ArcFaceONNX.get()` (attribute assignment) and `Landmark.get()` (dict assignment) in the same object.
- Empirically verified (via InsightFace's own bundled `t1` test image, `insightface.data.get_image('t1')` — a public demo asset shipped with the library, not any real user's photo) 106-point eye-region indices:
  - Right eye ring: `{33, 35, 36, 37, 38, 39, 40, 41, 42, 46}`
  - Left eye ring: `{81, 87, 89, 90, 91, 93, 94, 95, 96, 98}`
  - Eyebrows are visually confirmed distinct/higher (44, 45, 49, 51 and 99, 100, 102-105) — no contamination risk.

---

### Task 1: Bundle SCRFD + 2d106det model files, add path-resolution helpers

**Files:**
- Create: `weights/det_500m.onnx` (copy from `~/.insightface/models/buffalo_sc/det_500m.onnx`)
- Create: `weights/2d106det.onnx` (copy from `~/.insightface/models/antelopev2/antelopev2/2d106det.onnx`)
- Create: `weights/w600k_mbf.onnx` (copy from `~/.insightface/models/buffalo_sc/w600k_mbf.onnx` — this was NOT bundled in this session's earlier InsightFace fix, it relied on a network-download fallback; bundling it now makes the whole InsightFace stack fully offline-first, consistent with the rest of `weights/`)
- Modify: `proctor.py` (add path-resolution helpers near `_find_yolo_model`/`_find_ear_model`, ~line 216-231)

**Interfaces:**
- Produces: `_find_scrfd_model() -> Optional[str]`, `_find_landmark106_model() -> Optional[str]`, `_find_insight_rec_model() -> Optional[str]` — each mirrors `_find_yolo_model()`'s exact resolution pattern (checks `os.environ.get("PROCTOR_..._MODEL")` override, then `weights/` next to `__file__`, then `ELECTRON_RESOURCES_PATH`).

- [ ] **Step 1: Copy the three model files into `weights/`**

```bash
cd /Users/arihantkaul/proctored-browser
cp /Users/arihantkaul/.insightface/models/buffalo_sc/det_500m.onnx weights/det_500m.onnx
cp /Users/arihantkaul/.insightface/models/antelopev2/antelopev2/2d106det.onnx weights/2d106det.onnx
cp /Users/arihantkaul/.insightface/models/buffalo_sc/w600k_mbf.onnx weights/w600k_mbf.onnx
ls -la weights/det_500m.onnx weights/2d106det.onnx weights/w600k_mbf.onnx
```
Expected: three files listed, sizes ~2.5MB / ~5.0MB / ~13.6MB respectively.

- [ ] **Step 2: Add the three path-resolution helper functions**

In `proctor.py`, immediately after `_find_yolo_model()` (the function ending just before the `def _yolo_providers():` line, currently around line 231-246), add:

```python
def _find_scrfd_model() -> Optional[str]:
    """Resolve the bundled SCRFD detector weights (det_500m.onnx). Same
    override/search pattern as _find_yolo_model."""
    candidates = [
        os.environ.get("PROCTOR_SCRFD_MODEL", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "det_500m.onnx"),
        os.path.join(os.environ.get("ELECTRON_RESOURCES_PATH", ""), "weights", "det_500m.onnx"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

def _find_landmark106_model() -> Optional[str]:
    """Resolve the bundled 106-point landmark model (2d106det.onnx)."""
    candidates = [
        os.environ.get("PROCTOR_LANDMARK106_MODEL", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "2d106det.onnx"),
        os.path.join(os.environ.get("ELECTRON_RESOURCES_PATH", ""), "weights", "2d106det.onnx"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

def _find_insight_rec_model() -> Optional[str]:
    """Resolve the bundled InsightFace recognition model (w600k_mbf.onnx)."""
    candidates = [
        os.environ.get("PROCTOR_INSIGHT_REC_MODEL", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "w600k_mbf.onnx"),
        os.path.join(os.environ.get("ELECTRON_RESOURCES_PATH", ""), "weights", "w600k_mbf.onnx"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None
```

- [ ] **Step 3: Verify syntax and existing tests still pass**

```bash
python3 -m py_compile proctor.py
python3 -m pytest tests/test_proctor_e2e.py tests/test_proctor_features.py tests/test_proctor_calibration.py tests/test_proctor_onnx_detection.py -q
```
Expected: `py_compile` silent (success), `73 passed`.

- [ ] **Step 4: Commit**

```bash
git add weights/det_500m.onnx weights/2d106det.onnx weights/w600k_mbf.onnx proctor.py
git commit -m "$(cat <<'EOF'
Bundle SCRFD + 2d106det + InsightFace recognition model weights

Offline-first, matching the existing weights/ pattern (retinaface,
yolo26n, resnet18_gaze). Path-resolution helpers only in this commit —
not yet wired into any loading code.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Replace RetinaFace with SCRFD-weighted detector in `detect_faces()`

**Files:**
- Modify: `proctor.py:171-183` (RetinaFace init block)
- Modify: `proctor.py:2516-2572` (`detect_faces()`)
- Test: `tests/test_proctor_onnx_detection.py`

**Interfaces:**
- Consumes: `_find_scrfd_model()` from Task 1.
- Produces: `detect_faces(frame: np.ndarray) -> list[tuple[list[int], np.ndarray]]` — UNCHANGED external contract (`bbox_int` list of 4 ints, `lm_arr` shape `(5,2)` float64 ndarray). Every existing caller (`InsightFace` recognition, enrollment, main loop) needs zero changes.
- `RETINA_AVAILABLE` (bool) — same name, same telemetry meaning, now reflects SCRFD load success instead of `uniface` RetinaFace.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_proctor_onnx_detection.py`:

```python
def test_detect_faces_uses_scrfd_not_uniface():
    """detect_faces() must not import uniface any more — it should be
    backed by insightface.model_zoo's SCRFD-weighted detector."""
    import proctor
    assert 'uniface' not in sys.modules or not hasattr(proctor, '_retina') or proctor._retina is None, (
        "proctor._retina should no longer be a live uniface RetinaFace instance")
    assert hasattr(proctor, '_scrfd_detector'), "expected a _scrfd_detector module attribute"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_proctor_onnx_detection.py::test_detect_faces_uses_scrfd_not_uniface -v
```
Expected: FAIL with `AttributeError` (no `_scrfd_detector` attribute yet).

- [ ] **Step 3: Replace the RetinaFace init block**

In `proctor.py`, replace the block at (originally) lines 171-183:

```python
# uniface: face detection + 5 landmarks (ONNX RetinaFace under the hood)
try:
    _seed_retina_model()
    from uniface import RetinaFace
    _retina = RetinaFace()
    RETINA_AVAILABLE = True
    print("[Retina] ✅ Ready")
except Exception as _re:
    print(f"[Retina] ❌ Not available: {_re} — face detection disabled")
    RETINA_AVAILABLE = False
    _retina = None
    _MODEL_ERRORS["retina"] = type(_re).__name__
```

with:

```python
# Face detection: insightface's own SCRFD-weighted detector (det_500m.onnx),
# loaded directly via model_zoo — NOT uniface's RetinaFace any more. SCRFD is
# InsightFace's current-generation detector: published to beat a comparably
# sized RetinaFace variant by ~21% hard-AP while using LESS compute. Loading
# it this way (not through FaceAnalysis) keeps us in full control of exactly
# which weights load, matching the pattern already used for recognition.
# uniface/RetinaFace code below is kept as dormant fallback, not deleted —
# same "retire, don't remove" treatment as EarClassifier.
_retina = None  # dormant — uniface RetinaFace is no longer the active path
try:
    from insightface.model_zoo import get_model as _insight_get_model_face
    _scrfd_model_path = _find_scrfd_model()
    if not _scrfd_model_path:
        raise FileNotFoundError("weights/det_500m.onnx not found")
    _scrfd_detector = _insight_get_model_face(_scrfd_model_path, providers=['CPUExecutionProvider'])
    _scrfd_detector.prepare(ctx_id=-1, input_size=(320, 320), det_thresh=0.5)
    RETINA_AVAILABLE = True
    print("[Detector] ✅ SCRFD ready (det_500m.onnx)")
except Exception as _re:
    print(f"[Detector] ❌ Not available: {_re} — face detection disabled")
    RETINA_AVAILABLE = False
    _scrfd_detector = None
    _MODEL_ERRORS["retina"] = type(_re).__name__
```

- [ ] **Step 4: Rewrite `detect_faces()` internals**

Replace the whole function body (originally lines 2516-2572) with:

```python
def detect_faces(frame: np.ndarray):
    """Return list of (bbox, landmarks_2d) tuples — empty list if no faces.

    Backed by insightface's SCRFD-weighted detector (det_500m.onnx). Its
    .detect() returns (bboxes[N,5], kpss[N,5,2]) — bbox is [x1,y1,x2,y2,score],
    kps is the same 5-point convention (left_eye, right_eye, nose, left_mouth,
    right_mouth) uniface's RetinaFace used, so every downstream consumer
    (InsightFace recognition, head-pose solvePnP) needs zero changes.
    """
    if not RETINA_AVAILABLE:
        return []
    try:
        bboxes, kpss = _scrfd_detector.detect(frame, max_num=0, metric='default')
        if bboxes is None or len(bboxes) == 0:
            return []
        out = []
        for i, box in enumerate(bboxes):
            bbox_int = [int(round(c)) for c in box[:4]]
            lm_arr = np.asarray(kpss[i], dtype=np.float64).reshape(-1, 2)[:5]
            if lm_arr.shape != (5, 2):
                continue
            out.append((bbox_int, lm_arr))
        return out
    except Exception as e:
        print(f"[Detector Error] {e}")
        return []
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python3 -m pytest tests/test_proctor_onnx_detection.py::test_detect_faces_uses_scrfd_not_uniface -v
```
Expected: PASS.

- [ ] **Step 6: Run the full regression suite**

```bash
python3 -m pytest tests/test_proctor_e2e.py tests/test_proctor_features.py tests/test_proctor_calibration.py tests/test_proctor_onnx_detection.py -q
```
Expected: all 73+ tests pass (mocked `insightface`/`uniface` — this task doesn't change the mocking contract, `detect_faces` still returns the same shape).

- [ ] **Step 7: Real (non-mocked) smoke test against the actual installed library**

```bash
python3 -c "
import proctor
import numpy as np
frame = (np.random.rand(480, 640, 3) * 255).astype('uint8')
faces = proctor.detect_faces(frame)
print('RETINA_AVAILABLE:', proctor.RETINA_AVAILABLE)
print('faces on random noise (expect 0):', len(faces))
from insightface.data import get_image
real_faces = proctor.detect_faces(get_image('t1'))
print('faces on real test image (expect >=1):', len(real_faces))
if real_faces:
    bbox, lm = real_faces[0]
    print('bbox:', bbox, 'landmarks shape:', lm.shape)
"
```
Expected: `RETINA_AVAILABLE: True`, `0` faces on random noise, `>=1` face on the real InsightFace test image, landmarks shape `(5, 2)`.

- [ ] **Step 8: Commit**

```bash
git add proctor.py tests/test_proctor_onnx_detection.py
git commit -m "$(cat <<'EOF'
Replace RetinaFace (uniface) with SCRFD-weighted detector

detect_faces() keeps its exact external contract — every downstream
consumer (InsightFace recognition, head-pose solvePnP, enrollment)
needs zero changes. uniface/RetinaFace code kept as dormant fallback,
not deleted.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add eye-openness geometry from 106-point landmarks, replacing the Haar cascade

**Files:**
- Modify: `proctor.py:1968-1988` (Haar cascade `eyes_detected()` block)
- Modify: `proctor.py` (add landmark106 loading near the SCRFD init block from Task 2)
- Modify: `proctor.py:~3608` (call site — `eyes_open = eyes_detected(face_crop)`)
- Test: `tests/test_proctor_features.py`

**Interfaces:**
- Consumes: `insightface.app.common.Face`, `_find_landmark106_model()` from Task 1, `bbox`/`lm_2d`/`frame` already in scope at the call site (same variables Task's earlier InsightFace fix already threads through).
- Produces: `eyes_open_from_landmarks(frame, bbox) -> Optional[bool]` (returns `None` on failure — fail-open at the call site, matching existing `eyes_detected()` semantics), `EYE_OPEN_RATIO_THRESHOLD` (float constant).
- `EYES_AVAILABLE` (bool) — same name, same telemetry meaning, now reflects landmark106 load success (falls back to Haar cascade's own `EYES_AVAILABLE` value if landmark106 fails to load).

- [ ] **Step 1: Write the failing unit test**

Add to `tests/test_proctor_features.py`:

```python
def test_eye_openness_ratio_open_vs_closed():
    """Verified index sets from InsightFace's own t1 test image (see plan
    doc for how these were empirically confirmed — not guessed)."""
    import proctor
    import numpy as np

    # Open eye: points spread in both x and y -> roughly circular/almond ring.
    open_pts = np.array([
        [10, 5], [8, 3], [12, 3], [15, 4], [18, 5],
        [10, 9], [8, 8], [12, 8], [15, 9], [18, 8],
    ], dtype=np.float64)
    ratio_open = proctor._eye_openness_ratio(open_pts)

    # Closed eye: same horizontal spread, near-zero vertical spread (a line).
    closed_pts = np.array([
        [10, 6], [8, 6.1], [12, 6], [15, 6.1], [18, 6],
        [10, 6.2], [8, 6.1], [12, 6.2], [15, 6.1], [18, 6.2],
    ], dtype=np.float64)
    ratio_closed = proctor._eye_openness_ratio(closed_pts)

    assert ratio_open > ratio_closed
    assert ratio_closed < proctor.EYE_OPEN_RATIO_THRESHOLD
    assert ratio_open > proctor.EYE_OPEN_RATIO_THRESHOLD
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_proctor_features.py::test_eye_openness_ratio_open_vs_closed -v
```
Expected: FAIL with `AttributeError: module 'proctor' has no attribute '_eye_openness_ratio'`.

- [ ] **Step 3: Add the landmark106 model loading block**

In `proctor.py`, immediately after the SCRFD detector init block from Task 2, add:

```python
# 106-point landmark model (2d106det.onnx) — used ONLY for eye-openness
# geometry below. Head-pose deliberately keeps using the detector's own
# 5-point kps (unchanged solvePnP code) — no landmark-index remapping risk
# for that calculation. Index ranges below were empirically verified against
# InsightFace's own bundled t1 test image (a public demo asset shipped with
# the library, not any real user's photo) — NOT taken from documentation,
# which does not reliably publish exact index-to-region mappings.
_landmark106 = None
LANDMARK106_AVAILABLE = False
try:
    from insightface.model_zoo import get_model as _insight_get_model_lmk
    _lmk_model_path = _find_landmark106_model()
    if not _lmk_model_path:
        raise FileNotFoundError("weights/2d106det.onnx not found")
    _landmark106 = _insight_get_model_lmk(_lmk_model_path, providers=['CPUExecutionProvider'])
    _landmark106.prepare(ctx_id=-1)
    LANDMARK106_AVAILABLE = True
    print("[Landmark106] ✅ Ready (2d106det.onnx)")
except Exception as _le:
    print(f"[Landmark106] ❌ Not available: {_le} — eye-openness geometry disabled, falling back to Haar cascade")
    LANDMARK106_AVAILABLE = False
    _landmark106 = None

# Empirically verified index sets — see Global Constraints in the plan doc.
_RIGHT_EYE_IDX = [33, 35, 36, 37, 38, 39, 40, 41, 42, 46]
_LEFT_EYE_IDX  = [81, 87, 89, 90, 91, 93, 94, 95, 96, 98]
EYE_OPEN_RATIO_THRESHOLD = 0.25  # height/width below this = closed

def _eye_openness_ratio(pts: np.ndarray) -> float:
    """height/width of a landmark point cluster's bounding box. Works on
    ANY subset of points forming a ring around one eye — robust to not
    knowing exactly which index is 'upper-mid' vs 'lower-mid', since we
    just take min/max over the whole known ring."""
    xs, ys = pts[:, 0], pts[:, 1]
    width = float(xs.max() - xs.min())
    height = float(ys.max() - ys.min())
    if width <= 0:
        return 0.0
    return height / width

def eyes_open_from_landmarks(frame: np.ndarray, bbox) -> Optional[bool]:
    """Return True (open) / False (closed) / None (unavailable/error).
    Fail-open at the call site mirrors eyes_detected()'s existing contract."""
    if not LANDMARK106_AVAILABLE:
        return None
    try:
        from insightface.app.common import Face
        face = Face(bbox=np.asarray(bbox[:4], dtype=np.float32))
        _landmark106.get(frame, face)
        pts = face.landmark_2d_106
        r_ratio = _eye_openness_ratio(pts[_RIGHT_EYE_IDX])
        l_ratio = _eye_openness_ratio(pts[_LEFT_EYE_IDX])
        avg_ratio = (r_ratio + l_ratio) / 2.0
        return avg_ratio >= EYE_OPEN_RATIO_THRESHOLD
    except Exception:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_proctor_features.py::test_eye_openness_ratio_open_vs_closed -v
```
Expected: PASS.

- [ ] **Step 5: Wire the call site to prefer landmarks, fall back to Haar cascade**

Find the existing call site (originally around line 3608):
```python
                eyes_open = eyes_detected(face_crop)
```
Replace with:
```python
                _eyes_landmark_result = eyes_open_from_landmarks(frame, (x1, y1, x2, y2))
                eyes_open = (_eyes_landmark_result
                             if _eyes_landmark_result is not None
                             else eyes_detected(face_crop))
```
(`x1, y1, x2, y2` are already in scope at this call site from the existing `bbox, lm_2d = faces[0]` unpacking a few lines above — same variables Task 2's InsightFace-fix call sites already use.)

- [ ] **Step 6: Run the full regression suite + real smoke test**

```bash
python3 -m pytest tests/test_proctor_e2e.py tests/test_proctor_features.py tests/test_proctor_calibration.py tests/test_proctor_onnx_detection.py -q
```
Expected: all tests pass.

```bash
python3 -c "
import proctor
from insightface.data import get_image
img = get_image('t1')
faces = proctor.detect_faces(img)
bbox, lm = faces[0]
result = proctor.eyes_open_from_landmarks(img, bbox)
print('LANDMARK106_AVAILABLE:', proctor.LANDMARK106_AVAILABLE)
print('eyes_open_from_landmarks on real open-eyed test photo:', result)
assert result is True, 'expected True (open) on a normal open-eyed photo'
print('OK')
"
```
Expected: `LANDMARK106_AVAILABLE: True`, result `True`, `OK` printed.

- [ ] **Step 7: Commit**

```bash
git add proctor.py tests/test_proctor_features.py
git commit -m "$(cat <<'EOF'
Add eye-openness geometry from 2d106det landmarks

Replaces the Haar-cascade eyes_detected() as the primary signal,
using empirically-verified eye-region index sets (see plan doc).
Haar cascade kept as automatic fallback if landmark106 is unavailable
— never a hard failure, matching the existing fail-open philosophy.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Docker constrained-hardware test harness

**Files:**
- Create: `scripts/constrained-hardware-test/Dockerfile`
- Create: `scripts/constrained-hardware-test/run.sh`
- Create: `scripts/constrained-hardware-test/smoke_test.py`

**Interfaces:**
- Consumes: `proctor.py` module (imported, not run as the full app — this is a targeted vision-pipeline smoke test, not a full exam simulation).
- Produces: a runnable `./scripts/constrained-hardware-test/run.sh` that builds and runs the container with `--memory=6g --cpus=2`.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# scripts/constrained-hardware-test/Dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-proctor.txt ./
RUN pip install --no-cache-dir -r requirements-proctor.txt

COPY proctor.py .
COPY weights/ weights/
COPY scripts/constrained-hardware-test/smoke_test.py .

ENV PROCTOR_ORT_THREADS=2
ENV OMP_NUM_THREADS=2

CMD ["python3", "smoke_test.py"]
```

- [ ] **Step 2: Write the smoke test script**

```python
# scripts/constrained-hardware-test/smoke_test.py
"""Exercises the real vision pipeline (SCRFD detect, 2d106det landmarks,
eye-openness, InsightFace recognition) under real memory+CPU constraints
(this script is meant to run inside a `docker run --memory=6g --cpus=2`
container — see run.sh). Not a substitute for real-hardware validation,
just a strong pre-check for graceful degradation."""
import time
import resource
import proctor
from insightface.data import get_image

img = get_image('t1')

print(f"RETINA_AVAILABLE={proctor.RETINA_AVAILABLE}")
print(f"LANDMARK106_AVAILABLE={proctor.LANDMARK106_AVAILABLE}")
print(f"INSIGHT_AVAILABLE={proctor.INSIGHT_AVAILABLE}")

N = 30
t0 = time.perf_counter()
for _ in range(N):
    faces = proctor.detect_faces(img)
    assert faces, "expected at least one face on the bundled test image"
    bbox, lm_2d = faces[0]
    eyes_open = proctor.eyes_open_from_landmarks(img, bbox)
    emb = proctor.get_face_embedding_from_crop(img, lm_2d)
elapsed = time.perf_counter() - t0

peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # KB->MB on Linux
print(f"{N} iterations in {elapsed:.2f}s ({elapsed/N*1000:.1f}ms/iter avg)")
print(f"peak RSS: {peak_rss_mb:.1f} MB")
print(f"last eyes_open={eyes_open}, embedding shape={None if emb is None else emb.shape}")
assert eyes_open is True, "expected True (open) on the bundled open-eyed test photo"
assert emb is not None, "expected a valid embedding"
print("SMOKE TEST PASSED")
```

- [ ] **Step 3: Write the run script**

```bash
#!/usr/bin/env bash
# scripts/constrained-hardware-test/run.sh
# Builds and runs the vision pipeline smoke test inside a container capped
# at 6GB RAM and 2 CPUs — simulating a budget laptop's resource envelope.
# NOTE: this Mac may be Apple Silicon (ARM); a real budget Windows laptop
# is almost certainly x86 with different single-core characteristics. This
# is a strong pre-check for graceful degradation, NOT a substitute for
# real-hardware validation before using "runs on a ₹30,000 laptop" as a
# customer-facing claim.
set -euo pipefail
cd "$(dirname "$0")/../.."
docker build -f scripts/constrained-hardware-test/Dockerfile -t proctor-constrained-test .
docker run --rm --memory=6g --cpus=2 proctor-constrained-test
```

```bash
chmod +x scripts/constrained-hardware-test/run.sh
```

- [ ] **Step 4: Run it**

```bash
./scripts/constrained-hardware-test/run.sh
```
Expected: `SMOKE TEST PASSED` with a printed per-iteration latency and peak RSS well under 6GB (proctor.py's own dependencies — opencv, onnxruntime, numpy — are already modest; this is checking for no crash/OOM and reasonable per-frame timing, not asserting a specific latency number since Docker-on-Mac's virtualization overhead isn't representative of real hardware timing).

- [ ] **Step 5: Commit**

```bash
git add scripts/constrained-hardware-test/
git commit -m "$(cat <<'EOF'
Add Docker-based constrained-hardware smoke test harness

Exercises the real vision pipeline (SCRFD, 2d106det, eye-openness,
InsightFace recognition) under a real --memory=6g --cpus=2 cgroup
limit. A strong pre-check for graceful degradation on budget hardware,
not a substitute for real-device validation.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Final full-suite regression pass and self-review

**Files:** none new — verification only.

- [ ] **Step 1: Run the complete existing Python test suite**

```bash
cd /Users/arihantkaul/proctored-browser
python3 -m pytest tests/ -q 2>&1 | tail -30
```
Expected: no new failures versus the pre-migration baseline (73+ passed, same as this session's earlier baseline run).

- [ ] **Step 2: Re-read every modified section of `proctor.py` for the self-review checklist**

Per this project's `feedback_self_review_before_commit.md` house rule: re-read the full diff and check for syntax/runtime/config/cross-reference/auth/failure issues before considering the task done.

```bash
git diff main -- proctor.py | head -300
python3 -m py_compile proctor.py
```

- [ ] **Step 3: Confirm telemetry field names are unchanged**

```bash
grep -n '"retina":\|"eyes":\|"ear":\|"insightface":' proctor.py
```
Expected: same four lines as before this migration (`"retina": RETINA_AVAILABLE`, `"ear": EAR_CLASSIFIER_AVAILABLE`, `"insightface": INSIGHT_AVAILABLE`, `"eyes": EYES_AVAILABLE`).

- [ ] **Step 4: Final commit if any cleanup was needed, otherwise this task is verification-only.**

## Self-review notes (spec coverage check)

- Head-pose robustness (the original motivation): addressed indirectly — SCRFD's improved detection-level 5-point localization feeds the *unchanged* `solvePnP` code, rather than remapping to 106 points (deliberately lower-risk, per Task 2/3 design decision).
- Eyes-closed accuracy: addressed directly in Task 3, with empirically-verified indices, not guessed ones.
- CPU/accuracy tradeoff honesty: no task claims a CPU win — the spec's own honest framing (net cost roughly a wash) stands; Task 4's Docker harness measures actual behavior rather than asserting a number.
- Naming collision avoidance (EAR vs EarClassifier's EAR_EVERY_N): enforced in Task 3 via `EYE_OPEN_RATIO_THRESHOLD` / `_eye_openness_ratio` naming.
- Fallback/fail-open behavior preserved: Task 2 (SCRFD failure → `RETINA_AVAILABLE=False`, same as before), Task 3 (`landmark106` failure → Haar cascade fallback, never a hard crash).

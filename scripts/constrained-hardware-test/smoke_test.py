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
eyes_open = None
emb = None
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

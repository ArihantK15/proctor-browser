#!/usr/bin/env python3
"""Export the YOLO26-nano object detector to ONNX and install it into weights/.

This is the ONE step that actually activates the YOLO26 swap. The proctor code
has preferred weights/yolo26n.onnx since 1af0bbdb (the decode in
proctor.py:_yolo_infer auto-detects the YOLO26 NMS-free head vs the legacy v8
head), but the weights file was never produced — so every session silently
falls back to yolov8n. Run this once; commit the result; ship.

DEV/BUILD TOOL ONLY. It needs `ultralytics` (which pulls torch) to do the
export, so it is deliberately NOT bundled to students (not in package.json
extraResources). Students receive the committed weights/yolo26n.onnx in the
Electron build; they never run this.

What it does, crash-safe + idempotent:
  1. Skip if weights/yolo26n.onnx already exists (unless --force).
  2. `YOLO("yolo26n.pt").export(format=onnx, imgsz=640, opset=…)` — downloads the
     official COCO-pretrained .pt from ultralytics if absent.
  3. Move the export to weights/yolo26n.onnx (atomic replace).
  4. VALIDATE with onnxruntime: input must be 640x640, output must be a head the
     proctor can decode ([1,N,6] YOLO26, or [1,84,8400] v8 fallback). A wrong
     imgsz or a broken export fails LOUDLY here instead of silently mis-detecting
     on a live exam.

Usage:
    python scripts/export_yolo26.py            # export if missing
    python scripts/export_yolo26.py --force    # re-export, overwrite
    python scripts/export_yolo26.py --check     # report what's installed, no export
    python scripts/export_yolo26.py --model yolo26s.pt   # use the small variant

Exit codes:
    0 — weights/yolo26n.onnx present + validated
    1 — export or validation failed
    2 — ultralytics not installed (with the pip hint)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Windows consoles default to cp1252 and crash on the ✓/⚠ glyphs below; force
# UTF-8 (matches scripts/download_audio_models.py).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_DIR = REPO_ROOT / "weights"
TARGET = WEIGHTS_DIR / "yolo26n.onnx"
IMGSZ = 640          # MUST match proctor.py _YOLO_INPUT_SIZE
DEFAULT_OPSET = 12   # onnxruntime>=1.19 supports it; conservative + matches v8 era


def _validate(onnx_path: Path) -> bool:
    """Load the exported model and confirm the proctor can use it: 640x640 input
    and an output head _yolo_infer decodes. Returns True iff usable."""
    try:
        import numpy as np
        import onnxruntime as ort
    except Exception as e:
        print(f"⚠ Cannot validate (onnxruntime/numpy missing in this env): {e}")
        print("  Skipping validation — verify on a machine with the proctor runtime.")
        return True  # don't block export on a missing validation dep

    try:
        sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        # Static exports report [1,3,640,640]; dynamic ones may report strings.
        dims = [d for d in inp.shape if isinstance(d, int)]
        if 640 not in dims and len(dims) >= 2:
            print(f"✗ Unexpected input shape {inp.shape} — expected 640x640. "
                  f"Re-export with imgsz={IMGSZ}.")
            return False
        out = sess.run(None, {inp.name: np.zeros((1, 3, IMGSZ, IMGSZ), np.float32)})[0]
        if out.ndim == 3 and out.shape[2] == 6:
            head = "YOLO26 NMS-free [1,N,6]"
        elif out.ndim == 3 and out.shape[1] in (84, 85):
            head = f"legacy v8 [1,{out.shape[1]},{out.shape[2]}] (still decodable)"
        else:
            print(f"✗ Output shape {out.shape} is neither head proctor._yolo_infer "
                  f"can decode ([1,N,6] or [1,84,8400]). Aborting.")
            return False
        print(f"✓ Validated: input {inp.shape}, output head = {head}")
        return True
    except Exception as e:
        print(f"✗ Validation failed loading {onnx_path.name}: {e}")
        return False


def _export(model_name: str, opset: int) -> Path | None:
    try:
        from ultralytics import YOLO
    except Exception:
        print("✗ ultralytics is not installed (this is a dev/build tool).")
        print("  Install it on a dev box:  pip install ultralytics")
        sys.exit(2)

    with tempfile.TemporaryDirectory(prefix="yolo26_export_") as td:
        cwd = os.getcwd()
        try:
            # Export into the temp dir so we never leave a stray .onnx/.pt in cwd.
            os.chdir(td)
            print(f"↓ Loading/downloading {model_name} (official COCO weights)…")
            model = YOLO(model_name)
            print(f"→ Exporting to ONNX (imgsz={IMGSZ}, opset={opset})…")
            out = model.export(format="onnx", imgsz=IMGSZ, opset=opset)
            # export() returns the path in recent ultralytics; glob as a fallback.
            onnx_path = Path(out) if out and Path(out).exists() else None
            if onnx_path is None:
                hits = list(Path(td).rglob("*.onnx"))
                onnx_path = hits[0] if hits else None
            if onnx_path is None or not onnx_path.exists():
                print("✗ Export produced no .onnx file.")
                return None
            WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
            staged = WEIGHTS_DIR / (TARGET.name + ".tmp")
            shutil.copy2(onnx_path, staged)
            os.replace(staged, TARGET)   # atomic on same filesystem
            return TARGET
        finally:
            os.chdir(cwd)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export YOLO26-n to weights/yolo26n.onnx")
    ap.add_argument("--force", action="store_true", help="re-export even if it exists")
    ap.add_argument("--check", action="store_true", help="report status only, no export")
    ap.add_argument("--model", default="yolo26n.pt", help="ultralytics model (default yolo26n.pt)")
    ap.add_argument("--opset", type=int, default=DEFAULT_OPSET)
    args = ap.parse_args()

    if TARGET.exists():
        size_mb = TARGET.stat().st_size / 1e6
        if args.check or not args.force:
            print(f"✓ weights/yolo26n.onnx present ({size_mb:.1f} MB).")
            ok = _validate(TARGET)
            if ok and not args.force:
                print("  Nothing to do. Use --force to re-export.")
                return 0 if ok else 1
            if args.check:
                return 0 if ok else 1
    elif args.check:
        print("✗ weights/yolo26n.onnx NOT present — proctor is running legacy yolov8n.")
        print("  Run:  python scripts/export_yolo26.py")
        return 1

    target = _export(args.model, args.opset)
    if target is None:
        return 1
    print(f"✓ Wrote {target.relative_to(REPO_ROOT)} ({target.stat().st_size/1e6:.1f} MB)")
    if not _validate(target):
        print("✗ The export is present but did not validate — NOT safe to ship.")
        return 1
    print("\nNext:")
    print("  git add weights/yolo26n.onnx")
    print("  git commit -m 'feat(proctor): bundle yolo26n.onnx — activate YOLO26'")
    print("  (the Electron build ships weights/ automatically)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

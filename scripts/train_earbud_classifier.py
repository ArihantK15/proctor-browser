#!/usr/bin/env python3
"""Train + export the earbud ear-crop classifier -> weights/earbud_classifier.onnx

Produces EXACTLY the contract proctor.py's EarClassifier expects, so the file
auto-loads with zero code change (and ships via electron-builder's `weights`
extraResources):

  * input :  [1, 3, 64, 64]  float32, RGB, /255.0, NCHW   (name "input")
  * output:  [1, 2]          softmax probabilities
             index 1 = P(earbud)  <- EarClassifier.classify reads outputs[0][0][1]

Dataset layout (from harvest_ear_crops.py):
  data/ear/earbud/*.png      -> label 1
  data/ear/no_earbud/*.png   -> label 0

Usage:
  pip install torch torchvision onnx        # dev-time only, NOT bundled
  python scripts/train_earbud_classifier.py --data data/ear --epochs 15

Quality note: a model trained on one person's webcam is a strong upgrade over the
Canny/dark heuristic but will overfit to that face/lighting. For production,
harvest from several people and varied lighting, or augment with public
earbud/headphone imagery cropped through harvest_ear_crops.py --frames.
"""
import argparse
import glob
import os
import sys

import numpy as np

IMG = 64


def load_split(root, val_frac=0.15, seed=0):
    import cv2
    xs, ys = [], []
    for label, cls in ((0, "no_earbud"), (1, "earbud")):
        files = sum([glob.glob(os.path.join(root, cls, e))
                     for e in ("*.png", "*.jpg", "*.jpeg")], [])
        for f in files:
            img = cv2.imread(f)
            if img is None:
                continue
            img = cv2.resize(img, (IMG, IMG))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            xs.append(np.transpose(img, (2, 0, 1)))  # HWC -> CHW
            ys.append(label)
    if not xs:
        print(f"ERROR: no crops found under {root}/(earbud|no_earbud)")
        sys.exit(1)
    X = np.asarray(xs, dtype=np.float32)
    Y = np.asarray(ys, dtype=np.int64)
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(X))
    X, Y = X[idx], Y[idx]
    n_val = max(1, int(len(X) * val_frac))
    print(f"[data] {len(X)} crops  (earbud={int(Y.sum())} no_earbud={int((1-Y).sum())}) "
          f"-> train {len(X)-n_val} / val {n_val}")
    return (X[n_val:], Y[n_val:]), (X[:n_val], Y[:n_val])


def build_model():
    import torch.nn as nn
    return nn.Sequential(
        nn.Conv2d(3, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),   # 32
        nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),  # 16
        nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),  # 8
        nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        nn.Linear(64, 64), nn.ReLU(), nn.Dropout(0.3),
        nn.Linear(64, 2),
        nn.Softmax(dim=1),   # bake softmax in so output is P(earbud) directly
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ear")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--out", default="weights/earbud_classifier.onnx")
    args = ap.parse_args()

    import torch
    from torch.utils.data import DataLoader, TensorDataset

    (Xtr, Ytr), (Xva, Yva) = load_split(args.data)
    tr = DataLoader(TensorDataset(torch.from_numpy(Xtr), torch.from_numpy(Ytr)),
                    batch_size=args.batch, shuffle=True)
    model = build_model()
    # NLLLoss on log(softmax_probs); softmax is already in the model.
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = torch.nn.NLLLoss()

    for ep in range(1, args.epochs + 1):
        model.train()
        tot = 0.0
        for xb, yb in tr:
            opt.zero_grad()
            probs = model(xb).clamp_min(1e-7)
            loss = loss_fn(torch.log(probs), yb)
            loss.backward(); opt.step()
            tot += float(loss) * len(xb)
        model.eval()
        with torch.no_grad():
            vp = model(torch.from_numpy(Xva))
            acc = float((vp.argmax(1).numpy() == Yva).mean())
        print(f"[train] epoch {ep:2d}  loss {tot/len(Xtr):.4f}  val_acc {acc:.3f}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    model.eval()
    dummy = torch.zeros(1, 3, IMG, IMG)
    torch.onnx.export(
        model, dummy, args.out,
        input_names=["input"], output_names=["probs"],
        opset_version=13,
        # static [1,3,64,64] so proctor's model_size = input_shape[2:][::-1] = (64,64)
    )
    print(f"[export] wrote {args.out}")

    # Self-check against the proctor's exact inference path.
    try:
        import onnxruntime as ort
        s = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
        o = s.run(None, {"input": dummy.numpy()})[0]
        assert o.shape == (1, 2), o.shape
        print(f"[verify] OK — output shape {o.shape}, P(earbud)={float(o[0][1]):.3f} on a blank crop")
        print("Drop the file in weights/ — proctor.py picks it up automatically "
              "(replaces the heuristic).")
    except Exception as e:
        print(f"[verify] WARNING: {e}")


if __name__ == "__main__":
    main()

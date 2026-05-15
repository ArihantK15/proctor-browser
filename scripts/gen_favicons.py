#!/usr/bin/env python3
"""
gen_favicons.py — render the Procta favicon SVG to PNGs at every size
browsers + Google + Apple + Windows ask for.

Why we do this instead of relying on `<link rel="icon" type="image/svg+xml">`:
Google's SERP favicon crawler historically prefers a >=48×48 raster (PNG/ICO).
Sites that ship only SVG often get a re-rasterised, smudged thumbnail in
search results. Shipping a clean 192×192 PNG fixes the snippet rendering.

Sizes produced:
  16, 32, 48          standard favicon stack (.ico-equivalents)
  180                 apple-touch-icon (iOS home-screen)
  192, 512            web manifest (Android Chrome PWA install)
  1200×630            og-image (regenerated separately — see og-image.svg)

Run:
  DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib python3 scripts/gen_favicons.py

The DYLD path is needed on Apple Silicon so cairosvg can find libcairo
from Homebrew. Linux users can drop the prefix.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import cairosvg
except ImportError:
    sys.stderr.write(
        "cairosvg not installed. Install with:\n"
        "  brew install cairo pango   # macOS\n"
        "  pip3 install cairosvg\n"
    )
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "website" / "public" / "favicon.svg"
OUT_DIR = ROOT / "website" / "public"

SIZES = {
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-48.png": 48,
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "icon-512.png": 512,
}


def main() -> None:
    if not SRC.exists():
        sys.stderr.write(f"missing {SRC}\n")
        sys.exit(1)

    svg_bytes = SRC.read_bytes()
    print(f"Source: {SRC.relative_to(ROOT)}")
    for filename, size in SIZES.items():
        out = OUT_DIR / filename
        cairosvg.svg2png(
            bytestring=svg_bytes,
            output_width=size,
            output_height=size,
            write_to=str(out),
        )
        kb = out.stat().st_size / 1024
        print(f"  → {filename:<24} {size}×{size}  ({kb:.1f} KB)")

    print("\nDone. Remember to commit the PNGs alongside favicon.svg.")


if __name__ == "__main__":
    main()

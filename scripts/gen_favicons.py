#!/usr/bin/env python3
"""
gen_favicons.py — render the Procta favicon SVG to PNGs at every size
browsers + Google + Apple + Windows ask for, including padded maskable
variants for Android PWAs.

Why we ship PNGs instead of relying on `<link rel="icon" type="image/svg+xml">`:
Google's SERP favicon crawler historically prefers a >=48x48 raster
(PNG/ICO). Sites that ship only SVG often get a re-rasterised, smudged
thumbnail in search results. Shipping a clean 192x192 PNG fixes the
snippet rendering.

Why maskable variants:
Android's PWA install wraps icons in a rounded-square/circle mask and
crops anything outside the inner 80% safe zone. Our default favicon
mark spans ~28-78% of the icon width, which can clip on circle masks.
The maskable variants are the same mark rendered onto a larger canvas
(safe-zone padded), so the mark sits inside Android's guaranteed-visible
inner circle.

Sizes produced:
  16, 32, 48                   standard favicon stack (.ico-equivalents)
  180                          apple-touch-icon (iOS home-screen)
  192, 512                     web manifest (Android Chrome PWA install, "any")
  192 + 512 maskable variants  Android adaptive icons ("maskable")

Run:
  python3 scripts/gen_favicons.py

On macOS the script auto-sets DYLD_FALLBACK_LIBRARY_PATH so cairosvg can
find libcairo from Homebrew. On Linux/Windows the system loader handles
it once libcairo2 is installed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Auto-wire the Homebrew libcairo path on Apple Silicon so a fresh clone
# `just works` — without this, cairosvg's first import errors with
# `OSError: no library called "cairo-2" was found`.
if sys.platform == "darwin":
    os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")

try:
    import cairosvg
except ImportError:
    sys.stderr.write(
        "cairosvg not installed. Install with:\n"
        "  brew install cairo pango     # macOS\n"
        "  apt-get install libcairo2    # Debian/Ubuntu\n"
        "  pip3 install cairosvg\n"
    )
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "website" / "public" / "favicon.svg"
OUT_DIR = ROOT / "website" / "public"

# Standard variants — the mark fills the canvas as-is.
SIZES = {
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-48.png": 48,
    "apple-touch-icon.png": 180,
    "icon-192.png": 192,
    "icon-512.png": 512,
}

# Maskable variants — the mark is shrunk to fit inside Android's 80%
# safe zone. We wrap the original SVG in an outer rect of the same fill
# color and translate/scale the inner contents so the mark occupies the
# central 80% of the canvas.
MASKABLE_SIZES = {
    "icon-192-maskable.png": 192,
    "icon-512-maskable.png": 512,
}

MASKABLE_SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">
  <!-- Full-bleed brand-navy background so Android's mask has color to crop. -->
  <rect width="80" height="80" fill="#0F1629"/>
  <!-- Original favicon (64x64 viewBox) placed inside the 80% safe zone
       (8px margin on all sides = inner 64x64 region of an 80x80 canvas). -->
  <g transform="translate(8, 8)">
    {inner}
  </g>
</svg>
"""


def _extract_inner(svg_bytes: bytes) -> str:
    """Pull the inner content of the source SVG (everything inside <svg>)."""
    s = svg_bytes.decode("utf-8")
    start = s.index(">", s.index("<svg")) + 1
    end = s.rindex("</svg>")
    return s[start:end]


def main() -> None:
    if not SRC.exists():
        sys.stderr.write(f"missing {SRC}\n")
        sys.exit(1)

    svg_bytes = SRC.read_bytes()
    print(f"Source: {SRC.relative_to(ROOT)}")

    # Standard variants
    for filename, size in SIZES.items():
        out = OUT_DIR / filename
        cairosvg.svg2png(
            bytestring=svg_bytes,
            output_width=size,
            output_height=size,
            write_to=str(out),
        )
        kb = out.stat().st_size / 1024
        print(f"  -> {filename:<28} {size}x{size}  ({kb:.1f} KB)")

    # Maskable variants — pad the mark into the inner 80% safe zone
    inner = _extract_inner(svg_bytes)
    maskable_svg = MASKABLE_SVG_TEMPLATE.format(inner=inner).encode("utf-8")
    for filename, size in MASKABLE_SIZES.items():
        out = OUT_DIR / filename
        cairosvg.svg2png(
            bytestring=maskable_svg,
            output_width=size,
            output_height=size,
            write_to=str(out),
        )
        kb = out.stat().st_size / 1024
        print(f"  -> {filename:<28} {size}x{size}  ({kb:.1f} KB)  [maskable]")

    print("\nDone. Remember to:")
    print("  1. cp the new PNGs into app/static/ for app.procta.net")
    print("  2. Commit alongside favicon.svg")


if __name__ == "__main__":
    main()

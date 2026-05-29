#!/usr/bin/env bash
# Regenerate build/icon.{icns,ico,png} from the marketing favicon SVG.
#
# Why a separate script instead of folding into gen_favicons.py:
#   gen_favicons.py needs cairosvg which is broken on macOS without
#   Homebrew libcairo. sips ships with macOS and renders SVG fine for
#   our rectangular icon. PIL handles the .ico packing.
#
# Run on macOS only — uses iconutil + sips. Run after editing the
# website favicon to keep the Electron app icon in sync.
#
# Usage:
#   ./scripts/gen_electron_icons.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVG="$ROOT/website/public/favicon.svg"
ICONSET="$ROOT/build/icon.iconset"
OUT_ICNS="$ROOT/build/icon.icns"
OUT_ICO="$ROOT/build/icon.ico"
OUT_PNG="$ROOT/build/icon.png"

if [ ! -f "$SVG" ]; then
  echo "ERR: $SVG not found"
  exit 1
fi

if ! command -v iconutil >/dev/null 2>&1; then
  echo "ERR: iconutil missing — run on macOS."
  exit 1
fi

if ! command -v sips >/dev/null 2>&1; then
  echo "ERR: sips missing — run on macOS."
  exit 1
fi

echo "→ Rendering SVG to 1024px master PNG..."
sips -s format png "$SVG" --resampleHeightWidth 1024 1024 --out "$OUT_PNG" >/dev/null

echo "→ Rebuilding $ICONSET..."
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# Apple-required iconset sizes. @2x means render at 2x and label as
# the base size; macOS picks the right one for the screen DPI.
declare -a sizes=(16 32 128 256 512)
for s in "${sizes[@]}"; do
  s2=$((s * 2))
  sips -s format png "$SVG" --resampleHeightWidth "$s" "$s" \
       --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -s format png "$SVG" --resampleHeightWidth "$s2" "$s2" \
       --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
# Top-end variant — iconset spec calls it icon_512x512@2x.png, not 1024.
cp "$OUT_PNG" "$ICONSET/icon_512x512@2x.png"

echo "→ Building $OUT_ICNS..."
iconutil -c icns "$ICONSET" -o "$OUT_ICNS"

echo "→ Building $OUT_ICO via PIL..."
python3 - "$OUT_PNG" "$OUT_ICO" <<'PY'
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGBA")
# Standard .ico sizes Windows expects.
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save(dst, format="ICO", sizes=sizes)
print(f"  wrote {dst}")
PY

echo "✓ Icons regenerated:"
ls -lh "$OUT_PNG" "$OUT_ICNS" "$OUT_ICO"

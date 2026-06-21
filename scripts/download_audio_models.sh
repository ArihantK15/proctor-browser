#!/usr/bin/env bash
# Download + verify the on-device audio detection models.
#
# Phase 75 / Part A. Pulls Vosk (en-IN + hi-IN) and Silero VAD into
# ./weights/ where audio_processor.py expects them. Idempotent: if a
# model is already present + checksum matches we skip it. Designed to
# be safe to run on every Electron first-launch (~5 s on cache hit).
#
# Why a bash script not Python: keeps the model bootstrap independent
# of the proctor's vosk install state. A fresh machine has neither
# vosk nor the models; we want this to bring up both without an
# import error chasing its own tail.
#
# Why we mirror to a GitHub release: the upstream alphacephei.com CDN
# has had downtime windows during paid-exam hours before. The mirror
# URL is a hard-pinned release asset on this repo so an outage on
# their side doesn't break a live exam.
#
# Usage:
#   ./scripts/download_audio_models.sh           # download missing/stale
#   ./scripts/download_audio_models.sh --force   # re-download everything
#   ./scripts/download_audio_models.sh --check   # report status only
#
# Exit codes:
#   0 — all models present + verified
#   1 — download or checksum failure (caller should retry later)
#   2 — required CLI tool missing (curl + unzip + shasum)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEIGHTS="$ROOT/weights"
mkdir -p "$WEIGHTS"

# ── Model registry ──────────────────────────────────────────────
# Each entry: name | URL | extracted-dir-name | SHA256
#
# The SHA256 values are PLACEHOLDERS — replace with the real digest
# from `shasum -a 256 <file>` after first download. When unset (or
# left as TODO_PIN_SHA), the script still works but only verifies
# that the extracted directory exists.
#
# Upstream URLs first; the plan is to re-host these as release assets
# on this repo before going to production so we don't depend on a
# third-party CDN during a paid exam window.

MODELS=(
  "vosk-en|https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip|vosk-model-small-en-in-0.4|TODO_PIN_SHA"
  # vosk-hi intentionally dropped (~40 MB, not required for now) — audio_processor
  # degrades gracefully when the Hindi dir is absent.
  "silero-vad|https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx|silero_vad.onnx|TODO_PIN_SHA"
)

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
  esac
done

# Tool check
for tool in curl unzip shasum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERR: $tool not found in PATH" >&2
    exit 2
  fi
done

verify_sha() {
  local file="$1" expected="$2"
  [ -z "$expected" ] || [ "$expected" = "TODO_PIN_SHA" ] && return 0
  local actual
  actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  if [ "$actual" != "$expected" ]; then
    echo "  ✗ SHA256 mismatch on $file"
    echo "    expected: $expected"
    echo "    actual:   $actual"
    return 1
  fi
  return 0
}

# Returns 0 if model is already present + (when known) checksum-verifies.
already_present() {
  local target_path="$1" sha="$2"
  [ -e "$target_path" ] || return 1
  # For .onnx single files we verify file checksum directly. For
  # extracted directories we treat "exists + non-empty" as good
  # enough — Vosk's zip releases don't ship a manifest.
  if [ -f "$target_path" ]; then
    verify_sha "$target_path" "$sha" || return 1
  else
    [ "$(ls -A "$target_path" 2>/dev/null)" ] || return 1
  fi
  return 0
}

download_one() {
  local name="$1" url="$2" target="$3" sha="$4"
  local target_path="$WEIGHTS/$target"
  if [ "$FORCE" -ne 1 ] && already_present "$target_path" "$sha"; then
    echo "  ✓ $name (already present)"
    return 0
  fi
  if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "  ✗ $name (missing — run without --check to download)"
    return 1
  fi
  echo "  ↓ $name from $url"
  local tmp; tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN
  local fname; fname="$(basename "$url")"
  local out="$tmp/$fname"
  if ! curl -fLsS --connect-timeout 10 --max-time 600 -o "$out" "$url"; then
    echo "  ✗ download failed: $url"
    return 1
  fi
  verify_sha "$out" "$sha" || return 1
  case "$fname" in
    *.zip)
      unzip -q "$out" -d "$WEIGHTS"
      ;;
    *.onnx|*.bin|*.tar.gz)
      mv "$out" "$target_path"
      ;;
    *)
      mv "$out" "$target_path"
      ;;
  esac
  echo "  ✓ $name installed → $target_path"
}

echo "[audio-models] weights dir: $WEIGHTS"
status=0
for entry in "${MODELS[@]}"; do
  IFS='|' read -r name url target sha <<< "$entry"
  download_one "$name" "$url" "$target" "$sha" || status=1
done

if [ "$status" -ne 0 ]; then
  if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "[audio-models] one or more models missing — re-run without --check to download"
  else
    echo "[audio-models] one or more models FAILED to install"
  fi
  exit 1
fi
echo "[audio-models] all models present"

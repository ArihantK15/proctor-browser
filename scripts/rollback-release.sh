#!/usr/bin/env bash
#
# rollback-release.sh — roll the PUBLIC "Latest" release pointer back to a
# known-good version when a freshly-published release turns out bad.
#
# WHAT THIS DOES
#   Marks v<good> as GitHub's "Latest" release. The website's /download/*
#   resolver (app/services/release.py follows /releases/latest) AND GitHub's
#   own /releases/latest/ redirect both track that flag, so NEW installs
#   immediately get <good> again — stopping the bleed from a bad release.
#
# WHAT THIS DOES *NOT* DO
#   It cannot downgrade clients that already auto-updated to the bad version:
#   electron-updater only moves FORWARD. To pull those back, ship a NEW higher
#   patch built from the good code:
#       git revert <bad-commit> && git tag v<higher> && git push origin v<higher>
#   (the verify-release CI job will gate that new tag.)
#
# USAGE
#   scripts/rollback-release.sh <good-version>      e.g. 2.3.26
#
# Requires: gh CLI authenticated with repo write access.

set -euo pipefail

GOOD="${1:-}"
if [ -z "$GOOD" ]; then
  echo "usage: $0 <good-version>   e.g. $0 2.3.26" >&2
  exit 2
fi
TAG="v${GOOD#v}"   # accept '2.3.26' or 'v2.3.26'

if ! command -v gh >/dev/null 2>&1; then
  echo "✗ gh CLI not found — install it and 'gh auth login' first" >&2
  exit 1
fi

if ! gh release view "$TAG" >/dev/null 2>&1; then
  echo "✗ release $TAG not found. Existing releases:" >&2
  gh release list --limit 10 >&2 || true
  exit 1
fi

CURRENT="$(gh release list --limit 30 --json tagName,isLatest \
  -q '.[] | select(.isLatest) | .tagName' 2>/dev/null || true)"
echo "Current Latest: ${CURRENT:-<none>}"
echo "Re-pointing Latest -> $TAG (new downloads will pull $GOOD)…"
gh release edit "$TAG" --latest

echo
echo "✓ $TAG is now the Latest release."
echo "  • New installs via /download/* and /releases/latest/ now get $GOOD."
echo "  • Clients already on the bad version will NOT auto-downgrade."
echo "    To revert them, ship a higher patch from the good code:"
echo "      git revert <bad-commit> && git tag v<higher> && git push origin v<higher>"

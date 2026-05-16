#!/usr/bin/env bash
# server_deploy.sh — run this on the KVM server to pull latest code and
# restart the API + workers.  The postgres profile is NOT restarted
# automatically (it persists across deploys; bring it up once manually).
#
# Usage:
#   cd /opt/proctor
#   ./scripts/server_deploy.sh
#
# Optional: pass a branch name to deploy from a non-main branch:
#   ./scripts/server_deploy.sh staging

set -euo pipefail

BRANCH="${1:-main}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> [deploy] $(date -u '+%Y-%m-%dT%H:%M:%SZ') branch=$BRANCH dir=$APP_DIR"

cd "$APP_DIR"

# ── 1. Pull latest code ──────────────────────────────────────────────────
echo "==> git fetch + checkout $BRANCH"
git fetch origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

# ── 2. Export build metadata ─────────────────────────────────────────────
export GIT_SHA="$(git rev-parse --short HEAD)"
export APP_VERSION="$(git describe --tags --always 2>/dev/null || echo "$GIT_SHA")"
export IMAGE_TAG="local"

echo "==> building sha=$GIT_SHA version=$APP_VERSION"

# ── 3. Build + restart API and workers (leave postgres/ofelia alone) ──────
docker compose build api worker autosave-worker

docker compose up -d --no-deps api worker autosave-worker

# ── 4. Wait for API healthcheck (max 90 s) ───────────────────────────────
echo "==> waiting for API to become healthy..."
for i in $(seq 1 18); do
  STATUS="$(docker inspect --format='{{.State.Health.Status}}' proctor-api 2>/dev/null || echo unknown)"
  if [ "$STATUS" = "healthy" ]; then
    echo "==> API healthy after $((i * 5))s"
    break
  fi
  if [ "$i" -eq 18 ]; then
    echo "ERROR: API did not become healthy within 90s"
    docker compose logs --tail=50 api
    exit 1
  fi
  sleep 5
done

# ── 5. Prune dangling images to reclaim disk ─────────────────────────────
docker image prune -f --filter "until=24h" >/dev/null 2>&1 || true

echo "==> deploy complete  sha=$GIT_SHA"

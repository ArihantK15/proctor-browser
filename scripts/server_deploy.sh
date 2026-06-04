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

# ── 4b. Sync the React dashboard bundle from the image onto the host ──────
# Caddy serves /static/* from the host bind-mount (./app/static -> /srv/static
# in docker-compose.yml), but app/static/dashboard-react/ is gitignored and is
# built ONLY inside the api image (Dockerfile COPY --from=uibuilder). So after
# a fresh checkout the host dir is empty and every Vite-hashed bundle asset
# 404s -> the React dashboard renders a black/blank #root. Copy the built
# bundle (incl. pre-gzipped variants) out of the running api container into the
# host dir Caddy reads. rm -rf first so stale old-hash assets from previous
# deploys don't accumulate. The api container is healthy by this point (step 4).
echo "==> syncing React dashboard bundle (image -> host static for Caddy)"
rm -rf ./app/static/dashboard-react
mkdir -p ./app/static/dashboard-react
docker cp proctor-api:/app/app/static/dashboard-react/. ./app/static/dashboard-react/

# ── 5. Prune dangling images to reclaim disk ─────────────────────────────
docker image prune -f --filter "until=24h" >/dev/null 2>&1 || true

echo "==> deploy complete  sha=$GIT_SHA"

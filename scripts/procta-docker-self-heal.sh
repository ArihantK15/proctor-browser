#!/bin/bash
# Procta — periodic docker self-heal cron.
#
# Real incident (2026-07-04 12:22 UTC): dockerd crashed (a buildkit
# concurrent-map-write fatal panic from overlapping build sessions — see
# journalctl -u docker around that timestamp). systemd's Restart=always
# brought the DAEMON back in ~3s, but the crash orphaned every running
# container's containerd task ("stale sandbox" / "sandbox ... not found"
# on daemon restart) — so `restart: unless-stopped` had nothing left to
# act on. Nothing was running again until a human noticed and manually
# ran `docker compose up -d`, ~10 minutes later. This script is that same
# recovery command, run on a short interval instead of waiting on a human.
#
# Installation: see DEPLOY.md §2.5.
#
# Deliberately conservative: only ever runs `docker compose up -d`
# (idempotent, a no-op when everything's already up) and checks postgres
# health afterward. Never touches images, never rebuilds, never restarts
# anything that's already healthy — that's the deploy workflow's job.

set -uo pipefail

PROJECT_DIR="${PROCTA_PROJECT_DIR:-/root/proctor-browser}"
LOCK_FILE="/tmp/procta-docker-self-heal.lock"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Prevent overlapping runs if a previous invocation is still in flight
# (e.g. docker itself is slow/degraded) — a stacked second run fighting
# the same compose project would only make diagnosis harder.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(ts)] self-heal: previous run still in progress, skipping"
  exit 0
fi

cd "$PROJECT_DIR" || { echo "[$(ts)] ERROR: cannot cd to $PROJECT_DIR"; exit 1; }

before="$(docker compose --compatibility --profile postgres ps --services --filter 'status=running' 2>/dev/null | sort)"

up_output="$(docker compose --compatibility --profile postgres up -d 2>&1)"

after="$(docker compose --compatibility --profile postgres ps --services --filter 'status=running' 2>/dev/null | sort)"

if [ "$before" = "$after" ]; then
  echo "[$(ts)] self-heal: no-op, all services already running"
else
  brought_back="$(comm -13 <(echo "$before") <(echo "$after"))"
  echo "[$(ts)] self-heal: brought back service(s): $(echo "$brought_back" | tr '\n' ' ')"
  echo "[$(ts)] self-heal: docker compose up -d output:"
  echo "$up_output"
fi

# Postgres-health sanity check, same pattern as the deploy preflight
# hard-stop (.github/workflows/deploy.yml) — loud, not silent.
PG_STATUS=$(docker inspect --format='{{.State.Health.Status}}' proctor-postgres 2>/dev/null || echo "missing")
if [ "$PG_STATUS" != "healthy" ]; then
  echo "[$(ts)] ERROR: postgres is not healthy after self-heal (status: ${PG_STATUS}) — needs manual attention"
fi

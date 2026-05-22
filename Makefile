# Procta operator shortcuts. Tab-indented (make syntax).
#
# Usage: from the repo root on the KVM:
#   make up       — start everything with the right worker scale
#   make scale    — re-apply the worker scale without recreating containers
#   make logs     — tail api + worker logs together
#   make health   — quick health snapshot (CPU, queue depth, pg backends)
#   make down     — stop everything
#
# We can't use docker-compose's deploy.replicas in non-swarm mode, so
# the scaling parameter has to be supplied on every `up` command.
# Codifying it here makes "run with N workers" a memorable one-liner.

WORKER_REPLICAS ?= 16
AUTOSAVE_WORKER_REPLICAS ?= 2

.PHONY: up scale logs health down restart pull

up:
	docker compose up -d --scale worker=$(WORKER_REPLICAS) --scale autosave-worker=$(AUTOSAVE_WORKER_REPLICAS)
	@echo ""
	@echo "✓ Stack up with $(WORKER_REPLICAS) scoring workers + $(AUTOSAVE_WORKER_REPLICAS) autosave workers"
	@docker compose ps | grep -E "NAME|worker|api|postgres" | head -20

scale:
	docker compose up -d --no-recreate --scale worker=$(WORKER_REPLICAS) --scale autosave-worker=$(AUTOSAVE_WORKER_REPLICAS)

logs:
	docker compose logs -f --tail=50 api worker

health:
	@echo "── docker stats ──"
	@docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | head -20
	@echo ""
	@echo "── Postgres backends ──"
	@docker exec proctor-postgres psql -U procta -d procta -tA -c \
	  "SELECT state, COUNT(*) FROM pg_stat_activity WHERE datname='procta' GROUP BY state"
	@echo ""
	@echo "── pgbouncer pools (if running) ──"
	@docker exec proctor-pgbouncer psql -h 127.0.0.1 -p 6432 -U procta -d pgbouncer \
	  -c "SHOW POOLS" 2>/dev/null || echo "  pgbouncer not running or not yet wired to app"
	@echo ""
	@echo "── Redis queues ──"
	@echo -n "  scoring: "; docker exec proctor-redis redis-cli LLEN rq:queue:scoring
	@echo -n "  autosave: "; docker exec proctor-redis redis-cli LLEN rq:queue:autosave
	@echo -n "  default: "; docker exec proctor-redis redis-cli LLEN rq:queue:default
	@echo ""
	@echo "── Kernel SYN backlog ──"
	@nstat -az TcpExtListenOverflows TcpExtListenDrops TcpExtTCPBacklogDrop | grep -v "#"

# pgbouncer-specific diagnostics. Call after `make up` and any load
# test to see how many real Postgres backends pgbouncer is using vs
# how many app-side clients it's juggling.
pgbouncer-stats:
	@echo "── SHOW POOLS (per-database, per-user) ──"
	@docker exec proctor-pgbouncer psql -h 127.0.0.1 -p 6432 -U procta -d pgbouncer -c "SHOW POOLS"
	@echo ""
	@echo "── SHOW CLIENTS (last 20 connected) ──"
	@docker exec proctor-pgbouncer psql -h 127.0.0.1 -p 6432 -U procta -d pgbouncer -c "SHOW CLIENTS" | head -25
	@echo ""
	@echo "── SHOW STATS (totals since boot) ──"
	@docker exec proctor-pgbouncer psql -h 127.0.0.1 -p 6432 -U procta -d pgbouncer -c "SHOW STATS"

# Verify the app/workers ARE going through pgbouncer. If they're
# bypassing it, this prints "WARNING: …".
pgbouncer-verify:
	@docker exec proctor-api env 2>/dev/null | grep -E "DATABASE_URL|DATABASE_USE_PGBOUNCER" || true
	@docker exec proctor-api python -c "import os; url = os.environ.get('DATABASE_URL', ''); print('  api → ' + ('pgbouncer ✓' if 'pgbouncer' in url else 'postgres direct ✗ (not via pgbouncer)'))"

restart:
	docker compose restart api caddy
	@echo "✓ api + caddy restarted (workers + postgres untouched)"

down:
	docker compose down

pull:
	cd $(shell pwd) && git pull --rebase=false
	docker compose pull
	@echo "✓ pulled latest code + images"

# Distributed load test — convenience wrapper. Most users won't use
# this directly (the orchestrator is run from the Mac, not the KVM),
# but it's here as a reference for the canonical invocation.
load-3000:
	@echo "Run this FROM YOUR MAC, not the KVM:"
	@echo "  loadtest/run_distributed.sh loadtest@procta.net 'LoadTest!2026' 3000 300"

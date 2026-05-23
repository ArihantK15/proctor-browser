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

# Empirically tuned 2026-05-23: at 1500 VU production-path load on a
# 4-vCPU KVM, 8 workers × 1.0 CPU cap drains the scoring queue 100%
# (1500/1500 jobs) with avg latency 8.7s. The prior 16 × 0.5 config
# only drained 13% (196/1500) because each worker was throttled to
# ~0.25 effective core under saturation. Same total CPU budget, but
# half the context-switch overhead lets jobs actually finish.
WORKER_REPLICAS ?= 8
AUTOSAVE_WORKER_REPLICAS ?= 2

# postgres + pgbouncer are gated behind the "postgres" profile so the
# legacy Supabase-only deploys don't accidentally spin up an unused
# postgres instance. For local-postgres deploys (which is what the
# KVM uses) we always want the profile on. COMPOSE_PROFILES is
# Docker Compose's standard env-var for this.
export COMPOSE_PROFILES = postgres

.PHONY: up scale logs health down restart pull

up:
	docker compose up -d --scale worker=$(WORKER_REPLICAS) --scale autosave-worker=$(AUTOSAVE_WORKER_REPLICAS)
	@echo ""
	@echo "✓ Stack up with $(WORKER_REPLICAS) scoring workers + $(AUTOSAVE_WORKER_REPLICAS) autosave workers"
	@docker compose ps | grep -E "NAME|worker|api|postgres|pgbouncer" | head -25

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
	@# psql isn't in the pgbouncer image. Run psql from proctor-postgres
	@# (which has psql) targeting the pgbouncer container by service name.
	@PGPASSWORD=$$(grep ^POSTGRES_PASSWORD= .env 2>/dev/null | head -1 | cut -d= -f2-); \
	  docker exec -e PGPASSWORD=$$PGPASSWORD proctor-postgres \
	    psql -h proctor-pgbouncer -p 6432 -U procta -d pgbouncer \
	    -c "SHOW POOLS" 2>/dev/null \
	  || echo "  pgbouncer not running, not wired, or auth missing"
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
	@# psql lives in the proctor-postgres image, NOT proctor-pgbouncer.
	@# We exec into proctor-postgres and connect over the Docker network
	@# to proctor-pgbouncer. PGPASSWORD comes from .env on the host.
	@PGPASSWORD=$$(grep ^POSTGRES_PASSWORD= .env 2>/dev/null | head -1 | cut -d= -f2-); \
	  test -n "$$PGPASSWORD" || { echo "ERROR: POSTGRES_PASSWORD not in .env"; exit 1; }; \
	  echo "── SHOW POOLS (per-database, per-user) ──"; \
	  docker exec -e PGPASSWORD=$$PGPASSWORD proctor-postgres \
	    psql -h proctor-pgbouncer -p 6432 -U procta -d pgbouncer -c "SHOW POOLS"; \
	  echo ""; \
	  echo "── SHOW CLIENTS (first 25) ──"; \
	  docker exec -e PGPASSWORD=$$PGPASSWORD proctor-postgres \
	    psql -h proctor-pgbouncer -p 6432 -U procta -d pgbouncer -c "SHOW CLIENTS" | head -25; \
	  echo ""; \
	  echo "── SHOW STATS (totals since boot) ──"; \
	  docker exec -e PGPASSWORD=$$PGPASSWORD proctor-postgres \
	    psql -h proctor-pgbouncer -p 6432 -U procta -d pgbouncer -c "SHOW STATS"

# Verify the app/workers ARE going through pgbouncer (not direct to
# postgres). Reads the DATABASE_URL from inside the running api
# container — that's the runtime truth, not the .env file on disk.
pgbouncer-verify:
	@echo "── api container DATABASE_URL ──"
	@docker exec proctor-api sh -c 'echo "$$DATABASE_URL" | sed "s/:[^:@]*@/:****@/"' 2>/dev/null \
	  || { echo "ERROR: proctor-api container not running"; exit 1; }
	@docker exec proctor-api sh -c \
	  'case "$$DATABASE_URL" in *pgbouncer*) echo "  api → pgbouncer ✓";; *) echo "  api → direct postgres ✗ (DATABASE_URL needs pgbouncer:6432)";; esac'
	@docker exec proctor-api sh -c \
	  'case "$$DATABASE_USE_PGBOUNCER" in 1|true|yes) echo "  asyncpg statement_cache → disabled ✓";; *) echo "  asyncpg statement_cache → ENABLED ✗ (set DATABASE_USE_PGBOUNCER=1 or you will see prepared-statement errors)";; esac'

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

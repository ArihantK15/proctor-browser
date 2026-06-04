# ── Stage 0: Build the React teacher dashboard ─────────────────────
# Served by the API at /dashboard-react (the live view + camera panel).
# app/static/dashboard-react/ is gitignored and the prod checkout never
# had it, so /dashboard-react used to 404. Build it here so the runtime
# image always ships the current dashboard. vite outDir is
# ../static/dashboard-react, so mirror the app/dashboard-ui ↔ app/static
# layout the config expects.
FROM node:22-slim AS uibuilder
WORKDIR /ui
COPY app/dashboard-ui/package.json app/dashboard-ui/package-lock.json ./app/dashboard-ui/
RUN cd app/dashboard-ui && npm ci --no-audit --no-fund
COPY app/dashboard-ui ./app/dashboard-ui
RUN cd app/dashboard-ui && npm run build
# → /ui/app/static/dashboard-react/{index.html,assets/…}

# ── Stage 1: Build deps ────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.lock .

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libc-dev && \
    pip install --no-cache-dir --prefix=/install -r requirements.lock && \
    apt-get purge -y gcc libc-dev && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# ── Stage 2: Runtime ───────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy compiled packages from builder
COPY --from=builder /install /usr/local

# Copy application code + entrypoint
COPY app/ ./app/
# Built React dashboard (overlays the gitignored, context-absent dir so
# /dashboard-react serves a real app instead of 404).
COPY --from=uibuilder /ui/app/static/dashboard-react ./app/static/dashboard-react
COPY worker.py .
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

# Pre-compress static assets (Caddy serves .gz variants directly)
RUN find /app/static -type f \( -name "*.html" -o -name "*.js" -o -name "*.css" \) \
    -exec gzip -9 -k {} \; 2>/dev/null || true

# Non-root user for security
RUN useradd --system --create-home appuser && \
    mkdir -p /app/screenshots /app/question_images && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENV PYTHONMALLOC=malloc PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/entrypoint.sh"]

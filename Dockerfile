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

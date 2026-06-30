"""Admin status page — /api/v1/admin/status endpoint + /status-page HTML."""

import json
import os
import time
import logging
from typing import Any, cast
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..auth.admin_auth import require_admin
from ..limiter import limiter

_log = logging.getLogger("admin_status")

router = APIRouter(prefix="/api/v1/admin", tags=["admin_status"])

_STATUS_PAGE_CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f7fa;color:#1e293b;padding:40px}
h1{font-size:24px;margin-bottom:8px}
.sub{color:#64748b;font-size:14px;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.card{border-radius:12px;padding:20px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h3{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:#64748b;margin-bottom:12px}
.status-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}
.ok{background:#22c55e}
.warning{background:#f59e0b}
.critical,.error,.stale,.misconfigured,.no_heartbeat{background:#ef4444}
.unavailable{background:#94a3b8}
.noop{background:#a78bfa}
.value{font-size:28px;font-weight:600}
.meta{font-size:12px;color:#94a3b8;margin-top:4px}.meta span{margin-right:12px}
"""

_STATUS_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>System Status — Procta</title>
<style>{css}</style>
</head>
<body>
<h1>System Status</h1>
<p class="sub" id="ts">Loading...</p>
<div class="grid" id="grid"></div>
<script src="/static/admin-status.js" defer></script>
</body>
</html>
"""


_REQ_TS = time.time()

@router.get("/status")
@limiter.limit("30/minute")
async def get_status(request: Request):
    """Return system status JSON (admin only)."""
    teacher = await require_admin(request)
    _ = teacher  # used for auth only

    checks: dict[str, str] = {}
    metrics: dict[str, Any] = {}
    release = {
        "environment": os.environ.get("SENTRY_ENVIRONMENT", os.environ.get("APP_ENV", "production")),
        "version": os.environ.get("APP_VERSION", ""),
        "commit": os.environ.get("GIT_SHA", os.environ.get("SOURCE_COMMIT", os.environ.get("GIT_COMMIT", ""))),
        "image": os.environ.get("IMAGE_TAG", ""),
        "sentry_configured": bool(os.environ.get("SENTRY_DSN")),
    }
    ok = True

    # Error rate from startup counters
    try:
        from ..main import _METRICS
        metrics["total_requests"] = _METRICS.get("request_count", 0)
        metrics["total_errors"] = _METRICS.get("error_count", 0)
        total_req = max(metrics["total_requests"], 1)
        metrics["error_rate_pct"] = round(metrics["total_errors"] / total_req * 100, 2)
    except Exception:
        _log.warning("admin_status: metrics gather failed", exc_info=True)

    # Database
    try:
        from ..database import async_table as _atable
        await _atable("exam_config").select("id").limit(1).execute()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        ok = False

    # Redis (reuse module-level client via cache module)
    try:
        from .. import cache as _cache
        if _cache:
            r = _cache._client() if hasattr(_cache, '_client') else None
            if r:
                r.ping()
                checks["redis"] = "ok"
                try:
                    info = r.info()
                    metrics["redis_connected_clients"] = int(info.get("connected_clients", 0))
                except Exception:
                    _log.debug("admin_status: redis info gather failed", exc_info=True)
            else:
                checks["redis"] = "unavailable"
        else:
            checks["redis"] = "unavailable"
    except Exception:
        checks["redis"] = "error"
        ok = False

    # Worker heartbeat
    try:
        from .. import cache as _cache
        if _cache:
            r = _cache._client() if hasattr(_cache, '_client') else None
            if r:
                hb = cast(str, r.get("worker:last_heartbeat"))
                if hb:
                    age = time.time() - float(hb)
                    checks["worker"] = "ok" if age < 60 else "stale"
                    metrics["worker_heartbeat_age_sec"] = round(age, 1)
                    if age >= 60:
                        ok = False
                else:
                    checks["worker"] = "no_heartbeat"
            else:
                checks["worker"] = "unavailable"
        else:
            checks["worker"] = "unavailable"
    except Exception:
        checks["worker"] = "error"

    # RQ queue depth / failures
    try:
        from rq import Queue
        from rq.registry import FailedJobRegistry, ScheduledJobRegistry, StartedJobRegistry
        from redis import Redis
        from ..jobs import _redis_url
        queue_name = os.environ.get("RQ_QUEUE", "default")
        conn = Redis.from_url(_redis_url())
        q = Queue(queue_name, connection=conn)
        metrics["queue_name"] = queue_name
        metrics["queue_depth"] = int(q.count)
        metrics["queue_started"] = len(StartedJobRegistry(queue=q).get_job_ids())
        metrics["queue_failed"] = len(FailedJobRegistry(queue=q).get_job_ids())
        metrics["queue_scheduled"] = len(ScheduledJobRegistry(queue=q).get_job_ids())
        if metrics["queue_failed"] > 0:
            checks["queue"] = "warning"
        elif metrics["queue_depth"] > int(os.environ.get("OPS_QUEUE_DEPTH_WARN", "100")):
            checks["queue"] = "warning"
        else:
            checks["queue"] = "ok"
    except Exception:
        metrics["queue_depth"] = None
        metrics["queue_failed"] = None
        checks["queue"] = "unavailable"

    # Email
    try:
        provider = os.environ.get("EMAIL_PROVIDER", "resend")
        email_from = os.environ.get("EMAIL_FROM", "")
        if provider != "noop" and email_from:
            checks["email"] = "ok"
        elif provider == "noop":
            checks["email"] = "noop"
        else:
            checks["email"] = "misconfigured"
            ok = False
    except Exception:
        checks["email"] = "error"

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        pct = mem.percent
        checks["memory_pct"] = f"{pct}%"
        metrics["memory_pct"] = pct
        if pct >= float(os.environ.get("OPS_MEMORY_CRITICAL_PCT", "95")):
            ok = False
        elif pct >= float(os.environ.get("OPS_MEMORY_WARN_PCT", "85")):
            checks["memory_pct"] = "warning"
    except ImportError:
        pass

    # Disk
    try:
        import shutil
        target = os.getenv("SCREENSHOTS_DIR", "/var/lib/proctor/screenshots")
        total, used, free = shutil.disk_usage(target)
        free_mb = free // (1024 * 1024)
        if free_mb < 500:
            checks["disk"] = "critical"
            ok = False
        elif free_mb < 2000:
            checks["disk"] = "warning"
        else:
            checks["disk"] = "ok"
        checks["disk_free_mb"] = f"{free_mb} MB"

        os.makedirs(target, exist_ok=True)
        test_path = os.path.join(target, ".health_write_test")
        with open(test_path, "wb") as f:
            f.write(b"ok")
        os.remove(test_path)
        checks["storage_write"] = "ok"
    except Exception:
        checks["storage_write"] = "error"

    # Product/operator metrics
    try:
        from ..database import async_table as _atable
        active = await _atable("exam_sessions")\
            .select("session_key", count="exact")\
            .eq("status", "in_progress")\
            .execute()
        metrics["active_sessions"] = active.count or 0
    except Exception:
        metrics["active_sessions"] = None

    try:
        from ..database import async_table as _atable
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        failed = await _atable("violations")\
            .select("session_key", count="exact")\
            .eq("violation_type", "submit_failed")\
            .gte("created_at", since)\
            .execute()
        metrics["submit_failures_24h"] = failed.count or 0
    except Exception:
        metrics["submit_failures_24h"] = None

    # Fleet proctor health — device-failure rates Sentry can't see (the events
    # POST as 200s). Surfaced here for the status page + any admin monitor; the
    # leader worker's alert loop pages on a sustained breach. A breach degrades
    # the overall status so a watcher on the `status` field notices.
    try:
        from ..services.fleet_health import proctor_fleet_health
        ph = await proctor_fleet_health()
        metrics["proctor_health"] = ph
        if ph.get("degraded"):
            ok = False
    except Exception:
        _log.warning("admin_status: proctor health gather failed", exc_info=True)
        metrics["proctor_health"] = None

    uptime_sec = round(time.time() - _REQ_TS, 1)

    return {
        "status": "ok" if ok else "degraded",
        "uptime_sec": uptime_sec,
        "health_checks": len(checks),
        "checks": checks,
        "metrics": metrics,
        "release": release,
    }


@router.get("/status-page")
async def status_page(request: Request):
    """Admin status page HTML."""
    html = _STATUS_PAGE_HTML.replace("{css}", _STATUS_PAGE_CSS, 1)
    return HTMLResponse(content=html)

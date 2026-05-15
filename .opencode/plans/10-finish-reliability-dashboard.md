# Finish Reliability Dashboard

## Remaining items

### 1. Deploy version info (backend)

**File:** `app/routers/admin_status.py` — add release metadata to the status response:

```python
release = {
    "environment": os.environ.get("PROCTOR_ENV", "production"),
    "version": os.environ.get("APP_VERSION", os.environ.get("RELEASE_TAG", "")),
    "commit": os.environ.get("GIT_COMMIT", os.environ.get("SOURCE_COMMIT", "")),
    "image": os.environ.get("IMAGE_TAG", ""),
    "sentry_configured": bool(os.environ.get("SENTRY_DSN")),
}
```

These env vars are set by the deploy pipeline (GitHub Actions injects `GIT_COMMIT`, `IMAGE_TAG`, `RELEASE_TAG`). Add `release` to the returned dict.

### 2. Local error rate (backend)

**File:** `app/main.py` already has `_METRICS = {"request_count": 0, "error_count": 0, ...}` tracked by `_count_requests` middleware. The status endpoint already uses `metrics`, but doesn't read error count. Add:

```python
# In admin_status.py get_status():
from ..main import _METRICS
metrics["total_requests"] = _METRICS.get("request_count", 0)
metrics["error_rate_5m"] = _METRICS.get("error_count", 0) / max(_METRICS.get("request_count", 1), 1)
```

Alternatively, export `error_rate_5m` via a rolling window. For MVP, export total error rate from the startup counters.

### 3. Health thresholds indication (frontend)

**File:** `app/dashboard-ui/src/panels/OpsPanel.jsx`

- Add tone/warning coloring to MetricCard based on thresholds
- Show `queue_failed > 0` as warning (amber), not bad (red) — already handled
- Add `disk_free_mb` threshold coloring (<500 MB red, <2000 amber)
- Show `active_sessions` as a simple number (no coloring needed)

### 4. Queue retry details (backend)

**File:** `app/routers/admin_status.py` — already has `queue_depth`, `queue_failed`. Add:

```python
from rq.registry import ScheduledJobRegistry
metrics["queue_scheduled"] = len(ScheduledJobRegistry(queue=q).get_job_ids())
```

### Files to modify:
- `app/routers/admin_status.py` — add release metadata + error rate + scheduled jobs
- `app/dashboard-ui/src/panels/OpsPanel.jsx` — add threshold-aware coloring, show deploy info

### Effort: ~2 hr

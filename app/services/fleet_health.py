"""Fleet proctor-health: the rate of on-device failures across recent sessions.

`proctor_camera_failed` / `model_load_failed` are POSTed by proctor.py as
violations and the request SUCCEEDS (200), so Sentry's exception capture never
sees them. A fleet-wide regression — the RetinaFace model download failing for
every offline student, a build shipping a dead onnxruntime, a bad camera-driver
rollout — therefore stays invisible until one student happens to report it
(exactly the reactive-firefighting pattern this hardening closes).

This computes the failure RATE (denominator = `proctor_boot`, i.e. how many
proctors actually started) so:
  • /status surfaces it for the admin status page, and
  • the leader-worker alert loop (main.py lifespan) pages on a breach —
    WARNING log always, Sentry capture_message when SENTRY_DSN is set.

Thresholds are env-tunable; a minimum boot count avoids crying wolf on a quiet
hour where a single failure is 100%.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from ..database import async_table as _atable

logger = logging.getLogger(__name__)

WINDOW_MINS = int(os.environ.get("PROCTOR_HEALTH_WINDOW_MINS", "60"))
MIN_BOOTS = int(os.environ.get("PROCTOR_HEALTH_MIN_BOOTS", "5"))
CAMERA_FAIL_PCT = float(os.environ.get("PROCTOR_CAMERA_FAIL_PCT", "20"))
MODEL_FAIL_PCT = float(os.environ.get("PROCTOR_MODEL_FAIL_PCT", "30"))
ALERT_INTERVAL_SEC = int(os.environ.get("PROCTOR_HEALTH_ALERT_INTERVAL_SEC", "600"))


async def _count(vtype: str, since: str) -> int:
    r = await _atable("violations").select("session_key", count="exact")\
        .eq("violation_type", vtype).gte("created_at", since).execute()
    return r.count or 0


async def proctor_fleet_health(window_mins: int = WINDOW_MINS) -> dict:
    """Recent on-device failure rates. Cheap (3 COUNT queries on an indexed
    column). Returns rates + a `degraded` verdict the caller can act on."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=window_mins)).isoformat()
    boots = await _count("proctor_boot", since)
    camera_failed = await _count("proctor_camera_failed", since)
    model_failed = await _count("model_load_failed", since)
    denom = max(boots, 1)
    camera_pct = round(camera_failed / denom * 100, 1)
    model_pct = round(model_failed / denom * 100, 1)
    degraded = (
        boots >= MIN_BOOTS
        and (camera_pct >= CAMERA_FAIL_PCT or model_pct >= MODEL_FAIL_PCT)
    )
    return {
        "window_mins": window_mins,
        "boots": boots,
        "camera_failed": camera_failed,
        "model_load_failed": model_failed,
        "camera_failed_pct": camera_pct,
        "model_load_failed_pct": model_pct,
        "degraded": degraded,
    }


def _alert_message(ph: dict) -> str:
    return (
        f"Fleet proctor health DEGRADED: camera_failed={ph['camera_failed_pct']}% "
        f"({ph['camera_failed']}), model_load_failed={ph['model_load_failed_pct']}% "
        f"({ph['model_load_failed']}) over {ph['window_mins']}m across {ph['boots']} boots"
    )


async def proctor_health_alert_loop() -> None:
    """Leader-worker loop: periodically check fleet proctor health and alert on
    a breach. The WARNING log fires regardless (visible in the OBSERVABILITY
    runbook tail); Sentry capture only fires when SENTRY_DSN is configured —
    sentry_sdk.capture_message is a safe no-op when the SDK isn't initialized."""
    while True:
        try:
            ph = await proctor_fleet_health()
            if ph["degraded"]:
                msg = _alert_message(ph)
                logger.error("[ALERT] %s", msg)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_message(msg, level="error")
                except Exception:
                    logger.debug("proctor_health: sentry capture skipped", exc_info=True)
        except Exception:
            logger.warning("proctor_health: check failed", exc_info=True)
        await asyncio.sleep(ALERT_INTERVAL_SEC)

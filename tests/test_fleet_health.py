"""Fleet proctor-health rate + degraded verdict.

Device failures (proctor_camera_failed / model_load_failed) POST as 200s, so
they're invisible to Sentry's exception capture. proctor_fleet_health computes
the rate (denominator = proctor_boot) so /status + the leader-worker alert loop
can catch a fleet-wide regression. These lock the rate math and the degraded
verdict (incl. the min-boots guard that avoids crying wolf on a quiet hour).
"""
import asyncio
from unittest.mock import patch, AsyncMock

from app.services import fleet_health


def _with_counts(mapping):
    """Patch _count so each violation_type returns a fixed count."""
    def _f(vtype, since):
        return mapping.get(vtype, 0)
    return patch.object(fleet_health, "_count", new=AsyncMock(side_effect=_f))


def _health(mapping):
    with _with_counts(mapping):
        return asyncio.run(fleet_health.proctor_fleet_health())


def test_rates_computed_against_boots():
    ph = _health({"proctor_boot": 20, "proctor_camera_failed": 5, "model_load_failed": 2})
    assert ph["boots"] == 20
    assert ph["camera_failed_pct"] == 25.0   # 5/20
    assert ph["model_load_failed_pct"] == 10.0


def test_degraded_when_camera_fail_rate_high():
    ph = _health({"proctor_boot": 10, "proctor_camera_failed": 4, "model_load_failed": 0})
    assert ph["camera_failed_pct"] == 40.0
    assert ph["degraded"] is True


def test_degraded_when_model_fail_rate_high():
    ph = _health({"proctor_boot": 10, "proctor_camera_failed": 0, "model_load_failed": 4})
    assert ph["degraded"] is True


def test_healthy_fleet_not_degraded():
    ph = _health({"proctor_boot": 50, "proctor_camera_failed": 1, "model_load_failed": 1})
    assert ph["degraded"] is False


def test_below_min_boots_never_degraded():
    # 2 of 2 boots failed = 100%, but the sample is too small to flag.
    ph = _health({"proctor_boot": 2, "proctor_camera_failed": 2, "model_load_failed": 0})
    assert ph["boots"] == 2
    assert ph["camera_failed_pct"] == 100.0
    assert ph["degraded"] is False


def test_zero_boots_no_divide_by_zero():
    ph = _health({"proctor_boot": 0, "proctor_camera_failed": 0, "model_load_failed": 0})
    assert ph["camera_failed_pct"] == 0.0
    assert ph["degraded"] is False

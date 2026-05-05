"""
Performance profiling for the Procta stack.

Measures:
1. Server memory footprint (FastAPI API)
2. Per-endpoint latency under load
3. Estimated capacity on 2GB droplet

Run: python3 scripts/profile_performance.py
"""
import os
import sys
import time
import json
import resource
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Setup — mock heavy deps BEFORE importing app ─────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("SCREENSHOTS_DIR", "/tmp/procta_prof_screenshots")
os.environ.setdefault("QUESTION_IMG_DIR", "/tmp/procta_prof_qimages")

from unittest.mock import MagicMock, AsyncMock

# Mock supabase
_mock_supabase = MagicMock()
_mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.select.return_value.execute.return_value = MagicMock(data=[])
_mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
mock_database = MagicMock()
mock_database.supabase = _mock_supabase
mock_database.async_table = MagicMock()
sys.modules["app.database"] = mock_database

# Mock other deps
sys.modules["app.logger"] = MagicMock()
sys.modules["app.event_bus"] = MagicMock()
sys.modules["app.cache"] = MagicMock()
sys.modules["app.emailer"] = MagicMock()

from fastapi.testclient import TestClient
from app.main import app


def get_memory_mb():
    """Return current process RSS in MB (approximate on macOS)."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ru.ru_maxrss / 1024  # macOS reports in KB


def profile_endpoint_latency(client, endpoint, method="GET", payload=None, headers=None, n=50):
    """Measure latency distribution for an endpoint."""
    latencies = []
    errors = 0

    def make_request():
        try:
            start = time.perf_counter()
            if method == "GET":
                client.get(endpoint, headers=headers)
            else:
                client.post(endpoint, json=payload, headers=headers)
            elapsed = (time.perf_counter() - start) * 1000
            return elapsed
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(make_request) for _ in range(n)]
        for f in as_completed(futures):
            result = f.result()
            if result is not None:
                latencies.append(result)
            else:
                errors += 1

    if not latencies:
        return {"error": "all requests failed"}

    latencies.sort()
    return {
        "requests": len(latencies),
        "errors": errors,
        "p50_ms": round(latencies[len(latencies) // 2], 1),
        "p95_ms": round(latencies[int(len(latencies) * 0.95)], 1),
        "p99_ms": round(latencies[int(len(latencies) * 0.99)], 1),
        "min_ms": round(min(latencies), 1),
        "max_ms": round(max(latencies), 1),
        "avg_ms": round(sum(latencies) / len(latencies), 1),
    }


def profile_concurrent_load(client, endpoint, method="GET", payload=None, headers=None, n=100, workers=10):
    """Measure throughput under concurrent load."""
    success = 0
    errors = 0
    total_time = 0.0

    def make_request():
        nonlocal success, errors, total_time
        try:
            start = time.perf_counter()
            if method == "GET":
                client.get(endpoint, headers=headers)
            else:
                client.post(endpoint, json=payload, headers=headers)
            total_time += (time.perf_counter() - start)
            success += 1
        except Exception:
            errors += 1

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(make_request) for _ in range(n)]
        for f in as_completed(futures):
            f.result()
    wall_elapsed = time.perf_counter() - wall_start

    return {
        "total_requests": n,
        "successful": success,
        "errors": errors,
        "wall_time_s": round(wall_elapsed, 2),
        "throughput_rps": round(success / wall_elapsed, 1) if wall_elapsed > 0 else 0,
        "avg_latency_ms": round((total_time / success) * 1000, 1) if success > 0 else 0,
    }


def main():
    print("=" * 60)
    print("PROCTA PERFORMANCE PROFILE")
    print("=" * 60)

    client = TestClient(app, raise_server_exceptions=False)

    # ── Memory (rough estimate) ──────────────────────────────────────────
    print("\n📊 Memory Footprint")
    print("-" * 40)
    baseline = get_memory_mb()
    # Warm up
    client.get("/health")
    time.sleep(0.2)
    after_init = get_memory_mb()
    # Make some requests to populate caches
    for _ in range(20):
        client.get("/health")
    time.sleep(0.2)
    after_load = get_memory_mb()
    # On macOS getrusage reports total virtual memory, not container RSS.
    # The delta is more meaningful than the absolute number.
    print(f"  RSS delta (init → load): {after_load - after_init:+.0f} MB")
    print(f"  Docker limit: 1000 MB (API container)")
    print(f"  Note: macOS reports total VM; actual container RSS will be lower")

    # ── Latency ───────────────────────────────────────────────────────
    print("\n⏱️  Endpoint Latency (30 requests each)")
    print("-" * 40)

    from jose import jwt
    from datetime import datetime, timezone, timedelta

    def make_student_token():
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {"roll": "TEST001", "tid": "t1", "eid": "e1", "iat": now,
             "exp": now + timedelta(hours=1)},
            os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")

    def make_admin_token():
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {"tid": "t1", "email": "a@b.com", "role": "teacher",
             "exp": now + timedelta(hours=1), "iat": now},
            os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")

    student_headers = {"Authorization": f"Bearer {make_student_token()}"}
    admin_headers = {"Authorization": f"Bearer {make_admin_token()}"}

    endpoints = [
        ("GET /health", "/health", None, None),
        ("POST /heartbeat (student)", "/heartbeat",
         {"session_id": "s1", "event_type": "heartbeat", "severity": "low", "details": "alive"},
         student_headers),
        ("POST /event (student)", "/event",
         {"session_id": "s1", "event_type": "gaze_away", "severity": "medium",
          "details": "test", "timestamp": datetime.now(timezone.utc).isoformat()},
         student_headers),
        ("GET /api/v1/verify (admin)", "/api/v1/verify?session_id=s1", None, admin_headers),
    ]

    for name, endpoint, payload, headers in endpoints:
        result = profile_endpoint_latency(client, endpoint,
                                          "POST" if payload else "GET",
                                          payload, headers, n=30)
        if "error" in result:
            print(f"  {name}: {result['error']}")
        else:
            print(f"  {name}")
            print(f"    p50: {result['p50_ms']}ms | p95: {result['p95_ms']}ms | "
                  f"p99: {result['p99_ms']}ms | avg: {result['avg_ms']}ms")

    # ── Throughput ────────────────────────────────────────────────────
    print("\n🚀 Concurrent Throughput (100 requests, 10 workers)")
    print("-" * 40)

    tp = profile_concurrent_load(client, "/health", n=100, workers=10)
    print(f"  Throughput: {tp['throughput_rps']} req/s")
    print(f"  Avg latency: {tp['avg_latency_ms']}ms")
    print(f"  Errors: {tp['errors']}/{tp['total_requests']}")

    # ── Capacity estimate ─────────────────────────────────────────────
    print("\n📈 2GB Droplet Capacity Estimate")
    print("-" * 40)
    print(f"  Docker limits (docker-compose.yml):")
    print(f"    API:  1000 MB RAM, 1.5 CPU (2 workers × 500 MB)")
    print(f"    Caddy: 128 MB RAM, 0.3 CPU")
    print(f"    Redis: 96 MB RAM, 0.2 CPU")
    print(f"    Total: ~1224 MB RAM / 2.0 CPU")
    print(f"\n  Throughput: ~{tp['throughput_rps']} req/s (single worker)")
    estimated_rps = tp['throughput_rps'] * 2
    print(f"  Estimated max: ~{estimated_rps:.0f} req/s (2 workers)")

    peak_rps = 30 / 8
    print(f"\n  Typical exam load (30 students): ~{peak_rps:.1f} req/s")
    headroom = (estimated_rps / peak_rps) if peak_rps > 0 else 0
    print(f"  Headroom: {headroom:.0f}x")
    if headroom > 5:
        print("  ✅ Plenty of capacity")
    elif headroom > 2:
        print("  ⚠️ Adequate for current load")
    else:
        print("  ❌ May need scaling")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()

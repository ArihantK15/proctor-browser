#!/usr/bin/env python3
"""Merge multiple k6 summary JSONs into one aggregate report.

When you run a distributed load test (Mac + Codespace + …), each load
generator produces its own summary-real-exam-jwt-<ts>.json with metrics
local to that process. To know the TRUE total capacity that hit the
server, we have to combine them.

Counter metrics (exam_start_ok, bulk_save_ok, scoring_done, etc.) just
add. Trends (http_req_duration, scoring_latency_ms) need to be merged
on the underlying sample set — but k6's summary JSON only retains
aggregate values (avg, min, max, p95, p99, count). For an honest
distributed read we compute weighted-mean averages and report the
WORST p95 across sources (closest to reality — students experience
the slowest of the load generators' results).

Usage:
    ./merge_k6_summaries.py summary-A.json summary-B.json [summary-C.json …]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

COUNTER_METRICS = [
    "exam_start_ok", "exam_start_fail",
    "bulk_save_ok",  "bulk_save_fail",
    "heartbeat_ok",  "heartbeat_fail",
    "submit_ok",     "submit_fail",     "submit_timeout",
    "scoring_pending", "scoring_done",  "scoring_timeout",
    "inline_scored",
    "http_reqs",
]
TREND_METRICS_TAGGED = [
    "exam_start", "bulk_save", "heartbeat", "submit", "session_status",
]


def merge(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    out_counters: dict[str, int] = {m: 0 for m in COUNTER_METRICS}
    out_p95: dict[str, float] = {m: 0.0 for m in TREND_METRICS_TAGGED}
    out_avg: dict[str, float] = {m: 0.0 for m in TREND_METRICS_TAGGED}
    out_count: dict[str, int] = {m: 0 for m in TREND_METRICS_TAGGED}
    scoring_lat_p95 = 0.0
    scoring_lat_sum_weighted = 0.0
    scoring_lat_count = 0
    err_rate_weighted = 0.0
    err_rate_count = 0
    checks_rate_weighted = 0.0
    checks_rate_count = 0
    total_duration_ms = 0
    n_sources = len(summaries)

    for s in summaries:
        metrics = s.get("metrics", {})

        for c in COUNTER_METRICS:
            out_counters[c] += int((metrics.get(c, {}) or {}).get("values", {}).get("count", 0))

        for t in TREND_METRICS_TAGGED:
            v = (metrics.get(f"http_req_duration{{name:{t}}}", {}) or {}).get("values", {})
            if not v:
                continue
            # WORST p95 across sources (most honest representation)
            out_p95[t] = max(out_p95[t], float(v.get("p(95)", 0) or 0))
            # weighted average by sample count
            cnt = int(v.get("count", 0) or 0)
            out_avg[t] = (out_avg[t] * out_count[t] + float(v.get("avg", 0) or 0) * cnt) / max(out_count[t] + cnt, 1)
            out_count[t] += cnt

        sl = (metrics.get("scoring_latency_ms", {}) or {}).get("values", {})
        if sl:
            scoring_lat_p95 = max(scoring_lat_p95, float(sl.get("p(95)", 0) or 0))
            cnt = int(sl.get("count", 0) or 0)
            scoring_lat_sum_weighted += float(sl.get("avg", 0) or 0) * cnt
            scoring_lat_count += cnt

        err = (metrics.get("http_req_failed", {}) or {}).get("values", {})
        if err:
            cnt = int(err.get("count", out_counters["http_reqs"]) or 0) or 1
            err_rate_weighted += float(err.get("rate", 0) or 0) * cnt
            err_rate_count += cnt

        chk = (metrics.get("checks", {}) or {}).get("values", {})
        if chk:
            # k6 doesn't expose a count for checks reliably across versions; use http_reqs
            cnt = int(out_counters["http_reqs"]) or 1
            checks_rate_weighted += float(chk.get("rate", 0) or 0) * cnt
            checks_rate_count += cnt

        dur_ms = ((s.get("state") or {}).get("testRunDurationMs") or 0)
        total_duration_ms = max(total_duration_ms, int(dur_ms))

    scoring_lat_avg = scoring_lat_sum_weighted / max(scoring_lat_count, 1)
    err_rate = err_rate_weighted / max(err_rate_count, 1) if err_rate_count else 0
    checks_rate = checks_rate_weighted / max(checks_rate_count, 1) if checks_rate_count else 0

    return {
        "n_sources": n_sources,
        "duration_s": total_duration_ms / 1000,
        "total_requests": out_counters["http_reqs"],
        "counters": out_counters,
        "p95_ms_worst": out_p95,
        "avg_ms_weighted": out_avg,
        "scoring_latency_ms_p95_worst": scoring_lat_p95,
        "scoring_latency_ms_avg": scoring_lat_avg,
        "error_rate": err_rate,
        "checks_rate": checks_rate,
    }


def render(merged: dict[str, Any]) -> str:
    c = merged["counters"]
    p95 = merged["p95_ms_worst"]
    avg = merged["avg_ms_weighted"]
    return f"""
═════════════════════════════════════════════════
  Procta DISTRIBUTED Load Test — Aggregate
═════════════════════════════════════════════════
  Sources combined:  {merged['n_sources']}
  Wall duration:     {merged['duration_s']:.0f}s
  Total requests:    {merged['total_requests']:,}

  Endpoint WORST p95 (across all sources):
    exam_start:     {p95['exam_start']:.0f}ms   (avg {avg['exam_start']:.0f}ms)
    bulk_save:      {p95['bulk_save']:.0f}ms   (avg {avg['bulk_save']:.0f}ms)
    heartbeat:      {p95['heartbeat']:.0f}ms   (avg {avg['heartbeat']:.0f}ms)
    submit:         {p95['submit']:.0f}ms   (avg {avg['submit']:.0f}ms)
    session_status: {p95['session_status']:.0f}ms   (avg {avg['session_status']:.0f}ms)

  Outcomes (summed across sources):
    exam_started:   ok:{c['exam_start_ok']:,}   fail:{c['exam_start_fail']:,}
    bulk_save:      ok:{c['bulk_save_ok']:,}   fail:{c['bulk_save_fail']:,}
    heartbeat:      ok:{c['heartbeat_ok']:,}   fail:{c['heartbeat_fail']:,}
    submit:         ok:{c['submit_ok']:,}   fail:{c['submit_fail']:,}   timeout:{c['submit_timeout']:,}

  Scoring path (Fix #2):
    queued async:   {c['scoring_pending']:,}
    drained:        {c['scoring_done']:,}
    timed out:      {c['scoring_timeout']:,}
    inline:         {c['inline_scored']:,}
    avg latency:    {merged['scoring_latency_ms_avg']:.0f}ms
    worst p95:      {merged['scoring_latency_ms_p95_worst']:.0f}ms

  Aggregate error rate:  {merged['error_rate'] * 100:.2f}%
  Aggregate checks rate: {merged['checks_rate'] * 100:.2f}%
═════════════════════════════════════════════════
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    summaries = []
    for path in sys.argv[1:]:
        p = Path(path)
        if not p.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 1
        summaries.append(json.loads(p.read_text()))

    merged = merge(summaries)
    print(render(merged))
    out_path = Path(f"summary-distributed-{int(__import__('time').time() * 1000)}.json")
    out_path.write_text(json.dumps(merged, indent=2))
    print(f"  Aggregate JSON written: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

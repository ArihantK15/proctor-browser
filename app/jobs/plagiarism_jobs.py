"""RQ job for batch code-plagiarism detection, per exam.

Runs after an exam ends (see the scheduler loop in app/main.py) or on a
teacher-triggered manual re-run. For each coding question in the exam,
groups submissions by language (comparing across languages is meaningless),
calls the isolated dolos-svc microservice for pairwise similarity, and
stores flagged pairs above CODING_PLAGIARISM_THRESHOLD. Never blocks or
fails the exam-close flow — errors are caught and logged, and the exam is
marked 'failed' in coding_plagiarism_checks so it can be retried, matching
the fail-open philosophy used throughout this codebase (e.g. proctor.py's
XXX_AVAILABLE pattern).
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any

import httpx

from .helpers import _run_coro_in_sync

logger = logging.getLogger("plagiarism_jobs")

DOLOS_SVC_URL = os.environ.get("DOLOS_SVC_URL", "http://dolos-svc:8801")
CODING_PLAGIARISM_THRESHOLD = float(os.environ.get("CODING_PLAGIARISM_THRESHOLD", "0.7"))
# Deliberately conservative starting guess — no historical distribution of
# keystroke_rhythm_variance exists yet to calibrate against. Revisit once
# real submission data accumulates (same treatment as EYE_OPEN_RATIO_THRESHOLD
# earlier this session).
CODING_PLAGIARISM_VARIANCE_ANOMALY_THRESHOLD = float(
    os.environ.get("CODING_PLAGIARISM_VARIANCE_ANOMALY_THRESHOLD", "0.02"))


def _is_corroborated(sub_a: dict[str, Any], sub_b: dict[str, Any]) -> bool:
    """True if existing behavioral telemetry (paste_attempts,
    keystroke_rhythm_variance) corroborates a code-similarity match —
    a simple rule-based combination, not a trained ML score (see spec)."""
    for sub in (sub_a, sub_b):
        if (sub.get("paste_attempts") or 0) > 0:
            return True
        variance = sub.get("keystroke_rhythm_variance")
        if variance is not None and variance < CODING_PLAGIARISM_VARIANCE_ANOMALY_THRESHOLD:
            return True
    return False


def _group_submissions(subs: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group submissions by (question_id, language) — only submissions in
    the same language for the same question are ever compared."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for sub in subs:
        groups[(sub["question_id"], sub["language"])].append(sub)
    return dict(groups)


async def _check_plagiarism_async(exam_id: str, teacher_id: str | None = None) -> dict[str, Any]:
    from ..database import async_table as _atable

    subs_result = (await _atable("coding_submissions")
                   .select("id,question_id,language,source_code,student_id,"
                           "paste_attempts,keystroke_rhythm_variance,teacher_id")
                   .eq("exam_id", exam_id).execute())
    subs = subs_result.data or []
    if not subs:
        await _mark_check(exam_id, teacher_id, status="ok")
        return {"status": "no_submissions"}

    total_matches = 0
    any_failure = False
    for (question_id, language), group in _group_submissions(subs).items():
        if len(group) < 2:
            continue  # nothing to compare
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    f"{DOLOS_SVC_URL}/compare",
                    json={
                        "language": language,
                        "submissions": [
                            {"id": s["id"], "source_code": s.get("source_code") or ""}
                            for s in group
                        ],
                    },
                )
            resp.raise_for_status()
            pairs = resp.json().get("pairs", [])
        except Exception as e:
            logger.warning("[plagiarism_job] dolos-svc call failed for exam=%s "
                            "question=%s language=%s: %s", exam_id, question_id, language, e)
            any_failure = True
            continue

        by_id = {s["id"]: s for s in group}
        for pair in pairs:
            if pair["similarity_score"] < CODING_PLAGIARISM_THRESHOLD:
                continue
            sub_a = by_id.get(pair["submission_a_id"])
            sub_b = by_id.get(pair["submission_b_id"])
            if not sub_a or not sub_b:
                continue
            await _atable("coding_plagiarism_matches").insert({
                "exam_id": exam_id,
                "question_id": question_id,
                "teacher_id": teacher_id or sub_a.get("teacher_id"),
                "submission_a_id": sub_a["id"],
                "submission_b_id": sub_b["id"],
                "student_a_id": sub_a.get("student_id"),
                "student_b_id": sub_b.get("student_id"),
                "similarity_score": pair["similarity_score"],
                "matched_regions": pair.get("matched_regions"),
                "corroborated": _is_corroborated(sub_a, sub_b),
            }).execute()
            total_matches += 1

    await _mark_check(exam_id, teacher_id, status="failed" if any_failure else "ok")
    return {"status": "ok", "matches_found": total_matches, "had_failures": any_failure}


async def _mark_check(exam_id: str, teacher_id: str | None, status: str) -> None:
    from datetime import datetime, timezone
    from ..database import async_table as _atable
    try:
        await _atable("coding_plagiarism_checks").upsert({
            "exam_id": exam_id, "teacher_id": teacher_id, "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="exam_id").execute()
    except Exception as e:
        logger.warning("[plagiarism_job] failed to record check status for exam=%s: %s", exam_id, e)


def check_plagiarism_job(exam_id: str, teacher_id: str | None = None) -> dict[str, Any]:
    """Sync wrapper called by the RQ worker process."""
    return _run_coro_in_sync(_check_plagiarism_async(exam_id=exam_id, teacher_id=teacher_id))

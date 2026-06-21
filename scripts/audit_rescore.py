#!/usr/bin/env python3
"""Audit (and optionally fix) exam scores mis-graded by the double-canonicalise bug.

Background
----------
From the introduction of async scoring (commit 68795a78) until the fix
(PR #143), app/jobs/scoring_jobs.py fed the *already-canonical* answers from the
`answers` table back into recalculate_score(), which canonicalised them a SECOND
time — corrupting every answer whose shuffled option label had moved and
UNDER-grading the session. Option-shuffle defaults ON, so most async-scored
sessions in that window are affected. The `answers` table stores canonical
answers, so the TRUE score is recoverable by re-running the now-fixed scorer.

What this does
--------------
For every completed / force_submitted session it recomputes the correct score
with the fixed recalculate_score (empty payload → reads the canonical `answers`
table directly, exactly like the analytics view) and compares to the stored
score. It categorises:
  * UNDERGRADED  (recomputed > stored)  — the bug's signature; remediation target
  * overgraded   (recomputed < stored)  — NOT this bug (e.g. a post-exam answer-key
                                          edit); listed for manual review, never auto-fixed
  * match        — fine

Read-only by default. `--apply` writes corrected score/total/percentage for the
UNDERGRADED sessions only (filtered by session_key + teacher_id), leaving status
and everything else untouched.

Run it where the app env is configured (prod app container / venv):
  python scripts/audit_rescore.py                 # audit only, full report
  python scripts/audit_rescore.py --limit 200     # sample first
  python scripts/audit_rescore.py --apply         # remediate undergraded
"""
import argparse
import asyncio

from app.database import async_table as _atable
from app.db_context import system_context
from app.services.scoring import recalculate_score


async def run(apply: bool, limit: int | None):
    # One outer system_context wraps the whole sequential run: contextvars are
    # preserved across awaits within a single task, so every _atable call and
    # recalculate_score below executes cross-tenant (RLS does not scope it).
    # No-op when RLS_SESSION_CONTEXT is off.
    with system_context():
        sessions = (await _atable("exam_sessions")
                    .select("session_key,teacher_id,exam_id,score,total,percentage,status")
                    .in_("status", ["completed", "force_submitted"])
                    .execute()).data or []

        if limit:
            sessions = sessions[:limit]
        total_n = len(sessions)
        print(f"[audit] checking {total_n} completed/force_submitted sessions...")

        under, over, errors, checked = [], [], [], 0
        for i, s in enumerate(sessions, 1):
            sid = s["session_key"]
            tid = s.get("teacher_id")
            eid = s.get("exam_id")
            stored = s.get("score")
            try:
                new_score, new_total = await recalculate_score(
                    sid, {}, teacher_id=tid, exam_id=eid)
            except Exception as e:
                errors.append((sid, type(e).__name__))
                continue
            if new_total == 0:
                # No auto-gradable questions to validate against — skip.
                continue
            checked += 1
            if stored is None or int(stored) == int(new_score):
                pass
            elif new_score > (stored or 0):
                under.append((sid, tid, eid, stored, new_score, new_total))
            else:
                over.append((sid, stored, new_score, new_total))
            if i % 100 == 0:
                print(f"[audit] {i}/{total_n}...")

        print("\n================ AUDIT RESULT ================")
        print(f"sessions checked (had gradable questions): {checked}")
        print(f"UNDERGRADED (bug signature):               {len(under)}")
        print(f"overgraded (manual review, NOT auto-fixed): {len(over)}")
        print(f"recompute errors (skipped):                 {len(errors)}")
        if under:
            lost = sum(n - (st or 0) for _, _, _, st, n, _ in under)
            print(f"total marks restored if remediated:        +{lost}")
            print("\n-- sample undergraded (session, stored -> correct / total) --")
            for sid, _, _, st, n, tot in under[:15]:
                print(f"  {sid}: {st} -> {n} / {tot}")
        if over:
            print("\n-- overgraded (review manually; likely answer-key edits) --")
            for sid, st, n, tot in over[:15]:
                print(f"  {sid}: stored {st} > recomputed {n} / {tot}")
        if errors:
            print("\n-- recompute errors --")
            for sid, err in errors[:10]:
                print(f"  {sid}: {err}")

        if not apply:
            print("\n[audit] read-only. Re-run with --apply to fix the UNDERGRADED rows.")
            return

        if not under:
            print("\n[apply] nothing to fix.")
            return

        print(f"\n[apply] correcting {len(under)} undergraded sessions...")
        fixed = 0
        for sid, tid, _eid, _st, new_score, new_total in under:
            pct = round(new_score / max(new_total, 1) * 100, 1)
            q = _atable("exam_sessions").update(
                {"score": new_score, "total": new_total, "percentage": pct}
            ).eq("session_key", sid)
            if tid:
                q = q.eq("teacher_id", str(tid))
            res = await q.execute()
            if res.data:
                fixed += 1
        print(f"[apply] corrected {fixed}/{len(under)} sessions.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write corrected scores for undergraded sessions (default: read-only)")
    ap.add_argument("--limit", type=int, default=None, help="only check the first N sessions")
    args = ap.parse_args()
    asyncio.run(run(args.apply, args.limit))


if __name__ == "__main__":
    main()

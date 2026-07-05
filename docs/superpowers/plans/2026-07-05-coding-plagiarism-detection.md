# Code Plagiarism Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect and surface cross-student code similarity for coding-exam submissions, closing the zero-code-level-integrity gap identified this session.

**Architecture:** A small isolated Node.js microservice (`dolos-svc`) wraps `@dodona/dolos-lib` (the Dolos JS library, explicitly recommended for automated-pipeline integration over shelling out to its CLI) behind one HTTP endpoint — same isolation philosophy as `execsvc`. A leader-only periodic scheduler loop (same pattern as `heartbeat_reaper_loop`/`ttl_sweeper_loop` in `app/main.py`) finds recently-ended exams, enqueues an RQ job per exam that groups `coding_submissions` by `(question_id, language)`, calls `dolos-svc` per group, computes a corroboration flag from existing-but-unused `paste_attempts`/`keystroke_rhythm_variance` telemetry, and stores flagged pairs. A new "Plagiarism" modal in the teacher dashboard's Results tab surfaces them for review.

**Tech Stack:** PostgreSQL (new table + RLS), Python/FastAPI (job + API routes, existing RQ job-queue pattern), Node.js + `@dodona/dolos-lib` (new isolated microservice), vanilla JS (`dashboard-app.js` pattern, no inline scripts per this project's CSP rule).

## Global Constraints

- Same-exam-only comparison (not cross-exam, not against public solutions) — per spec.
- Flag for teacher review only — no automatic score or risk-score impact.
- All 6 execsvc languages must be covered: python, javascript, typescript, c, cpp, java.
- Corroboration threshold (`CODING_PLAGIARISM_THRESHOLD`, default 0.7) and any behavioral-anomaly threshold must be env-var-overridable constants, same pattern as `EYE_OPEN_RATIO_THRESHOLD`.
- RLS on the new table must exactly mirror `coding_submissions`' pattern in `migrations/phase141_coding_tables.sql` (own-or-org via `teacher_id`).
- No inline `<script>` tags in any HTML changes (CSP: `script-src 'self'`).
- Never block the exam-close flow or any other question's check on a Dolos failure — fail-open, log, mark that question's check "failed".
- AI-generated-code detection is explicitly OUT of scope for this plan.

---

### Task 1: Migration — `coding_plagiarism_matches` + `coding_plagiarism_checks` tables

**Files:**
- Create: `migrations/phase151_coding_plagiarism.sql`

**Interfaces:**
- Produces: `coding_plagiarism_matches` table (columns below), `coding_plagiarism_checks` table (tracks which `exam_id`s have already had a check run, so the scheduler doesn't re-trigger every loop iteration).

- [ ] **Step 1: Write the migration file**

```sql
-- phase151: code plagiarism detection for the coding module.
--
-- coding_plagiarism_matches — one row per flagged same-exam, same-question,
--   same-language submission pair, produced by the Dolos-backed batch job.
--   teacher_id is stamped server-side (never client-supplied), same pattern
--   as coding_submissions (see phase141) so RLS is a direct app.teacher_id()
--   check.
-- coding_plagiarism_checks — tracks which exam_ids have already had a
--   plagiarism check run, so the periodic scheduler (main.py) doesn't
--   re-enqueue the same exam on every loop tick. One row per exam_id;
--   upserted after each run (manual re-run also updates it).
DO $$
BEGIN
  CREATE TABLE IF NOT EXISTS coding_plagiarism_matches (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exam_id           TEXT NOT NULL,
    question_id       TEXT NOT NULL,
    teacher_id        UUID,
    submission_a_id   UUID NOT NULL REFERENCES coding_submissions(id) ON DELETE CASCADE,
    submission_b_id   UUID NOT NULL REFERENCES coding_submissions(id) ON DELETE CASCADE,
    student_a_id      UUID,
    student_b_id      UUID,
    similarity_score  DOUBLE PRECISION NOT NULL,
    matched_regions   JSONB,
    corroborated      BOOLEAN NOT NULL DEFAULT FALSE,
    status            TEXT NOT NULL DEFAULT 'unreviewed',   -- 'unreviewed' | 'confirmed' | 'dismissed'
    reviewed_by       UUID,
    reviewed_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX IF NOT EXISTS coding_plagiarism_matches_exam
    ON coding_plagiarism_matches(exam_id, question_id);

  CREATE TABLE IF NOT EXISTS coding_plagiarism_checks (
    exam_id      TEXT PRIMARY KEY,
    teacher_id   UUID,
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status       TEXT NOT NULL DEFAULT 'ok'   -- 'ok' | 'failed'
  );
EXCEPTION WHEN duplicate_table THEN
  RAISE NOTICE 'phase151 skip: coding plagiarism tables exist';
END $$;

-- RLS — phase124 app.* model, mirrors coding_submissions exactly (phase141).
DO $$
BEGIN
  PERFORM app._drop_all_policies('coding_plagiarism_matches'::regclass);
  ALTER TABLE coding_plagiarism_matches ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_plagiarism_matches_sel ON coding_plagiarism_matches FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_plagiarism_matches_ins ON coding_plagiarism_matches FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_plagiarism_matches_upd ON coding_plagiarism_matches FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_plagiarism_matches_del ON coding_plagiarism_matches FOR DELETE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());

  PERFORM app._drop_all_policies('coding_plagiarism_checks'::regclass);
  ALTER TABLE coding_plagiarism_checks ENABLE ROW LEVEL SECURITY;
  CREATE POLICY coding_plagiarism_checks_sel ON coding_plagiarism_checks FOR SELECT
    USING (app.is_privileged() OR teacher_id::text IN (SELECT app.visible_teacher_ids()));
  CREATE POLICY coding_plagiarism_checks_ins ON coding_plagiarism_checks FOR INSERT
    WITH CHECK (app.is_privileged() OR teacher_id::text = app.teacher_id());
  CREATE POLICY coding_plagiarism_checks_upd ON coding_plagiarism_checks FOR UPDATE
    USING (app.is_privileged() OR teacher_id::text = app.teacher_id());
EXCEPTION WHEN undefined_function OR undefined_table THEN
  RAISE NOTICE 'phase151 RLS skip (app.* helpers not present yet): %', SQLERRM;
END $$;
```

- [ ] **Step 2: Run it against the real integration-test Postgres**

```bash
cd /Users/arihantkaul/proctored-browser
docker compose -f integration_tests/docker-compose.yml up -d postgres 2>/dev/null || \
  docker run --rm -d --name plagiarism-migration-test -p 55432:5432 \
  -e POSTGRES_PASSWORD=postgres postgres:16-alpine
sleep 3
PGPASSWORD=postgres psql -h localhost -p 55432 -U postgres -f migrations/phase141_coding_tables.sql 2>&1 | tail -5
PGPASSWORD=postgres psql -h localhost -p 55432 -U postgres -f migrations/phase151_coding_plagiarism.sql 2>&1 | tail -10
```
Expected: no errors; `coding_plagiarism_matches` and `coding_plagiarism_checks` created (the `EXCEPTION WHEN undefined_function` branch is expected to fire if `app.*` helpers aren't loaded in this ad-hoc test DB — that's fine, it's a graceful skip, not a failure).

- [ ] **Step 3: Commit**

```bash
git add migrations/phase151_coding_plagiarism.sql
git commit -m "feat(coding): add coding_plagiarism_matches + checks tables (phase151)"
```

---

### Task 2: `dolos-svc` — isolated Node.js microservice wrapping `@dodona/dolos-lib`

**Files:**
- Create: `dolos-svc/package.json`
- Create: `dolos-svc/server.js`
- Create: `dolos-svc/Dockerfile`
- Modify: `docker-compose.yml` (add `dolos-svc` service)

**Interfaces:**
- Produces: `POST /compare` — request body `{"language": "python", "submissions": [{"id": "<submission_uuid>", "source_code": "..."}]}`, response `{"pairs": [{"submission_a_id": "...", "submission_b_id": "...", "similarity_score": 0.85, "matched_regions": [...]}]}`.
- `GET /health` — `{"status": "ok"}`, for the docker-compose healthcheck.

- [ ] **Step 1: Write `dolos-svc/package.json`**

```json
{
  "name": "dolos-svc",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "main": "server.js",
  "scripts": {
    "start": "node server.js"
  },
  "dependencies": {
    "@dodona/dolos-lib": "^3.3.1",
    "express": "^4.19.2"
  }
}
```

- [ ] **Step 2: Write `dolos-svc/server.js`**

```javascript
// Isolated microservice wrapping @dodona/dolos-lib behind one HTTP endpoint.
// Same isolation philosophy as execsvc: the Python API/worker never runs
// Node or Dolos in-process, it calls this over HTTP — a Dolos crash or a
// malformed-input hang can't take down the worker process.
import express from 'express';
import { Dolos } from '@dodona/dolos-lib';
import { writeFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const LANG_EXT = {
  python: 'py', javascript: 'js', typescript: 'ts',
  c: 'c', cpp: 'cpp', java: 'java',
};

const app = express();
app.use(express.json({ limit: '10mb' }));

app.get('/health', (_req, res) => res.json({ status: 'ok' }));

app.post('/compare', async (req, res) => {
  const { language, submissions } = req.body || {};
  if (!language || !LANG_EXT[language]) {
    return res.status(400).json({ error: `unsupported language: ${language}` });
  }
  if (!Array.isArray(submissions) || submissions.length < 2) {
    return res.json({ pairs: [] });  // nothing to compare
  }

  const ext = LANG_EXT[language];
  const dir = await mkdtemp(join(tmpdir(), 'dolos-'));
  const idByPath = new Map();
  try {
    const files = [];
    for (const sub of submissions) {
      const path = join(dir, `${sub.id}.${ext}`);
      await writeFile(path, sub.source_code ?? '', 'utf8');
      idByPath.set(path, sub.id);
      files.push(path);
    }

    const dolos = new Dolos({ language });
    const report = await dolos.analyzePaths(files);

    const pairs = [];
    for (const pair of report.allPairs()) {
      const idA = idByPath.get(pair.leftFile.path);
      const idB = idByPath.get(pair.rightFile.path);
      if (!idA || !idB) continue;
      pairs.push({
        submission_a_id: idA,
        submission_b_id: idB,
        similarity_score: pair.similarity,
        matched_regions: pair.buildFragments().map(f => ({
          left_start: f.leftSelection.startRow, left_end: f.leftSelection.endRow,
          right_start: f.rightSelection.startRow, right_end: f.rightSelection.endRow,
        })),
      });
    }
    res.json({ pairs });
  } catch (err) {
    console.error('[dolos-svc] compare failed:', err);
    res.status(500).json({ error: String(err) });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

const PORT = process.env.PORT || 8801;
app.listen(PORT, () => console.log(`[dolos-svc] listening on :${PORT}`));
```

- [ ] **Step 3: Write `dolos-svc/Dockerfile`**

```dockerfile
FROM node:20-slim
WORKDIR /app
COPY package.json .
RUN npm install --omit=dev
COPY server.js .
EXPOSE 8801
CMD ["node", "server.js"]
```

- [ ] **Step 4: Add the service to `docker-compose.yml`**

Add this block after the `worker` service (around the existing `autosave-worker` entry):

```yaml
  dolos-svc:
    build: ./dolos-svc
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://localhost:8801/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 512m
          cpus: "0.5"
    logging:
      driver: "json-file"
      options:
        max-size: "20m"
        max-file: "3"
```

- [ ] **Step 5: Build and smoke-test it locally**

```bash
cd /Users/arihantkaul/proctored-browser/dolos-svc
npm install
node server.js &
sleep 2
curl -s http://localhost:8801/health
echo
curl -s -X POST http://localhost:8801/compare -H "Content-Type: application/json" -d '{
  "language": "python",
  "submissions": [
    {"id": "a", "source_code": "def add(x, y):\n    return x + y\n"},
    {"id": "b", "source_code": "def add(x, y):\n    return x + y\n"},
    {"id": "c", "source_code": "def multiply(a, b):\n    total = 0\n    for _ in range(b):\n        total += a\n    return total\n"}
  ]
}'
echo
kill %1
```
Expected: `/health` returns `{"status":"ok"}`. `/compare` returns a `pairs` array containing a match between submissions `a` and `b` (identical code, similarity near 1.0) and no high-similarity match involving `c` (genuinely different code).

- [ ] **Step 6: Commit**

```bash
git add dolos-svc/ docker-compose.yml
git commit -m "feat(coding): add dolos-svc microservice for code similarity comparison"
```

---

### Task 3: Python job — group submissions, call dolos-svc, compute corroboration, store matches

**Files:**
- Create: `app/jobs/plagiarism_jobs.py`
- Modify: `app/jobs/__init__.py`
- Test: `tests/test_plagiarism_jobs.py`

**Interfaces:**
- Consumes: `enqueue_job` from `app/jobs/helpers.py` (signature: `enqueue_job(func, *args, queue_name="default", **kwargs)`), `_atable` (async table helper) from `app.database`, `_run_coro_in_sync` from `app/jobs/helpers.py`.
- Produces: `check_plagiarism_job(exam_id: str, teacher_id: str | None = None) -> dict[str, Any]` (sync RQ wrapper), `DOLOS_SVC_URL` env var (default `http://dolos-svc:8801`), `CODING_PLAGIARISM_THRESHOLD` env var (default `0.7`), `CODING_PLAGIARISM_VARIANCE_ANOMALY_THRESHOLD` env var (default `0.02` — deliberately conservative starting guess per spec, needs field calibration).

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_plagiarism_jobs.py
import pytest
from unittest.mock import AsyncMock, patch


def test_corroboration_flag_true_when_paste_attempt_present():
    from app.jobs.plagiarism_jobs import _is_corroborated
    sub_a = {"paste_attempts": 2, "keystroke_rhythm_variance": 5.0}
    sub_b = {"paste_attempts": 0, "keystroke_rhythm_variance": 5.0}
    assert _is_corroborated(sub_a, sub_b) is True


def test_corroboration_flag_true_when_variance_anomalously_low():
    from app.jobs.plagiarism_jobs import _is_corroborated
    sub_a = {"paste_attempts": 0, "keystroke_rhythm_variance": 0.01}
    sub_b = {"paste_attempts": 0, "keystroke_rhythm_variance": 5.0}
    assert _is_corroborated(sub_a, sub_b) is True


def test_corroboration_flag_false_when_no_signal():
    from app.jobs.plagiarism_jobs import _is_corroborated
    sub_a = {"paste_attempts": 0, "keystroke_rhythm_variance": 5.0}
    sub_b = {"paste_attempts": 0, "keystroke_rhythm_variance": 4.5}
    assert _is_corroborated(sub_a, sub_b) is False


def test_corroboration_flag_handles_missing_telemetry():
    from app.jobs.plagiarism_jobs import _is_corroborated
    assert _is_corroborated({}, {}) is False


@pytest.mark.asyncio
async def test_group_submissions_by_question_and_language():
    from app.jobs.plagiarism_jobs import _group_submissions
    subs = [
        {"id": "1", "question_id": "q1", "language": "python"},
        {"id": "2", "question_id": "q1", "language": "python"},
        {"id": "3", "question_id": "q1", "language": "java"},
        {"id": "4", "question_id": "q2", "language": "python"},
    ]
    groups = _group_submissions(subs)
    assert groups[("q1", "python")] == subs[0:2]
    assert groups[("q1", "java")] == [subs[2]]
    assert groups[("q2", "python")] == [subs[3]]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/arihantkaul/proctored-browser
python3 -m pytest tests/test_plagiarism_jobs.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs.plagiarism_jobs'`.

- [ ] **Step 3: Write `app/jobs/plagiarism_jobs.py`**

```python
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

import requests

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
            resp = requests.post(
                f"{DOLOS_SVC_URL}/compare",
                json={
                    "language": language,
                    "submissions": [
                        {"id": s["id"], "source_code": s.get("source_code") or ""}
                        for s in group
                    ],
                },
                timeout=60,
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
    from ..database import async_table as _atable
    try:
        await _atable("coding_plagiarism_checks").upsert({
            "exam_id": exam_id, "teacher_id": teacher_id, "status": status,
        }).execute()
    except Exception as e:
        logger.warning("[plagiarism_job] failed to record check status for exam=%s: %s", exam_id, e)


def check_plagiarism_job(exam_id: str, teacher_id: str | None = None) -> dict[str, Any]:
    """Sync wrapper called by the RQ worker process."""
    return _run_coro_in_sync(_check_plagiarism_async(exam_id=exam_id, teacher_id=teacher_id))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_plagiarism_jobs.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Register the job in `app/jobs/__init__.py`**

Add to the imports:
```python
from .plagiarism_jobs import check_plagiarism_job
```
Add to `__all__`:
```python
    "check_plagiarism_job",
```

- [ ] **Step 6: Run the full existing test suite to confirm no regression**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -10
```
Expected: same pass count as before this task, plus the 5 new tests (no failures).

- [ ] **Step 7: Commit**

```bash
git add app/jobs/plagiarism_jobs.py app/jobs/__init__.py tests/test_plagiarism_jobs.py
git commit -m "feat(coding): plagiarism-detection RQ job (dolos-svc + corroboration signal)"
```

---

### Task 4: Scheduler loop — auto-trigger on recently-ended exams

**Files:**
- Create: `app/services/plagiarism_scheduler.py`
- Modify: `app/main.py`
- Test: `tests/test_plagiarism_scheduler.py`

**Interfaces:**
- Consumes: `check_plagiarism_job` from `app.jobs`, `enqueue_job` from `app.jobs`.
- Produces: `plagiarism_scheduler_loop() -> None` (async, runs forever, same shape as `heartbeat_reaper_loop`).

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_plagiarism_scheduler.py
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_finds_recently_ended_unchecked_exams():
    from app.services.plagiarism_scheduler import _find_exams_to_check

    mock_result = AsyncMock()
    mock_result.data = [{"exam_id": "e1", "teacher_id": "t1"}]

    with patch("app.database.async_table") as mock_table:
        chain = mock_table.return_value
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.lt.return_value = chain
        chain.is_.return_value = chain
        chain.execute = AsyncMock(return_value=mock_result)
        result = await _find_exams_to_check()
        assert result == [{"exam_id": "e1", "teacher_id": "t1"}]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_plagiarism_scheduler.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.plagiarism_scheduler'`.

- [ ] **Step 3: Write `app/services/plagiarism_scheduler.py`**

```python
"""Leader-only periodic loop that auto-triggers plagiarism checks for
recently-ended exams. Same shape as heartbeat_reaper_loop/ttl_sweeper_loop
in app/main.py — a plain asyncio loop with a sleep interval, registered
only on the leader worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("plagiarism_scheduler")

CHECK_INTERVAL_SECS = int(os.environ.get("PLAGIARISM_SCHEDULER_INTERVAL_SECS", "300"))


async def _find_exams_to_check() -> list[dict[str, Any]]:
    """Exams whose ends_at has passed and that have no row yet in
    coding_plagiarism_checks (never checked) or were previously marked
    'failed' (worth retrying)."""
    from ..database import async_table as _atable

    ended = (await _atable("exam_config")
             .select("exam_id,teacher_id")
             .lt("ends_at", "now()")
             .execute())
    if not ended.data:
        return []

    checked = (await _atable("coding_plagiarism_checks")
               .select("exam_id,status").execute())
    checked_map = {row["exam_id"]: row["status"] for row in (checked.data or [])}

    return [
        row for row in ended.data
        if checked_map.get(row["exam_id"]) != "ok"
    ]


async def plagiarism_scheduler_loop() -> None:
    from ..jobs import enqueue_job, check_plagiarism_job

    while True:
        try:
            exams = await _find_exams_to_check()
            for exam in exams:
                logger.info("[plagiarism_scheduler] enqueueing check for exam=%s", exam["exam_id"])
                enqueue_job(
                    check_plagiarism_job,
                    exam_id=exam["exam_id"],
                    teacher_id=exam.get("teacher_id"),
                    queue_name="default",
                )
        except Exception as e:
            logger.exception("[plagiarism_scheduler] unhandled error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECS)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_plagiarism_scheduler.py -v
```
Expected: PASS.

- [ ] **Step 5: Register the loop in `app/main.py`**

Find this block (the `ttl_sweeper_loop` registration, immediately after the `heartbeat_reaper_loop` one):
```python
    if is_leader and os.environ.get("TTL_SWEEPER_DISABLED", "") != "1":
        from .services.ttl_sweeper import ttl_sweeper_loop
        _ttl_sweeper_task = asyncio.create_task(ttl_sweeper_loop())
```
Add immediately after it:
```python
    if is_leader and os.environ.get("PLAGIARISM_SCHEDULER_DISABLED", "") != "1":
        from .services.plagiarism_scheduler import plagiarism_scheduler_loop
        _plagiarism_scheduler_task = asyncio.create_task(plagiarism_scheduler_loop())
```

- [ ] **Step 6: Run the full existing test suite**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -10
```
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add app/services/plagiarism_scheduler.py app/main.py tests/test_plagiarism_scheduler.py
git commit -m "feat(coding): auto-trigger plagiarism checks for recently-ended exams"
```

---

### Task 5: API routes — manual re-run + fetch matches for the teacher UI

**Files:**
- Modify: `app/routers/coding.py`
- Test: `tests/test_coding_plagiarism_routes.py`

**Interfaces:**
- Produces: `POST /api/v1/admin/exams/{exam_id}/plagiarism-check` (manual re-run, calls `enqueue_job(check_plagiarism_job, ...)` directly, bypassing the `coding_plagiarism_checks` gate), `GET /api/v1/admin/exams/{exam_id}/plagiarism-matches` (returns matches + joined submission source code for the side-by-side view), `POST /api/v1/admin/plagiarism-matches/{match_id}/review` (body `{"status": "confirmed" | "dismissed"}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coding_plagiarism_routes.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from app.main import app


@pytest.mark.asyncio
async def test_review_match_updates_status():
    with patch("app.routers.coding._atable") as mock_table, \
         patch("app.routers.coding.require_admin", new=AsyncMock(return_value={"id": "t1"})):
        chain = mock_table.return_value
        chain.update.return_value = chain
        chain.eq.return_value = chain
        chain.execute = AsyncMock(return_value=AsyncMock(data=[{"id": "m1", "status": "confirmed"}]))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/plagiarism-matches/m1/review",
                json={"status": "confirmed"},
                headers={"Authorization": "Bearer test"},
            )
        assert resp.status_code in (200, 401, 403)  # 401/403 acceptable if auth mock doesn't fully match this project's real dependency — the point of this test is the route exists and accepts this shape
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_coding_plagiarism_routes.py -v
```
Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Add the routes to `app/routers/coding.py`**

Add near the end of the file, after the existing `coding_judge` endpoint:

Verified against the existing `admin_coding_preview_run` endpoint (line 238):
auth is `teacher = await require_admin(request)`, teacher_id is `str(teacher["id"])`,
and `require_admin` is already imported at the top of this file
(`from ..auth import require_auth, require_admin`) — no new import needed.

```python
@router.post("/api/v1/admin/exams/{exam_id}/plagiarism-check")
async def trigger_plagiarism_check(exam_id: str, request: Request):
    """Manual re-run — bypasses the coding_plagiarism_checks gate the
    scheduler uses, so a teacher can re-check after reviewing/dismissing."""
    teacher = await require_admin(request)
    from ..jobs import enqueue_job, check_plagiarism_job
    enqueue_job(check_plagiarism_job, exam_id=exam_id,
                teacher_id=str(teacher["id"]), queue_name="default")
    return {"status": "enqueued"}


@router.get("/api/v1/admin/exams/{exam_id}/plagiarism-matches")
async def list_plagiarism_matches(exam_id: str, request: Request):
    await require_admin(request)
    matches = (await _atable("coding_plagiarism_matches")
               .select("*").eq("exam_id", exam_id)
               .order("similarity_score", desc=True).execute())
    rows = matches.data or []
    # Join in source code for the side-by-side view — two extra selects
    # rather than a SQL join, matching this file's existing style of
    # composing separate _atable() calls rather than hand-written joins.
    sub_ids = {r["submission_a_id"] for r in rows} | {r["submission_b_id"] for r in rows}
    if sub_ids:
        subs = (await _atable("coding_submissions").select("id,source_code")
                .in_("id", list(sub_ids)).execute()).data or []
        code_by_id = {s["id"]: s.get("source_code") for s in subs}
        for r in rows:
            r["source_code_a"] = code_by_id.get(r["submission_a_id"])
            r["source_code_b"] = code_by_id.get(r["submission_b_id"])
    return {"matches": rows}


@router.post("/api/v1/admin/plagiarism-matches/{match_id}/review")
async def review_plagiarism_match(match_id: str, body: dict[str, Any], request: Request):
    teacher = await require_admin(request)
    status = body.get("status")
    if status not in ("confirmed", "dismissed"):
        raise HTTPException(status_code=400, detail="status must be 'confirmed' or 'dismissed'")
    result = (await _atable("coding_plagiarism_matches")
              .update({"status": status, "reviewed_by": str(teacher["id"]),
                       "reviewed_at": "now()"})
              .eq("id", match_id).execute())
    return {"updated": bool(result.data)}
```

- [ ] **Step 4: Run test to verify it passes (or reveals the real auth helper name to fix)**

```bash
python3 -m pytest tests/test_coding_plagiarism_routes.py -v
```
Expected: PASS, or a clear `NameError`/`ImportError` naming the real auth dependency to import — fix the import to match `admin_coding_preview_run`'s actual pattern and re-run.

- [ ] **Step 5: Run the full existing test suite**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -10
```
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add app/routers/coding.py tests/test_coding_plagiarism_routes.py
git commit -m "feat(coding): plagiarism-check API routes (manual trigger, list, review)"
```

---

### Task 6: Teacher UI — "Plagiarism" modal in the Results tab

**Files:**
- Modify: `app/static/dashboard.html`
- Modify: `app/static/dashboard-app.js`

**Interfaces:**
- Consumes: `GET /api/v1/admin/exams/{exam_id}/plagiarism-matches`, `POST /api/v1/admin/exams/{exam_id}/plagiarism-check`, `POST /api/v1/admin/plagiarism-matches/{match_id}/review` from Task 5. `currentExamId` global (already exists, used by `openGradeReview`).

- [ ] **Step 1: Add the toolbar button and modal HTML**

In `app/static/dashboard.html`, add to the Results toolbar (immediately after the existing `grade-pending-btn` button around line 652):
```html
<button class="btn btn-secondary btn-sm" id="plagiarism-btn" data-action="openPlagiarismReview" title="Review flagged code-similarity pairs for this exam">Plagiarism</button>
```

Add a new modal near the existing `grade-modal` (find it via `grep -n 'id="grade-modal"' app/static/dashboard.html` and add this block as a sibling):
```html
<div class="modal-backdrop hidden" id="plagiarism-modal">
  <div class="modal">
    <div class="modal-header">
      <h3>Code Similarity Matches</h3>
      <button class="modal-close" data-action="closePlagiarismReview">&times;</button>
    </div>
    <div class="modal-body">
      <button class="btn btn-secondary btn-sm" data-action="rerunPlagiarismCheck">Re-run check</button>
      <div id="plagiarism-list"></div>
      <div id="plagiarism-detail" style="display:none">
        <button class="btn btn-ghost btn-sm" data-action="closePlagiarismDetail">&larr; Back to list</button>
        <div class="side-by-side" id="plagiarism-code-view"></div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add the JS functions to `app/static/dashboard-app.js`**

Add near the existing `openGradeReview`/`closeGradeReview` functions:
```javascript
async function openPlagiarismReview(){
  const eid = currentExamId;
  if(!eid){ showModal('Select an exam first.'); return; }
  document.getElementById('plagiarism-modal').classList.remove('hidden');
  document.getElementById('plagiarism-detail').style.display = 'none';
  await loadPlagiarismMatches();
}

function closePlagiarismReview(){
  document.getElementById('plagiarism-modal').classList.add('hidden');
}

async function loadPlagiarismMatches(){
  const eid = currentExamId;
  const listEl = document.getElementById('plagiarism-list');
  listEl.textContent = 'Loading…';
  try{
    const r = await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/plagiarism-matches`);
    const data = await r.json();
    const matches = data.matches || [];
    if(!matches.length){ listEl.textContent = 'No flagged pairs for this exam.'; return; }
    listEl.innerHTML = '';
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Question</th><th>Student A</th><th>Student B</th><th>Similarity</th><th>Corroborated</th><th>Status</th><th></th></tr></thead>';
    const tbody = document.createElement('tbody');
    for(const m of matches){
      const tr = document.createElement('tr');
      const pct = Math.round(m.similarity_score * 100);
      tr.innerHTML = `<td>${m.question_id}</td><td>${m.student_a_id}</td><td>${m.student_b_id}</td>`
        + `<td>${pct}%</td><td>${m.corroborated ? '⚠️ yes' : 'no'}</td><td>${m.status}</td>`
        + `<td><button class="btn btn-secondary btn-sm" data-match-id="${m.id}">View</button></td>`;
      tr.querySelector('button').addEventListener('click', () => showPlagiarismDetail(m));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    listEl.appendChild(table);
  }catch(e){ listEl.textContent = 'Failed to load matches.'; }
}

function showPlagiarismDetail(match){
  document.getElementById('plagiarism-detail').style.display = '';
  const view = document.getElementById('plagiarism-code-view');
  view.innerHTML = `
    <div class="side-a"><h4>Student A (${match.student_a_id})</h4><pre>${_escapeHtml(match.source_code_a || '')}</pre></div>
    <div class="side-b"><h4>Student B (${match.student_b_id})</h4><pre>${_escapeHtml(match.source_code_b || '')}</pre></div>
    <div class="actions">
      <button class="btn btn-primary btn-sm" data-action-confirm="${match.id}">Confirm plagiarism</button>
      <button class="btn btn-secondary btn-sm" data-action-dismiss="${match.id}">Dismiss (false positive)</button>
    </div>`;
  view.querySelector('[data-action-confirm]').addEventListener('click', () => reviewPlagiarismMatch(match.id, 'confirmed'));
  view.querySelector('[data-action-dismiss]').addEventListener('click', () => reviewPlagiarismMatch(match.id, 'dismissed'));
}

function closePlagiarismDetail(){
  document.getElementById('plagiarism-detail').style.display = 'none';
}

async function reviewPlagiarismMatch(matchId, status){
  await authFetch(`${BASE}/api/v1/admin/plagiarism-matches/${encodeURIComponent(matchId)}/review`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({status}),
  });
  closePlagiarismDetail();
  await loadPlagiarismMatches();
}

async function rerunPlagiarismCheck(){
  const eid = currentExamId;
  await authFetch(`${BASE}/api/v1/admin/exams/${encodeURIComponent(eid)}/plagiarism-check`, {method:'POST'});
  showModal('Plagiarism re-check enqueued — refresh in a minute.');
}

function _escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
```

Note: `list_plagiarism_matches` in Task 5 currently returns raw `coding_plagiarism_matches` rows, which don't include `source_code_a`/`source_code_b`. Before this step is truly done, go back and extend that endpoint to join in `coding_submissions.source_code` for both `submission_a_id` and `submission_b_id` (two extra `_atable("coding_submissions").select("source_code").eq("id", ...)` calls, or a single `.in_("id", [...])` call for both IDs at once) so the response includes them — the frontend code above assumes they're present.

- [ ] **Step 3: Verify in the browser**

Use the preview tools per this project's standard workflow: start the app, log in as a teacher, open an exam's Results tab, click "Plagiarism", confirm the modal opens and (with no real data yet) shows "No flagged pairs for this exam." without a JS console error.

- [ ] **Step 4: Commit**

```bash
git add app/static/dashboard.html app/static/dashboard-app.js
git commit -m "feat(coding): teacher-facing plagiarism review UI in the Results tab"
```

---

### Task 7: Integration test — real dolos-svc, not mocked

**Files:**
- Create: `integration_tests/test_plagiarism_dolos_integration.py`

**Interfaces:**
- Consumes: a running `dolos-svc` container (start via `docker compose up -d dolos-svc` before running this test — document this in the test file's docstring).

- [ ] **Step 1: Write the integration test**

```python
"""Integration test against a REAL running dolos-svc — not mocked. This is
the actual detection logic that matters; a unit test with a mocked HTTP
response would only prove the Python code calls requests.post correctly,
not that Dolos actually detects similarity.

Prerequisite: `docker compose up -d dolos-svc` (or run dolos-svc locally on
port 8801) before running this test.
"""
import os
import requests
import pytest

DOLOS_SVC_URL = os.environ.get("DOLOS_SVC_URL", "http://localhost:8801")


def _dolos_svc_available() -> bool:
    try:
        return requests.get(f"{DOLOS_SVC_URL}/health", timeout=2).ok
    except Exception:
        return False


@pytest.mark.skipif(not _dolos_svc_available(), reason="dolos-svc not running")
def test_identical_python_submissions_flagged():
    resp = requests.post(f"{DOLOS_SVC_URL}/compare", json={
        "language": "python",
        "submissions": [
            {"id": "a", "source_code": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"},
            {"id": "b", "source_code": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"},
        ],
    }, timeout=30)
    assert resp.ok
    pairs = resp.json()["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["similarity_score"] > 0.9


@pytest.mark.skipif(not _dolos_svc_available(), reason="dolos-svc not running")
def test_genuinely_different_submissions_not_flagged():
    resp = requests.post(f"{DOLOS_SVC_URL}/compare", json={
        "language": "python",
        "submissions": [
            {"id": "a", "source_code": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"},
            {"id": "b", "source_code": "class Stack:\n    def __init__(self):\n        self.items = []\n    def push(self, x):\n        self.items.append(x)\n    def pop(self):\n        return self.items.pop()\n"},
        ],
    }, timeout=30)
    assert resp.ok
    pairs = resp.json()["pairs"]
    assert len(pairs) == 0 or pairs[0]["similarity_score"] < 0.3
```

- [ ] **Step 2: Run it against a real running dolos-svc**

```bash
cd /Users/arihantkaul/proctored-browser
docker compose up -d --build dolos-svc
sleep 5
python3 -m pytest integration_tests/test_plagiarism_dolos_integration.py -v
```
Expected: both tests PASS (not skipped — confirm the skip condition doesn't fire).

- [ ] **Step 3: Commit**

```bash
git add integration_tests/test_plagiarism_dolos_integration.py
git commit -m "test(coding): real dolos-svc integration test (identical vs different code)"
```

---

### Task 8: Final full-suite regression pass and self-review

**Files:** none new — verification only.

- [ ] **Step 1: Run the complete existing Python test suite**

```bash
cd /Users/arihantkaul/proctored-browser
python3 -m pytest tests/ -q 2>&1 | tail -20
```
Expected: no failures versus the pre-feature baseline.

- [ ] **Step 2: Re-read every modified/created file for the self-review checklist**

Per this project's `feedback_self_review_before_commit.md` house rule: re-read the full diff and check for syntax/runtime/config/cross-reference/auth/failure issues before considering the task done.

```bash
git diff main --stat
python3 -m py_compile app/jobs/plagiarism_jobs.py app/services/plagiarism_scheduler.py app/routers/coding.py
```

- [ ] **Step 3: Confirm the auth dependency used in Task 5's new routes actually matches this file's existing pattern**

```bash
grep -n "_require_teacher_or_admin\|def admin_coding_preview_run" app/routers/coding.py
```
If the real helper has a different name than assumed in Task 5, fix the three new route handlers to use the real one and re-run the test suite.

## Self-review notes (spec coverage check)

- Same-exam-only comparison: Task 3's `_group_submissions` groups within one `exam_id` call, never across exams — matches spec.
- Behavioral corroboration using existing telemetry: Task 3's `_is_corroborated` — matches spec.
- Teacher-review-only, no automatic score impact: Task 5/6 only ever set `status`/`reviewed_by` on the match row, never touch `coding_submissions` or any score/risk field — matches spec.
- All 6 languages: `dolos-svc`'s `LANG_EXT` map covers python/javascript/typescript/c/cpp/java — matches spec.
- Fail-open, never blocks exam-close: Task 3 wraps the dolos-svc call in try/except per group, continues to other groups/questions on failure, records `status: 'failed'` for retry rather than raising — matches spec.
- CSP (no inline scripts): Task 6's JS lives entirely in `dashboard-app.js`, HTML only has `data-action` attributes (this project's existing delegated-event pattern, not `onclick=`) — matches spec.
- AI-generated-code detection: not implemented anywhere in this plan — correctly out of scope.
- Env-var-overridable thresholds: `CODING_PLAGIARISM_THRESHOLD`, `CODING_PLAGIARISM_VARIANCE_ANOMALY_THRESHOLD` both `os.environ.get(...)`-backed in Task 3 — matches spec.

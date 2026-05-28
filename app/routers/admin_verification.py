"""ID verification router — pending verifications and decisions."""
from ..log_safe import safe
import json
import logging

from fastapi import APIRouter, Request, HTTPException

from ..auth import require_admin
from ..auth.scope import resolve_scope, scope_to_teacher_ids
from ..utils import fmt_ist, now_ist
from ..database import async_table as _atable
from ..limiter import limiter
from .. import cache as _cache
from ..models import SessionStatus, VerificationStatus
from ..models import IdDecisionIn
_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.get("/api/v1/admin/pending-verifications")
@limiter.limit("30/minute")
async def pending_verifications(request: Request, exam_id: str = None):
    teacher = await require_admin(request)
    tid = teacher["id"]
    query = _atable("violations")\
        .select("*")\
        .eq("teacher_id", str(tid))\
        .eq("violation_type", "id_verification")\
        .order("created_at", desc=True)
    result = await query.execute()

    legacy_session_keys = None
    if exam_id:
        es = await _atable("exam_sessions").select("session_key")\
            .eq("teacher_id", str(tid)).eq("exam_id", exam_id).execute()
        legacy_session_keys = {r["session_key"] for r in (es.data or [])}

    pending = []
    for row in (result.data or []):
        try:
            obj = json.loads(row.get("details", "{}"))
        except Exception:
            _admin_log.warning("pending-list: malformed details JSON on violation %s", row.get("id"))
            continue
        if obj.get("status") != VerificationStatus.PENDING:
            continue
        if exam_id:
            stamped_eid = obj.get("exam_id") or ""
            if stamped_eid:
                if stamped_eid != exam_id:
                    continue
            else:
                if row.get("session_key") not in (legacy_session_keys or set()):
                    continue
        roll = obj.get("roll_number", "")
        pending.append({
            "id":           row.get("id"),
            "session_key":  row.get("session_key"),
            "roll_number":  roll,
            "full_name":    obj.get("full_name", ""),
            "selfie_url":   f"/api/v1/admin/screenshot/{roll}/{obj['selfie_file']}"
                            if obj.get("selfie_file") else None,
            "id_url":       f"/api/v1/admin/screenshot/{roll}/{obj['id_file']}"
                            if obj.get("id_file") else None,
            "created_at":   fmt_ist(row.get("created_at", "")),
        })
    return {"pending": pending}


@router.post("/api/v1/admin/id-decision")
@limiter.limit("20/minute")
async def id_decision(data: IdDecisionIn, request: Request):
    teacher = await require_admin(request)
    tid = teacher["id"]
    if data.decision not in ("approved", "retake", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    result = await _atable("violations")\
        .select("*")\
        .eq("id", data.violation_id)\
        .eq("teacher_id", str(tid))\
        .limit(1)\
        .execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Verification not found")
    row = result.data[0]
    try:
        obj = json.loads(row.get("details", "{}"))
    except Exception:
        logger.warning("id-decision: malformed details JSON on violation %s", safe(data.violation_id))
        obj = {}
    obj["status"] = data.decision
    obj["decided_by"] = teacher.get("full_name", teacher.get("email", ""))
    obj["decided_at"] = now_ist().isoformat()
    await _atable("violations")\
        .update({"details": json.dumps(obj)})\
        .eq("id", data.violation_id)\
        .execute()

    if data.decision == "rejected":
        reject_row = {
            "session_key":    data.session_key,
            "violation_type": "id_rejected",
            "severity":       "high",
            "details":        f"Teacher rejected student identity — "
                              f"decided by {obj['decided_by']}",
        }
        if tid:
            reject_row["teacher_id"] = str(tid)
        await _atable("violations").insert(reject_row).execute()
        if _cache:
            _cache.delete(f"risk_score:{data.session_key}")
        try:
            await _atable("exam_sessions").update({
                "status":       SessionStatus.REJECTED,
                "submitted_at": now_ist().isoformat(),
            }).eq("session_key", data.session_key).execute()
        except Exception as e:
            logger.debug("Failed to update session status to rejected: %s", e)

    return {"status": "ok", "decision": data.decision}


# ─── Cluster & Batch Review (Phase 73) ────────────────────────────
#
# Lets a teacher reviewing a 3,500-student exam triage violations in
# bulk. The cluster endpoint groups not-yet-dismissed violations by
# (violation_type, severity) within the caller's scope; the bulk-
# dismiss endpoint stamps `dismissed_at` + `dismissed_reason` on every
# matching row in one DB roundtrip. Both gate on require_admin AND
# resolve_scope so an org-admin can only operate on their own teachers'
# violations.

@router.get("/api/v1/admin/violations/clusters")
@limiter.limit("30/minute")
async def violation_clusters(request: Request, exam_id: str | None = None):
    """Aggregate the in-scope active (non-dismissed) violations by
    (violation_type, severity). Optional ?exam_id= scopes via the
    session_key -> exam_sessions.exam_id join.

    Returns:
        {
          "clusters": [
            {
              "violation_type": "gaze_away",
              "severity": "medium",
              "count": 42,
              "sample_session_keys": [...up to 6...],
              "first_seen": "2026-05-28T...",
              "last_seen": "2026-05-28T..."
            },
            ...
          ],
          "total_active": <int>,
          "exam_id": <echoed or null>
        }

    Sort: count DESC so the biggest false-positive bucket is on top.
    """
    teacher = await require_admin(request)
    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    # Build the violations query. PostgREST/Supabase doesn't expose
    # SQL GROUP BY; we pull the rows and aggregate in Python. Capped
    # at 10,000 rows — well above a single exam's expected violation
    # count even at 3,500 students; if a teacher's all-time queue ever
    # exceeds it we surface a `truncated: true` flag.
    #
    # IMPORTANT: PostgresTable (and postgrest-py) builders MUTATE
    # `_filters` in `.in_()` / `.eq()` and return `self` — so a query
    # builder can be used at most once. We construct a fresh builder
    # per chunk via this helper.
    def _new_violations_query():
        bq = (_atable("violations")
              .select("session_key,violation_type,severity,created_at")
              .is_("dismissed_at", "null")
              .order("created_at", desc=True)
              .limit(10000))
        if tids is not None:
            if not tids:
                bq = bq.eq("teacher_id", "__none__")
            elif len(tids) == 1:
                bq = bq.eq("teacher_id", str(tids[0]))
            else:
                bq = bq.in_("teacher_id", tids)
        return bq

    if exam_id:
        # Layer exam scoping via session_key allow-list (no SQL JOIN
        # over PostgREST). Fetch the exam's session_keys first.
        es_q = _atable("exam_sessions").select("session_key").eq("exam_id", exam_id)
        if tids is not None and tids:
            es_q = es_q.eq("teacher_id", str(tids[0])) if len(tids) == 1 else es_q.in_("teacher_id", tids)
        es_rows = (await es_q.limit(20000).execute()).data or []
        session_keys = [r["session_key"] for r in es_rows if r.get("session_key")]
        if not session_keys:
            return {"clusters": [], "total_active": 0, "exam_id": exam_id}
        # Supabase REST .in_ tops out around ~1k items reliably; chunk
        # the lookup if we're in the multi-thousand range.
        rows = []
        for i in range(0, len(session_keys), 800):
            chunk = session_keys[i:i + 800]
            r = await _new_violations_query().in_("session_key", chunk).execute()
            rows.extend(r.data or [])
    else:
        rows = (await _new_violations_query().execute()).data or []

    truncated = len(rows) >= 10000

    # Aggregate in Python — (type, severity) buckets.
    buckets: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r.get("violation_type") or "unknown", r.get("severity") or "low")
        b = buckets.setdefault(key, {
            "violation_type": key[0],
            "severity": key[1],
            "count": 0,
            "sample_session_keys": [],
            "first_seen": None,
            "last_seen": None,
        })
        b["count"] += 1
        if len(b["sample_session_keys"]) < 6 and r.get("session_key"):
            b["sample_session_keys"].append(r["session_key"])
        ts = r.get("created_at")
        if ts:
            if b["first_seen"] is None or ts < b["first_seen"]:
                b["first_seen"] = ts
            if b["last_seen"] is None or ts > b["last_seen"]:
                b["last_seen"] = ts

    clusters = sorted(buckets.values(), key=lambda x: x["count"], reverse=True)
    out = {
        "clusters": clusters,
        "total_active": sum(c["count"] for c in clusters),
        "exam_id": exam_id,
    }
    if truncated:
        out["truncated"] = True
    return out


@router.post("/api/v1/admin/violations/bulk-dismiss")
@limiter.limit("20/minute")
async def violations_bulk_dismiss(request: Request, body: dict | None = None):
    """Mark every active (not-yet-dismissed) violation matching the
    cluster key as dismissed by the calling teacher.

    Body:
      {
        "violation_type": "gaze_away",
        "severity": "medium",                 # optional, defaults to all
        "exam_id": "<uuid>",                  # optional, scopes via sessions
        "reason": "False positive — printed diagram on desk"
      }

    Returns: {"dismissed": <int>, "violation_type": ..., "severity": ...}.
    """
    teacher = await require_admin(request)
    body = body or {}
    violation_type = (body.get("violation_type") or "").strip()
    if not violation_type:
        raise HTTPException(status_code=400, detail="violation_type is required")
    severity = (body.get("severity") or "").strip() or None
    exam_id = (body.get("exam_id") or "").strip() or None
    reason = (body.get("reason") or "").strip()[:280] or None

    scope = await resolve_scope(teacher, request)
    tids = await scope_to_teacher_ids(scope)

    # Determine the session_key allow-list when exam_id was supplied —
    # same approach as the cluster endpoint above. Without exam scoping
    # the dismiss applies to every active violation of (type, severity)
    # in scope, which is what the teacher sees in the unfiltered
    # cluster view.
    session_keys: list[str] | None = None
    if exam_id:
        es_q = _atable("exam_sessions").select("session_key").eq("exam_id", exam_id)
        if tids is not None and tids:
            es_q = es_q.eq("teacher_id", str(tids[0])) if len(tids) == 1 else es_q.in_("teacher_id", tids)
        es_rows = (await es_q.limit(20000).execute()).data or []
        session_keys = [r["session_key"] for r in es_rows if r.get("session_key")]
        if not session_keys:
            return {"dismissed": 0, "violation_type": violation_type, "severity": severity}

    now_iso = now_ist().isoformat()
    update_payload = {"dismissed_at": now_iso}
    if reason:
        update_payload["dismissed_reason"] = reason

    total = 0
    # Chunk by session_keys when scoped to an exam, otherwise issue
    # one update against the full teacher_id scope.
    if session_keys is not None:
        for i in range(0, len(session_keys), 800):
            chunk = session_keys[i:i + 800]
            q = (_atable("violations").update(update_payload)
                 .eq("violation_type", violation_type)
                 .is_("dismissed_at", "null")
                 .in_("session_key", chunk))
            if severity:
                q = q.eq("severity", severity)
            if tids is not None and tids:
                q = q.eq("teacher_id", str(tids[0])) if len(tids) == 1 else q.in_("teacher_id", tids)
            r = await q.execute()
            total += len(r.data or [])
    else:
        q = (_atable("violations").update(update_payload)
             .eq("violation_type", violation_type)
             .is_("dismissed_at", "null"))
        if severity:
            q = q.eq("severity", severity)
        if tids is not None and tids:
            q = q.eq("teacher_id", str(tids[0])) if len(tids) == 1 else q.in_("teacher_id", tids)
        r = await q.execute()
        total = len(r.data or [])

    # Invalidate cached risk-score / cluster aggregates for the touched
    # sessions so dashboards reflect the new state on next poll.
    if _cache and session_keys:
        for sk in session_keys:
            try:
                _cache.delete(f"risk_score:{sk}")
            except Exception:
                logger.debug("bulk-dismiss: risk_score cache invalidate failed", exc_info=True)

    logger.info(
        "[bulk-dismiss] teacher=%s type=%s severity=%s exam=%s dismissed=%d",
        safe(teacher.get("id", "")), safe(violation_type), safe(severity or ""),
        safe(exam_id or ""), total,
    )
    return {
        "dismissed": total,
        "violation_type": violation_type,
        "severity": severity,
        "exam_id": exam_id,
    }

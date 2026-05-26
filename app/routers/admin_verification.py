"""ID verification router — pending verifications and decisions."""
from ..log_safe import safe
import json
import logging

from fastapi import APIRouter, Request, HTTPException

from ..auth import require_admin
from ..utils import fmt_ist, now_ist
from ..database import async_table as _atable
from ..limiter import limiter
from .. import cache as _cache
from ..models import SessionStatus, VerificationStatus
from ..models import IdDecisionIn
_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")

_import_json = json  # local reference


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

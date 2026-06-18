"""Google Classroom integration — OAuth, course linking, roster sync, grade passback."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, HTTPException
from starlette.responses import RedirectResponse, HTMLResponse

from ..auth import require_admin
from ..database import async_table as _atable
from ..limiter import limiter
from ..services.crypto import encrypt_token, decrypt_token
from ..utils import now_ist
from ..log_safe import mask_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/google", tags=["google"])


@router.get("/auth")
@limiter.limit("10/minute")
async def google_auth(request: Request):
    """Initiate Google OAuth 2.0 flow for the current teacher."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    state = f"{tid}:{uuid.uuid4().hex[:12]}"
    from ..services.google_classroom import get_authorization_url, is_configured
    if not is_configured():
        return {"error": "Google Classroom is not configured. Set GOOGLE_CLASSROOM_CLIENT_ID and GOOGLE_CLASSROOM_CLIENT_SECRET."}
    url, code_verifier = get_authorization_url(state)
    # Store state (+ the PKCE verifier) for verification/replay on return.
    await _atable("google_oauth_states").insert({
        "state": state,
        "teacher_id": tid,
        "code_verifier": code_verifier,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }).execute()
    return {"auth_url": url}


@router.get("/callback")
@limiter.limit("10/minute")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth 2.0 callback from Google."""
    if error:
        return HTMLResponse(f"<html><body><h3>Google sign-in failed: {error}</h3></body></html>")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    # Verify state
    stored = await _atable("google_oauth_states").select("*").eq("state", state).limit(1).execute()
    if not stored.data:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    state_row = stored.data[0]
    teacher_id = state_row.get("teacher_id", "")

    # Exchange code for tokens (replaying the PKCE verifier captured at /auth)
    from ..services.google_classroom import exchange_code
    token_data = exchange_code(code, code_verifier=state_row.get("code_verifier"))
    if not token_data:
        return HTMLResponse("<html><body><h3>Failed to authenticate with Google. Please try again.</h3></body></html>")

    # Store tokens
    existing = await _atable("google_auth_tokens").select("id").eq("teacher_id", teacher_id).limit(1).execute()
    token_row = {
        "teacher_id": teacher_id,
        "email": token_data.get("email", ""),
        "display_name": token_data.get("name", ""),
        "token_json": encrypt_token(json.dumps(token_data)),
        "updated_at": now_ist().isoformat(),
    }
    if existing.data:
        await _atable("google_auth_tokens").update(token_row).eq("teacher_id", teacher_id).execute()
    else:
        await _atable("google_auth_tokens").insert(token_row).execute()

    # Clean up state
    await _atable("google_oauth_states").delete().eq("state", state).execute()

    # Return a page that closes the popup
    return HTMLResponse("""<html><body><h3>Google Classroom connected! You can close this window.</h3><script src="/static/oauth-popup-close.js" defer></script></body></html>""")


@router.post("/disconnect")
@limiter.limit("10/minute")
async def google_disconnect(request: Request):
    """Disconnect Google Classroom integration."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _atable("google_auth_tokens").delete().eq("teacher_id", tid).execute()
    await _atable("google_classroom_links").delete().eq("teacher_id", tid).execute()
    return {"ok": True}


@router.get("/courses")
@limiter.limit("30/minute")
async def google_courses(request: Request):
    """List Google Classroom courses linked or available for linking."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])

    token_row = await _atable("google_auth_tokens").select("*").eq("teacher_id", tid).limit(1).execute()
    if not token_row.data:
        return {"connected": False, "courses": []}

    from ..services.google_classroom import _build_credentials, list_courses
    token_dict = json.loads(decrypt_token(token_row.data[0].get("token_json", "{}")) or "{}")
    creds = _build_credentials(token_dict)
    if not creds:
        return {"connected": False, "courses": [], "error": "Session expired. Reconnect Google Classroom."}

    courses = await list_courses(creds)

    # Check which courses are already linked
    links = await _atable("google_classroom_links").select("google_course_id,exam_id").eq("teacher_id", tid).execute()
    linked = {l["google_course_id"]: l["exam_id"] for l in (links.data or [])}

    for c in courses:
        c["linked"] = c["id"] in linked
        c["linked_exam_id"] = linked.get(c["id"])

    return {"connected": True, "email": token_row.data[0].get("email", ""), "courses": courses}


@router.post("/link-exam")
@limiter.limit("20/minute")
async def google_link_exam(body: dict, request: Request):
    """Link a Procta exam to a Google Classroom course."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    course_id = (body.get("course_id") or "").strip()
    exam_id = (body.get("exam_id") or "").strip()

    if not course_id or not exam_id:
        raise HTTPException(status_code=400, detail="course_id and exam_id required")

    # Verify the exam belongs to this teacher
    exam = await _atable("exam_config").select("exam_id").eq("exam_id", exam_id).eq("teacher_id", tid).limit(1).execute()
    if not exam.data:
        raise HTTPException(status_code=404, detail="Exam not found")

    existing = await _atable("google_classroom_links").select("id").eq("teacher_id", tid).eq("google_course_id", course_id).limit(1).execute()
    if existing.data:
        await _atable("google_classroom_links").update({"exam_id": exam_id}).eq("id", existing.data[0]["id"]).execute()
    else:
        await _atable("google_classroom_links").insert({
            "teacher_id": tid,
            "google_course_id": course_id,
            "exam_id": exam_id,
        }).execute()

    return {"ok": True, "course_id": course_id, "exam_id": exam_id}


@router.post("/unlink-exam")
@limiter.limit("20/minute")
async def google_unlink_exam(body: dict, request: Request):
    """Unlink a Google Classroom course from its exam."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    course_id = (body.get("course_id") or "").strip()
    await _atable("google_classroom_links").delete().eq("teacher_id", tid).eq("google_course_id", course_id).execute()
    return {"ok": True}


@router.post("/sync-roster")
@limiter.limit("10/minute")
async def google_sync_roster(body: dict, request: Request):
    """Sync student roster from Google Classroom into Procta."""
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    course_id = (body.get("course_id") or "").strip()
    exam_id = (body.get("exam_id") or "").strip()

    if not course_id:
        raise HTTPException(status_code=400, detail="course_id required")

    token_row = await _atable("google_auth_tokens").select("*").eq("teacher_id", tid).limit(1).execute()
    if not token_row.data:
        raise HTTPException(status_code=400, detail="Google Classroom not connected")

    from ..services.google_classroom import _build_credentials, list_students, get_course_name
    token_dict = json.loads(decrypt_token(token_row.data[0].get("token_json", "{}")) or "{}")
    creds = _build_credentials(token_dict)
    if not creds:
        raise HTTPException(status_code=400, detail="Session expired")

    # Tag imported students with the Google class name as their cohort (batch),
    # so the synced roster is immediately usable for exam→batch assignment.
    course_name = (await get_course_name(creds, course_id))[:120]

    students = await list_students(creds, course_id)
    imported = 0
    updated = 0
    skipped_no_email = 0
    for s in students:
        email = (s.get("email") or "").strip()
        name = s.get("name", "")
        # Roll number is derived from the email — without one we can't create a
        # usable roster entry. This happens when the classroom.profile.emails
        # scope wasn't granted; surface it so the teacher can reconnect.
        if "@" not in email:
            skipped_no_email += 1
            continue
        roll = email.split("@")[0].upper()
        existing = await _atable("students").select("id,batch").eq("roll_number", roll).eq("teacher_id", tid).limit(1).execute()
        if existing.data:
            # Back-fill the cohort onto an already-imported student, but never
            # clobber a batch the teacher set manually.
            if course_name and not (existing.data[0].get("batch") or "").strip():
                try:
                    await _atable("students").update({"batch": course_name}).eq("id", existing.data[0]["id"]).execute()
                    updated += 1
                except Exception as e:
                    logger.warning("[google] roster batch back-fill failed for %s: %s", mask_email(email), e)
            continue
        try:
            row = {
                "roll_number": roll,
                "full_name": name,
                "email": email,
                "teacher_id": tid,
            }
            if course_name:
                row["batch"] = course_name
            await _atable("students").insert(row).execute()
            imported += 1
        except Exception as e:
            logger.warning("[google] roster import failed for %s: %s", mask_email(email), e)

    resp = {
        "ok": True,
        "imported": imported,
        "updated": updated,
        "total": len(students),
        "batch": course_name,
    }
    if skipped_no_email:
        resp["skipped_no_email"] = skipped_no_email
        resp["warning"] = (
            f"{skipped_no_email} student(s) had no readable email — grant the "
            "'See student email addresses' permission and reconnect Google Classroom."
        )
    return resp

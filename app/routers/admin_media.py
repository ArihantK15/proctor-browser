"""Media router — question image upload/serve and screenshot serve."""
import base64
import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import FileResponse

from ..auth import require_admin, verify_admin_token
from ..auth.scope import resolve_scope, assert_session_accessible
from ..limiter import limiter
from ..constants import QUESTION_IMG_DIR, SCREENSHOTS_DIR
from ..services.object_store import is_enabled as _s3_enabled, fetch_screenshot as _s3_fetch
from ..utils import _safe_path_component, _assert_within_directory
from ..models import UploadQuestionImageIn

_admin_log = logging.getLogger("admin")
logger = logging.getLogger(__name__)

router = APIRouter(prefix="")


@router.post("/api/v1/admin/upload-question-image")
@limiter.limit("30/minute")
async def upload_question_image(request: Request, body: UploadQuestionImageIn = Body(...)):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    raw = body.data_url or ""
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=400, detail="Missing 'image' (base64)")
    if raw.startswith("data:"):
        try:
            _, raw = raw.split(",", 1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Malformed data URL")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image payload")
    if len(blob) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 4MB)")

    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
        media = "image/png"
    elif blob[:3] == b"\xff\xd8\xff":
        ext = "jpg"
        media = "image/jpeg"
    elif blob[:6] in (b"GIF87a", b"GIF89a"):
        ext = "gif"
        media = "image/gif"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        ext = "webp"
        media = "image/webp"
    else:
        raise HTTPException(status_code=400, detail="Unsupported image format (PNG/JPEG/GIF/WebP only)")

    digest = hashlib.sha256(blob, usedforsecurity=False).hexdigest()[:24]
    filename = f"{digest}.{ext}"
    tdir = Path(QUESTION_IMG_DIR) / tid
    tdir.mkdir(parents=True, exist_ok=True)
    fpath = tdir / filename
    if not fpath.exists():
        try:
            with open(fpath, "wb") as f:
                f.write(blob)
        except OSError as e:
            _admin_log.error("[QImage] write failed: %s", e)
            raise HTTPException(status_code=500, detail="Failed to store image")

    url = f"/api/v1/question-image/{tid}/{filename}"
    return {"url": url, "bytes": len(blob), "media_type": media}


@router.get("/api/v1/question-image/{tid}/{filename}")
@limiter.limit("60/minute")
async def get_question_image(tid: str, filename: str, request: Request, exam_id: str = ""):
    import jwt
    from jwt.exceptions import InvalidTokenError as JWTError
    auth = request.headers.get("Authorization", "")
    allowed = False
    is_student = False
    payload = None
    if auth.startswith("Bearer "):
        tok = auth[7:]
        try:
            teacher = await verify_admin_token(tok)
            if str(teacher.get("id")) == str(tid):
                allowed = True
        except HTTPException:
            pass
        if not allowed:
            try:
                from ..auth.tokens import _decode_token
                from ..constants import ALL_SIGNING_KEYS
                payload = _decode_token(tok, ALL_SIGNING_KEYS)
                if str(payload.get("tid") or "") == str(tid):
                    allowed = True
                    is_student = True
            except JWTError:
                pass
    if not allowed:
        raise HTTPException(status_code=401, detail="Authentication required")

    # For student tokens, if an exam_id is provided verify it matches the JWT
    if is_student and exam_id and payload and exam_id != str(payload.get("eid") or ""):
        raise HTTPException(status_code=403, detail="Not authorized for this exam")

    safe_teacher_id = _safe_path_component(tid)
    safe_file = _safe_path_component(filename)
    fpath = Path(QUESTION_IMG_DIR) / safe_teacher_id / safe_file
    try:
        _assert_within_directory(fpath, Path(QUESTION_IMG_DIR))
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Image not found")
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    suffix = fpath.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp"}
    media = media_map.get(suffix, "application/octet-stream")
    return FileResponse(str(fpath), media_type=media)


@router.get("/api/v1/admin/screenshot/{roll}/{filename}")
@limiter.limit("60/minute")
async def get_screenshot(roll: str, filename: str, request: Request,
                         session_id: str = ""):
    teacher = await require_admin(request)
    safe_roll = _safe_path_component(roll)
    safe_file = _safe_path_component(filename)
    # Screenshots live under SCREENSHOTS_DIR/{owning_teacher_id}/{roll}/...
    # where the owner is the teacher who set the exam (the student's exam
    # token tid), NOT necessarily the caller. When a session_id is supplied
    # we resolve the owner through the scope spine: assert_session_accessible
    # 404s any session outside the caller's tenant, so an org admin can reach
    # an org-member's screenshots, a plain teacher stays locked to their own,
    # and a superadmin is unrestricted. Absent session_id we fall back to the
    # caller's own tid (legacy direct links / own-scoped verification URLs).
    if session_id:
        scope = await resolve_scope(teacher, request)
        sess = await assert_session_accessible(session_id, scope)
        tid = str(sess.get("teacher_id") or "")
        # A session with no derivable owner (orphan row) must NOT widen the
        # search to the root screenshots dir — that would expose the whole
        # tree across tenants. Treat as not-found.
        if not tid:
            raise HTTPException(status_code=404, detail="Screenshot not found")
    else:
        tid = str(teacher["id"])
    safe_tid = _safe_path_component(tid)
    base = Path(SCREENSHOTS_DIR) / safe_tid
    fpath = base / safe_roll / safe_file
    try:
        _assert_within_directory(fpath, base)
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if not fpath.exists() or not fpath.is_file():
        # Fall back to S3 when S3 is enabled and the local file is absent
        if _s3_enabled():
            s3_key = f"{safe_tid}/{safe_roll}/{safe_file}"
            blob = _s3_fetch(s3_key)
            if blob is not None:
                suffix = fpath.suffix.lower()
                media = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
                from fastapi.responses import Response
                return Response(content=blob, media_type=media,
                                headers={"Cache-Control": "private, max-age=3600"})
        raise HTTPException(status_code=404, detail="Screenshot not found")
    suffix = fpath.suffix.lower()
    media = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(fpath), media_type=media,
                        headers={"Cache-Control": "private, max-age=3600"})

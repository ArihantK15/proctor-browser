"""List-Unsubscribe one-click (RFC 8058) and human-link unsubscribe.

Unauthenticated, token-gated endpoints.  No CSRF required — the POST
request carries no browser session cookie.
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..database import async_table as _atable
from ..services.local_auth import verify_unsubscribe_token
from ..logger import get_logger

_log = get_logger("unsubscribe")

router = APIRouter(prefix="/api/v1")


class _UnsubBody(BaseModel):
    token: str


async def _perform_unsubscribe(token: str) -> None:
    """Verify token and flip email_reminders_enabled to false.

    Returns (raises HTTPException on failure).  Idempotent — repeat
    calls with the same token are harmless.
    """
    claims = verify_unsubscribe_token(token)
    if not claims:
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe token")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Token missing email claim")
    try:
        await (_atable("student_accounts")
               .update({"email_reminders_enabled": False})
               .eq("email", email)
               .execute())
    except Exception as exc:
        _log.warning("unsubscribe db update failed for %s: %s", email, exc)


@router.post("/unsubscribe")
async def unsubscribe_one_click(body: _UnsubBody):
    """RFC 8058 one-click unsubscribe.

    Email providers (Gmail, Outlook, etc.) POST to this URL with
    ``List-Unsubscribe=One-Click`` in the body when the user clicks
    "Unsubscribe" in the email UI.  Returns 200 even if the student
    is already unsubscribed.
    """
    await _perform_unsubscribe(body.token)
    return {"ok": True}


@router.get("/unsubscribe")
async def unsubscribe_link(request: Request):
    """Human-readable unsubscribe page.

    Clicking the List-Unsubscribe link in an email client that does
    not support one-click RFC 8058 lands here.  Performs the
    unsubscribe and returns a simple confirmation page.
    """
    token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(status_code=400, detail="Missing token parameter")
    await _perform_unsubscribe(token)
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unsubscribed</title>
<style>
  body{font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;background:#f5f5f5}
  .card{background:#fff;padding:2rem;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);text-align:center;max-width:400px}
  h1{font-size:1.25rem;margin:0 0 .5rem}
  p{color:#555;margin:0;line-height:1.5}
</style>
</head>
<body>
<div class="card">
<h1>You have been unsubscribed</h1>
<p>You will no longer receive exam reminder emails from Procta. You can re-enable reminders from your account settings at any time.</p>
</div>
</body>
</html>""",
        status_code=200,
        media_type="text/html",
    )

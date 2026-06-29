"""LTI 1.3 router — OIDC login, launch, JWKS, config, deep linking, and AGS."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse

from ..auth import require_admin
from ..lti.key import generate_jwks
from ..lti.registration import load_registrations, find_registration
from ..lti.launch import (
    build_oidc_redirect,
    validate_id_token,
    find_or_create_lti_user,
    issue_lti_session_token,
    get_ags_context,
    get_nrps_context,
)
from ..lti.deeplink import (
    validate_deep_linking_request,
    build_deep_linking_response,
    get_teacher_exams_as_content_items,
    build_auto_submit_html,
)
from ..lti.ags import (
    get_access_token,
    post_score,
    get_results,
    create_line_item,
)
from ..lti.nrps import fetch_membership, sync_learner_roster
from ..limiter import limiter
from ..log_safe import mask_email, safe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lti")


@router.get("/jwks")
async def lti_jwks():
    """Return our tool's public JWKS for LTI 1.3 key exchange.

    LMS platforms fetch this to verify JWTs we sign (e.g., AGS grade
    passback requests, deep linking responses).
    """
    return JSONResponse(content=generate_jwks())


@router.get("/jwks.json")
async def lti_jwks_json():
    return await lti_jwks()


@router.get("/config")
async def lti_config():
    """Return the LTI 1.3 tool configuration as JSON.

    LMS admins use this URL during tool registration to auto-configure
    the integration.  Compatible with Canvas, Moodle, and Google Classroom.
    """
    from ..constants import LTI_LOGIN_URL, LTI_LAUNCH_URL, LTI_DEEP_LINKING_URL

    return {
        "title": "Procta",
        "description": "AI-powered proctored exam platform",
        "oidc_login_url": LTI_LOGIN_URL or "https://app.procta.net/lti/login",
        "target_link_uri": LTI_LAUNCH_URL or "https://app.procta.net/lti/launch",
        "custom_url": LTI_DEEP_LINKING_URL or "https://app.procta.net/lti/deeplink",
        "public_jwk_url": "https://app.procta.net/lti/jwks",
        "scopes": [
            "https://purl.imsglobal.org/spec/lti-ags/scope/lineitem",
            "https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly",
            "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly",
        ],
    }


@router.api_route("/login", methods=["GET", "POST"])
@limiter.limit("30/minute")
async def lti_login(request: Request):
    """LTI 1.3 OIDC login initiation.

    The LMS redirects the user here.  We generate state + nonce, store
    them, and redirect back to the LMS's OIDC auth endpoint.

    Per the LTI 1.3 / OIDC third-party-initiated-login spec, the tool
    MUST accept this request as BOTH HTTP GET (params in the query
    string) and HTTP POST (params form-encoded in the body). Canvas and
    several platforms POST — accepting only GET returned 405 Method Not
    Allowed and broke the launch before it began.

    Parameters (LTI 1.3 OIDC):
      - iss:  Issuer URL identifying the LMS platform
      - login_hint: Opaque token the LMS uses to identify the user
      - target_link_uri: Where the user should end up after launch
      - client_id: Our tool's client_id within the LMS
      - lti_message_hint: Additional context from the LMS
    """
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            params.update({k: str(v) for k, v in form.items()})
        except Exception:
            pass

    iss = params.get("iss", "")
    login_hint = params.get("login_hint", "")
    target_link_uri = params.get("target_link_uri", "")
    client_id = params.get("client_id", "")
    lti_message_hint = params.get("lti_message_hint", "")
    lti_deployment_id = params.get("lti_deployment_id", "")

    if not iss or not login_hint or not target_link_uri or not client_id:
        raise HTTPException(status_code=400, detail="Missing required OIDC parameters")

    registration = find_registration(iss, client_id)
    if not registration:
        raise HTTPException(
            status_code=400,
            detail=f"No LTI registration found for issuer={iss} client_id={client_id}. "
                   "The tool has not been configured for this LMS platform yet."
        )

    try:
        redirect_url, state = build_oidc_redirect(
            auth_login_url=registration.auth_login_url,
            client_id=client_id,
            login_hint=login_hint,
            target_link_uri=target_link_uri,
            lti_message_hint=lti_message_hint,
            deployment_id=lti_deployment_id,
        )
    except Exception as e:
        logger.error("Failed to build OIDC redirect: %s", e)
        raise HTTPException(status_code=500, detail="Failed to initiate LTI login")

    return RedirectResponse(url=redirect_url)


@router.post("/launch")
@limiter.limit("60/minute")
async def lti_launch(request: Request):
    """LTI 1.3 launch endpoint.

    The LMS POSTs the id_token here after authenticating the user.
    We validate the JWT, create/find the user, and redirect them to
    the appropriate page (exam for students, dashboard for teachers).

    Expected POST body (form-encoded): id_token=<JWT>&state=<state>
    """
    try:
        body = await request.form()
        id_token = body.get("id_token", "")
        state = body.get("state", "")
    except Exception:
        try:
            raw = await request.json()
            id_token = raw.get("id_token", "")
            state = raw.get("state", "")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

    if not id_token:
        raise HTTPException(status_code=400, detail="Missing id_token")

    # Validate the id_token
    try:
        claims = await validate_id_token(str(id_token), str(state))
    except ValueError as e:
        logger.warning("LTI launch validation failed: %s", e)
        raise HTTPException(status_code=401, detail=str(e))

    # Find or create user
    try:
        user = await find_or_create_lti_user(claims)
    except ValueError as e:
        # Deliberate fail-closed refusal (e.g. registration not bound to an
        # org). Not a server fault — surface as 403 so it's distinguishable
        # from unexpected errors in logs/monitoring.
        logger.warning("LTI launch refused: %s", e)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("Failed to process LTI user: %s", e)
        raise HTTPException(status_code=500, detail="Failed to process launch")

    # Issue session token
    target_link_uri = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/target_link_uri",
        "/dashboard",
    )
    token = issue_lti_session_token(user, target_link_uri)

    # Redirect based on role — use fragment to avoid token in server logs.
    # /dashboard serves the legacy HTML dashboard whose JS reads the
    # #access_token fragment that LTI delivers (no cookie is set here), so
    # launches stay authenticated.
    if user.get("role") == "teacher":
        redirect_to = f"/dashboard#access_token={token}&token_type=Bearer"
    else:
        redirect_to = f"/student#access_token={token}&token_type=Bearer"

    logger.info("LTI launch successful: role=%s email=%s", safe(user.get("role")), mask_email(user.get("email")))
    return RedirectResponse(url=redirect_to, status_code=302)


@router.post("/deeplink")
@limiter.limit("60/minute")
async def lti_deeplink(request: Request):
    """LTI 1.3 Deep Linking endpoint.

    The LMS POSTs a Deep Linking *request* here after the OIDC dance.
    Like any LTI 1.3 message it arrives as `id_token` via form_post
    (the JWT's message_type is LtiDeepLinkingRequest). We validate it,
    find the teacher's exams, and return content items in a signed
    response posted back to the LMS's deep_link_return_url.

    Note: `id_token` is the platform→tool field. `JWT` is what the
    TOOL→platform *response* uses, so we accept it as a fallback for
    odd platforms, but the spec-correct field for the inbound request
    is `id_token`. (Reading only `JWT` was why launches 400'd with
    "Missing JWT" — the platform never sends a `JWT` field inbound.)

    Expected POST body (form-encoded): id_token=<JWT>
    """
    try:
        body = await request.form()
        jwt_token = body.get("id_token", "") or body.get("JWT", "")
    except Exception:
        try:
            raw = await request.json()
            jwt_token = raw.get("id_token", "") or raw.get("JWT", "")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

    if not jwt_token:
        raise HTTPException(status_code=400, detail="Missing id_token")

    # Validate the deep linking request JWT
    try:
        claims = await validate_deep_linking_request(str(jwt_token))
    except ValueError as e:
        logger.warning("Deep linking validation failed: %s", e)
        raise HTTPException(status_code=401, detail=str(e))

    # Determine the user's role and find their content
    roles = claims.get(
        "https://purl.imsglobal.org/spec/lti/claim/roles", []
    )
    is_instructor = any(
        "Instructor" in r or "Administrator" in r or "TeachingAssistant" in r or "ContentDeveloper" in r
        for r in roles
    )

    if not is_instructor:
        raise HTTPException(
            status_code=403,
            detail="Only instructors can select content via deep linking",
        )

    # Find or create the user to get their local ID
    try:
        user = await find_or_create_lti_user(claims)
    except ValueError as e:
        logger.warning("LTI deep linking refused: %s", e)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("Failed to process deep linking user: %s", e)
        raise HTTPException(status_code=500, detail="Failed to process user")

    # Get teacher's exams as content items
    try:
        teacher_id = user.get("id", "")
        if not teacher_id:
            # No resolved local user (e.g. provisioning failed upstream) — don't
            # query with an empty string, which asyncpg rejects as an invalid
            # UUID. Degrade to an empty picker rather than log a noisy error.
            logger.warning("lti: deeplink with no resolved teacher_id; empty content list")
            content_items = []
        else:
            content_items = await get_teacher_exams_as_content_items(teacher_id)
    except Exception as e:
        logger.error("Failed to fetch content items: %s", e)
        content_items = []

    # Build the signed response JWT
    try:
        jwt_response = build_deep_linking_response(claims, content_items)
    except Exception as e:
        logger.error("Failed to build deep linking response: %s", e)
        raise HTTPException(status_code=500, detail="Failed to build response")

    # Get the return URL from the request claims
    deep_link_return_url = claims.get(
        "https://purl.imsglobal.org/spec/lti-dl/claim/return_url",
        "",
    )
    if not deep_link_return_url:
        raise HTTPException(status_code=400, detail="No return URL in deep linking request")

    # Return an HTML page that auto-submits the response to the LMS
    html = build_auto_submit_html(deep_link_return_url, jwt_response)
    return HTMLResponse(content=html)


@router.get("/ags/lineitems")
@limiter.limit("30/minute")
async def lti_ags_lineitems(request: Request):
    """List AGS line items for the current context.

    This is an LMS-facing endpoint.  In a full implementation, this
    would read line items from a local database.  Currently returns
    a stub indicating the scope is declared but the endpoint is
    placeholder-only.
    """
    return JSONResponse(
        content={"status": "declared", "message": "AGS line items endpoint declared but not fully implemented"},
        status_code=501,
    )


@router.get("/nrps/membership")
@limiter.limit("30/minute")
async def lti_nrps_membership(request: Request):
    """Return NRPS membership for the current context.

    This is an LMS-facing endpoint.  In a full implementation, this
    would return course roster data.  Currently returns a stub
    indicating the scope is declared but the endpoint is
    placeholder-only.
    """
    return JSONResponse(
        content={"status": "declared", "message": "NRPS membership endpoint declared but not fully implemented"},
        status_code=501,
    )


@router.post("/ags/sync-membership")
@limiter.limit("30/minute")
async def lti_sync_membership(request: Request):
    """Manually trigger an NRPS membership sync from the LMS.

    Expects JSON body with:
      - iss: The LMS issuer URL
      - client_id: The tool's client_id
      - deployment_id: The deployment ID

    This is used to pre-provision student accounts from the course
    roster after an instructor's LTI launch.
    """
    # Admin-only: pulls a roster from the LMS using Procta's tool credentials —
    # must not be anonymously triggerable.
    await require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    iss = body.get("iss", "")
    client_id = body.get("client_id", "")
    deployment_id = body.get("deployment_id", "")

    if not iss or not deployment_id:
        raise HTTPException(status_code=400, detail="iss and deployment_id required")

    registration = find_registration(iss, client_id)
    if not registration:
        raise HTTPException(status_code=400, detail=f"No registration found for issuer={iss}")

    nrps_ctx = get_nrps_context(iss, deployment_id)
    if not nrps_ctx or not nrps_ctx.get("nrps_url"):
        raise HTTPException(
            status_code=404,
            detail="No NRPS context found. An instructor must launch the tool first.",
        )

    access_token = await get_access_token(
        issuer=iss,
        client_id=registration.client_id,
        auth_token_url=registration.auth_token_url,
        scopes=[
            "https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly"
        ],
    )
    if not access_token:
        raise HTTPException(status_code=502, detail="Failed to get access token from LMS")

    members = await fetch_membership(
        context_memberships_url=nrps_ctx["nrps_url"],
        access_token=access_token,
    )

    return {"members": members, "count": len(members)}


@router.post("/ags/push-grades")
@limiter.limit("30/minute")
async def lti_push_grades(request: Request):
    """Push exam scores back to the LMS via AGS grade passback.

    Expects JSON body with:
      - iss: The LMS issuer URL
      - client_id: The tool's client_id
      - deployment_id: The deployment ID
      - user_id: The LTI user ID (sub claim)
      - score_given: The score to push
      - score_maximum: The maximum possible score
      - timestamp: ISO 8601 timestamp
      - comment: Optional comment
    """
    # Admin-only: this pushes a grade to the LMS using Procta's OWN tool
    # credentials, so it must never be anonymous — otherwise anyone who knows an
    # issuer + LTI user_id could set arbitrary grades. (No frontend caller.)
    await require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    iss = body.get("iss", "")
    client_id = body.get("client_id", "")
    deployment_id = body.get("deployment_id", "")
    user_id = body.get("user_id", "")
    score_given = body.get("score_given")
    score_maximum = body.get("score_maximum", 100)
    timestamp = body.get("timestamp", "")
    comment = body.get("comment", "")

    if not all([iss, deployment_id, user_id, score_given is not None]):
        raise HTTPException(
            status_code=400,
            detail="iss, deployment_id, user_id, and score_given are required",
        )

    ags_ctx = get_ags_context(iss, deployment_id)
    if not ags_ctx or not ags_ctx.get("ags_lineitems"):
        raise HTTPException(
            status_code=404,
            detail="No AGS context found. An instructor must launch the tool first.",
        )

    registration = find_registration(iss, client_id)
    if not registration:
        raise HTTPException(status_code=400, detail=f"No registration found for issuer={iss}")

    access_token = await get_access_token(
        issuer=iss,
        client_id=registration.client_id,
        auth_token_url=registration.auth_token_url,
        scopes=ags_ctx.get("ags_scope") or [
            "https://purl.imsglobal.org/spec/lti-ags/scope/score",
        ],
    )
    if not access_token:
        raise HTTPException(status_code=502, detail="Failed to get access token from LMS")

    success = await post_score(
        lineitem_url=ags_ctx["ags_lineitems"],
        access_token=access_token,
        user_id=user_id,
        score_given=float(score_given),
        score_maximum=float(score_maximum),
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        comment=comment,
    )

    if not success:
        raise HTTPException(status_code=502, detail="Failed to push grade to LMS")

    return {"ok": True, "user_id": user_id, "score_given": score_given}

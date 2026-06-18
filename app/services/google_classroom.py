"""Google Classroom API integration service.

Handles OAuth 2.0 authorization, course listing, roster sync,
and grade passback to Google Classroom.
"""

import json
import logging
import os
from typing import Optional

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

_GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLASSROOM_CLIENT_ID", "").strip()
_GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLASSROOM_CLIENT_SECRET", "").strip()
_GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_CLASSROOM_REDIRECT_URI",
                                        "https://app.procta.net/api/v1/google/callback")
_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    # Required to read student email addresses — rosters.readonly alone returns
    # the student record WITHOUT profile.emailAddress, and the roster import
    # derives the roll number from the email. Without this scope sync-roster
    # gets no usable identity for any student.
    "https://www.googleapis.com/auth/classroom.profile.emails",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.coursework.me",
]

_SERVICE = "classroom"
_API_VERSION = "v1"


def is_configured() -> bool:
    """Check whether Google Classroom OAuth credentials are configured."""
    return bool(_GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET)


def get_authorization_url(state: str) -> tuple[str, str]:
    """Generate the Google OAuth authorization URL.

    Returns (auth_url, code_verifier). google-auth-oauthlib auto-generates a
    PKCE code_verifier here and embeds its code_challenge in the URL; the same
    verifier MUST be replayed at token exchange, so the caller has to persist
    it (see exchange_code) — otherwise Google rejects the exchange with
    "(invalid_grant) Missing code verifier."
    """
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": _GOOGLE_CLIENT_ID,
                "client_secret": _GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_GOOGLE_REDIRECT_URI],
            }
        },
        scopes=_GOOGLE_SCOPES,
        state=state,
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = _GOOGLE_REDIRECT_URI
    auth_url = flow.authorization_url(
        access_type="offline",
        prompt="consent",  # Force refresh_token every time
    )[0]
    return auth_url, flow.code_verifier


def exchange_code(code: str, code_verifier: Optional[str] = None) -> Optional[dict]:
    """Exchange the OAuth code for tokens. Returns token dict or None.

    code_verifier is the PKCE verifier captured at get_authorization_url time
    and persisted with the state row; it must be supplied or Google rejects the
    exchange ("Missing code verifier").
    """
    try:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": _GOOGLE_CLIENT_ID,
                    "client_secret": _GOOGLE_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [_GOOGLE_REDIRECT_URI],
                }
            },
            scopes=_GOOGLE_SCOPES,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = _GOOGLE_REDIRECT_URI
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        token_data = {
            "token": flow.credentials.token,
            "refresh_token": flow.credentials.refresh_token,
            "token_uri": flow.credentials.token_uri,
            "client_id": flow.credentials.client_id,
            "client_secret": flow.credentials.client_secret,
            "scopes": flow.credentials.scopes,
            "expiry": flow.credentials.expiry.isoformat() if flow.credentials.expiry else None,
        }
        # Get user info from the ID token
        id_info = flow.credentials.id_token
        if id_info:
            token_data["email"] = id_info.get("email", "")
            token_data["name"] = id_info.get("name", "")
        return token_data
    except Exception as e:
        logger.error("[google_classroom] token exchange failed: %s", e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        return None


def _build_credentials(token_dict: dict) -> Optional[Credentials]:
    """Build Google Credentials from a stored token dict, refreshing if needed."""
    try:
        creds = Credentials(
            token=token_dict.get("token"),
            refresh_token=token_dict.get("refresh_token"),
            token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_dict.get("client_id", _GOOGLE_CLIENT_ID),
            client_secret=token_dict.get("client_secret", _GOOGLE_CLIENT_SECRET),
            scopes=token_dict.get("scopes", _GOOGLE_SCOPES),
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds
    except Exception as e:
        logger.warning("[google_classroom] credential refresh failed: %s", e)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        return None


async def list_courses(creds: Credentials) -> list[dict]:
    """List Google Classroom courses accessible by this teacher."""
    try:
        service = build(_SERVICE, _API_VERSION, credentials=creds, cache_discovery=False)
        result = service.courses().list(
            pageSize=100,
            courseStates=["ACTIVE", "PROVISIONED"],
        ).execute()
        courses = result.get("courses", [])
        return [
            {"id": c["id"], "name": c["name"], "section": c.get("section", ""),
             "enrollment_code": c.get("enrollmentCode", "")}
            for c in courses
        ]
    except HttpError as e:
        logger.warning("[google_classroom] list courses failed: %s", e)
        return []


async def get_course_name(creds: Credentials, course_id: str) -> str:
    """Return a Google Classroom course's display name (or "" if unavailable).

    Used to tag imported students with their cohort (students.batch) so a synced
    roster is immediately usable for exam→batch assignment. Fail-soft: a missing
    name must never break the roster import.
    """
    try:
        service = build(_SERVICE, _API_VERSION, credentials=creds, cache_discovery=False)
        course = service.courses().get(id=course_id).execute()
        return (course.get("name") or "").strip()
    except Exception as e:
        logger.warning("[google_classroom] get course name failed: %s", e)
        return ""


async def list_students(creds: Credentials, course_id: str) -> list[dict]:
    """List students enrolled in a Google Classroom course."""
    try:
        service = build(_SERVICE, _API_VERSION, credentials=creds, cache_discovery=False)
        result = service.courses().students().list(
            courseId=course_id, pageSize=200
        ).execute()
        students = result.get("students", [])
        out = []
        for s in students:
            profile = s.get("profile", {}) or {}
            out.append({
                "user_id": s.get("userId", ""),
                # emailAddress is only present when the classroom.profile.emails
                # scope was granted; tolerate its absence instead of KeyError-ing.
                "email": profile.get("emailAddress", ""),
                "name": (profile.get("name", {}) or {}).get("fullName", ""),
            })
        return out
    except HttpError as e:
        logger.warning("[google_classroom] list students failed: %s", e)
        return []
    except Exception as e:  # malformed profile payloads, etc. — never 500 the caller
        logger.warning("[google_classroom] list students parse failed: %s", e)
        return []


async def create_coursework(creds: Credentials, course_id: str, title: str,
                            max_points: float) -> Optional[str]:
    """Create a graded ASSIGNMENT courseWork in a course; return its id or None.

    Procta creates one assignment per linked exam (lazily, the first time a
    grade is pushed) and patches each student's submission onto it. Creating a
    PUBLISHED assignment auto-creates a studentSubmission for every assignee,
    which push_grade then grades. Fail-soft: a creation failure must not break
    exam submission — the score is still recorded in Procta.
    """
    try:
        service = build(_SERVICE, _API_VERSION, credentials=creds, cache_discovery=False)
        body = {
            "title": (title or "Procta Exam")[:3000],
            "description": "Auto-created by Procta — exam scores are posted here.",
            "workType": "ASSIGNMENT",
            "state": "PUBLISHED",
        }
        if max_points and max_points > 0:
            body["maxPoints"] = float(max_points)
        created = service.courses().courseWork().create(
            courseId=course_id, body=body,
        ).execute()
        return created.get("id")
    except Exception as e:
        logger.warning("[google_classroom] create coursework failed: %s", e)
        return None


async def push_grade(creds: Credentials, course_id: str, course_work_id: str,
                     user_id: str, score: float, max_score: float) -> bool:
    """Push a student's grade back to a Google Classroom course work item."""
    try:
        service = build(_SERVICE, _API_VERSION, credentials=creds, cache_discovery=False)
        submission = service.courses().courseWork().studentSubmissions().get(
            courseId=course_id, courseWorkId=course_work_id, userId=user_id
        ).execute()
        assignment_grade = submission.get("assignedGrade")
        if assignment_grade is not None and float(assignment_grade) >= score:
            return True  # Already graded with same or higher score
        submission["assignedGrade"] = score
        submission["maxPoints"] = max_score
        submission["draftGrade"] = score
        service.courses().courseWork().studentSubmissions().patch(
            courseId=course_id, courseWorkId=course_work_id, userId=user_id,
            body={"assignedGrade": score, "draftGrade": score},
            updateMask="assignedGrade,draftGrade",
        ).execute()
        # Mark as returned so the student can see the grade
        service.courses().courseWork().studentSubmissions().return_(
            courseId=course_id, courseWorkId=course_work_id, userId=user_id,
        ).execute()
        return True
    except HttpError as e:
        logger.warning("[google_classroom] grade push failed: %s", e)
        return False

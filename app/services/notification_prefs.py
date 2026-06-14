"""Teacher/admin notification preferences — opt-out per category.

Usage:
    if await teacher_wants(teacher_id, "security"):
        send_suspicious_login_email(...)

Categorisation (see GAP_ANALYSIS_2026-06-12.md #28):
    billing          → payment-failure dunning
    security         → suspicious-login alerts
    student_activity → student-account-lifecycle notifications to teachers

Fail-OPEN: on any lookup/parse error we return True (send) so a missing
column or schema lag never silently drops notifications.
"""
from __future__ import annotations

import json
import logging

from ..database import async_table as _atable

logger = logging.getLogger(__name__)

KNOWN_CATEGORIES = frozenset({"billing", "security", "student_activity"})


async def teacher_wants(teacher_id: str, category: str) -> bool:
    """True unless the teacher explicitly disabled this category.

    Opt-OUT model: absent key or True = send; explicit jsonb false = suppress.
    """
    if category not in KNOWN_CATEGORIES:
        return True
    try:
        rows = (
            await _atable("teachers")
            .select("notification_prefs")
            .eq("id", teacher_id)
            .limit(1)
            .execute()
        ).data or []
        if not rows:
            return True
        raw = rows[0].get("notification_prefs")
        if raw is None:
            return True
        if isinstance(raw, str):
            prefs = json.loads(raw)
        elif isinstance(raw, dict):
            prefs = raw
        else:
            return True
        return prefs.get(category) is not False
    except Exception:
        logger.warning("[notification_prefs] lookup failed for %s/%s", teacher_id, category, exc_info=True)
        return True


async def get_prefs(teacher_id: str) -> dict[str, bool]:
    """Return {category: bool} for all known categories. Defaults to True."""
    try:
        rows = (
            await _atable("teachers")
            .select("notification_prefs")
            .eq("id", teacher_id)
            .limit(1)
            .execute()
        ).data or []
        if not rows:
            return {c: True for c in KNOWN_CATEGORIES}
        raw = rows[0].get("notification_prefs")
        if raw is None:
            return {c: True for c in KNOWN_CATEGORIES}
        if isinstance(raw, str):
            prefs = json.loads(raw)
        elif isinstance(raw, dict):
            prefs = raw
        else:
            return {c: True for c in KNOWN_CATEGORIES}
        return {c: prefs.get(c) is not False for c in KNOWN_CATEGORIES}
    except Exception:
        logger.warning("[notification_prefs] get_prefs failed for %s", teacher_id, exc_info=True)
        return {c: True for c in KNOWN_CATEGORIES}

"""Soft cap: when OVERAGE_BILLING_ENABLED, exceeding the plan student limit is
allowed (billed as overage) instead of a hard 403. Off = hard cap (legacy)."""
import os
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from fastapi import HTTPException
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
from app.services import sessions

def _run(c): return asyncio.run(c)

def _setup(cache_dict):
    c = MagicMock(); c.get.return_value = cache_dict
    return c

def test_hard_cap_denies_when_soft_off():
    teacher = {"org_id": "o1", "org_role": "admin"}
    with patch.object(sessions, "_check_subscription_active", AsyncMock(return_value=None)), \
         patch.object(sessions, "_cache", _setup({"org": {"max_students": 30}, "student_count": 31})), \
         patch.object(sessions, "_soft_cap_enabled", return_value=False):
        with pytest.raises(HTTPException) as ei:
            _run(sessions.check_org_limits(teacher, delta=0))
        assert ei.value.status_code == 403

def test_soft_cap_allows_overage_when_on():
    teacher = {"org_id": "o1", "org_role": "admin"}
    with patch.object(sessions, "_check_subscription_active", AsyncMock(return_value=None)), \
         patch.object(sessions, "_cache", _setup({"org": {"max_students": 30}, "student_count": 31})), \
         patch.object(sessions, "_soft_cap_enabled", return_value=True):
        org = _run(sessions.check_org_limits(teacher, delta=0))
        assert org == {"max_students": 30}

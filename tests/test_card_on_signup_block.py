"""Card-on-signup backend block: a 'created' (un-authorised) subscription is
denied access only when CARD_ON_SIGNUP_ENFORCED is on. Flag off = legacy
no-card free-trial behaviour, unchanged.
"""
import os
import asyncio
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import HTTPException

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

import app.constants
from app.services import sessions


def _run(coro):
    return asyncio.run(coro)


def test_created_sub_blocked_when_flag_on():
    with patch.object(sessions, "get_org_subscription", AsyncMock(return_value={"status": "created"})), \
         patch.object(app.constants, "CARD_ON_SIGNUP_ENFORCED", True):
        with pytest.raises(HTTPException) as ei:
            _run(sessions._check_subscription_active("org1"))
        assert ei.value.status_code == 403
        assert "payment" in ei.value.detail.lower()


def test_created_sub_allowed_when_flag_off():
    with patch.object(sessions, "get_org_subscription", AsyncMock(return_value={"status": "created"})), \
         patch.object(app.constants, "CARD_ON_SIGNUP_ENFORCED", False):
        # legacy behaviour: no block
        _run(sessions._check_subscription_active("org1"))


def test_entitling_status_never_blocked_even_with_flag_on():
    with patch.object(sessions, "get_org_subscription", AsyncMock(return_value={"status": "active"})), \
         patch.object(app.constants, "CARD_ON_SIGNUP_ENFORCED", True):
        _run(sessions._check_subscription_active("org1"))


def test_no_sub_row_does_not_block():
    with patch.object(sessions, "get_org_subscription", AsyncMock(return_value=None)), \
         patch.object(app.constants, "CARD_ON_SIGNUP_ENFORCED", True):
        _run(sessions._check_subscription_active("org1"))


@pytest.mark.parametrize("status,needle", [
    ("halted", "payment"),
    ("paused", "paused"),
    ("completed", "ended"),
])
def test_dead_statuses_block_with_clear_message(status, needle):
    """#9: halted/paused/completed drop the org to FREE_CAP in reconcile, so the
    status gate must block with a clear billing message rather than fall through
    to a confusing 'student limit reached'."""
    with patch.object(sessions, "get_org_subscription",
                      AsyncMock(return_value={"status": status})):
        with pytest.raises(HTTPException) as ei:
            _run(sessions._check_subscription_active("org1"))
        assert ei.value.status_code == 403
        assert needle in ei.value.detail.lower()

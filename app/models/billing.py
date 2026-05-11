from __future__ import annotations
from enum import StrEnum
from typing import Optional
from pydantic import BaseModel


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PlanTier(StrEnum):
    STARTER = "starter"
    GROWTH = "growth"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionOut(BaseModel):
    id: str
    org_id: str
    plan: str
    status: str
    trial_end: str | None = None
    current_period_end: str | None = None

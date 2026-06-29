"""API key management models."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    name: str
    key_prefix: str
    created_at: str | None = None
    last_used_at: str | None = None
    is_active: bool = True


class ApiKeyCreated(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    name: str
    key: str  # full key — shown only once at creation

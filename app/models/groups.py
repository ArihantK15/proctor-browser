from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class CreateGroupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    group_name: str


class RenameGroupIn(BaseModel):
    model_config = ConfigDict(strict=True)
    group_name: str


class GroupMembersIn(BaseModel):
    model_config = ConfigDict(strict=True)
    roll_numbers: list[str]


class ExamGroupAssignIn(BaseModel):
    model_config = ConfigDict(strict=True)
    group_ids: list[str]

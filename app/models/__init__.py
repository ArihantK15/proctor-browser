"""Domain enums and Pydantic models.

Split into domain files for maintainability.
All names are re-exported here for backward compatibility.
"""
from __future__ import annotations

from .teacher import TeacherSignupIn, TeacherLoginIn, RefreshIn, PasswordResetIn
from .student import (
    RegisterIn, ValidateIn, ResultIn, AnswerIn, BulkAnswerIn,
    StudentSignupIn, StudentLoginIn, BulkStudentIn,
)
from .exam import (
    SessionStatus, VerificationStatus,
    EventIn, FrameIn, IdVerifyIn, IdDecisionIn,
    ClearSessionsIn, EmailScorecardsIn, ScheduleIn, ShuffleIn,
    AccessCodeIn, BulkRegisterIn, CreateExamIn,
    UploadQuestionImageIn, SaveTemplateIn, DuplicateExamIn,
    ProctoringConfigIn,
)
from .invites import InviteStatus, InviteRecipient, SendInvitesBody
from .groups import CreateGroupIn, RenameGroupIn, GroupMembersIn, ExamGroupAssignIn
from .org import OrgRole, OrgInviteStatus, OrgInviteIn, OrgOut, OrgMemberOut, OrgInviteOut, OrgBillingOut
from .billing import SubscriptionStatus, PlanTier, SubscriptionOut
from .demo_request import *

__all__ = [
    # teacher
    "TeacherSignupIn", "TeacherLoginIn", "RefreshIn", "PasswordResetIn",
    # student
    "RegisterIn", "ValidateIn", "ResultIn", "AnswerIn", "BulkAnswerIn",
    "StudentSignupIn", "StudentLoginIn", "BulkStudentIn",
    # exam
    "SessionStatus", "VerificationStatus",
    "EventIn", "FrameIn", "IdVerifyIn", "IdDecisionIn",
    "ClearSessionsIn", "EmailScorecardsIn", "ScheduleIn", "ShuffleIn",
    "AccessCodeIn", "BulkRegisterIn", "CreateExamIn",
    "UploadQuestionImageIn", "SaveTemplateIn", "DuplicateExamIn",
    "ProctoringConfigIn",
    # invites
    "InviteStatus", "InviteRecipient", "SendInvitesBody",
    # groups
    "CreateGroupIn", "RenameGroupIn", "GroupMembersIn", "ExamGroupAssignIn",
    # org
    "OrgRole", "OrgInviteStatus", "OrgInviteIn", "OrgOut", "OrgMemberOut",
    "OrgInviteOut", "OrgBillingOut",
    # billing
    "SubscriptionStatus", "PlanTier", "SubscriptionOut",
]

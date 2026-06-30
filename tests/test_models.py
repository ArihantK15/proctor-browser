"""Tests for Pydantic model schemas (app/models/*.py).

Covers field validation, strict-mode rejection of coerced types,
default values, and key edge cases for every public model.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    RegisterIn, ValidateIn, ResultIn, AnswerIn, BulkAnswerIn,
    StudentSignupIn, BulkStudentIn,
    TeacherSignupIn, TeacherLoginIn, RefreshIn, PasswordResetIn,
    SessionStatus, VerificationStatus,
    LIVE_STATUSES, RESULT_STATUSES, TERMINAL_STATUSES, RECOVERABLE_STATUSES,
    EventIn, FrameIn, IdVerifyIn, IdDecisionIn,
    TeacherWarnIn, SessionTerminateIn,
    ClearSessionsIn, ScheduleIn, ShuffleIn,
    BulkRegisterIn, PassMarkIn, AttestIn,
    ID_REJECT_REASON_CODES, SESSION_END_REASON_CODES, TEACHER_WARN_CHIP_CODES,
    InviteStatus, InviteRecipient, SendInvitesBody,
    CreateGroupIn, GroupMembersIn, ExamGroupAssignIn,
    OrgRole, OrgInviteStatus, OrgOut, OrgMemberOut, OrgBillingOut,
    SubscriptionStatus, PlanTier, SubscriptionOut,
    ApiKeyCreate, ApiKeyOut, ApiKeyCreated,
    LtiRegistrationIn,
)
from app.models.exam import ContextFrame


class TestRegisterIn:
    def test_valid(self):
        m = RegisterIn(full_name="Alice", roll_number="R001", email="a@b.com")
        assert m.full_name == "Alice"
        assert m.email == "a@b.com"
        assert m.phone is None
        assert m.teacher_id is None
        assert m.exam_id is None
        assert m.batch is None
        assert m.date_of_birth is None
        assert m.guardian_email is None

    def test_strict_rejects_int_for_str(self):
        with pytest.raises(ValidationError):
            RegisterIn(full_name="Alice", roll_number=123, email="a@b.com")  # type: ignore[arg-type]

    def test_rejects_excess_length(self):
        with pytest.raises(ValidationError):
            RegisterIn(full_name="A" * 101, roll_number="R001", email="a@b.com")

    def test_rejects_long_email(self):
        with pytest.raises(ValidationError):
            RegisterIn(full_name="A", roll_number="R", email="x" * 255 + "@b.com")

    def test_optional_fields(self):
        m = RegisterIn(
            full_name="Bob", roll_number="R002", email="b@b.com",
            phone="+911234567890", exam_id="e1",
        )
        assert m.phone == "+911234567890"
        assert m.exam_id == "e1"


class TestValidateIn:
    def test_valid(self):
        m = ValidateIn(roll_number="R001")
        assert m.access_code is None
        assert m.exam_id is None

    def test_full(self):
        m = ValidateIn(roll_number="R001", access_code="abc", exam_id="e1")
        assert m.access_code == "abc"
        assert m.exam_id == "e1"


class TestResultIn:
    def test_minimal(self):
        m = ResultIn(
            session_id="s1", roll_number="R1",
            full_name="Alice", email="a@b.com",
            time_taken_secs=120, answers={}, score=0, total=0,
        )
        assert m.violations == []

    def test_with_violations(self):
        m = ResultIn(
            session_id="s1", roll_number="R1",
            full_name="Alice", email="a@b.com",
            time_taken_secs=120, answers={"q1": "A"},
            score=8, total=10, violations=[{"type": "gaze"}],
        )
        assert m.answers == {"q1": "A"}
        assert m.violations == [{"type": "gaze"}]


class TestAnswerIn:
    def test_valid(self):
        m = AnswerIn(session_id="s1", question_id="q1", answer="A")
        assert m.answer == "A"

    def test_empty_answer(self):
        m = AnswerIn(session_id="s1", question_id="q1", answer="")
        assert m.answer == ""


class TestBulkAnswerIn:
    def test_valid(self):
        m = BulkAnswerIn(session_id="s1", answers={"q1": "A", "q2": "B"})
        assert m.answers["q1"] == "A"


class TestStudentSignupIn:
    def test_valid(self):
        m = StudentSignupIn(email="a@b.com", password="secret", full_name="Alice")
        assert m.captcha_token is None

    def test_with_captcha(self):
        m = StudentSignupIn(email="a@b.com", password="secret", full_name="Alice", captcha_token="tok")
        assert m.captcha_token == "tok"


class TestBulkStudentIn:
    def test_valid(self):
        m = BulkStudentIn(students=[{"roll": "R1", "name": "Alice"}])
        assert m.students == [{"roll": "R1", "name": "Alice"}]


class TestTeacherSignupIn:
    def test_defaults(self):
        m = TeacherSignupIn(email="a@b.com", password="secret", full_name="Alice")
        assert m.org_name == ""
        assert m.account_type == "solo"
        assert m.captcha_token is None

    def test_org_account_type(self):
        m = TeacherSignupIn(email="a@b.com", password="secret", full_name="Alice", account_type="org")
        assert m.account_type == "org"

    def test_invalid_account_type(self):
        with pytest.raises(ValidationError):
            TeacherSignupIn(email="a@b.com", password="secret", full_name="Alice", account_type="admin")  # type: ignore[arg-type]


class TestTeacherLoginIn:
    def test_minimal(self):
        m = TeacherLoginIn(email="a@b.com", password="secret")
        assert m.captcha_token is None
        assert m.email_otp_code is None

    def test_with_otp(self):
        m = TeacherLoginIn(email="a@b.com", password="secret", email_otp_code="123456")
        assert m.email_otp_code == "123456"


class TestRefreshIn:
    def test_default(self):
        m = RefreshIn()
        assert m.refresh_token == ""

    def test_with_token(self):
        m = RefreshIn(refresh_token="tok")
        assert m.refresh_token == "tok"


class TestPasswordResetIn:
    def test_valid(self):
        m = PasswordResetIn(email="a@b.com")
        assert m.captcha_token is None

    def test_with_captcha(self):
        m = PasswordResetIn(email="a@b.com", captcha_token="tok")
        assert m.captcha_token == "tok"


class TestSessionStatus:
    def test_values(self):
        assert SessionStatus.IN_PROGRESS.value == "in_progress"
        assert SessionStatus.COMPLETED.value == "completed"
        assert SessionStatus.SUBMITTED.value == "submitted"
        assert SessionStatus.FORCE_SUBMITTED.value == "force_submitted"
        assert SessionStatus.ABANDONED.value == "abandoned"
        assert SessionStatus.REJECTED.value == "rejected"
        assert SessionStatus.PAUSED.value == "paused"

    def test_live_statuses(self):
        assert LIVE_STATUSES == {SessionStatus.IN_PROGRESS, SessionStatus.PAUSED}

    def test_result_statuses(self):
        assert RESULT_STATUSES == {SessionStatus.COMPLETED, SessionStatus.FORCE_SUBMITTED}

    def test_terminal_statuses(self):
        assert SessionStatus.COMPLETED in TERMINAL_STATUSES
        assert SessionStatus.SUBMITTED in TERMINAL_STATUSES
        assert SessionStatus.IN_PROGRESS not in TERMINAL_STATUSES

    def test_recoverable_statuses(self):
        assert RECOVERABLE_STATUSES == {SessionStatus.ABANDONED, SessionStatus.REJECTED}


class TestVerificationStatus:
    def test_values(self):
        assert VerificationStatus.PENDING.value == "pending"
        assert VerificationStatus.APPROVED.value == "approved"
        assert VerificationStatus.REJECTED.value == "rejected"


class TestEventIn:
    def test_minimal(self):
        m = EventIn(session_id="s1", event_type="gaze", severity="high")
        assert m.details is None
        assert m.detection_confidence is None
        assert m.att is None
        assert m.sig is None

    def test_full(self):
        m = EventIn(
            session_id="s1", event_type="gaze", severity="high",
            details="looked away", detection_confidence=0.95,
            att={"key": "val"}, sig="abc",
        )
        assert m.detection_confidence == 0.95
        assert m.att == {"key": "val"}


class TestContextFrame:
    def test_valid(self):
        m = ContextFrame(frame_b64="AAAA", offset_ms=3000)
        assert m.offset_ms == 3000

    def test_strict_rejects_str_offset(self):
        with pytest.raises(ValidationError):
            ContextFrame(frame_b64="AAAA", offset_ms="3000")  # type: ignore[arg-type]


class TestFrameIn:
    def test_minimal(self):
        m = FrameIn(session_id="s1", frame="AAAA", timestamp="2024-01-01T00:00:00Z")
        assert m.event_type is None
        assert m.context_frames is None

    def test_with_context(self):
        ctx = ContextFrame(frame_b64="BBBB", offset_ms=1000)
        m = FrameIn(
            session_id="s1", frame="AAAA", timestamp="now",
            event_type="tab_hidden", context_frames=[ctx],
        )
        assert m.event_type == "tab_hidden"
        assert m.context_frames is not None
        assert len(m.context_frames) == 1
        assert m.context_frames[0].offset_ms == 1000


class TestIdVerifyIn:
    def test_valid(self):
        m = IdVerifyIn(session_id="s1", roll_number="R1", selfie_frame="a", id_frame="b")
        assert m.full_name == ""
        assert m.timestamp == ""

    def test_full(self):
        m = IdVerifyIn(session_id="s1", roll_number="R1", selfie_frame="a", id_frame="b", full_name="Alice", timestamp="now")
        assert m.full_name == "Alice"


class TestIdDecisionIn:
    def test_minimal(self):
        m = IdDecisionIn(violation_id=1, session_key="s1", decision="retake")
        assert m.reason_code is None
        assert m.reason_text is None

    def test_full(self):
        m = IdDecisionIn(violation_id=1, session_key="s1", decision="reject",
                         reason_code="face_mismatch", reason_text="not the same person")
        assert m.reason_code == "face_mismatch"

    def test_strict_rejects_str_violation_id(self):
        with pytest.raises(ValidationError):
            IdDecisionIn(violation_id="1", session_key="s1", decision="retake")  # type: ignore[arg-type]


class TestTeacherWarnIn:
    def test_both_none(self):
        m = TeacherWarnIn()
        assert m.chip_code is None
        assert m.text is None

    def test_with_chip(self):
        m = TeacherWarnIn(chip_code="eyes_off_screen")
        assert m.chip_code == "eyes_off_screen"

    def test_with_text(self):
        m = TeacherWarnIn(text="Please focus")
        assert m.text == "Please focus"


class TestSessionTerminateIn:
    def test_defaults(self):
        m = SessionTerminateIn()
        assert m.reauth_token is None
        assert m.reason_code is None
        assert m.reason_text is None


class TestClearSessionsIn:
    def test_defaults(self):
        m = ClearSessionsIn()
        assert m.step == ""
        assert m.include_active is False


class TestScheduleIn:
    def test_minimal(self):
        m = ScheduleIn(exam_id="e1")
        assert m.starts_at is None
        assert m.early_join_minutes is None

    def test_with_early_join(self):
        m = ScheduleIn(exam_id="e1", starts_at="2024-01-01T00:00:00Z", early_join_minutes=15)
        assert m.early_join_minutes == 15


class TestShuffleIn:
    def test_valid(self):
        m = ShuffleIn(exam_id="e1", shuffle_questions=True)
        assert m.shuffle_questions is True
        assert m.shuffle_options is None


class TestBulkRegisterIn:
    def test_defaults(self):
        m = BulkRegisterIn(students=[{"roll": "R1"}])
        assert m.send_invites is True
        assert m.dry_run is False

    def test_dry_run(self):
        m = BulkRegisterIn(students=[{"roll": "R1"}], dry_run=True)
        assert m.dry_run is True
        assert m.send_invites is True


class TestAttestIn:
    def test_valid(self):
        m = AttestIn(att={"cmd": "open"}, sig="abc")
        assert m.att == {"cmd": "open"}
        assert m.sig == "abc"

    def test_strict_rejects_int_att(self):
        with pytest.raises(ValidationError):
            AttestIn(att=42, sig="abc")  # type: ignore[arg-type]


class TestPassMarkIn:
    def test_valid(self):
        m = PassMarkIn(exam_id="e1", pass_mark=40)
        assert m.pass_mark == 40

    def test_strict_rejects_str(self):
        with pytest.raises(ValidationError):
            PassMarkIn(exam_id="e1", pass_mark="40")  # type: ignore[arg-type]


class TestReasonCodes:
    def test_id_reject_reason_codes(self):
        assert "face_mismatch" in ID_REJECT_REASON_CODES
        assert "other" in ID_REJECT_REASON_CODES

    def test_session_end_reason_codes(self):
        assert "academic_dishonesty" in SESSION_END_REASON_CODES
        assert "other" in SESSION_END_REASON_CODES

    def test_teacher_warn_chip_codes(self):
        assert "eyes_off_screen" in TEACHER_WARN_CHIP_CODES
        assert "other" in TEACHER_WARN_CHIP_CODES


class TestInviteStatus:
    def test_values(self):
        assert InviteStatus.SENT.value == "sent"
        assert InviteStatus.OPENED.value == "opened"
        assert InviteStatus.ACCEPTED.value == "accepted"
        assert InviteStatus.BOUNCED.value == "bounced"
        assert InviteStatus.REVOKED.value == "revoked"


class TestSendInvitesBody:
    def test_defaults(self):
        m = SendInvitesBody(recipients=[], exam_id="e1")
        assert m.custom_message is None
        assert m.per_invite_code is True
        assert m.group_id is None

    def test_with_recipients(self):
        r = InviteRecipient(email="a@b.com", full_name="Alice", roll_number="R1")
        m = SendInvitesBody(recipients=[r], exam_id="e1")
        assert m.recipients[0].email == "a@b.com"
        assert m.recipients[0].full_name == "Alice"
        assert m.recipients[0].roll_number == "R1"


class TestCreateGroupIn:
    def test_valid(self):
        m = CreateGroupIn(group_name="Section A")
        assert m.group_name == "Section A"

    def test_strict_rejects_int(self):
        with pytest.raises(ValidationError):
            CreateGroupIn(group_name=42)  # type: ignore[arg-type]


class TestGroupMembersIn:
    def test_valid(self):
        m = GroupMembersIn(roll_numbers=["R1", "R2"])
        assert m.roll_numbers == ["R1", "R2"]


class TestExamGroupAssignIn:
    def test_valid(self):
        m = ExamGroupAssignIn(group_ids=["g1", "g2"])
        assert m.group_ids == ["g1", "g2"]


class TestOrgRole:
    def test_values(self):
        assert OrgRole.ADMIN.value == "admin"
        assert OrgRole.TEACHER.value == "teacher"
        assert OrgRole.SUPERADMIN.value == "superadmin"


class TestOrgInviteStatus:
    def test_values(self):
        assert OrgInviteStatus.PENDING.value == "pending"
        assert OrgInviteStatus.ACCEPTED.value == "accepted"
        assert OrgInviteStatus.EXPIRED.value == "expired"


class TestOrgOut:
    def test_valid(self):
        m = OrgOut(id="o1", name="Test Org", slug="test-org", max_students=100, created_at="now")
        assert m.slug == "test-org"
        assert m.max_students == 100


class TestOrgMemberOut:
    def test_minimal(self):
        m = OrgMemberOut(id="u1", email="a@b.com", full_name="Alice", org_role="teacher")
        assert m.created_at is None

    def test_full(self):
        m = OrgMemberOut(id="u1", email="a@b.com", full_name="Alice", org_role="admin", created_at="now")
        assert m.created_at == "now"


class TestOrgBillingOut:
    def test_defaults(self):
        m = OrgBillingOut(plan="starter", status="active")
        assert m.student_count == 0
        assert m.max_students == 30


class TestSubscriptionStatus:
    def test_values(self):
        assert SubscriptionStatus.TRIALING.value == "trialing"
        assert SubscriptionStatus.ACTIVE.value == "active"
        assert SubscriptionStatus.CANCELLED.value == "cancelled"


class TestPlanTier:
    def test_values(self):
        assert PlanTier.STARTER.value == "starter"
        assert PlanTier.GROWTH.value == "growth"
        assert PlanTier.PRO.value == "pro"
        assert PlanTier.ENTERPRISE.value == "enterprise"


class TestSubscriptionOut:
    def test_minimal(self):
        m = SubscriptionOut(id="sub1", org_id="o1", plan="pro", status="active")
        assert m.trial_end is None

    def test_full(self):
        m = SubscriptionOut(
            id="sub1", org_id="o1", plan="starter", status="trialing",
            trial_end="2024-02-01", current_period_end="2024-03-01",
        )
        assert m.trial_end == "2024-02-01"


class TestApiKeyCreate:
    def test_valid(self):
        m = ApiKeyCreate(name="My Key")
        assert m.name == "My Key"

    def test_strict_rejects_int(self):
        with pytest.raises(ValidationError):
            ApiKeyCreate(name=123)  # type: ignore[arg-type]


class TestApiKeyOut:
    def test_defaults(self):
        m = ApiKeyOut(id="k1", name="Key", key_prefix="abc_")
        assert m.is_active is True
        assert m.created_at is None

    def test_inactive(self):
        m = ApiKeyOut(id="k1", name="Key", key_prefix="abc_", is_active=False)
        assert m.is_active is False


class TestApiKeyCreated:
    def test_valid(self):
        m = ApiKeyCreated(id="k1", name="Key", key="abc_xyz")
        assert m.key == "abc_xyz"


class TestLtiRegistrationIn:
    def test_minimal(self):
        m = LtiRegistrationIn(
            issuer="https://canvas.instructure.com",
            client_id="1000",
            auth_login_url="https://canvas.instructure.com/api/lti/authorize_redirect",
            auth_token_url="https://canvas.instructure.com/login/oauth2/token",
            key_set_url="https://canvas.instructure.com/api/lti/security/jwks",
        )
        assert m.deployment_ids == []
        assert m.platform_name is None

    def test_full(self):
        m = LtiRegistrationIn(
            issuer="https://moodle.example.com",
            client_id="2000",
            auth_login_url="https://moodle.example.com/mod/lti/auth.php",
            auth_token_url="https://moodle.example.com/mod/lti/token.php",
            key_set_url="https://moodle.example.com/mod/lti/certs.php",
            deployment_ids=["d1", "d2"],
            platform_name="Moodle",
        )
        assert m.deployment_ids == ["d1", "d2"]
        assert m.platform_name == "Moodle"

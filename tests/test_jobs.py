"""Tests for the background job system (app.jobs).

Covers:
  1. ``enqueue_job`` — sync fallback path (RQ_ENABLED=0)
  2. ``_run_coro_in_sync`` — async→sync bridge
  3. All 5 email job functions — verify they call the right emailer methods
  4. RQ retry configuration
"""

import sys
from unittest.mock import MagicMock, patch, ANY

import pytest

sys.path.insert(0, __file__.rsplit("/", 2)[0])

# ── enqueue_job ─────────────────────────────────────────────────────


class TestEnqueueJob:
    """Tests for ``enqueue_job`` sync fallback (RQ_ENABLED=0)."""

    def test_sync_fallback_runs_function(self):
        from app.jobs import enqueue_job

        def dummy(**kw):
            return {"ok": True, "data": kw}

        result = enqueue_job(dummy, x=1, y=2)
        assert result == {"ok": True, "data": {"x": 1, "y": 2}}

    def test_sync_fallback_returns_result(self):
        from app.jobs import enqueue_job

        def dummy():
            return {"msg": "hello"}

        result = enqueue_job(dummy)
        assert result == {"msg": "hello"}

    def test_sync_fallback_propagates_exception(self):
        from app.jobs import enqueue_job

        def crash():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            enqueue_job(crash)

    def test_enqueue_job_passes_args_and_kwargs(self):
        from app.jobs import enqueue_job

        captured = {}

        def recorder(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"ok": True}

        enqueue_job(recorder, "a", "b", x=1)
        assert captured["args"] == ("a", "b")
        assert captured["kwargs"] == {"x": 1}

    @patch.dict("os.environ", {"RQ_ENABLED": "1"})
    def test_rq_enabled_returns_none(self):
        """When RQ_ENABLED=1, enqueue_job returns None (enqueued)."""
        # Queue/Redis are imported lazily inside enqueue_job, so we patch
        # the top-level module paths, not app.jobs.helpers.*
        with patch("rq.Queue") as MockQ, \
             patch("redis.Redis"):
            mock_queue = MockQ.return_value
            mock_queue.enqueue.return_value = None

            from app.jobs import enqueue_job

            def dummy():
                pass

            result = enqueue_job(dummy)
            assert result is None
            mock_queue.enqueue.assert_called_once()

    @patch.dict("os.environ", {"RQ_ENABLED": "1"})
    def test_rq_calls_enqueue_with_retry(self):
        """Verify retry config is passed through to the RQ Queue."""
        with patch("rq.Queue") as MockQ, \
             patch("redis.Redis"):
            from app.jobs import enqueue_job

            def dummy():
                pass

            enqueue_job(dummy, foo="bar")
            MockQ.return_value.enqueue.assert_called_once_with(
                dummy,
                foo="bar",
                retry=ANY,
            )
            # Verify Retry was constructed with expected args
            call_kwargs = MockQ.return_value.enqueue.call_args[1]
            retry = call_kwargs["retry"]
            assert retry.max == 3
            assert retry.intervals == [10, 60, 300]

    @patch.dict("os.environ", {"RQ_ENABLED": "1"})
    def test_rq_can_target_named_queue(self):
        """Autosave can be isolated from the default job queue."""
        with patch("rq.Queue") as MockQ, \
             patch("redis.Redis"):
            from app.jobs import enqueue_job

            def dummy():
                pass

            enqueue_job(dummy, queue_name="autosave")
            assert MockQ.call_args[0][0] == "autosave"


# ── _rq_enabled / _redis_url ────────────────────────────────────────


class TestHelpers:
    def test_rq_enabled_false_by_default(self):
        from app.jobs import _rq_enabled
        assert _rq_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "yes", "YES"])
    def test_rq_enabled_true_variants(self, val):
        with patch.dict("os.environ", {"RQ_ENABLED": val}):
            from app.jobs import _rq_enabled
            assert _rq_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "", "disabled"])
    def test_rq_enabled_false_variants(self, val):
        with patch.dict("os.environ", {"RQ_ENABLED": val}):
            from app.jobs import _rq_enabled
            assert _rq_enabled() is False

    def test_redis_url_default(self):
        from app.jobs import _redis_url
        with patch.dict("os.environ", {}, clear=True):
            url = _redis_url()
        assert url == "redis://localhost:6379/0"

    def test_redis_url_from_env(self):
        from app.jobs import _redis_url
        with patch.dict("os.environ", {"REDIS_URL": "redis://myhost:7777"}):
            assert _redis_url() == "redis://myhost:7777"

    def test_rq_retry_config_from_env(self):
        from app.jobs.helpers import _retry_max, _retry_intervals
        with patch.dict("os.environ", {
            "RQ_RETRY_MAX": "5",
            "RQ_RETRY_INTERVALS": "5,30,120,300",
        }):
            assert _retry_max() == 5
            assert _retry_intervals() == [5, 30, 120, 300]

    def test_rq_retry_defaults(self):
        from app.jobs.helpers import _retry_max, _retry_intervals
        with patch.dict("os.environ", {}, clear=True):
            assert _retry_max() == 3
            assert _retry_intervals() == [10, 60, 300]


# ── _run_coro_in_sync ───────────────────────────────────────────────


class TestRunCoroInSync:
    def test_runs_coro_and_returns_result(self):
        from app.jobs import _run_coro_in_sync

        async def echo(x):
            return x

        result = _run_coro_in_sync(echo(42))
        assert result == 42

    def test_propagates_exception(self):
        from app.jobs import _run_coro_in_sync

        async def crash():
            raise ValueError("broken")

        with pytest.raises(ValueError, match="broken"):
            _run_coro_in_sync(crash())

    def test_await_multiple_calls(self):
        from app.jobs import _run_coro_in_sync

        async def add(a, b):
            return a + b

        assert _run_coro_in_sync(add(1, 2)) == 3
        assert _run_coro_in_sync(add(10, 20)) == 30


# ── Email job functions ──────────────────────────────────────────────


class TestEmailJobFunctions:
    """Each email job function should call the corresponding emailer function
    and return a dict with ok/error keys."""

    @staticmethod
    def _mock_emailer():
        """Return the conftest mock_emailer instance from sys.modules.

        ``app`` is a namespace package (no ``__init__.py``), so
        ``patch("app.emailer")`` doesn't work.  Instead we import the
        already-mocked module via ``from app import emailer``, then use
        ``patch.object`` to override per-method return values.
        """
        from app import emailer as _mod
        return _mod

    def test_send_invite_email_job(self):
        mock_emailer = self._mock_emailer()
        with patch.object(mock_emailer, "send_invite_email",
                          return_value=MagicMock(ok=True, provider_msg_id="msg-1", error=None)):
            from app.jobs import send_invite_email_job

            result = send_invite_email_job(
                to_email="a@b.com", to_name="Alice",
                exam_title="Midterm", invite_url="https://example.com/invite/abc",
                download_url="https://example.com/download",
                roll_number="R001",
                registration_url="https://example.com/register?t=t1&e=e1",
                access_code="ABC123",
                exam_starts_at="1 Jan, 10:00",
                exam_ends_at="1 Jan, 11:00",
                custom_message="Be on time.",
                teacher_name="Prof",
            )
            assert result["ok"] is True
            assert result["provider_msg_id"] == "msg-1"
            mock_emailer.send_invite_email.assert_called_once_with(
                to_email="a@b.com", to_name="Alice",
                exam_title="Midterm", invite_url="https://example.com/invite/abc",
                download_url="https://example.com/download",
                roll_number="R001",
                registration_url="https://example.com/register?t=t1&e=e1",
                access_code="ABC123",
                exam_starts_at="1 Jan, 10:00",
                exam_ends_at="1 Jan, 11:00",
                custom_message="Be on time.",
                teacher_name="Prof",
            )

    def test_send_invite_email_job_failure_raises_for_retry(self):
        """Provider failure must raise EmailDeliveryError so RQ retries.

        Earlier this job returned ``{"ok": False}`` and let RQ mark the
        job as successful, which silently consumed the only delivery
        attempt for an invite. Raising puts the job through the retry
        policy and surfaces persistent failures in the RQ failed queue.
        """
        from app.jobs.email_jobs import EmailDeliveryError

        mock_emailer = self._mock_emailer()
        with patch.object(mock_emailer, "send_invite_email",
                          return_value=MagicMock(ok=False, provider_msg_id=None, error="SMTP timeout")):
            from app.jobs import send_invite_email_job

            with pytest.raises(EmailDeliveryError, match="SMTP timeout"):
                send_invite_email_job(
                    to_email="a@b.com", to_name="Alice",
                    exam_title="Midterm", invite_url="https://ex.co/i/abc",
                    download_url="https://ex.co/dl", roll_number="R001",
                )

    def test_send_demo_request_notification_job(self):
        mock_emailer = self._mock_emailer()
        with patch.object(mock_emailer, "send_demo_request_notification",
                          return_value=MagicMock(ok=True, provider_msg_id="msg-d", error=None)):
            from app.jobs import send_demo_request_notification_job

            result = send_demo_request_notification_job(
                name="Bob", email="bob@test.com",
                institution="MIT", role="teacher",
            )
            assert result["ok"] is True
            mock_emailer.send_demo_request_notification.assert_called_once()

    def test_send_org_invite_email_job(self):
        mock_emailer = self._mock_emailer()
        with patch.object(mock_emailer, "send_org_invite_email",
                          return_value=MagicMock(ok=True, provider_msg_id="msg-o", error=None)):
            from app.jobs import send_org_invite_email_job

            result = send_org_invite_email_job(
                to_email="org@test.com", invite_url="https://ex.co/invite",
                org_name="Test Org",
            )
            assert result["ok"] is True
            mock_emailer.send_org_invite_email.assert_called_once()

    def test_send_new_account_notification_job(self):
        mock_emailer = self._mock_emailer()
        with patch.object(mock_emailer, "send_new_account_notification",
                          return_value=MagicMock(ok=True, provider_msg_id="msg-n", error=None)):
            from app.jobs import send_new_account_notification_job

            result = send_new_account_notification_job(
                account_type="teacher", name="Carol", email="c@test.com",
            )
            assert result["ok"] is True
            mock_emailer.send_new_account_notification.assert_called_once()

    def test_send_scorecard_email_job(self):
        """The scorecard job is the most complex — it builds a PDF then emails."""
        mock_emailer = self._mock_emailer()
        with patch.object(mock_emailer, "send_scorecard_email",
                          return_value=MagicMock(ok=True, provider_msg_id="msg-s", error=None)), \
             patch("app.services.scorecard._build_scorecard_pdf") as mock_build:
            mock_build.return_value = (
                b"fake-pdf-bytes",
                "scorecard_R001.pdf",
                {"exam_title": "Midterm", "score": 8, "total": 10,
                 "percentage": 80.0, "passed": True},
            )
            from app.jobs import send_scorecard_email_job

            result = send_scorecard_email_job(
                session_key="sess-1", teacher_id="t-1",
                email="stu@test.com", full_name="Student",
                teacher_name="Prof",
            )
            assert result["ok"] is True
            mock_build.assert_called_once_with("sess-1", "t-1")
            mock_emailer.send_scorecard_email.assert_called_once_with(
                to_email="stu@test.com", to_name="Student",
                exam_title="Midterm", score=8, total=10,
                percentage=80.0, passed=True,
                pdf_bytes=b"fake-pdf-bytes", pdf_filename="scorecard_R001.pdf",
                teacher_name="Prof", custom_message=None,
            )


# ── health check ──────────────────────────────────────────────────


class TestWorkerHealth:
    """Verify the API provides a way to check if the worker is responsive."""

    def test_metrics_endpoint_available(self, client, admin_headers):
        """The /api/v1/metrics endpoint (requires admin auth) can be used
        to confirm the API is alive; the worker has no separate endpoint."""
        from unittest.mock import patch, AsyncMock
        with patch("app.auth.admin_auth._get_teacher_by_id", new_callable=AsyncMock) as m:
            m.return_value = {"id": "teacher-1", "email": "prof@test.com", "full_name": "Prof", "org_id": "org-1", "org_role": "admin"}
            resp = client.get("/api/v1/metrics", headers=admin_headers)
            assert resp.status_code == 200
            body = resp.json()
            assert "proctor_uptime_seconds" in body
            assert "proctor_requests_total" in body

    def test_health_available(self, client):
        resp = client.get("/health")
        # The health endpoint should respond even without auth. It may return
        # 503 in test/dev when optional providers such as email are not set.
        assert resp.status_code in (200, 404, 503)


# ── AGS grade passback tenant isolation ────────────────────────────────

class TestAgsTenantScoping:
    """Pin the cross-tenant defense in `_try_ags_grade_passback`.

    Two teachers can each have a `students` row with the same
    roll_number (the composite unique key is (roll_number, teacher_id)).
    The students table lookup MUST filter by teacher_id; otherwise a
    `.limit(1)` would return an arbitrary teacher's row and the grade
    would be pushed to that teacher's LMS user — cross-tenant data +
    grade corruption.
    """

    @pytest.mark.asyncio
    async def test_bails_without_teacher_id(self):
        """Missing teacher_id must short-circuit BEFORE the unscoped
        students lookup. Earlier the function fell through to
        .eq("roll_number", roll).limit(1) which could match a different
        teacher's row."""
        from app.routers.exam import _try_ags_grade_passback

        with patch("app.routers.exam._atable") as m_atable:
            await _try_ags_grade_passback(
                "STU001", 8, 10, 80.0,
                teacher_id=None,
            )
            # _atable should never have been called — we bailed before
            # the lookup. If teacher_id=None ever silently fell back to
            # an unscoped query, m_atable would have been invoked.
            m_atable.assert_not_called()

    @pytest.mark.asyncio
    async def test_students_query_includes_teacher_id_filter(self):
        """Verify the students lookup chain hits eq("teacher_id", ...)."""
        from app.routers.exam import _try_ags_grade_passback

        # Mock _atable so we can inspect the chained .eq() calls.
        select_chain = MagicMock()
        select_chain.eq = MagicMock(return_value=select_chain)
        select_chain.limit = MagicMock(return_value=select_chain)

        async def _execute():
            return MagicMock(data=[])  # no LTI user → silent return
        select_chain.execute = _execute

        table = MagicMock()
        table.select = MagicMock(return_value=select_chain)

        with patch("app.routers.exam._atable", return_value=table):
            await _try_ags_grade_passback(
                "STU001", 8, 10, 80.0,
                teacher_id="teacher-1",
            )

        # The first .eq() should be on roll_number, the second on teacher_id.
        eq_calls = select_chain.eq.call_args_list
        cols = [c.args[0] for c in eq_calls]
        assert "teacher_id" in cols, (
            "students lookup must include teacher_id filter to prevent "
            "cross-tenant roll-number collisions from routing grades to "
            "the wrong LMS user"
        )

    @pytest.mark.asyncio
    async def test_skips_passback_when_total_zero(self):
        """total=0 would send scoreMaximum=0, which AGS (Canvas/Moodle) rejects
        with a 4xx → under raise_on_failure RQ would retry forever. Must skip
        (no post_score) rather than push an invalid payload."""
        from unittest.mock import AsyncMock
        from app.routers.exam import _try_ags_grade_passback

        select_chain = MagicMock()
        select_chain.eq = MagicMock(return_value=select_chain)
        select_chain.limit = MagicMock(return_value=select_chain)

        async def _execute():
            return MagicMock(data=[{"lti_user_id": "iss1|sub1"}])
        select_chain.execute = _execute
        table = MagicMock()
        table.select = MagicMock(return_value=select_chain)

        reg = MagicMock()
        reg.client_id = "cid"
        reg.auth_token_url = "https://lms/token"
        reg.issuer = "iss1"

        async def _get_token(**kwargs):
            return "tok"
        post_score_mock = AsyncMock(return_value=True)

        with patch("app.routers.exam._atable", return_value=table), \
             patch("app.lti.launch.get_lti_student_context",
                   return_value={"iss": "iss1", "sub": "sub1",
                                 "deployment_id": "d1", "client_id": "cid"}), \
             patch("app.lti.launch.get_ags_context",
                   return_value={"ags_lineitems": "https://lms/li",
                                 "ags_scope": ["https://purl.imsglobal.org/spec/lti-ags/scope/score"]}), \
             patch("app.lti.registration.find_registration", return_value=reg), \
             patch("app.lti.ags.get_access_token", _get_token), \
             patch("app.lti.ags.post_score", post_score_mock):
            await _try_ags_grade_passback("STU001", 0, 0, 0.0, teacher_id="teacher-1")

        post_score_mock.assert_not_called()

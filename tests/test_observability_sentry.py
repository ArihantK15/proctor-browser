"""The shared Sentry PII scrubber (app/observability.scrub_sentry_event) is the
single guard standing between proctoring data (OTPs, answers, roll numbers,
recipient emails) and a third-party error host. It is used by BOTH the API and
the RQ worker, so these tests lock its behavior for every component at once.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.observability import scrub_sentry_event, SAFE_SENTRY_KWARGS


def test_redacts_auth_and_cookie_headers_case_insensitively():
    ev = {"request": {"headers": {"Authorization": "Bearer secret", "X-Reauth-Token": "t", "Accept": "json"}}}
    scrub_sentry_event(ev, None)
    h = ev["request"]["headers"]
    assert h["Authorization"] == "[REDACTED]"
    assert h["X-Reauth-Token"] == "[REDACTED]"
    assert h["Accept"] == "json"   # non-sensitive header preserved


def test_redacts_query_string_and_url_params():
    ev = {"request": {
        "query_string": "otp=123456&exam=abc",
        "url": "https://x/api?token=zzz&page=2",
    }}
    scrub_sentry_event(ev, None)
    assert "123456" not in ev["request"]["query_string"]
    assert "exam=abc" in ev["request"]["query_string"]
    assert "zzz" not in ev["request"]["url"]
    assert "page=2" in ev["request"]["url"]


def test_redacts_pii_keys_in_dict_body_recursively():
    ev = {"request": {"data": {"email": "a@b.com", "answers": ["A", "B"],
                                "nested": {"password": "p"}, "exam_id": "e1"}}}
    scrub_sentry_event(ev, None)
    d = ev["request"]["data"]
    assert d["email"] == "[REDACTED]"
    assert d["answers"] == "[REDACTED]"
    assert d["nested"]["password"] == "[REDACTED]"
    assert d["exam_id"] == "e1"   # non-PII preserved


def test_parses_and_scrubs_json_string_body():
    ev = {"request": {"data": '{"otp": "999", "exam_id": "e1"}'}}
    scrub_sentry_event(ev, None)
    assert ev["request"]["data"]["otp"] == "[REDACTED]"
    assert ev["request"]["data"]["exam_id"] == "e1"


def test_redacts_unparseable_body_that_smells_of_pii():
    ev = {"request": {"data": "roll_number=ABC123 garbage not json"}}
    scrub_sentry_event(ev, None)
    assert ev["request"]["data"] == "[REDACTED — contained PII]"


def test_scrubs_exception_value_string():
    ev = {"exception": {"values": [{"value": "invalid otp=123456 for user"}]}}
    scrub_sentry_event(ev, None)
    assert "123456" not in ev["exception"]["values"][0]["value"]


def test_scrubs_extra_dict_job_context():
    # The worker may attach extras; PII-shaped keys there must be redacted too.
    ev = {"extra": {"email": "a@b.com", "job_arg_count": 3}}
    scrub_sentry_event(ev, None)
    assert ev["extra"]["email"] == "[REDACTED]"
    assert ev["extra"]["job_arg_count"] == 3


def test_non_http_worker_event_passes_through_safely():
    # A bare worker exception event (no `request`) must not raise.
    ev = {"exception": {"values": [{"value": "boom"}]}, "level": "error"}
    out = scrub_sentry_event(ev, None)
    assert out["level"] == "error"


def test_safe_defaults_disable_pii_and_locals():
    assert SAFE_SENTRY_KWARGS["send_default_pii"] is False
    assert SAFE_SENTRY_KWARGS["include_local_variables"] is False
    assert SAFE_SENTRY_KWARGS["max_request_body_size"] == "small"

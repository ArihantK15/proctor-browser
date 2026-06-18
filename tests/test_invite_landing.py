"""Fixtures for invite-landing HTML rendering (services/invite_landing.py).

The landing page interpolates student/exam-supplied values into HTML, so
the security-critical property is that EVERY dynamic value is
HTML-escaped — a roll number or exam title containing markup must not
break out into executable HTML (stored XSS). These tests inject a
script/quote payload into each field and assert the raw payload never
appears unescaped in the output.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.invite_landing import (
    _render_invite_landing, _render_invite_error, _invite_registration,
)

XSS = '<script>alert(1)</script>"'


def _render(**overrides):
    kw = dict(token="tok", full_name="Asha", exam_title="Midterm",
              roll_number="R1", access_code="CODE", starts_at="", ends_at="",
              registration_url="")
    kw.update(overrides)
    return _render_invite_landing(**kw)


def test_payload_in_each_field_is_escaped():
    for field in ("full_name", "exam_title", "roll_number", "access_code", "token"):
        html = _render(**{field: XSS})
        assert "<script>alert(1)</script>" not in html, f"{field} not escaped"
        assert "&lt;script&gt;" in html


def test_registration_url_escaped_when_present():
    html = _render(registration_url='http://x/"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in html
    assert 'href="http://x/&quot;&gt;' in html


def test_empty_registration_url_omits_section():
    assert _invite_registration("") == ""
    assert "Need to register first?" not in _render(registration_url="")


def test_access_code_section_conditional():
    with_code = _render(access_code="ABC")
    without_code = _render(access_code="")
    assert "Access code" in with_code
    assert "Access code" not in without_code
    # steps text adapts to whether a code is shown
    assert "and access code" in with_code
    assert "and access code" not in without_code


def test_error_page_escapes_message():
    html = _render_invite_error(XSS)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "Invite unavailable" in html


def test_landing_references_external_script_not_inline():
    """CSP forbids inline scripts on this route — the JS must be an
    external same-origin reference, with no inline handler block."""
    html = _render()
    assert '<script src="/static/invite-landing.js"' in html
    # no inline script body (a bare <script> followed by JS, not src=)
    assert "<script>" not in html

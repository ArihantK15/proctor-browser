from starlette.requests import Request

from app.auth.tokens import create_token
from app.limiter import _rate_limit_key


def _request(headers=None, client_host="203.0.113.10"):
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/save-answers-bulk",
        "headers": raw_headers,
        "client": (client_host, 44321),
    })


def test_rate_limit_key_uses_valid_jwt_identity_before_ip():
    token_a = create_token("ROLL001", teacher_id="teacher-1", exam_id="exam-1")
    token_b = create_token("ROLL002", teacher_id="teacher-1", exam_id="exam-1")

    key_a = _rate_limit_key(_request({"Authorization": f"Bearer {token_a}"}))
    key_b = _rate_limit_key(_request({"Authorization": f"Bearer {token_b}"}))

    assert key_a.startswith("jwt:")
    assert key_b.startswith("jwt:")
    assert key_a != key_b


def test_rate_limit_key_falls_back_to_ip_for_invalid_jwt():
    req = _request({"Authorization": "Bearer not-a-valid-token"})

    assert _rate_limit_key(req) == "203.0.113.10"

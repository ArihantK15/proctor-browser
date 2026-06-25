"""Authoring-time validation for coding questions (forensic audit, item 3).

These are pure validators (no DB/auth) so they run in the normal unit suite.
Two gaps fixed here:
  * a non-numeric float_tolerance must be a clean 400, not a 500 from a raw
    ValueError escaping _clean_cases before the endpoint's try-block.
  * input / expected_output must be size-capped — 50 cases * megabytes each is
    a storage/DoS vector (execsvc already caps run-time payloads).
"""
import pytest
from fastapi import HTTPException

from app.routers.admin_coding import _clean_cases, _clean_options, MAX_FIELD_LEN


def test_non_numeric_float_tolerance_is_400_not_500():
    with pytest.raises(HTTPException) as ei:
        _clean_cases([{"visibility": "hidden", "expected_output": "x", "float_tolerance": "abc"}])
    assert ei.value.status_code == 400


def test_oversized_expected_output_rejected():
    big = "y" * (MAX_FIELD_LEN + 1)
    with pytest.raises(HTTPException) as ei:
        _clean_cases([{"visibility": "hidden", "expected_output": big}])
    assert ei.value.status_code == 400


def test_oversized_input_rejected():
    big = "y" * (MAX_FIELD_LEN + 1)
    with pytest.raises(HTTPException) as ei:
        _clean_cases([{"visibility": "hidden", "expected_output": "x", "input": big}])
    assert ei.value.status_code == 400


def test_c_cpp_java_supported_for_authoring():
    """v1 language set is the full six the runner supports — a teacher must be
    able to author C / C++ / Java questions, not only JS/TS/Python."""
    for lang in ("c", "cpp", "java", "python", "javascript", "typescript"):
        out = _clean_options({"allowed_languages": [lang]})
        assert out["allowed_languages"] == [lang]


def test_language_aliases_normalized_to_canonical():
    """Authoring layer may send aliases; store the canonical key the runner +
    student client agree on (execsvc keys on cpp/javascript/typescript)."""
    assert _clean_options({"allowed_languages": ["c++"]})["allowed_languages"] == ["cpp"]
    assert _clean_options({"allowed_languages": ["js"]})["allowed_languages"] == ["javascript"]
    assert _clean_options({"allowed_languages": ["ts"]})["allowed_languages"] == ["typescript"]


def test_unknown_language_still_rejected():
    with pytest.raises(HTTPException) as ei:
        _clean_options({"allowed_languages": ["cobol"]})
    assert ei.value.status_code == 400


def test_blank_starter_template_is_preserved():
    """A teacher may intentionally clear a language's starter to "" — keep it.
    (Truthiness on the value would wrongly drop the empty string.)"""
    out = _clean_options({
        "allowed_languages": ["python", "javascript"],
        "starter_code": {"python": "", "javascript": "console.log(1)"},
    })
    assert out["starter_code"] == {"python": "", "javascript": "console.log(1)"}


def test_missing_starter_language_is_dropped():
    out = _clean_options({
        "allowed_languages": ["python", "javascript"],
        "starter_code": {"python": "print(1)"},   # javascript intentionally absent
    })
    assert out["starter_code"] == {"python": "print(1)"}


def test_valid_cases_pass_through():
    out = _clean_cases([
        {"visibility": "hidden", "expected_output": "42", "input": "1 2"},
        {"visibility": "sample", "expected_output": "ok", "input": "", "float_tolerance": 0.01},
    ])
    assert out[0]["expected_output"] == "42"
    assert out[1]["float_tolerance"] == 0.01

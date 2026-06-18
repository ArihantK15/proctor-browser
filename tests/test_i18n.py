"""Fixtures for the i18n string table + t() helper (services/i18n.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.i18n import t, _STRINGS


def test_known_key_returns_string():
    assert t("already_submitted") == "You have already submitted this exam."


def test_unknown_key_falls_back_to_key():
    assert t("does_not_exist") == "does_not_exist"


def test_interpolation():
    assert t("history_load_failed", msg="timeout") == "Failed to load: timeout"


def test_missing_kwarg_returns_template_unformatted():
    # exam_not_started needs {starts_at}; with none given it must not crash.
    out = t("exam_not_started")
    assert out == _STRINGS["exam_not_started"]


def test_bad_format_args_do_not_crash():
    # Extra/irrelevant kwargs are harmless; a template with no placeholders
    # ignores them.
    assert t("loading", foo="bar") == "Loading..."


def test_all_templates_format_cleanly_with_their_placeholders():
    """Every template's named placeholders must be valid identifiers so a
    correct call site can always format it (guards against typo'd braces)."""
    import string
    fmt = string.Formatter()
    for key, tmpl in _STRINGS.items():
        names = [fname for _, fname, _, _ in fmt.parse(tmpl) if fname]
        kwargs = {n: "x" for n in names}
        # must not raise
        t(key, **kwargs)

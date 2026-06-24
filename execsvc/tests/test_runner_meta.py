"""Meta-file parsing + numeric coercion robustness (runs everywhere — no
isolate needed). A corrupt isolate meta file must DEGRADE a single run's
result, never crash (and thus 500) the whole grading job."""
import os
import tempfile

from execsvc.runner import _parse_meta, _safe_int, _safe_float


def test_safe_int_degrades_on_garbage():
    assert _safe_int("7", 0) == 7
    assert _safe_int("garbage", -1) == -1
    assert _safe_int(None, 0) == 0
    assert _safe_int("", 5) == 5


def test_safe_float_degrades_on_garbage():
    assert _safe_float("0.523", 0.0) == 0.523
    assert _safe_float("nan-ish", 0.0) == 0.0
    assert _safe_float(None, 1.0) == 1.0


def test_parse_meta_handles_colon_values_and_blank_lines():
    # isolate meta lines can carry a colon in the VALUE (e.g. a message/path).
    content = "status:TO\nmessage:Time limit exceeded: foo:bar\ntime:0.5\n\nexitcode:0\n"
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        with open(path, "w") as f:
            f.write(content)
        m = _parse_meta(path)
    finally:
        os.unlink(path)
    assert m["status"] == "TO"
    assert m["message"] == "Time limit exceeded: foo:bar"  # split once, keep rest
    assert m["time"] == "0.5"
    assert m["exitcode"] == "0"


def test_parse_meta_missing_file_returns_empty():
    assert _parse_meta("/no/such/meta/file") == {}

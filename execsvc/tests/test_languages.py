from execsvc.languages import LANGUAGES, lang_spec


def test_python_spec_has_run_and_no_compile():
    s = lang_spec("python")
    assert s.source_filename == "main.py"
    assert s.compile_cmd is None
    assert s.run_cmd == ["python3", "main.py"]


def test_unknown_language_raises():
    import pytest

    with pytest.raises(KeyError):
        lang_spec("brainfuck")

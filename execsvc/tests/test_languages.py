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


def test_all_v1_languages_present():
    for lang in ("python", "javascript", "typescript", "c", "cpp", "java"):
        assert lang in LANGUAGES, lang


def test_compiled_languages_have_compile_then_run():
    c = lang_spec("c")
    assert c.compile_cmd and c.compile_cmd[0] == "gcc"
    assert c.run_cmd == ["./main"]
    java = lang_spec("java")
    assert java.source_filename == "Main.java"          # class Main
    # javac compiles Main.java, java runs class Main (JVM memory flags between).
    assert java.compile_cmd[0] == "javac" and java.compile_cmd[-1] == "Main.java"
    assert java.run_cmd[0] == "java" and java.run_cmd[-1] == "Main"


def test_aliases_resolve():
    assert lang_spec("js") is lang_spec("javascript")
    assert lang_spec("c++") is lang_spec("cpp")

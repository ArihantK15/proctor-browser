import shutil
import pytest
from execsvc.runner import run_in_isolate
from execsvc.isolate_cmd import Limits

pytestmark = pytest.mark.skipif(shutil.which("isolate") is None, reason="isolate not installed")
L = Limits(cpu_ms=2000, wall_ms=4000, mem_mb=256, output_kb=64)


def test_python_hello():
    r = run_in_isolate("python", 'print("Hello, World!")', stdin="", limits=L)
    assert r.stdout.strip() == "Hello, World!"
    assert r.exit_code == 0 and not r.timed_out


def test_stdin_echo():
    r = run_in_isolate("python", "import sys; print(sys.stdin.readline().strip())", stdin="ping\n", limits=L)
    assert r.stdout.strip() == "ping"


def test_infinite_loop_times_out():
    r = run_in_isolate("python", "while True: pass", stdin="", limits=L)
    assert r.timed_out


def test_javascript_hello():
    r = run_in_isolate("javascript", 'console.log("hi-js")', stdin="", limits=L)
    assert r.stdout.strip() == "hi-js"
    assert r.exit_code == 0 and not r.timed_out


def test_c_compile_and_run():
    src = '#include <stdio.h>\nint main(){printf("hi-c\\n");return 0;}'
    r = run_in_isolate("c", src, stdin="", limits=L)
    assert r.compile_error is None
    assert r.stdout.strip() == "hi-c" and r.exit_code == 0


def test_cpp_compile_and_run():
    src = '#include <iostream>\nint main(){std::cout << "hi-cpp" << std::endl; return 0;}'
    r = run_in_isolate("cpp", src, stdin="", limits=L)
    assert r.compile_error is None
    assert r.stdout.strip() == "hi-cpp" and r.exit_code == 0


def test_java_compile_and_run():
    src = ('public class Main { public static void main(String[] a) {'
           ' System.out.println("hi-java"); } }')
    r = run_in_isolate("java", src, stdin="", limits=L)
    assert r.compile_error is None
    assert r.stdout.strip() == "hi-java" and r.exit_code == 0


def test_c_compile_error_surfaces():
    r = run_in_isolate("c", "int main(){ this is not valid c }", stdin="", limits=L)
    assert r.compile_error is not None  # gcc failed; non-zero compile return

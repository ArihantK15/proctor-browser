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

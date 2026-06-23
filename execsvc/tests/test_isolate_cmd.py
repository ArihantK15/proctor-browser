from execsvc.isolate_cmd import run_args, Limits


def test_limits_map_to_flags():
    a = run_args(box_id=3, limits=Limits(cpu_ms=2000, wall_ms=5000, mem_mb=256, output_kb=64),
                 cmd=["python3", "main.py"])
    s = " ".join(a)
    assert "--box-id=3" in s
    assert "--time=2" in s and "--wall-time=5" in s   # seconds
    assert "--mem=262144" in s                         # KB
    assert "--processes" in s and "--no-default-dirs" not in s
    assert s.endswith("python3 main.py")
    assert "--share-net" not in s                      # NO network, ever


def test_validated_host_flags_present():
    # Locked in from the live-server validation (2026-06-23): cgroup mode + a PATH
    # (so compilers find ld) + HOME (javac). Without these, compiled langs break.
    s = " ".join(run_args(0, Limits(1000, 2000, 128, 64), ["gcc", "main.c"]))
    assert "--cg" in s
    assert "--env=PATH=/usr/bin:/usr/local/bin:/bin" in s
    assert "--env=HOME=/box" in s

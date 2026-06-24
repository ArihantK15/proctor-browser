import shutil
import subprocess
import tempfile
import os
from dataclasses import dataclass, replace
from typing import Optional
from .languages import lang_spec
from .isolate_cmd import run_args, Limits


def _abs(cmd: list) -> list:
    """Resolve the program (cmd[0]) to an absolute path.

    isolate execve()s the command directly and does NOT do a PATH lookup —
    `--env=PATH=...` only affects the environment *inside* the program, not how
    isolate finds it. So a bare `python3`/`node`/`gcc`/`java` fails with
    `execve("python3"): No such file or directory` (status 127) even though it's
    on PATH. The host and the box share /usr/bin, so resolving on the host yields
    a path valid inside the box. Box-local programs (`./main` for C/C++) are not
    on the host PATH → `which` returns None → left unchanged, which is correct.
    """
    if not cmd:
        return cmd
    resolved = shutil.which(cmd[0])
    if resolved:
        # Dereference symlinks: javac/java are /usr/bin → /etc/alternatives → JDK
        # symlink chains, and isolate can't follow a chain that crosses dirs the
        # box doesn't bind → execve("/usr/bin/javac"): No such file. The real
        # binary lives under /usr (bound by default), so realpath resolves it.
        resolved = os.path.realpath(resolved)
    return [resolved or cmd[0], *cmd[1:]]


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    time_ms: int
    timed_out: bool
    oom: bool
    compile_error: Optional[str]


def _safe_int(val, default: int) -> int:
    """Coerce an isolate-meta value to int; a corrupt/missing value degrades to
    `default` instead of raising (one bad meta must not 500 the grade)."""
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


def _safe_float(val, default: float) -> float:
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return default


def _extra_dirs(language: str) -> list:
    """Read-only binds a language's runtime needs beyond the box's default dirs.

    Java: OpenJDK's conf/ dir is real (under /usr, already bound) but its FILES
    (java.security, logging.properties, …) are SYMLINKS to /etc/java-NN-openjdk on
    Debian/Ubuntu. The box binds /usr but not that /etc target, so the JVM fails
    with "Error loading java.security file". Resolve a known config file off the
    `java` binary and bind its /etc/java-NN-openjdk root read-only.
    """
    if language.lower() == "java":
        jbin = shutil.which("java")
        if jbin:
            home = os.path.dirname(os.path.dirname(os.path.realpath(jbin)))
            sec = os.path.realpath(os.path.join(home, "conf", "security", "java.security"))
            if os.path.isfile(sec):
                etc_dir = os.path.dirname(os.path.dirname(sec))   # /etc/java-NN-openjdk
                if os.path.isdir(etc_dir):
                    return [etc_dir]
    return []


def run_in_isolate(language: str, source: str, stdin: str, limits: Limits, box_id: int = 0) -> ExecResult:
    """Run `source` for `language` inside a single `isolate` sandbox box.

    Real implementation — exercised only when `isolate` is installed (Linux
    host with the IOI isolate sandbox). On this macOS dev machine the calling
    tests are skipped via `pytest.mark.skipif(shutil.which("isolate") is None, ...)`
    so this body never executes here, but it is concrete and correct per the
    plan for the real Linux/KVM execution host.
    """
    spec = lang_spec(language)
    # Raise the memory cap to the language's floor if it needs one (the JVM
    # reserves far more virtual address space than the sandbox's default cap
    # allows). Applies to both compile and run below.
    if spec.min_mem_mb and limits.mem_mb < spec.min_mem_mb:
        limits = replace(limits, mem_mb=spec.min_mem_mb)
    extra_dirs = _extra_dirs(language)
    # --cg is required on init/cleanup too (cgroup-v2 mode is set up at init);
    # validated on the Hostinger host. Without it, --cg --run mismatches the box.
    subprocess.run(["isolate", "--cg", f"--box-id={box_id}", "--init"], check=True, capture_output=True)
    box_dir = f"/var/local/lib/isolate/{box_id}/box"
    meta_files: list[str] = []

    def _new_meta() -> str:
        # mkstemp (not mktemp): no predictable-path race, and we track every
        # file so the finally-block deletes it — otherwise each run leaks a
        # /tmp meta file forever.
        fd, path = tempfile.mkstemp(prefix="isolate-meta-")
        os.close(fd)
        meta_files.append(path)
        return path

    try:
        with open(os.path.join(box_dir, spec.source_filename), "w") as f:
            f.write(source)
        compile_error = None
        if spec.compile_cmd:
            cp = subprocess.run(run_args(box_id, limits, _abs(spec.compile_cmd), _new_meta(), extra_dirs),
                                 input="", capture_output=True, text=True)
            if cp.returncode != 0:
                return ExecResult("", "", cp.returncode, 0, False, False, cp.stdout + cp.stderr)
        meta = _new_meta()
        rp = subprocess.run(run_args(box_id, limits, _abs(spec.run_cmd), meta, extra_dirs),
                             input=stdin, capture_output=True, text=True)
        m = _parse_meta(meta)
        return ExecResult(
            rp.stdout, rp.stderr,
            _safe_int(m.get("exitcode", rp.returncode), rp.returncode),
            int(_safe_float(m.get("time", 0), 0.0) * 1000),
            m.get("status") == "TO",
            m.get("status") == "SG" and "memory" in m.get("message", ""),
            compile_error,
        )
    finally:
        for p in meta_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        subprocess.run(["isolate", "--cg", f"--box-id={box_id}", "--cleanup"], capture_output=True)


def _parse_meta(path: str) -> dict:
    try:
        with open(path) as f:
            return dict(line.split(":", 1) for line in f.read().splitlines() if ":" in line)
    except FileNotFoundError:
        return {}

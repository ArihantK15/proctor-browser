from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    cpu_ms: int
    wall_ms: int
    mem_mb: int
    output_kb: int


def run_args(box_id: int, limits: Limits, cmd: list, meta: str | None = None,
             extra_dirs: list | None = None) -> list:
    # Validated on the Hostinger box 2026-06-23 (see
    # docs/runbooks/2026-06-23-isolate-sandbox-hostinger.md):
    #   --cg              cgroup-v2 mode — isolate 2.6 needs it for mem/pid caps.
    #   --share-net ABSENT  no network (verified: connect() raises in-sandbox).
    #   --env=PATH        isolate wipes the env, so compilers can't find ld/as/
    #                     collect2 without PATH (gcc failed "cannot find 'ld'"
    #                     until set). /usr/local/bin covers npm-global tools (tsc).
    #   --env=HOME=/box   javac and friends need a writable HOME.
    # NOTE: do NOT pass --stderr-to-stdout. In isolate 2.6 it is a BOOLEAN flag
    # (no argument) whose mere presence MERGES the program's stderr into stdout;
    # `--stderr-to-stdout=0` is rejected outright ("doesn't allow an argument"),
    # making isolate print usage and run nothing. We capture stdout/stderr
    # separately via subprocess, so we simply omit it (the default = separate).
    args = [
        "isolate", f"--box-id={box_id}", "--cg", "--run",
        f"--time={limits.cpu_ms / 1000:g}",
        f"--wall-time={limits.wall_ms / 1000:g}",
        f"--mem={limits.mem_mb * 1024}",
        f"--fsize={limits.output_kb}",
        "--processes=64",
        "--env=PATH=/usr/bin:/usr/local/bin:/bin",
        "--env=HOME=/box",
    ]
    # Extra read-only binds for runtimes whose config lives outside the box's
    # default-bound dirs (e.g. the JDK's conf/ is a symlink to /etc/java-*-openjdk
    # on Debian, which /usr binding doesn't cover → "Error loading java.security").
    for d in (extra_dirs or []):
        args.append(f"--dir={d}")
    # --meta is an ISOLATE flag and MUST come before the `--` separator. Appended
    # after `--` it would be handed to the program (gcc rejects it; the meta file
    # is never written, so timeout/oom/exit detection silently fails for every
    # language). Belongs here.
    if meta:
        args.append(f"--meta={meta}")
    args += ["--", *cmd]
    return args

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    cpu_ms: int
    wall_ms: int
    mem_mb: int
    output_kb: int


def run_args(box_id: int, limits: Limits, cmd: list) -> list:
    # Validated on the Hostinger box 2026-06-23 (see
    # docs/runbooks/2026-06-23-isolate-sandbox-hostinger.md):
    #   --cg              cgroup-v2 mode — isolate 2.6 needs it for mem/pid caps.
    #   --share-net ABSENT  no network (verified: connect() raises in-sandbox).
    #   --env=PATH        isolate wipes the env, so compilers can't find ld/as/
    #                     collect2 without PATH (gcc failed "cannot find 'ld'"
    #                     until set). /usr/local/bin covers npm-global tools (tsc).
    #   --env=HOME=/box   javac and friends need a writable HOME.
    return [
        "isolate", f"--box-id={box_id}", "--cg", "--run",
        f"--time={limits.cpu_ms / 1000:g}",
        f"--wall-time={limits.wall_ms / 1000:g}",
        f"--mem={limits.mem_mb * 1024}",
        f"--fsize={limits.output_kb}",
        "--processes=64",
        "--env=PATH=/usr/bin:/usr/local/bin:/bin",
        "--env=HOME=/box",
        "--stderr-to-stdout=0",
        "--", *cmd,
    ]

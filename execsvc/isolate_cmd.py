from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    cpu_ms: int
    wall_ms: int
    mem_mb: int
    output_kb: int


def run_args(box_id: int, limits: Limits, cmd: list) -> list:
    # NB: --share-net is deliberately absent → the sandbox has no network.
    return [
        "isolate", f"--box-id={box_id}", "--run",
        f"--time={limits.cpu_ms / 1000:g}",
        f"--wall-time={limits.wall_ms / 1000:g}",
        f"--mem={limits.mem_mb * 1024}",
        f"--fsize={limits.output_kb}",
        "--processes=64",
        "--stderr-to-stdout=0",
        "--", *cmd,
    ]

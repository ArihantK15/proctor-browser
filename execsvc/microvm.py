"""Firecracker microVM lifecycle: boot from rootfs+kernel, exec a command
inside, capture output, destroy. One responsibility: the VM boundary.

PARKED, not wired into app.py (confirmed 2026-07-03): the production host
(Hostinger VPS running execsvc) exposes no nested KVM — `/dev/kvm` does not
exist, `egrep -c '(vmx|svm)' /proc/cpuinfo` returns 0, and the `firecracker`
binary isn't installed. Hostinger's hypervisor doesn't pass nested
virtualization through to the guest, which is a hosting-tier decision, not
something fixable in this codebase. `run_in_microvm` is also functionally
incomplete independent of that: it never actually sends `cmd`/`stdin` into
the guest (no vsock or serial channel exists yet — see the "Implementation
sketch" note below), and no rootfs/kernel image has been built. The live
`/run` endpoint in app.py uses `isolate` only (see runner.py); do not wire
this module in without first (a) moving execsvc to a host that exposes
`/dev/kvm`, and (b) implementing the actual guest I/O channel this module
currently only sketches.

The security-critical property of this module is `build_vm_config`: the
generated Firecracker config NEVER contains a `network-interfaces` entry
(that absence is what guarantees the sandbox has no network egress), and the
rootfs drive is always mounted read-only. `build_vm_config` is a pure
function and is unit-tested on every platform (Task 2.2); the actual
Firecracker process spawn (`run_in_microvm`) only runs on a Linux/KVM host
and is guarded by `pytest.mark.skipif(not os.path.exists("/dev/kvm"), ...)`
in execsvc/tests/test_microvm.py.
"""

import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass
class VmResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool


def build_vm_config(rootfs: str, kernel: str, mem_mb: int = 256, vcpu_count: int = 1) -> dict:
    """Pure function: build the Firecracker VM config dict for one run.

    Deliberately has NO "network-interfaces" key — that omission is the
    egress block for the whole microVM (in addition to `isolate`'s own
    `--share-net`-less invocation inside it, this is defense-in-depth).
    The rootfs drive is mounted read-only; a separate writable scratch
    drive would be added by callers that need a workdir overlay.
    """
    return {
        "boot-source": {
            "kernel_image_path": kernel,
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off",
        },
        "drives": [
            {
                "drive_id": "rootfs",
                "path_on_host": rootfs,
                "is_root_device": True,
                "is_read_only": True,
            },
        ],
        "machine-config": {
            "vcpu_count": vcpu_count,
            "mem_size_mib": mem_mb,
        },
        # NOTE: no "network-interfaces" key — this VM has no NIC, ever.
    }


def run_in_microvm(cmd: list, stdin: str, wall_ms: int,
                    rootfs: str = "execsvc/rootfs/rootfs.ext4",
                    kernel: str = "execsvc/rootfs/vmlinux") -> VmResult:
    """Boot a per-run Firecracker microVM (no network device), execute `cmd`
    inside it, capture output, and destroy the VM.

    Real body intended for the Linux/KVM execution host; only exercised by
    the skipped integration test `test_microvm.py` on a real host. On this
    macOS dev box `/dev/kvm` does not exist so the calling test is skipped
    and this function is never invoked.

    Implementation sketch (best-effort, concrete enough to run on a real
    host once vsock/serial wiring for cmd/stdin/stdout is in place):
      1. Write build_vm_config(rootfs, kernel) to a per-run config JSON.
      2. Spawn `firecracker --api-sock <sock> --config-file <config.json>`.
      3. Push the command + stdin into the VM (vsock, or a small init script
         baked into the rootfs that reads a mounted scratch file and writes
         stdout/exit-code back to another scratch file).
      4. Poll/wait up to `wall_ms` for the scratch "done" marker; on timeout
         kill the firecracker process (hard backstop) and report timed_out.
      5. Read stdout/exit-code from the scratch file, then kill -9 firecracker
         to destroy the VM unconditionally.
    """
    config = build_vm_config(rootfs=rootfs, kernel=kernel)
    run_id = uuid.uuid4().hex
    workdir = tempfile.mkdtemp(prefix=f"execsvc-vm-{run_id}-")
    config_path = os.path.join(workdir, "vm_config.json")
    api_sock = os.path.join(workdir, "firecracker.sock")

    with open(config_path, "w") as f:
        json.dump(config, f)

    proc = subprocess.Popen(
        ["firecracker", "--api-sock", api_sock, "--config-file", config_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # Real implementation would talk to the VM over vsock/serial here to
        # push `cmd` + `stdin` and collect stdout/exit-code. That channel is
        # environment-specific (depends on the rootfs's init script) and is
        # exercised only on the real KVM host by the skipped integration test.
        deadline = time.time() + (wall_ms / 1000.0)
        timed_out = False
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            timed_out = True
        out, err = proc.communicate(timeout=1)
        return VmResult(
            stdout=out.decode(errors="replace") if out else "",
            stderr=err.decode(errors="replace") if err else "",
            exit_code=proc.returncode or 0,
            timed_out=timed_out,
        )
    finally:
        # Destroy the VM unconditionally.
        try:
            proc.kill()
        except Exception:
            pass

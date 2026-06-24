import os
import pytest
from execsvc.microvm import run_in_microvm

pytestmark = pytest.mark.skipif(not os.path.exists("/dev/kvm"), reason="no KVM")


def test_vm_runs_and_has_no_network():
    # Inside the VM: a network attempt must fail (no NIC configured).
    out = run_in_microvm(cmd=["python3", "-c",
        "import socket; "
        "import sys; "
        "\ntry:\n socket.create_connection(('1.1.1.1',53),2); print('NET-OK')\nexcept Exception: print('NET-BLOCKED')"],
        stdin="", wall_ms=8000)
    assert "NET-BLOCKED" in out.stdout
    assert out.exit_code == 0

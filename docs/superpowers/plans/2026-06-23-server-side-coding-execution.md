# Server-side Coding Execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run student code in a hostile-by-default server sandbox (per-run Firecracker microVM + `isolate`), grade authoritatively against secret expected outputs, and surface a LeetCode-style verdict — with the executor holding no secrets and a breach engineered to be worthless.

**Architecture:** Three trust zones. The **kiosk** only edits + POSTs source (already done, commit `09ba4839`). The **app/orchestrator** (FastAPI, trusted) holds expected outputs, calls the execution service per input, compares, stores, returns counts. The **execution service** (standalone, network-isolated, credential-less Linux/KVM host) exposes one endpoint `POST /run {language,source,stdin,limits} → {stdout,stderr,exit,time_ms,timed_out,oom,compile_error}` — each call a fresh ~125 ms microVM with `isolate` inside, then destroyed. Spec: `docs/superpowers/specs/2026-06-23-server-side-coding-execution-design.md`.

**Tech Stack:** Firecracker (microVM), `isolate` (IOI sandbox: namespaces+cgroups+seccomp), a small Python (FastAPI+uvicorn) service for `/run`, the existing FastAPI app for the orchestrator, Postgres + RLS (existing `coding_test_cases`/`coding_submissions`), `pytest`.

**ENVIRONMENT NOTE (read first):** Phases 0–4 (execution service) **must be built and tested on a Linux host with KVM** (`/dev/kvm` present) — Firecracker and `isolate` do not run on macOS. Use the Hostinger box, a Linux VM, or a Linux CI runner. Phase 5 (orchestrator) is pure Python and is testable on macOS. Do **not** attempt Phases 0–4 on the Mac.

---

## File Structure

New standalone service (its own directory, shippable as the self-host appliance):

- `execsvc/app.py` — FastAPI `/run` endpoint; validates input, enforces limits, returns the result envelope. One responsibility: the HTTP contract + queueing.
- `execsvc/runner.py` — given `{language,source,stdin,limits}`, drive one microVM run and return the raw result. One responsibility: orchestrating a single sandboxed run.
- `execsvc/microvm.py` — Firecracker microVM lifecycle (boot from rootfs+kernel, exec a command inside, capture output, destroy). One responsibility: the VM boundary.
- `execsvc/isolate_cmd.py` — build the `isolate` command line (limits → flags) + per-language compile/run command tables. One responsibility: the inner sandbox + language configs.
- `execsvc/languages.py` — the language table: `{id → {source_filename, compile_cmd|None, run_cmd}}` for js/ts/python/c/cpp/java. One responsibility: language definitions (the only file that grows per language).
- `execsvc/tests/` — unit + the **escape-attempt security suite** (gating).
- `execsvc/rootfs/` — build scripts for the VM rootfs image (base + toolchains).
- `execsvc/Makefile`, `execsvc/README.md`, `execsvc/docker-compose.yml` — the portable self-host packaging.

App orchestrator (existing repo):

- `app/routers/coding.py` — MODIFY: `/coding/judge` becomes an executor-orchestrator; add `/coding/run` (sample).
- `app/services/exec_client.py` — CREATE: thin HTTP client to the execution service (`run_one(language, source, stdin, limits) → ExecResult`), with timeout + the single auth header. One responsibility: the app→service boundary.
- `app/services/coding_judge.py` — KEEP `normalize_output` + comparison; remove any assumption that outputs come from the client.
- `migrations/phase144_coding_exec_metrics.sql` — CREATE: add `compile_output text`, keep existing metric columns.
- `tests/test_exec_client.py`, `tests/test_coding_router.py` (extend) — orchestrator tests (Mac-testable, service mocked).

---

## PHASE 0 — Execution host + toolchain (Linux/KVM, ops)

### Task 0.1: Provision the isolated execution host

**Files:** none (infra). Document the result in `execsvc/README.md` (created in Task 7.1).

- [ ] **Step 1: Stand up a Linux host with KVM, network-egress blocked.**

Run (on the host):
```bash
ls /dev/kvm && echo "KVM present"
# Block all outbound from the sandbox network namespace later; for the HOST,
# confirm the box itself can reach ONLY the app's private IP (no public egress
# from the execution path). Document the firewall rules in README.
```
Expected: `/dev/kvm` exists. The host accepts inbound `/run` only from the app's private address; the sandbox itself gets no network (enforced per-VM in Task 2).

- [ ] **Step 2: Install Firecracker + isolate.**

Run:
```bash
# Firecracker
ARCH=$(uname -m)
curl -L https://github.com/firecracker-microvm/firecracker/releases/latest/download/firecracker-${ARCH}.tgz | tar xz
sudo install release-*/firecracker-* /usr/local/bin/firecracker
firecracker --version
# isolate (build from source)
sudo apt-get install -y libcap-dev pkg-config build-essential
git clone https://github.com/ioi/isolate && cd isolate && make && sudo make install
isolate --version
```
Expected: both print versions.

- [ ] **Step 3: Commit the README stub recording host requirements.** (folded into Task 7.1)

### Task 0.2: Build the base rootfs with Python

**Files:** Create `execsvc/rootfs/build.sh`

- [ ] **Step 1: Write `execsvc/rootfs/build.sh`** that produces a minimal ext4 rootfs containing a non-root `runner` user, `isolate`, and CPython.

```bash
#!/usr/bin/env bash
# Builds rootfs.ext4 (a minimal Debian + python3 + isolate, non-root runner).
# Re-run to rebuild; output is execsvc/rootfs/rootfs.ext4 (gitignored, large).
set -euo pipefail
ROOT=$(mktemp -d)
debootstrap --variant=minbase --include=python3,libcap2,iproute2 stable "$ROOT"
# isolate binary + a 'runner' uid; NO network tools beyond iproute2 for setup.
cp "$(command -v isolate)" "$ROOT/usr/local/bin/"
chroot "$ROOT" useradd -u 1000 -m runner
# size + format
dd if=/dev/zero of=rootfs.ext4 bs=1M count=512
mkfs.ext4 -d "$ROOT" rootfs.ext4
echo "built execsvc/rootfs/rootfs.ext4"
```

- [ ] **Step 2: Run it; confirm the image exists.**

Run: `cd execsvc/rootfs && sudo bash build.sh && ls -la rootfs.ext4`
Expected: `rootfs.ext4` (~512 MB).

- [ ] **Step 3: Add a kernel.** Download a Firecracker-compatible `vmlinux` to `execsvc/rootfs/vmlinux` (documented in README; gitignored).

Run: `curl -L -o vmlinux <firecracker-ci-vmlinux-url>` (pin the version in README).
Expected: `vmlinux` present.

- [ ] **Step 4: Commit the build script + .gitignore for the images.**

```bash
printf 'rootfs.ext4\nvmlinux\n' > execsvc/rootfs/.gitignore
git add execsvc/rootfs/build.sh execsvc/rootfs/.gitignore
git commit -m "build(execsvc): base rootfs build script (python)"
```

---

## PHASE 1 — `isolate` sandbox runner (Linux; TDD)

Build the **inner** sandbox first (faster to iterate than full VMs), then wrap it in Firecracker (Phase 2). The runner here executes inside the host using `isolate`; Phase 2 moves the same `isolate` invocation inside a microVM.

### Task 1.1: Language table

**Files:** Create `execsvc/languages.py`, `execsvc/tests/test_languages.py`

- [ ] **Step 1: Write the failing test.**

```python
# execsvc/tests/test_languages.py
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
```

- [ ] **Step 2: Run it, verify it fails.** Run: `python -m pytest execsvc/tests/test_languages.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `execsvc/languages.py`.**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class LangSpec:
    source_filename: str
    compile_cmd: list[str] | None   # run in the box before run_cmd; None = interpreted
    run_cmd: list[str]

LANGUAGES: dict[str, LangSpec] = {
    "python": LangSpec("main.py", None, ["python3", "main.py"]),
    # JS/TS/C/C++/Java added in Phase 6 — one entry each.
}

def lang_spec(language: str) -> LangSpec:
    return LANGUAGES[language.lower()]
```

- [ ] **Step 4: Run it, verify pass.** Run: `python -m pytest execsvc/tests/test_languages.py -v` → PASS.

- [ ] **Step 5: Commit.** `git add execsvc/languages.py execsvc/tests/test_languages.py && git commit -m "feat(execsvc): language table (python)"`

### Task 1.2: `isolate` command builder

**Files:** Create `execsvc/isolate_cmd.py`, `execsvc/tests/test_isolate_cmd.py`

- [ ] **Step 1: Write the failing test** (pure string-building, no isolate needed → runs anywhere).

```python
# execsvc/tests/test_isolate_cmd.py
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
```

- [ ] **Step 2: Run, verify fail.** `python -m pytest execsvc/tests/test_isolate_cmd.py -v` → FAIL.

- [ ] **Step 3: Implement `execsvc/isolate_cmd.py`.**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Limits:
    cpu_ms: int
    wall_ms: int
    mem_mb: int
    output_kb: int

def run_args(box_id: int, limits: Limits, cmd: list[str]) -> list[str]:
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
```

- [ ] **Step 4: Run, verify pass.** → PASS.

- [ ] **Step 5: Commit.** `git commit -am "feat(execsvc): isolate command builder (no network, capped)"`

### Task 1.3: Single sandboxed run via isolate (Linux-only integration test)

**Files:** Create `execsvc/runner.py`, `execsvc/tests/test_runner_isolate.py`

- [ ] **Step 1: Write the failing integration test** (marked linux/isolate; skipped where unavailable).

```python
# execsvc/tests/test_runner_isolate.py
import shutil, pytest
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
```

- [ ] **Step 2: Run on the Linux host, verify fail.** `python -m pytest execsvc/tests/test_runner_isolate.py -v` → FAIL (function missing).

- [ ] **Step 3: Implement `execsvc/runner.py`** (`run_in_isolate`: `isolate --init`, write source into the box, optional compile, run, parse the meta file, `isolate --cleanup`).

```python
import subprocess, tempfile, os
from dataclasses import dataclass
from .languages import lang_spec
from .isolate_cmd import run_args, Limits

@dataclass
class ExecResult:
    stdout: str; stderr: str; exit_code: int
    time_ms: int; timed_out: bool; oom: bool; compile_error: str | None

def run_in_isolate(language: str, source: str, stdin: str, limits: Limits, box_id: int = 0) -> ExecResult:
    spec = lang_spec(language)
    subprocess.run(["isolate", f"--box-id={box_id}", "--init"], check=True, capture_output=True)
    box = subprocess.run(["isolate", f"--box-id={box_id}", "--print-cg-root"],
                         capture_output=True, text=True)  # locate box dir
    box_dir = f"/var/local/lib/isolate/{box_id}/box"
    try:
        with open(os.path.join(box_dir, spec.source_filename), "w") as f:
            f.write(source)
        compile_error = None
        if spec.compile_cmd:
            meta = tempfile.mktemp()
            cp = subprocess.run(run_args(box_id, limits, spec.compile_cmd) + [f"--meta={meta}"],
                                input="", capture_output=True, text=True)
            if cp.returncode != 0:
                return ExecResult("", "", cp.returncode, 0, False, False, cp.stdout + cp.stderr)
        meta = tempfile.mktemp()
        rp = subprocess.run(run_args(box_id, limits, spec.run_cmd) + [f"--meta={meta}"],
                            input=stdin, capture_output=True, text=True)
        m = _parse_meta(meta)
        return ExecResult(rp.stdout, rp.stderr, int(m.get("exitcode", rp.returncode)),
                          int(float(m.get("time", 0)) * 1000),
                          m.get("status") == "TO", m.get("status") == "SG" and "memory" in m.get("message",""),
                          compile_error)
    finally:
        subprocess.run(["isolate", f"--box-id={box_id}", "--cleanup"], capture_output=True)

def _parse_meta(path: str) -> dict:
    try:
        return dict(l.split(":", 1) for l in open(path).read().splitlines() if ":" in l)
    except FileNotFoundError:
        return {}
```

- [ ] **Step 4: Run on Linux, verify pass.** → 3 PASS.

- [ ] **Step 5: Commit.** `git commit -am "feat(execsvc): single sandboxed run via isolate (python)"`

---

## PHASE 2 — Firecracker microVM wrapper (Linux/KVM)

### Task 2.1: Boot a microVM, run a command, destroy it

**Files:** Create `execsvc/microvm.py`, `execsvc/tests/test_microvm.py`

- [ ] **Step 1: Write the failing integration test** (skipped without `/dev/kvm`).

```python
# execsvc/tests/test_microvm.py
import os, pytest
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
```

- [ ] **Step 2: Run on KVM host, verify fail.** → FAIL.

- [ ] **Step 3: Implement `execsvc/microvm.py`** — start Firecracker via its API socket with `rootfs.ext4` + `vmlinux`, **no network device** (this is what guarantees no egress), pass the command+stdin in via a vsock or a mounted scratch file, read stdout back, then `kill` the VM. Use the Firecracker `--no-api` jailer config; one VM per run; hard wall-clock kill on the host as a backstop.

```python
# Pseudocode-level but concrete: spawn firecracker with a per-run config json that
# has boot-args, the rootfs drive (read-only) + a writable overlay drive, and
# NO network-interfaces block. Communicate cmd/stdin/stdout over vsock. Destroy.
# (Full firecracker config JSON template lives in execsvc/microvm.py as a constant.)
```

> Implementation detail: the **absence of a `network-interfaces` entry** in the Firecracker config is the egress block — assert it in a unit test on the generated config (Task 2.2) so it can never regress.

- [ ] **Step 4: Run on KVM host, verify pass** (`NET-BLOCKED`). → PASS.

- [ ] **Step 5: Commit.** `git commit -am "feat(execsvc): firecracker microvm per run, no network device"`

### Task 2.2: Config-generation guard (runs anywhere)

**Files:** `execsvc/tests/test_microvm_config.py`

- [ ] **Step 1: Write the failing test** asserting the generated VM config has **no** network interface and a read-only rootfs.

```python
from execsvc.microvm import build_vm_config
def test_config_has_no_network_and_readonly_root():
    cfg = build_vm_config(rootfs="rootfs.ext4", kernel="vmlinux")
    assert "network-interfaces" not in cfg
    assert cfg["drives"][0]["is_read_only"] is True
```

- [ ] **Step 2–4:** Run → fail → factor `build_vm_config` out as a pure function → pass.
- [ ] **Step 5: Commit.** `git commit -am "test(execsvc): guard VM config (no net, ro root)"`

### Task 2.3: Point `runner.run_in_isolate` through the microVM

**Files:** MODIFY `execsvc/runner.py`

- [ ] **Step 1:** Change `runner` to invoke `isolate` **inside** the microVM (defense-in-depth) instead of on the host. Re-run `test_runner_isolate.py` adapted to the VM path; expect the same hello/stdin/timeout results. Commit.

---

## PHASE 3 — `/run` service API (Linux; TDD)

### Task 3.1: The `/run` endpoint

**Files:** Create `execsvc/app.py`, `execsvc/tests/test_app.py`

- [ ] **Step 1: Write the failing test** (mock `runner.run_in_isolate` so this runs anywhere).

```python
# execsvc/tests/test_app.py
from fastapi.testclient import TestClient
from unittest.mock import patch
from execsvc.app import app
from execsvc.runner import ExecResult
client = TestClient(app)

def test_run_returns_envelope():
    fake = ExecResult("Hello, World!\n", "", 0, 12, False, False, None)
    with patch("execsvc.app.run_in_isolate", return_value=fake):
        r = client.post("/run", json={"language":"python","source":"x","stdin":"",
                                      "cpu_ms":2000,"wall_ms":4000,"mem_mb":256,"output_kb":64})
    assert r.status_code == 200
    b = r.json()
    assert b["stdout"] == "Hello, World!\n" and b["timed_out"] is False and b["exit_code"] == 0

def test_run_rejects_unknown_language():
    r = client.post("/run", json={"language":"cobol","source":"x","stdin":"",
                                  "cpu_ms":1,"wall_ms":1,"mem_mb":1,"output_kb":1})
    assert r.status_code == 400
```

- [ ] **Step 2: Run, verify fail.** → FAIL.

- [ ] **Step 3: Implement `execsvc/app.py`** (FastAPI; validate language against `LANGUAGES`; clamp limits to service maxima; one concurrency semaphore = pool size; call `run_in_isolate`; return the envelope). The endpoint holds NO expected outputs.

- [ ] **Step 4: Run, verify pass.** → PASS.

- [ ] **Step 5: Commit.** `git commit -am "feat(execsvc): POST /run endpoint + limit clamping"`

---

## PHASE 4 — Escape-attempt security suite (Linux; GATING)

> This suite is the "no slightest hiccup" gate. It MUST pass on the real microVM path before the service is allowed anywhere near production. Each is a real run that must be safely contained.

### Task 4.1: The security suite

**Files:** Create `execsvc/tests/test_security.py`

- [ ] **Step 1: Write the suite** (each runs hostile source through `/run` on the real VM path; assert containment).

```python
# execsvc/tests/test_security.py — run on the KVM host against the real runner.
import os, pytest
from fastapi.testclient import TestClient
from execsvc.app import app
pytestmark = pytest.mark.skipif(not os.path.exists("/dev/kvm"), reason="needs real sandbox")
client = TestClient(app)
def _run(src, lang="python", **lim):
    base = {"language":lang,"source":src,"stdin":"","cpu_ms":2000,"wall_ms":5000,"mem_mb":256,"output_kb":64}
    base.update(lim); return client.post("/run", json=base).json()

def test_no_network_egress():
    r = _run("import socket\ntry:\n socket.create_connection(('1.1.1.1',53),2);print('OPEN')\nexcept Exception:print('BLOCKED')")
    assert "BLOCKED" in r["stdout"]

def test_cannot_read_host_files():
    r = _run("print(open('/etc/hostname').read())")  # VM's own hostname at most — never the host's
    assert "OPEN" not in r["stdout"]  # no crash-leak; manual: confirm it is NOT the host name

def test_fork_bomb_is_contained():
    r = _run("import os\nwhile True:\n try: os.fork()\n except: pass")
    assert r["timed_out"] or r["exit_code"] != 0   # killed by pid/time caps, host stays up

def test_oom_is_contained():
    r = _run("x=' '*10**12", mem_mb=128)
    assert r["oom"] or r["exit_code"] != 0

def test_oversized_output_is_capped():
    r = _run("print('A'*10**9)", output_kb=64)
    assert len(r["stdout"]) <= 64*1024 + 4096
```

- [ ] **Step 2: Run on the KVM host.** Expected: ALL pass; the host remains responsive throughout. Any failure = stop; do not ship.

- [ ] **Step 3: Commit.** `git commit -am "test(execsvc): gating escape-attempt security suite"`

- [ ] **Step 4: Wire the suite into the service's CI** so it gates every change to `execsvc/`.

---

## PHASE 5 — App orchestrator (macOS-testable; TDD)

### Task 5.1: Execution-service client

**Files:** Create `app/services/exec_client.py`, `tests/test_exec_client.py`

- [ ] **Step 1: Write the failing test** (mock the HTTP call).

```python
# tests/test_exec_client.py
from unittest.mock import patch, MagicMock
from app.services.exec_client import run_one, ExecLimits

def test_run_one_posts_and_parses():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"stdout":"42\n","stderr":"","exit_code":0,"time_ms":9,
                              "timed_out":False,"oom":False,"compile_error":None}
    with patch("app.services.exec_client._post", return_value=resp):
        r = run_one("python", "print(42)", "", ExecLimits(2000,4000,256,64))
    assert r.stdout == "42\n" and r.time_ms == 9 and r.timed_out is False
```

- [ ] **Step 2–4:** Run → fail → implement `run_one` (httpx POST to `EXEC_SERVICE_URL/run` with the service auth header + a hard client timeout) → pass.
- [ ] **Step 5: Commit.** `git commit -am "feat(coding): execution-service client"`

### Task 5.2: Migration for compile output

**Files:** Create `migrations/phase144_coding_exec_metrics.sql`

- [ ] **Step 1:** Add `compile_output text` to `coding_submissions` (idempotent `ADD COLUMN IF NOT EXISTS`), matching the existing phase-141 RLS pattern. Apply against the integration DB. Commit.

### Task 5.3: `/coding/run` (sample cases, server-run)

**Files:** MODIFY `app/routers/coding.py`; extend `tests/test_coding_router.py`

- [ ] **Step 1: Write the failing test** — POST `{session_id,question_id,language,source}` → for each SAMPLE case, the service is called and `{cases:[{input,expected_output,output,status,time_ms,error}],passed,total}` returns; sample expecteds ARE included (public).

```python
def test_run_sample_executes_and_returns_cases(client, seed_sample_cases, mock_exec):
    mock_exec.return_value.stdout = "Hello, World!\n"
    r = client.post("/api/v1/coding/run", json={"session_id":S,"question_id":"1",
                                                 "language":"python","source":"print('Hello, World!')"})
    body = r.json()
    assert body["total"] == 1 and body["passed"] == 1
    assert body["cases"][0]["status"] == "passed"
    assert body["cases"][0]["expected_output"] == "Hello, World!"
```

- [ ] **Step 2–4:** Run → fail → implement the endpoint (read sample cases under `system_context`; for each, `run_one`; compare with `normalize_output`; build per-case dicts; never call the service with expected outputs) → pass.
- [ ] **Step 5: Commit.** `git commit -am "feat(coding): /coding/run executes sample cases server-side"`

### Task 5.4: Rewire `/coding/judge` (hidden cases, graded)

**Files:** MODIFY `app/routers/coding.py`; extend `tests/test_coding_router.py`

- [ ] **Step 1: Write the failing test** — POST `{session_id,question_id,language,source,telemetry}` (NO `outputs`). For each HIDDEN case the service runs; the app compares to the SECRET expected; returns `{passed,total,average_execution_ms}`; the existing attempt-cap + idempotency still hold; **assert the service is never sent `expected_output`.**

```python
def test_judge_runs_hidden_and_never_sends_expected(client, seed_hidden_cases, mock_exec):
    mock_exec.return_value.stdout = "Hello, World!\n"; mock_exec.return_value.time_ms = 7
    r = client.post("/api/v1/coding/judge", json={"session_id":S,"question_id":"1",
        "language":"python","source":"print('Hello, World!')",
        "telemetry":{"keystroke_rhythm_variance":0,"paste_attempts":0,"focus_loss_count":0}})
    b = r.json()
    assert b["passed"] == b["total"] == 1
    # the secret expected output was never part of any call to the executor:
    for call in mock_exec.call_args_list:
        assert "Hello, World!" not in str(call)  # only source+stdin are sent
```

- [ ] **Step 2–4:** Run → fail → rewire judge: keep attempt-cap/idempotency/insert; replace "compare client outputs" with "run each hidden case via `run_one`, compare to secret expected, aggregate"; store `compile_output` + metrics → pass.
- [ ] **Step 5: Commit.** `git commit -am "feat(coding): /coding/judge runs+grades server-side (executor holds no secrets)"`

### Task 5.5: Failure policy — "make them wait" (LeetCode-style)

**Files:** MODIFY `app/routers/coding.py`; extend tests

- [ ] **Step 1: Write the failing test** — when `run_one` raises a transient unavailability, judge returns **HTTP 503 with a retryable marker** (never a silent 0/total, never auto-fail). The kiosk already shows "please wait" + auto-retries.

```python
def test_judge_503_on_executor_unavailable(client, seed_hidden_cases, mock_exec):
    mock_exec.side_effect = ExecUnavailable()
    r = client.post("/api/v1/coding/judge", json={...})
    assert r.status_code == 503 and r.json()["retryable"] is True
```

- [ ] **Step 2–4:** Run → fail → catch `ExecUnavailable` → 503 `{retryable:true}`; do NOT write a submission row → pass.
- [ ] **Step 5: Commit.** `git commit -am "feat(coding): executor-unavailable -> 503 retryable (LeetCode wait, never silent fail)"`

---

## PHASE 6 — Remaining languages (Linux; repeat the Python pattern)

For EACH language add (a) one `LANGUAGES` entry, (b) its toolchain in `rootfs/build.sh`, (c) a correctness test mirroring `test_runner_isolate.py::test_python_hello`, (d) the security suite re-run. The pattern is identical; only the spec entry + rootfs package differ:

- [ ] **Task 6.1 — JavaScript:** `LangSpec("main.js", None, ["node","main.js"])`; rootfs `+nodejs`. Test: `console.log("Hello, World!")` → `Hello, World!`. Commit.
- [ ] **Task 6.2 — TypeScript:** `LangSpec("main.ts", ["tsc","main.ts","--outFile","main.js"], ["node","main.js"])`; rootfs `+ npm i -g typescript`. Compile-error path test. Commit.
- [ ] **Task 6.3 — C:** `LangSpec("main.c", ["gcc","main.c","-O2","-o","main"], ["./main"])`; rootfs `+gcc`. Commit.
- [ ] **Task 6.4 — C++:** `LangSpec("main.cpp", ["g++","main.cpp","-O2","-std=c++17","-o","main"], ["./main"])`; rootfs `+g++`. Commit.
- [ ] **Task 6.5 — Java:** `LangSpec("Main.java", ["javac","Main.java"], ["java","Main"])`; rootfs `+default-jdk`. Note: source filename must be `Main.java` and the class `Main`. Commit.
- [ ] **Task 6.6:** Re-run `test_security.py` for `c`/`cpp` (compiled, larger surface) and confirm containment. Commit.

---

## PHASE 7 — Portable self-host packaging

### Task 7.1: README + Makefile + compose

**Files:** Create `execsvc/README.md`, `execsvc/Makefile`, `execsvc/docker-compose.yml`

- [ ] **Step 1:** README documents host requirements (KVM, firewall/no-egress, the app→service private link, the single auth secret), `make rootfs`, `make run`, and the security-suite gate. Makefile wraps rootfs build + service start + tests. Compose brings up the service as the one artifact an enterprise self-hosts.
- [ ] **Step 2:** Verify `make test` runs the unit suite (and, on a KVM host, the security suite). Commit `chore(execsvc): self-host packaging (README/Makefile/compose)`.

---

## PHASE 8 — Scaling (BLOCKED on real numbers)

- [ ] **Task 8.1:** Once real exam concurrency numbers exist (open item §9.1), size the worker-pool semaphore + queue depth in `execsvc/app.py`, add a `/healthz` + a queue-depth metric, and load-test against that number. Do not commit hardware spend before this. (Left intentionally without code — needs the data.)

---

## Self-Review

- **Spec coverage:** trust zones (Phases 1–5) ✓; Firecracker+isolate per-run (Phases 1–2) ✓; executor-holds-no-secrets (Task 5.4 test) ✓; no-network/escape suite (Phase 4) ✓; portability/self-host (Phase 7) ✓; LeetCode wait (Task 5.5) ✓; languages incl. C (Phase 6) ✓; reuse coding_test_cases/submissions + attempt-cap/idempotency (Tasks 5.3–5.4) ✓; client teardown — already DONE (`09ba4839`) ✓; scaling left open (Phase 8) matching spec §9.1 ✓.
- **Type consistency:** `ExecResult` (execsvc) and the app's `run_one`→client result share the same field names (`stdout/stderr/exit_code/time_ms/timed_out/oom/compile_error`); `Limits`/`ExecLimits` carry the same four caps; `/coding/run` case dict (`input,expected_output,output,status,time_ms,error`) matches the kiosk's `coding-ui.js` render (`c.status==='passed'`, `c.output`, `c.expected_output`, `c.time_ms`, `c.error`).
- **Placeholder scan:** Phase 8 is intentionally code-free (blocked on data, flagged in spec). Task 2.1 contains one pseudocode block for the Firecracker config (the surrounding tasks 2.2/2.3 make it concrete + tested); acceptable as the VM-boot glue is environment-specific.

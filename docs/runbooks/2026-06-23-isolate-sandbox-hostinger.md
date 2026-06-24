# Runbook — isolate sandbox on the Hostinger box (VALIDATED 2026-06-23)

Server: `srv1675832`, Ubuntu 24.04 (noble), cgroup v2, **no nested virt** (so
isolate, not Firecracker). All steps below were run and verified on the box.

## 1. Install isolate 2.6 (from source)

```bash
apt-get install -y libcap-dev libsystemd-dev libseccomp-dev pkg-config build-essential git
cd /opt && git clone https://github.com/ioi/isolate.git && cd isolate
make && make install        # the a2x/manpage error is harmless (binary builds fine)
isolate --version           # -> "process isolator 2.6"
```
(`libseccomp-dev` is required — easy to miss.)

## 2. Dedicated user + subuid range + cgroup keeper

isolate 2.6 maps sandboxes to an `isolate` user with a user-namespace subuid range.

```bash
useradd --system --no-create-home --shell /usr/sbin/nologin isolate 2>/dev/null || true
usermod --add-subuids 1000000-1065535 --add-subgids 1000000-1065535 isolate
# (or: echo "isolate:1000000:65536" >> /etc/subuid ; same for /etc/subgid)
systemctl daemon-reload
systemctl enable --now isolate.service   # must be active(running); needs the user above
```

## 3. Run caps (the contract our execsvc uses)

`--cg --time=1 --wall-time=2 --mem=131072 --processes=64` and **no `--share-net`**
(absence = no network). `python3`/compilers are visible because isolate's default
config binds `/usr` etc. read-only.

## 4. Validated security gate (re-run after any change)

All four were confirmed contained, host load stayed ~0.07:
- **No network:** `socket.create_connection` → exception (`NET-BLOCKED-OK`).
- **Infinite loop:** `status:TO killed:1` at ~1.09s (CPU cap).
- **Memory bomb:** `MemoryError` / `status:RE`, max-rss capped (128 MB cap).
- **Fork bomb:** `killed:1`, host responsive (process cap).

(Exact test block: see chat 2026-06-23 / Step 3, or the escape-attempt suite in
`execsvc/tests/test_security.py` once execsvc is deployed.)

## 5. NEXT (not yet done)

- Install language toolchains on the host so the sandbox can run all v1 langs:
  `apt-get install -y nodejs npm gcc g++ default-jdk && npm i -g typescript`.
- Create a dedicated **non-root service user** to run the execsvc `/run` API; verify
  it can drive isolate (isolate is setuid-root, but confirm the config permits it).
- Deploy `execsvc/` (FastAPI `/run`) as a systemd service bound to **localhost only**;
  wire `runner.py` to the real `isolate` path validated above.
- Point the app at it: `EXEC_SERVICE_URL=http://127.0.0.1:<port>`; apply migration
  `phase144_coding_exec_metrics.sql`; end-to-end test Run/Submit.
- gVisor remains a future stronger-boundary swap (replaces isolate, not layered).

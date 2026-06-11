// scripts/integrity.test.mjs — unit tests for lib/integrity.js
//
// runIntegrityChecks() inspects real machine state (network interfaces, env,
// argv, processes) for cheating/anti-proctoring signals. We mock the
// deterministic inputs — os.networkInterfaces (shared module object),
// process.env, process.argv — and assert on the SPECIFIC injected flag so the
// real, un-mockable process scan (ps/tasklist) can't make the test flaky.
//
// The multiple-monitors check (electron.screen) and the BIOS/proxy shell
// probes are environment-bound and intentionally not asserted here.
//
//   node --test scripts/integrity.test.mjs
//
import { test, describe, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const os = require('os');
const { runIntegrityChecks } = require('../lib/integrity');
const { VM_MAC_PREFIXES } = require('../config');

const VM_MAC = `${VM_MAC_PREFIXES[0]}:aa:bb:cc`;   // e.g. 00:05:69:aa:bb:cc
const CLEAN_MAC = '02:11:22:33:44:55';             // locally-administered, not a VM prefix

const PROXY_VARS = ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','SOCKS_PROXY',
                    'http_proxy','https_proxy','all_proxy','socks_proxy'];
const _savedEnv = {};
const _savedArgv = process.argv.slice();

afterEach(() => {
  for (const v of PROXY_VARS) {
    if (v in _savedEnv) { process.env[v] = _savedEnv[v]; delete _savedEnv[v]; }
    else delete process.env[v];
  }
  process.argv = _savedArgv.slice();
});

function setProxyEnv(name, val) { _savedEnv[name] = process.env[name]; process.env[name] = val; }

describe('integrity — VM MAC detection', () => {
  test('a VM MAC prefix is flagged vm_detected', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({
      en0: [{ mac: VM_MAC, internal: false, family: 'IPv4', address: '10.0.0.2' }],
    }));
    const flags = await runIntegrityChecks();
    assert.ok(flags.some(f => f.type === 'vm_detected' && f.details.includes(VM_MAC)));
  });

  test('a normal MAC is NOT flagged via the MAC path', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({
      en0: [{ mac: CLEAN_MAC, internal: false, family: 'IPv4', address: '10.0.0.2' }],
    }));
    const flags = await runIntegrityChecks();
    assert.equal(flags.some(f => f.details.includes(CLEAN_MAC)), false);
  });
});

describe('integrity — VPN / tunnel interfaces', () => {
  test('a named VPN interface (tailscale) is flagged', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({
      tailscale0: [{ internal: false, family: 'IPv4', address: '100.64.0.1' }],
    }));
    const flags = await runIntegrityChecks();
    assert.ok(flags.some(f => f.type === 'vpn_detected' && f.details.includes('tailscale0')));
  });

  test('a generic tunnel with a routable address is flagged', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({
      utun3: [{ internal: false, family: 'IPv4', address: '10.8.0.6' }],
    }));
    const flags = await runIntegrityChecks();
    assert.ok(flags.some(f => f.type === 'vpn_detected' && f.details.includes('utun3')));
  });

  test('a tunnel with ONLY a link-local IPv6 address is NOT flagged', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({
      utun3: [{ internal: false, family: 'IPv6', address: 'fe80::1', scopeid: 5 }],
    }));
    const flags = await runIntegrityChecks();
    assert.equal(flags.some(f => f.type === 'vpn_detected' && f.details.includes('utun3')), false);
  });
});

describe('integrity — proxy env + debugger flags', () => {
  test('an HTTPS_PROXY env var is flagged proxy_detected', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({}));
    setProxyEnv('HTTPS_PROXY', 'http://127.0.0.1:8888');
    const flags = await runIntegrityChecks();
    assert.ok(flags.some(f => f.type === 'proxy_detected' && f.details.includes('HTTPS_PROXY')));
  });

  test('an --inspect launch flag is flagged debugger_detected', async (t) => {
    t.mock.method(os, 'networkInterfaces', () => ({}));
    process.argv = [...process.argv, '--inspect=9229'];
    const flags = await runIntegrityChecks();
    assert.ok(flags.some(f => f.type === 'debugger_detected'));
  });
});

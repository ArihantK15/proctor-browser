import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { canonicalJSON: cjson, sign: _sign } = createRequire(import.meta.url)('../lib/attestation.js');

const SECRET = 'test-secret-42';

describe('attestation.js canonical JSON', () => {

  it('matches Python json.dumps(sort_keys=True, separators=(",",":")) — full payload', () => {
    const att = { kiosk: true, ts: 1234567890, client_version: '1.2.3' };
    const got = cjson(att);
    const want = '{"client_version":"1.2.3","kiosk":true,"ts":1234567890}';
    assert.equal(got, want);
  });

  it('matches Python — with session_key and roll', () => {
    const att = { kiosk: true, ts: 1234567890, client_version: '1.2.3', session_key: 'abc', roll: 'STU001' };
    const got = cjson(att);
    const want = '{"client_version":"1.2.3","kiosk":true,"roll":"STU001","session_key":"abc","ts":1234567890}';
    assert.equal(got, want);
  });

  it('matches Python — heartbeat (only kiosk + ts)', () => {
    const att = { kiosk: true, ts: 1234567890 };
    const got = cjson(att);
    const want = '{"kiosk":true,"ts":1234567890}';
    assert.equal(got, want);
  });

  it('sorts keys alphabetically regardless of insertion order', () => {
    const att = { ts: 100, client_version: '1.0.0', kiosk: true };
    const got = cjson(att);
    const want = '{"client_version":"1.0.0","kiosk":true,"ts":100}';
    assert.equal(got, want);
  });

});

describe('attestation.js HMAC-SHA256', () => {

  it('produces expected signature for full payload', () => {
    const att = { kiosk: true, ts: 1234567890, client_version: '1.2.3' };
    const got = _sign(att, SECRET);
    const want = 'b50f0df9077def211c634fb57083d8d777f1bb132f7b5f8b48a9a98611fc34c1';
    assert.equal(got, want);
  });

  it('produces expected signature with session_key and roll', () => {
    const att = { kiosk: true, ts: 1234567890, client_version: '1.2.3', session_key: 'abc', roll: 'STU001' };
    const got = _sign(att, SECRET);
    const want = 'e7ed9a79c659bd432abe2ced1175e65d83a9b3ac0d2bc59231921dfed6eb1306';
    assert.equal(got, want);
  });

  it('produces expected signature for heartbeat payload', () => {
    const att = { kiosk: true, ts: 1234567890 };
    const got = _sign(att, SECRET);
    const want = 'c3700460d568e55c63a981b56dd7eb548ae4b90cc0353d1b28db7fc1915d4b14';
    assert.equal(got, want);
  });

  it('returns empty string when secret is empty', () => {
    const att = { kiosk: true, ts: 1234567890 };
    const got = _sign(att, '');
    assert.equal(got, '');
  });

  it('constant-time: different secrets produce different signatures', () => {
    const att = { kiosk: true, ts: 1234567890 };
    const s1 = _sign(att, 'secret-a');
    const s2 = _sign(att, 'secret-b');
    assert.notEqual(s1, s2);
    assert.equal(s1.length, 64);
    assert.equal(s2.length, 64);
  });

});

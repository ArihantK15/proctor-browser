// scripts/scrub-secrets.test.mjs — unit tests for the proctor env-scrubber
//
// Before forking proctor.py, python-manager strips secret/credential env vars
// from the inherited parent environment (code-signing creds, CI tokens, DB /
// cloud secrets, anything ending in _SECRET/_TOKEN/_PASSWORD/_API_KEY/
// _PRIVATE_KEY) so a compromised or chatty child can't read them. PROCTOR_* is
// the app's own namespace and must always pass through (the proctor needs its
// JWT). A regression that stops scrubbing = a real credential leak into a
// subprocess, so this locks the boundary.
//
//   node --test scripts/scrub-secrets.test.mjs
//
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { _scrubSecretsFromEnv } = require('../lib/python-manager');

describe('_scrubSecretsFromEnv — denylisted exact names', () => {
  test('drops code-signing, CI, DB and cloud secrets', () => {
    const out = _scrubSecretsFromEnv({
      CSC_KEY_PASSWORD: 'x', APPLE_APP_SPECIFIC_PASSWORD: 'x',
      GH_TOKEN: 'x', DATABASE_URL: 'postgres://u:p@h/db',
      SUPABASE_SERVICE_ROLE_KEY: 'x', REDIS_URL: 'redis://h',
      AWS_SECRET_ACCESS_KEY: 'x',
    });
    assert.deepEqual(out, {});   // every one is denylisted
  });
});

describe('_scrubSecretsFromEnv — suffix pattern', () => {
  test('drops anything ending in a secret suffix', () => {
    const out = _scrubSecretsFromEnv({
      MY_API_KEY: 'x', FOO_SECRET: 'x', BAR_TOKEN: 'x',
      DB_PASSWORD: 'x', SIGNING_PRIVATE_KEY: 'x',
    });
    assert.deepEqual(out, {});
  });

  test('the suffix match is case-insensitive', () => {
    const out = _scrubSecretsFromEnv({ my_token: 'x', some_password: 'x' });
    assert.deepEqual(out, {});
  });

  test('a secret-ish word NOT at the end is kept (anchored match)', () => {
    // TOKEN_BUDGET does not end in _TOKEN, so it is not a secret by name.
    const out = _scrubSecretsFromEnv({ TOKEN_BUDGET: '500', SECRETARY: 'jane' });
    assert.equal(out.TOKEN_BUDGET, '500');
    assert.equal(out.SECRETARY, 'jane');
  });
});

describe('_scrubSecretsFromEnv — PROCTOR_* passthrough + innocuous vars', () => {
  test('PROCTOR_* is kept even when it ends in a secret suffix', () => {
    const out = _scrubSecretsFromEnv({
      PROCTOR_JWT_TOKEN: 'jwt', PROCTOR_SESSION_ID: 's1',
      PROCTOR_API_KEY: 'k',
    });
    assert.equal(out.PROCTOR_JWT_TOKEN, 'jwt');
    assert.equal(out.PROCTOR_SESSION_ID, 's1');
    assert.equal(out.PROCTOR_API_KEY, 'k');
  });

  test('ordinary OS / runtime vars pass through untouched', () => {
    const env = { PATH: '/usr/bin', HOME: '/home/u', LANG: 'en_US.UTF-8', PYTHONUTF8: '1' };
    assert.deepEqual(_scrubSecretsFromEnv(env), env);
  });

  test('does not mutate the input env object', () => {
    const env = { DATABASE_URL: 'x', PATH: '/bin' };
    _scrubSecretsFromEnv(env);
    assert.equal('DATABASE_URL' in env, true);   // original still intact
  });

  test('a realistic mixed env keeps only the safe vars', () => {
    const out = _scrubSecretsFromEnv({
      PATH: '/usr/bin', HOME: '/home/u',
      DATABASE_URL: 'secret', GH_TOKEN: 'secret', AWS_SECRET_ACCESS_KEY: 'secret',
      MY_API_KEY: 'secret',
      PROCTOR_JWT_TOKEN: 'keep', PROCTOR_SERVER_URL: 'keep',
    });
    assert.deepEqual(Object.keys(out).sort(),
      ['HOME', 'PATH', 'PROCTOR_JWT_TOKEN', 'PROCTOR_SERVER_URL']);
  });
});

const crypto = require('crypto');

// SECURITY: the real secret is baked in at BUILD time by build/beforePack.js,
// which replaces the placeholder on the next line with the CI
// KIOSK_ATTESTATION_SECRET value BEFORE the asar is packed (asar:true means
// afterPack is too late — the code is already sealed). In dev (unpackaged, the
// placeholder still intact) we fall back to the env var so local runs can sign.
// Do NOT reformat the next line — beforePack.js string-matches it exactly.
const _BAKED_ATTESTATION_SECRET = '__KIOSK_ATTESTATION_SECRET__';
const KIOSK_ATTESTATION_SECRET = (() => {
  // Split so the literal placeholder token never appears here (only on the
  // line above), keeping the build-time replace surgical.
  const placeholder = '__KIOSK_ATTESTATION' + '_SECRET__';
  if (_BAKED_ATTESTATION_SECRET && _BAKED_ATTESTATION_SECRET !== placeholder) {
    return _BAKED_ATTESTATION_SECRET;
  }
  try { return process.env.KIOSK_ATTESTATION_SECRET || ''; } catch { return ''; }
})();

function buildPayload(overrides) {
  const payload = {
    kiosk: true,
    ts: Math.floor(Date.now() / 1000),
    client_version: '',
    ...overrides,
  };
  if (!payload.client_version) {
    try {
      payload.client_version = require('electron').app.getVersion();
    } catch {
      payload.client_version = '0.0.0';
    }
  }
  return payload;
}

function buildV2Payload({ session_key, exam_id, roll, nonce }) {
  let client_version = '0.0.0';
  try {
    client_version = require('electron').app.getVersion();
  } catch {}
  return {
    v: 2,
    session_key,
    exam_id,
    roll,
    nonce,
    kiosk: true,
    client_version,
    packaged: require('electron').app.isPackaged,
    platform: process.platform,
    ts: Math.floor(Date.now() / 1000),
  };
}

function canonicalJSON(obj) {
  const keys = Object.keys(obj).sort();
  const parts = keys.map(k => JSON.stringify(k) + ':' + JSON.stringify(obj[k]));
  return '{' + parts.join(',') + '}';
}

function sign(att, secret) {
  const key = secret || KIOSK_ATTESTATION_SECRET;
  if (!key) return '';
  const canon = canonicalJSON(att);
  return crypto.createHmac('sha256', key).update(canon).digest('hex');
}

module.exports = { buildPayload, buildV2Payload, canonicalJSON, sign };

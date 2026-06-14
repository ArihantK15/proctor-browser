// electron-builder beforePack hook — bake the kiosk-attestation secret into
// lib/attestation.js BEFORE the asar is packed.
//
// WHY HERE (not afterPack): the build uses asar:true, so by afterPack time the
// code is already sealed inside app.asar. The only place to modify source that
// ends up in the shipped asar is before packing. This runs on every
// platform/arch (each matrix leg is a fresh checkout), inside electron-builder,
// so it inherits the build step's KIOSK_ATTESTATION_SECRET env.
//
// It replaces the __KIOSK_ATTESTATION_SECRET__ placeholder in lib/attestation.js
// with the real value so the shipped client can sign attestations the server
// accepts. In a no-secret/dev build it warns loudly and leaves the placeholder
// (attestation.js then falls back to process.env for local runs).
//
// NOTE: the baked secret is plaintext inside the asar (asar is not encrypted).
// That is the accepted threat model — it raises the bar against casual bypass,
// not against a reverse-engineer who runs `npx asar extract`. Enabling
// PROCTA_BYTENODE for the release build is the next step to harden this.

const fs = require('fs');
const path = require('path');

// Must match the (quoted) placeholder line in lib/attestation.js exactly.
const PLACEHOLDER = "'__KIOSK_ATTESTATION_SECRET__'";

exports.default = async function beforePack(context) {
  const secret = process.env.KIOSK_ATTESTATION_SECRET || '';
  const projectDir = (context && context.packager && context.packager.projectDir) || process.cwd();
  const file = path.join(projectDir, 'lib', 'attestation.js');

  if (!fs.existsSync(file)) {
    console.warn('[beforePack] lib/attestation.js not found — cannot bake attestation secret.');
    return;
  }

  const code = fs.readFileSync(file, 'utf8');

  if (!code.includes(PLACEHOLDER)) {
    // Placeholder already gone. If we DO have a secret to bake, that means the
    // file changed in a way we can't safely inject — fail rather than ship a
    // build that silently can't attest.
    if (secret) {
      throw new Error('[beforePack] attestation-secret placeholder missing in lib/attestation.js — refusing to ship an un-bakeable build.');
    }
    console.warn('[beforePack] placeholder absent and no secret set — nothing to bake.');
    return;
  }

  if (!secret) {
    console.warn('[beforePack] KIOSK_ATTESTATION_SECRET is EMPTY — this build CANNOT produce valid attestations (dev/no-secret build). Do NOT set KIOSK_ATTESTATION_ENFORCED=1 for it.');
    return;
  }

  // Replacement via a FUNCTION so `$`-sequences in the secret (e.g. "$&") are
  // not interpreted as String.replace special patterns. JSON.stringify yields a
  // correctly-quoted/escaped JS string literal.
  const baked = code.replace(PLACEHOLDER, () => JSON.stringify(secret));
  fs.writeFileSync(file, baked, 'utf8');
  console.log(`[beforePack] baked KIOSK_ATTESTATION_SECRET into lib/attestation.js (${secret.length} chars).`);
};

/**
 * Have I Been Pwned — k-anonymity password check.
 *
 * Replaces Supabase Pro's "leaked password protection" feature at
 * zero cost. The full password never leaves the browser:
 *
 *   1. SHA-1 hash the password locally (Web Crypto API)
 *   2. Send only the FIRST 5 hex chars to the HIBP range API
 *   3. HIBP returns ~500-800 candidate hash suffixes that have been
 *      seen in breaches
 *   4. We check if our hash's suffix is in the response
 *
 * The API is free, requires no key, and runs behind Cloudflare's CDN.
 *
 * Usage:
 *   if (await isPasswordPwned(password)) {
 *     setError("This password has appeared in a data breach. Pick a different one.")
 *   }
 *
 * Failure handling: if the API is unreachable (offline, blocked,
 * rate-limited), `isPasswordPwned` resolves to `false` — fail-open.
 * The server still enforces length + complexity via validate_password
 * so a weak-but-uncompromised password is rejected upstream.
 */

const API = 'https://api.pwnedpasswords.com/range/'

/**
 * Compute SHA-1 of a string and return uppercase hex.
 * Uses the browser's Web Crypto API — present in all modern browsers
 * including the Electron renderer.
 */
async function sha1Hex(input) {
  const data = new TextEncoder().encode(input)
  const buf = await crypto.subtle.digest('SHA-1', data)
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase()
}

/**
 * Check if a password appears in the HIBP breach corpus.
 *
 * @param  {string} password - the user's plaintext password (in memory only)
 * @return {Promise<boolean>} true if pwned, false if clean or API unreachable
 */
export async function isPasswordPwned(password) {
  if (!password) return false
  let hash
  try {
    hash = await sha1Hex(password)
  } catch (e) {
    // Web Crypto unavailable (very old browser / non-https context)
    // — fail-open. Server still validates.
    return false
  }
  const prefix = hash.slice(0, 5)
  const suffix = hash.slice(5)

  try {
    const resp = await fetch(API + prefix, {
      // Padding header asks HIBP to randomise response size so an
      // observer can't infer which prefix was queried.
      headers: { 'Add-Padding': 'true' },
    })
    if (!resp.ok) return false
    const text = await resp.text()
    // Response format: "SUFFIX:count\nSUFFIX:count\n..."
    // We just need to find our suffix on any line.
    const upper = text.toUpperCase()
    // Match at start-of-line so a substring collision can't false-positive
    return upper.split('\n').some((line) => line.startsWith(suffix + ':'))
  } catch (e) {
    // Network error, blocked, offline — fail-open
    return false
  }
}

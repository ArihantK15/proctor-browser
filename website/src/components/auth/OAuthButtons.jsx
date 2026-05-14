/**
 * OAuth sign-in/sign-up buttons (Google + Microsoft).
 *
 * Server-side flow — no @supabase/supabase-js needed in the bundle.
 * Each button is a plain <a href="/api/v1/auth/oauth/start?...">,
 * the backend redirects to Supabase → Google/Microsoft → back.
 *
 * Props:
 *   intent     'teacher' | 'student' — which kind of account the
 *              callback should create / bind to. Defaults to 'teacher'.
 *   returnTo   absolute or relative URL the user lands on after we
 *              issue their JWT. The JWT is appended as a URL fragment
 *              (#access_token=...) so it never leaks to logs.
 *   apiBase    override the backend origin (default: VITE_API_BASE
 *              or https://app.procta.net).
 *   showMicrosoft  hide the MS button per-surface if you want.
 */
export default function OAuthButtons({
  intent = 'teacher',
  returnTo = '',
  apiBase,
  showMicrosoft = true,
}) {
  const base = apiBase || import.meta.env.VITE_API_BASE || 'https://app.procta.net'
  const enc = encodeURIComponent
  const ret = returnTo
    ? `&return_to=${enc(returnTo)}`
    : ''

  const googleHref = `${base}/api/v1/auth/oauth/start?provider=google&intent=${enc(intent)}${ret}`
  const azureHref = `${base}/api/v1/auth/oauth/start?provider=azure&intent=${enc(intent)}${ret}`

  return (
    <div className="space-y-2">
      <a
        href={googleHref}
        className="flex w-full items-center justify-center gap-3 rounded-lg border border-white/[0.12] bg-white/[0.03] px-4 py-3 text-sm font-semibold text-white transition-all hover:bg-white/[0.06] no-underline"
      >
        {/* Google G — colour-correct logo */}
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
          <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"/>
          <path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"/>
          <path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238A11.91 11.91 0 0 1 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"/>
          <path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l.003-.002 6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"/>
        </svg>
        Continue with Google
      </a>

      {showMicrosoft && (
        <a
          href={azureHref}
          className="flex w-full items-center justify-center gap-3 rounded-lg border border-white/[0.12] bg-white/[0.03] px-4 py-3 text-sm font-semibold text-white transition-all hover:bg-white/[0.06] no-underline"
        >
          {/* Microsoft 4-square logo */}
          <svg width="18" height="18" viewBox="0 0 21 21" aria-hidden="true">
            <rect x="1" y="1" width="9" height="9" fill="#F25022"/>
            <rect x="11" y="1" width="9" height="9" fill="#7FBA00"/>
            <rect x="1" y="11" width="9" height="9" fill="#00A4EF"/>
            <rect x="11" y="11" width="9" height="9" fill="#FFB900"/>
          </svg>
          Continue with Microsoft
        </a>
      )}
    </div>
  )
}

# Security Policy

Procta handles exam answers, camera/audio feeds, and student PII. We
take security reports seriously and would rather hear from you early
than read about it on Twitter.

## Supported versions

Pre-1.0 release; only the latest `main` branch and the most recent
tagged release (`v2.2.x`) receive security patches. Older builds are
not maintained.

| Version | Supported |
|---|---|
| `main` (HEAD) | ✅ |
| `v2.2.x` (latest tagged) | ✅ |
| anything older | ❌ |

## Reporting a vulnerability

**Please do not open public issues for security bugs.** Two private
channels:

1. **Preferred — GitHub Security Advisory.** Open a draft advisory at
   <https://github.com/ArihantK15/proctor-browser/security/advisories/new>.
   This keeps the discussion private, gives us a CVE pipeline, and lets
   us credit you in the public advisory once a fix ships.
2. **Fallback — email.** <arihantkaul@outlook.com> with subject
   `[security] <short description>`. PGP not currently offered; if you
   need encrypted transport, ask in your first message and we'll
   exchange keys.

What to include:
- A short description of the issue and its impact.
- Steps to reproduce (or a proof-of-concept). Even a small video helps.
- The commit hash / version where you observed it.
- Whether you've disclosed this anywhere else.

## What to expect

- **Acknowledgement: within 48 hours** (often within a few hours
  during weekdays IST).
- **Triage update: within 7 days.** We'll tell you whether we're
  treating it as critical, high, medium, low, or out-of-scope, and
  give a rough fix timeline.
- **Coordinated disclosure: 90 days** from the original report. We'll
  aim to ship a fix well inside that window; if we need more time
  we'll write to you with a specific reason.
- **Credit.** If you'd like to be named in the public advisory and
  release notes, we'll do that. If you'd rather stay anonymous, we
  won't put your name anywhere.

## Scope

In scope:
- The Procta API server (`app/`, deployed at `app.procta.net`).
- The Electron exam client (this repository's root).
- The marketing site (`website/`).
- The React dashboards (`app/dashboard-ui/`, `app/student-ui/`).
- The proctoring renderers (`renderer/`).
- Any infrastructure that we control directly — CDN config, Caddy
  rules, etc.

Out of scope:
- Third-party services we depend on (Razorpay, Supabase, Cloudflare,
  DigitalOcean). Report those directly to the vendor.
- Social-engineering attacks against our team or users.
- Denial-of-service attacks that don't lead to a privilege boundary
  being crossed (rate-limit bypass that just makes us slower without
  exposing other users' data isn't in scope).
- Theoretical attacks without a working proof-of-concept (e.g.
  "an attacker with physical access to a kiosk machine could…").
- Issues that depend on a victim installing a malicious browser
  extension, accepting a TLS warning, or running modified Procta
  binaries.
- Missing best-practice headers / cookie flags that don't lead to a
  concrete attack — open an issue or PR instead.
- Findings from automated scanners with no exploitation context.

If you're not sure whether something's in scope, ask. Borderline
reports are still worth sending.

## Safe-harbour

If you make a good-faith effort to follow this policy, we won't take
legal action against you. Specifically:

- Don't access, modify, or delete data that doesn't belong to you.
- Don't run the issue against real customer accounts — use a test org
  (sign up with a throwaway email or ask us for a sandbox).
- Don't disrupt service for other users (no real DoS testing).
- Give us a reasonable window to fix before going public.

In return, we'll:

- Treat your report as authorised under our terms of service.
- Not pursue legal action for the testing itself.
- Work with you on disclosure timing.

## Operational practices

For transparency, here's what we run on our end:

- **Dependencies**: weekly Dependabot scan + `pip-audit` on every CI
  run. Critical CVEs trigger a hot-fix patch release.
- **Static analysis**: Semgrep + CodeQL on every push, blocking on
  high-severity findings.
- **Secret scanning**: gitleaks pre-commit hook (local) + GitHub
  push protection (remote). Both block known secret patterns before
  they enter git history.
- **Logging**: Sentry for error-level events, structured JSON logs
  for everything else. No raw passwords or full credit-card numbers
  are ever logged.
- **Storage of student data**: row-level-security policies on
  Supabase + Postgres; per-org isolation enforced at the query
  layer (`app/auth/scope.py`).

## Recent advisories

None yet. This section will list previously fixed issues and credit
the reporters once we have any.

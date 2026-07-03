# Procta Secrets — Inventory + Rotation Policy

Every authentication credential, signing key, and API token used by
the running app, where it lives, who issued it, and how to rotate it.

Keep this file in sync with reality. When you add a new secret to
`.env`, add the row here too.

## Where secrets live

| Location | What | Permissions |
|---|---|---|
| `/root/proctor-browser/.env` | Main app secrets read by docker compose | chmod 600, root only |
| `/etc/procta/secrets.env` | S3 (ap-south-1) off-site backup credentials read by `backup_to_s3.sh`; also holds legacy B2 credentials if `backup_to_b2.sh` is still run manually | chmod 600, root only |
| `/etc/procta-backup.env` | restic backup credentials (only if restic is configured) | chmod 600, root only |
| Sentry projects (4) | DSNs — public-by-design, committed in repo | n/a |

**Never commit `/root/proctor-browser/.env` or `/etc/procta/secrets.env`.**
The repo's `.gitignore` blocks `.env` and `.env.*` by default; the
only exceptions are `app/dashboard-ui/.env.production` and
`app/student-ui/.env.production`, which hold public Sentry DSNs only.

## Inventory

| Env var | Purpose | Issuer | Rotate every | Notes |
|---|---|---|---|---|
| `DATABASE_URL` | Postgres connection string (contains password) | self-hosted (you set it in `postgres` container) | 6 months | Rotation requires coordinated update of POSTGRES_PASSWORD inside the container + this URL + a restart |
| `JWT_SECRET` | Signs FastAPI access tokens | generated at install | 6 months | All active sessions invalidate on rotation — users get logged out |
| `TOTP_ENCRYPTION_KEY` | Encrypts Google Classroom OAuth tokens stored in `google_auth_tokens.token_json` | generated at install; Fernet key | 12 months | Rotation requires decrypting all stored tokens with old key + re-encrypting with new — see Rotation playbook §1 |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Payment gateway API auth | Razorpay dashboard → API Keys | 12 months or on staff offboarding | Pair rotates together |
| `RAZORPAY_WEBHOOK_SECRET` | Verifies signed Razorpay webhook payloads | Razorpay dashboard → Webhooks → edit | 12 months | Mismatched secret → 401 on incoming webhooks |
| `SANDBOX_WEBHOOK_SECRET` | Sandbox-mode webhook verification | Razorpay sandbox | rotate with Razorpay | Only used when `RAZORPAY_SANDBOX_MODE=1` |
| `RESEND_API_KEY` | Outbound email | resend.com dashboard → API Keys | 12 months | If rotated mid-send, in-flight retries fail; safe to rotate during low-traffic window |
| `RESEND_WEBHOOK_SECRET` | Verifies signed delivery/bounce events | resend.com dashboard → Webhooks | 12 months | |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | Cloudflare captcha for signup/login forms | Cloudflare dashboard → Turnstile | 12 months | Site key is public; secret key alone needs rotation |
| `GOOGLE_CLASSROOM_CLIENT_ID` / `GOOGLE_CLASSROOM_CLIENT_SECRET` | Google OAuth for Classroom integration | Google Cloud Console → APIs & Services → Credentials | 12 months or on suspected compromise | Rotation invalidates the consent screen for new authorizations only — existing teacher tokens stored in `google_auth_tokens` keep working |
| `LTI_PRIVATE_KEY` / `LTI_NEXT_PRIVATE_KEY` | Signs JWTs in LTI 1.3 launches | generated locally via `openssl genrsa` | 6 months with rolling overlap | Use `LTI_NEXT_*` for zero-downtime rotation (publish next key in JWKS, then swap) |
| `LTI_KID` / `LTI_NEXT_KID` | Key IDs published in the JWKS endpoint | matches the private-key generation | rotate with private key | |
| `GROQ_API_KEY` / `LLM_API_KEY` | LLM provider auth (short-answer grading) | console.groq.com (or your LLM provider) | 12 months | Rate limits matter more than rotation here |
| `LOADTEST_SECRET` | Bypasses captcha + lockouts during load testing | generated; bake into your load-test runner | rotate after every load test campaign | If leaked, attackers can flood signup endpoints |
| `SMTP_PASS` | SMTP fallback when Resend is unavailable | your SMTP provider | 12 months | Only set if `EMAIL_PROVIDER=smtp` |
| `GITHUB_TOKEN` | Reads release tags for the `/download` page | github.com → Settings → Developer settings → Personal access tokens (classic, repo:read scope) | 12 months | Token expiry is the more common rotation trigger |
| ~~`SUPABASE_SERVICE_ROLE_KEY`~~ | **Legacy** — Procta migrated off Supabase in early 2026 and now runs on native Postgres. The env var slot may still exist in old `.env` files; safe to delete. The auth.uid() shim in scripts/run_postgres_migrations.py returns NULL so any leftover RLS policies referencing it never fire. | — | — | — |
| `B2_APPLICATION_KEY_ID` / `B2_APPLICATION_KEY` | **Legacy** — Backblaze B2 for off-site backups. Superseded by S3 (ap-south-1) via `scripts/install_s3_backup.sh`; B2 has no India region so scheduling it left DB backups outside India while evidence lives in S3 Mumbai. `backup_to_b2.sh` is kept only as a manual fallback and is no longer on cron once the S3 installer has run. | backblaze.com → Account → Application Keys | — | Lives in `/etc/procta/secrets.env`, not main `.env`. |
| `BACKUP_S3_BUCKET` | S3 bucket name for off-site DB/media backups (default `procta-backups`), read by `scripts/backup_to_s3.sh` | created via `aws s3api create-bucket --region ap-south-1 --create-bucket-configuration LocationConstraint=ap-south-1`, with a 30-day lifecycle expiry rule | never (bucket name is public) | Distinct from `S3_BUCKET` (screenshot storage) — keep backups in a separate bucket so a lifecycle-expiry misconfiguration can't delete live evidence. Lives in `/etc/procta/secrets.env`. |
| `SENTRY_DSN` (4 of them) | Sends errors to Sentry projects | sentry.io project settings → Client Keys | rotate only on compromise; DSNs are project identifiers, not auth tokens | Three live in committed `.env.production`/`main.js` (browser-side, public by design); one in `/root/proctor-browser/.env` (server-side, also not really secret) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS IAM credentials for S3 screenshot storage (ap-south-1, SSE-S3) | AWS IAM → Users → procta-s3-screenshot-writer → Security credentials | 12 months | Scope policy to `s3:PutObject`, `s3:GetObject`, `s3:ListBucket`, `s3:DeleteObject` on the screenshot bucket only. Stores screenshots with SSE-S3 server-side encryption at rest. |
| `S3_BUCKET` | S3 bucket name for encrypted screenshot storage (e.g. `procta-screenshots-<env>`) | created via `aws s3api create-bucket --region ap-south-1 --create-bucket-configuration LocationConstraint=ap-south-1` | never (bucket name is public) | Must be unique globally. Bucket policy must block public access + enforce `aws:SecureTransport` + require `x-amz-server-side-encryption: AES256`. |
| `S3_REGION` | AWS region for S3 bucket (default: `ap-south-1`) | n/a (env var mirrors infra config) | never | Update if you migrate regions. |
| `S3_ENABLED` | Feature flag — set to `1` or `true` to enable S3 screenshot offload | n/a | n/a | When unset or `0`, all S3 operations are no-ops and everything stays on local disk (previous behaviour). |
| `S3_LOCAL_CACHE_DAYS` | Days to retain local screenshot cache when S3 is system-of-record (default: `7`) | n/a | n/a | Only meaningful when `S3_ENABLED=1`. Without the flag the cleanup sweep defaults to 30d. |

## Rotation playbook

### 1. `TOTP_ENCRYPTION_KEY` (rare, careful)

This Fernet key encrypts every row in `google_auth_tokens.token_json`.
Rotating it requires re-encrypting all data; a naive swap will make
every teacher's Google Classroom integration silently break (tokens
won't decrypt and the OAuth flow will start failing on the next
classroom sync).

Procedure when you must rotate:

1. Read every `google_auth_tokens.token_json` with the OLD key, decrypt
   in memory, re-encrypt with the NEW key, write back. A one-shot
   script in `scripts/rotate_totp_key.py` is the right shape (doesn't
   exist yet — write before first rotation).
2. Update `TOTP_ENCRYPTION_KEY` in `/root/proctor-browser/.env`.
3. `docker compose restart api worker`.

### 2. `JWT_SECRET`

Rotation kicks every active session. Plan for it.

1. Update `JWT_SECRET` in `.env`.
2. `docker compose restart api worker autosave-worker`.
3. Existing access tokens (~15-minute lifetime per the JWT settings)
   start failing immediately. Users see a re-login prompt within 15 min.
4. Refresh tokens stored in DB remain valid — they re-mint a new access
   token with the new secret on the next /refresh call.

### 3. Razorpay keys

1. Razorpay dashboard → Settings → API Keys → Generate key.
2. They show the new pair **once** — copy the key ID + secret.
3. Update `.env` for both `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`.
4. `docker compose restart api worker`.
5. The old key remains active for 24h on Razorpay's side. Manually
   disable it from the dashboard once you've confirmed payments are
   processing on the new key.

### 4. LTI signing keys — zero-downtime rolling rotation

LTI's JWKS model lets you publish multiple keys at once. The launch
side validates with whichever key's `kid` is in the JWT header.

1. Generate the new keypair:
   `openssl genrsa -out lti_next.pem 2048`
2. Put the private key in `LTI_NEXT_PRIVATE_KEY`, set `LTI_NEXT_KID` to a
   new unique value (e.g., `procta-2026-12`).
3. Restart. The JWKS endpoint now publishes both old + new public keys.
4. Wait at least one consumer cache cycle (some LMSes cache JWKS for
   24h — check Canvas's JWKS cache TTL).
5. Move `LTI_NEXT_*` values to `LTI_PRIVATE_KEY` + `LTI_KID`. Clear the
   `LTI_NEXT_*` slots.
6. Restart. The JWKS now publishes only the new key. Old key is dead.

### 5. Database password rotation

Hard. Native Postgres in docker-compose stores its password in two
places: the `postgres` container's `POSTGRES_PASSWORD` env, and every
client's `DATABASE_URL`. Mismatch breaks all DB access.

1. Set a maintenance window — this WILL interrupt traffic briefly.
2. `docker compose stop api worker autosave-worker` (keep postgres up).
3. `docker compose exec postgres psql -U procta -c "ALTER USER procta PASSWORD '<new-pw>';"`.
4. Update `POSTGRES_PASSWORD` in `.env` AND `DATABASE_URL` (both).
5. `docker compose up -d` to restart everything with new envs.

## Cloudflare WAF rate limit (recommended)

`slowapi` does in-app rate limiting on auth endpoints, but an edge
rule is defense in depth — blocks the attacker before they reach
your origin. Free Cloudflare tier includes 1 WAF rate-limit rule.

Configure in the Cloudflare dashboard:

1. Cloudflare → your `procta.net` zone → **Security** → **WAF** →
   **Rate limiting rules** → **Create rule**.
2. **Field:** URI Path. **Operator:** matches regex.
   **Value:** `^/api/v1/(auth|student/auth)/(login|register|otp/send|otp/verify|password/reset)`
3. **Action:** Block. **Duration:** 10 minutes.
4. **Threshold:** 10 requests per 1 minute per IP.
5. Deploy.

This blocks an IP making more than 10 auth-related requests per
minute for 10 minutes. Legitimate users (login, retry, signup) never
hit it; brute-force scripts trip it instantly.

The free tier gives one rule; if you have spare slots, add:

- `^/api/v1/registration` → 5 req/min (registration is rate-limited
  internally but edge protection blocks fake-account creation floods)
- `^/api/v1/invites/` → 30 req/min (invite link clicks; legit
  bulk-send by teachers can hit this so keep it high)

## Suspected-compromise procedure

If you suspect any secret has leaked (committed to git, posted in a
support ticket, on a developer's stolen laptop):

1. **Within the hour:** rotate that one secret using the playbook
   above. Don't wait to investigate — rotate first, analyze later.
2. **Within the day:** audit access logs for the period the secret
   was exposed. Postgres: `pg_stat_activity` archive if you have it;
   Razorpay: dashboard → API logs; Sentry: ingest events from that
   project.
3. **Within the week:** rotate all *related* secrets too. If
   `JWT_SECRET` leaked, also rotate `TOTP_ENCRYPTION_KEY` since both
   sit in the same `.env`. If a Razorpay key leaked, also rotate the
   webhook secret.
4. **Document:** add a row to `auth_events` describing the rotation
   reason, with `event_type='secret_rotated'`. Future you will want
   the trail.

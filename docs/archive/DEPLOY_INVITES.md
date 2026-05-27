# Email Invites — Deploy Handoff

Resume point for the email-invite rollout on `app.procta.net`. Pick this up cold tomorrow.

---

## Where we are

### ✅ Code — shipped and pushed to `origin/main`

| Commit    | Summary |
|-----------|---------|
| `405dee4` | fix(live+clear): derive Live/Stale from heartbeat |
| `dfd04e6` | fix(panic): actually quit on Windows |
| `bc0d2f3` | feat(invites): email-based student onboarding with Resend + per-invite codes |
| `84b497f` | feat(invites): dashboard UI for bulk send, status tracking, group-pivoted invites |

Files touched (all already merged to main):
- `app/main.py` — invite endpoints, live_state derivation, enriched group members
- `app/emailer.py` — Resend backend + noop backend + webhook verification
- `app/static/dashboard.html` — Invites tool-card, live/stale badges
- `migrations/phase10_student_invites.sql` — `student_invites` + `invite_send_counters` tables
- `tests/test_invites.py` (12 tests), `tests/test_live_sessions.py` (5 tests)

Tests: `102 passed` (excluding pre-existing playwright + pydantic import errors unrelated to this work).

### ✅ DNS + Email setup — completed today

- `procta.net` is on Cloudflare nameservers ✅
- **Resend domain** `mail.procta.net` — DNS records added to Cloudflare, verified via `dig`, Resend UI shows **Verified** ✅
- **Cloudflare Email Routing** enabled on `procta.net` ✅
  - Destination Gmail: verified ✅
  - Custom address `support@procta.net` → forwards to personal Gmail ✅
- First inbound test email forwarded successfully (landed in Gmail **spam** — expected for brand-new domain)

### ⚠️ Known open items (pre-pilot, not blockers for initial smoke test)

- Inbound forwarded mail lands in Gmail spam. Need to:
  1. Mark "Not spam" a few times as more test mail arrives
  2. Gmail → Settings → Filters → create filter `to:*@procta.net` → **Never send to Spam**
  3. Add `support@procta.net` to Gmail contacts

---

## What's pending — pick up HERE tomorrow

### Step 1 — Generate Resend API key + webhook secret

1. Resend → **API Keys → Create API Key**
   - Name: `procta-production`
   - Permission: **Full access**
   - Domain: `mail.procta.net`
   - Copy the `re_...` value → save somewhere safe

2. Resend → **Webhooks → Add Endpoint**
   - Endpoint URL: `https://app.procta.net/api/webhooks/email`
   - Events: `email.delivered`, `email.bounced`, `email.opened`, `email.complained`
   - Save → click into the webhook → **Signing Secret** → reveal → copy the `whsec_...` value

### Step 2 — Apply Supabase migration

Supabase Dashboard → SQL Editor → New query → paste contents of
`migrations/phase10_student_invites.sql` → Run.

Verify in Table Editor that `student_invites` and `invite_send_counters` exist.

### Step 3 — Set env vars on droplet

```bash
ssh <droplet>
cd <procta checkout>
# edit .env (or wherever env lives)
```

Add/update these:
```bash
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_xxxxxxxxxxxx          # from Step 1
RESEND_WEBHOOK_SECRET=whsec_xxxxxxxxx   # from Step 1
EMAIL_FROM=invites@mail.procta.net
EMAIL_FROM_NAME=Procta
EMAIL_REPLY_TO=support@procta.net
INVITE_BASE_URL=https://app.procta.net
```

**`INVITE_BASE_URL` must be `app.procta.net`** (dashboard host) — not `procta.net` (marketing). Students click links of the form `https://app.procta.net/invite/<token>` which are served by the FastAPI app.

### Step 4 — Pull + restart

```bash
git pull
# whichever applies:
systemctl restart procta
# or
docker compose up -d --build
# or
pm2 restart procta
```

### Step 5 — Sanity check routing before first send

```bash
# Webhook route exists (POST-only, so GET returns 405)
curl -I https://app.procta.net/api/webhooks/email
# Expect: 405 Method Not Allowed or 403

# Invite landing route goes to the app, not the marketing 404
curl -I https://app.procta.net/invite/does-not-exist
# Expect: 404 from the FastAPI app (branded error page)
```

If either hits the marketing site instead of FastAPI, your reverse proxy (Caddy/Nginx) needs to route `/api/webhooks/email` and `/invite/*` to the app. **Do not send invites until this passes.**

### Step 6 — Smoke test (2 min)

1. Log into https://app.procta.net → Tools tab → scroll to **Email Invites**
2. Pick an exam (top of dashboard)
3. Paste one row:
   ```
   Your Name, your-personal-email@gmail.com, TEST001
   ```
4. Leave "Generate one-time access code" checked
5. Click **Send Invites** — should say "Sent 1"
6. Check personal Gmail inbox (and spam — likely spam on first send) → click the invite link → should land on `https://app.procta.net/invite/<token>` with a branded download page
7. Back in dashboard → Refresh List → status should flip `sent → opened`

### Step 7 — Webhook bounce test

Send an invite to `bounce@resend.dev` (Resend's test bounce address).
Within ~60s, the status in the dashboard Invites table should flip to `bounced` (red badge).

**If it stays `sent`, the webhook isn't reaching the app.** Diagnostic:
```bash
# On droplet, watch for webhook hits
journalctl -u procta -f | grep -i webhook
# or: docker logs -f procta
```
Then Resend → Webhooks → endpoint → **Logs** tab. Typical failures:
- `404` → reverse proxy not routing `/api/webhooks/email`
- `403 invalid signature` → `RESEND_WEBHOOK_SECRET` mismatch — re-copy and restart app
- `timeout` → Cloudflare WAF/rate-limit blocking Resend IPs. Allowlist per https://resend.com/docs/dashboard/webhooks/introduction#ip-addresses

### Step 8 — Spam reputation hardening (before first real school pilot)

1. **Upgrade DMARC** — Cloudflare DNS → edit `_dmarc.mail.procta.net` TXT record to:
   ```
   v=DMARC1; p=none; rua=mailto:dmarc@procta.net; ruf=mailto:dmarc@procta.net; fo=1
   ```
   Then in Cloudflare Email Routing → Routes → Custom addresses → add `dmarc@procta.net` → forward to personal Gmail (you'll get daily aggregate reports from Google/Microsoft)

2. **mail-tester.com baseline** — open mail-tester.com → they give you a throwaway `test-xxxx@mail-tester.com` address → send an invite to it from the dashboard → check the score. Aim for **9+/10**. Fix anything flagged.

3. **Domain warm-up discipline**:
   - Day 1: 10 invites max
   - Day 2–3: 25–50
   - Day 4–7: 100–200
   - Week 2+: up to the 500/day cap
   - **Never** dump 500 invites on day one — it will kill your domain reputation.

---

## Feature reference — what the invites system does

### Endpoints now live in `app/main.py`
- `POST /api/admin/invites/send` — bulk send with custom_message, per_invite_code, expiry
- `GET /api/admin/invites?exam_id=X` — list with decorated invite_url
- `POST /api/admin/invites/{id}/resend` — rotate token + resend
- `POST /api/admin/invites/resend-bounced` — bulk retry bounced/failed
- `DELETE /api/admin/invites/{id}` — soft revoke (status → `revoked`)
- `GET /invite/{token}` — public landing page with OS-sniffed download button
- `POST /api/webhooks/email` — Resend webhook, HMAC-verified, flips status on bounce/open/delivered

### Dashboard UI — Tools tab → "Email Invites" card
- Three recipient input modes: paste, CSV upload, pull-from-group
- Custom message field (up to 500 chars, appended to email body)
- Per-invite access code checkbox (generates per-student one-time code)
- Expires-at datetime (defaults to exam end)
- Sent Invites table: name / email / roll / status badge / sent time / Copy–Resend–Revoke actions
- Bulk "Resend Bounced" button
- Auto-refreshes when exam switched or Refresh clicked

### Status lifecycle
```
queued → sent → opened → accepted
                      ↘ bounced / failed (→ revoked | resend)
```
- `queued` — row written, email not yet sent (should be rare — batching is synchronous)
- `sent` — Resend accepted the message
- `opened` — best-effort: student GET'd `/invite/<token>` landing page
- `accepted` — student validated into an exam session using the invite's token/code
- `bounced` — provider webhook reported hard bounce
- `failed` — provider rejected (spam block, invalid address, etc.)
- `revoked` — teacher clicked Revoke before student used it

### Rate limit
- **500 invites/day/teacher** — hard-coded in `_check_daily_cap` in `app/main.py`.
- Counter resets daily via the date key in `invite_send_counters`.
- To raise: edit the `500` literal in `_check_daily_cap`, or add a per-teacher override column.

### Per-invite access code
- Default: ON (checkbox pre-checked in UI)
- Each invite gets its own 8-char typo-resistant code (no `I O 0 1`)
- Code is embedded in the invite email + stored on the row
- `validate-student` accepts either the exam's shared `access_code` OR the invite's per-invite `access_code`
- Benefit: teachers don't have to read out a shared code on the intercom; each student has their own

---

## If you get stuck tomorrow

Re-read this doc. Then if still stuck, the likely trouble spots (in order of frequency):

1. **Reverse proxy not routing `/invite/*` or `/api/webhooks/email` to FastAPI** — Step 5 catches this. Check `Caddyfile` / nginx site config.
2. **Webhook returning 403** — `RESEND_WEBHOOK_SECRET` has whitespace or got truncated. Re-copy from Resend, paste fresh, restart.
3. **Emails landing in spam at mail-tester** — paste the mail-tester score page into a new Claude chat with this doc linked.
4. **Supabase insert failing with "column does not exist"** — migration didn't apply. Re-run `phase10_student_invites.sql` in SQL Editor.

---

## TL;DR tomorrow

1. Resend → create API key + webhook (5 min)
2. Supabase → run migration (1 min)
3. Droplet → set 7 env vars → `git pull` → restart (3 min)
4. `curl` sanity checks (Step 5)
5. Send yourself a test invite (Step 6)
6. Send to `bounce@resend.dev` to verify webhook (Step 7)
7. mail-tester.com score check before any real school pilot (Step 8)

Total: ~20 min if no routing issues, ~45 min with one round of reverse-proxy debug.

---

## Resumable Checklist — what's left (as of 2026-04-21)

### ✅ Done
- [x] All code shipped to `main` (4 commits: stale-Live fix, panic fix, invites backend, invites UI)
- [x] Follow-up commits: server-side failure logging (`42ddd2b`), RLS policies (`252e309`), desktop-only notice (`6286c5b`), per-email failure UX (`c487640`)
- [x] Resend domain `mail.procta.net` verified
- [x] Cloudflare Email Routing live (`support@procta.net` → personal Gmail)
- [x] Resend API key + webhook endpoint configured
- [x] Supabase migration applied + **RLS policies added** (critical — the migration alone isn't enough)
- [x] Droplet env vars set, Docker rebuild working
- [x] Caddy correctly routes `/invite/*` and `/api/webhooks/email` to FastAPI
- [x] First real test invite: landed in inbox, landing page rendered, status flipped `sent → opened`

### ⬜ Pending
- [ ] **Droplet rebuild** to pick up desktop-only notice + per-email failure UX:
      ```bash
      cd ~/proctor-browser
      git pull
      docker compose build --no-cache api && docker compose up -d --force-recreate api
      ```
- [ ] **Webhook bounce test** — send to `bounce@resend.dev` (roll `BOUNCE01`), verify status flips from `sent` → `bounced` within 60s.
      Diagnostic if it doesn't: `docker compose logs -f api | grep -iE "webhook|bounce"` then check Resend → Webhooks → endpoint → Logs tab for delivery failures (typical: 403 = secret mismatch, 404 = Caddy misroute).
- [ ] **Gmail spam hygiene** —
      - Gmail → Settings → Filters → Create filter `to:*@procta.net` → Never send to Spam
      - Add `support@procta.net` to Gmail contacts
      - For any incoming invite test hits, "Report Not Spam" a few times
- [ ] **Upgrade DMARC with reporting** — Cloudflare DNS → edit `_dmarc.mail.procta.net` TXT to:
      `v=DMARC1; p=none; rua=mailto:dmarc@procta.net; ruf=mailto:dmarc@procta.net; fo=1`
      Then in Email Routing, add `dmarc@procta.net` → personal Gmail.
- [ ] **mail-tester.com baseline** — before any real school pilot, send an invite to the throwaway address mail-tester gives you. Aim for 9+/10. Fix anything flagged.
- [ ] **Domain warm-up schedule** — don't dump 500 invites on day one:
      - Day 1: ≤10
      - Day 2–3: 25–50
      - Day 4–7: 100–200
      - Week 2+: up to the 500/day cap

### Nice-to-haves (not blocking)
- [ ] "Resend bounced" row action per invite (not just bulk button) — UI polish
- [ ] CSV import that auto-detects column order via header row
- [ ] Email preview modal in the dashboard showing the rendered HTML before send
- [ ] Analytics on invite funnel (sent → opened → accepted rate per exam)

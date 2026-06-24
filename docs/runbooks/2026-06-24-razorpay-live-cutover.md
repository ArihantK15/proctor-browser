# Runbook — Razorpay test → live cutover

**Goal:** start collecting real money safely. This is **NOT a key swap.** Live mode needs
new keys *and* a full set of live-mode plan IDs *and* a live webhook secret *and* an
activated (KYC-approved) account. A naive swap (live keys, test plan IDs) makes every
checkout fail with "plan not found" — the exact flag-ahead-of-dependency failure mode that
already cost us a 4-day signup outage ([[incident_signup_constraint_2026_06]]). Do this
deliberately, in a quiet window, with a real test charge before you announce it.

## How the code decides test vs live (so you know exactly what each var does)

| Behavior | Trigger (in `app/services/billing.py`) |
|---|---|
| **Live API calls** | `RAZORPAY_KEY_ID` **and** `RAZORPAY_KEY_SECRET` both set → `is_live()` true ([billing.py:34](../../app/services/billing.py)) |
| **Sandbox (fake) mode** | `RAZORPAY_SANDBOX_MODE=1` — explicit; never returns a real order |
| **Which plan to charge** | reads `RAZORPAY_PLAN_{PLAN}{_ANNUAL}` *at request time*; if missing → 500 "…not configured — cannot create subscription" ([billing.py:114,147](../../app/services/billing.py)) |
| **Webhook trust** | HMAC-SHA256 of the raw body vs `X-Razorpay-Signature` using `RAZORPAY_WEBHOOK_SECRET` ([billing.py:172](../../app/services/billing.py)); route `POST /api/v1/webhooks/razorpay` |

**The trap:** Razorpay plan IDs, the webhook secret, and customers are **mode-specific**. A
test-mode `plan_xxx` does not exist in live mode. So flipping only `KEY_ID/SECRET` leaves the
six `RAZORPAY_PLAN_*` vars pointing at test plans that live mode can't see → checkout 500s.

## Pre-flight (do these BEFORE touching any env var)

1. **KYC / account activation.** Razorpay Dashboard → confirm the account is **Activated** for
   live mode (business KYC approved, settlement bank account added). Live keys reject everything
   until this is done. This can take days — start here.
2. **Decide the catalog.** Confirm the live prices for starter / growth / pro (monthly + annual)
   match `PLANS` in `app/constants.py`. Razorpay plan amount is fixed at plan-creation; if a
   price changes later you create a *new* plan, you don't edit the old one.

## Cutover steps

> Do this in a no-checkout window. Keep the test values saved so you can roll back instantly.

### 1. Generate live keys
Dashboard → **switch the toggle to Live** → Settings → API Keys → Generate Live Key.
Record `key_id` (starts `rzp_live_…`) and `key_secret` (shown once — store in the secret manager,
never a repo).

### 2. Recreate ALL SIX plans in **live** mode
Still in Live mode: Subscriptions → Plans → create each of:
`starter` (monthly + annual), `growth` (monthly + annual), `pro` (monthly + annual).
Match each amount/interval to `PLANS`. Record the six new `plan_…` IDs. These map to:

```
RAZORPAY_PLAN_STARTER          RAZORPAY_PLAN_STARTER_ANNUAL
RAZORPAY_PLAN_GROWTH           RAZORPAY_PLAN_GROWTH_ANNUAL
RAZORPAY_PLAN_PRO              RAZORPAY_PLAN_PRO_ANNUAL
```

### 3. Create the live webhook
Dashboard (Live) → Settings → Webhooks → Add:
- **URL:** `https://app.procta.net/api/v1/webhooks/razorpay`
- **Secret:** generate a strong random string → this becomes `RAZORPAY_WEBHOOK_SECRET`
- **Active events — exactly what the handler acts on** (`_SUB_GRANT`,
  [billing.py:337-346](../../app/routers/billing.py)): `subscription.authenticated`,
  `subscription.activated`, `subscription.charged`, `subscription.resumed`,
  `subscription.halted`, `subscription.cancelled`, `subscription.completed`,
  `subscription.paused`. Also enable `invoice.paid` — `invoice.*` events are recorded for the
  billing trail but never change entitlement (only `subscription.*` does,
  [billing.py:420](../../app/routers/billing.py)). Don't subscribe to events the handler ignores;
  they just add noise.

### 4. Swap the env (all at once — never partially)
On the server, set **all nine** together, then redeploy:
```
RAZORPAY_KEY_ID=rzp_live_xxx
RAZORPAY_KEY_SECRET=<live secret>
RAZORPAY_WEBHOOK_SECRET=<live webhook secret from step 3>
RAZORPAY_PLAN_STARTER=plan_live_xxx
RAZORPAY_PLAN_STARTER_ANNUAL=plan_live_xxx
RAZORPAY_PLAN_GROWTH=plan_live_xxx
RAZORPAY_PLAN_GROWTH_ANNUAL=plan_live_xxx
RAZORPAY_PLAN_PRO=plan_live_xxx
RAZORPAY_PLAN_PRO_ANNUAL=plan_live_xxx
# and ensure RAZORPAY_SANDBOX_MODE is unset / not '1'
```
**Partial swaps are the danger.** Live keys + any test plan ID = broken checkout. Set them as
one batch.

## Verification (BEFORE you announce)

1. **Env sanity** (masks values):
   `docker exec proctor-api printenv | grep -E '^RAZORPAY_' | sed 's/=.*/=<set>/'` — confirm all
   nine present and `RAZORPAY_SANDBOX_MODE` absent.
2. **Real low-value test charge.** From a real account, subscribe to the cheapest plan with a
   real card (or Razorpay's live-mode test instrument if available). Confirm:
   - the subscription is created (no "plan not configured" 500),
   - the `subscription.activated` / `subscription.charged` webhook arrives and **verifies**
     (watch the logs for the webhook handler; a signature failure means the wrong
     `RAZORPAY_WEBHOOK_SECRET`),
   - the `subscriptions` row flips to `active` (the constraint now accepts every status —
     phase144), entitlement turns on, and the invoice shows in the Razorpay live dashboard.
3. **Refund the test charge** from the dashboard and confirm `subscription.cancelled`/refund
   handling behaves.

## Rollback
Restore the saved **test** values for all nine vars and redeploy. Because live and test are fully
separate namespaces, reverting is clean — no live data is touched by going back to test keys.
Any real subscription created during the window stays in the live dashboard; cancel/refund it
there.

## Notes / gotchas
- `is_live()` is true the instant both keys are set — there is no separate "go live" flag, so the
  env batch IS the switch.
- Webhook signature uses the **raw** request body; don't add any proxy that re-serializes JSON in
  front of `/api/v1/webhooks/razorpay` or every signature will fail.
- Keep `RAZORPAY_WEBHOOK_SECRET` and `RAZORPAY_KEY_SECRET` out of logs/Sentry (same lesson as the
  S3_KMS_KEY_ID leak — exception strings are not scrubbed).

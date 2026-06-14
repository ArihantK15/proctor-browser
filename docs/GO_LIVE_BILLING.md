# Go-Live Checklist — Razorpay Billing

Moving billing from **Test mode** to **Live mode**. The trap: **plan IDs and
webhooks are mode-specific** — test-mode `plan_…` IDs and the test webhook do
**not** exist in Live mode. Swapping only the API keys breaks every checkout
(503 "plan not configured") and stops webhooks arriving. You must recreate the
plans and webhook in Live mode and update their IDs/secret too.

The amounts and structure are identical to Test mode — this is ~15 min of
clicking, no code changes.

## Account prerequisites (do first — Live mode is locked until these pass)

- [ ] **KYC / business verification** approved on the Razorpay account.
- [ ] **Subscriptions + UPI Autopay** enabled on the live account
      (Settings → Payment methods). Test mode has these on by default; Live
      often needs explicit activation.

## Step 1 — Recreate the 6 plans in **Live mode**

Dashboard (toggle to **Live Mode**) → Subscriptions → Plans → Create Plan.
Amounts must match `app/constants.py` `PLANS` exactly (INR, enter rupees):

| Plan Name              | Billing Cycle | Amount (₹) |
|------------------------|---------------|------------|
| Procta Starter         | Monthly       | 2,400      |
| Procta Growth          | Monthly       | 12,000     |
| Procta Pro             | Monthly       | 30,000     |
| Procta Starter (Annual)| Yearly        | 24,000     |
| Procta Growth (Annual) | Yearly        | 120,000    |
| Procta Pro (Annual)    | Yearly        | 300,000    |

Copy each new `plan_…` ID.

## Step 2 — Register the webhook in **Live mode**

Settings → Webhooks → Add New Webhook:

- **URL:** `https://app.procta.net/api/v1/webhooks/razorpay`
- **Secret:** any strong string (you may reuse the test secret value, or
  generate a new one with `python3 -c "import secrets; print('whsec_'+secrets.token_hex(24))"`).
- **Active events (10):**
  `subscription.authenticated`, `subscription.activated`, `subscription.charged`,
  `subscription.pending`, `subscription.halted`, `subscription.cancelled`,
  `subscription.completed`, `subscription.paused`, `subscription.resumed`,
  `invoice.paid`

## Step 3 — Update the Hostinger environment

Replace these in the server env, then **restart the container**:

```dotenv
RAZORPAY_KEY_ID=rzp_live_xxxxxxxx        # Live API key
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxx        # Live API secret
RAZORPAY_WEBHOOK_SECRET=whsec_xxxxx      # the Live webhook secret from Step 2

# Live plan IDs from Step 1 (replace ALL six):
RAZORPAY_PLAN_STARTER=plan_xxxxx
RAZORPAY_PLAN_GROWTH=plan_xxxxx
RAZORPAY_PLAN_PRO=plan_xxxxx
RAZORPAY_PLAN_STARTER_ANNUAL=plan_xxxxx
RAZORPAY_PLAN_GROWTH_ANNUAL=plan_xxxxx
RAZORPAY_PLAN_PRO_ANNUAL=plan_xxxxx
```

`APP_ENV=production` and everything else stays unchanged. With `APP_ENV=production`,
`RAZORPAY_WEBHOOK_SECRET` is **mandatory** — without it every webhook 400s.

## Step 4 — Verify after restart

- [ ] Upgrade click from the dashboard redirects to a real Razorpay checkout
      (`short_url`), not a 503.
- [ ] After a real payment, the org's `organizations.max_students` rises to the
      plan cap (webhook → `reconcile_org_entitlement`).
- [ ] The Billing → Invoices tab lists the paid invoice with a working link.
- [ ] Razorpay Dashboard → Webhooks shows recent deliveries returning **200**.

## Notes

- Test-mode plans/webhook can stay; they're inert in Live mode.
- Overage billing stays off (`OVERAGE_BILLING_ENABLED` unset) until you've put a
  full live subscription through end-to-end and want to start charging for
  students over the plan cap.
- Reference: plan prices live in `app/constants.py` (`PLANS`); env var names are
  read in `app/services/billing.py` (`RAZORPAY_PLAN_<TIER>[_ANNUAL]`).

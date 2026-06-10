# Billing — Enterprise Rebuild (Recurring Subscriptions only)

**Date:** 2026-06-10 · **Status:** Approved (model decision); implementing
**Decision:** Razorpay **recurring Subscriptions** are the single billing model.
Deprecate one-off Orders for plan activation; remove the dead generic checkout.

## Target architecture

### Single source of truth for entitlement
`subscriptions(org_id, plan, status, …)` is authoritative. The *enforced* cap
`organizations.max_students` becomes a **projection** of subscription state,
written **only** through one function:

```
reconcile_org_entitlement(org_id):
    sub = subscription for org
    entitled = sub.status in ENTITLING_STATUSES   # active/authenticated/charged/pending(grace)
    organizations.max_students = PLAN_LIMITS[sub.plan] if entitled else FREE_CAP (30)
    invalidate billing cache
```

Every webhook/lifecycle transition calls `reconcile_org_entitlement`. No code
path writes `max_students` directly anymore → kills drift (root cause A).

### Entitlement granted ONLY on confirmed payment
`create-subscription` creates the Razorpay subscription (status `created`),
stores `{plan, status:'created', razorpay_subscription_id}`, and **does not
grant** the cap. Entitlement is granted when `subscription.activated` /
`subscription.charged` arrives → reconcile. Closes the free-plan bypass
(critical #1).

### Payment ledger (root cause B)
New immutable table `billing_events`:
```
billing_events(
  id uuid pk, event_id text unique,          -- Razorpay event.id → DB idempotency
  org_id uuid, razorpay_subscription_id text, razorpay_payment_id text,
  event_type text, status text, amount int, currency text,
  payload jsonb, created_at timestamptz default now())
```
Gives: **durable idempotency** (unique `event_id`), audit trail, reconciliation,
invoice history (incl. for any legacy order), and a basis for GST records.

### Lifecycle state machine (Razorpay subscription states → ours)
| Razorpay event            | our status   | entitlement |
|---------------------------|--------------|-------------|
| subscription.authenticated| authenticated| grant       |
| subscription.activated    | active       | grant       |
| subscription.charged      | active       | grant (renew period) |
| subscription.pending      | past_due     | **keep (grace)** + notify |
| subscription.halted       | halted       | downgrade   |
| subscription.cancelled    | cancelled    | downgrade (at period end) |
| subscription.completed    | completed    | downgrade   |
| subscription.paused       | paused       | downgrade   |

**Dunning:** Razorpay performs retries. `pending` = a charge failed but retries
continue → we keep service (grace) and email the admin. `halted` = retries
exhausted → downgrade. No more instant-downgrade on first failure (high #7).

### GST
Capture the org's **GSTIN** (optional) at subscription creation, pass to the
Razorpay customer/subscription. Razorpay generates GST-compliant invoices;
`/invoices` surfaces them. (No custom invoice engine.)

### Authorization
`cancel`, `/invoices`, `/usage` use `_require_billing_admin` (admin/superadmin
only) — fixes the any-teacher-can-cancel gap (high #4).

### Deprecations
- Remove `/billing/checkout/order` + `/billing/checkout/verify` (one-off
  activation) and the webhook `payment.captured/order.paid` reconciliation.
- Remove the dead anonymous `checkout.py` generic flow.

## Fix order (each step ships green + tested)
1. Migration: `billing_events` ledger; `subscriptions` status CHECK + `gstin` on
   organizations; dunning columns if needed.
2. `reconcile_org_entitlement` + canonical `SUB_STATUS` constants (service).
3. Rewrite webhook → subscription lifecycle + ledger (durable idempotency) +
   reconcile + dunning. DB-unique `event_id` replaces Redis-only dedup.
4. `create-subscription` → no premature grant; store plan + created status.
5. Authz fixes on cancel/invoices/usage.
6. Remove Order path + dead `checkout.py`.
7. GSTIN capture + Razorpay invoice surfacing.
8. Status enum/constants cleanup; remove magic strings.

## Non-negotiables
- Webhook signature verified first (keep). DB idempotency via `event_id` unique.
- Entitlement never granted without a confirming Razorpay event.
- Full suite green at each step; never commit (user commits).

CREATE TABLE IF NOT EXISTS coupons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    razorpay_offer_id TEXT NOT NULL,
    description TEXT,
    max_redemptions INTEGER,
    times_redeemed INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 4 Sprint 1: Refund tracking
-- Adds refund_amount to orders + separate refunds table for idempotent partial refund handling

-- Accumulated refund amount on orders (computed from refunds table on each webhook)
ALTER TABLE orders ADD COLUMN IF NOT EXISTS refund_amount NUMERIC(10,2) DEFAULT 0
    CHECK (refund_amount >= 0);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ;

-- Individual refund events (supports multiple partial refunds per order)
CREATE TABLE IF NOT EXISTS refunds (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    shopify_refund_id BIGINT NOT NULL UNIQUE,
    amount NUMERIC(10,2) NOT NULL CHECK (amount > 0),
    reason TEXT DEFAULT '',
    is_partial BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE refunds ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON refunds FOR ALL USING (true) WITH CHECK (true);

CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_id);
CREATE INDEX IF NOT EXISTS idx_refunds_shopify_id ON refunds(shopify_refund_id);

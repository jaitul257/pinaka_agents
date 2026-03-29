-- Pinaka Agents — Initial Database Schema
-- Run this in your Supabase SQL editor or via CLI migration.

-- ── Orders ──
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    receipt_id BIGINT UNIQUE NOT NULL,
    buyer_email TEXT NOT NULL DEFAULT '',
    buyer_name TEXT NOT NULL DEFAULT '',
    total DECIMAL(10, 2) NOT NULL DEFAULT 0,
    shipping_cost DECIMAL(10, 2) NOT NULL DEFAULT 0,
    cogs DECIMAL(10, 2) NOT NULL DEFAULT 0,
    ad_spend DECIMAL(10, 2) NOT NULL DEFAULT 0,
    from_offsite_ad BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'new',
    fraud_flagged BOOLEAN NOT NULL DEFAULT FALSE,
    fraud_reasons TEXT[] DEFAULT '{}',
    insurance_gap DECIMAL(10, 2) DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_buyer_email ON orders(buyer_email);
CREATE INDEX idx_orders_created_at ON orders(created_at);

-- ── Customers ──
CREATE TABLE IF NOT EXISTS customers (
    id BIGSERIAL PRIMARY KEY,
    etsy_user_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    total_orders INT NOT NULL DEFAULT 0,
    total_spent DECIMAL(10, 2) NOT NULL DEFAULT 0,
    first_order_at TIMESTAMPTZ,
    last_order_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Messages ──
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    buyer_name TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general_inquiry',
    is_urgent BOOLEAN NOT NULL DEFAULT FALSE,
    ai_draft TEXT,
    status TEXT NOT NULL DEFAULT 'pending_review',
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_status ON messages(status);
CREATE INDEX idx_messages_conversation ON messages(conversation_id);

-- ── Daily Stats ──
CREATE TABLE IF NOT EXISTS daily_stats (
    id BIGSERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    revenue DECIMAL(12, 2) NOT NULL DEFAULT 0,
    cogs DECIMAL(12, 2) NOT NULL DEFAULT 0,
    etsy_fees DECIMAL(12, 2) NOT NULL DEFAULT 0,
    shipping_cost DECIMAL(12, 2) NOT NULL DEFAULT 0,
    ad_spend DECIMAL(12, 2) NOT NULL DEFAULT 0,
    ad_revenue DECIMAL(12, 2) NOT NULL DEFAULT 0,
    net_profit DECIMAL(12, 2) NOT NULL DEFAULT 0,
    order_count INT NOT NULL DEFAULT 0,
    avg_order_value DECIMAL(10, 2) NOT NULL DEFAULT 0,
    avg_margin_pct DECIMAL(5, 1) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_daily_stats_date ON daily_stats(date);

-- ── Review Requests ──
CREATE TABLE IF NOT EXISTS review_requests (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id),
    review_received BOOLEAN NOT NULL DEFAULT FALSE,
    reminder_count INT NOT NULL DEFAULT 0,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_review_requests_pending ON review_requests(review_received) WHERE NOT review_received;

-- ── Row Level Security ──
-- Enable RLS on all tables (Supabase best practice).
-- Service role key bypasses RLS, so the API can read/write freely.
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_requests ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role full access" ON orders FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON customers FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON messages FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON daily_stats FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access" ON review_requests FOR ALL USING (true) WITH CHECK (true);

-- ── Updated At Trigger ──
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

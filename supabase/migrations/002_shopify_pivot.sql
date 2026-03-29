-- Pinaka Agents — Shopify DTC Pivot Migration
-- Clean break: drop Etsy-specific columns/tables, add Shopify equivalents.
-- No production data to migrate (pre-launch).

-- ══════════════════════════════════════════════
-- CUSTOMERS — Now the primary entity
-- ══════════════════════════════════════════════

-- Drop Etsy-specific column
ALTER TABLE customers DROP COLUMN IF EXISTS etsy_user_id;

-- Add Shopify customer fields
ALTER TABLE customers ADD COLUMN IF NOT EXISTS shopify_customer_id BIGINT UNIQUE;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT '';
ALTER TABLE customers ADD COLUMN IF NOT EXISTS acquisition_channel TEXT DEFAULT '';
ALTER TABLE customers ADD COLUMN IF NOT EXISTS acquisition_cost DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS lifetime_value DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS order_count INT DEFAULT 0;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS lifecycle_stage TEXT DEFAULT 'lead'
    CHECK (lifecycle_stage IN ('lead', 'first_purchase', 'repeat', 'advocate'));
ALTER TABLE customers ADD COLUMN IF NOT EXISTS accepts_marketing BOOLEAN DEFAULT FALSE;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '';

-- Rename total_orders/total_spent if they exist (from v1 schema)
-- Using DO block to handle column existence gracefully
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='customers' AND column_name='total_orders') THEN
        ALTER TABLE customers DROP COLUMN total_orders;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='customers' AND column_name='total_spent') THEN
        ALTER TABLE customers DROP COLUMN total_spent;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='customers' AND column_name='first_order_at') THEN
        ALTER TABLE customers RENAME COLUMN first_order_at TO first_order_date;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='customers' AND column_name='last_order_at') THEN
        ALTER TABLE customers RENAME COLUMN last_order_at TO last_order_date;
    END IF;
END$$;

-- Index for customer lookup by email (not unique — guest checkout duplicates)
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_shopify_id ON customers(shopify_customer_id);
CREATE INDEX IF NOT EXISTS idx_customers_lifecycle ON customers(lifecycle_stage);

-- ══════════════════════════════════════════════
-- ORDERS — Shopify order IDs replace Etsy receipt IDs
-- ══════════════════════════════════════════════

ALTER TABLE orders DROP COLUMN IF EXISTS receipt_id;
ALTER TABLE orders DROP COLUMN IF EXISTS from_offsite_ad;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS shopify_order_id BIGINT UNIQUE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_id BIGINT REFERENCES customers(id);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS subtotal DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tax DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_processing_fee DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shopify_fee DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS profit_margin DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fraud_score DECIMAL(5, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_carrier TEXT DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_number TEXT DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS insurance_amount DECIMAL(10, 2) DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS checkout_token TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_orders_shopify_id ON orders(shopify_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_checkout_token ON orders(checkout_token);

-- ══════════════════════════════════════════════
-- MESSAGES — Customer-linked, direction-aware
-- ══════════════════════════════════════════════

ALTER TABLE messages DROP COLUMN IF EXISTS conversation_id;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS customer_id BIGINT REFERENCES customers(id);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS customer_email TEXT DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS direction TEXT DEFAULT 'inbound'
    CHECK (direction IN ('inbound', 'outbound'));
ALTER TABLE messages ADD COLUMN IF NOT EXISTS urgency TEXT DEFAULT 'normal'
    CHECK (urgency IN ('low', 'normal', 'high', 'urgent'));
ALTER TABLE messages ADD COLUMN IF NOT EXISTS subject TEXT DEFAULT '';
ALTER TABLE messages ADD COLUMN IF NOT EXISTS human_approved BOOLEAN DEFAULT FALSE;

-- Drop old column if exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='messages' AND column_name='is_urgent') THEN
        ALTER TABLE messages DROP COLUMN is_urgent;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_messages_customer_id ON messages(customer_id);
CREATE INDEX IF NOT EXISTS idx_messages_customer_email ON messages(customer_email);

-- ══════════════════════════════════════════════
-- DAILY STATS — Shopify-centric metrics
-- ══════════════════════════════════════════════

ALTER TABLE daily_stats DROP COLUMN IF EXISTS etsy_fees;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS shopify_fees DECIMAL(12, 2) DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS new_customers INT DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS repeat_customers INT DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS ad_spend_google DECIMAL(12, 2) DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS ad_spend_meta DECIMAL(12, 2) DEFAULT 0;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS customer_acquisition_cost DECIMAL(10, 2) DEFAULT 0;

-- ══════════════════════════════════════════════
-- CART EVENTS — Abandoned cart tracking
-- ══════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS cart_events (
    id BIGSERIAL PRIMARY KEY,
    shopify_checkout_token TEXT NOT NULL,
    customer_id BIGINT REFERENCES customers(id),
    customer_email TEXT DEFAULT '',
    event_type TEXT NOT NULL DEFAULT 'created'
        CHECK (event_type IN ('created', 'updated', 'abandoned', 'recovered')),
    cart_value DECIMAL(10, 2) DEFAULT 0,
    items_json JSONB DEFAULT '[]',
    recovery_email_status TEXT DEFAULT NULL
        CHECK (recovery_email_status IS NULL OR recovery_email_status IN ('pending', 'approved', 'sent', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cart_events_token ON cart_events(shopify_checkout_token);
CREATE INDEX IF NOT EXISTS idx_cart_events_customer ON cart_events(customer_id);
CREATE INDEX IF NOT EXISTS idx_cart_events_type ON cart_events(event_type);
CREATE INDEX IF NOT EXISTS idx_cart_events_recovery ON cart_events(recovery_email_status)
    WHERE recovery_email_status IS NOT NULL;

ALTER TABLE cart_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON cart_events FOR ALL USING (true) WITH CHECK (true);

CREATE TRIGGER cart_events_updated_at
    BEFORE UPDATE ON cart_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ══════════════════════════════════════════════
-- DROP Etsy-specific tables
-- ══════════════════════════════════════════════

DROP TABLE IF EXISTS review_requests;

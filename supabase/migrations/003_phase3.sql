-- Phase 3: Shipping tracking + listing drafts

-- Orders: shipping tracking fields
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_url TEXT DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipstation_order_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_orders_tracking ON orders(tracking_number) WHERE tracking_number != '';
CREATE INDEX IF NOT EXISTS idx_orders_shipstation_id ON orders(shipstation_order_id);

-- Listing drafts
CREATE TABLE IF NOT EXISTS listing_drafts (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT[] DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'approved', 'rejected', 'published')),
    shopify_product_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE listing_drafts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON listing_drafts FOR ALL USING (true) WITH CHECK (true);

CREATE TRIGGER listing_drafts_updated_at
    BEFORE UPDATE ON listing_drafts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

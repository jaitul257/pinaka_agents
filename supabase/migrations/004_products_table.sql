-- Products table: persistent product catalog (replaces filesystem JSON)

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    materials JSONB NOT NULL DEFAULT '{}',
    pricing JSONB NOT NULL DEFAULT '{}',
    story TEXT NOT NULL DEFAULT '',
    care_instructions TEXT NOT NULL DEFAULT '',
    occasions TEXT[] DEFAULT '{}',
    certification JSONB,
    images TEXT[] DEFAULT '{}',
    tags TEXT[] DEFAULT '{}',
    shopify_product_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE products ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON products FOR ALL USING (true) WITH CHECK (true);

CREATE TRIGGER products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

-- Phase 6.1: Automated ad creative generation
-- Stores Claude-generated ad copy variants, Meta push state, and audit trail.
-- Each generate() call produces 1-3 rows sharing a generation_batch_id (uuid).

CREATE TABLE IF NOT EXISTS ad_creatives (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT REFERENCES products(sku) ON DELETE SET NULL,
    variant_label TEXT NOT NULL,                    -- 'A','B','C' today; TEXT to avoid future migration
    headline TEXT NOT NULL,                          -- Meta limit ~40 chars
    primary_text TEXT NOT NULL,                      -- Meta limit ~125 chars
    description TEXT NOT NULL DEFAULT '',            -- Meta limit ~30 chars, optional
    cta TEXT NOT NULL DEFAULT 'SHOP_NOW',            -- Meta call_to_action enum value
    image_url TEXT NOT NULL,                          -- from products.images[]
    status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review','publishing','published','rejected','paused')),
    meta_creative_id TEXT,                            -- returned from /adcreatives POST
    generation_batch_id TEXT NOT NULL,                -- uuid groups A/B/C together
    brand_dna_hash TEXT NOT NULL,                     -- sha1 of BrandDNA snapshot at generation time
    validation_warning TEXT,                          -- set if banned-word retry failed
    approved_by TEXT,                                 -- who clicked approve (founder email/username)
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE ad_creatives ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON ad_creatives FOR ALL USING (true) WITH CHECK (true);

CREATE TRIGGER ad_creatives_updated_at
    BEFORE UPDATE ON ad_creatives
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX IF NOT EXISTS idx_ad_creatives_sku ON ad_creatives(sku);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_status ON ad_creatives(status);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_batch ON ad_creatives(generation_batch_id);
CREATE INDEX IF NOT EXISTS idx_ad_creatives_created ON ad_creatives(created_at DESC);

-- Generation batches: tracks async Claude calls to prevent duplicate submissions.
CREATE TABLE IF NOT EXISTS generation_batches (
    id TEXT PRIMARY KEY,                              -- uuid, also referenced as ad_creatives.generation_batch_id
    sku TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,             -- sha1(sku + ISO-minute) prevents double-submit
    status TEXT NOT NULL DEFAULT 'generating'
        CHECK (status IN ('generating','complete','failed')),
    variant_count INT NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE generation_batches ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON generation_batches FOR ALL USING (true) WITH CHECK (true);

CREATE TRIGGER generation_batches_updated_at
    BEFORE UPDATE ON generation_batches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE INDEX IF NOT EXISTS idx_generation_batches_sku ON generation_batches(sku);
CREATE INDEX IF NOT EXISTS idx_generation_batches_idempotency ON generation_batches(idempotency_key);

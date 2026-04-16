-- Post-purchase attribution survey responses.
-- Ground truth for "how did you hear about us" — overrides noisy Meta/GA4 last-click.
-- Captured at the Shopify thank-you page (native JS widget -> /api/attribution/submit).

CREATE TABLE IF NOT EXISTS post_purchase_attribution (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    shopify_order_id        TEXT NOT NULL,
    customer_email          TEXT,
    channel_primary         TEXT NOT NULL,        -- 'instagram' | 'google_search' | 'friend' | 'podcast' | 'meta_ads' | 'tiktok' | 'pinterest' | 'other'
    channel_detail          TEXT,                 -- free text: "@someone on IG", "Tim Ferris podcast"
    purchase_reason         TEXT,                 -- 'gift' | 'self_purchase' | 'anniversary' | 'milestone' | 'other'
    purchase_reason_detail  TEXT,                 -- free text
    submitted_via           TEXT NOT NULL DEFAULT 'thankyou_page',  -- 'thankyou_page' | 'email_survey'
    ip_address              TEXT,
    user_agent              TEXT,
    raw_response            JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- One response per order per submission channel (prevents double-submit on refresh)
    UNIQUE (shopify_order_id, submitted_via)
);

CREATE INDEX IF NOT EXISTS idx_ppa_created_at ON post_purchase_attribution (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ppa_shopify_order ON post_purchase_attribution (shopify_order_id);
CREATE INDEX IF NOT EXISTS idx_ppa_channel ON post_purchase_attribution (channel_primary, created_at DESC);

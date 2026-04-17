-- Per-ad daily performance metrics from Meta Insights API (level=ad).
--
-- Powers creative fatigue detection + per-creative breakdown in weekly reports.
-- Keyed on meta_ad_id (not meta_creative_id) because Meta's insights/level=ad
-- gives per-ad data, and the same creative can back multiple ads under different
-- ad sets. We also store meta_creative_id for grouping.

CREATE TABLE IF NOT EXISTS ad_creative_metrics (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    date                    DATE NOT NULL,

    -- Meta identifiers
    meta_ad_id              TEXT NOT NULL,
    meta_creative_id        TEXT,
    meta_adset_id           TEXT,
    meta_campaign_id        TEXT,
    ad_name                 TEXT,
    creative_name           TEXT,

    -- Volume
    impressions             INT  DEFAULT 0,
    reach                   INT  DEFAULT 0,
    clicks                  INT  DEFAULT 0,
    frequency               NUMERIC(6,3) DEFAULT 0,

    -- Cost
    spend                   NUMERIC(12,2) DEFAULT 0,
    cpm                     NUMERIC(10,4) DEFAULT 0,
    cpc                     NUMERIC(10,4) DEFAULT 0,
    ctr                     NUMERIC(8,4) DEFAULT 0,

    -- Conversions (via funnel)
    view_content_count      INT DEFAULT 0,
    atc_count               INT DEFAULT 0,
    ic_count                INT DEFAULT 0,
    purchase_count          INT DEFAULT 0,
    purchase_value          NUMERIC(12,2) DEFAULT 0,

    -- Full insights payload for future re-analysis without re-calling Meta
    raw                     JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- One row per ad per day
    UNIQUE (date, meta_ad_id)
);

CREATE INDEX IF NOT EXISTS idx_acm_date ON ad_creative_metrics (date DESC);
CREATE INDEX IF NOT EXISTS idx_acm_ad_date ON ad_creative_metrics (meta_ad_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_acm_creative_date ON ad_creative_metrics (meta_creative_id, date DESC);

-- Long-tail SEO topic rotation for the weekly journal post writer.
--
-- Seeded from src/content/seo_writer.py::SEO_KEYWORDS on first cron run.
-- Each weekly run picks the row with the oldest last_used_at (or NULL)
-- and bumps it. Survived restarts + deploys.

CREATE TABLE IF NOT EXISTS seo_topics (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    keyword             TEXT NOT NULL UNIQUE,
    category            TEXT,              -- 'anniversary' | 'education' | 'comparison' | 'occasion'
    last_used_at        TIMESTAMPTZ,
    times_used          INT NOT NULL DEFAULT 0,
    last_draft_url      TEXT,              -- Shopify admin article URL
    last_shopify_article_id BIGINT,
    last_published_at   TIMESTAMPTZ,       -- NULL if still draft; set when user publishes
    active              BOOLEAN NOT NULL DEFAULT TRUE  -- allow temp-disable without delete
);

CREATE INDEX IF NOT EXISTS idx_seo_topics_rotation ON seo_topics (active, last_used_at NULLS FIRST, times_used);

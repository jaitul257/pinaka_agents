-- Phase 6.2: collapse the creative→ad manual attachment gap.
-- When the founder clicks Go Live, the dashboard now creates an Ad object under
-- META_DEFAULT_ADSET_ID (the pre-created PAUSED ad set) so impressions can serve
-- as soon as the user flips the parent Ad Set to ACTIVE once in Ads Manager.
-- Store the returned Ad ID alongside the creative for traceability + deep links.

ALTER TABLE ad_creatives ADD COLUMN IF NOT EXISTS meta_ad_id TEXT;
ALTER TABLE ad_creatives ADD COLUMN IF NOT EXISTS meta_adset_id TEXT;

CREATE INDEX IF NOT EXISTS idx_ad_creatives_meta_ad_id ON ad_creatives(meta_ad_id);

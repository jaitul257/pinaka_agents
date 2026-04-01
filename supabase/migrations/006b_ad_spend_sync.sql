-- Track when ad spend was last synced (idempotency + audit trail).
-- ad_spend_source: 'manual' = hand-entered, 'api' = pulled from Meta/Google APIs.

ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS ad_spend_synced_at TIMESTAMPTZ;
ALTER TABLE daily_stats ADD COLUMN IF NOT EXISTS ad_spend_source TEXT DEFAULT 'manual';

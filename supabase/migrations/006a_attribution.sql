-- Capture attribution params from order webhooks (before they're lost).
-- gclid/fbclid enable server-side conversion tracking for Google/Meta ads.
-- utm_* params enable channel attribution in daily reports.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS gclid TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS fbclid TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS utm_source TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS utm_medium TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS utm_campaign TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS google_conversion_sent_at TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS meta_capi_sent_at TIMESTAMPTZ;

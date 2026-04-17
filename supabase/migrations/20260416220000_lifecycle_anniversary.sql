-- Phase 9.2: Lifecycle orchestration + anniversary capture.
--
-- Three pieces:
--   1. customer_anniversaries — the actual anniversary/engagement/milestone
--      date behind a purchase. Drives year-out re-engagement emails.
--   2. customers.lifecycle_emails_sent — per-trigger dedupe for post-purchase
--      arc (care_guide_day10, referral_day60, custom_inquiry_day180,
--      anniversary_year1). JSONB map so we don't explode the schema with
--      separate boolean columns.
--   3. customers.welcome_started_at — timestamp of the "welcome" cohort
--      entry. Daily cron compares elapsed days to figure out which of
--      the 5 welcome emails to send.

CREATE TABLE IF NOT EXISTS customer_anniversaries (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    customer_id         BIGINT REFERENCES customers(id) ON DELETE CASCADE,
    customer_email      TEXT,
    anniversary_date    DATE NOT NULL,
    relationship        TEXT,    -- 'wedding_anniversary' | 'engagement' | 'birthday' | 'milestone' | 'other'
    notes               TEXT,
    source_order_id     BIGINT,  -- orders.id if captured via post-purchase survey
    -- Track which yearly reminders we've already sent to avoid duplicates
    reminded            JSONB NOT NULL DEFAULT '{}'::JSONB,

    -- One anniversary per (customer, date). Prevents duplicate captures
    -- if a buyer submits the survey twice somehow.
    UNIQUE (customer_id, anniversary_date)
);

CREATE INDEX IF NOT EXISTS idx_anniv_customer ON customer_anniversaries (customer_id);
CREATE INDEX IF NOT EXISTS idx_anniv_date ON customer_anniversaries (anniversary_date);


-- Per-customer dedup for lifecycle triggers + welcome cohort entry.
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS lifecycle_emails_sent JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS welcome_started_at TIMESTAMPTZ;

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS welcome_step INT NOT NULL DEFAULT 0;  -- last welcome step sent (0..5)


-- Extend post_purchase_attribution to capture anniversary context alongside
-- the channel + reason questions. Optional — only populated when buyer
-- opts into the "special date?" follow-up.
ALTER TABLE post_purchase_attribution
    ADD COLUMN IF NOT EXISTS anniversary_date DATE;

ALTER TABLE post_purchase_attribution
    ADD COLUMN IF NOT EXISTS relationship TEXT;

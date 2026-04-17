-- Phase 10: Customer Intelligence Layer
--
-- Three additions to turn the scattered customer data into a queryable,
-- segmented, theme-mined view.

-- ── customer_rfm: daily snapshot of RFM scoring per customer ──
--
-- One row per (customer, day) at most. Computed daily by /cron/rfm-compute.
-- Absolute thresholds (not quintile-based) because our N is too small for
-- quintiles to be stable.

CREATE TABLE IF NOT EXISTS customer_rfm (
    id                      BIGSERIAL PRIMARY KEY,
    customer_id             BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    computed_date           DATE NOT NULL,

    -- Raw inputs
    recency_days            INT,                  -- days since last paid order
    frequency               INT NOT NULL DEFAULT 0,  -- total paid orders
    monetary                NUMERIC(12,2) NOT NULL DEFAULT 0,  -- sum of net order totals

    -- 1-5 scored (5 = best)
    r_score                 SMALLINT NOT NULL,
    f_score                 SMALLINT NOT NULL,
    m_score                 SMALLINT NOT NULL,
    rfm_score_total         SMALLINT NOT NULL,    -- r+f+m, 3..15

    -- Derived segment
    segment                 TEXT NOT NULL,        -- champion|loyal|at_risk|hibernating|new|one_and_done|lost

    -- Projected 12-month lifetime value (avg_order * projected_orders)
    avg_order_value         NUMERIC(12,2) NOT NULL DEFAULT 0,
    projected_ltv_365d      NUMERIC(12,2) NOT NULL DEFAULT 0,

    -- One row per customer per day
    UNIQUE (customer_id, computed_date)
);

CREATE INDEX IF NOT EXISTS idx_cust_rfm_customer ON customer_rfm (customer_id, computed_date DESC);
CREATE INDEX IF NOT EXISTS idx_cust_rfm_segment ON customer_rfm (segment, computed_date DESC);
CREATE INDEX IF NOT EXISTS idx_cust_rfm_date ON customer_rfm (computed_date DESC);


-- ── customer_insights: weekly voice-of-customer theme mining ──
--
-- One row per week. Claude reads the week's customer messages + chat transcripts
-- + survey free-text and produces 3-5 clustered themes with representative
-- quotes. Stored so the dashboard brief can surface them.

CREATE TABLE IF NOT EXISTS customer_insights (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    week_ending         DATE NOT NULL UNIQUE,     -- Sunday of the week analyzed
    themes              JSONB NOT NULL DEFAULT '[]'::JSONB,  -- [{theme, quote, count, source}]
    messages_analyzed   INT NOT NULL DEFAULT 0,
    chats_analyzed      INT NOT NULL DEFAULT 0,
    survey_responses    INT NOT NULL DEFAULT 0,
    sources             JSONB NOT NULL DEFAULT '{}'::JSONB,   -- counts per source type
    claude_tokens_used  INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cust_insights_week ON customer_insights (week_ending DESC);


-- Extend customers with RFM pointer for quick lookups
ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_rfm_at TIMESTAMPTZ;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_segment TEXT;
